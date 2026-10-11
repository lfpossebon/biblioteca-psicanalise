-- Biblioteca psicanalítica — esquema base
-- Camadas: raw (arquivos imutáveis em /data/raw e /corpus) → bronze (segment) → silver (chunk)
--          → gold (embeddings, menções de conceitos, visões de linha do tempo)

create extension if not exists vector;
create extension if not exists unaccent;
create extension if not exists pg_trgm;

-- unaccent() não é IMMUTABLE; o wrapper permite usá-lo em colunas geradas e índices.
create or replace function f_unaccent(text) returns text
    language sql immutable parallel safe strict
    as $$ select public.unaccent('public.unaccent'::regdictionary, $1) $$;

-- ---------------------------------------------------------------- catálogo

create table source (
    id          text primary key,              -- local | staferla | openalex
    name        text not null,
    kind        text not null,                 -- filesystem | website | api
    base_url    text
);

create table author (
    id          serial primary key,
    name        text not null unique,
    line        text not null,                 -- freud | lacan | lacaniano | escola_argentina | ...
    in_scope    boolean not null
);

-- Obra abstrata: "Séminaire XI", "A instância da letra"... Várias edições/traduções apontam para ela.
create table work (
    id              serial primary key,
    slug            text not null unique,
    title           text not null,
    author_id       int references author(id),
    work_type       text not null,             -- seminario | escrito | livro | artigo | conferencia | obras_completas | aula
    seminar_number  text,                      -- '11', '19b' ...
    year_start      int,                       -- ano em que o texto foi produzido/proferido
    year_end        int
);

-- Manifestação concreta: um arquivo local, um .docx do Staferla, um artigo OpenAlex.
create table document (
    id                  serial primary key,
    source_id           text not null references source(id),
    uri                 text not null unique,  -- caminho relativo ao corpus ou URL
    local_path          text,                  -- onde o arquivo está dentro do container
    title               text not null,
    author_id           int references author(id),
    work_id             int references work(id),
    line                text,                  -- linha teórica (cópia do autor ou override)
    language            text,                  -- pt | es | fr | en | de
    year_publication    int,
    file_type           text,                  -- pdf | docx | html
    bytes               bigint,
    sha256              text,
    duplicate_of        int references document(id),
    status              text not null default 'discovered',
        -- discovered | excluded | in_scope | extracted | chunked | failed
    scope_reason        text,
    ocr_needed          boolean,
    n_pages             int,
    n_words             int,
    meta                jsonb not null default '{}'::jsonb,
    error               text,
    created_at          timestamptz not null default now(),
    updated_at          timestamptz not null default now()
);
create index on document (status);
create index on document (sha256);
create index on document (work_id);

-- ---------------------------------------------------------------- bronze: texto extraído

-- Unidade de extração: uma página de PDF ou uma sessão de seminário (docx do Staferla).
create table segment (
    id              bigserial primary key,
    document_id     int not null references document(id) on delete cascade,
    ordinal         int not null,
    page_no         int,
    section_label   text,                      -- "Leçon 3", "Capítulo II"
    session_date    date,                      -- data da sessão do seminário, quando conhecida
    text            text not null,
    unique (document_id, ordinal)
);

-- ---------------------------------------------------------------- silver: chunks

create table chunk (
    id              bigserial primary key,
    document_id     int not null references document(id) on delete cascade,
    ordinal         int not null,
    text            text not null,
    n_words         int not null,
    page_start      int,
    page_end        int,
    section_label   text,
    session_date    date,
    -- Data "do pensamento": sessão do seminário > ano da obra > ano de publicação.
    period_date     date,
    period_precision text,                     -- day | year
    language        text,
    tsv             tsvector generated always as (to_tsvector('simple', f_unaccent(lower(text)))) stored,
    unique (document_id, ordinal)
);
create index on chunk using gin (tsv);
create index on chunk (period_date);

-- Um vetor por (chunk, modelo): trocar de modelo de embedding não exige migração.
-- Índices HNSW são criados por modelo com a dimensão certa (ver psilib.embeddings.ensure_index).
create table chunk_embedding (
    chunk_id    bigint not null references chunk(id) on delete cascade,
    model       text not null,
    embedding   vector not null,
    primary key (chunk_id, model)
);

-- ---------------------------------------------------------------- gold: conceitos

create table concept (
    id          serial primary key,
    slug        text not null unique,
    label       text not null,                 -- rótulo em português
    family      text,                          -- registro, estrutura, clínica, topologia...
    description text
);

create table concept_term (
    concept_id      int not null references concept(id) on delete cascade,
    language        text not null,
    pattern         text not null,             -- regex aplicada ao texto sem acentos
    case_sensitive  boolean not null default false,
    primary key (concept_id, language, pattern)
);

create table concept_mention (
    concept_id  int not null references concept(id) on delete cascade,
    chunk_id    bigint not null references chunk(id) on delete cascade,
    n           int not null,
    primary key (concept_id, chunk_id)
);
create index on concept_mention (chunk_id);

-- ---------------------------------------------------------------- linhagem

create table pipeline_run (
    id          bigserial primary key,
    step        text not null,
    params      jsonb not null default '{}'::jsonb,
    stats       jsonb not null default '{}'::jsonb,
    status      text not null default 'running', -- running | ok | failed
    error       text,
    started_at  timestamptz not null default now(),
    finished_at timestamptz
);

-- Itens descobertos por monitores (OpenAlex etc.) aguardando curadoria.
create table watch_item (
    id          bigserial primary key,
    source_id   text not null references source(id),
    external_id text not null,
    title       text,
    authors     text,
    year        int,
    language    text,
    url         text,
    oa_pdf_url  text,
    abstract    text,
    payload     jsonb not null default '{}'::jsonb,
    decision    text not null default 'pending',  -- pending | accepted | rejected
    first_seen  timestamptz not null default now(),
    unique (source_id, external_id)
);

insert into source (id, name, kind, base_url) values
    ('local',    'Acervo local',                          'filesystem', null),
    ('staferla', 'STAFERLA — séminaires de Lacan',        'website',    'http://staferla.free.fr/'),
    ('openalex', 'OpenAlex — literatura acadêmica aberta', 'api',       'https://api.openalex.org/');
