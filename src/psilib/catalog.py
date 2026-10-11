"""Catálogo: autores, obras e documentos no banco + curadoria humana via CSV versionado.

- catalog/overrides.csv  → decisões manuais (editado por você; vence as regras automáticas)
- catalog/catalogo.csv   → retrato gerado do acervo (não editar; regenerado a cada inventário)
"""

import csv
import json
import unicodedata
from dataclasses import dataclass, field

from psilib.config import settings
from psilib.scope import WorkRef, line_in_scope

OVERRIDE_FIELDS = ["uri", "incluir", "linha", "autor", "obra", "lingua", "ano", "nota"]


@dataclass
class DocSpec:
    source_id: str
    uri: str
    title: str
    local_path: str | None
    author: str | None
    line: str
    in_scope: bool
    scope_reason: str
    work: WorkRef | None = None
    language: str | None = None
    year_publication: int | None = None
    file_type: str | None = None
    bytes: int | None = None
    meta: dict = field(default_factory=dict)


def load_overrides() -> dict[str, dict]:
    path = settings().catalog_dir / "overrides.csv"
    if not path.exists():
        return {}
    with open(path, encoding="utf-8", newline="") as f:
        return {unicodedata.normalize("NFC", row["uri"]): row for row in csv.DictReader(f) if row.get("uri")}


def apply_override(spec: DocSpec, ov: dict | None) -> DocSpec:
    if not ov:
        return spec
    if ov.get("linha"):
        spec.line = ov["linha"]
        spec.in_scope = line_in_scope(spec.line)
    if ov.get("autor"):
        spec.author = ov["autor"]
    if ov.get("lingua"):
        spec.language = ov["lingua"]
    if ov.get("ano"):
        spec.year_publication = int(ov["ano"])
    if ov.get("incluir", "").strip().lower() in ("sim", "s", "yes", "true", "1"):
        spec.in_scope = True
    elif ov.get("incluir", "").strip().lower() in ("nao", "não", "n", "no", "false", "0"):
        spec.in_scope = False
    spec.scope_reason = f"override: {ov.get('nota') or 'manual'}"
    if ov.get("obra"):
        spec.meta["work_override"] = ov["obra"]
    return spec


def upsert_author(conn, name: str | None, line: str) -> int | None:
    if not name:
        return None
    return conn.execute(
        """insert into author (name, line, in_scope) values (%s, %s, %s)
           on conflict (name) do update set line = excluded.line, in_scope = excluded.in_scope
           returning id""",
        (name, line, line_in_scope(line)),
    ).fetchone()["id"]


def upsert_work(conn, work: WorkRef | None, author_id: int | None) -> int | None:
    if not work:
        return None
    return conn.execute(
        """insert into work (slug, title, author_id, work_type, seminar_number, year_start, year_end)
           values (%s, %s, %s, %s, %s, %s, %s)
           on conflict (slug) do update set title = excluded.title, year_start = excluded.year_start,
               year_end = excluded.year_end, author_id = coalesce(excluded.author_id, work.author_id)
           returning id""",
        (work.slug, work.title, author_id, work.work_type, work.seminar_number, work.year_start, work.year_end),
    ).fetchone()["id"]


def upsert_document(conn, spec: DocSpec, changed: bool = False) -> tuple[int, str]:
    """Insere/atualiza; devolve (id, status).

    Documentos que saem do escopo perdem seus chunks; documentos cujo arquivo mudou
    (`changed` ou tamanho diferente) voltam para 'in_scope' e são reprocessados.
    """
    row = conn.execute("select id, status, bytes, meta from document where uri = %s", (spec.uri,)).fetchone()
    # Decisões da sondagem de conteúdo (psilib probe) valem para arquivos que as regras não reconhecem.
    if row is not None and spec.line == "revisar" and row["meta"].get("auto_line"):
        spec.line, spec.author = row["meta"]["auto_line"], row["meta"].get("auto_author")
        spec.in_scope = line_in_scope(spec.line)
        spec.scope_reason = row["meta"].get("auto_reason", "sondagem de conteúdo")
    author_id = upsert_author(conn, spec.author, spec.line)
    work_id = upsert_work(conn, spec.work, author_id)
    new_status = "in_scope" if spec.in_scope else "excluded"
    if row is not None and row["bytes"] is not None and spec.bytes is not None and row["bytes"] != spec.bytes:
        changed = True
    if row is None:
        doc_id = conn.execute(
            """insert into document (source_id, uri, local_path, title, author_id, work_id, line, language,
                   year_publication, file_type, bytes, status, scope_reason, meta)
               values (%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s) returning id""",
            (spec.source_id, spec.uri, spec.local_path, spec.title, author_id, work_id, spec.line,
             spec.language, spec.year_publication, spec.file_type, spec.bytes, new_status,
             spec.scope_reason, json.dumps(spec.meta)),
        ).fetchone()["id"]
        return doc_id, new_status

    doc_id, status = row["id"], row["status"]
    if not spec.in_scope or changed:
        conn.execute("delete from segment where document_id = %s", (doc_id,))
        conn.execute("delete from chunk where document_id = %s", (doc_id,))
        status = "in_scope" if spec.in_scope else "excluded"
    elif status in ("excluded", "discovered", "failed"):
        status = "in_scope"
    conn.execute(
        """update document set local_path=%s, title=%s, author_id=%s, work_id=%s, line=%s,
               language=coalesce(%s, language), year_publication=coalesce(%s, year_publication),
               file_type=%s, bytes=%s, status=%s, scope_reason=%s, meta = meta || %s, updated_at=now()
           where id=%s""",
        (spec.local_path, spec.title, author_id, work_id, spec.line, spec.language, spec.year_publication,
         spec.file_type, spec.bytes, status, spec.scope_reason, json.dumps(spec.meta), doc_id),
    )
    return doc_id, status


def export_catalog(conn) -> int:
    rows = conn.execute(
        """select d.uri, d.status, d.line as linha, a.name as autor, w.slug as obra, d.language as lingua,
                  coalesce(w.year_start, d.year_publication) as ano, d.scope_reason as motivo,
                  d.meta->>'content_suggestion' as sugestao_conteudo, d.n_words as palavras,
                  d.ocr_needed as precisa_ocr, dup.uri as duplicata_de
           from document d
           left join author a on a.id = d.author_id
           left join work w on w.id = d.work_id
           left join document dup on dup.id = d.duplicate_of
           order by d.status, d.line, d.uri"""
    ).fetchall()
    path = settings().catalog_dir / "catalogo.csv"
    path.parent.mkdir(parents=True, exist_ok=True)
    with open(path, "w", encoding="utf-8", newline="") as f:
        w = csv.DictWriter(f, fieldnames=list(rows[0].keys()) if rows else ["uri"])
        w.writeheader()
        w.writerows(rows)
    return len(rows)
