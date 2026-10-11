"""Etapas do pipeline. Todas são idempotentes e incrementais: rodar de novo só processa o que falta.

inventory → (staferla) → probe → extract → (ocr) → chunk → concepts → embed → refresh
"""

import hashlib
import json
import logging
from collections import Counter
from datetime import date
from pathlib import Path

from psilib import lang
from psilib.catalog import export_catalog, load_overrides, upsert_author, upsert_document
from psilib.chunking import chunk_segments
from psilib.concepts import count_mentions, sync_lexicon
from psilib.config import settings
from psilib.db import connect, run_step
from psilib.extract import docx as docx_x
from psilib.extract import html as html_x
from psilib.extract import pdf as pdf_x
from psilib.scope import content_signal
from psilib.sources import local, staferla
from psilib.text import normalize

log = logging.getLogger(__name__)


def _sha256(path: str) -> str:
    h = hashlib.sha256()
    with open(path, "rb") as f:
        for block in iter(lambda: f.read(1 << 20), b""):
            h.update(block)
    return h.hexdigest()


# ------------------------------------------------------------------ inventário e coleta


def inventory() -> dict:
    with run_step("inventory") as stats:
        specs = local.scan()
        with connect() as conn:
            seen = set()
            for spec in specs:
                _, status = upsert_document(conn, spec)
                seen.add(spec.uri)
                stats[status] = stats.get(status, 0) + 1
            # Arquivos que sumiram do acervo local
            gone = conn.execute(
                "update document set status='excluded', scope_reason='arquivo removido', updated_at=now()"
                " where source_id='local' and uri <> all(%s) and status <> 'excluded' returning id",
                (list(seen),),
            ).fetchall()
            stats["removed"] = len(gone)
            stats["lines"] = dict(Counter(s.line for s in specs if s.in_scope))
            conn.commit()
            export_catalog(conn)
    return stats


def fetch_staferla(include_freud: bool = False, source: str = "auto") -> dict:
    with run_step("fetch_staferla", include_freud=include_freud, source=source) as stats:
        with connect() as conn:
            known = {
                r["uri"]: r["meta"]
                for r in conn.execute("select uri, meta from document where source_id='staferla'")
            }
        results = staferla.fetch(include_freud=include_freud, known=known, source=source)
        with connect() as conn:
            for spec, changed in results:
                upsert_document(conn, spec, changed=changed)
                stats["changed" if changed else "unchanged"] = (
                    stats.get("changed" if changed else "unchanged", 0) + 1
                )
            conn.commit()
            export_catalog(conn)
    return stats


# ------------------------------------------------------------------ sondagem de conteúdo

_FREUD_MARKERS = (
    "edicao standard brasileira",
    "obras psicologicas completas de sigmund freud",
    "standard edition",
)


def probe(limit: int | None = None) -> dict:
    """Lê o início dos documentos 'revisar' e sugere uma linha teórica pelo vocabulário.

    Cabeçalhos inequívocos (Edição Standard de Freud) são aplicados automaticamente;
    o resto vira sugestão em catalog/catalogo.csv para você decidir em overrides.csv.
    """
    overrides = load_overrides()
    with run_step("probe") as stats, connect() as conn:
        docs = conn.execute(
            "select id, uri, local_path, file_type from document where line='revisar' and status='excluded'"
            " and coalesce(meta->>'content_suggestion','') = '' order by id limit %s",
            (limit,),
        ).fetchall()
        for d in docs:
            try:
                if d["file_type"] == "pdf":
                    import pymupdf

                    with pymupdf.open(d["local_path"]) as doc:
                        sample = "\n".join(doc[i].get_text() for i in range(min(15, doc.page_count)))
                elif d["file_type"] == "docx":
                    sample = "\n".join(s.text for s in docx_x.extract(d["local_path"]).segments)[:60000]
                else:
                    continue
            except Exception as e:
                stats["errors"] = stats.get("errors", 0) + 1
                log.warning("probe falhou em %s: %s", d["uri"], e)
                continue
            suggestion, scores = content_signal(sample)
            meta = {"content_suggestion": suggestion, "content_scores": scores}
            if any(m in normalize(sample) for m in _FREUD_MARKERS) and d["uri"] not in overrides:
                reason = "conteúdo: cabeçalho da Edição Standard"
                meta |= {"auto_line": "freud", "auto_author": "Sigmund Freud", "auto_reason": reason}
                author_id = upsert_author(conn, "Sigmund Freud", "freud")
                conn.execute(
                    "update document set line='freud', author_id=%s, status='in_scope', scope_reason=%s,"
                    " meta = meta || %s where id=%s",
                    (author_id, reason, json.dumps(meta), d["id"]),
                )
                stats["auto_freud"] = stats.get("auto_freud", 0) + 1
            else:
                conn.execute("update document set meta = meta || %s where id=%s", (json.dumps(meta), d["id"]))
                stats[suggestion] = stats.get(suggestion, 0) + 1
            conn.commit()
        export_catalog(conn)
    return stats


