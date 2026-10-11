"""Chat da POC: o Claude responde perguntas consultando a biblioteca.

As ferramentas chamam as mesmas funções de `service` que a API REST e o MCP usam. O modelo cita
trechos por marcadores [§chunk_id]; quem monta a referência (autor, obra, sessão, página) é o banco,
e só valem marcadores de trechos que as ferramentas realmente devolveram na conversa — citação
inventada não chega ao leitor.

Cada pergunta é um laço: chamada ao Claude em streaming → ferramentas pedidas → resultados de volta
→ ... até a resposta final. O gerador `answer` emite eventos para a interface (status, texto, fim).
"""

import json
import logging
import re
import time
import uuid
from collections.abc import Iterator
from typing import Literal

import anthropic
from pydantic import BaseModel, Field, ValidationError

from psilib import service
from psilib.config import settings
from psilib.db import connect
from psilib.service import Filters

log = logging.getLogger(__name__)

MAX_STEPS = 8  # chamadas ao modelo por pergunta; a última é forçada a responder sem ferramentas
EXCERPT_CHARS = 300  # trecho mostrado ao leitor junto da citação (obras protegidas: só o essencial)
CITATION_RE = re.compile(r"\[§\s*(\d+)\]")
FALLBACK_BETA = "server-side-fallback-2026-07-01"

SYSTEM = """\
Você é o assistente de pesquisa de uma biblioteca de psicanálise freudiana e lacaniana, em fase de \
teste com psicanalistas e pesquisadores. Responda na língua da pergunta (normalmente português).

Acervo: seminários de Lacan (em francês, do Staferla, datados sessão a sessão, e traduções), \
Escritos e outros textos de Lacan, Freud (traduções brasileiras e Standard Edition), comentadores \
lacanianos e freudianos, escola argentina (Eidelsztein, Schejtman...) e dicionários. Ficam fora \
Klein, Winnicott, Bion, Ferenczi e a psicologia do ego.

Como trabalhar:
- Fundamente as afirmações sobre os autores em trechos do acervo, obtidos com as ferramentas. \
Busque primeiro nas fontes primárias (linhas "lacan" e "freud"), depois nos comentadores, e deixe \
claro quem diz o quê.
- Busque em português: a busca encontra trechos equivalentes em francês e espanhol. Faça mais de \
uma busca quando a pergunta tiver várias partes ou quando os primeiros resultados forem fracos.
- Use ler_trecho para ler o contexto antes de apoiar um argumento num trecho curto ou ambíguo.
- Para evolução e relações entre conceitos, use linha_do_tempo e conceitos_relacionados. \
Frequência indica onde ler, não o que o conceito significa.
- Se o acervo não tiver material suficiente, diga isso com clareza. Se recorrer a conhecimento \
geral, sinalize que não vem do acervo.

Citações:
- Depois de cada afirmação apoiada num trecho, coloque o marcador [§chunk_id] com o chunk_id que \
a ferramenta devolveu (ex.: [§48213]); um marcador por colchete. Nunca invente um chunk_id nem cite \
um trecho que você não recebeu de uma ferramenta.
- O sistema transforma os marcadores em notas com autor, obra, sessão e página: não escreva a \
referência bibliográfica completa no texto.
- Citações literais: curtas (até duas frases) e entre aspas. Trechos em francês ou espanhol: \
traduza e mantenha o termo original entre parênteses. Não reproduza passagens longas: o acervo \
inclui obras protegidas por direitos autorais.

Forma: direta e rigorosa, para um leitor com formação em psicanálise. Parágrafos corridos; \
subtítulos só em respostas longas.
"""

Linha = Literal[
    "freud", "lacan", "lacaniano", "escola_argentina", "comentador_freudiano", "fonte_clinica", "referencia"
]
Lingua = Literal["pt", "fr", "es", "en", "de"]


class ChatError(Exception):
    """Erro mostrado ao convidado (limite atingido, convite inválido...)."""


# ------------------------------------------------------------------ ferramentas


