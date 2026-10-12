-- Análise em Python no chat: código executado, saída e arquivos gerados (gráficos PNG, CSV) por
-- resposta, e o container de execução da conversa (o estado do Python persiste entre perguntas).
alter table chat_turn add column python jsonb not null default '[]'::jsonb;
alter table chat_conversation add column container_id text;
