"""Linha de comando: `psilib <etapa>` (dentro do container: `docker compose run --rm pipeline <etapa>`)."""

import json
import logging
from datetime import date

import typer

from psilib import pipeline
from psilib.db import connect
from psilib.db import migrate as run_migrations

app = typer.Typer(help="Biblioteca psicanalítica — pipeline de dados e consultas", no_args_is_help=True)
watch_app = typer.Typer(help="Monitor de publicações novas (OpenAlex)")
app.add_typer(watch_app, name="watch")


@app.callback()
def _setup(verbose: bool = typer.Option(False, "--verbose", "-v")):
    logging.basicConfig(
        level=logging.DEBUG if verbose else logging.INFO,
        format="%(asctime)s %(levelname)s %(name)s: %(message)s",
        datefmt="%H:%M:%S",
    )
    for noisy in ("httpx", "httpcore", "sentence_transformers"):
        logging.getLogger(noisy).setLevel(logging.WARNING)


def _print(obj) -> None:
    typer.echo(json.dumps(obj, ensure_ascii=False, indent=2, default=str))


@app.command()
def migrate():
    """Cria/atualiza o esquema do banco."""
    _print({"aplicadas": run_migrations()})


@app.command()
def inventory():
    """Varre o acervo local, classifica o escopo e gera catalog/catalogo.csv."""
    _print(pipeline.inventory())


@app.command()
def staferla(
    freud: bool = typer.Option(False, help="Inclui Freud (SE inglês + GW alemão)"),
    fonte: str = typer.Option("auto", help="auto | site | arquivo (Internet Archive)"),
):
    """Baixa/atualiza seminários e textos de Lacan do Staferla (só o que mudou).

    Com o site fora do ar, 'auto' usa as cópias do Internet Archive; quando o site voltar,
    a próxima execução troca essas cópias pelas versões atuais.
    """
    _print(pipeline.fetch_staferla(include_freud=freud, source=fonte))


@app.command()
def probe(limit: int = typer.Option(None)):
    """Lê o início dos documentos 'revisar' e sugere a linha teórica pelo conteúdo."""
    _print(pipeline.probe(limit))


@app.command()
def extract(limit: int = typer.Option(None), source: str = typer.Option(None)):
    """Extrai texto dos documentos no escopo (PDF/DOCX), com deduplicação por hash."""
    _print(pipeline.extract(limit, source))


@app.command()
def ocr(limit: int = typer.Option(None)):
    """OCR (tesseract) dos PDFs escaneados; depois rode extract + chunk de novo."""
    _print(pipeline.ocr(limit))


@app.command()
def chunk(limit: int = typer.Option(None)):
    """Divide o texto extraído em trechos datados."""
    _print(pipeline.chunk(limit))


@app.command()
def concepts():
    """Sincroniza config/concepts.yaml e recalcula as menções de conceitos."""
    _print(pipeline.concepts())


@app.command()
def embed(limit: int = typer.Option(None), batch: int = typer.Option(256)):
    """Gera embeddings para os trechos que ainda não têm (incremental)."""
    _print(pipeline.embed(limit, batch))


@app.command()
def refresh():
    """Recalcula as visões analíticas (linha do tempo, coocorrência)."""
    _print(pipeline.refresh())


@app.command()
def run(
    staferla_: bool = typer.Option(True, "--staferla/--no-staferla"),
    embed_: bool = typer.Option(True, "--embed/--no-embed"),
):
    """Pipeline completo, incremental: migrate → inventory → staferla → extract → chunk → concepts → embed → refresh."""
    out = {"migrate": run_migrations(), "inventory": pipeline.inventory()}
    if staferla_:
        out["staferla"] = pipeline.fetch_staferla()
    out["extract"] = pipeline.extract()
    out["chunk"] = pipeline.chunk()
    out["concepts"] = pipeline.concepts()
    if embed_:
        out["embed"] = pipeline.embed()
    out["refresh"] = pipeline.refresh()
    _print(out)


# ------------------------------------------------------------------ consultas rápidas


@app.command()
def search(query: str, k: int = 5, linha: list[str] = typer.Option(None), modo: str = "hybrid"):
    """Busca de teste no terminal."""
    from psilib.service import Filters
    from psilib.service import search as do_search

    res = do_search(query, k, Filters(lines=linha or None), mode=modo, max_chars=400)
    for r in res["resultados"]:
        typer.echo(f"\n[{r['chunk_id']}] {r['citacao']}  (score {r['score']})\n{r['texto']}")
    typer.echo(
        f"\nconceitos reconhecidos: {res['conceitos_reconhecidos']} | estratégias: {res['estrategias']}"
    )


@app.command()
def timeline(concept: str, granularidade: str = "obra", linha: list[str] = typer.Option(None)):
    """Linha do tempo de um conceito no terminal."""
    from psilib.service import Filters
    from psilib.service import timeline as do_timeline

    _print(do_timeline(concept, granularidade, Filters(lines=linha or None)))


@app.command()
def stats():
    """Panorama do acervo."""
    from psilib.service import stats as do_stats

    _print(do_stats())


# ------------------------------------------------------------------ chat da POC


