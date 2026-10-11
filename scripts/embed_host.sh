#!/usr/bin/env bash
# Gera embeddings no Mac, fora do Docker, usando a GPU Apple (MPS) — várias vezes mais rápido
# que a CPU do container. Grava no mesmo Postgres (localhost:5433).
set -euo pipefail
cd "$(dirname "$0")/.."
uv sync --frozen -q
# uv marca arquivos do .venv como ocultos no macOS e o Python 3.12 ignora .pth ocultos
chflags nohidden .venv/lib/python3.12/site-packages/*.pth 2>/dev/null || true
export DATABASE_URL="${DATABASE_URL:-postgresql://psi:psi@localhost:5433/psilib}"
export PYTHONPATH=src EMBEDDING_DEVICE="${EMBEDDING_DEVICE:-auto}"
exec uv run --no-sync python -m psilib.cli embed "$@"
