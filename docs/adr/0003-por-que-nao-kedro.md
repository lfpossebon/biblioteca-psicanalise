# ADR 0003 — Por que não usar Kedro (e como seus princípios foram aplicados)

Status: aceito · Data: 2026-10-11

## Contexto

A disciplina de Deploy (Insper PADS) usa Kedro + FastAPI + Docker como pilha de referência para
levar ciência de dados à produção. Avaliamos se o pipeline desta biblioteca deveria ser migrado
para Kedro.

O Kedro foi desenhado para pipelines **batch** de ML: um DAG que, a cada `kedro run`, lê arquivos
pelo Data Catalog, ajusta transformações no treino (fit/transform) e grava artefatos. O próprio
material do curso lista as limitações: foco em batch, não é orquestrador, e acrescenta overhead em
workloads que não seguem o padrão.

Esta biblioteca tem outra natureza: é uma **ingestão incremental** sobre um acervo que cresce aos
poucos (PDFs novos, seminários atualizados no Staferla, artigos do OpenAlex). Reprocessar tudo a
cada execução custaria horas (só os embeddings levam horas em CPU). O estado de cada documento
(`in_scope → extracted → chunked`), o hash SHA-256 e o ETag do Staferla ficam no Postgres, e cada
etapa processa apenas o que falta. Não há treino de modelo: o modelo de embeddings é pré-treinado
e só faz inferência.

## Decisão

Não usar Kedro. Manter o pipeline atual (etapas idempotentes em `psilib.pipeline`, estado no
Postgres, CLI Typer) e aplicar explicitamente os princípios que o Kedro encarna.

| Princípio do Kedro / 12 fatores | Como está aplicado aqui |
|---|---|
| Lógica em `src/`, configuração em `conf/` | `src/psilib/` × `config/*.yaml` (montado `:ro`) e `.env` |
| Camadas de dados (01_raw … 08_reporting) | raw (`/corpus`, `data/raw`) → bronze `segment` → silver `chunk` → gold (menções, embeddings, visões) |
| Nós como funções puras e testáveis | Extração, chunking, datas e conceitos são puros e cobertos por testes unitários; o I/O fica concentrado em `pipeline.py` |
| Checkpoints / descartabilidade | Status por documento + `pipeline_run`; execuções interrompidas são marcadas e retomadas |
| Artefato produtivo versionado | Vetores gravados com o nome do modelo; revisão do BGE-M3 fixada em `embedding_revision` |
| Pirâmide de testes | Unitários (`test_units.py`), integração com Postgres (`test_integration.py`), e2e da API (`test_api.py`) |
| Build, release, run | Imagem única (`uv.lock` + `--frozen`), testes dentro da imagem (`make test-docker`) |

## Consequências

- Sem a visualização do DAG (`kedro viz`): a ordem das etapas está documentada no docstring de
  `psilib.pipeline` e no comando `psilib run`.
- Sem o Data Catalog: trocar a origem de um dado exige mudar código, não YAML. Aceitável porque as
  fontes são poucas e estáveis (acervo local, Staferla, OpenAlex).
- Rever esta decisão se o projeto passar a **treinar** modelos (ex.: classificador de linha teórica
  ou detecção semântica de conceitos, ADR 0002), caso em que o padrão fit/transform do Kedro volta a
  fazer sentido para essa parte.
