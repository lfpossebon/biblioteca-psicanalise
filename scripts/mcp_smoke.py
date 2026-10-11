"""Teste de fumaça do servidor MCP: lista ferramentas e chama algumas.

Uso: uv run python scripts/mcp_smoke.py [url]
"""

import asyncio
import json
import sys

from mcp import Client


async def main(url: str) -> None:
    async with Client(url) as client:
        tools = await client.list_tools()
        print("ferramentas:", [t.name for t in tools.tools])
        for name, args in [
            ("estatisticas_acervo", {}),
            ("buscar", {"consulta": "objeto a causa do desejo", "k": 3, "linhas": ["lacan"]}),
        ]:
            res = await client.call_tool(name, args)
            text = res.content[0].text if res.content else json.dumps(res.structured_content)
            print(f"\n== {name}\n{text[:1200]}")


asyncio.run(main(sys.argv[1] if len(sys.argv) > 1 else "http://localhost:8010/mcp"))
