import zipfile
from datetime import date

import pytest

from psilib.chunking import chunk_segments
from psilib.concepts import count_mentions, match_query
from psilib.extract import docx as docx_x
from psilib.scope import classify, detect_lacan_seminar, year_range_from_name
from psilib.text import parse_date, session_heading_date

# ------------------------------------------------------------------ datas


@pytest.mark.parametrize(
    "line, expected",
    [
        ("15  janvier  1964   Table des séances", date(1964, 1, 15)),
        ("Leçon du 1er décembre 1971", date(1971, 12, 1)),
        ("15 de janeiro de 1964", date(1964, 1, 15)),
        ("Clase del 3 de mayo de 1967", date(1967, 5, 3)),
        ("1º de novembro de 1966", date(1966, 11, 1)),
        ("Ele disse em 15 de janeiro de 1964 que o sujeito se constitui no campo do Outro", None),
        ("Leçon 1 15 janvier 1964", None),  # linha do sumário, não cabeçalho
        ("l4  Novembre  l962", date(1962, 11, 14)),  # 'l' digitado no lugar de 1 (Staferla S10)
        ("0l Décembre l965", date(1965, 12, 1)),
        ("Jeudi 4 Novembre 1971", date(1971, 11, 4)),
    ],
)
def test_session_heading_date(line, expected):
    assert session_heading_date(line) == expected


def test_parse_date_inside_text():
    assert parse_date("Postface (1er janvier 1973)") == date(1973, 1, 1)


# ------------------------------------------------------------------ escopo


@pytest.mark.parametrize(
    "path, line, author",
    [
        (
            "livros/Seminarios Lacan/11-LACAN-Jacques.-O-seminário-livro-11.-Os-quatro-conceitos.pdf",
            "lacan",
            "Jacques Lacan",
        ),
        (
            "Camila Kushner/pdfcoffee.com_alfredo-eidelsztein-el-seminario-20-de-jacques-lacan-4-pdf-free.pdf",
            "escola_argentina",
            "Alfredo Eidelsztein",
        ),
        (
            "Camila Kushner/El establecimiento del seminario de Jacques Lacan - Jacques-Alain Miller.pdf",
            "lacaniano",
            "Jacques-Alain Miller",
        ),
        ("livros/Introducao a leitura de Lacan - Joel Dor.pdf", "lacaniano", "Joël Dor"),
        (
            "livros/KLEIN, Melanie - Inveja E Gratidão e Outros Trabalhos - 1946-1963.pdf",
            "kleiniano",
            "Melanie Klein",
        ),
        ("livros/WINNICOTT D W - Privação e Delinquência.pdf", "winnicottiano", "D. W. Winnicott"),
        (
            "livros/Freud Obras Completas/freud-sigmund-obras-completas-imago-vol-17-1917-1918.pdf",
            "freud",
            "Sigmund Freud",
        ),
        (
            "CEP/CICLO 01/AULA 04/Ciclo-I-Comp.-Aula-04-FREUD-S.-Vol.-VI-Atos-falhos-combinados.pdf",
            "freud",
            "Sigmund Freud",
        ),
        ("livros/julho CONTRATO DE LOCAÇÃO RESIDENCIAL FERNÃO DIAS.docx", "fora_do_tema", "Fora do tema"),
        ("livros/Introdução a Jacques Lacan.pdf", "lacaniano", "Comentador lacaniano (não identificado)"),
    ],
)
def test_classify(path, line, author):
    c = classify(path)
    assert (c.line, c.author) == (line, author)


def test_out_of_scope_lines():
    assert not classify("livros/KLEIN, Melanie - A Psicanalise de Crianças.pdf").in_scope
    assert classify("Camila Kushner/Bruno Bonoris - Goce.pdf").in_scope


@pytest.mark.parametrize(
    "path, n",
    [
        (
            "livros/Seminarios Lacan/05-LACAN-Jacques.-O-seminário.-O-desejo-e-sua-interpretação-1958-59.pdf",
            "6",
        ),
        (
            "livros/Seminarios Lacan/25-LACAN-Jacques.-O-seminário-livro-26.-El-momento-de-concluir-1977-78.pdf",
            "25",
        ),
        ("livros/Seminarios Lacan/19-LACAN-Jacques.-O-seminário.-O-saber-do-psicanalista-1971-72.pdf", "19b"),
        ("livros/Seminarios Lacan/Seminário 24 - Jacques Lacan.pdf", "24"),
        ("Camila Kushner/Lacan - Seminário 21 - Os nomes do pai (os não tolos erram).pdf", "21"),
        ("Camila Kushner/Lacan. Seminários do 1 ao 27 portugues.pdf", None),
    ],
)
def test_detect_lacan_seminar(path, n):
    ref = detect_lacan_seminar(path)
    assert (ref.seminar_number if ref else None) == n


