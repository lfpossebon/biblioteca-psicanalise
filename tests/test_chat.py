"""Chat da POC: laço de ferramentas, citações, limites e modo público.

A Claude API é substituída por um cliente falso que devolve mensagens reais do SDK (BetaMessage):
nenhum teste gasta tokens nem precisa de rede.
"""

import json
from types import SimpleNamespace

import pytest
from anthropic.types.beta import BetaMessage
from fastapi.testclient import TestClient

from psilib import chat, config, db, embeddings

ANA = "11111111-1111-4111-8111-111111111111"
BRUNO = "22222222-2222-4222-8222-222222222222"
KEY_ANA, KEY_BRUNO = "ip-ana", "ip-bruno"


# ------------------------------------------------------------------ unitários


def test_visitor_and_client_key():
    assert chat.visitor_id(ANA) == ANA
    assert chat.visitor_id("<script>") == "anonimo"
    assert chat.visitor_id(None) == "anonimo"
    k = chat.client_key("200.1.2.3")
    assert len(k) == 16 and "200" not in k  # hash: o IP não é guardado
    assert k == chat.client_key("200.1.2.3") != chat.client_key("200.1.2.4")


def test_resolve_citations_drops_unseen_ids():
    seen = {"10": {"citacao": "Lacan, S10", "autor": "Lacan", "obra": "S10", "trecho": "..."}}
    notes, invalid = chat.resolve_citations("A [§10] e B [§99] e de novo A [§10].", seen)
    assert [n["chunk_id"] for n in notes] == [10]
    assert notes[0]["n"] == 1
    assert invalid == [99]


def test_tool_definitions_are_valid_schemas():
    defs = chat.tool_definitions()
    assert [d["name"] for d in defs] == list(chat.TOOLS)
    buscar = defs[0]
    assert buscar["input_schema"]["required"] == ["consulta"]
    assert buscar["input_schema"]["properties"]["k"]["maximum"] == 10
    assert all(d["description"] for d in defs)


def test_run_tool_rejects_invalid_input():
    content, is_error = chat.run_tool("buscar", {"k": 50}, {})
    assert is_error
    assert json.loads(content)["erro"] == "argumentos inválidos"
    _, is_error = chat.run_tool("apagar_tudo", {}, {})
    assert is_error


# ------------------------------------------------------------------ cliente falso da Claude API


def _message(content: list[dict], stop_reason: str) -> BetaMessage:
    return BetaMessage.model_validate({
        "id": "msg_teste", "type": "message", "role": "assistant", "model": "claude-opus-5-5",
        "content": content, "stop_reason": stop_reason, "stop_sequence": None,
        "usage": {"input_tokens": 1000, "output_tokens": 200, "cache_read_input_tokens": 500,
                  "cache_creation_input_tokens": 0},
    })  # fmt: skip


class FakeStream:
    def __init__(self, msg: BetaMessage):
        self.msg = msg

    def __enter__(self):
        return self

    def __exit__(self, *exc):
        return False

    def __iter__(self):
        for block in self.msg.content:
            if block.type == "text":
                yield SimpleNamespace(type="text", text=block.text)

    def get_final_message(self):
        return self.msg


class FakeClient:
    """Responde com uma sequência roteirizada de mensagens e guarda cada requisição."""

    def __init__(self, script: list):
        self.script = list(script)
        self.calls: list[dict] = []
        self.beta = SimpleNamespace(messages=SimpleNamespace(stream=self._stream))

    def _stream(self, **kwargs):
        self.calls.append(json.loads(json.dumps(kwargs, default=str)))
        step = self.script.pop(0)
        return FakeStream(step(kwargs) if callable(step) else step)


@pytest.fixture
def chat_env(seeded, monkeypatch):
    def no_vectors(*_a, **_k):
        raise RuntimeError("sem modelo nos testes")

    monkeypatch.setattr(embeddings, "embed", no_vectors)  # busca segue só com texto + conceitos
    yield
    monkeypatch.undo()
    config.settings.cache_clear()


def _install(monkeypatch, script) -> FakeClient:
    fake = FakeClient(script)
    monkeypatch.setattr(chat, "_client", lambda: fake)
    return fake


