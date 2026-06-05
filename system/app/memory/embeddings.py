"""Embeddings for RAG.

Claude has no embeddings endpoint (REQUIREMENTS §4.4), so ATRIUM uses a
separate embedding layer. The owner can choose local Ollama, OpenAI Platform,
Voyage, or the deterministic hash fallback. All paths return L2-normalized
vectors so cosine comparison remains consistent.
"""
from __future__ import annotations

import hashlib
import math
import re
from typing import Protocol

import httpx

from ..config import Settings, get_settings

_TOKEN_RE = re.compile(r"[0-9A-Za-z฀-๿]+")


def _tokens(text: str) -> list[str]:
    return _TOKEN_RE.findall(text.lower())


def _normalize(vec: list[float]) -> list[float]:
    n = math.sqrt(sum(v * v for v in vec))
    return [v / n for v in vec] if n else vec


class Embedder(Protocol):
    name: str
    dim: int

    async def embed(self, texts: list[str]) -> list[list[float]]: ...


def embedding_metadata(embedder: Embedder, vector: list[float] | None = None) -> dict[str, int | str | None]:
    name = str(getattr(embedder, "name", "") or "")
    model = str(getattr(embedder, "model", "") or name)
    dim = len(vector) if vector else int(getattr(embedder, "dim", 0) or 0)
    return {
        "provider": name or None,
        "model": model or None,
        "dim": dim or None,
    }


class HashEmbedder:
    """Deterministic bag-of-words hashing embedder — no network, stable across runs.

    Each token is hashed into the vector with a signed weight; Thai is tokenized
    by character n-grams so substring overlap still produces similarity.
    """

    def __init__(self, dim: int = 256):
        self.dim = dim
        self.name = f"hash-{dim}"

    def _embed_one(self, text: str) -> list[float]:
        vec = [0.0] * self.dim
        toks = _tokens(text)
        grams: list[str] = list(toks)
        # add char trigrams to give Thai (no spaces) some shared structure
        joined = "".join(toks)
        for i in range(len(joined) - 2):
            grams.append(joined[i : i + 3])
        if not grams:
            return vec
        for g in grams:
            h = int.from_bytes(hashlib.md5(g.encode("utf-8")).digest()[:8], "little")
            idx = h % self.dim
            sign = 1.0 if (h >> 63) & 1 else -1.0
            vec[idx] += sign
        return _normalize(vec)

    async def embed(self, texts: list[str]) -> list[list[float]]:
        return [self._embed_one(t) for t in texts]


class OllamaEmbedder:
    """Local multilingual embeddings via Ollama (default model: bge-m3, dim 1024)."""

    def __init__(
        self,
        base_url: str,
        model: str,
        dim: int = 1024,
        timeout: float = 60.0,
        transport: httpx.AsyncBaseTransport | None = None,
    ):
        self.base_url = base_url.rstrip("/")
        self.model = model
        self.dim = dim
        self.timeout = timeout
        self.name = f"ollama:{model}"
        self._transport = transport

    async def embed(self, texts: list[str]) -> list[list[float]]:
        if not texts:
            return []
        payload = {"model": self.model, "input": texts[0] if len(texts) == 1 else texts}
        async with httpx.AsyncClient(timeout=self.timeout, transport=self._transport) as client:
            resp = await client.post(f"{self.base_url}/api/embed", json=payload)
            resp.raise_for_status()
            data = resp.json()
        embeddings = data.get("embeddings")
        if isinstance(embeddings, list):
            out = [_normalize([float(v) for v in row]) for row in embeddings if isinstance(row, list)]
            if out:
                self.dim = len(out[0])
            if len(out) == len(texts):
                return out
        if isinstance(data.get("embedding"), list):
            vec = _normalize([float(v) for v in data["embedding"]])
            self.dim = len(vec)
            return [vec]
        raise ValueError("Ollama embeddings response missing embedding(s)")


class VoyageEmbedder:
    def __init__(
        self,
        api_key: str,
        model: str,
        base_url: str,
        timeout: float = 30.0,
        transport: httpx.AsyncBaseTransport | None = None,
    ):
        self.api_key = api_key
        self.model = model
        self.base_url = base_url.rstrip("/")
        self.timeout = timeout
        self.dim = 1024  # voyage-3.5 default output dimension
        self.name = f"voyage:{model}"
        self._transport = transport

    async def embed(self, texts: list[str]) -> list[list[float]]:
        if not texts:
            return []
        async with httpx.AsyncClient(timeout=self.timeout, transport=self._transport) as client:
            resp = await client.post(
                f"{self.base_url}/embeddings",
                headers={"Authorization": f"Bearer {self.api_key}", "Content-Type": "application/json"},
                json={"input": texts, "model": self.model},
            )
            resp.raise_for_status()
            data = resp.json()
        rows = data.get("data")
        if not isinstance(rows, list):
            raise ValueError("Voyage embeddings response missing data list")
        out = []
        for item in sorted(rows, key=lambda x: x.get("index", 0) if isinstance(x, dict) else 0):
            if not isinstance(item, dict) or not isinstance(item.get("embedding"), list):
                raise ValueError("Voyage embeddings response contains invalid embedding row")
            out.append([float(v) for v in item["embedding"]])
        if len(out) != len(texts):
            raise ValueError(f"Voyage embeddings response returned {len(out)} vectors for {len(texts)} inputs")
        if out:
            self.dim = len(out[0])
        return [_normalize(v) for v in out]


