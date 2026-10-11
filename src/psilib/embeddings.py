"""Embeddings multilíngues (padrão: BAAI/bge-m3 local; opcional: Voyage via API).

Cada vetor é guardado com o nome do modelo, e o índice HNSW é parcial por modelo — dá para
testar outro modelo lado a lado sem migrar nada.
"""

import logging
import re
import threading

import httpx

from psilib.config import settings

log = logging.getLogger(__name__)
_model = None
_model_lock = threading.Lock()


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


def _local_model():
    """Modelo carregado uma vez por processo. Double-checked locking (Deck 03 da disciplina):
    REST e MCP atendem em várias threads e, sem o lock, duas buscas simultâneas logo após o
    start carregariam o modelo (~2 GB) duas vezes."""
    global _model  # noqa: PLW0603 — singleton por processo, protegido pelo lock
    if _model is None:
        with _model_lock:
            if _model is None:
                from sentence_transformers import SentenceTransformer

                s, device = settings(), _device()
                log.info(
                    "carregando modelo de embeddings %s@%s em %s",
                    s.embedding_model,
                    (s.embedding_revision or "main")[:7],
                    device,
                )
                model = SentenceTransformer(
                    s.embedding_model, revision=s.embedding_revision or None, device=device
                )
                model.max_seq_length = min(model.max_seq_length or 1024, 1024)  # chunks têm ~500 tokens
                _model = model
    return _model


def is_ready() -> bool:
    """Pronto para busca semântica: modelo local carregado (ou provedor remoto, que não carrega nada)."""
    return settings().embedding_provider != "local" or _model is not None


def warmup() -> None:
    """Carrega o modelo antes da primeira busca (chamado em thread daemon no startup da API)."""
    if settings().embedding_provider != "local":
        return
    try:
        _local_model()
        log.info("modelo de embeddings pronto")
    except Exception:
        log.exception("falha ao carregar o modelo de embeddings; a busca segue só com texto")


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
    vecs = _local_model().encode(
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