class Buscar(BaseModel):
    """Busca trechos do acervo (texto + semântica entre línguas + conceitos). Devolve chunk_id,
    citação e texto de cada trecho."""

    consulta: str = Field(
        description="O que procurar, em português (acha também trechos em francês/espanhol)"
    )
    linhas: list[Linha] | None = Field(None, description="Restringe às linhas teóricas, ex. ['lacan']")
    autor: str | None = Field(None, description="Parte do nome do autor, ex. 'Eidelsztein'")
    obra: str | None = Field(None, description="Slug ou parte do título, ex. 'seminario-11', 'Encore'")
    lingua: Lingua | None = None
    ano_de: int | None = Field(None, description="Data em que o texto foi proferido/escrito (não da edição)")
    ano_ate: int | None = None
    k: int = Field(8, ge=1, le=10, description="Quantos trechos devolver")


class LerTrecho(BaseModel):
    """Lê um trecho com os vizinhos da mesma obra, para entender o contexto antes de citar."""

    chunk_id: int
    contexto: int = Field(1, ge=0, le=2, description="Quantos trechos antes e depois")


class LinhaDoTempo(BaseModel):
    """Evolução de um conceito: ocorrências por 10 mil palavras por sessão, ano ou obra.
    'sessao' só existe para os seminários do Staferla."""

    conceito: str = Field(description="Slug ou nome do conceito, ex. 'gozo', 'objeto-a'")
    granularidade: Literal["sessao", "ano", "obra"] = "obra"
    linhas: list[Linha] | None = None
    autor: str | None = None


class ConceitosRelacionados(BaseModel):
    """Conceitos que aparecem nos mesmos trechos, ordenados por PMI (associação acima do acaso)."""

    conceito: str
    limite: int = Field(15, ge=1, le=25)


class ListarConceitos(BaseModel):
    """Conceitos do léxico (slug, rótulo, família) com o total de menções no acervo."""


class ListarObras(BaseModel):
    """Obras indexadas, com período, língua e tamanho (até 80)."""

    linhas: list[Linha] | None = None
    autor: str | None = None
    lingua: Lingua | None = None


def _slim(hit: dict) -> dict:
    return {
        "chunk_id": hit["chunk_id"],
        "citacao": hit["citacao"],
        "linha": hit["linha"],
        "texto": hit["texto"],
    }


def _remember(seen: dict, hits: list[dict]) -> None:
    for h in hits:
        seen[str(h["chunk_id"])] = {
            "citacao": h["citacao"],
            "autor": h["autor"],
            "obra": h["obra"],
            "trecho": h["texto"][:EXCERPT_CHARS] + ("…" if len(h["texto"]) > EXCERPT_CHARS else ""),
        }


def _buscar(a: Buscar, seen: dict) -> dict:
    f = Filters(
        lines=a.linhas, author=a.autor, work=a.obra, language=a.lingua, year_from=a.ano_de, year_to=a.ano_ate
    )
    res = service.search(a.consulta, a.k, f)
    _remember(seen, res["resultados"])
    return {
        "conceitos_reconhecidos": res["conceitos_reconhecidos"],
        "resultados": [_slim(h) for h in res["resultados"]],
    }


def _ler_trecho(a: LerTrecho, seen: dict) -> dict:
    res = service.read_chunk(a.chunk_id, a.contexto)
    if "erro" in res:
        return res
    _remember(seen, res["trechos"])
    return {"trechos": [_slim(h) for h in res["trechos"]]}


def _linha_do_tempo(a: LinhaDoTempo, _seen: dict) -> dict:
    res = service.timeline(a.conceito, a.granularidade, Filters(lines=a.linhas, author=a.autor))
    if "erro" in res:
        return res
    keep = ("periodo", "obra", "autor", "seminarios", "por_10k", "mencoes")
    res["serie"] = [{k: r[k] for k in keep if r.get(k)} for r in res["serie"]]
    return res


def _relacionados(a: ConceitosRelacionados, _seen: dict) -> dict:
    return service.cooccurrence(a.conceito, a.limite)


