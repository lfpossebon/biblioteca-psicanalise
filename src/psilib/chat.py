"""Chat da POC: o Claude responde perguntas consultando a biblioteca.

As ferramentas chamam as mesmas funções de `service` que a API REST e o MCP usam. O modelo cita
trechos por marcadores [§chunk_id]; quem monta a referência (autor, obra, sessão, página) é o banco,
e só valem marcadores de trechos que as ferramentas realmente devolveram na conversa — citação
inventada não chega ao leitor.

Cada pergunta é um laço: chamada ao Claude em streaming → ferramentas pedidas → resultados de volta
→ ... até a resposta final. O gerador `answer` emite eventos para a interface (status, texto, fim).
"""

import base64
import hashlib
import json
import logging
import os
import re
import time
import uuid
from collections.abc import Iterator
from dataclasses import dataclass, field
from typing import Literal

import anthropic
from pydantic import BaseModel, Field, ValidationError

from psilib import service, visuals
from psilib.config import settings
from psilib.db import connect
from psilib.service import Filters

log = logging.getLogger(__name__)

MAX_STEPS = 16  # chamadas ao modelo por pergunta (cada pausa do código conta); a última força a resposta
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
- Figuras: em perguntas sobre evolução no tempo, comparação entre conceitos, onde um conceito pesa \
mais ou com que conceitos se associa, mostre uma figura com mostrar_visual (no máximo 3 por resposta) \
e comente-a no texto como "Figura N". Os números da figura vêm do acervo; não os repita todos no texto.
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

PYTHON_GUIDE = """
Análise em Python (ferramenta code_execution):
- Use quando a pessoa pedir uma análise ou um gráfico sob medida que as figuras prontas não cobrem \
(agrupar por década, comparar linhas teóricas ou autores, heatmap de conceitos por seminário, \
correlações, rankings), ou quando pedir Python, código, tabela ou estatística. Para as figuras padrão \
(linha do tempo, peso por obra, relacionados), prefira mostrar_visual: é mais rápido.
- No código, os dados vêm só das funções dados_frequencias, dados_coocorrencia, dados_conceitos, \
dados_obras e dados_trechos (assíncronas, recebem um dict, devolvem JSON em string). Exemplo: \
`df = pd.DataFrame(json.loads(await dados_frequencias({"conceitos": ["gozo"], "linhas": ["lacan"]}))["linhas"])`. \
Não há internet nem outros dados.
- Frequência agregada = soma de mencoes ÷ soma de palavras × 10 000 (nunca a média de por_10k).
- Antes de desenhar, aplique este estilo:
  import matplotlib.pyplot as plt; from cycler import cycler
  plt.rcParams.update({"figure.figsize": (9, 4.5), "figure.dpi": 150, "figure.facecolor": "#fcfcfb", \
"axes.facecolor": "#fcfcfb", "axes.edgecolor": "#c3c2b7", "axes.spines.top": False, \
"axes.spines.right": False, "axes.grid": True, "grid.color": "#e1e0d9", "grid.linewidth": 0.8, \
"axes.axisbelow": True, "axes.prop_cycle": cycler(color=["#2a78d6", "#eb6834", "#1baf7a", "#eda100", \
"#e87ba4", "#008300", "#6250d6", "#e34948"]), "lines.linewidth": 2, "font.size": 10, \
"axes.titlesize": 12, "axes.titleweight": "semibold", "axes.labelcolor": "#52514e", \
"xtick.color": "#898781", "ytick.color": "#898781", "legend.frameon": False})
  Título e eixos em português; um eixo y só (nunca dois); legenda a partir de 2 séries; heatmaps com \
o mapa "Blues"; no máximo 3 gráficos por resposta.
- Salve cada gráfico em /tmp/saida/NOME.png (dpi=150, bbox_inches="tight") e os dados plotados em \
/tmp/saida/NOME.csv. No fim, num comando bash: `cp /tmp/saida/* "$OUTPUT_DIR"/ && ls "$OUTPUT_DIR"`. \
Só assim os arquivos chegam ao leitor.
- No texto, comente o que o resultado mostra; se usou dados_trechos, cite os trechos como \
[§chunk_id]. Não cole o código na resposta: a página já o mostra.
"""


