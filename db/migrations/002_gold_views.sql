-- Camada gold: visões analíticas para linha do tempo e redes de conceitos.
-- Atualizar com:  psilib refresh   (REFRESH MATERIALIZED VIEW ...)

-- Chunk com todo o contexto bibliográfico necessário para citar.
create or replace view v_chunk_context as
select ch.id                as chunk_id,
       ch.document_id,
       d.title              as doc_title,
       d.uri,
       d.source_id,
       coalesce(ch.language, d.language) as language,
       d.line,
       a.name               as author,
       w.slug               as work_slug,
       w.title              as work_title,
       w.work_type,
       w.seminar_number,
       ch.ordinal,
       ch.section_label,
       ch.session_date,
       ch.period_date,
       ch.period_precision,
       ch.page_start,
       ch.page_end,
       ch.n_words,
       ch.text
from chunk ch
join document d   on d.id = ch.document_id
left join author a on a.id = d.author_id
left join work w   on w.id = d.work_id
where d.duplicate_of is null;

-- Frequência de cada conceito por documento × período, normalizada por 10 mil palavras.
-- Normalizar é essencial: sem isso, sessões longas parecem "falar mais" de tudo.
create materialized view mv_concept_timeline as
with words as (
    select document_id, period_date, period_precision, sum(n_words) as words
    from chunk
    group by 1, 2, 3
), mentions as (
    select m.concept_id, ch.document_id, ch.period_date,
           sum(m.n) as n, count(*) as n_chunks
    from concept_mention m
    join chunk ch on ch.id = m.chunk_id
    group by 1, 2, 3
)
select c.id                 as concept_id,
       c.slug               as concept,
       d.id                 as document_id,
       d.source_id,
       d.language,
       d.line,
       a.name               as author,
       w.slug               as work_slug,
       w.title              as work_title,
       w.seminar_number,
       wd.period_date,
       wd.period_precision,
       wd.words,
       coalesce(mn.n, 0)        as mentions,
       coalesce(mn.n_chunks, 0) as chunks_with_mention,
       round(10000.0 * coalesce(mn.n, 0) / nullif(wd.words, 0), 3) as per_10k
from words wd
cross join concept c
join document d    on d.id = wd.document_id and d.duplicate_of is null
left join author a on a.id = d.author_id
left join work w   on w.id = d.work_id
left join mentions mn
       on mn.concept_id = c.id
      and mn.document_id = wd.document_id
      and mn.period_date is not distinct from wd.period_date;

create index on mv_concept_timeline (concept, period_date);
create index on mv_concept_timeline (author);

-- Coocorrência de conceitos no mesmo chunk (base para grafo de conceitos e PMI).
create materialized view mv_concept_cooccurrence as
select a.concept_id as c1,
       b.concept_id as c2,
       count(*)     as n_chunks
from concept_mention a
join concept_mention b on a.chunk_id = b.chunk_id and a.concept_id < b.concept_id
join chunk ch          on ch.id = a.chunk_id
join document d        on d.id = ch.document_id and d.duplicate_of is null
group by 1, 2;

create index on mv_concept_cooccurrence (c1);
create index on mv_concept_cooccurrence (c2);
