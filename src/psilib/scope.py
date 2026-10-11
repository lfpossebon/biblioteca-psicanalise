"""Classificação de escopo (linha teórica, autor) e identificação de obras canônicas."""

import re
from dataclasses import dataclass
from functools import lru_cache

from psilib.config import load_yaml
from psilib.text import normalize, normalize_path


@dataclass(frozen=True)
class Classification:
    author: str | None
    line: str
    in_scope: bool
    reason: str


@dataclass(frozen=True)
class WorkRef:
    slug: str
    title: str
    work_type: str
    seminar_number: str | None
    year_start: int | None
    year_end: int | None


@lru_cache
def _rules():
    cfg = load_yaml("scope.yaml")
    authors = [(re.compile(a["pattern"]), a["name"], a["line"]) for a in cfg["authors"]]
    folders = [(re.compile(f["pattern"]), f.get("author"), f["line"]) for f in cfg["folders"]]
    ignore = [re.compile(p) for p in cfg["ignore"]]
    return cfg, authors, folders, ignore


def line_in_scope(line: str) -> bool:
    cfg = _rules()[0]
    return bool(cfg["lines"].get(line, {}).get("in_scope", False))


def is_ignored(relpath: str) -> bool:
    p = normalize_path(relpath)
    return any(rx.search(p) for rx in _rules()[3])


def is_document(relpath: str) -> bool:
    ext = relpath.rsplit(".", 1)[-1].lower() if "." in relpath.rsplit("/", 1)[-1] else ""
    return ext in _rules()[0]["document_extensions"]


def classify(relpath: str) -> Classification:
    _, authors, folders, _ = _rules()
    p = normalize_path(relpath)
    for rx, name, line in authors:
        if rx.search(p):
            return Classification(name, line, line_in_scope(line), f"autor:{name}")
    for rx, name, line in folders:
        if rx.search(p):
            return Classification(name, line, line_in_scope(line), f"pasta:{rx.pattern}")
    return Classification(None, "revisar", False, "sem regra")


def content_signal(text: str) -> tuple[str, dict[str, int]]:
    """Sugere uma linha teórica pelo vocabulário (para documentos 'revisar')."""
    signals = _rules()[0]["content_signals"]
    t = normalize(text)
    scores = {line: sum(t.count(term) for term in terms) for line, terms in signals.items()}
    best = max(scores, key=scores.get)
    return (best if scores[best] > 0 else "revisar"), scores


# ------------------------------------------------------------------ obras


@lru_cache
def _seminars():
    return [
        (s["n"], re.compile(s["cues"]), s) for s in load_yaml("works.yaml")["lacan_seminars"]
    ]


def seminar_ref(n: str) -> WorkRef:
    for num, _, s in _seminars():
        if num == n:
            y0, y1 = s["years"]
            return WorkRef(f"lacan-seminario-{num}", f"Séminaire {num} — {s['fr']}", "seminario", num, y0, y1)
    raise KeyError(n)


def detect_lacan_seminar(relpath: str) -> WorkRef | None:
    """Número do seminário a partir do nome do arquivo. Pistas de título vencem números."""
    name = normalize_path(relpath.rsplit("/", 1)[-1])
    name = re.sub(r"[-_.]+", " ", name)
    if re.search(r"\bseminarios\b|\bseminaires\b|\b\d+ ao \d+\b", name):
        return None  # compilação de vários seminários
    if not re.search(r"seminari|seminaire|livro \d|\bs\d{1,2}b?\b|^\d{2} ", name):
        return None
    for num, cue, _ in _seminars():
        if cue.search(name):
            return seminar_ref(num)
    m = re.search(r"(?:livro|seminario|seminaire|\bs)\s*(\d{1,2}b?)\b", name) or re.match(r"^(\d{2}) ", name)
    if m:
        num = m.group(1).lstrip("0")
        try:
            return seminar_ref(num)
        except KeyError:
            return None
    return None


def lacan_text_ref(slug: str) -> WorkRef:
    for t in load_yaml("works.yaml")["lacan_texts"]:
        if t["slug"] == slug:
            return WorkRef(f"lacan-{slug.replace('_', '-')}", t["title"], "escrito", None, t["year"], t["year"])
    raise KeyError(slug)


def year_range_from_name(relpath: str) -> tuple[int | None, int | None]:
    """Anos no nome do arquivo: 'vol-17-1917-1918' → (1917, 1918); '(2013)' → (2013, 2013)."""
    years = [int(y) for y in re.findall(r"(?<!\d)(1[89]\d\d|20[0-2]\d)(?!\d)", relpath.rsplit("/", 1)[-1])]
    if not years:
        return None, None
    return min(years), max(years)