@app.command()
def convites(nomes: list[str]):
    """Gera códigos de convite para o chat. Cole a linha CHAT_INVITES no .env (ou no Railway)."""
    import secrets

    from psilib.chat import invites
    from psilib.config import settings

    current = settings().chat_invites.strip()
    known = set(invites().values())
    new = []
    for nome in nomes:
        if nome in known:
            typer.echo(f"já existe: {nome}")
            continue
        code = secrets.token_urlsafe(6).replace("-", "x").replace("_", "y")
        new.append(f"{nome}:{code}")
        typer.echo(f"{nome}: {code}")
    if new:
        typer.echo("\nCHAT_INVITES=" + ",".join(filter(None, [current, *new])))


@app.command("chat-relatorio")
def chat_relatorio(dias: int = 30):
    """Uso da POC por convidado: perguntas, custo, avaliações e comentários."""
    with connect() as conn:
        rows = conn.execute(
            """select invite as convidado, count(*) as perguntas,
                      count(*) filter (where feedback = 1) as uteis,
                      count(*) filter (where feedback = -1) as nao_uteis,
                      count(*) filter (where error is not null) as erros,
                      round(sum(cost_usd), 2) as custo_usd,
                      round(avg(latency_ms) / 1000.0, 1) as seg_medio
               from chat_turn where created_at >= now() - make_interval(days => %s)
               group by 1 order by 2 desc""",
            (dias,),
        ).fetchall()
        comments = conn.execute(
            """select created_at::date as dia, invite, feedback, question, comment from chat_turn
               where comment is not null and created_at >= now() - make_interval(days => %s)
               order by created_at desc limit 30""",
            (dias,),
        ).fetchall()
    _print({"por_convidado": rows, "comentarios": comments})


# ------------------------------------------------------------------ monitor


@watch_app.command("scan")
def watch_scan(since: str = typer.Option(None, help="AAAA-MM-DD; padrão: última varredura - 7 dias")):
    """Procura publicações novas no OpenAlex e registra como pendentes."""
    from psilib.sources import openalex

    with connect() as conn:
        start = date.fromisoformat(since) if since else openalex.default_since(conn)
    works = openalex.search(start)
    new = 0
    with connect() as conn:
        for w in works:
            row = openalex.to_row(w)
            res = conn.execute(
                """insert into watch_item (source_id, external_id, title, authors, year, language, url,
                       oa_pdf_url, abstract, payload)
                   values ('openalex', %(external_id)s, %(title)s, %(authors)s, %(year)s, %(language)s, %(url)s,
                       %(oa_pdf_url)s, %(abstract)s, %(payload)s)
                   on conflict (source_id, external_id) do nothing returning id""",
                row,
            ).fetchone()
            new += bool(res)
        conn.commit()
    _print({"desde": start, "encontrados": len(works), "novos": new})


@watch_app.command("list")
def watch_list(decision: str = "pending", limit: int = 30, lingua: str = typer.Option(None)):
    """Lista itens do monitor (pending | accepted | rejected)."""
    with connect() as conn:
        rows = conn.execute(
            """select id, year, language, title, authors, oa_pdf_url is not null as pdf_aberto
               from watch_item where decision=%s and (%s::text is null or language=%s)
               order by year desc nulls last, id desc limit %s""",
            (decision, lingua, lingua, limit),
        ).fetchall()
    for r in rows:
        typer.echo(
            f"{r['id']:>6}  {r['year']}  {r['language'] or '--'}  {'PDF' if r['pdf_aberto'] else '   '}  "
            f"{(r['title'] or '')[:90]}  — {(r['authors'] or '')[:50]}"
        )


@watch_app.command("accept")
def watch_accept(ids: list[int], linha: str = "lacaniano"):
    """Aceita itens: baixa o PDF aberto e coloca no acervo (status in_scope)."""
    import httpx

    from psilib.catalog import DocSpec, upsert_document
    from psilib.config import settings

    out = settings().data_dir / "raw" / "openalex"
    out.mkdir(parents=True, exist_ok=True)
    with connect() as conn:
        for item_id in ids:
            it = conn.execute("select * from watch_item where id=%s", (item_id,)).fetchone()
            if not it or not it["oa_pdf_url"]:
                typer.echo(f"{item_id}: sem PDF de acesso aberto — mantido como pendente")
                continue
            path = out / f"{it['external_id']}.pdf"
            r = httpx.get(
                it["oa_pdf_url"],
                follow_redirects=True,
                timeout=120,
                headers={"User-Agent": settings().http_user_agent},
            )
            if r.status_code != 200 or not r.content.startswith(b"%PDF"):
                typer.echo(f"{item_id}: download falhou ({r.status_code})")
                continue
            path.write_bytes(r.content)
            first_author = (it["authors"] or "").split(";")[0].strip() or None
            upsert_document(
                conn,
                DocSpec(
                    source_id="openalex",
                    uri=it["url"],
                    title=it["title"] or it["external_id"],
                    local_path=str(path),
                    author=first_author,
                    line=linha,
                    in_scope=True,
                    scope_reason="aceito do monitor OpenAlex",
                    language=it["language"],
                    year_publication=it["year"],
                    file_type="pdf",
                    bytes=len(r.content),
                    meta={"openalex_id": it["external_id"]},
                ),
            )
            conn.execute("update watch_item set decision='accepted' where id=%s", (item_id,))
            conn.commit()
            typer.echo(f"{item_id}: aceito → {path.name}")


@watch_app.command("reject")
def watch_reject(ids: list[int]):
    """Rejeita itens do monitor."""
    with connect() as conn:
        conn.execute("update watch_item set decision='rejected' where id = any(%s)", (ids,))
        conn.commit()


if __name__ == "__main__":
    app()