# ------------------------------------------------------------------ extração


def _extract_one(conn, d: dict) -> str:
    path = d["local_path"]
    if not path or not Path(path).exists():
        raise FileNotFoundError(path)
    sha = _sha256(path)
    dup = conn.execute(
        "select id from document where sha256=%s and id<>%s and duplicate_of is null"
        " and status in ('extracted','chunked') limit 1",
        (sha, d["id"]),
    ).fetchone()
    if dup:
        conn.execute(
            "update document set sha256=%s, duplicate_of=%s, status='duplicate', updated_at=now() where id=%s",
            (sha, dup["id"], d["id"]),
        )
        return "duplicate"

    if d["file_type"] == "pdf":
        ex = pdf_x.extract(path)
        if ex.ocr_needed and d["meta"].get("ocr_path"):
            ex = pdf_x.extract(d["meta"]["ocr_path"])
    elif d["file_type"] == "docx":
        ex = docx_x.extract(path)
    elif d["file_type"] in ("html", "htm"):
        ex = html_x.extract(path)
    else:
        raise ValueError(f"formato não suportado: {d['file_type']}")

    language = (
        d["language"]
        or lang.detect("\n".join(s.text for s in ex.segments[5:40])[:5000])
        or lang.detect("\n".join(s.text for s in ex.segments)[:5000])
    )
    conn.execute("delete from segment where document_id=%s", (d["id"],))
    with conn.cursor().copy(
        "copy segment (document_id, ordinal, page_no, section_label, session_date, text) from stdin"
    ) as cp:
        for i, s in enumerate(ex.segments):
            cp.write_row((d["id"], i, s.page_no, s.section_label, s.session_date, s.text.replace("\x00", "")))
    n_words = sum(len(s.text.split()) for s in ex.segments)
    conn.execute(
        """update document set sha256=%s, language=%s, n_pages=%s, n_words=%s, ocr_needed=%s,
               status='extracted', error=null, meta = meta || %s, updated_at=now() where id=%s""",
        (sha, language, ex.n_pages, n_words, ex.ocr_needed, json.dumps(ex.meta), d["id"]),
    )
    return "extracted"


def extract(limit: int | None = None, source: str | None = None, retry_failed: bool = True) -> dict:
    statuses = ["in_scope", "failed"] if retry_failed else ["in_scope"]
    with run_step("extract", limit=limit, source=source) as stats, connect() as conn:
        docs = conn.execute(
            "select id, uri, local_path, file_type, language, meta from document"
            " where status = any(%s) and (%s::text is null or source_id=%s) order by id limit %s",
            (statuses, source, source, limit),
        ).fetchall()
        for i, d in enumerate(docs, 1):
            try:
                result = _extract_one(conn, d)
            except Exception as e:
                conn.rollback()
                conn.execute(
                    "update document set status='failed', error=%s where id=%s", (repr(e)[:500], d["id"])
                )
                result = "failed"
                log.warning("falha ao extrair %s: %s", d["uri"], e)
            conn.commit()
            stats[result] = stats.get(result, 0) + 1
            log.info("[%d/%d] %s %s", i, len(docs), result, d["uri"][-90:])
    return stats


