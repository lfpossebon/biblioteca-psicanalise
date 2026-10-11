"""Divisão de segmentos em chunks de ~N palavras, respeitando frases e fronteiras de sessão.

Sem sobreposição entre chunks: cada ocorrência de um conceito é contada uma única vez
(a linha do tempo depende disso). A continuidade para leitura vem dos chunks vizinhos.
"""

import re
from dataclasses import dataclass
from datetime import date

from psilib.text import word_count

_SENT_SPLIT = re.compile(r"(?<=[.!?»…:;])\s+(?=[A-ZÀ-ÝÉ«\"“—(\[0-9])")


@dataclass
class ChunkDraft:
    text: str
    n_words: int
    page_start: int | None
    page_end: int | None
    section_label: str | None
    session_date: date | None


def _unwrap(text: str) -> str:
    """Junta quebras de linha internas a parágrafos (PDF quebra por linha)."""
    text = re.sub(r"\n\s*\n", " ", text)              # parágrafos reais
    text = re.sub(r"(?<![.!?»:;])\n(?=[a-zà-ÿ0-9,(])", " ", text)  # quebra no meio da frase
    return text.replace(" ", "\n\n")


def _sentences(text: str, max_words: int) -> list[str]:
    out = []
    for para in re.split(r"\n+", _unwrap(text)):
        para = para.strip()
        if not para:
            continue
        for sent in _SENT_SPLIT.split(para):
            words = sent.split()
            # Frases gigantes (listas, OCR ruim) são cortadas à força.
            for i in range(0, len(words), max_words):
                out.append(" ".join(words[i : i + max_words]))
        out[-1] += "\n"
    return out


def chunk_segments(segments: list[dict], target_words: int = 350) -> list[ChunkDraft]:
    """`segments`: dicts ordenados com text, page_no, section_label, session_date."""
    chunks: list[ChunkDraft] = []
    buf: list[str] = []
    n = 0
    p0 = p1 = None
    key = None

    def emit():
        nonlocal buf, n, p0, p1
        text = " ".join(s.strip() if not s.endswith("\n") else s for s in buf).strip()
        text = re.sub(r" *\n *", "\n", text)
        if text:
            chunks.append(ChunkDraft(text, word_count(text), p0, p1, key[0] if key else None, key[1] if key else None))
        buf, n, p0, p1 = [], 0, None, None

    for seg in segments:
        seg_key = (seg.get("section_label"), seg.get("session_date"))
        if buf and seg_key != key:
            emit()
        key = seg_key
        for sent in _sentences(seg["text"], target_words):
            w = word_count(sent)
            if buf and n + w > target_words and n >= target_words // 2:
                emit()
            if p0 is None:
                p0 = seg.get("page_no")
            p1 = seg.get("page_no")
            buf.append(sent)
            n += w
    if buf:
        emit()
    return chunks
