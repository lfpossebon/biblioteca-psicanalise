"""Extração de HTML (páginas salvas, ex.: '*.pdf.html' do material do CEP)."""

from pathlib import Path

import lxml.html

from psilib.extract.base import Extraction, Segment


def extract(path: str | Path) -> Extraction:
    doc = lxml.html.fromstring(Path(path).read_bytes())
    for bad in doc.xpath("//script|//style|//noscript"):
        bad.drop_tree()
    text = "\n".join(t.strip() for t in doc.text_content().splitlines() if t.strip())
    return Extraction(segments=[Segment(text=text)] if text else [])
