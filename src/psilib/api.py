"""API REST (FastAPI, docs em /docs) + servidor MCP (/mcp) + chat da POC (/chat), no mesmo processo.

Um processo só = um modelo de embeddings carregado na memória para todas as interfaces.
Com PUBLIC_MODE=true (implantação aberta na internet) só o chat e os health checks são servidos:
a API REST, o /docs e o MCP — que expõem o texto integral do acervo — ficam desligados.
"""

import json
import threading
from contextlib import AsyncExitStack, asynccontextmanager
from pathlib import Path
from typing import Annotated, Literal

from fastapi import APIRouter, FastAPI, Header, Query, Request
from fastapi.responses import FileResponse, JSONResponse, RedirectResponse, StreamingResponse
from pydantic import BaseModel, Field

from psilib import chat, embeddings, service
from psilib.config import settings
from psilib.service import Filters

STATIC = Path(__file__).parent / "static"

# ------------------------------------------------------------------ health checks


health_router = APIRouter()


@health_router.get("/health")
def health():
    """Liveness: o processo está de pé."""
    return {"ok": True}


@health_router.get("/ready")
def ready():
    """Readiness: banco acessível e modelo de embeddings carregado. 503 enquanto não estiver pronto."""
    checks = {"banco": service.db_ok(), "embeddings": embeddings.is_ready()}
    return JSONResponse(
        {"pronto": all(checks.values()), **checks}, status_code=200 if all(checks.values()) else 503
    )


# ------------------------------------------------------------------ API REST interna


internal_router = APIRouter()


def _filters(linha, autor, obra, lingua, ano_de, ano_ate, fonte) -> Filters:
    return Filters(
        lines=linha, author=autor, work=obra, language=lingua, year_from=ano_de, year_to=ano_ate, source=fonte
    )


Linha = Annotated[list[str] | None, Query(description="freud, lacan, lacaniano, escola_argentina...")]


@internal_router.get("/busca")
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


@internal_router.get("/trechos/{chunk_id}")
def trecho(chunk_id: int, contexto: int = 1):
    return service.read_chunk(chunk_id, contexto)


@internal_router.get("/conceitos")
def conceitos():
    return service.list_concepts()


@internal_router.get("/conceitos/{conceito}/linha-do-tempo")
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


@internal_router.get("/conceitos/{conceito}/relacionados")
def relacionados(conceito: str, limite: int = 15):
    return service.cooccurrence(conceito, limite)


@internal_router.get("/obras")
def obras(linha: Linha = None, autor: str | None = None, lingua: str | None = None, fonte: str | None = None):
    return service.list_works(_filters(linha, autor, None, lingua, None, None, fonte))


@internal_router.get("/estatisticas")
def estatisticas():
    return service.stats()


# ------------------------------------------------------------------ chat da POC


chat_router = APIRouter(prefix="/chat")
Visitante = Annotated[str | None, Header(alias="X-Visitante")]


def _client_ip(request: Request) -> str | None:
    # Atrás do proxy da plataforma (Railway), o IP do cliente vem nos cabeçalhos de encaminhamento.
    forwarded = request.headers.get("x-forwarded-for")
    if forwarded:
        return forwarded.split(",")[0].strip()
    return request.headers.get("x-real-ip") or (request.client.host if request.client else None)


class Mensagem(BaseModel):
    pergunta: str = Field(max_length=4000)
    conversa: str | None = None


class Feedback(BaseModel):
    turno: int
    valor: Literal[1, -1]
    comentario: str | None = Field(None, max_length=2000)


@chat_router.get("", include_in_schema=False)
def chat_page():
    return FileResponse(STATIC / "chat.html", headers={"Cache-Control": "no-cache"})


@chat_router.post("/api/mensagem")
def chat_mensagem(body: Mensagem, request: Request, visitante: Visitante = None):
    visitor, key = chat.visitor_id(visitante), chat.client_key(_client_ip(request))

    # Gerador síncrono: o Starlette o consome no thread pool, sem bloquear o event loop (Deck 03).
    def sse():
        for event in chat.answer(visitor, key, body.pergunta, body.conversa):
            yield f"data: {json.dumps(event, ensure_ascii=False)}\n\n"

    return StreamingResponse(
        sse(),
        media_type="text/event-stream",
        headers={"Cache-Control": "no-cache", "X-Accel-Buffering": "no"},
    )


@chat_router.post("/api/feedback")
def chat_feedback(body: Feedback, visitante: Visitante = None):
    chat.feedback(chat.visitor_id(visitante), body.turno, body.valor, body.comentario)
    return {"ok": True}


# ------------------------------------------------------------------ montagem


def create_app() -> FastAPI:
    public = settings().public_mode
    mcp_app = None
    if not public:
        from psilib.mcp_server import mcp, transport_security

        mcp_app = mcp.streamable_http_app(transport_security=transport_security(), host="0.0.0.0")

    @asynccontextmanager
    async def lifespan(_app: FastAPI):
        # Carrega o modelo de embeddings em background: a API sobe na hora e /ready só fica
        # verde quando a primeira busca não vai mais pagar o cold start (~2 GB na memória).
        if settings().embedding_warmup:
            threading.Thread(target=embeddings.warmup, name="embeddings-warmup", daemon=True).start()
        async with AsyncExitStack() as stack:
            if mcp_app is not None:
                await stack.enter_async_context(mcp_app.router.lifespan_context(mcp_app))
            yield

    app = FastAPI(
        title="Biblioteca Psicanalítica",
        description="Freud, Lacan e escolas lacanianas — busca com citação e linha do tempo de conceitos. "
        "Servidor MCP em /mcp; chat em /chat.",
        version="0.1.0",
        lifespan=lifespan,
        docs_url=None if public else "/docs",
        redoc_url=None if public else "/redoc",
        openapi_url=None if public else "/openapi.json",
    )
    app.include_router(health_router)
    app.include_router(chat_router)
    if public:
        app.add_api_route("/", lambda: RedirectResponse("/chat"), include_in_schema=False)
    else:
        app.include_router(internal_router)
        # Por último: tudo que não é rota REST vai para o app MCP (que atende em /mcp).
        app.mount("/", mcp_app)
    return app


app = create_app()
