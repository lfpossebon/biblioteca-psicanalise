"""Inventário do acervo local (pasta montada em /corpus, somente leitura).

Não lê o conteúdo dos arquivos (muitos estão no iCloud e seriam baixados): classifica pelo
caminho. O hash e o texto vêm depois, na extração, apenas para o que está no escopo.
"""

import os
import re
import unicodedata
from pathlib import Path

from psilib.catalog import DocSpec, apply_override, load_overrides
from psilib.config import settings
from psilib.scope import classify, detect_lacan_seminar, is_document, is_ignored, year_range_from_name


def _sniff_type(path: Path) -> str | None:
    try:
        with open(path, "rb") as f:
            head = f.read(4)
    except OSError:
        return None
    if head.startswith(b"%PDF"):
        return "pdf"
    if head.startswith(b"PK"):
        return "docx"
    return None


def _guess_language(relpath: str) -> str | None:
    name = relpath.lower()
    if re.search(r"\b(frances|français|francais)\b", name):
        return "fr"
    if re.search(
        r"\b(el|la|los|las|del|psicoanalisis|psicoanalítica|clinica psicoanalitica)\b", name
    ) and not re.search(r"\b(o|os|da|do|das|dos)\b", name):
        return "es"
    return None  # detectado pelo texto na extração


def scan() -> list[DocSpec]:
    root = settings().corpus_dir
    overrides = load_overrides()
    specs: list[DocSpec] = []
    for dirpath, dirnames, filenames in os.walk(root):
        dirnames[:] = sorted(
            d for d in dirnames if not is_ignored(os.path.relpath(os.path.join(dirpath, d), root) + "/")
        )
        for fn in sorted(filenames):
            full = Path(dirpath) / fn
            rel = os.path.relpath(full, root)
            if is_ignored(rel):
                continue
            ftype = rel.rsplit(".", 1)[-1].lower() if is_document(rel) else None
            if ftype is None and "." not in fn.strip(". ").rsplit(" ", 1)[-1]:
                ftype = _sniff_type(full)  # arquivos sem extensão (ex.: "Escritos ... em portugues")
            if ftype is None:
                continue
            c = classify(rel)
            work = detect_lacan_seminar(rel) if c.line == "lacan" else None
            y0, _ = year_range_from_name(rel)
            spec = DocSpec(
                source_id="local",
                uri=unicodedata.normalize("NFC", f"local/{rel}"),  # macOS/iCloud pode guardar nomes em NFD
                title=unicodedata.normalize("NFC", Path(fn).stem),
                local_path=str(full),
                author=c.author,
                line=c.line,
                in_scope=c.in_scope,
                scope_reason=c.reason,
                work=work,
                language=_guess_language(rel),
                year_publication=y0 if c.line not in ("lacan",) else None,
                file_type="docx" if ftype in ("docx",) else ftype,
                bytes=full.stat().st_size,
            )
            if c.line == "freud":
                spec.meta["freud_years"] = list(year_range_from_name(rel))
            specs.append(apply_override(spec, overrides.get(spec.uri)))
    return specs
