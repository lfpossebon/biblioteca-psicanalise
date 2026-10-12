"""Análise em Python: execução de código da Claude API com chamada programática das ferramentas de dados.

Cliente falso com mensagens reais do SDK (BetaMessage), nos formatos observados numa sondagem real da
API: célula `code_execution` + `tool_use` com caller de execução de código, depois o resultado do bash
com os `file_id` dos arquivos copiados para $OUTPUT_DIR.
"""

import base64
import json
from types import SimpleNamespace

import anthropic
import httpx2
import pytest
from test_chat import ANA, KEY_ANA, FakeClient, _install, _message

from psilib import chat, config, db

pytestmark = pytest.mark.integration

PNG = b"\x89PNG\r\n\x1a\n" + b"0" * 64
CSV = b"decada,por_10k\n1960,4.2\n"
CODE = "import json\ndf = json.loads(await dados_frequencias({'conceitos': ['gozo'], 'linhas': ['lacan']}))"


def _msg(content: list[dict], stop: str, container: str | None = "container_1") -> object:
    m = _message(content, stop)
    if container:
        m = m.model_copy(update={"container": {"id": container, "expires_at": "2026-10-12T20:00:00Z"}})
        m = type(m).model_validate(m.model_dump())
    return m


def _cell(code=CODE, sid="srvtoolu_1"):
    return {"type": "server_tool_use", "id": sid, "name": "code_execution", "input": {"code": code}}


def _code_call(name="dados_frequencias", args=None, tid="toolu_c1", sid="srvtoolu_1"):
    return {"type": "tool_use", "id": tid, "name": name, "input": args or {"conceitos": ["gozo"], "linhas": ["lacan"]},
            "caller": {"type": "code_execution_20260120", "tool_id": sid}}  # fmt: skip


def _cell_result(stdout="6 linhas", sid="srvtoolu_1"):
    return {"type": "code_execution_tool_result", "tool_use_id": sid,
            "content": {"type": "code_execution_result", "stdout": stdout, "stderr": "", "return_code": 0, "content": []}}  # fmt: skip


def _bash(files, sid="srvtoolu_2"):
    return [
        {"type": "server_tool_use", "id": sid, "name": "bash_code_execution",
         "input": {"command": 'cp /tmp/saida/* "$OUTPUT_DIR"/ && ls "$OUTPUT_DIR"'}},
        {"type": "bash_code_execution_tool_result", "tool_use_id": sid,
         "content": {"type": "bash_code_execution_result", "stdout": "\n".join(files), "stderr": "", "return_code": 0,
                     "content": [{"type": "bash_code_execution_output", "file_id": f"file_{n}"} for n in files]}},
    ]  # fmt: skip


class FilesFake:
    META = {
        "file_gozo.png": ("gozo.png", "image/png", PNG),
        "file_gozo.csv": ("gozo.csv", "text/plain", CSV),  # CSV às vezes vem como text/plain: vale a extensão
        "file_pagina.html": ("pagina.html", "text/html", b"<script>alert(1)</script>"),
    }

    def retrieve_metadata(self, fid):
        name, mime, data = self.META[fid]
        return SimpleNamespace(filename=name, mime_type=mime, size_bytes=len(data))

    def download(self, fid):
        return SimpleNamespace(read=lambda: self.META[fid][2])


def _install_with_files(monkeypatch, script) -> FakeClient:
    fake = _install(monkeypatch, script)
    fake.files = FilesFake()
    return fake


def test_definitions_expose_code_execution_and_data_tools():
    defs = {d.get("name"): d for d in chat.tool_definitions(python=True)}
    assert defs["code_execution"] == chat.CODE_TOOL
    for name in chat.DATA_TOOLS:
        assert defs[name]["allowed_callers"] == ["code_execution_20260120"]
    assert "allowed_callers" not in defs["buscar"]  # ferramentas de resposta seguem diretas
    assert [d["name"] for d in chat.tool_definitions(python=False)] == list(chat.TOOLS)


def test_python_can_be_switched_off(monkeypatch):
    monkeypatch.setenv("CHAT_PYTHON_ENABLED", "false")
    config.settings.cache_clear()
    try:
        assert "code_execution" not in [d["name"] for d in chat.tool_definitions()]
        assert "Análise em Python" not in chat.system_prompt()
    finally:
        monkeypatch.undo()
        config.settings.cache_clear()


def test_data_tools_return_tables_and_short_citable_excerpts(chat_env):
    ctx = chat.TurnContext()
    content, err = chat.run_tool(
        "dados_frequencias", {"conceitos": ["gozo", "desejo"], "linhas": ["lacan"]}, ctx
    )
    table = json.loads(content)
    assert not err and table["conceitos"] == ["gozo", "desejo"]
    row = table["linhas"][0]
    assert {
        "conceito",
        "obra",
        "linha",
        "periodo",
        "precisao",
        "palavras",
        "mencoes",
        "por_10k",
    } <= row.keys()
    assert isinstance(row["por_10k"], float) and isinstance(row["palavras"], int)
    assert len(table["linhas"]) == 12  # 2 conceitos × 6 sessões

    content, err = chat.run_tool("dados_trechos", {"consulta": "gozo", "k": 3}, ctx)
    hits = json.loads(content)
    assert not err and hits and all(len(h["texto"]) <= chat.EXCERPT_CHARS + 1 for h in hits)
    assert str(hits[0]["chunk_id"]) in ctx.seen  # pode ser citado como [§id]

    content, err = chat.run_tool("dados_frequencias", {"conceitos": ["inexistente"]}, ctx)
    assert not err and "não está no léxico" in json.loads(content)["erro"]


