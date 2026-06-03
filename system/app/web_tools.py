"""Key-free web search and page-read helpers for Owner Mode tools."""
from __future__ import annotations

import contextlib
import html
import ipaddress
import json
import re
import socket
import time
import urllib.error
import urllib.parse
import urllib.request
from email.message import Message
from html.parser import HTMLParser
from typing import Any

from .config import Settings, get_settings

WEB_USER_AGENT = (
    "Mozilla/5.0 (Macintosh; Intel Mac OS X 14_7_2) "
    "AppleWebKit/537.36 (KHTML, like Gecko) Chrome/122.0.0.0 Safari/537.36"
)
DEFAULT_ACCEPT_LANGUAGE = "en-US,en;q=0.9,th;q=0.8"
DEFAULT_SEARCH_COUNT = 5
MAX_SEARCH_COUNT = 10
DEFAULT_FETCH_MAX_BYTES = 750_000
MAX_FETCH_MAX_BYTES = 10_000_000
DEFAULT_FETCH_MAX_CHARS = 20_000
MAX_FETCH_MAX_CHARS = 120_000
DEFAULT_MAX_REDIRECTS = 3
SEARCH_CACHE: dict[str, tuple[float, dict[str, Any]]] = {}


class _NoRedirect(urllib.request.HTTPRedirectHandler):
    def redirect_request(self, req, fp, code, msg, headers, newurl):  # type: ignore[no-untyped-def]
        return None


class _ReadableHtmlParser(HTMLParser):
    _BLOCK_TAGS = {
        "article",
        "aside",
        "blockquote",
        "br",
        "dd",
        "div",
        "dl",
        "dt",
        "figcaption",
        "footer",
        "h1",
        "h2",
        "h3",
        "h4",
        "h5",
        "h6",
        "header",
        "hr",
        "li",
        "main",
        "nav",
        "ol",
        "p",
        "pre",
        "section",
        "table",
        "td",
        "th",
        "tr",
        "ul",
    }
    _SKIP_TAGS = {"script", "style", "svg", "canvas", "noscript", "template"}

    def __init__(self, *, base_url: str, image_limit: int, link_limit: int) -> None:
        super().__init__(convert_charrefs=True)
        self.base_url = base_url
        self.image_limit = max(0, image_limit)
        self.link_limit = max(0, link_limit)
        self.parts: list[str] = []
        self.images: list[dict[str, Any]] = []
        self.links: list[dict[str, Any]] = []
        self.title_parts: list[str] = []
        self._title_depth = 0
        self._skip_depth = 0

    def handle_starttag(self, tag: str, attrs: list[tuple[str, str | None]]) -> None:
        tag = tag.lower()
        if tag in self._SKIP_TAGS:
            self._skip_depth += 1
            return
        if tag == "title":
            self._title_depth += 1
        if self._skip_depth:
            return
        attr = {key.lower(): value or "" for key, value in attrs}
        if tag in self._BLOCK_TAGS:
            self.parts.append("\n")
        if tag == "img" and len(self.images) < self.image_limit:
            raw_src = (
                attr.get("src")
                or attr.get("data-src")
                or attr.get("data-original")
                or attr.get("data-lazy-src")
            )
            src = _absolute_public_link(self.base_url, raw_src)
            if src:
                self.images.append({
                    "url": src,
                    "alt": _compact_space(attr.get("alt", ""))[:240],
                    "title": _compact_space(attr.get("title", ""))[:240],
                    "width": attr.get("width") or None,
                    "height": attr.get("height") or None,
                })
        if tag == "a" and len(self.links) < self.link_limit:
            href = _absolute_public_link(self.base_url, attr.get("href"))
            if href:
                self.links.append({"url": href})

    def handle_endtag(self, tag: str) -> None:
        tag = tag.lower()
        if tag in self._SKIP_TAGS and self._skip_depth:
            self._skip_depth -= 1
            return
        if tag == "title" and self._title_depth:
            self._title_depth -= 1
        if not self._skip_depth and tag in self._BLOCK_TAGS:
            self.parts.append("\n")

    def handle_data(self, data: str) -> None:
        if self._skip_depth:
            return
        if self._title_depth:
            self.title_parts.append(data)
            return
        self.parts.append(data)

    def output(self) -> tuple[str, str]:
        return _normalize_readable_text("".join(self.title_parts)), _normalize_readable_text(
            "".join(self.parts)
        )