def _listar_conceitos(_a: ListarConceitos, _seen: dict) -> list[dict]:
    return service.list_concepts()


def _listar_obras(a: ListarObras, _seen: dict) -> list[dict]:
    return service.list_works(Filters(lines=a.linhas, author=a.autor, language=a.lingua))[:80]


TOOLS = {
    "buscar": (Buscar, _buscar),
    "ler_trecho": (LerTrecho, _ler_trecho),
    "linha_do_tempo": (LinhaDoTempo, _linha_do_tempo),
    "conceitos_relacionados": (ConceitosRelacionados, _relacionados),
    "listar_conceitos": (ListarConceitos, _listar_conceitos),
    "listar_obras": (ListarObras, _listar_obras),
}


def tool_definitions() -> list[dict]:
    """Definições na ordem fixa de TOOLS (ordem estável = prefixo estável para o prompt caching)."""
    out = []
    for name, (model, _) in TOOLS.items():
        schema = model.model_json_schema()
        schema.pop("title", None)
        schema.pop("description", None)
        out.append({"name": name, "description": " ".join(model.__doc__.split()), "input_schema": schema})
    return out


def run_tool(name: str, raw_input: object, seen: dict) -> tuple[str, bool]:
    """Executa a ferramenta; devolve (conteúdo JSON, is_error). Entradas inválidas viram erro para o modelo."""
    if name not in TOOLS:
        return json.dumps({"erro": f"ferramenta desconhecida: {name}"}), True
    model, fn = TOOLS[name]
    try:
        args = model.model_validate(raw_input)
    except ValidationError as e:
        return json.dumps(
            {"erro": "argumentos inválidos", "detalhes": e.errors(include_url=False)}, default=str
        ), True
    try:
        return json.dumps(fn(args, seen), ensure_ascii=False, default=str), False
    except Exception:
        log.exception("ferramenta %s falhou", name)
        return json.dumps({"erro": "falha interna ao consultar a biblioteca"}), True


def describe(name: str, raw_input: dict) -> str:
    """Mensagem de status para a interface enquanto a ferramenta roda."""
    if name == "buscar":
        return f"Buscando no acervo: {raw_input.get('consulta', '')}"
    if name == "ler_trecho":
        return "Lendo o trecho no contexto"
    if name == "linha_do_tempo":
        return f"Montando a linha do tempo: {raw_input.get('conceito', '')}"
    if name == "conceitos_relacionados":
        return f"Mapeando conceitos relacionados a {raw_input.get('conceito', '')}"
    return "Consultando o catálogo"


# ------------------------------------------------------------------ citações


def resolve_citations(text: str, seen: dict) -> tuple[list[dict], list[int]]:
    """Marcadores [§id] na ordem em que aparecem → notas numeradas; ids nunca vistos são inválidos."""
    notes, invalid, order = [], [], []
    for m in CITATION_RE.finditer(text):
        cid = int(m.group(1))
        if cid in order or cid in invalid:
            continue
        if str(cid) in seen:
            order.append(cid)
            notes.append({"n": len(order), "chunk_id": cid, **seen[str(cid)]})
        else:
            invalid.append(cid)
    return notes, invalid


# ------------------------------------------------------------------ convites, limites, custo


def invites() -> dict[str, str]:
    """código → nome do convidado, a partir de CHAT_INVITES ("Nome:código,Outro:código2")."""
    out = {}
    for item in settings().chat_invites.split(","):
        name, _, code = item.strip().rpartition(":")
        if name and code:
            out[code.strip()] = name.strip()
    return out


def invite_name(code: str | None) -> str:
    name = invites().get((code or "").strip())
    if not name:
        raise ChatError("Código de convite inválido.")
    return name


