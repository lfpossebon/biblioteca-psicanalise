"""Embeddings multilíngues (padrão: BAAI/bge-m3 local; opcional: Voyage via API).

Cada vetor é guardado com o nome do modelo, e o índice HNSW é parcial por modelo — dá para
testar outro modelo lado a lado sem migrar nada.
"""

import logging
import re
from functools import lru_cache

import httpx

from psilib.config import settings

log = logging.getLogger(__name__)


def _prefixes(model: str) -> tuple[str, str]:
    # Família E5 exige prefixos; BGE-M3 e Voyage não.
    if "e5" in model.lower():
        return "query: ", "passage: "
    return "", ""


def _device() -> str:
    wanted = settings().embedding_device
    if wanted != "auto":
        return wanted
    import torch

    if torch.cuda.is_available():
        return "cuda"
    if torch.backends.mps.is_available():  # Mac fora do Docker: GPU Apple, bem mais rápido
        return "mps"
    return "cpu"


@lru_cache
def _local_model(name: str):
    from sentence_transformers import SentenceTransformer

    device = _device()
    log.info("carregando modelo de embeddings %s em %s (primeira vez baixa ~2 GB)", name, device)
    model = SentenceTransformer(name, device=device)
    model.max_seq_length = min(model.max_seq_length or 1024, 1024)  # chunks têm ~500 tokens; limita a RAM
    return model


def _voyage(texts: list[str], input_type: str) -> list[list[float]]:
    s = settings()
    r = httpx.post(
        "https://api.voyageai.com/v1/embeddings",
        headers={"Authorization": f"Bearer {s.voyage_api_key}"},
        json={"input": texts, "model": s.embedding_model, "input_type": input_type},
        timeout=120,
    )
    r.raise_for_status()
    return [d["embedding"] for d in r.json()["data"]]


def embed(texts: list[str], kind: str = "passage") -> list[list[float]]:
    s = settings()
    if s.embedding_provider == "voyage":
        return _voyage(texts, "query" if kind == "query" else "document")
    q, p = _prefixes(s.embedding_model)
    prefixed = [(q if kind == "query" else p) + t for t in texts]
    vecs = _local_model(s.embedding_model).encode(
        prefixed, batch_size=s.embedding_batch, normalize_embeddings=True, show_progress_bar=False
    )
    return vecs.tolist()


def index_name(model: str) -> str:
    return "chunk_embedding_hnsw_" + re.sub(r"[^a-z0-9]+", "_", model.lower()).strip("_")


def ensure_index(conn) -> None:
    s = settings()
    conn.execute(
        f"""create index if not exists {index_name(s.embedding_model)}
            on chunk_embedding using hnsw ((embedding::vector({s.embedding_dim})) vector_cosine_ops)
            where model = '{s.embedding_model}'"""
    )
