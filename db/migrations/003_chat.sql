-- Chat da POC: conversas com o Claude sobre o acervo, com custo e feedback dos convidados.

-- Histórico completo enviado à API a cada turno (append-only: blocos de pensamento e de
-- ferramenta voltam exatamente como vieram).
create table chat_conversation (
    id          uuid primary key,
    invite      text not null,                 -- nome do convidado (do código de convite)
    messages    jsonb not null default '[]'::jsonb,
    -- Trechos que as ferramentas devolveram nesta conversa: só eles podem ser citados.
    seen        jsonb not null default '{}'::jsonb,
    created_at  timestamptz not null default now(),
    updated_at  timestamptz not null default now()
);

-- Um turno = uma pergunta do convidado e a resposta final. É a base da análise da POC.
create table chat_turn (
    id              bigserial primary key,
    conversation_id uuid not null references chat_conversation(id) on delete cascade,
    invite          text not null,
    question        text not null,
    answer          text,
    citations       jsonb not null default '[]'::jsonb,  -- trechos citados (chunk_id + citação)
    invalid_citations jsonb not null default '[]'::jsonb, -- marcadores sem trecho correspondente
    tool_calls      jsonb not null default '[]'::jsonb,  -- ferramentas usadas, com argumentos
    model           text,
    input_tokens    int not null default 0,
    output_tokens   int not null default 0,
    cache_read_tokens  int not null default 0,
    cache_write_tokens int not null default 0,
    cost_usd        numeric(10, 5) not null default 0,
    latency_ms      int,
    stop_reason     text,
    error           text,
    feedback        smallint,                    -- 1 (útil) | -1 (não útil)
    comment         text,
    created_at      timestamptz not null default now()
);
create index on chat_turn (invite, created_at);
create index on chat_turn (created_at);