def _search_then_answer(answer_text: str):
    """1ª chamada pede a busca; 2ª responde citando o primeiro trecho devolvido (e um id inventado)."""

    def second(kwargs):
        tool_result = kwargs["messages"][-1]["content"][0]
        first_id = json.loads(tool_result["content"])["resultados"][0]["chunk_id"]
        text = answer_text.format(id=first_id)
        return _message([{"type": "text", "text": text}], "end_turn")

    first = _message(
        [
            {
                "type": "tool_use",
                "id": "toolu_1",
                "name": "buscar",
                "input": {"consulta": "gozo", "linhas": ["lacan"]},
            }
        ],
        "tool_use",
    )
    return [first, second]


# ------------------------------------------------------------------ integração


@pytest.mark.integration
def test_answer_runs_tools_and_validates_citations(chat_env, monkeypatch):
    fake = _install(monkeypatch, _search_then_answer("O gozo excede o prazer [§{id}]. Invenção [§999999]."))
    events = list(chat.answer(ANA, KEY_ANA, "O que é o gozo?"))

    tipos = [e["tipo"] for e in events]
    assert tipos[0] == "conversa"
    assert "status" in tipos
    fim = events[-1]
    assert fim["tipo"] == "fim"
    assert len(fim["citacoes"]) == 1
    assert fim["citacoes"][0]["n"] == 1
    assert "sessão de" in fim["citacoes"][0]["citacao"]
    assert len(fim["citacoes"][0]["trecho"]) <= chat.EXCERPT_CHARS + 1
    assert fim["invalidas"] == [999999]

    # Requisição: fallback de recusa ligado, cache e esforço explícitos, ferramentas na ordem fixa
    req = fake.calls[0]
    assert req["fallbacks"] == "default"
    assert req["betas"] == [chat.FALLBACK_BETA]
    assert req["output_config"] == {"effort": "medium"}
    assert [t["name"] for t in req["tools"]] == list(chat.TOOLS)

    with db.connect() as conn:
        turn = conn.execute("select * from chat_turn where id = %s", (fim["turno"],)).fetchone()
    assert turn["visitor"] == ANA and turn["client_key"] == KEY_ANA
    assert turn["error"] is None
    assert turn["input_tokens"] == 2000 and turn["output_tokens"] == 400
    assert float(turn["cost_usd"]) == pytest.approx((2000 * 4 + 400 * 20 + 1000 * 0.2) / 1e6, rel=1e-3)
    assert turn["tool_calls"][0]["nome"] == "buscar"


@pytest.mark.integration
def test_follow_up_keeps_history(chat_env, monkeypatch):
    _install(monkeypatch, _search_then_answer("Resposta [§{id}]."))
    first = list(chat.answer(ANA, KEY_ANA, "O que é o gozo?"))
    conversa = first[0]["id"]

    fake = _install(monkeypatch, [_message([{"type": "text", "text": "Continuando."}], "end_turn")])
    second = list(chat.answer(ANA, KEY_ANA, "E o mais-de-gozar?", conversa))
    assert second[0]["id"] == conversa
    sent = fake.calls[0]["messages"]
    # pergunta 1, tool_use, tool_result, resposta 1, pergunta 2
    assert [m["role"] for m in sent] == ["user", "assistant", "user", "assistant", "user"]
    assert sent[1]["content"][0]["type"] == "tool_use"


@pytest.mark.integration
def test_other_visitor_cannot_continue_conversation(chat_env, monkeypatch):
    _install(monkeypatch, [_message([{"type": "text", "text": "Oi."}], "end_turn")])
    conversa = list(chat.answer(ANA, KEY_ANA, "Olá"))[0]["id"]
    fake = _install(monkeypatch, [_message([{"type": "text", "text": "Oi."}], "end_turn")])
    events = list(chat.answer(BRUNO, KEY_BRUNO, "Olá", conversa))
    assert events[0]["id"] != conversa  # vira conversa nova, sem o histórico da Ana
    assert len(fake.calls[0]["messages"]) == 1