def test_code_calls_tool_then_returns_chart(chat_env, monkeypatch):
    def resume(kwargs):
        assert kwargs["container"] == "container_1"  # obrigatório com chamada programática pendente
        result = kwargs["messages"][-1]
        assert [b["type"] for b in result["content"]] == ["tool_result"]  # só tool_result
        rows = json.loads(result["content"][0]["content"])["linhas"]
        assert len(rows) == 6
        return _msg([_cell_result(), *_bash(["gozo.png", "gozo.csv", "pagina.html"]),
                     {"type": "text", "text": "O gráfico mostra o gozo por década."}], "end_turn")  # fmt: skip

    _install_with_files(monkeypatch, [_msg([_cell(), _code_call()], "tool_use"), resume])
    events = list(chat.answer(ANA, KEY_ANA, "Em Python, o gozo por década"))

    (py,) = [e for e in events if e["tipo"] == "python"]
    assert [a["nome"] for a in py["arquivos"]] == ["gozo.png", "gozo.csv"]  # .html descartado
    assert py["arquivos"][1]["mime"] == "text/csv"
    assert base64.b64decode(py["arquivos"][0]["base64"]) == PNG
    assert [c["linguagem"] for c in py["celulas"]] == ["python", "bash"]
    assert py["celulas"][0]["codigo"] == CODE and "6 linhas" in py["saida"]
    assert "Buscando dados para a análise em Python" in [e.get("texto") for e in events]
    assert events[-1]["tipo"] == "fim"

    with db.connect() as conn:
        turn = conn.execute("select python, tool_calls, conversation_id from chat_turn where id = %s",
                            (events[-1]["turno"],)).fetchone()  # fmt: skip
        conv = conn.execute(
            "select container_id from chat_conversation where id = %s", (turn["conversation_id"],)
        ).fetchone()
    assert len(turn["python"][0]["arquivos"]) == 2
    assert turn["tool_calls"][0]["codigo"] is True
    assert conv["container_id"] == "container_1"


def test_pause_turn_resumes_and_shows_code_without_files(chat_env, monkeypatch):
    def resume(kwargs):
        assert kwargs["messages"][-1]["role"] == "assistant"  # retomada sem mensagem extra
        return _msg([_cell_result("r = 0,82"), {"type": "text", "text": "Correlação de 0,82."}], "end_turn")

    _install_with_files(monkeypatch, [_msg([_cell("print(0.82)")], "pause_turn"), resume])
    events = list(chat.answer(ANA, KEY_ANA, "Calcule a correlação"))
    (py,) = [e for e in events if e["tipo"] == "python"]
    assert py["arquivos"] == [] and py["celulas"][0]["codigo"] == "print(0.82)"
    assert "r = 0,82" in py["saida"]


def test_follow_up_reuses_container_and_recovers_when_expired(chat_env, monkeypatch):
    _install_with_files(monkeypatch, [_msg([{"type": "text", "text": "Ok."}], "end_turn", "container_9")])
    conversa = list(chat.answer(ANA, KEY_ANA, "Primeira"))[0]["id"]

    fake = _install_with_files(
        monkeypatch, [_msg([{"type": "text", "text": "De novo."}], "end_turn", "container_9")]
    )
    list(chat.answer(ANA, KEY_ANA, "Segunda", conversa))
    assert fake.calls[0]["container"] == "container_9"  # estado do Python mantido na conversa

    expired = anthropic.BadRequestError(
        "container_9 expired",
        response=httpx2.Response(
            400, request=httpx2.Request("POST", "https://api.anthropic.com/v1/messages")
        ),
        body=None,
    )

    def fail_if_container(kwargs):
        if "container" in kwargs:
            raise expired
        return _msg([{"type": "text", "text": "Novo container."}], "end_turn", "container_10")

    fake = FakeClient([])
    fake.files = FilesFake()
    attempts = []

    def stream(**kwargs):
        attempts.append("container" in kwargs)
        return __import__("test_chat").FakeStream(fail_if_container(kwargs))

    fake.beta = SimpleNamespace(messages=SimpleNamespace(stream=stream))
    monkeypatch.setattr(chat, "_client", lambda: fake)
    events = list(chat.answer(ANA, KEY_ANA, "Terceira", conversa))
    assert attempts == [True, False] and events[-1]["tipo"] == "fim"
    with db.connect() as conn:
        row = conn.execute("select container_id from chat_conversation where id = %s", (conversa,)).fetchone()
    assert row["container_id"] == "container_10"
