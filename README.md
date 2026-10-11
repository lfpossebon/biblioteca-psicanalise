# Biblioteca Psicanalítica

Freud, Lacan e as escolas lacanianas (incluindo a argentina) num único lugar, indexados para
**estudo de conceitos com IA**: busca entre línguas com citação, leitura em contexto e
**linha do tempo de cada conceito** (sessão a sessão nos seminários de Lacan).

```
acervo local (PDF/DOCX, somente leitura) ─┐
Staferla (seminários FR, datas de sessão) ─┼─► pipeline ─► Postgres + pgvector ─► API REST  (/docs)
OpenAlex (publicações novas, curadas) ────┘   (Docker)    texto · vetores ·      └► MCP (/mcp) ─► Claude
                                                          conceitos · linha do tempo
```

## Começando

Pré-requisitos: Docker Desktop (8 GB de RAM para o Docker) e ~5 GB livres.

```bash
make build            # imagem com Python, tesseract (OCR) e dependências travadas
make up               # Postgres (localhost:5433) + API/MCP (localhost:8010)
make migrate
make inventory        # classifica o acervo → catalog/catalogo.csv
make staferla         # baixa os 28 seminários (devagar, por cortesia ao site)
make extract chunk concepts refresh
make embed            # embeddings (1ª vez baixa o modelo BGE-M3, ~2 GB; CPU: algumas horas)
make mcp-add          # registra a biblioteca no Claude Code
```

Depois, a qualquer momento: `make pipeline` (só processa o que é novo).

Os arquivos do acervo estão no iCloud. Rode `make hydrate` antes da primeira extração para baixá-los.

A API só fica `healthy` quando o banco responde e o modelo de embeddings está carregado
(`/ready`; `/health` indica apenas que o processo está de pé).

## Desenvolvimento

```bash
make check            # lint (ruff) + testes com cobertura — rode antes de cada commit
make test             # unitários + integração + e2e (estes usam o Postgres do `make up`,
                      # num banco descartável psilib_test; sem Postgres, são pulados)
make test-docker      # os mesmos testes dentro da imagem, no ambiente da produção
make format           # corrige e formata o código
make up-prod          # sobe só o que está na imagem (sem montar src/)
```

`docker-compose.override.yml` monta `src/` ao vivo para desenvolvimento e é carregado
automaticamente; a imagem em si é autocontida. Os containers rodam como usuário sem privilégios (`psi`).

## Usando com o Claude

Com o MCP registrado, pergunte em português, por exemplo:

- "Explique o objeto a para um iniciante, com trechos de Lacan e de Eidelsztein."
- "Como o gozo ganha peso ao longo dos seminários? Mostre a linha do tempo por sessão."
- "Quais conceitos mais se associam ao sinthoma? O que dizer da passagem do Seminário 22 ao 23?"

Ferramentas expostas: `buscar`, `ler_trecho`, `linha_do_tempo`, `conceitos_relacionados`,
`listar_conceitos`, `listar_obras`, `estatisticas_acervo`.

## Chat da POC (avaliação com especialistas)

