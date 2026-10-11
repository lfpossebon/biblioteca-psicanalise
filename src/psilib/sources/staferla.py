"""Coletor do STAFERLA (http://staferla.free.fr): seminários de Lacan em francês, Escritos e Freud.

O site é pequeno e mantido por uma pessoa: baixamos devagar (http_delay_seconds), uma vez,
e depois só revalidamos com ETag/Last-Modified — revisões novas do Staferla são detectadas
e reprocessadas sem baixar o que não mudou.

Se o site estiver fora do ar, usamos a captura mais recente de cada arquivo no Internet Archive
(Wayback Machine). O `uri` continua sendo o endereço original do Staferla, então quando o site
voltar a próxima coleta substitui as cópias do arquivo pelas versões atuais.
"""

import logging
import re
import time
from pathlib import Path
from urllib.parse import quote, unquote, urljoin

import httpx

from psilib.catalog import DocSpec
from psilib.config import settings
from psilib.scope import lacan_text_ref, seminar_ref
from psilib.text import normalize

log = logging.getLogger(__name__)
BASE = "http://staferla.free.fr/"
CDX = "https://web.archive.org/cdx/search/cdx"


class Client:
    def __init__(self):
        s = settings()
        self.http = httpx.Client(
            headers={"User-Agent": s.http_user_agent},
            timeout=httpx.Timeout(60, read=300),
            follow_redirects=True,
        )
        self.delay = s.http_delay_seconds
        self._last = 0.0

    def get(self, url: str, **kw) -> httpx.Response:
        wait = self._last + self.delay - time.monotonic()
        if wait > 0:
            time.sleep(wait)
        for attempt in range(4):
            try:
                r = self.http.get(url, **kw)
                self._last = time.monotonic()
                if r.status_code < 500:
                    return r
            except httpx.TransportError as e:
                log.warning("falha em %s (%s), tentativa %d", url, e, attempt + 1)
            time.sleep(5 * (attempt + 1))
        raise RuntimeError(f"não foi possível baixar {url}")


def _links(html: str, base: str) -> list[str]:
    return [urljoin(base, quote(h, safe="/:%.'()")) for h in re.findall(r'href="([^"]+)"', html, flags=re.I)]


def _page(client: Client, url: str) -> str:
    r = client.get(url)
    r.raise_for_status()
    return r.content.decode("latin-1")


def _pick_file(links: list[str]) -> str | None:
    """Prefere .docx (estrutura de sessões, notas) ao .pdf."""
    for ext in (".docx", ".pdf"):
        for link in links:
            if link.lower().endswith(ext):
                return link
    return None


def discover(client: Client, include_freud: bool = False) -> list[dict]:
    """Lista os arquivos a coletar: seminários, textos de Lacan e (opcional) Freud."""
    items: list[dict] = []
    home = _page(client, BASE)
    for link in _links(home, BASE):
        m = re.search(r"/(S\d+b?)/\1\.htm$", link)
        if m:
            n = m.group(1)[1:]
            f = _pick_file(_links(_page(client, link), link))
            if f:
                items.append({"kind": "seminario", "key": n, "url": f})
    lacan_index = urljoin(BASE, "Lacan/lacan.htm")
    for link in _links(_page(client, lacan_index), lacan_index):
        if link.endswith(".htm"):
            slug = Path(unquote(link)).stem
            try:
                f = _pick_file(_links(_page(client, link), link))
            except Exception as e:  # uma página fora do ar não derruba a coleta inteira
                log.warning("pulando %s: %s", link, e)
                continue
            if f:
                items.append({"kind": "texto", "key": slug, "url": f})
    if include_freud:
        freud_index = urljoin(BASE, "Freud/freud.htm")
        for link in _links(_page(client, freud_index), freud_index):
            if link.lower().endswith(".docx"):
                lang = "de" if "gesammelte" in link.lower() else "en"
                items.append({"kind": "freud", "key": lang, "url": link})
    return items


def site_is_up(client: Client) -> bool:
    try:
        r = client.http.get(BASE, timeout=20)
        return r.status_code == 200 and "Séminaire" in r.content.decode("latin-1", "ignore")
    except httpx.HTTPError:
        return False


def _text_slug(filename: str) -> str:
    """'L'etourdit.docx' → 'letourdit'; 'La_Troisieme.docx' → 'la_troisieme'."""
    stem = normalize(Path(filename).stem).replace("'", "")
    return re.sub(r"[ _]+", "_", stem).strip("_")