def test_year_range():
    assert year_range_from_name("x/freud-sigmund-obras-completas-imago-vol-17-1917-1918.pdf") == (1917, 1918)
    assert year_range_from_name("x/Synthome-Grama (2013).pdf") == (2013, 2013)


# ------------------------------------------------------------------ conceitos


def test_concepts_respect_language():
    fr = "La jouissance n'est pas le désir. L'objet a cause le désir; le grand Autre est barré."
    m = count_mentions(fr, "fr")
    assert m["gozo"] == 1 and m["desejo"] == 2 and m["objeto-a"] == 1 and m["grande-outro"] == 1
    # "real" em inglês não é o registro do Real
    assert "real" not in count_mentions("This is a real problem", "en")


def test_case_sensitive_other():
    assert count_mentions("o desejo do Outro", "pt").get("grande-outro") == 1
    assert "grande-outro" not in count_mentions("um outro dia qualquer", "pt")


def test_objeto_a_negative_lookahead():
    assert "objeto-a" not in count_mentions("o objeto a ser analisado", "pt")
    assert count_mentions("o objeto a como causa", "pt")["objeto-a"] == 1


def test_match_query_any_language():
    assert "gozo" in match_query("o que é jouissance?")
    assert "nome-do-pai" in match_query("Nome-do-Pai e psicose")


# ------------------------------------------------------------------ chunking


def test_chunks_do_not_cross_sessions_and_keep_pages():
    para = "Isto é uma frase sobre o sujeito. " * 60  # ~420 palavras
    segs = [
        {"text": para, "page_no": 1, "section_label": "A", "session_date": date(1964, 1, 15)},
        {"text": para, "page_no": 2, "section_label": "B", "session_date": date(1964, 1, 22)},
    ]
    chunks = chunk_segments(segs, target_words=200)
    assert {c.session_date for c in chunks} == {date(1964, 1, 15), date(1964, 1, 22)}
    assert all(c.n_words <= 220 for c in chunks)
    assert sum(c.n_words for c in chunks) == 2 * len(para.split())  # nada perdido, nada duplicado


# ------------------------------------------------------------------ docx (formato Staferla)


def _docx(tmp_path, body: str):
    xml = (
        '<?xml version="1.0" encoding="UTF-8"?>'
        '<w:document xmlns:w="http://schemas.openxmlformats.org/wordprocessingml/2006/main"><w:body>'
        + body
        + "</w:body></w:document>"
    )
    path = tmp_path / "s.docx"
    with zipfile.ZipFile(path, "w") as z:
        z.writestr("word/document.xml", xml)
    return path


def _p(text, bookmark=None, link=None):
    bm = f'<w:bookmarkStart w:id="1" w:name="{bookmark}"/>' if bookmark else ""
    run = f"<w:r><w:t>{text}</w:t></w:r>"
    if link:
        run = f'<w:hyperlink w:anchor="{link}">{run}</w:hyperlink>'
    return f"<w:p>{bm}{run}</w:p>"


def test_staferla_sessions(tmp_path):
    body = (
        _p("Table des séances")
        + _p("15 janvier 1964", link="Janv15")
        + _p("22 janvier 1964", link="Janv22")
        + _p("15 janvier 1964", bookmark="Janv15")
        + _p("Mesdames, Messieurs, aujourd'hui l'inconscient.")
        + _p("22 janvier 1964", bookmark="Janv22")
        + _p("La répétition n'est pas la reproduction.")
    )
    ex = docx_x.extract(_docx(tmp_path, body))
    assert [s.session_date for s in ex.segments] == [date(1964, 1, 15), date(1964, 1, 22)]
    assert "inconscient" in ex.segments[0].text and "Table" not in ex.segments[0].text


# ------------------------------------------------------------------ Staferla / Internet Archive


@pytest.mark.parametrize(
    "filename, slug",
    [
        ("L'etourdit.docx", "letourdit"),
        ("La_Troisieme.docx", "la_troisieme"),
        ("L'instance de la lettre.docx", "linstance_de_la_lettre"),
        ("Trois prefaces.docx", "trois_prefaces"),
    ],
)
def test_staferla_text_slug(filename, slug):
    from psilib.scope import lacan_text_ref
    from psilib.sources.staferla import _text_slug

    assert _text_slug(filename) == slug
    assert lacan_text_ref(slug)  # existe em config/works.yaml


def test_fix_session_years():
    from psilib.pipeline import fix_session_years

    segs = [
        {"session_date": date(1965, 6, 16), "section_label": "Sessão de 16/06/1965"},
        {"session_date": date(1966, 6, 23), "section_label": "Sessão de 23/06/1966"},
        {"session_date": date(1973, 1, 1), "section_label": "Postface (1er janvier 1973)"},
    ]
    assert fix_session_years(segs, 1964, 1965) == ["1966-06-23 → 1965-06-23"]
    assert segs[1]["session_date"] == date(1965, 6, 23)
    assert segs[2]["session_date"] == date(1973, 1, 1)  # posfácio fica com a própria data
