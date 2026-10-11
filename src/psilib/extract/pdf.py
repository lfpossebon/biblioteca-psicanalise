"""Extração de PDF página a página (PyMuPDF), com limpeza de cabeçalhos/rodapés e OCR opcional."""

import logging
import re
import shutil
import subprocess
from collections import Counter
from pathlib import Path

import pymupdf

from psilib.config import settings
from psilib.extract.base import Extraction, Segment
from psilib.text import normalize

log = logging.getLogger(__name__)


def _edge_lines(text: str, n: int = 2) -> list[str]:
    lines = [ln.strip() for ln in text.splitlines() if ln.strip()]
    return lines[:n] + lines[-n:]


def _key(line: str) -> str:
    # Números de página variam; o resto do cabeçalho se repete.
    return re.sub(r"\d+", "#", normalize(line))


def clean_pages(pages: list[str]) -> list[str]:
    """Remove cabeçalhos/rodapés repetidos (>30% das páginas), números de página e hifenização."""
    counts = Counter(_key(ln) for p in pages for ln in set(_edge_lines(p)))
    threshold = max(3, int(0.3 * len(pages)))
    repeated = {k for k, c in counts.items() if c >= threshold and len(k) < 120}
    out = []
    for p in pages:
        lines = p.splitlines()
        kept = []
        for i, ln in enumerate(lines):
            s = ln.strip()
            edge = i < 3 or i >= len(lines) - 3
            if edge and (_key(s) in repeated or re.fullmatch(r"[\divxlcIVXLC\-–—. ]{1,8}", s or "x")):
                continue
            kept.append(ln)
        text = "\n".join(kept)
        text = re.sub(r"(\w)[-­]\n(\w)", r"\1\2", text)          # sig-\nnificante → significante
        text = re.sub(r"[ \t]+", " ", text)
        text = re.sub(r"\n{3,}", "\n\n", text)
        out.append(text.strip())
    return out


def extract(path: str | Path) -> Extraction:
    min_chars = settings().min_chars_per_page
    with pymupdf.open(path) as doc:
        raw = [page.get_text("text", sort=True) for page in doc]
        n_pages = doc.page_count
    pages = clean_pages(raw)
    weak = sum(1 for p in pages if len(p) < min_chars)
    ocr_needed = n_pages > 0 and weak / n_pages > 0.3
    segments = [Segment(text=t, page_no=i + 1) for i, t in enumerate(pages) if t]
    return Extraction(segments=segments, n_pages=n_pages, ocr_needed=ocr_needed, meta={"weak_pages": weak})


def ocr(path: str | Path, sha256: str) -> Path | None:
    """Gera uma cópia com camada de texto (ocrmypdf + tesseract) em data/derived/ocr/."""
    if not shutil.which("ocrmypdf"):
        log.warning("ocrmypdf não instalado; rode dentro do container 'pipeline'")
        return None
    out = settings().data_dir / "derived" / "ocr" / f"{sha256}.pdf"
    if out.exists():
        return out
    out.parent.mkdir(parents=True, exist_ok=True)
    cmd = [
        "ocrmypdf", "--skip-text", "--jobs", "4", "--output-type", "pdf", "--optimize", "0",
        "-l", settings().ocr_languages, str(path), str(out),
    ]
    log.info("OCR: %s", Path(path).name)
    proc = subprocess.run(cmd, capture_output=True, text=True)
    if proc.returncode not in (0, 6):  # 6 = já tinha texto
        log.error("OCR falhou (%s): %s", proc.returncode, proc.stderr[-500:])
        return None
    return out