def ocr(limit: int | None = None) -> dict:
    """Roda OCR nos PDFs marcados como imagem e os devolve para re-extração."""
    with run_step("ocr", limit=limit) as stats, connect() as conn:
        docs = conn.execute(
            "select id, uri, local_path, sha256 from document where ocr_needed and file_type='pdf'"
            " and status in ('extracted','chunked') and meta->>'ocr_path' is null order by bytes limit %s",
            (limit,),
        ).fetchall()
        for d in docs:
            out = pdf_x.ocr(d["local_path"], d["sha256"])
            if out:
                conn.execute(
                    "update document set meta = meta || %s, status='in_scope' where id=%s",
                    (json.dumps({"ocr_path": str(out)}), d["id"]),
                )
                conn.execute("delete from chunk where document_id=%s", (d["id"],))
                stats["ok"] = stats.get("ok", 0) + 1
            else:
                stats["failed"] = stats.get("failed", 0) + 1
            conn.commit()
    return stats


# ------------------------------------------------------------------ chunks


def _period(session_date: date | None, doc: dict) -> tuple[date | None, str | None]:
    if session_date:
        return session_date, "day"
    y0, y1 = doc["year_start"], doc["year_end"]
    if not y0 and doc["meta"].get("freud_years"):
        y0, y1 = doc["meta"]["freud_years"]
    if y0 and (not y1 or y1 - y0 <= 2):
        return date(y0, 1, 1), "year"
    if doc["year_publication"] and doc["line"] not in ("lacan", "freud"):
        return date(doc["year_publication"], 1, 1), "year"
    return None, None


def fix_session_years(segs: list[dict], y0: int | None, y1: int | None) -> list[str]:
    """Qualidade de dados: sessão com ano fora do período do seminário é erro de digitação da fonte
    (ex.: S12 '23 juin 1966' num seminário de 1964-65). Corrige pelo ano letivo:
    meses de agosto a dezembro → ano inicial; janeiro a julho → ano final. Devolve o log das correções."""
    if not y0:
        return []
    y1 = y1 or y0
    fixes = []
    for sg in segs:
        d = sg["session_date"]
        if d and (sg["section_label"] or "").startswith("Sessão de") and not (y0 <= d.year <= y1):
            new_year = y0 if d.month >= 8 else y1
            fixed = d.replace(year=new_year)
            fixes.append(f"{d.isoformat()} → {fixed.isoformat()}")
            sg["session_date"] = fixed
            sg["section_label"] = f"Sessão de {fixed:%d/%m/%Y}"
    return sorted(set(fixes))


def chunk(limit: int | None = None) -> dict:
    target = settings().chunk_words
    with run_step("chunk", limit=limit, target_words=target) as stats, connect() as conn:
        docs = conn.execute(
            """select d.id, d.uri, d.language, d.line, d.year_publication, d.meta, w.year_start, w.year_end,
                      w.work_type
               from document d left join work w on w.id = d.work_id
               where d.status='extracted' order by d.id limit %s""",
            (limit,),
        ).fetchall()
        for d in docs:
            segs = conn.execute(
                "select text, page_no, section_label, session_date from segment where document_id=%s order by ordinal",
                (d["id"],),
            ).fetchall()
            # Só seminários: escritos/entrevistas podem legitimamente ter datas fora do "ano da obra"
            # (Télévision: gravado em 1973, transmitido em 1974).
            fixes = (
                fix_session_years(segs, d["year_start"], d["year_end"])
                if d["work_type"] == "seminario"
                else []
            )
            if fixes:
                log.warning("datas de sessão corrigidas em %s: %s", d["uri"][-60:], fixes)
                conn.execute(
                    "update document set meta = meta || %s where id=%s",
                    (json.dumps({"session_date_fixes": fixes}), d["id"]),
                )
                stats["session_date_fixes"] = stats.get("session_date_fixes", 0) + len(fixes)
            drafts = chunk_segments(segs, target)
            langs = lang.detect_many([c.text for c in drafts]) if drafts else []
            conn.execute("delete from chunk where document_id=%s", (d["id"],))
            with conn.cursor().copy(
                """copy chunk (document_id, ordinal, text, n_words, page_start, page_end, section_label,
                               session_date, period_date, period_precision, language) from stdin"""
            ) as cp:
                for i, (c, lg) in enumerate(zip(drafts, langs, strict=True)):
                    pdate, prec = _period(c.session_date, d)
                    cp.write_row(
                        (
                            d["id"],
                            i,
                            c.text,
                            c.n_words,
                            c.page_start,
                            c.page_end,
                            c.section_label,
                            c.session_date,
                            pdate,
                            prec,
                            lg or d["language"],
                        )
                    )
            conn.execute("update document set status='chunked', updated_at=now() where id=%s", (d["id"],))
            conn.commit()
            stats["documents"] = stats.get("documents", 0) + 1
            stats["chunks"] = stats.get("chunks", 0) + len(drafts)
    return stats


