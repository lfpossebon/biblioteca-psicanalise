# syntax=docker/dockerfile:1.7
# Imagem única para pipeline, API e MCP (mesmo código, comandos diferentes).
FROM python:3.12-slim-bookworm AS base

ENV PYTHONUNBUFFERED=1 \
    PYTHONDONTWRITEBYTECODE=1 \
    UV_COMPILE_BYTECODE=1 \
    UV_LINK_MODE=copy \
    UV_NO_CACHE=1 \
    HF_HOME=/cache/huggingface \
    PATH="/app/.venv/bin:$PATH"

# OCR: tesseract com os idiomas do acervo + ocrmypdf
RUN apt-get update && apt-get install -y --no-install-recommends \
        ocrmypdf tesseract-ocr tesseract-ocr-por tesseract-ocr-spa tesseract-ocr-fra tesseract-ocr-eng \
        tesseract-ocr-deu ca-certificates \
    && rm -rf /var/lib/apt/lists/*

# Usuário sem privilégios para rodar a aplicação. O cache de modelos já nasce com o dono certo:
# volumes nomeados herdam a permissão do diretório da imagem na primeira montagem.
RUN useradd --uid 1000 --create-home --shell /usr/sbin/nologin psi \
    && mkdir -p /cache/huggingface /data \
    && chown psi:psi /cache/huggingface /data

COPY --from=ghcr.io/astral-sh/uv:0.12.13 /uv /uvx /bin/

WORKDIR /app
# Dependências primeiro (camada em cache enquanto só o código muda)
COPY pyproject.toml uv.lock ./
RUN uv sync --frozen --no-dev --no-install-project

# Modelo de embeddings embutido na imagem (BAKE_MODEL=1, usado na nuvem): artefato imutável na
# revisão fixa, sem download a cada deploy. Localmente fica 0 e o modelo vive no volume hf_cache.
ARG BAKE_MODEL=0
ARG EMBEDDING_MODEL=BAAI/bge-m3
ARG EMBEDDING_REVISION=5617a9f61b028005a4858fdac845db406aefb181
USER psi
RUN if [ "$BAKE_MODEL" = "1" ]; then \
        python -c "import os; from sentence_transformers import SentenceTransformer as M; \
M(os.environ['EMBEDDING_MODEL'], revision=os.environ['EMBEDDING_REVISION'], device='cpu')"; \
    fi
USER root

COPY src ./src
COPY config ./config
COPY db ./db
RUN uv sync --frozen --no-dev

USER psi
EXPOSE 8010
# Porta vem de $PORT quando a plataforma injeta (Railway, Cloud Run); `exec` mantém o uvicorn como PID 1
CMD ["sh", "-c", "exec uvicorn psilib.api:app --host 0.0.0.0 --port ${PORT:-8010}"]

# Testes dentro do mesmo ambiente da produção (make test-docker): base + pytest/ruff + tests/
FROM base AS test
USER root
RUN uv sync --frozen
COPY tests ./tests
USER psi
CMD ["pytest", "-q", "-p", "no:cacheprovider"]

# Jupyter para análises exploratórias (make lab)
FROM base AS lab
USER root
RUN uv sync --frozen --no-dev --extra lab
USER psi
CMD ["jupyter", "lab", "--ip=0.0.0.0", "--port=8888", "--no-browser", "--ServerApp.root_dir=/app/notebooks"]

# Imagem final padrão
FROM base