class OpenAIEmbedder:
    def __init__(
        self,
        api_key: str,
        model: str,
        base_url: str,
        *,
        dimensions: int = 1024,
        timeout: float = 30.0,
        transport: httpx.AsyncBaseTransport | None = None,
    ):
        self.api_key = api_key
        self.model = model
        self.base_url = base_url.rstrip("/")
        self.dimensions = max(0, int(dimensions or 0))
        self.timeout = timeout
        self.dim = self.dimensions or (3072 if model == "text-embedding-3-large" else 1536)
        self.name = f"openai:{model}"
        self._transport = transport

    async def embed(self, texts: list[str]) -> list[list[float]]:
        if not texts:
            return []
        payload: dict[str, object] = {
            "input": texts,
            "model": self.model,
            "encoding_format": "float",
        }
        if self.dimensions > 0:
            payload["dimensions"] = self.dimensions
        async with httpx.AsyncClient(timeout=self.timeout, transport=self._transport) as client:
            resp = await client.post(
                f"{self.base_url}/embeddings",
                headers={"Authorization": f"Bearer {self.api_key}", "Content-Type": "application/json"},
                json=payload,
            )
            resp.raise_for_status()
            data = resp.json()
        rows = data.get("data")
        if not isinstance(rows, list):
            raise ValueError("OpenAI embeddings response missing data list")
        out = []
        for item in sorted(rows, key=lambda x: x.get("index", 0) if isinstance(x, dict) else 0):
            if not isinstance(item, dict) or not isinstance(item.get("embedding"), list):
                raise ValueError("OpenAI embeddings response contains invalid embedding row")
            out.append([float(v) for v in item["embedding"]])
        if len(out) != len(texts):
            raise ValueError(f"OpenAI embeddings response returned {len(out)} vectors for {len(texts)} inputs")
        if out:
            self.dim = len(out[0])
        return [_normalize(v) for v in out]


_embedder: Embedder | None = None


async def ollama_reachable(settings: Settings | None = None) -> bool:
    settings = settings or get_settings()
    base = settings.ollama_base_url.strip()
    if not base:
        return False
    try:
        async with httpx.AsyncClient(timeout=3.0) as client:
            resp = await client.get(f"{base.rstrip('/')}/api/tags")
            return resp.status_code < 500
    except Exception:
        return False


def get_embedder(settings: Settings | None = None) -> Embedder:
    global _embedder
    if _embedder is not None:
        return _embedder
    settings = settings or get_settings()
    mode = settings.embedding_provider_mode
    if mode == "openai":
        api_key = settings.effective_openai_embedding_api_key
        if not api_key:
            raise RuntimeError("OpenAI embeddings require ATRIUM_OPENAI_API_KEY or ATRIUM_OPENAI_EMBEDDING_API_KEY")
        _embedder = OpenAIEmbedder(
            api_key,
            settings.openai_embedding_model,
            settings.effective_openai_embedding_base_url,
            dimensions=settings.effective_openai_embedding_dimensions,
        )
    elif mode == "local":
        _embedder = OllamaEmbedder(
            settings.ollama_base_url,
            settings.ollama_embedding_model,
            dim=settings.embedding_dim,
        )
    elif mode == "voyage":
        if not settings.voyage_api_key:
            raise RuntimeError("Voyage embeddings require VOYAGE_API_KEY or ATRIUM_VOYAGE_API_KEY")
        _embedder = VoyageEmbedder(settings.voyage_api_key, settings.voyage_model, settings.voyage_base_url)
    elif mode == "hash":
        _embedder = HashEmbedder(settings.embedding_dim)
    elif settings.prefer_ollama_embeddings:
        _embedder = OllamaEmbedder(
            settings.ollama_base_url,
            settings.ollama_embedding_model,
            dim=settings.embedding_dim,
        )
    elif settings.live_embeddings:
        _embedder = VoyageEmbedder(settings.voyage_api_key, settings.voyage_model, settings.voyage_base_url)
    else:
        _embedder = HashEmbedder(settings.embedding_dim)
    return _embedder


async def resolve_embedder(settings: Settings | None = None) -> Embedder:
    """Resolve the configured embedder.

    auto preserves legacy behavior: reachable Ollama → Voyage → hash. Explicit
    modes do not silently switch to a paid or remote provider.
    """
    settings = settings or get_settings()
    mode = settings.embedding_provider_mode
    if mode == "openai":
        api_key = settings.effective_openai_embedding_api_key
        if not api_key:
            raise RuntimeError("OpenAI embeddings require ATRIUM_OPENAI_API_KEY or ATRIUM_OPENAI_EMBEDDING_API_KEY")
        return OpenAIEmbedder(
            api_key,
            settings.openai_embedding_model,
            settings.effective_openai_embedding_base_url,
            dimensions=settings.effective_openai_embedding_dimensions,
        )
    if mode == "local":
        return OllamaEmbedder(
            settings.ollama_base_url,
            settings.ollama_embedding_model,
            dim=settings.embedding_dim,
        )
    if mode == "voyage":
        if not settings.voyage_api_key:
            raise RuntimeError("Voyage embeddings require VOYAGE_API_KEY or ATRIUM_VOYAGE_API_KEY")
        return VoyageEmbedder(settings.voyage_api_key, settings.voyage_model, settings.voyage_base_url)
    if mode == "hash":
        return HashEmbedder(settings.embedding_dim)
    if settings.prefer_ollama_embeddings and await ollama_reachable(settings):
        return OllamaEmbedder(
            settings.ollama_base_url,
            settings.ollama_embedding_model,
            dim=settings.embedding_dim,
        )
    if settings.live_embeddings:
        return VoyageEmbedder(settings.voyage_api_key, settings.voyage_model, settings.voyage_base_url)
    return HashEmbedder(settings.embedding_dim)


def reset_embedder() -> None:
    global _embedder
    _embedder = None
