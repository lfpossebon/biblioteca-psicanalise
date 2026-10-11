## Biblioteca psicanalítica — atalhos. `make help` lista tudo.
DC  := docker compose
RUN := $(DC) run --rm pipeline

.PHONY: help up down build logs psql test migrate inventory staferla staferla-freud probe extract ocr chunk \
        concepts embed embed-host refresh pipeline stats watch watch-list lab hydrate mcp-add

help:  ## Lista os comandos
	@grep -E '^[a-zA-Z_-]+:.*?## ' $(MAKEFILE_LIST) | awk 'BEGIN {FS = ":.*?## "}; {printf "  \033[36m%-16s\033[0m %s\n", $$1, $$2}'

build:  ## Constrói a imagem
	$(DC) build app

up:  ## Sobe Postgres + API/MCP (http://localhost:8010/docs, MCP em /mcp)
	$(DC) up -d db app

down:  ## Para os serviços (dados ficam no volume pgdata)
	$(DC) down

logs:  ## Logs da API/MCP
	$(DC) logs -f app

psql:  ## Console SQL
	$(DC) exec db psql -U psi -d psilib

test:  ## Testes unitários (sem banco)
	PYTHONPATH=src uv run pytest -q

# ---------------------------------------------------------------- pipeline (incremental)
migrate:  ## Cria/atualiza o esquema
	$(RUN) migrate
inventory:  ## Varre o acervo local e gera catalog/catalogo.csv
	$(RUN) inventory
staferla:  ## Baixa/atualiza os seminários do Staferla
	$(RUN) staferla
staferla-freud:  ## Idem, incluindo Freud (SE + GW)
	$(RUN) staferla --freud
probe:  ## Sugere a linha teórica dos documentos 'revisar' pelo conteúdo
	$(RUN) probe
extract:  ## Extrai texto do que está no escopo
	$(RUN) extract
ocr:  ## OCR dos PDFs escaneados (lento) e re-extração
	$(RUN) ocr && $(RUN) extract && $(RUN) chunk
chunk:  ## Gera os trechos datados
	$(RUN) chunk
concepts:  ## Recalcula menções de conceitos (após editar config/concepts.yaml)
	$(RUN) concepts && $(RUN) refresh
embed:  ## Gera embeddings faltantes (CPU, dentro do Docker)
	$(RUN) embed
embed-host:  ## Gera embeddings no Mac com GPU Apple (MPS) — bem mais rápido
	./scripts/embed_host.sh
refresh:  ## Recalcula visões de linha do tempo e coocorrência
	$(RUN) refresh
pipeline:  ## Tudo, de ponta a ponta (só processa o que é novo)
	$(RUN) run
stats:  ## Panorama do acervo
	$(RUN) stats

watch:  ## Procura publicações novas (OpenAlex)
	$(RUN) watch scan
watch-list:  ## Lista publicações novas pendentes
	$(RUN) watch list

lab:  ## Jupyter em http://localhost:8888
	$(DC) --profile lab up -d lab && $(DC) logs lab | grep -m1 token= || true

# Força o download do iCloud dos arquivos no escopo antes de rodar no Docker
hydrate:  ## Baixa do iCloud os arquivos do acervo (evita leituras lentas no container)
	./scripts/hydrate.sh

mcp-add:  ## Registra a biblioteca como servidor MCP no Claude Code
	claude mcp add --transport http psicanalise http://localhost:8010/mcp
