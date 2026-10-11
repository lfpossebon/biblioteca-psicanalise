#!/usr/bin/env bash
# Publica o banco local num Postgres remoto VAZIO (primeira carga da POC na nuvem).
#
#   TARGET_DATABASE_URL=postgresql://usuario:senha@host:porta/banco ./scripts/publish_db.sh
#
# Vai o que o chat precisa: catálogo, trechos, embeddings, conceitos e visões. Fica de fora o
# texto bruto por página (segment, só usado pelo pipeline). Recusa um destino que já tenha tabelas,
# para nunca apagar as conversas e avaliações gravadas pelos convidados.
set -euo pipefail
cd "$(dirname "$0")/.."
: "${TARGET_DATABASE_URL:?defina TARGET_DATABASE_URL (Postgres de destino, vazio)}"
IMAGE=pgvector/pgvector:0.8.0-pg16
DUMP="$(mktemp -d)/psilib.dump"
trap 'rm -rf "$(dirname "$DUMP")"' EXIT

remote() { docker run --rm -i -e PGCONNECT_TIMEOUT=15 "$IMAGE" "$@"; }

tables=$(remote psql "$TARGET_DATABASE_URL" -Atc "select count(*) from pg_tables where schemaname='public'")
if [ "$tables" != "0" ]; then
  echo "O destino já tem $tables tabelas: abortando para não sobrescrever dados da POC." >&2
  exit 1
fi

echo "→ exportando o banco local (sem a tabela segment e sem conversas locais)"
docker compose exec -T db pg_dump -U psi -d psilib -Fc --no-owner --no-privileges \
  --exclude-table-data=segment --exclude-table-data=chat_turn --exclude-table-data=chat_conversation \
  > "$DUMP"
ls -lh "$DUMP" | awk '{print "  dump:", $5}'

echo "→ restaurando no destino (o índice HNSW é reconstruído: alguns minutos)"
remote pg_restore --no-owner --no-privileges --exit-on-error -d "$TARGET_DATABASE_URL" < "$DUMP"

echo "→ conferindo"
remote psql "$TARGET_DATABASE_URL" -Atc \
  "select 'trechos: ' || count(*) from chunk union all select 'embeddings: ' || count(*) from chunk_embedding
   union all select 'migrações: ' || string_agg(name, ', ' order by name) from schema_migration"