def check_limits(conn, invite: str, conversation_id: uuid.UUID | None) -> None:
    s = settings()
    row = conn.execute(
        """select count(*) filter (where invite = %(i)s) as minhas, coalesce(sum(cost_usd), 0) as custo
           from chat_turn where created_at >= current_date""",
        {"i": invite},
    ).fetchone()
    if row["minhas"] >= s.chat_daily_limit:
        raise ChatError(f"Você atingiu o limite de {s.chat_daily_limit} perguntas por dia. Volte amanhã!")
    if float(row["custo"]) >= s.chat_daily_budget_usd:
        raise ChatError("O limite diário de uso da POC foi atingido. Tente novamente amanhã.")
    if conversation_id:
        n = conn.execute(
            "select count(*) as n from chat_turn where conversation_id = %s and error is null",
            (conversation_id,),
        ).fetchone()["n"]
        if n >= s.chat_max_turns:
            raise ChatError("Esta conversa ficou longa. Comece uma nova conversa para continuar.")


def cost_usd(usage: dict) -> float:
    s = settings()
    return (
        usage["input_tokens"] * s.chat_price_input
        + usage["output_tokens"] * s.chat_price_output
        + usage["cache_read_tokens"] * s.chat_price_cache_read
        + usage["cache_write_tokens"] * s.chat_price_cache_write
    ) / 1_000_000


# ------------------------------------------------------------------ o turno


def _load_conversation(conn, conversation_id: str | None, invite: str) -> tuple[uuid.UUID | None, list, dict]:
    if not conversation_id:
        return None, [], {}
    try:
        cid = uuid.UUID(conversation_id)
    except ValueError:
        return None, [], {}
    row = conn.execute(
        "select invite, messages, seen from chat_conversation where id = %s", (cid,)
    ).fetchone()
    if not row or row["invite"] != invite:
        return None, [], {}
    return cid, row["messages"], row["seen"]


def _client() -> anthropic.Anthropic:
    return anthropic.Anthropic()


