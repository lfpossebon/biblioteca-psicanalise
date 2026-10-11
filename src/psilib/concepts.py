"""Léxico de conceitos (config/concepts.yaml) → tabelas concept/concept_term → concept_mention."""

import logging
import re
from dataclasses import dataclass
from functools import lru_cache

from psilib.config import load_yaml
from psilib.text import normalize

log = logging.getLogger(__name__)
LANGS = ("pt", "es", "fr", "en", "de")


@dataclass
class CompiledConcept:
    slug: str
    label: str
    family: str | None
    # língua → (regex sem maiúsculas, regex sensível a maiúsculas)
    by_lang: dict[str, tuple[re.Pattern | None, re.Pattern | None]]


def _terms(raw) -> list[tuple[str, bool]]:
    return [(t["p"], bool(t.get("cs"))) if isinstance(t, dict) else (t, False) for t in raw]


@lru_cache
def compiled() -> list[CompiledConcept]:
    out = []
    for c in load_yaml("concepts.yaml")["concepts"]:
        terms = c.get("terms", {})
        common = _terms(terms.get("any", []))
        by_lang = {}
        for lang in LANGS + (None,):
            ts = common + (
                _terms(terms.get(lang, []))
                if lang
                else [t for lng in LANGS for t in _terms(terms.get(lng, []))]
            )
            ci = [p for p, cs in ts if not cs]
            cs = [p for p, cs in ts if cs]
            by_lang[lang] = (
                re.compile("|".join(f"(?:{p})" for p in ci)) if ci else None,
                re.compile("|".join(f"(?:{p})" for p in cs)) if cs else None,
            )
        out.append(CompiledConcept(c["slug"], c["label"], c.get("family"), by_lang))
    return out


def count_mentions(text: str, language: str | None) -> dict[str, int]:
    """Ocorrências de cada conceito no texto, usando só os padrões da língua do texto."""
    lower = normalize(text)
    cased = normalize(text, lower=False)
    lang = language if language in LANGS else None
    out = {}
    for c in compiled():
        ci, cs = c.by_lang[lang]
        n = (len(ci.findall(lower)) if ci else 0) + (len(cs.findall(cased)) if cs else 0)
        if n:
            out[c.slug] = n
    return out


def match_query(query: str) -> list[str]:
    """Conceitos citados numa pergunta (em qualquer língua) — usados para expandir a busca."""
    found = []
    for c in compiled():
        ci, cs = c.by_lang[None]
        if (ci and ci.search(normalize(query))) or (cs and cs.search(normalize(query, lower=False))):
            found.append(c.slug)
        elif normalize(c.label) in normalize(query):
            found.append(c.slug)
    return found


def sync_lexicon(conn) -> dict[str, int]:
    """Espelha o YAML nas tabelas concept/concept_term; devolve slug → id."""
    cfg = load_yaml("concepts.yaml")["concepts"]
    ids = {}
    for c in cfg:
        ids[c["slug"]] = conn.execute(
            """insert into concept (slug, label, family, description) values (%s, %s, %s, %s)
               on conflict (slug) do update set label=excluded.label, family=excluded.family,
                   description=excluded.description
               returning id""",
            (c["slug"], c["label"], c.get("family"), c.get("description")),
        ).fetchone()["id"]
        conn.execute("delete from concept_term where concept_id = %s", (ids[c["slug"]],))
        for lang, raw in (c.get("terms") or {}).items():
            for p, cs in _terms(raw):
                conn.execute(
                    "insert into concept_term (concept_id, language, pattern, case_sensitive) values (%s,%s,%s,%s)"
                    " on conflict do nothing",
                    (ids[c["slug"]], lang, p, cs),
                )
    removed = conn.execute(
        "delete from concept where slug <> all(%s) returning slug", (list(ids),)
    ).fetchall()
    if removed:
        log.info("conceitos removidos do léxico: %s", [r["slug"] for r in removed])
    return ids