def _settings() -> Settings:
    return get_settings()


def _compact_space(value: str) -> str:
    return re.sub(r"\s+", " ", value or "").strip()


def _normalize_readable_text(value: str) -> str:
    lines = [_compact_space(line) for line in re.split(r"[\r\n]+", html.unescape(value or ""))]
    out: list[str] = []
    blank = False
    for line in lines:
        if not line:
            if out and not blank:
                out.append("")
            blank = True
            continue
        out.append(line)
        blank = False
    return "\n".join(out).strip()


def _clip(text: str, limit: int) -> tuple[str, bool]:
    if limit <= 0 or len(text) <= limit:
        return text, False
    return text[: max(0, limit - 16)].rstrip() + "\n...[truncated]", True


def _int_arg(args: dict[str, Any], name: str, default: int, *, min_value: int, max_value: int) -> int:
    raw = args.get(name)
    if raw is None:
        raw = args.get(_camel_to_snake(name))
    try:
        value = int(raw) if raw is not None else default
    except (TypeError, ValueError):
        value = default
    return max(min_value, min(value, max_value))


def _float_arg(args: dict[str, Any], name: str, default: float, *, min_value: float, max_value: float) -> float:
    raw = args.get(name)
    if raw is None:
        raw = args.get(_camel_to_snake(name))
    try:
        value = float(raw) if raw is not None else default
    except (TypeError, ValueError):
        value = default
    return max(min_value, min(value, max_value))


def _bool_arg(args: dict[str, Any], name: str, default: bool = False) -> bool:
    raw = args.get(name)
    if raw is None:
        raw = args.get(_camel_to_snake(name))
    if isinstance(raw, bool):
        return raw
    if isinstance(raw, str):
        lowered = raw.strip().lower()
        if lowered in {"1", "true", "yes", "y", "on"}:
            return True
        if lowered in {"0", "false", "no", "n", "off"}:
            return False
    return default


def _str_arg(args: dict[str, Any], name: str, default: str = "") -> str:
    raw = args.get(name)
    if raw is None:
        raw = args.get(_camel_to_snake(name))
    return str(raw or default).strip()


def _camel_to_snake(value: str) -> str:
    return re.sub(r"(?<!^)([A-Z])", r"_\1", value).lower()


def _is_blocked_host_name(host: str) -> bool:
    lowered = host.strip(".").lower()
    return (
        lowered in {"localhost", "metadata.google.internal"}
        or lowered.endswith(".localhost")
        or lowered.endswith(".local")
        or lowered.endswith(".internal")
    )


def _ip_is_private(value: str) -> bool:
    try:
        ip = ipaddress.ip_address(value.strip("[]"))
    except ValueError:
        return False
    return any(
        (
            ip.is_loopback,
            ip.is_private,
            ip.is_link_local,
            ip.is_reserved,
            ip.is_multicast,
            ip.is_unspecified,
        )
    )


def _assert_http_url(raw_url: str, *, allow_private_hosts: bool = False) -> str:
    url = str(raw_url or "").strip()
    parsed = urllib.parse.urlparse(url)
    if parsed.scheme not in {"http", "https"} or not parsed.netloc:
        raise ValueError("web tool requires an http(s) URL")
    host = parsed.hostname or ""
    if not allow_private_hosts:
        if _is_blocked_host_name(host) or _ip_is_private(host):
            raise ValueError("web tool blocks localhost, private-network, and metadata URLs")
        try:
            infos = socket.getaddrinfo(host, None, type=socket.SOCK_STREAM)
        except socket.gaierror as exc:
            raise ValueError(f"cannot resolve URL host: {host}") from exc
        for info in infos:
            address = info[4][0]
            if _ip_is_private(address):
                raise ValueError("web tool blocks hosts resolving to private-network addresses")
    return urllib.parse.urlunparse(parsed)


