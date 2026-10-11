# syntax=docker/dockerfile:1.7
# Imagem única para pipeline, API e MCP (mesmo código, comandos diferentes).
FROM python:3.12-slim-bookworm AS base

ENV PYTHONUNBUFFERED=1 \
    PYTHONDONTWRITEBYTECODE=1 \
    UV_COMPILE_BYTECODE=1 \
    UV_LINK_MODE=copy \
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
RUN --mount=type=cache,target=/root/.cache/uv uv sync --frozen --no-dev --no-install-project

COPY src ./src
COPY config ./config
COPY db ./db
RUN --mount=type=cache,target=/root/.cache/uv uv sync --frozen --no-dev

USER psi
EXPOSE 8010
CMD ["uvicorn", "psilib.api:app", "--host", "0.0.0.0", "--port", "8010"]

# Testes dentro do mesmo ambiente da produção (make test-docker): base + pytest/ruff + tests/
FROM base AS test
USER root
RUN --mount=type=cache,target=/root/.cache/uv uv sync --frozen
COPY tests ./tests
USER psi
CMD ["pytest", "-q", "-p", "no:cacheprovider"]

# Jupyter para análises exploratórias (make lab)
FROM base AS lab
USER root
RUN --mount=type=cache,target=/root/.cache/uv uv sync --frozen --no-dev --extra lab
USER psi
CMD ["jupyter", "lab", "--ip=0.0.0.0", "--port=8888", "--no-browser", "--ServerApp.root_dir=/app/notebooks"]

# Imagem final padrão
FROM base
