# ADR 0001 — Arquitetura da biblioteca

Status: aceito · Data: 2026-10-11

## Contexto

O material de estudo (seminários de Lacan, Freud, comentadores, escola argentina) está espalhado
em PDFs pessoais, no Staferla (transcrições francesas dos seminários) e em periódicos acadêmicos.
Objetivos: (1) consultar tudo num lugar, (2) explorar conceitos de forma simples com IA, com
citação verificável, (3) medir a evolução de cada conceito no tempo.

## Decisões

1. **Um único banco: PostgreSQL 16 + pgvector.** Metadados, texto, busca textual (tsvector),
   vetores e as visões analíticas ficam no mesmo lugar. Linha do tempo e coocorrência são SQL.
   Evita sincronizar um vector DB separado; escala tranquilamente a alguns milhões de trechos.

2. **Camadas de dados (medalhão).**
   - *raw*: arquivos originais, imutáveis (`/corpus` somente leitura; `data/raw/` para downloads).
   - *bronze*: `segment` — texto extraído por página (PDF) ou por sessão (DOCX do Staferla).
   - *silver*: `chunk` — trechos de ~350 palavras, com data, língua e páginas.
   - *gold*: `concept_mention`, `chunk_embedding`, `mv_concept_timeline`, `mv_concept_cooccurrence`.
   Cada camada pode ser reconstruída a partir da anterior.

3. **Pipeline idempotente e incremental.** Cada etapa processa só o que falta (`status` do
   documento, hash SHA-256, ETag do Staferla). Toda execução é registrada em `pipeline_run`.

4. **Escopo curado e auditável.** Regras em `config/scope.yaml` classificam todo arquivo
   (inclusive os excluídos — Klein, Winnicott, Ferenczi...). Decisões manuais em
   `catalog/overrides.csv`, versionado. A sondagem de conteúdo (`probe`) sugere a linha dos
   arquivos sem regra; só aplica sozinha quando há marcador inequívoco (Edição Standard de Freud).

5. **Staferla como fonte canônica das sessões.** Os DOCX do Staferla marcam cada sessão com um
   bookmark e a data — é o que permite a linha do tempo sessão a sessão. As traduções em PDF
   entram com precisão de ano (as edições brasileiras datam a lição no fim do capítulo, o que
   torna a detecção ambígua; preferimos não inventar precisão).
   Com o site fora do ar (out/2026), a coleta usa a captura mais recente de cada arquivo no
   Internet Archive, mantendo o endereço do Staferla como `uri`; a coleta seguinte, com o site
   no ar, substitui as cópias e reprocessa só o que mudou.

6. **Data do pensamento ≠ data da edição.** `period_date` é a data da sessão, senão o ano da obra
   (se a obra cobre ≤ 2 anos), senão o ano de publicação (comentadores). Obras completas que
   cobrem décadas ficam sem data até serem segmentadas por texto.

7. **Busca híbrida com RRF.** Três rankings fundidos por Reciprocal Rank Fusion: texto (FTS
   sem acentos), semântica (BGE-M3 multilíngue: pergunta em português encontra trecho em
   francês) e conceitos (léxico multilíngue). Sem pesos mágicos para calibrar.

8. **IA consome via MCP.** O mesmo processo serve REST (`/docs`) e MCP (`/mcp`); o Claude
   chama `buscar`, `ler_trecho`, `linha_do_tempo`, `conceitos_relacionados`. Toda resposta traz
   citação (autor, obra, sessão, página, língua, fonte). Um processo só carrega um modelo.

9. **Tudo em Docker, dependências travadas.** `uv.lock` + imagem única (pipeline/API/MCP) +
   Postgres com versão fixa. Torch CPU-only para não baixar CUDA.

## Consequências

- Rodar de novo é barato; trocar o modelo de embedding não exige migração (vetores por modelo,
  índice HNSW parcial por modelo).
- A qualidade da linha do tempo depende do léxico (`config/concepts.yaml`) — ver ADR 0002.
- PDFs escaneados precisam de OCR (lento); ficam marcados com `ocr_needed` até `make ocr`.
- Direitos autorais: o acervo é de uso pessoal/estudo. Não publicar trechos longos de obras
  protegidas; o monitor de novidades só baixa PDFs de acesso aberto.
