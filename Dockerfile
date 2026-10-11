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

COPY --from=ghcr.io/astral-sh/uv:0.12.13 /uv /uvx /bin/

WORKDIR /app
# Dependências primeiro (camada em cache enquanto só o código muda)
COPY pyproject.toml uv.lock ./
RUN --mount=type=cache,target=/root/.cache/uv uv sync --frozen --no-dev --no-install-project

COPY src ./src
COPY config ./config
COPY db ./db
RUN --mount=type=cache,target=/root/.cache/uv uv sync --frozen --no-dev

EXPOSE 8010
CMD ["uvicorn", "psilib.api:app", "--host", "0.0.0.0", "--port", "8010"]

# Jupyter para análises exploratórias (make lab)
FROM base AS lab
RUN --mount=type=cache,target=/root/.cache/uv uv sync --frozen --no-dev --extra lab
CMD ["jupyter", "lab", "--ip=0.0.0.0", "--port=8888", "--no-browser", "--allow-root", "--ServerApp.root_dir=/app/notebooks"]

# Imagem final padrão
FROM base