def system_prompt() -> str:
    return SYSTEM + (PYTHON_GUIDE if settings().chat_python_enabled else "")


Linha = Literal[
    "freud", "lacan", "lacaniano", "escola_argentina", "comentador_freudiano", "fonte_clinica", "referencia"
]
Lingua = Literal["pt", "fr", "es", "en", "de"]


class ChatError(Exception):
    """Erro mostrado ao visitante (limite atingido, pergunta inválida...)."""


# ------------------------------------------------------------------ ferramentas


@dataclass
class TurnContext:
    """Estado de uma resposta: trechos vistos (citáveis) e figuras exibidas."""

    seen: dict = field(default_factory=dict)
    visuals: list[dict] = field(default_factory=list)
    container: str | None = None  # container de execução de código (estado do Python na conversa)
    python: list[dict] = field(default_factory=list)  # análises em Python exibidas (código + arquivos)


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


class MostrarVisual(BaseModel):
    """Mostra ao leitor uma figura com dados do acervo; o sistema desenha, você só escolhe.
    linha_do_tempo: evolução de 1 a 3 conceitos (comparados) por ano ou por sessão dos seminários.
    peso_por_obra: em que obras ou seminários um conceito pesa mais. relacionados: conceitos que se
    associam a um conceito (PMI). Depois, comente a figura no texto como "Figura N"."""

    tipo: Literal["linha_do_tempo", "peso_por_obra", "relacionados"]
    conceitos: list[str] = Field(
        min_length=1, max_length=3, description="Slugs ou nomes; só linha_do_tempo usa mais de um"
    )
    granularidade: Literal["ano", "sessao"] = Field(
        "ano", description="linha_do_tempo: 'sessao' só para os seminários (use linhas=['lacan'])"
    )
    linhas: list[Linha] | None = None
    autor: str | None = None
    titulo: str | None = Field(None, max_length=90, description="Título curto; opcional")


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
            "linha": h["linha"],
            "periodo": h["sessao"] or h["periodo"],  # data do pensamento: sessão, senão ano da obra
            "precisao": "dia" if h["sessao"] else ("ano" if h["periodo"] else None),
            "trecho": h["texto"][:EXCERPT_CHARS] + ("…" if len(h["texto"]) > EXCERPT_CHARS else ""),
        }


def _buscar(a: Buscar, ctx: TurnContext) -> dict:
    f = Filters(
        lines=a.linhas, author=a.autor, work=a.obra, language=a.lingua, year_from=a.ano_de, year_to=a.ano_ate
    )
    res = service.search(a.consulta, a.k, f)
    _remember(ctx.seen, res["resultados"])
    return {
        "conceitos_reconhecidos": res["conceitos_reconhecidos"],
        "resultados": [_slim(h) for h in res["resultados"]],
    }


def _ler_trecho(a: LerTrecho, ctx: TurnContext) -> dict:
    res = service.read_chunk(a.chunk_id, a.contexto)
    if "erro" in res:
        return res
    _remember(ctx.seen, res["trechos"])
    return {"trechos": [_slim(h) for h in res["trechos"]]}


def _linha_do_tempo(a: LinhaDoTempo, _ctx: TurnContext) -> dict:
    res = service.timeline(a.conceito, a.granularidade, Filters(lines=a.linhas, author=a.autor))
    if "erro" in res:
        return res
    keep = ("periodo", "obra", "autor", "seminarios", "por_10k", "mencoes")
    res["serie"] = [{k: r[k] for k in keep if r.get(k)} for r in res["serie"]]
    return res


def _relacionados(a: ConceitosRelacionados, _ctx: TurnContext) -> dict:
    return service.cooccurrence(a.conceito, a.limite)


def _listar_conceitos(_a: ListarConceitos, _ctx: TurnContext) -> list[dict]:
    return service.list_concepts()


def _listar_obras(a: ListarObras, _ctx: TurnContext) -> list[dict]:
    return service.list_works(Filters(lines=a.linhas, author=a.autor, language=a.lingua))[:80]