def _absolute_public_link(base_url: str, raw: str | None) -> str | None:
    if not raw:
        return None
    value = html.unescape(raw.strip())
    if not value or value.startswith(("data:", "javascript:", "mailto:", "tel:")):
        return None
    try:
        absolute = urllib.parse.urljoin(base_url, value)
        parsed = urllib.parse.urlparse(absolute)
        if parsed.scheme not in {"http", "https"} or not parsed.netloc:
            return None
        return absolute
    except Exception:
        return None


def _headers(raw: Any = None) -> dict[str, str]:
    headers = {
        "User-Agent": WEB_USER_AGENT,
        "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,application/json;q=0.8,*/*;q=0.5",
        "Accept-Language": DEFAULT_ACCEPT_LANGUAGE,
    }
    if isinstance(raw, dict):
        for key, value in raw.items():
            if isinstance(key, str) and isinstance(value, str):
                headers[key] = value
    return headers


def _read_limited(response: Any, max_bytes: int) -> bytes:
    data = response.read(max_bytes + 1)
    if len(data) > max_bytes:
        raise ValueError(f"response exceeds maxBytes={max_bytes}")
    return data


def _bounded_get(
    url: str,
    *,
    timeout: float,
    max_bytes: int,
    max_redirects: int = DEFAULT_MAX_REDIRECTS,
    headers: dict[str, str] | None = None,
    allow_private_hosts: bool = False,
) -> dict[str, Any]:
    current = _assert_http_url(url, allow_private_hosts=allow_private_hosts)
    opener = urllib.request.build_opener(_NoRedirect)
    redirects: list[str] = []
    for _ in range(max(0, max_redirects) + 1):
        req = urllib.request.Request(current, headers=headers or _headers(), method="GET")
        try:
            with opener.open(req, timeout=timeout) as res:
                data = _read_limited(res, max_bytes)
                return {
                    "url": res.geturl(),
                    "status": int(res.status),
                    "headers": res.headers,
                    "body": data,
                    "redirects": redirects,
                }
        except urllib.error.HTTPError as exc:
            if exc.code in {301, 302, 303, 307, 308}:
                location = exc.headers.get("Location")
                if not location:
                    raise ValueError(f"redirect from {current} did not include Location")
                next_url = urllib.parse.urljoin(current, location)
                current = _assert_http_url(next_url, allow_private_hosts=allow_private_hosts)
                redirects.append(current)
                continue
            data = _read_limited(exc, max_bytes)
            return {
                "url": current,
                "status": int(exc.code),
                "headers": exc.headers,
                "body": data,
                "redirects": redirects,
            }
    raise ValueError(f"too many redirects after {max_redirects}")


def _content_type(headers: Message | dict[str, Any] | None) -> str:
    if headers is None:
        return ""
    if isinstance(headers, Message):
        return headers.get("content-type", "")
    return str(headers.get("content-type") or headers.get("Content-Type") or "")


def _decode_body(body: bytes, headers: Message | dict[str, Any] | None) -> str:
    charset = None
    if isinstance(headers, Message):
        charset = headers.get_content_charset()
    return body.decode(charset or "utf-8", errors="replace")


def _cache_get(key: str, ttl_s: int) -> dict[str, Any] | None:
    if ttl_s <= 0:
        return None
    cached = SEARCH_CACHE.get(key)
    if not cached:
        return None
    inserted, value = cached
    if time.time() - inserted > ttl_s:
        SEARCH_CACHE.pop(key, None)
        return None
    return {**value, "cached": True}


