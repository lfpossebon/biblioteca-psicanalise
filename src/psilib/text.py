"""Normalização de texto e datas, compartilhada por escopo, conceitos e parsers."""

import re
import unicodedata
from datetime import date

_APOSTROPHES = str.maketrans({"’": "'", "‘": "'", "ʼ": "'", "`": "'", " ": " "})


def strip_accents(s: str) -> str:
    s = s.replace("ß", "ss").replace("œ", "oe").replace("Œ", "OE").replace("æ", "ae")
    return "".join(c for c in unicodedata.normalize("NFKD", s) if not unicodedata.combining(c))


def normalize(s: str, *, lower: bool = True) -> str:
    """Texto para casamento de padrões: sem acentos, apóstrofos unificados, espaços colapsados."""
    s = strip_accents(s.translate(_APOSTROPHES))
    s = re.sub(r"\s+", " ", s)
    return s.lower() if lower else s


def normalize_path(path: str) -> str:
    """Caminho relativo normalizado para as regras de escopo: '_' e '-' viram espaço, mantém '/'."""
    return re.sub(r" +", " ", normalize(re.sub(r"[_\-–]", " ", path)))


def word_count(s: str) -> int:
    return len(s.split())


MONTHS = {
    # fr
    "janvier": 1, "fevrier": 2, "mars": 3, "avril": 4, "mai": 5, "juin": 6, "juillet": 7,
    "aout": 8, "septembre": 9, "octobre": 10, "novembre": 11, "decembre": 12,
    # pt
    "janeiro": 1, "fevereiro": 2, "marco": 3, "abril": 4, "maio": 5, "junho": 6, "julho": 7,
    "agosto": 8, "setembro": 9, "outubro": 10, "novembro": 11, "dezembro": 12,
    # es
    "enero": 1, "febrero": 2, "mayo": 5, "junio": 6, "julio": 7,
    "septiembre": 9, "setiembre": 9, "octubre": 10, "noviembre": 11, "diciembre": 12,
}
_MONTH_RE = "|".join(sorted(MONTHS, key=len, reverse=True))

# "15 janvier 1964", "15 de janeiro de 1964", "1er décembre 1971", "15 de enero de 1964"
DATE_RE = re.compile(
    rf"\b(\d{{1,2}})(?:er|o|º|°)?\s+(?:de\s+)?({_MONTH_RE})\s+(?:de\s+)?(19\d\d)\b", re.IGNORECASE
)


_WEEKDAYS = (r"(?:lundi|mardi|mercredi|jeudi|vendredi|samedi|dimanche|segunda|terca|quarta|quinta|sexta"
             r"|sabado|domingo|lunes|martes|miercoles|jueves|viernes)(?:-feira)?,?\s+")


def fix_digit_typos(s: str) -> str:
    """Corrige a letra 'l' digitada no lugar do 1 em datas: 'l4 novembre l962' → '14 novembre 1962'."""
    return re.sub(r"(?<![a-z])l(?=\d)|(?<=\d)l(?![a-z])", "1", s)


def parse_date(s: str) -> date | None:
    m = DATE_RE.search(fix_digit_typos(normalize(s)))
    if not m:
        return None
    day, month, year = int(m.group(1)), MONTHS[m.group(2).lower()], int(m.group(3))
    try:
        return date(year, month, day)
    except ValueError:
        return None


def session_heading_date(line: str, max_len: int = 70) -> date | None:
    """Data de sessão quando a linha *é* um cabeçalho de sessão (começa com a data e é curta)."""
    s = fix_digit_typos(normalize(line).strip())
    s = re.sub(r"\btable des seances\b", "", s).strip()
    s = re.sub(rf"^{_WEEKDAYS}", "", s)
    if not s or len(s) > max_len:
        return None
    m = DATE_RE.match(s)
    if not m:
        # "Lição de 15 de janeiro de 1964", "Leçon du 15 janvier 1964", "Clase del 15 de enero de 1964"
        m2 = re.match(r"^(?:licao|lecon|clase|sessao|seance|aula)\s+(?:del|du|de)?\s*", s)
        if not m2:
            return None
        m = DATE_RE.match(s[m2.end():])
        if not m:
            return None
    try:
        return date(int(m.group(3)), MONTHS[m.group(2).lower()], int(m.group(1)))
    except ValueError:
        return None