def discover_archive(client: Client, include_freud: bool = False) -> list[dict]:
    """Captura mais recente (HTTP 200) de cada arquivo do Staferla na Wayback Machine. Prefere .docx."""
    r = client.http.get(
        CDX,
        params={
            "url": "staferla.free.fr/*",
            "output": "json",
            "filter": "statuscode:200",
            "fl": "original,timestamp",
        },
        timeout=120,
    )
    r.raise_for_status()
    latest: dict[str, tuple[str, str]] = {}
    for original, ts in r.json()[1:]:
        url = original.replace(":80/", "/")
        key = normalize(unquote(url))
        if key.endswith((".docx", ".pdf")) and (key not in latest or ts > latest[key][1]):
            latest[key] = (url, ts)

    chosen: dict[tuple[str, str], dict] = {}
    for url, ts in latest.values():
        path = unquote(url[len(BASE) :]) if url.startswith(BASE) else None
        if not path:
            continue
        folder, filename = path.split("/", 1) if "/" in path else ("", path)
        ext = filename.rsplit(".", 1)[-1].lower()
        if re.fullmatch(r"S\d+b?", folder):
            kind, key = "seminario", folder[1:]
        elif folder == "Lacan":
            kind, key = "texto", _text_slug(filename)
        elif folder == "Freud" and include_freud and ext == "docx":
            kind, key = "freud", "de" if "gesammelte" in filename.lower() else "en"
        else:
            continue
        item = {
            "kind": kind,
            "key": key,
            "url": url,
            "ext": ext,
            "wayback_ts": ts,
            "download_url": f"https://web.archive.org/web/{ts}id_/{url}",
        }
        prev = chosen.get((kind, key))
        # .docx vence .pdf (tem as sessões marcadas); entre dois do mesmo tipo, a captura mais recente
        rank = lambda i: (i["ext"] == "docx", i["wayback_ts"])  # noqa: E731
        if prev is None or rank(item) > rank(prev):
            chosen[(kind, key)] = item
    return sorted(chosen.values(), key=lambda i: (i["kind"], i["key"]))


def _spec(item: dict, path: Path, meta: dict) -> DocSpec:
    kind, key = item["kind"], item["key"]
    if kind == "seminario":
        work, author, line, lang = seminar_ref(key), "Jacques Lacan", "lacan", "fr"
        title = f"Staferla — {work.title}"
    elif kind == "texto":
        try:
            work = lacan_text_ref(key)
        except KeyError:
            work = None
        author, line, lang = "Jacques Lacan", "lacan", "fr"
        title = f"Staferla — {work.title if work else key}"
    else:
        work, author, line, lang = None, "Sigmund Freud", "freud", key
        title = (
            "Freud — Standard Edition (Staferla)" if key == "en" else "Freud — Gesammelte Werke (Staferla)"
        )
    return DocSpec(
        source_id="staferla",
        uri=item["url"],
        title=title,
        local_path=str(path),
        author=author,
        line=line,
        in_scope=True,
        scope_reason="fonte: staferla",
        work=work,
        language=lang,
        file_type=path.suffix.lstrip(".").lower(),
        bytes=path.stat().st_size,
        meta=meta,
    )


def fetch(
    include_freud: bool = False, known: dict[str, dict] | None = None, source: str = "auto"
) -> list[tuple[DocSpec, bool]]:
    """Baixa o que é novo ou mudou. Devolve [(spec, mudou?)].

    source: 'site' (Staferla), 'arquivo' (Wayback Machine) ou 'auto' (site; se fora do ar, arquivo).
    """
    known = known or {}
    client = Client()
    if source == "auto":
        source = "site" if site_is_up(client) else "arquivo"
        if source == "arquivo":
            log.warning("Staferla fora do ar: usando as cópias do Internet Archive")
    items = discover(client, include_freud) if source == "site" else discover_archive(client, include_freud)
    log.info("staferla (%s): %d arquivos", source, len(items))
    out: list[tuple[DocSpec, bool]] = []
    raw = settings().data_dir / "raw" / "staferla"
    for item in items:
        sub = {"seminario": f"S{item['key']}", "texto": "Lacan", "freud": "Freud"}[item["kind"]]
        path = raw / sub / unquote(item["url"].rsplit("/", 1)[-1])
        path.parent.mkdir(parents=True, exist_ok=True)
        prev = known.get(item["url"], {})
        if "wayback_ts" in item:
            if path.exists() and prev.get("wayback_timestamp") == item["wayback_ts"]:
                out.append((_spec(item, path, prev), False))
                continue
            r = client.get(item["download_url"])
            r.raise_for_status()
            path.write_bytes(r.content)
            meta = {
                "via": "wayback",
                "wayback_timestamp": item["wayback_ts"],
                "etag": None,
                "last_modified": None,
            }
            log.info(
                "baixado do arquivo %s (captura %s, %d KB)",
                path.name,
                item["wayback_ts"][:8],
                len(r.content) // 1024,
            )
            out.append((_spec(item, path, meta), True))
            continue
        headers = {}
        if path.exists() and prev.get("etag"):
            headers["If-None-Match"] = prev["etag"]
        if path.exists() and prev.get("last_modified"):
            headers["If-Modified-Since"] = prev["last_modified"]
        r = client.get(item["url"], headers=headers)
        if r.status_code == 304:
            out.append((_spec(item, path, prev), False))
            continue
        r.raise_for_status()
        path.write_bytes(r.content)
        meta = {
            "via": "site",
            "wayback_timestamp": None,
            "etag": r.headers.get("etag"),
            "last_modified": r.headers.get("last-modified"),
        }
        log.info("baixado %s (%d KB)", path.name, len(r.content) // 1024)
        out.append((_spec(item, path, meta), True))
    return out
