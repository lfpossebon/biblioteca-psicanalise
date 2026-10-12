"""Figuras do chat: especificações montadas com dados do banco e o fluxo do evento `visual`."""

import json

import pytest
from test_chat import ANA, KEY_ANA, _install, _message

from psilib import chat, db, visuals
from psilib.service import Filters

pytestmark = pytest.mark.integration


@pytest.fixture(autouse=True)
def short_sessions(monkeypatch):
    # as sessões do banco de teste têm ~120 palavras; no acervo real cada sessão tem milhares
    monkeypatch.setattr(visuals, "MIN_WORDS", 0)


def test_timeline_by_session_with_tiles(seeded):
    spec = visuals.concept_timeline(["gozo"], "sessao", Filters(lines=["lacan"]), None)
    assert spec["tipo"] == "linha" and spec["granularidade"] == "sessao"
    (serie,) = spec["series"]
    assert serie["nome"] == "Gozo" and len(serie["pontos"]) == 6
    assert serie["pontos"][0]["rotulo"] == "14/11/1962 · Seminário 10"
    assert all(p["y"] > 0 and p["mencoes"] > 0 for p in serie["pontos"])
    assert [t["rotulo"] for t in spec["fichas"]] == ["Primeira aparição", "Pico", "Menções no período"]
    assert spec["fichas"][0]["valor"] == "1962"
    assert "Lacan" in spec["subtitulo"] and spec["titulo"] == "Gozo ao longo do tempo"


def test_timeline_compares_up_to_three_concepts(seeded):
    spec = visuals.concept_timeline(["gozo", "desejo", "objeto-a", "real"], "ano", Filters(), "Comparação")
    assert [s["nome"] for s in spec["series"]] == ["Gozo", "Desejo", "Objeto a"]  # corta na 3ª cor
    assert spec["titulo"] == "Comparação"
    assert all(t["rotulo"].startswith("Pico de") for t in spec["fichas"])
    assert {p["rotulo"] for p in spec["series"][0]["pontos"]} == {"1962", "1963"}


def test_unknown_concept_and_empty_scope(seeded):
    with pytest.raises(visuals.VisualError, match="não está no léxico"):
        visuals.concept_timeline(["inexistente"], "ano", Filters(), None)
    with pytest.raises(visuals.VisualError, match="sem dados"):
        visuals.concept_timeline(["gozo"], "ano", Filters(lines=["freud"]), None)


def test_weight_by_work_uses_work_title(seeded):
    spec = visuals.weight_by_work("gozo", Filters(), None)
    assert spec["tipo"] == "barras" and spec["titulo"] == "Onde gozo pesa mais"
    (bar,) = spec["barras"]
    assert bar["rotulo"] == "A angústia" and bar["valor"] > 0
    assert bar["detalhe"].startswith("Jacques Lacan · 1962 ·")


def test_related_keeps_only_positive_association(seeded, monkeypatch):
    # no banco de teste os três conceitos aparecem em todos os trechos: PMI = 0, nada acima do acaso
    with pytest.raises(visuals.VisualError, match="poucos trechos"):
        visuals.related("gozo", None)
    fake = {"conceito": "gozo", "relacionados": [
        {"slug": "desejo", "label": "Desejo", "trechos_juntos": 40, "pmi": 1.2},
        {"slug": "real", "label": "Real", "trechos_juntos": 9, "pmi": -0.3},
    ]}  # fmt: skip
    monkeypatch.setattr(visuals.service, "cooccurrence", lambda c, n: fake)
    spec = visuals.related("gozo", None)
    assert [b["rotulo"] for b in spec["barras"]] == ["Desejo"]
    assert spec["barras"][0]["detalhe"] == "40 trechos em comum"


def test_summary_is_compact_and_names_the_figure(seeded):
    spec = visuals.concept_timeline(["gozo"], "sessao", Filters(lines=["lacan"]), None)
    spec["figura"] = 2
    out = visuals.summary(spec)
    assert out["figura"] == 2 and "Figura 2" in out["instrucao"]
    assert len(out["series"][0]["picos"]) == 3
    assert "pontos" not in json.dumps(out["series"][0]["picos"])  # nada da série inteira


def _show(kind="linha_do_tempo", conceitos=("gozo",), tid="toolu_v"):
    return {"type": "tool_use", "id": tid, "name": "mostrar_visual",
            "input": {"tipo": kind, "conceitos": list(conceitos), "granularidade": "sessao", "linhas": ["lacan"]}}  # fmt: skip


def test_chat_emits_visual_before_text_and_stores_it(chat_env, monkeypatch):
    def answer_step(kwargs):
        result = json.loads(kwargs["messages"][-1]["content"][0]["content"])
        assert result["figura"] == 1 and result["exibida"] is True
        return _message([{"type": "text", "text": "Como mostra a Figura 1, o gozo..."}], "end_turn")

    _install(monkeypatch, [_message([_show()], "tool_use"), answer_step])
    events = list(chat.answer(ANA, KEY_ANA, "Como o gozo evolui no Seminário 10?"))
    kinds = [e["tipo"] for e in events]
    assert kinds.index("visual") < kinds.index("texto")
    visual = next(e["visual"] for e in events if e["tipo"] == "visual")
    assert visual["figura"] == 1 and len(visual["series"][0]["pontos"]) == 6
    assert "Desenhando a figura" in [e.get("texto") for e in events if e["tipo"] == "status"]

    with db.connect() as conn:
        stored = conn.execute(
            "select visuals from chat_turn where id = %s", (events[-1]["turno"],)
        ).fetchone()
    assert stored["visuals"][0]["titulo"] == "Gozo ao longo do tempo"


def test_chat_limits_figures_and_reports_errors(chat_env, monkeypatch):
    many = _message([_show(tid=f"toolu_{i}") for i in range(4)], "tool_use")

    def check(kwargs):
        results = kwargs["messages"][-1]["content"]
        assert [r["is_error"] for r in results] == [False, False, False, True]
        assert "limite de 3 figuras" in results[3]["content"]
        return _message([{"type": "text", "text": "Três figuras."}], "end_turn")

    _install(monkeypatch, [many, check])
    events = list(chat.answer(ANA, KEY_ANA, "Muitas figuras"))
    assert [e["visual"]["figura"] for e in events if e["tipo"] == "visual"] == [1, 2, 3]

    bad = _message([_show(conceitos=("inexistente",))], "tool_use")

    def check_bad(kwargs):
        r = kwargs["messages"][-1]["content"][0]
        assert r["is_error"] and "não está no léxico" in r["content"]
        return _message([{"type": "text", "text": "Ok."}], "end_turn")

    _install(monkeypatch, [bad, check_bad])
    events = list(chat.answer(ANA, KEY_ANA, "Conceito estranho"))
    assert "visual" not in [e["tipo"] for e in events]
