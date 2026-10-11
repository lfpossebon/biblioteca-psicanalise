"""Monitor de publicações novas via OpenAlex (cobre SciELO, Redalyc, periódicos e repositórios).

Itens novos entram em `watch_item` como 'pending'. Nada entra no acervo sem aceite
(`psilib watch accept`), porque o escopo é curado.
"""

import json
import logging
from datetime import date, timedelta

import httpx

from psilib.config import load_yaml, settings
from psilib.text import normalize

log = logging.getLogger(__name__)
API = "https://api.openalex.org/works"


def _abstract(inv: dict | None) -> str | None:
    if not inv:
        return None
    pos = sorted((i, w) for w, idxs in inv.items() for i in idxs)
    return " ".join(w for _, w in pos)


def search(since: date, max_per_query: int = 200) -> list[dict]:
    cfg = load_yaml("sources.yaml")["openalex"]
    s = settings()
    params_base = {"per_page": 100, "select": ",".join(cfg["fields"])}
    if s.openalex_mailto:
        params_base["mailto"] = s.openalex_mailto
    items: dict[str, dict] = {}
    with httpx.Client(timeout=60, headers={"User-Agent": s.http_user_agent}) as http:
        for q in cfg["queries"]:
            cursor = "*"
            fetched = 0
            while cursor and fetched < max_per_query:
                params = params_base | {
                    "filter": f"title_and_abstract.search:{q},from_publication_date:{since.isoformat()},"
                    f"type:{'|'.join(cfg['types'])}",
                    "cursor": cursor,
                }
                r = http.get(API, params=params)
                r.raise_for_status()
                data = r.json()
                for w in data["results"]:
                    if is_relevant(w, cfg["require_terms"]):
                        items[w["id"]] = w
                fetched += len(data["results"])
                cursor = data["meta"].get("next_cursor") if data["results"] else None
            log.info("openalex '%s': %d itens", q, fetched)
    return list(items.values())


def is_relevant(w: dict, terms: list[str]) -> bool:
    text = normalize(f"{w.get('display_name') or ''} {_abstract(w.get('abstract_inverted_index')) or ''}")
    return any(t in text for t in terms)


def to_row(w: dict) -> dict:
    oa = w.get("open_access") or {}
    loc = w.get("best_oa_location") or w.get("primary_location") or {}
    return {
        "external_id": w["id"].rsplit("/", 1)[-1],
        "title": w.get("display_name"),
        "authors": "; ".join(a["author"]["display_name"] for a in (w.get("authorships") or [])[:8]),
        "year": w.get("publication_year"),
        "language": w.get("language"),
        "url": w.get("doi") or loc.get("landing_page_url") or w["id"],
        "oa_pdf_url": oa.get("oa_url")
        if (oa.get("oa_url") or "").lower().endswith(".pdf")
        else (loc.get("pdf_url")),
        "abstract": _abstract(w.get("abstract_inverted_index")),
        "payload": json.dumps({k: w.get(k) for k in ("id", "doi", "publication_date", "type")}),
    }


def default_since(conn) -> date:
    row = conn.execute(
        "select max(first_seen)::date as d from watch_item where source_id='openalex'"
    ).fetchone()
    return (
        (row["d"] - timedelta(days=7))
        if row and row["d"]
        else date.fromisoformat(load_yaml("sources.yaml")["openalex"]["start_date"])
    )