def _cache_put(key: str, value: dict[str, Any], ttl_s: int) -> None:
    if ttl_s > 0:
        SEARCH_CACHE[key] = (time.time(), value)


def _duckduckgo_url(raw_url: str) -> str:
    try:
        normalized = f"https:{raw_url}" if raw_url.startswith("//") else raw_url
        parsed = urllib.parse.urlparse(normalized)
        uddg = urllib.parse.parse_qs(parsed.query).get("uddg")
        if uddg and uddg[0]:
            return uddg[0]
    except Exception:
        pass
    return raw_url


def _parse_duckduckgo_html(markup: str) -> list[dict[str, str]]:
    results: list[dict[str, str]] = []
    result_re = re.compile(
        r'<a\b(?=[^>]*\bclass="[^"]*\bresult__a\b[^"]*")([^>]*)>([\s\S]*?)</a>',
        re.IGNORECASE,
    )
    next_result_re = re.compile(
        r'<a\b(?=[^>]*\bclass="[^"]*\bresult__a\b[^"]*")[^>]*>',
        re.IGNORECASE,
    )
    snippet_re = re.compile(
        r'<a\b(?=[^>]*\bclass="[^"]*\bresult__snippet\b[^"]*")[^>]*>([\s\S]*?)</a>',
        re.IGNORECASE,
    )
    href_re = re.compile(r'\bhref="([^"]*)"', re.IGNORECASE)
    for match in result_re.finditer(markup):
        attrs = match.group(1) or ""
        title_html = match.group(2) or ""
        href_match = href_re.search(attrs)
        raw_url = href_match.group(1) if href_match else ""
        trailing = markup[match.end() :]
        next_match = next_result_re.search(trailing)
        scoped = trailing[: next_match.start()] if next_match else trailing
        snippet_match = snippet_re.search(scoped)
        title = _normalize_readable_text(re.sub(r"<[^>]+>", " ", title_html))
        snippet = _normalize_readable_text(
            re.sub(r"<[^>]+>", " ", snippet_match.group(1) if snippet_match else "")
        )
        url = _duckduckgo_url(html.unescape(raw_url))
        if title and url:
            results.append({"title": title, "url": url, "description": snippet})
    return results


def _duckduckgo_search(args: dict[str, Any], settings: Settings, *, count: int, timeout: float) -> dict[str, Any]:
    query = _str_arg(args, "query")
    if not query:
        raise ValueError("web.search requires query")
    safe_search = _str_arg(args, "safeSearch", "moderate").lower()
    safe_param = {"strict": "1", "moderate": "-1", "off": "-2"}.get(safe_search, "-1")
    region = _str_arg(args, "region") or _str_arg(args, "country")
    url = "https://html.duckduckgo.com/html?" + urllib.parse.urlencode({"q": query, "kp": safe_param})
    if region:
        url += "&" + urllib.parse.urlencode({"kl": region})
    cache_key = json.dumps({"provider": "duckduckgo", "query": query, "count": count, "region": region, "safe": safe_param}, sort_keys=True)
    ttl_s = max(0, int(settings.web_search_cache_ttl_s))
    cached = _cache_get(cache_key, ttl_s)
    if cached:
        return cached
    started = time.time()
    response = _bounded_get(
        url,
        timeout=timeout,
        max_bytes=1_000_000,
        headers=_headers(),
        allow_private_hosts=False,
    )
    markup = _decode_body(response["body"], response["headers"])
    if re.search(r"g-recaptcha|are you a human|id=\"challenge-form\"|name=\"challenge\"", markup, re.I):
        raise ValueError("DuckDuckGo returned a bot-detection challenge")
    results = _parse_duckduckgo_html(markup)[:count]
    payload = _search_payload(
        provider="duckduckgo",
        query=query,
        results=results,
        took_ms=int((time.time() - started) * 1000),
    )
    _cache_put(cache_key, payload, ttl_s)
    return payload


