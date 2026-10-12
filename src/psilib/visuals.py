"""Figuras do chat: especificações de gráficos montadas com dados do banco — nunca do modelo.

O modelo escolhe *qual* figura mostrar (ferramenta `mostrar_visual`); daqui sai a especificação que a
página desenha (static/viz.js) e um resumo curto que volta ao modelo para ele comentar a figura.
"""

from psilib import service
from psilib.service import Filters

UNIT = "ocorrências por 10 mil palavras"
METHOD_NOTE = (
    "Frequência normalizada pelo tamanho do texto: indica onde ler, não o que o conceito significa. "
    "Contagem pelo léxico de conceitos do acervo."
)
MAX_BARS = 30
MIN_WORDS = 2000  # período com menos palavras que isso gera proporções ruidosas e fica de fora
MAX_SERIES = 3  # paleta categórica validada para 3 cores em todos os pares
LINE_LABELS = {
    "freud": "Freud",
    "lacan": "Lacan",
    "lacaniano": "comentadores lacanianos",
    "escola_argentina": "escola argentina",
    "comentador_freudiano": "comentadores de Freud",
    "fonte_clinica": "fontes clínicas",
    "referencia": "dicionários",
}


class VisualError(ValueError):
    """Pedido de figura que não pode ser atendido (conceito fora do léxico, sem dados...)."""


def _concept_labels() -> dict[str, str]:
    return {c["slug"]: c["label"] for c in service.list_concepts()}


def _scope(filters: Filters) -> str:
    parts = []
    if filters.lines:
        parts.append(", ".join(LINE_LABELS.get(x, x) for x in filters.lines))
    if filters.author:
        parts.append(filters.author)
    return f" · {' · '.join(parts)}" if parts else " · todo o acervo"


def _point_label(iso: str, granularity: str, seminars: str | None) -> str:
    y, m, d = iso[:10].split("-")
    if granularity == "sessao":
        sem = ",".join(s for s in (seminars or "").split(",") if s)
        return f"{d}/{m}/{y}" + (f" · Seminário {sem}" if sem else "")
    return y


def concept_timeline(concepts: list[str], granularity: str, filters: Filters, title: str | None) -> dict:
    labels = _concept_labels()
    series, firsts = [], []
    for concept in concepts[:MAX_SERIES]:
        res = service.timeline(concept, granularity, filters, MIN_WORDS)
        if "erro" in res:
            raise VisualError(res["erro"])
        points = [
            {
                "x": r["periodo"][:10],
                "y": float(r["por_10k"] or 0),
                "rotulo": _point_label(r["periodo"], granularity, r.get("seminarios")),
                "mencoes": int(r["mencoes"] or 0),
            }
            for r in res["serie"]
        ]
        if not points:
            raise VisualError(f"sem dados suficientes para '{concept}' com esses filtros")
        series.append(
            {"nome": labels.get(res["conceito"], res["conceito"]), "slug": res["conceito"], "pontos": points}
        )
        firsts.append(res["primeiras_aparicoes"])

    tiles = []
    if len(series) == 1:
        s, first = series[0], firsts[0]
        if first:
            tiles.append(
                {"rotulo": "Primeira aparição", "valor": first[0]["data"][:4], "detalhe": first[0]["autor"]}
            )
        peak = max(s["pontos"], key=lambda p: p["y"])
        tiles.append({"rotulo": "Pico", "valor": peak["rotulo"], "detalhe": f"{_fmt(peak['y'])} {UNIT}"})
        tiles.append({"rotulo": "Menções no período", "valor": _int(sum(p["mencoes"] for p in s["pontos"])),
                      "detalhe": f"em {len(s['pontos'])} {'sessões' if granularity == 'sessao' else 'anos'}"})  # fmt: skip
    else:
        for s in series:
            peak = max(s["pontos"], key=lambda p: p["y"])
            tiles.append(
                {
                    "rotulo": f"Pico de {s['nome']}",
                    "valor": peak["rotulo"],
                    "detalhe": f"{_fmt(peak['y'])} {UNIT}",
                }
            )

    names = [s["nome"] for s in series]
    default_title = (
        names[0] if len(names) == 1 else ", ".join(names[:-1]) + " e " + names[-1]
    ) + " ao longo do tempo"
    return {
        "tipo": "linha",
        "granularidade": granularity,
        "titulo": title or default_title,
        "subtitulo": f"{UNIT[0].upper() + UNIT[1:]}, por {'sessão' if granularity == 'sessao' else 'ano'}{_scope(filters)}",
        "unidade": UNIT,
        "series": series,
        "fichas": tiles,
        "nota": METHOD_NOTE,
    }


