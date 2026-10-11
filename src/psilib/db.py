"""Acesso ao Postgres: pool de conexões, migrações versionadas e registro de execuções."""

import json
import logging
from contextlib import contextmanager

from pgvector.psycopg import register_vector
from psycopg import Connection
from psycopg.rows import dict_row
from psycopg_pool import ConnectionPool

from psilib.config import settings

log = logging.getLogger(__name__)
_pool: ConnectionPool | None = None


def _configure(conn: Connection) -> None:
    try:
        register_vector(conn)
    except Exception:  # extensão ainda não criada (antes da 1ª migração)
        pass


def pool() -> ConnectionPool:
    global _pool
    if _pool is None:
        _pool = ConnectionPool(
            settings().database_url,
            min_size=1,
            max_size=8,
            kwargs={"row_factory": dict_row, "autocommit": False},
            configure=_configure,
            open=True,
        )
    return _pool


@contextmanager
def connect():
    with pool().connection() as conn:
        yield conn


def migrate() -> list[str]:
    """Aplica, em ordem, os arquivos db/migrations/NNN_*.sql ainda não aplicados."""
    applied: list[str] = []
    with connect() as conn:
        conn.execute(
            "create table if not exists schema_migration (name text primary key, applied_at timestamptz default now())"
        )
        done = {r["name"] for r in conn.execute("select name from schema_migration")}
        for path in sorted(settings().migrations_dir.glob("*.sql")):
            if path.name in done:
                continue
            log.info("aplicando migração %s", path.name)
            conn.execute(path.read_text(encoding="utf-8"))
            conn.execute("insert into schema_migration (name) values (%s)", (path.name,))
            applied.append(path.name)
        conn.commit()
    return applied


@contextmanager
def run_step(step: str, **params):
    """Registra cada etapa do pipeline em pipeline_run (linhagem e auditoria)."""
    stats: dict = {}
    with connect() as conn:
        # Execução anterior da mesma etapa que morreu sem finalizar (container parado, Ctrl+C...)
        conn.execute(
            "update pipeline_run set status='interrupted', finished_at=now() where step=%s and status='running'",
            (step,),
        )
        run_id = conn.execute(
            "insert into pipeline_run (step, params) values (%s, %s) returning id",
            (step, json.dumps(params, default=str)),
        ).fetchone()["id"]
        conn.commit()
    try:
        yield stats
    except Exception as e:
        with connect() as conn:
            conn.execute(
                "update pipeline_run set status='failed', error=%s, stats=%s, finished_at=now() where id=%s",
                (repr(e), json.dumps(stats, default=str), run_id),
            )
            conn.commit()
        raise
    with connect() as conn:
        conn.execute(
            "update pipeline_run set status='ok', stats=%s, finished_at=now() where id=%s",
            (json.dumps(stats, default=str), run_id),
        )
        conn.commit()
