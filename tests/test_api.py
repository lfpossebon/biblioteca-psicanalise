"""E2E: a API REST via TestClient (sem subir servidor), sobre o banco de teste já populado."""

import pytest
from fastapi.testclient import TestClient

pytestmark = pytest.mark.integration


@pytest.fixture(scope="module")
def client(seeded):
    from psilib.api import app

    with TestClient(app) as c:
        yield c


def test_health(client):
    assert client.get("/health").json() == {"ok": True}


def test_ready_waits_for_model(client):
    # Warmup desligado nos testes: banco ok, modelo não carregado → 503 (não pronto).
    r = client.get("/ready")
    assert r.status_code == 503
    assert r.json() == {"pronto": False, "banco": True, "embeddings": False}


def test_busca_text(client):
    r = client.get("/busca", params={"q": "objeto a", "modo": "text", "k": 2})
    assert r.status_code == 200
    hits = r.json()["resultados"]
    assert len(hits) == 2
    assert hits[0]["autor"] == "Jacques Lacan"


def test_busca_rejects_invalid_mode(client):
    assert client.get("/busca", params={"q": "gozo", "modo": "xyz"}).status_code == 422


def test_trecho_with_context(client):
    chunk_id = client.get("/busca", params={"q": "gozo", "modo": "text", "k": 1}).json()["resultados"][0][
        "chunk_id"
    ]
    body = client.get(f"/trechos/{chunk_id}", params={"contexto": 1}).json()
    assert chunk_id in [t["chunk_id"] for t in body["trechos"]]


def test_linha_do_tempo(client):
    r = client.get("/conceitos/gozo/linha-do-tempo", params={"granularidade": "sessao", "min_palavras": 0})
    assert r.status_code == 200
    assert len(r.json()["serie"]) == 6