Página de chat em `/chat` (local: http://localhost:8010/chat): o Claude Opus 5.5 responde consultando
a biblioteca e cita os trechos com notas (autor, obra, sessão, página). Precisa de `ANTHROPIC_API_KEY`
no `.env` (depois, `docker compose up -d app`).

- Acesso aberto e anônimo. Limites: `CHAT_DAILY_LIMIT` perguntas por conexão por dia (IP guardado só
  como hash) e `CHAT_DAILY_BUDGET_USD` de gasto total por dia.
- Perguntas, respostas, custo e avaliações (👍/👎 + comentário) ficam em `chat_turn`.
  Resumo: `docker compose run --rm pipeline chat-relatorio` (local) ou `./scripts/relatorio_nuvem.sh` (nuvem).
- Citações só valem para trechos que a busca devolveu; o leitor vê no máximo 300 caracteres de cada.

### Na nuvem (Railway)

Projeto `biblioteca-psicanalise`, dois serviços:

| Serviço | O que é | Variáveis principais |
|---|---|---|
| `db` | `pgvector/pgvector:0.8.0-pg16` + volume em `/var/lib/postgresql/data` | `POSTGRES_*`, `PGDATA` |
| `app` | esta imagem (Dockerfile, `railway.json`), domínio público | `PUBLIC_MODE=true`, `BAKE_MODEL=1`, `HF_HUB_OFFLINE=1`, `DATABASE_URL` (referência a `db`), `ANTHROPIC_API_KEY`, `CHAT_*` |

- Novo deploy do código: `railway up --service app --ci`.
- Pausar a POC (para de cobrar memória): no painel, *app → Settings → Pause*; ou `railway down --service app`.
- Logs: `railway logs --service app`.
- Relatório da POC na nuvem (perguntas, custo, avaliações e comentários): `./scripts/relatorio_nuvem.sh [dias]`
  (abre um túnel temporário para o banco e o fecha ao terminar).
- Endereço público: https://app-production-aebe.up.railway.app
- O banco foi carregado uma vez com `scripts/publish_db.sh` (exige destino vazio, para nunca apagar
  as conversas). Para atualizar o acervo na nuvem, recrie o banco ou atualize tabela a tabela.
- Com `PUBLIC_MODE=true` não há `/docs`, REST nem MCP: só `/chat`, `/health` e `/ready`.

## Curadoria do escopo

- **Entram:** Freud, Lacan, comentadores lacanianos e freudianos, escola argentina, dicionários.
- **Ficam fora** (classificados, mas não indexados): Klein, Winnicott, Ferenczi, psicologia do
  ego, epistemologia de apoio e material fora do tema.

Regras em [config/scope.yaml](config/scope.yaml). Para mudar um arquivo específico, edite
[catalog/overrides.csv](catalog/overrides.csv) (`incluir=sim|nao`, `linha`, `autor`...) e rode
`make inventory`. Para os arquivos sem regra, `make probe` lê o início do texto e sugere a linha;
a sugestão aparece em `catalog/catalogo.csv`.

## Conceitos e linha do tempo

O léxico multilíngue (pt/es/fr/en/de) fica em [config/concepts.yaml](config/concepts.yaml).
Depois de editar, rode `make concepts`. A métrica é *ocorrências por 10 mil palavras* por período.
O raciocínio está em [docs/adr/0002](docs/adr/0002-medicao-de-conceitos.md).

```bash
docker compose run --rm pipeline timeline gozo --granularidade sessao --linha lacan
curl 'localhost:8010/conceitos/sinthoma/linha-do-tempo?granularidade=ano&linha=lacan'
```

## Novidades

```bash
make watch            # busca publicações novas no OpenAlex (SciELO, Redalyc, periódicos)
make watch-list       # revisa as pendentes
docker compose run --rm pipeline watch accept 12 15 --linha escola_argentina
make staferla         # revalida o Staferla e baixa só o que mudou (via ETag)
                      # se o site estiver fora do ar, usa as cópias do Internet Archive;
                      # quando ele voltar, a próxima execução troca pelas versões atuais
```

Para automatizar, agende `make staferla watch pipeline` semanalmente (cron/launchd).

## Estrutura

```
config/        escopo, obras canônicas, léxico de conceitos, fontes monitoradas
catalog/       overrides.csv (curadoria, versionado) · catalogo.csv (gerado)
db/migrations/ esquema SQL versionado (camadas bronze/silver/gold)
src/psilib/    pipeline, extratores, busca, API, MCP
docs/adr/      decisões de arquitetura (0003: por que não Kedro)
tests/         unitários, integração (Postgres) e e2e da API (make test)
```

## Uso e direitos

Esta biblioteca é para estudo pessoal. O acervo local inclui obras protegidas por direitos
autorais: não publique o banco nem trechos extensos. O monitor só baixa PDFs de acesso aberto.
O Staferla é baixado com intervalo entre requisições e revalidado por ETag, sem baixar de novo
o que não mudou.