def answer(invite_code: str | None, question: str, conversation_id: str | None = None) -> Iterator[dict]:
    """Responde uma pergunta. Emite eventos: conversa, status, texto, fim | erro."""
    s = settings()
    t0 = time.monotonic()
    question = question.strip()
    try:
        invite = invite_name(invite_code)
        if not question:
            raise ChatError("Escreva uma pergunta.")
        if len(question) > 4000:
            raise ChatError("A pergunta é longa demais (máximo de 4.000 caracteres).")
        with connect() as conn:
            cid, messages, seen = _load_conversation(conn, conversation_id, invite)
            check_limits(conn, invite, cid)
    except ChatError as e:
        yield {"tipo": "erro", "texto": str(e)}
        return

    new_conversation = cid is None
    cid = cid or uuid.uuid4()
    yield {"tipo": "conversa", "id": str(cid)}

    messages = [*messages, {"role": "user", "content": question}]
    usage = {"input_tokens": 0, "output_tokens": 0, "cache_read_tokens": 0, "cache_write_tokens": 0}
    tool_calls: list[dict] = []
    texts: list[str] = []
    model_used, stop_reason, error = s.chat_model, None, None
    tools = tool_definitions()

    try:
        client = _client()
        for step in range(MAX_STEPS):
            last = step == MAX_STEPS - 1
            with client.beta.messages.stream(
                model=s.chat_model,
                max_tokens=32000,
                system=SYSTEM,
                tools=tools,
                tool_choice={"type": "none"} if last else {"type": "auto"},
                messages=messages,
                output_config={"effort": s.chat_effort},
                cache_control={"type": "ephemeral"},
                betas=[FALLBACK_BETA],
                fallbacks="default",
            ) as stream:
                for event in stream:
                    if event.type == "text":
                        yield {"tipo": "texto", "texto": event.text}
                    elif event.type == "content_block_start" and event.content_block.type == "thinking":
                        yield {"tipo": "status", "texto": "Pensando…"}
                msg = stream.get_final_message()

            u = msg.usage
            usage["input_tokens"] += u.input_tokens or 0
            usage["output_tokens"] += u.output_tokens or 0
            usage["cache_read_tokens"] += u.cache_read_input_tokens or 0
            usage["cache_write_tokens"] += u.cache_creation_input_tokens or 0
            model_used, stop_reason = msg.model, msg.stop_reason
            messages.append(
                {
                    "role": "assistant",
                    "content": [b.model_dump(mode="json", exclude_none=True) for b in msg.content],
                }
            )
            texts += [b.text for b in msg.content if b.type == "text"]

            if msg.stop_reason == "refusal":
                error = "refusal"
                yield {
                    "tipo": "erro",
                    "texto": "O modelo não pôde responder a esta pergunta. Tente reformulá-la.",
                }
                break
            tool_uses = [b for b in msg.content if b.type == "tool_use"]
            if not tool_uses:
                break
            if msg.stop_reason == "max_tokens":
                error = "max_tokens com ferramenta pendente"
                yield {
                    "tipo": "erro",
                    "texto": "A resposta ficou longa demais. Tente uma pergunta mais específica.",
                }
                break

            results = []
            for b in tool_uses:
                yield {
                    "tipo": "status",
                    "texto": describe(b.name, b.input if isinstance(b.input, dict) else {}),
                }
                content, is_error = run_tool(b.name, b.input, seen)
                tool_calls.append({"nome": b.name, "args": b.input, "erro": is_error})
                results.append(
                    {"type": "tool_result", "tool_use_id": b.id, "content": content, "is_error": is_error}
                )
            messages.append({"role": "user", "content": results})
    except anthropic.APIError as e:
        log.exception("erro na Claude API")
        error = f"{type(e).__name__}: {e}"
        yield {
            "tipo": "erro",
            "texto": "O serviço de IA está indisponível no momento. Tente de novo em instantes.",
        }
    except Exception as e:
        log.exception("erro inesperado no chat")
        error = f"{type(e).__name__}: {e}"
        yield {"tipo": "erro", "texto": "Ocorreu um erro inesperado. Tente de novo."}

    final_text = "\n\n".join(t for t in texts if t.strip())
    notes, invalid = resolve_citations(final_text, seen)
    if invalid:
        log.warning("citações sem trecho correspondente: %s", invalid)
    cost = cost_usd(usage)
    latency = int((time.monotonic() - t0) * 1000)

    with connect() as conn:
        # O histórico só é gravado quando o turno termina inteiro: um turno quebrado deixaria
        # tool_use sem tool_result e invalidaria a conversa.
        if error is None:
            conn.execute(
                """insert into chat_conversation (id, invite, messages, seen) values (%s, %s, %s, %s)
                   on conflict (id) do update set messages = excluded.messages, seen = excluded.seen,
                       updated_at = now()""",
                (cid, invite, json.dumps(messages), json.dumps(seen)),
            )
        elif new_conversation:
            conn.execute(
                "insert into chat_conversation (id, invite) values (%s, %s) on conflict do nothing",
                (cid, invite),
            )
        turn_id = conn.execute(
            """insert into chat_turn (conversation_id, invite, question, answer, citations, invalid_citations,
                   tool_calls, model, input_tokens, output_tokens, cache_read_tokens, cache_write_tokens,
                   cost_usd, latency_ms, stop_reason, error)
               values (%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s) returning id""",
            (
                cid, invite, question, final_text, json.dumps(notes), json.dumps(invalid),
                json.dumps(tool_calls, default=str), model_used, usage["input_tokens"], usage["output_tokens"],
                usage["cache_read_tokens"], usage["cache_write_tokens"], cost, latency, stop_reason, error,
            ),
        ).fetchone()["id"]  # fmt: skip
        conn.commit()

    log.info("chat: %s | %d ferramentas | US$ %.4f | %d ms", invite, len(tool_calls), cost, latency)
    if error is None:
        yield {"tipo": "fim", "turno": turn_id, "citacoes": notes, "invalidas": invalid}


def feedback(invite_code: str | None, turn_id: int, value: int, comment: str | None) -> None:
    invite = invite_name(invite_code)
    with connect() as conn:
        conn.execute(
            "update chat_turn set feedback = %s, comment = %s where id = %s and invite = %s",
            (value, (comment or "").strip()[:2000] or None, turn_id, invite),
        )
        conn.commit()
