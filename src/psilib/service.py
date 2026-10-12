"""Consultas sobre a biblioteca — usadas pela API REST e pelo servidor MCP.

Toda resposta traz a citação completa (autor, obra, sessão/data, página, língua, fonte),
para que a IA que consome a biblioteca possa sempre apontar de onde veio cada trecho.
"""

import logging
from dataclasses import dataclass

from psilib.concepts import match_query
from psilib.config import settings
from psilib.db import connect

log = logging.getLogger(__name__)
RRF_K = 60


@dataclass
class Filters:
    lines: list[str] | None = None  # ex.: ["lacan"] ou ["escola_argentina", "lacaniano"]
    author: str | None = None  # substring, sem acento/maiúsculas
    work: str | None = None  # slug ou substring do título (ex.: "seminario-11")
    language: str | None = None
    year_from: int | None = None
    year_to: int | None = None
    source: str | None = None  # local | staferla | openalex

    def sql(self, alias: str = "v") -> tuple[str, dict]:
        conds, p = [], {}
        if self.lines:
            conds.append(f"{alias}.line = any(%(f_lines)s)")
            p["f_lines"] = self.lines
        if self.author:
            conds.append(
                f"f_unaccent(lower({alias}.author)) like '%%' || f_unaccent(lower(%(f_author)s)) || '%%'"
            )
            p["f_author"] = self.author
        if self.work:
            conds.append(
                f"({alias}.work_slug ilike '%%' || %(f_work)s || '%%'"
                f" or f_unaccent(lower(coalesce({alias}.work_title, {alias}.doc_title)))"
                f" like '%%' || f_unaccent(lower(%(f_work)s)) || '%%')"
            )
            p["f_work"] = self.work
        if self.language:
            conds.append(f"{alias}.language = %(f_lang)s")
            p["f_lang"] = self.language
        if self.year_from:
            conds.append(f"{alias}.period_date >= make_date(%(f_y0)s, 1, 1)")
            p["f_y0"] = self.year_from
        if self.year_to:
            conds.append(f"{alias}.period_date <= make_date(%(f_y1)s, 12, 31)")
            p["f_y1"] = self.year_to
        if self.source:
            conds.append(f"{alias}.source_id = %(f_source)s")
            p["f_source"] = self.source
        return (" and ".join(conds) or "true"), p


def db_ok() -> bool:
    try:
        with connect() as conn:
            conn.execute("select 1")
        return True
    except Exception as e:
        log.warning("banco indisponível: %s", e)
        return False


def citation(r: dict) -> str:
    parts = [r.get("author") or "?"]
    parts.append(r.get("work_title") or r.get("doc_title"))
    if r.get("session_date"):
        parts.append(f"sessão de {r['session_date']:%d/%m/%Y}")
    elif r.get("period_date"):
        parts.append(str(r["period_date"].year))
    if r.get("page_start"):
        pages = (
            r["page_start"] if r["page_start"] == r.get("page_end") else f"{r['page_start']}-{r['page_end']}"
        )
        parts.append(f"p. {pages}")
    parts.append(f"[{r.get('language') or '?'}, {r.get('source_id')}]")
    return ", ".join(str(p) for p in parts if p)


def _hit(r: dict, score: float | None = None, max_chars: int | None = None) -> dict:
    text = r["text"] if not max_chars or len(r["text"]) <= max_chars else r["text"][:max_chars] + "…"
    return {
        "chunk_id": r["chunk_id"],
        "score": round(score, 5) if score is not None else None,
        "citacao": citation(r),
        "autor": r["author"],
        "linha": r["line"],
        "obra": r["work_title"] or r["doc_title"],
        "obra_slug": r["work_slug"],
        "sessao": r["session_date"].isoformat() if r["session_date"] else None,
        "periodo": r["period_date"].isoformat() if r["period_date"] else None,
        "paginas": [r["page_start"], r["page_end"]] if r["page_start"] else None,
        "lingua": r["language"],
        "fonte": r["source_id"],
        "uri": r["uri"],
        "texto": text,
    }


