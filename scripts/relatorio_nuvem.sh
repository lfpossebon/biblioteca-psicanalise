#!/usr/bin/env bash
# Relatório da POC na nuvem (Railway): perguntas por dia, por visitante, avaliações e comentários.
#
#   ./scripts/relatorio_nuvem.sh [dias]          (padrão: 30)
#
# Abre um túnel TCP temporário para o banco, roda `psilib chat-relatorio` no container local e
# fecha o túnel ao sair (inclusive em caso de erro): o banco não fica exposto.
set -euo pipefail
cd "$(dirname "$0")/.."
DIAS="${1:-30}"
quiet() { grep -v -i "config as code\|migrate\|Existing files\|agent tooling\|Setup:" || true; }

proxy=$(railway tcp-proxy create --port 5432 --service db --json 2>/dev/null | python3 -c \
  "import json,sys; p=json.load(sys.stdin)['proxy']; print(p['id'], p['domain'], p['proxyPort'])")
read -r PROXY_ID HOST PORT <<<"$proxy"
trap 'railway tcp-proxy delete "$PROXY_ID" --service db --yes >/dev/null 2>&1 && echo "(túnel fechado)"' EXIT

PW=$(railway variable list --service db --kv 2>/dev/null | grep '^POSTGRES_PASSWORD=' | cut -d= -f2-)
URL="postgresql://psi:${PW}@${HOST}:${PORT}/psilib"

for _ in $(seq 1 12); do  # o túnel leva alguns segundos para ficar ativo
  docker run --rm -e PGCONNECT_TIMEOUT=5 pgvector/pgvector:0.8.0-pg16 psql "$URL" -Atc "select 1" >/dev/null 2>&1 && break
  sleep 5
done

docker compose run --rm --no-deps -e DATABASE_URL="$URL" pipeline chat-relatorio --dias "$DIAS" 2>&1 \
  | grep -v "^ Container" | quiet