@pytest.mark.integration
def test_daily_limit_by_connection_and_budget(chat_env, monkeypatch):
    assert list(chat.answer(ANA, KEY_ANA, "   ")) == [{"tipo": "erro", "texto": "Escreva uma pergunta."}]

    monkeypatch.setenv("CHAT_DAILY_LIMIT", "1")
    config.settings.cache_clear()
    _install(monkeypatch, [_message([{"type": "text", "text": "Oi."}], "end_turn")])
    list(chat.answer(BRUNO, "ip-limite", "Primeira"))
    # o limite é da conexão (IP), não do id do navegador: trocar de navegador não o zera
    events = list(chat.answer("33333333-3333-4333-8333-333333333333", "ip-limite", "Segunda"))
    assert events[0]["tipo"] == "erro"
    assert "30" not in events[0]["texto"] and "limite de 1 perguntas" in events[0]["texto"]

    monkeypatch.setenv("CHAT_DAILY_BUDGET_USD", "0")
    config.settings.cache_clear()
    events = list(chat.answer(ANA, "ip-novo", "Terceira"))
    assert "limite diário de uso" in events[0]["texto"]


@pytest.mark.integration
def test_last_step_forbids_tools(chat_env, monkeypatch):
    """Se o modelo insistir em ferramentas, a última chamada sai com tool_choice none."""
    loop = _message(
        [{"type": "tool_use", "id": "toolu_x", "name": "listar_conceitos", "input": {}}], "tool_use"
    )
    script = [loop] * (chat.MAX_STEPS - 1) + [_message([{"type": "text", "text": "Final."}], "end_turn")]
    fake = _install(monkeypatch, script)
    events = list(chat.answer(ANA, KEY_ANA, "Pergunta que gera laço"))
    assert events[-1]["tipo"] == "fim"
    assert fake.calls[-1]["tool_choice"] == {"type": "none"}
    assert all(c["tool_choice"] == {"type": "auto"} for c in fake.calls[:-1])


@pytest.mark.integration
def test_public_mode_exposes_only_chat(chat_env, monkeypatch):
    monkeypatch.setenv("PUBLIC_MODE", "true")
    config.settings.cache_clear()
    from psilib.api import create_app

    with TestClient(create_app()) as client:
        assert client.get("/busca", params={"q": "gozo"}).status_code == 404
        assert client.get("/trechos/1").status_code == 404
        assert client.get("/docs").status_code == 404
        assert client.get("/openapi.json").status_code == 404
        assert client.get("/mcp").status_code == 404
        assert client.get("/chat").status_code == 200
        assert client.get("/", follow_redirects=False).headers["location"] == "/chat"
        assert client.post("/chat/api/sessao").status_code in (404, 405)  # sem convite: não há sessão

        _install(monkeypatch, _search_then_answer("Resposta [§{id}]."))
        r = client.post(
            "/chat/api/mensagem",
            json={"pergunta": "O que é o gozo?"},
            headers={"X-Visitante": ANA, "X-Forwarded-For": "200.1.2.3, 10.0.0.1"},
        )
        events = [json.loads(line[6:]) for line in r.text.split("\n\n") if line.startswith("data: ")]
        assert events[-1]["tipo"] == "fim"
        turno = events[-1]["turno"]
        fb = {"turno": turno, "valor": -1, "comentario": "faltou o Seminário 7"}
        assert client.post("/chat/api/feedback", json=fb, headers={"X-Visitante": ANA}).status_code == 200

    with db.connect() as conn:
        row = conn.execute(
            "select feedback, comment, client_key from chat_turn where id = %s", (turno,)
        ).fetchone()
    assert row["feedback"] == -1 and row["comment"] == "faltou o Seminário 7"
    assert row["client_key"] == chat.client_key("200.1.2.3")  # IP do cliente, não do proxy


@pytest.mark.integration
def test_missing_api_key_gives_clear_message(chat_env, monkeypatch):
    monkeypatch.delenv("ANTHROPIC_API_KEY")
    events = list(chat.answer(ANA, KEY_ANA, "Olá"))
    assert events == [
        {
            "tipo": "erro",
            "texto": "O chat ainda não está configurado: falta a chave da Claude API no servidor.",
        }
    ]
