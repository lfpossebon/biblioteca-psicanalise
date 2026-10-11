"""Extração de DOCX direto do XML (sem dependência de Word).

Para os seminários do Staferla, cada sessão começa num parágrafo com a data
("15 janvier 1964") marcado por um bookmark que o sumário referencia. Usamos esses
bookmarks — e, na falta deles, o padrão de data — para segmentar por sessão.
"""

import re
import zipfile
from pathlib import Path

from lxml import etree

from psilib.extract.base import Extraction, Segment
from psilib.text import normalize, parse_date, session_heading_date, word_count

W = "{http://schemas.openxmlformats.org/wordprocessingml/2006/main}"
MAX_SEGMENT_WORDS = 3000


def _para_text(p) -> str:
    parts = []
    for el in p.iter(f"{W}t", f"{W}tab", f"{W}br", f"{W}cr"):
        if el.tag == f"{W}t":
            parts.append(el.text or "")
        else:
            parts.append(" " if el.tag == f"{W}tab" else "\n")
    return "".join(parts)


def _paragraphs(path: Path):
    with zipfile.ZipFile(path) as z:
        root = etree.fromstring(z.read("word/document.xml"))
    body = root.find(f"{W}body")
    anchors = {h.get(f"{W}anchor") for h in root.iter(f"{W}hyperlink") if h.get(f"{W}anchor")}
    for p in body.iter(f"{W}p"):
        style_el = p.find(f"{W}pPr/{W}pStyle")
        style = style_el.get(f"{W}val") if style_el is not None else ""
        marks = {b.get(f"{W}name") for b in p.iter(f"{W}bookmarkStart")}
        yield _para_text(p), style, bool(marks & anchors - {"Table", "_GoBack"})


def _anchored_date(text: str):
    """Cabeçalho de sessão marcado por bookmark, mas com acréscimos: "VIDEO\n02 Décembre 1964",
    "Jeudi 4 Novembre 1971 « Entretiens de Sainte-Anne »". Linhas do sumário ("Leçon 3 ...") não contam."""
    head = normalize(text).strip()
    if len(head) > 200 or re.match(r"^(lecon|licao|clase)\s+\d", head):
        return None
    return parse_date(head[:70])


def extract(path: str | Path) -> Extraction:
    path = Path(path)
    paras = list(_paragraphs(path))
    has_anchored_dates = any(anchored and (session_heading_date(t) or _anchored_date(t)) for t, _, anchored in paras)

    segments: list[Segment] = []
    buf: list[str] = []
    label: str | None = None
    session = None
    n_sessions = 0

    def flush():
        text = "\n".join(x for x in buf if x.strip()).strip()
        if text:
            segments.append(Segment(text=text, section_label=label, session_date=session))
        buf.clear()

    for text, style, anchored in paras:
        d = session_heading_date(text)
        if d is None and anchored:
            d = _anchored_date(text)
        is_session = d is not None and (anchored or not has_anchored_dates)
        if is_session:
            flush()
            n_sessions += 1
            session, label = d, f"Sessão de {d.strftime('%d/%m/%Y')}"
            continue
        if anchored and re.match(r"^\s*(postface|preface|annexe|appendice)", text, re.I):
            flush()
            session, label = parse_date(text), text.strip()[:80]  # "Postface (1er janvier 1973)"
            continue
        if re.search(r"heading|titre|titulo|ttulo", style, re.I) and text.strip() and n_sessions == 0:
            flush()
            label = text.strip()[:80]
        buf.append(text)
        if sum(word_count(x) for x in buf) >= MAX_SEGMENT_WORDS:
            flush()
    flush()

    # O sumário (antes da 1ª sessão) não é conteúdo de Lacan.
    if n_sessions:
        first = next(i for i, s in enumerate(segments) if s.session_date)
        segments = segments[first:]
    return Extraction(segments=segments, meta={"sessions": n_sessions})