def _searxng_base_url(args: dict[str, Any], settings: Settings) -> tuple[str, bool]:
    configured = str(settings.web_search_searxng_url or "").strip()
    raw = _str_arg(args, "searxngUrl") or _str_arg(args, "baseUrl") or configured
    if not raw:
        raise ValueError("SearXNG base URL is not configured")
    return raw.rstrip("/"), bool(configured and raw.rstrip("/") == configured.rstrip("/"))


def _searxng_search(args: dict[str, Any], settings: Settings, *, count: int, timeout: float) -> dict[str, Any]:
    query = _str_arg(args, "query")
    if not query:
        raise ValueError("web.search requires query")
    base_url, from_settings = _searxng_base_url(args, settings)
    params = {
        "q": query,
        "format": "json",
        "pageno": "1",
    }
    for arg_name, param_name in (("categories", "categories"), ("language", "language"), ("engines", "engines")):
        value = _str_arg(args, arg_name)
        if value:
            params[param_name] = value
    url = f"{base_url}/search?{urllib.parse.urlencode(params)}"
    cache_key = json.dumps({"provider": "searxng", "url": base_url, "params": params, "count": count}, sort_keys=True)
    ttl_s = max(0, int(settings.web_search_cache_ttl_s))
    cached = _cache_get(cache_key, ttl_s)
    if cached:
        return cached
    started = time.time()
    response = _bounded_get(
        url,
        timeout=timeout,
        max_bytes=1_500_000,
        headers=_headers({"Accept": "application/json"}),
        allow_private_hosts=from_settings or _bool_arg(args, "allowPrivateHosts"),
    )
    text = _decode_body(response["body"], response["headers"])
    if response["status"] >= 400:
        raise ValueError(f"SearXNG returned HTTP {response['status']}: {_clip(text, 500)[0]}")
    try:
        payload = json.loads(text)
    except json.JSONDecodeError as exc:
        raise ValueError("SearXNG did not return JSON; enable json in search.formats") from exc
    raw_results = payload.get("results") if isinstance(payload, dict) else []
    results: list[dict[str, Any]] = []
    if isinstance(raw_results, list):
        for index, item in enumerate(raw_results):
            if not isinstance(item, dict):
                continue
            title = _compact_space(str(item.get("title") or ""))
            result_url = _compact_space(str(item.get("url") or ""))
            description = _compact_space(str(item.get("content") or item.get("description") or ""))
            if title and result_url:
                results.append({
                    "title": title,
                    "url": result_url,
                    "description": description,
                    "position": index + 1,
                    "score": item.get("score"),
                    "engine": item.get("engine") or item.get("engines"),
                })
            if len(results) >= count:
                break
    out = _search_payload(
        provider="searxng",
        query=query,
        results=results,
        took_ms=int((time.time() - started) * 1000),
    )
    _cache_put(cache_key, out, ttl_s)
    return out


def _search_payload(*, provider: str, query: str, results: list[dict[str, Any]], took_ms: int) -> dict[str, Any]:
    normalized = []
    for index, item in enumerate(results):
        url = str(item.get("url") or "")
        parsed = urllib.parse.urlparse(url)
        normalized.append({
            "title": str(item.get("title") or ""),
            "url": url,
            "description": str(item.get("description") or item.get("snippet") or ""),
            "position": int(item.get("position") or index + 1),
            "siteName": parsed.netloc or None,
            **({"score": item.get("score")} if item.get("score") is not None else {}),
            **({"engine": item.get("engine")} if item.get("engine") else {}),
        })
    return {
        "ok": True,
        "provider": provider,
        "query": query,
        "count": len(normalized),
        "tookMs": took_ms,
        "results": normalized,
        "externalContent": {
            "untrusted": True,
            "source": "web.search",
            "provider": provider,
        },
    }


