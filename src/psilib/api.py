"""API REST (FastAPI, docs em /docs) + servidor MCP (/mcp) no mesmo processo.

Um processo só = um modelo de embeddings carregado na memória para as duas interfaces.
"""

import threading
from contextlib import asynccontextmanager
from typing import Annotated, Literal

from fastapi import FastAPI, Query
from fastapi.responses import JSONResponse

from psilib import embeddings, service
from psilib.config import settings
from psilib.mcp_server import mcp, transport_security
from psilib.service import Filters

_mcp_app = mcp.streamable_http_app(transport_security=transport_security(), host="0.0.0.0")


@asynccontextmanager
async def lifespan(_app: FastAPI):
    # Carrega o modelo de embeddings em background: a API sobe na hora e /ready só fica
    # verde quando a primeira busca não vai mais pagar o cold start (~2 GB na memória).
    if settings().embedding_warmup:
        threading.Thread(target=embeddings.warmup, name="embeddings-warmup", daemon=True).start()
    async with _mcp_app.router.lifespan_context(_mcp_app):
        yield


app = FastAPI(
    title="Biblioteca Psicanalítica",
    description="Freud, Lacan e escolas lacanianas — busca com citação e linha do tempo de conceitos. "
    "Servidor MCP em /mcp.",
    version="0.1.0",
    lifespan=lifespan,
)


def _filters(linha, autor, obra, lingua, ano_de, ano_ate, fonte) -> Filters:
    return Filters(
        lines=linha, author=autor, work=obra, language=lingua, year_from=ano_de, year_to=ano_ate, source=fonte
    )


Linha = Annotated[list[str] | None, Query(description="freud, lacan, lacaniano, escola_argentina...")]


@app.get("/health")
def health():
    """Liveness: o processo está de pé."""
    return {"ok": True}


@app.get("/ready")
def ready():
    """Readiness: banco acessível e modelo de embeddings carregado. 503 enquanto não estiver pronto."""
    checks = {"banco": service.db_ok(), "embeddings": embeddings.is_ready()}
    return JSONResponse(
        {"pronto": all(checks.values()), **checks}, status_code=200 if all(checks.values()) else 503
    )


@app.get("/busca")
def busca(
    q: str,
    k: int = 10,
    modo: Literal["hybrid", "text", "vector"] = "hybrid",
    linha: Linha = None,
    autor: str | None = None,
    obra: str | None = None,
    lingua: str | None = None,
    ano_de: int | None = None,
    ano_ate: int | None = None,
    fonte: str | None = None,
):
    return service.search(q, k, _filters(linha, autor, obra, lingua, ano_de, ano_ate, fonte), mode=modo)


@app.get("/trechos/{chunk_id}")
def trecho(chunk_id: int, contexto: int = 1):
    return service.read_chunk(chunk_id, contexto)


@app.get("/conceitos")
def conceitos():
    return service.list_concepts()


@app.get("/conceitos/{conceito}/linha-do-tempo")
def linha_do_tempo(
    conceito: str,
    granularidade: Literal["sessao", "ano", "obra"] = "obra",
    linha: Linha = None,
    autor: str | None = None,
    obra: str | None = None,
    lingua: str | None = None,
    ano_de: int | None = None,
    ano_ate: int | None = None,
    fonte: str | None = None,
    min_palavras: int = 2000,
):
    f = _filters(linha, autor, obra, lingua, ano_de, ano_ate, fonte)
    return service.timeline(conceito, granularidade, f, min_palavras)


@app.get("/conceitos/{conceito}/relacionados")
def relacionados(conceito: str, limite: int = 15):
    return service.cooccurrence(conceito, limite)


@app.get("/obras")
def obras(linha: Linha = None, autor: str | None = None, lingua: str | None = None, fonte: str | None = None):
    return service.list_works(_filters(linha, autor, None, lingua, None, None, fonte))


@app.get("/estatisticas")
def estatisticas():
    return service.stats()


# Por último: tudo que não é rota REST vai para o app MCP (que atende em /mcp).
app.mount("/", _mcp_app)
