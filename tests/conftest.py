"""Fixtures dos testes de integração e e2e.

Usam um banco descartável (psilib_test) no mesmo Postgres do projeto — o banco principal não é
tocado. Sem Postgres acessível, esses testes são pulados e os unitários rodam normalmente.
"""

from datetime import date
from urllib.parse import urlsplit, urlunsplit

import psycopg
import pytest

from psilib import config, db, embeddings, pipeline

TEST_DB = "psilib_test"

# Seis sessões do Seminário 10 com gozo, desejo e objeto a: suficiente para linha do tempo por
# sessão e para a coocorrência (que exige o par em pelo menos 5 trechos).
SESSION_TEXT = (
    "Nesta sessão retomamos a questão do gozo e do desejo. O desejo do sujeito se articula ao "
    "objeto a, causa do desejo, enquanto o gozo aparece como aquilo que excede o princípio do prazer. "
)
SESSIONS = [
    date(1962, 11, 14),
    date(1962, 11, 21),
    date(1962, 11, 28),
    date(1962, 12, 5),
    date(1963, 1, 9),
    date(1963, 1, 16),
]


@pytest.fixture(scope="session")
def test_db():
    base_url = config.Settings().database_url
    test_url = urlunsplit(urlsplit(base_url)._replace(path=f"/{TEST_DB}"))
    try:
        admin = psycopg.connect(base_url, autocommit=True, connect_timeout=3)
    except psycopg.OperationalError as e:
        pytest.skip(f"Postgres indisponível ({e.__class__.__name__}); suba com `make up`")
    with admin:
        admin.execute(f"drop database if exists {TEST_DB} with (force)")
        admin.execute(f"create database {TEST_DB}")

    mp = pytest.MonkeyPatch()
    mp.setenv("DATABASE_URL", test_url)
    mp.setenv("EMBEDDING_WARMUP", "false")
    mp.setenv("ANTHROPIC_API_KEY", "sk-ant-teste")  # a Claude API é falsa nos testes; só precisa existir
    config.settings.cache_clear()
    db._pool = None
    db.migrate()
    yield test_url

    db.pool().close()
    db._pool = None
    mp.undo()
    config.settings.cache_clear()
    with psycopg.connect(base_url, autocommit=True) as admin:
        admin.execute(f"drop database if exists {TEST_DB} with (force)")


@pytest.fixture(scope="session")
def seeded(test_db):
    """Um documento já extraído (camada bronze) passado por chunk → concepts → refresh."""
    with db.connect() as conn:
        author_id = conn.execute(
            "insert into author (name, line, in_scope) values ('Jacques Lacan', 'lacan', true) returning id"
        ).fetchone()["id"]
        work_id = conn.execute(
            """insert into work (slug, title, author_id, work_type, seminar_number, year_start, year_end)
               values ('seminario-10', 'A angústia', %s, 'seminario', '10', 1962, 1963) returning id""",
            (author_id,),
        ).fetchone()["id"]
        doc_id = conn.execute(
            """insert into document (source_id, uri, title, author_id, work_id, line, language, file_type, status)
               values ('staferla', 'teste://S10', 'S10 L''ANGOISSE', %s, %s, 'lacan', 'pt', 'docx', 'extracted')
               returning id""",
            (author_id, work_id),
        ).fetchone()["id"]
        for i, d in enumerate(SESSIONS):
            conn.execute(
                "insert into segment (document_id, ordinal, section_label, session_date, text) values (%s,%s,%s,%s,%s)",
                (doc_id, i, f"Sessão de {d:%d/%m/%Y}", d, SESSION_TEXT * 3),
            )
        conn.commit()

    stats = {"chunk": pipeline.chunk(), "concepts": pipeline.concepts(), "refresh": pipeline.refresh()}
    return {"document_id": doc_id, "stats": stats}


@pytest.fixture
def chat_env(seeded, monkeypatch):
    """Chat sobre o banco de teste, sem modelo de embeddings (busca só com texto + conceitos)."""

    def no_vectors(*_a, **_k):
        raise RuntimeError("sem modelo nos testes")

    monkeypatch.setattr(embeddings, "embed", no_vectors)  # busca segue só com texto + conceitos
    yield
    monkeypatch.undo()
    config.settings.cache_clear()