def _mostrar_visual(a: MostrarVisual, ctx: TurnContext) -> dict:
    if len(ctx.visuals) >= 3:
        raise visuals.VisualError("limite de 3 figuras por resposta atingido")
    spec = visuals.build(
        a.tipo, a.conceitos, a.granularidade, Filters(lines=a.linhas, author=a.autor), a.titulo
    )
    spec["figura"] = len(ctx.visuals) + 1
    ctx.visuals.append(spec)
    return visuals.summary(spec)


# ------------------------------------------------------------------ dados para o código Python
# Chamadas só de dentro da execução de código (allowed_callers): o resultado vai para o código, não
# para o contexto do modelo. Devolvem JSON em string; o código faz json.loads → pandas.


class DadosFrequencias(BaseModel):
    """Tabela de frequência de conceitos para análise: JSON {"conceitos": [...], "linhas": [...],
    "truncado": bool}; cada linha = conceito × documento × período, com campos conceito, obra, autor,
    linha (freud|lacan|lacaniano|escola_argentina|comentador_freudiano|fonte_clinica|referencia),
    lingua, fonte, seminario (str ou null), periodo (AAAA-MM-DD), precisao (day = sessão datada;
    year = ano), palavras (int), mencoes (int), por_10k (float). Para agregar, some mencoes e
    palavras e só então divida (× 10 000); não tire média de por_10k."""

    conceitos: list[str] = Field(min_length=1, max_length=8, description="Slugs ou nomes de conceitos")
    linhas: list[Linha] | None = None
    autor: str | None = None
    lingua: Lingua | None = None
    ano_de: int | None = None
    ano_ate: int | None = None


class DadosCoocorrencia(BaseModel):
    """Conceitos que coocorrem com um conceito: JSON {"conceito", "relacionados": [{slug, label,
    trechos_juntos (int), pmi (float)}]}, ordenado por PMI."""

    conceito: str
    limite: int = Field(30, ge=1, le=60)


class DadosConceitos(BaseModel):
    """Léxico de conceitos: JSON lista de {slug, label, family, mencoes (int), trechos (int)}."""


class DadosObras(BaseModel):
    """Obras indexadas: JSON lista de {autor, linha, obra, slug, lingua, fonte, periodo (AAAA-MM-DD ou
    null), trechos (int), palavras (int)} (até 500)."""

    linhas: list[Linha] | None = None
    autor: str | None = None
    lingua: Lingua | None = None


class DadosTrechos(BaseModel):
    """Busca trechos (texto + semântica + conceitos): JSON lista de {chunk_id, citacao, autor, obra,
    linha, periodo (ou null), texto (até 300 caracteres)}. Imprima os chunk_id que usar para poder
    citá-los como [§chunk_id] na resposta."""

    consulta: str
    linhas: list[Linha] | None = None
    autor: str | None = None
    obra: str | None = None
    lingua: Lingua | None = None
    k: int = Field(15, ge=1, le=30)


def _dados_frequencias(a: DadosFrequencias, _ctx: TurnContext) -> dict:
    f = Filters(lines=a.linhas, author=a.autor, language=a.lingua, year_from=a.ano_de, year_to=a.ano_ate)
    return service.concept_table(a.conceitos, f)


def _dados_coocorrencia(a: DadosCoocorrencia, _ctx: TurnContext) -> dict:
    return service.cooccurrence(a.conceito, a.limite)


def _dados_conceitos(_a: DadosConceitos, _ctx: TurnContext) -> list[dict]:
    return service.list_concepts()


def _dados_obras(a: DadosObras, _ctx: TurnContext) -> list[dict]:
    return service.list_works(Filters(lines=a.linhas, author=a.autor, language=a.lingua))


def _dados_trechos(a: DadosTrechos, ctx: TurnContext) -> list[dict]:
    f = Filters(lines=a.linhas, author=a.autor, work=a.obra, language=a.lingua)
    hits = service.search(a.consulta, a.k, f, max_chars=EXCERPT_CHARS)["resultados"]
    _remember(ctx.seen, hits)  # trechos servidos ao código também podem ser citados
    return [
        {"chunk_id": h["chunk_id"], "citacao": h["citacao"], "autor": h["autor"], "obra": h["obra"],
         "linha": h["linha"], "periodo": h["sessao"] or h["periodo"], "texto": h["texto"]}
        for h in hits
    ]  # fmt: skip


