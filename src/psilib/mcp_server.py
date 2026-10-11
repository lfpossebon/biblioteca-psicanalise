"""Servidor MCP: expõe a biblioteca como ferramentas para o Claude (Code, Desktop) ou outra IA.

Em produção ele roda junto com a API (psilib.api, porta 8010, caminho /mcp).
Conectar no Claude Code:
    claude mcp add --transport http psicanalise http://localhost:8010/mcp
Standalone (debug): python -m psilib.mcp_server [--stdio]
"""

import functools
import sys

import anyio
from mcp.server.mcpserver import MCPServer
from mcp.server.transport_security import TransportSecuritySettings

from psilib import service
from psilib.service import Filters

INSTRUCTIONS = """\
Biblioteca de psicanálise freudiana e lacaniana: seminários de Lacan (francês, Staferla, com
datas de cada sessão; e traduções), Escritos, Freud (Imago/Cia das Letras/SE), comentadores
lacanianos e escola argentina (Eidelsztein, Schejtman...).

Como usar bem:
- Para explicar um conceito, busque primeiro em Lacan/Freud (linhas=["lacan"] ou ["freud"]),
  depois em comentadores, e cite SEMPRE a 'citacao' de cada trecho usado. Não invente citações.
- Busque em português: a busca semântica encontra trechos equivalentes em francês e espanhol.
  Ao citar um trecho em francês, traduza e mantenha o termo original entre parênteses.
- Use linha_do_tempo para mostrar como um conceito ganha ou perde peso ao longo do ensino
  (granularidade 'sessao' só existe para os seminários do Staferla).
- Use conceitos_relacionados para montar o mapa de um conceito.
- A frequência de um termo não é a teoria: use a série temporal para localizar onde ler,
  e leia os trechos (ler_trecho) antes de concluir algo sobre a evolução do conceito.
"""

mcp = MCPServer("biblioteca-psicanalise", instructions=INSTRUCTIONS)


def _threaded(fn):
    @functools.wraps(fn)
    async def wrapper(*args, **kwargs):
        return await anyio.to_thread.run_sync(functools.partial(fn, *args, **kwargs))

    return wrapper


def _f(linhas, autor, obra, lingua, ano_de, ano_ate, fonte=None) -> Filters:
    return Filters(lines=linhas, author=autor, work=obra, language=lingua, year_from=ano_de, year_to=ano_ate,
                   source=fonte)


@mcp.tool()
@_threaded
def buscar(consulta: str, k: int = 8, linhas: list[str] | None = None, autor: str | None = None,
           obra: str | None = None, lingua: str | None = None, ano_de: int | None = None,
           ano_ate: int | None = None) -> dict:
    """Busca trechos da biblioteca (texto + semântica entre línguas + conceitos), com citação completa.

    linhas: freud, lacan, lacaniano, escola_argentina, comentador_freudiano, referencia.
    obra: slug ou parte do título, ex. "seminario-11", "Encore", "Otro Lacan".
    lingua: pt, fr, es, en, de. Anos filtram pela data em que o texto foi proferido/escrito.
    """
    return service.search(consulta, k, _f(linhas, autor, obra, lingua, ano_de, ano_ate))


@mcp.tool()
@_threaded
def ler_trecho(chunk_id: int, contexto: int = 1) -> dict:
    """Lê um trecho com os vizinhos (contexto = quantos antes e depois), para citar com segurança."""
    return service.read_chunk(chunk_id, contexto)


@mcp.tool()
@_threaded
def listar_conceitos() -> list[dict]:
    """Conceitos do léxico (slug, rótulo, família) com o total de menções no acervo."""
    return service.list_concepts()


@mcp.tool()
@_threaded
def linha_do_tempo(conceito: str, granularidade: str = "obra", linhas: list[str] | None = None,
                   autor: str | None = None, obra: str | None = None, lingua: str | None = None,
                   ano_de: int | None = None, ano_ate: int | None = None) -> dict:
    """Evolução de um conceito: ocorrências por 10 mil palavras por 'sessao', 'ano' ou 'obra'.

    Ex.: linha_do_tempo("gozo", "sessao", linhas=["lacan"]) mostra o peso do gozo sessão a sessão
    nos seminários. Também devolve a primeira aparição por autor.
    """
    return service.timeline(conceito, granularidade, _f(linhas, autor, obra, lingua, ano_de, ano_ate))


@mcp.tool()
@_threaded
def conceitos_relacionados(conceito: str, limite: int = 15) -> dict:
    """Conceitos que coocorrem nos mesmos trechos, ordenados por PMI (associação acima do acaso)."""
    return service.cooccurrence(conceito, limite)


@mcp.tool()
@_threaded
def listar_obras(linhas: list[str] | None = None, autor: str | None = None, lingua: str | None = None) -> list[dict]:
    """Obras indexadas com período, língua, fonte e tamanho."""
    return service.list_works(_f(linhas, autor, None, lingua, None, None))


@mcp.tool()
@_threaded
def estatisticas_acervo() -> dict:
    """Panorama do acervo indexado e das últimas execuções do pipeline."""
    return service.stats()


def transport_security() -> TransportSecuritySettings:
    # Proteção contra DNS rebinding: só aceita chamadas endereçadas a localhost.
    return TransportSecuritySettings(
        allowed_hosts=["localhost:*", "127.0.0.1:*", "app:*"],
        allowed_origins=["http://localhost:*", "http://127.0.0.1:*"],
    )


def main() -> None:
    if "--stdio" in sys.argv:
        mcp.run("stdio")
        return
    mcp.run("streamable-http", host="0.0.0.0", port=8011, transport_security=transport_security())


if __name__ == "__main__":
    main()
