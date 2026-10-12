-- Figuras exibidas em cada resposta do chat (especificações com os dados, como o leitor as viu).
alter table chat_turn add column visuals jsonb not null default '[]'::jsonb;