CODE_CALLER = "code_execution_20260120"  # como as respostas identificam chamadas vindas do código
CODE_TOOL = {"type": "code_execution_20260521", "name": "code_execution"}
DATA_TOOLS = {
    "dados_frequencias": (DadosFrequencias, _dados_frequencias),
    "dados_coocorrencia": (DadosCoocorrencia, _dados_coocorrencia),
    "dados_conceitos": (DadosConceitos, _dados_conceitos),
    "dados_obras": (DadosObras, _dados_obras),
    "dados_trechos": (DadosTrechos, _dados_trechos),
}

TOOLS = {
    "buscar": (Buscar, _buscar),
    "ler_trecho": (LerTrecho, _ler_trecho),
    "linha_do_tempo": (LinhaDoTempo, _linha_do_tempo),
    "conceitos_relacionados": (ConceitosRelacionados, _relacionados),
    "listar_conceitos": (ListarConceitos, _listar_conceitos),
    "listar_obras": (ListarObras, _listar_obras),
    "mostrar_visual": (MostrarVisual, _mostrar_visual),
}


def _definition(name: str, model: type[BaseModel]) -> dict:
    schema = model.model_json_schema()
    schema.pop("title", None)
    schema.pop("description", None)
    return {"name": name, "description": " ".join(model.__doc__.split()), "input_schema": schema}


def tool_definitions(python: bool | None = None) -> list[dict]:
    """Definições em ordem fixa (ordem estável = prefixo estável para o prompt caching).
    Com Python ligado, entram a execução de código e as ferramentas de dados chamáveis só por ela."""
    python = settings().chat_python_enabled if python is None else python
    out = [_definition(name, model) for name, (model, _) in TOOLS.items()]
    if python:
        out += [{**_definition(n, m), "allowed_callers": [CODE_CALLER]} for n, (m, _) in DATA_TOOLS.items()]
        out.append(CODE_TOOL)
    return out


def run_tool(name: str, raw_input: object, ctx: TurnContext) -> tuple[str, bool]:
    """Executa a ferramenta; devolve (conteúdo JSON, is_error). Entradas inválidas viram erro para o modelo."""
    registry = TOOLS | DATA_TOOLS
    if name not in registry:
        return json.dumps({"erro": f"ferramenta desconhecida: {name}"}), True
    model, fn = registry[name]
    try:
        args = model.model_validate(raw_input)
    except ValidationError as e:
        return json.dumps(
            {"erro": "argumentos inválidos", "detalhes": e.errors(include_url=False)}, default=str
        ), True
    try:
        return json.dumps(fn(args, ctx), ensure_ascii=False, default=str), False
    except visuals.VisualError as e:
        return json.dumps({"erro": str(e)}, ensure_ascii=False), True
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
    if name == "mostrar_visual":
        return "Desenhando a figura"
    if name in DATA_TOOLS:
        return "Buscando dados para a análise em Python"
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


# ------------------------------------------------------------------ visitantes, limites, custo


def client_key(ip: str | None) -> str:
    """Hash do IP com sal: basta para o limite diário e não guarda o IP (LGPD)."""
    raw = f"{settings().chat_ip_salt}:{ip or 'desconhecido'}"
    return hashlib.sha256(raw.encode()).hexdigest()[:16]


def visitor_id(raw: str | None) -> str:
    """Id anônimo gerado no navegador (uuid). Valores fora do formato viram 'anonimo'."""
    try:
        return str(uuid.UUID((raw or "").strip()))
    except ValueError:
        return "anonimo"


def check_limits(conn, key: str, conversation_id: uuid.UUID | None) -> None:
    s = settings()
    row = conn.execute(
        """select count(*) filter (where client_key = %(k)s) as minhas, coalesce(sum(cost_usd), 0) as custo
           from chat_turn where created_at >= current_date""",
        {"k": key},
    ).fetchone()
    if float(row["custo"]) >= s.chat_daily_budget_usd:
        raise ChatError(
            "O limite diário de uso desta prova de conceito foi atingido. Tente novamente amanhã."
        )
    if row["minhas"] >= s.chat_daily_limit:
        raise ChatError(f"Você atingiu o limite de {s.chat_daily_limit} perguntas por dia. Volte amanhã!")
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