def search(
    query: str,
    k: int = 10,
    filters: Filters | None = None,
    mode: str = "hybrid",
    max_chars: int | None = 1500,
) -> dict:
    """Busca híbrida: texto (FTS) + semântica (vetores, entre línguas) + conceitos, fundidas por RRF."""
    filters = filters or Filters()
    where, p = filters.sql("v")
    p |= {"q": query, "k": k, "n": max(50, k * 5)}
    concept_slugs = match_query(query)
    p["concepts"] = concept_slugs
    rankings = []

    with connect() as conn:
        if mode in ("hybrid", "text"):
            rankings.append(
                (
                    "texto",
                    conn.execute(
                        f"""select v.chunk_id from v_chunk_context v
                    join chunk c on c.id = v.chunk_id,
                         websearch_to_tsquery('simple', f_unaccent(lower(%(q)s))) q
                    where c.tsv @@ q and {where}
                    order by ts_rank_cd(c.tsv, q) desc limit %(n)s""",
                        p,
                    ).fetchall(),
                )
            )

        if mode in ("hybrid", "vector"):
            try:
                from psilib.embeddings import embed

                s = settings()
                p["vec"] = "[" + ",".join(f"{x:.6f}" for x in embed([query], kind="query")[0]) + "]"
                p["model"] = s.embedding_model
                conn.execute("set local hnsw.iterative_scan = relaxed_order")
                conn.execute("set local hnsw.ef_search = 200")
                rankings.append(
                    (
                        "semantica",
                        conn.execute(
                            f"""select v.chunk_id from chunk_embedding e
                        join v_chunk_context v on v.chunk_id = e.chunk_id
                        where e.model = %(model)s and {where}
                        order by e.embedding::vector({s.embedding_dim}) <=> %(vec)s::vector({s.embedding_dim})
                        limit %(n)s""",
                            p,
                        ).fetchall(),
                    )
                )
            except Exception as e:  # sem embeddings ainda: segue só com texto
                conn.rollback()
                log.warning("busca semântica indisponível: %s", e)

        if concept_slugs and mode == "hybrid":
            rankings.append(
                (
                    "conceito",
                    conn.execute(
                        f"""select v.chunk_id from concept_mention m
                    join concept c on c.id = m.concept_id
                    join v_chunk_context v on v.chunk_id = m.chunk_id
                    where c.slug = any(%(concepts)s) and {where}
                    group by v.chunk_id, v.n_words
                    order by sum(m.n)::float / greatest(v.n_words, 50) desc limit %(n)s""",
                        p,
                    ).fetchall(),
                )
            )

        scores: dict[int, float] = {}
        for _, rows in rankings:
            for rank, r in enumerate(rows, 1):
                scores[r["chunk_id"]] = scores.get(r["chunk_id"], 0) + 1 / (RRF_K + rank)
        top = sorted(scores, key=scores.get, reverse=True)[:k]
        rows = (
            {
                r["chunk_id"]: r
                for r in conn.execute(
                    "select * from v_chunk_context where chunk_id = any(%s)", (top,)
                ).fetchall()
            }
            if top
            else {}
        )

    return {
        "consulta": query,
        "conceitos_reconhecidos": concept_slugs,
        "estrategias": [name for name, _ in rankings],
        "resultados": [_hit(rows[i], scores[i], max_chars) for i in top if i in rows],
    }


def read_chunk(chunk_id: int, context: int = 1) -> dict:
    """Um trecho com `context` trechos vizinhos de cada lado (mesma obra), para ler em continuidade."""
    with connect() as conn:
        base = conn.execute("select * from v_chunk_context where chunk_id=%s", (chunk_id,)).fetchone()
        if not base:
            return {"erro": f"chunk {chunk_id} não encontrado"}
        rows = conn.execute(
            "select * from v_chunk_context where document_id=%s and ordinal between %s and %s order by ordinal",
            (base["document_id"], base["ordinal"] - context, base["ordinal"] + context),
        ).fetchall()
    return {"citacao": citation(base), "trechos": [_hit(r) for r in rows]}


def list_concepts() -> list[dict]:
    with connect() as conn:
        return conn.execute(
            """select c.slug, c.label, c.family, coalesce(sum(m.n), 0) as mencoes,
                      count(distinct m.chunk_id) as trechos
               from concept c left join concept_mention m on m.concept_id = c.id
               group by c.id order by c.family, c.label"""
        ).fetchall()


def _concept_slug(conn, concept: str) -> str | None:
    row = conn.execute(
        """select slug from concept where slug = %(c)s or f_unaccent(lower(label)) = f_unaccent(lower(%(c)s))
           limit 1""",
        {"c": concept},
    ).fetchone()
    if row:
        return row["slug"]
    found = match_query(concept)
    return found[0] if found else None


