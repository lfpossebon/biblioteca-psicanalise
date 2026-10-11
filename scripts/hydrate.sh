#!/usr/bin/env bash
# Baixa do iCloud (no Mac, fora do Docker) apenas os arquivos do acervo que estão no escopo.
# Dentro do container, arquivos "dataless" do iCloud dão erro de I/O.
set -euo pipefail
cd "$(dirname "$0")/.."
CORPUS="${CORPUS_DIR_HOST:-$(cd .. && pwd)}"
JOBS="${JOBS:-4}"

docker compose exec -T db psql -U psi -d psilib -At -c \
  "select substr(uri, 7) from document where source_id='local' and status in ('in_scope','failed')" |
while IFS= read -r rel; do
  f="$CORPUS/$rel"
  # só os que ainda não estão no disco
  if ls -lO "$f" 2>/dev/null | grep -q dataless; then printf '%s\0' "$f"; fi
done |
xargs -0 -n 1 -P "$JOBS" sh -c 'cat "$0" > /dev/null && echo "ok  $(basename "$0")" || echo "ERRO $0"'