# ------------------------------------------------------------------ análise em Python: saídas

ALLOWED_FILES = {"image/png": "png", "image/jpeg": "jpg", "text/csv": "csv"}
MAX_FILE_BYTES = 3 * 1024 * 1024
MAX_FILES = 6  # por resposta


def _collect_code(msg, pending: dict) -> list[str]:
    """Acumula as células executadas e suas saídas; devolve os ids dos arquivos gerados."""
    file_ids = []
    for b in msg.content:
        if b.type == "server_tool_use":
            inp = b.input if isinstance(b.input, dict) else {}
            if b.name == "code_execution" and inp.get("code"):
                pending["celulas"].append({"linguagem": "python", "codigo": inp["code"]})
            elif b.name == "bash_code_execution" and inp.get("command"):
                pending["celulas"].append({"linguagem": "bash", "codigo": inp["command"]})
            elif (
                b.name == "text_editor_code_execution"
                and inp.get("command") == "create"
                and str(inp.get("path", "")).endswith(".py")
            ):
                pending["celulas"].append({"linguagem": "python", "codigo": inp.get("file_text", "")})
        elif b.type in ("code_execution_tool_result", "bash_code_execution_tool_result"):
            res = b.content
            if getattr(res, "stdout", None):
                pending["saida"].append(res.stdout[-2000:])
            if getattr(res, "return_code", 0) and getattr(res, "stderr", None):
                pending["saida"].append("erro: " + res.stderr[-1000:])
            file_ids += [
                f.file_id for f in (getattr(res, "content", None) or []) if getattr(f, "file_id", None)
            ]
    return file_ids


def _download(client, file_ids: list[str], budget: int) -> list[dict]:
    """Baixa os arquivos gerados pelo código. Só imagens PNG/JPEG e CSV, até 3 MB."""
    out = []
    for fid in file_ids[: max(budget, 0)]:
        try:
            meta = client.files.retrieve_metadata(fid)
            name = os.path.basename(meta.filename or fid)
            mime = meta.mime_type or ""
            if name.lower().endswith(".csv"):
                mime = "text/csv"
            if mime not in ALLOWED_FILES:
                log.warning("arquivo gerado ignorado (tipo %s): %s", mime, name)
                continue
            if (meta.size_bytes or 0) > MAX_FILE_BYTES:
                log.warning("arquivo gerado ignorado (grande demais): %s", name)
                continue
            data = client.files.download(fid).read()
            out.append({"nome": name, "mime": mime, "base64": base64.b64encode(data).decode()})
        except anthropic.APIError:
            log.warning("falha ao baixar arquivo gerado %s", fid, exc_info=True)
    return out


def _python_event(ctx: TurnContext, pending: dict, files: list[dict]) -> dict:
    event = {
        "indice": len(ctx.python) + 1,
        "celulas": pending["celulas"],
        "saida": "\n".join(pending["saida"])[-3000:],
        "arquivos": files,
    }
    ctx.python.append(event)
    pending["celulas"], pending["saida"] = [], []
    return {"tipo": "python", **event}


# ------------------------------------------------------------------ o turno


def _load_conversation(
    conn, conversation_id: str | None, visitor: str
) -> tuple[uuid.UUID | None, list, dict, str | None]:
    if not conversation_id:
        return None, [], {}, None
    try:
        cid = uuid.UUID(conversation_id)
    except ValueError:
        return None, [], {}, None
    row = conn.execute(
        "select visitor, messages, seen, container_id from chat_conversation where id = %s", (cid,)
    ).fetchone()
    if not row or row["visitor"] != visitor:
        return None, [], {}, None
    return cid, row["messages"], row["seen"], row["container_id"]


def _client() -> anthropic.Anthropic:
    return anthropic.Anthropic()


def _status_for_server_tool(name: str) -> str:
    if name == "bash_code_execution":
        return "Preparando os arquivos da análise"
    if name == "text_editor_code_execution":
        return "Escrevendo o código"
    return "Executando Python"