# ------------------------------------------------------------------ conceitos, embeddings, gold


def concepts(full: bool = True) -> dict:
    """Recalcula as menções de todos os conceitos (rápido o bastante para refazer do zero).

    Atômico: as menções são calculadas primeiro e trocadas numa única transação. Se a etapa
    falhar no meio, a camada gold continua com a versão anterior em vez de ficar vazia; e a API
    segue lendo as menções antigas até o commit (delete, ao contrário de truncate, não bloqueia leituras).
    """
    with run_step("concepts") as stats, connect() as conn:
        ids = sync_lexicon(conn)
        mentions = []
        with conn.cursor(name="chunks") as cur:
            cur.itersize = 2000
            cur.execute("select id, text, language from chunk")
            for row in cur:
                for slug, n in count_mentions(row["text"], row["language"]).items():
                    mentions.append((ids[slug], row["id"], n))
        conn.execute("delete from concept_mention")
        with conn.cursor().copy("copy concept_mention (concept_id, chunk_id, n) from stdin") as cp:
            for r in mentions:
                cp.write_row(r)
        conn.commit()
        stats["mentions_rows"] = len(mentions)
        stats["concepts"] = len(ids)
    return stats


def embed(limit: int | None = None, batch_size: int = 256) -> dict:
    from psilib import embeddings

    model = settings().embedding_model
    with run_step("embed", model=model, limit=limit) as stats, connect() as conn:
        embeddings.ensure_index(conn)
        conn.commit()
        done = 0
        while limit is None or done < limit:
            rows = conn.execute(
                """select c.id, c.text, coalesce(a.name,'') as author, coalesce(w.title, d.title) as work
                   from chunk c join document d on d.id=c.document_id
                   left join author a on a.id=d.author_id left join work w on w.id=d.work_id
                   where d.duplicate_of is null and not exists
                     (select 1 from chunk_embedding e where e.chunk_id=c.id and e.model=%s)
                   order by c.id limit %s""",
                (model, batch_size),
            ).fetchall()
            if not rows:
                break
            # Um cabeçalho curto com autor/obra ajuda a busca a distinguir Lacan de comentadores.
            vecs = embeddings.embed([f"{r['author']} — {r['work']}\n{r['text']}" for r in rows])
            with conn.cursor().copy("copy chunk_embedding (chunk_id, model, embedding) from stdin") as cp:
                for r, v in zip(rows, vecs, strict=True):
                    cp.write_row((r["id"], model, "[" + ",".join(f"{x:.6f}" for x in v) + "]"))
            conn.commit()
            done += len(rows)
            log.info("embeddings: %d", done)
        stats["embedded"] = done
    return stats


def refresh() -> dict:
    with run_step("refresh") as stats, connect() as conn:
        for mv in ("mv_concept_timeline", "mv_concept_cooccurrence"):
            conn.execute(f"refresh materialized view {mv}")
        conn.commit()
        stats["ok"] = True
    return stats