def timeline(
    concept: str, granularity: str = "obra", filters: Filters | None = None, min_words: int = 2000
) -> dict:
    """Frequência relativa (por 10 mil palavras) de um conceito ao longo do tempo.

    granularity: 'sessao' (datas exatas — seminários do Staferla), 'ano' ou 'obra'.
    """
    filters = filters or Filters()
    with connect() as conn:
        slug = _concept_slug(conn, concept)
        if not slug:
            return {"erro": f"conceito '{concept}' não está no léxico (veja listar_conceitos)"}
        where, p = filters.sql("t")
        p |= {"slug": slug, "min_words": min_words}
        group = {
            "sessao": ("t.period_date", "t.period_precision = 'day'"),
            "ano": ("date_trunc('year', t.period_date)::date", "t.period_date is not null"),
            "obra": ("min(t.period_date)", "t.period_date is not null"),
        }[granularity]
        if granularity == "obra":
            # Sem obra canônica, o título do próprio documento serve de rótulo.
            sql = f"""select coalesce(t.work_slug, 'doc-' || t.document_id) as chave,
                             max(coalesce(t.work_title, d.title, '')) as obra, max(t.author) as autor,
                             min(t.period_date) as periodo, sum(t.mentions) as mencoes,
                             sum(t.words) as palavras,
                             round(10000.0 * sum(t.mentions) / nullif(sum(t.words), 0), 2) as por_10k
                      from mv_concept_timeline t join document d on d.id = t.document_id
                      where t.concept = %(slug)s and {group[1]} and {where}
                      group by 1 having sum(t.words) >= %(min_words)s order by periodo"""
        else:
            sql = f"""select {group[0]} as periodo, string_agg(distinct t.author, '; ') as autores,
                             string_agg(distinct coalesce(t.seminar_number, ''), ',') as seminarios,
                             sum(t.mentions) as mencoes, sum(t.words) as palavras,
                             round(10000.0 * sum(t.mentions) / nullif(sum(t.words), 0), 2) as por_10k
                      from mv_concept_timeline t
                      where t.concept = %(slug)s and {group[1]} and {where}
                      group by 1 having sum(t.words) >= %(min_words)s order by 1"""
        rows = conn.execute(sql, p).fetchall()
        first = conn.execute(
            f"""select t.author, min(t.period_date) as primeira
                from mv_concept_timeline t where t.concept=%(slug)s and t.mentions > 0
                  and t.period_date is not null and {where}
                group by t.author order by 2 limit 15""",
            p,
        ).fetchall()
    for r in rows:
        for key in ("periodo",):
            if r.get(key):
                r[key] = r[key].isoformat()
    return {
        "conceito": slug,
        "granularidade": granularity,
        "metrica": "ocorrências por 10 mil palavras (normalizado pelo tamanho de cada período)",
        "serie": rows,
        "primeiras_aparicoes": [{"autor": r["author"], "data": r["primeira"].isoformat()} for r in first],
    }


def cooccurrence(concept: str, limit: int = 15) -> dict:
    """Conceitos que aparecem nos mesmos trechos, ordenados por PMI (associação acima do acaso)."""
    with connect() as conn:
        slug = _concept_slug(conn, concept)
        if not slug:
            return {"erro": f"conceito '{concept}' não encontrado"}
        rows = conn.execute(
            """with tot as (select count(distinct chunk_id)::float as n from concept_mention),
                    freq as (select concept_id, count(*)::float as n from concept_mention group by 1),
                    me as (select id from concept where slug = %s),
                    pairs as (select case when c1 = me.id then c2 else c1 end as other, n_chunks
                              from mv_concept_cooccurrence, me where c1 = me.id or c2 = me.id)
               select c.slug, c.label, p.n_chunks as trechos_juntos,
                      round(ln((p.n_chunks * tot.n) / (fa.n * fb.n))::numeric, 3) as pmi
               from pairs p join concept c on c.id = p.other
               join freq fa on fa.concept_id = p.other
               join freq fb on fb.concept_id = (select id from me)
               cross join tot
               where p.n_chunks >= 5
               order by pmi desc limit %s""",
            (slug, limit),
        ).fetchall()
    return {"conceito": slug, "relacionados": rows}


def list_works(filters: Filters | None = None) -> list[dict]:
    filters = filters or Filters()
    where, p = filters.sql("v")
    with connect() as conn:
        return conn.execute(
            f"""select v.author as autor, v.line as linha, coalesce(v.work_title, v.doc_title) as obra,
                       v.work_slug as slug, v.language as lingua, v.source_id as fonte,
                       min(v.period_date) as periodo, count(*) as trechos, sum(v.n_words) as palavras
                from v_chunk_context v where {where}
                group by 1,2,3,4,5,6 order by periodo nulls last, autor limit 500""",
            p,
        ).fetchall()


def stats() -> dict:
    with connect() as conn:
        docs = conn.execute(
            "select status, count(*) as n from document group by 1 order by 2 desc"
        ).fetchall()
        lines = conn.execute(
            """select d.line, count(distinct d.id) as documentos, count(c.id) as trechos,
                      coalesce(sum(c.n_words), 0) as palavras
               from document d left join chunk c on c.document_id = d.id
               where d.status = 'chunked' group by 1 order by 4 desc"""
        ).fetchall()
        emb = conn.execute("select model, count(*) as n from chunk_embedding group by 1").fetchall()
        runs = conn.execute(
            "select step, status, started_at, finished_at from pipeline_run order by id desc limit 10"
        ).fetchall()
    return {
        "documentos_por_status": docs,
        "acervo_por_linha": lines,
        "embeddings": emb,
        "ultimas_execucoes": runs,
    }