def execute_web_search(args: dict[str, Any]) -> dict[str, Any]:
    settings = _settings()
    count = _int_arg(args, "count", DEFAULT_SEARCH_COUNT, min_value=1, max_value=MAX_SEARCH_COUNT)
    timeout = _float_arg(args, "timeoutSeconds", settings.web_search_timeout_s, min_value=1.0, max_value=30.0)
    provider = (_str_arg(args, "provider") or settings.web_search_provider or "auto").lower()
    if provider in {"auto", "keyfree", "key-free"}:
        errors: list[str] = []
        if settings.web_search_searxng_url:
            try:
                result = _searxng_search(args, settings, count=count, timeout=timeout)
                result["fallbackErrors"] = errors
                return result
            except Exception as exc:
                errors.append(f"searxng: {exc}")
        try:
            result = _duckduckgo_search(args, settings, count=count, timeout=timeout)
            result["fallbackErrors"] = errors
            return result
        except Exception as exc:
            errors.append(f"duckduckgo: {exc}")
            raise ValueError("; ".join(errors)) from exc
    if provider in {"duckduckgo", "ddg"}:
        return _duckduckgo_search(args, settings, count=count, timeout=timeout)
    if provider in {"searxng", "searx"}:
        return _searxng_search(args, settings, count=count, timeout=timeout)
    raise ValueError("web.search provider must be auto, duckduckgo, or searxng")


def execute_web_fetch(args: dict[str, Any]) -> dict[str, Any]:
    settings = _settings()
    url = _str_arg(args, "url")
    if not url:
        raise ValueError("web.fetch requires url")
    timeout = _float_arg(args, "timeoutSeconds", settings.web_fetch_timeout_s, min_value=1.0, max_value=60.0)
    max_bytes = _int_arg(
        args,
        "maxBytes",
        settings.web_fetch_max_bytes,
        min_value=32_000,
        max_value=MAX_FETCH_MAX_BYTES,
    )
    max_chars = _int_arg(
        args,
        "maxChars",
        settings.web_fetch_max_chars,
        min_value=100,
        max_value=MAX_FETCH_MAX_CHARS,
    )
    image_limit = _int_arg(args, "imageLimit", 12, min_value=0, max_value=50)
    link_limit = _int_arg(args, "linkLimit", 0, min_value=0, max_value=100)
    response = _bounded_get(
        url,
        timeout=timeout,
        max_bytes=max_bytes,
        max_redirects=_int_arg(args, "maxRedirects", DEFAULT_MAX_REDIRECTS, min_value=0, max_value=10),
        headers=_headers(args.get("headers")),
        allow_private_hosts=_bool_arg(args, "allowPrivateHosts"),
    )
    content_type = _content_type(response["headers"])
    body = response["body"]
    text = _decode_body(body, response["headers"])
    title = ""
    images: list[dict[str, Any]] = []
    links: list[dict[str, Any]] = []
    if content_type.lower().startswith("image/"):
        extracted = ""
        images = [{"url": response["url"], "contentType": content_type, "bytes": len(body)}]
    elif "html" in content_type.lower() or text.lstrip().lower().startswith(("<!doctype html", "<html")):
        parser = _ReadableHtmlParser(base_url=response["url"], image_limit=image_limit, link_limit=link_limit)
        parser.feed(text)
        title, extracted = parser.output()
        images = parser.images
        links = parser.links
    elif content_type.lower().startswith("application/json"):
        with contextlib.suppress(json.JSONDecodeError):
            text = json.dumps(json.loads(text), ensure_ascii=False, indent=2)
        extracted = text
    else:
        extracted = text
    clipped, truncated = _clip(extracted, max_chars)
    return {
        "ok": response["status"] < 400,
        "url": response["url"],
        "status": response["status"],
        "contentType": content_type,
        "title": title,
        "text": clipped,
        "truncated": truncated,
        "rawChars": len(extracted),
        "bytes": len(body),
        "redirects": response.get("redirects") or [],
        "images": images,
        "links": links,
        "externalContent": {
            "untrusted": True,
            "source": "web.fetch",
        },
    }
