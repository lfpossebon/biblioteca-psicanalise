from dataclasses import dataclass, field
from datetime import date


@dataclass
class Segment:
    """Unidade extraída: uma página (PDF) ou uma sessão/seção (DOCX)."""

    text: str
    page_no: int | None = None
    section_label: str | None = None
    session_date: date | None = None


@dataclass
class Extraction:
    segments: list[Segment]
    n_pages: int | None = None
    ocr_needed: bool = False
    meta: dict = field(default_factory=dict)
