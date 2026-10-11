# ADR 0002 — Como medimos a presença de um conceito

Status: aceito · Data: 2026-10-11

## Decisão

- **Léxico explícito por língua** (`config/concepts.yaml`), com regex sobre texto normalizado
  (sem acentos; apóstrofos unificados). Um chunk é comparado só com os padrões da sua língua —
  "real" em inglês não conta como o Real.
- **Maiúsculas importam quando o conceito depende delas** (`cs: true`): *Outro/Autre/Otro*
  com artigo, *a Coisa/la Chose*.
- **Padrões conservadores**: preferimos perder ocorrências a contar ruído (ex.: "objeto a" não
  conta em "o objeto a ser analisado"; "não-todo" exige hífen).
- **Métrica**: ocorrências por 10 mil palavras, por documento × período. Sem normalizar, sessões
  longas parecem falar mais de tudo.
- **Chunks sem sobreposição**, para cada ocorrência ser contada uma única vez.
- **Coocorrência por PMI** (`log P(a,b) / P(a)P(b)` sobre trechos), não por contagem bruta —
  conceitos muito frequentes (sujeito, desejo) não dominam o mapa.

## Limites conhecidos

- Frequência é um indicador de *onde ler*, não de *o que o conceito significa*. A ferramenta
  aponta os trechos; a leitura continua sendo sua.
- Traduções diferem (gozo/goce/jouissance; fantasia/fantasma): o léxico cobre as variantes
  usuais, mas comparar FR (Staferla) com PT (traduções) deve ser feito com cuidado.
- Próximo passo natural: validar o léxico com amostras anotadas (precisão/recall por conceito)
  e complementar com detecção semântica (embeddings de definições).