def answer(visitor: str, key: str, question: str, conversation_id: str | None = None) -> Iterator[dict]:
    """Responde uma pergunta do visitante (`key`: hash do IP, para o limite diário).
    Emite eventos: conversa, status, visual, python, texto, fim | erro."""
    s = settings()
    t0 = time.monotonic()
    question = question.strip()
    try:
        if not (os.environ.get("ANTHROPIC_API_KEY") or os.environ.get("ANTHROPIC_AUTH_TOKEN")):
            log.error("ANTHROPIC_API_KEY não configurada")
            raise ChatError("O chat ainda não está configurado: falta a chave da Claude API no servidor.")
        if not question:
            raise ChatError("Escreva uma pergunta.")
        if len(question) > 4000:
            raise ChatError("A pergunta é longa demais (máximo de 4.000 caracteres).")
        with connect() as conn:
            cid, messages, seen, container = _load_conversation(conn, conversation_id, visitor)
            check_limits(conn, key, cid)
    except ChatError as e:
        yield {"tipo": "erro", "texto": str(e)}
        return
    except Exception:
        log.exception("falha ao preparar a pergunta")
        yield {"tipo": "erro", "texto": "Ocorreu um erro inesperado. Tente de novo."}
        return

    new_conversation = cid is None
    cid = cid or uuid.uuid4()
    yield {"tipo": "conversa", "id": str(cid)}

    messages = [*messages, {"role": "user", "content": question}]
    usage = {"input_tokens": 0, "output_tokens": 0, "cache_read_tokens": 0, "cache_write_tokens": 0}
    tool_calls: list[dict] = []
    texts: list[str] = []
    ctx = TurnContext(seen=seen, container=container)
    pending = {"celulas": [], "saida": []}  # código executado ainda não exibido
    files_sent = 0
    code_waiting = False  # o código em execução espera resultados nossos (chamada programática)
    model_used, stop_reason, error = s.chat_model, None, None
    tools = tool_definitions()

    try:
        client = _client()
        for step in range(MAX_STEPS):
            params = {
                "model": s.chat_model,
                "max_tokens": 32000,
                "system": system_prompt(),
                "tools": tools,
                # na última etapa, força a resposta final (salvo se o código ainda espera dados)
                "tool_choice": {"type": "none"}
                if step == MAX_STEPS - 1 and not code_waiting
                else {"type": "auto"},
                "messages": messages,
                "output_config": {"effort": s.chat_effort},
                "cache_control": {"type": "ephemeral"},
                "betas": [FALLBACK_BETA],
                "fallbacks": "default",
            }
            if ctx.container:
                params["container"] = ctx.container
            for attempt in range(2):
                try:
                    with client.beta.messages.stream(**params) as stream:
                        for event in stream:
                            if event.type == "text":
                                yield {"tipo": "texto", "texto": event.text}
                            elif event.type == "content_block_start":
                                kind = event.content_block.type
                                if kind == "thinking":
                                    yield {"tipo": "status", "texto": "Pensando…"}
                                elif kind == "server_tool_use":
                                    yield {
                                        "tipo": "status",
                                        "texto": _status_for_server_tool(event.content_block.name),
                                    }
                        msg = stream.get_final_message()
                    break
                except anthropic.BadRequestError as e:
                    # container de outra pergunta que expirou: segue com um novo (o estado do Python se perde)
                    if (
                        attempt == 0
                        and "container" in params
                        and not code_waiting
                        and "container" in str(e).lower()
                    ):
                        log.warning("container %s indisponível; usando um novo", ctx.container)
                        ctx.container = None
                        params.pop("container")
                        continue
                    raise

            u = msg.usage
            usage["input_tokens"] += u.input_tokens or 0
            usage["output_tokens"] += u.output_tokens or 0
            usage["cache_read_tokens"] += u.cache_read_input_tokens or 0
            usage["cache_write_tokens"] += u.cache_creation_input_tokens or 0
            model_used, stop_reason = msg.model, msg.stop_reason
            if getattr(msg, "container", None):
                ctx.container = msg.container.id
            messages.append(
                {
                    "role": "assistant",
                    "content": [b.model_dump(mode="json", exclude_none=True) for b in msg.content],
                }
            )
            texts += [b.text for b in msg.content if b.type == "text"]

            file_ids = _collect_code(msg, pending)
            if file_ids:
                files = _download(client, file_ids, MAX_FILES - files_sent)
                files_sent += len(files)
                if files:
                    yield _python_event(ctx, pending, files)  # gráfico pronto: aparece antes do texto final

            if msg.stop_reason == "refusal":
                error = "refusal"
                yield {
                    "tipo": "erro",
                    "texto": "O modelo não pôde responder a esta pergunta. Tente reformulá-la.",
                }
                break
            if msg.stop_reason == "pause_turn":  # execução longa no servidor: reenviar retoma de onde parou
                continue
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
            code_waiting = False
            for b in tool_uses:
                from_code = getattr(getattr(b, "caller", None), "type", "direct") != "direct"
                code_waiting = code_waiting or from_code
                yield {
                    "tipo": "status",
                    "texto": describe(b.name, b.input if isinstance(b.input, dict) else {}),
                }
                shown = len(ctx.visuals)
                content, is_error = run_tool(b.name, b.input, ctx)
                tool_calls.append({"nome": b.name, "args": b.input, "erro": is_error, "codigo": from_code})
                for spec in ctx.visuals[shown:]:  # figura pronta: vai para a tela antes do texto
                    yield {"tipo": "visual", "visual": spec}
                results.append(
                    {"type": "tool_result", "tool_use_id": b.id, "content": content, "is_error": is_error}
                )
            # resposta a chamadas do código: a mensagem só pode ter tool_result (é o caso)
            messages.append({"role": "user", "content": results})
        if pending["celulas"]:  # análise sem arquivo (ex.: só uma tabela impressa): mostra o código
            yield _python_event(ctx, pending, [])
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
    notes, invalid = resolve_citations(final_text, ctx.seen)
    if invalid:
        log.warning("citações sem trecho correspondente: %s", invalid)
    cost = cost_usd(usage)
    latency = int((time.monotonic() - t0) * 1000)

    with connect() as conn:
        # O histórico só é gravado quando o turno termina inteiro: um turno quebrado deixaria
        # tool_use sem tool_result e invalidaria a conversa.
        if error is None:
            conn.execute(
                """insert into chat_conversation (id, visitor, messages, seen, container_id)
                   values (%s, %s, %s, %s, %s)
                   on conflict (id) do update set messages = excluded.messages, seen = excluded.seen,
                       container_id = excluded.container_id, updated_at = now()""",
                (cid, visitor, json.dumps(messages), json.dumps(ctx.seen), ctx.container),
            )
        elif new_conversation:
            conn.execute(
                "insert into chat_conversation (id, visitor) values (%s, %s) on conflict do nothing",
                (cid, visitor),
            )
        turn_id = conn.execute(
            """insert into chat_turn (conversation_id, visitor, client_key, question, answer, citations,
                   invalid_citations, tool_calls, visuals, python, model, input_tokens, output_tokens,
                   cache_read_tokens, cache_write_tokens, cost_usd, latency_ms, stop_reason, error)
               values (%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s) returning id""",
            (
                cid, visitor, key, question, final_text, json.dumps(notes), json.dumps(invalid),
                json.dumps(tool_calls, default=str), json.dumps(ctx.visuals), json.dumps(ctx.python),
                model_used, usage["input_tokens"], usage["output_tokens"], usage["cache_read_tokens"],
                usage["cache_write_tokens"], cost, latency, stop_reason, error,
            ),
        ).fetchone()["id"]  # fmt: skip
        conn.commit()

    log.info(
        "chat: %s | %d ferramentas | %d figuras | %d análises Python | US$ %.4f | %d ms",
        visitor[:8], len(tool_calls), len(ctx.visuals), len(ctx.python), cost, latency,
    )  # fmt: skip
    if error is None:
        yield {"tipo": "fim", "turno": turn_id, "citacoes": notes, "invalidas": invalid}


def feedback(visitor: str, turn_id: int, value: int, comment: str | None) -> None:
    """Avaliação de uma resposta; só o visitante que perguntou pode avaliá-la."""
    with connect() as conn:
        conn.execute(
            "update chat_turn set feedback = %s, comment = %s where id = %s and visitor = %s",
            (value, (comment or "").strip()[:2000] or None, turn_id, visitor),
        )
        conn.commit()
