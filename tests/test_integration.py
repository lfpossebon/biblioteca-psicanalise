"""Integração: etapas do pipeline contra um Postgres real (banco descartável psilib_test)."""

import pytest
from conftest import SESSIONS

from psilib import db, pipeline, service
from psilib.service import Filters

pytestmark = pytest.mark.integration


def _count(sql: str) -> int:
    with db.connect() as conn:
        return conn.execute(sql).fetchone()["n"]


def test_chunk_keeps_session_dates(seeded):
    assert seeded["stats"]["chunk"]["chunks"] == len(SESSIONS)
    with db.connect() as conn:
        rows = conn.execute(
            "select session_date, period_precision, language from chunk where document_id=%s order by ordinal",
            (seeded["document_id"],),
        ).fetchall()
    assert [r["session_date"] for r in rows] == SESSIONS
    assert {r["period_precision"] for r in rows} == {"day"}
    assert {r["language"] for r in rows} == {"pt"}


def test_concepts_is_idempotent(seeded):
    before = _count("select count(*) as n from concept_mention")
    assert before > 0
    pipeline.concepts()
    assert _count("select count(*) as n from concept_mention") == before


def test_concepts_failure_keeps_previous_mentions(seeded, monkeypatch):
    """Etapa atômica: se falhar no meio, a camada gold continua com a versão anterior."""
    before = _count("select count(*) as n from concept_mention")

    def boom(*_args):
        raise RuntimeError("falha simulada")

    monkeypatch.setattr(pipeline, "count_mentions", boom)
    with pytest.raises(RuntimeError):
        pipeline.concepts()
    assert _count("select count(*) as n from concept_mention") == before
    assert _count("select count(*) as n from pipeline_run where step='concepts' and status='failed'") >= 1


def test_text_search_returns_citation(seeded):
    res = service.search("gozo", k=3, filters=Filters(lines=["lacan"]), mode="text")
    assert res["estrategias"] == ["texto"]
    assert res["resultados"]
    assert "sessão de" in res["resultados"][0]["citacao"]


def test_timeline_by_session(seeded):
    res = service.timeline("gozo", "sessao", min_words=0)
    assert [p["periodo"] for p in res["serie"]] == [d.isoformat() for d in SESSIONS]
    assert all(p["por_10k"] > 0 for p in res["serie"])


def test_cooccurrence_finds_desire(seeded):
    res = service.cooccurrence("gozo")
    assert "desejo" in {r["slug"] for r in res["relacionados"]}