def weight_by_work(concept: str, filters: Filters, title: str | None) -> dict:
    res = service.timeline(concept, "obra", filters, MIN_WORDS)
    if "erro" in res:
        raise VisualError(res["erro"])
    rows = [r for r in res["serie"] if r["por_10k"] and float(r["por_10k"]) > 0]  # obras sem menção são ruído
    if not rows:
        raise VisualError(f"'{concept}' não aparece em nenhuma obra com esses filtros")
    total = len(rows)
    if total > MAX_BARS:  # as de maior peso, mas mostradas em ordem cronológica
        rows = sorted(
            sorted(rows, key=lambda r: r["por_10k"], reverse=True)[:MAX_BARS], key=lambda r: r["periodo"]
        )
    label = _concept_labels().get(res["conceito"], res["conceito"])
    note = METHOD_NOTE + (
        f" Mostrando as {MAX_BARS} de maior peso, entre {total} obras que citam o conceito."
        if total > MAX_BARS
        else ""
    )
    return {
        "tipo": "barras",
        "titulo": title or f"Onde {label.lower()} pesa mais",
        "subtitulo": f"{UNIT[0].upper() + UNIT[1:]}, por obra, em ordem cronológica{_scope(filters)}",
        "unidade": UNIT,
        "barras": [
            {
                "rotulo": r["obra"] or r["chave"],
                "valor": float(r["por_10k"]),
                "detalhe": f"{r['autor'] or '?'} · {r['periodo'][:4]} · {_int(r['mencoes'])} menções",
            }
            for r in rows
        ],
        "nota": note,
    }


def related(concept: str, title: str | None) -> dict:
    res = service.cooccurrence(concept, 15)
    if "erro" in res:
        raise VisualError(res["erro"])
    rows = [r for r in res["relacionados"] if r["pmi"] is not None and float(r["pmi"]) > 0]
    if not rows:
        raise VisualError(f"poucos trechos em comum para mapear relações de '{concept}'")
    label = _concept_labels().get(res["conceito"], res["conceito"])
    return {
        "tipo": "barras",
        "titulo": title or f"Conceitos associados a {label.lower()}",
        "subtitulo": "Associação acima do acaso (PMI) nos mesmos trechos · todo o acervo",
        "unidade": "PMI",
        "barras": [
            {
                "rotulo": r["label"],
                "valor": float(r["pmi"]),
                "detalhe": f"{_int(r['trechos_juntos'])} trechos em comum",
            }
            for r in rows
        ],
        "nota": "PMI acima de zero: os conceitos aparecem juntos mais do que o acaso explicaria. "
        "Coocorrência não é relação teórica: leia os trechos.",
    }


def build(kind: str, concepts: list[str], granularity: str, filters: Filters, title: str | None) -> dict:
    if kind == "linha_do_tempo":
        return concept_timeline(concepts, granularity, filters, title)
    if kind == "peso_por_obra":
        return weight_by_work(concepts[0], filters, title)
    if kind == "relacionados":
        return related(concepts[0], title)
    raise VisualError(f"tipo de figura desconhecido: {kind}")


def summary(spec: dict) -> dict:
    """O que volta ao modelo: o suficiente para comentar a figura, sem a série inteira (tokens)."""
    out = {
        "figura": spec["figura"],
        "exibida": True,
        "titulo": spec["titulo"],
        "subtitulo": spec["subtitulo"],
    }
    if spec["tipo"] == "linha":
        out["series"] = [
            {
                "conceito": s["nome"],
                "pontos": len(s["pontos"]),
                "de": s["pontos"][0]["rotulo"],
                "ate": s["pontos"][-1]["rotulo"],
                "picos": [
                    {"quando": p["rotulo"], "por_10k": p["y"]}
                    for p in sorted(s["pontos"], key=lambda p: p["y"], reverse=True)[:3]
                ],
            }
            for s in spec["series"]
        ]
        out["fichas"] = spec["fichas"]
    else:
        out["maiores"] = [
            {"rotulo": b["rotulo"], "valor": b["valor"], "detalhe": b["detalhe"]}
            for b in sorted(spec["barras"], key=lambda b: b["valor"], reverse=True)[:5]
        ]
        out["barras"] = len(spec["barras"])
    out["instrucao"] = (
        f"A figura já está visível para o leitor. Refira-se a ela como Figura {spec['figura']}."
    )
    return out


def _fmt(x: float) -> str:
    return f"{x:.2f}".replace(".", ",")


def _int(n) -> str:
    return f"{int(n or 0):,}".replace(",", ".")
