"""Content-addressed object store for artifact blobs (ATRIUM v2 Phase 0)."""
from __future__ import annotations

import hashlib
import os
import re
import shutil
import tempfile
from dataclasses import dataclass
from pathlib import Path

from ..config import Settings, get_settings

SHA256_RE = re.compile(r"^[0-9a-f]{64}$")


@dataclass(frozen=True)
class StoredObject:
    content_hash: str
    path: Path
    size_bytes: int
    mime: str = "text/plain; charset=utf-8"

    @property
    def uri(self) -> str:
        return f"atrium-object://{self.content_hash}"


class ObjectStoreError(RuntimeError):
    """Base error for content-addressed object store failures."""


class ObjectStoreIntegrityError(ObjectStoreError):
    """Raised when a blob path exists but its bytes do not match its digest."""


def content_hash_bytes(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()


def content_hash_text(text: str) -> str:
    return content_hash_bytes(text.encode("utf-8"))


def content_hash_file(path: Path) -> str:
    h = hashlib.sha256()
    with Path(path).open("rb") as fh:
        for chunk in iter(lambda: fh.read(1024 * 1024), b""):
            h.update(chunk)
    return h.hexdigest()


class ObjectStore:
    def __init__(self, root: Path | None = None):
        settings = get_settings()
        self.root = (root or settings.object_store_dir).resolve()
        self.root.mkdir(parents=True, exist_ok=True)

    def _validate_digest(self, digest: str) -> str:
        digest = str(digest or "").strip().lower()
        if not SHA256_RE.fullmatch(digest):
            raise ValueError("invalid object digest")
        return digest

    def _shard_path(self, digest: str) -> Path:
        digest = self._validate_digest(digest)
        return self.root / digest[:2] / digest[2:]

    def _atomic_write(self, path: Path, data: bytes) -> None:
        path.parent.mkdir(parents=True, exist_ok=True)
        fd, tmp_name = tempfile.mkstemp(prefix=f".{path.name}.", suffix=".tmp", dir=str(path.parent))
        tmp_path = Path(tmp_name)
        try:
            with os.fdopen(fd, "wb") as fh:
                fh.write(data)
                fh.flush()
                os.fsync(fh.fileno())
            os.replace(tmp_path, path)
        finally:
            if tmp_path.exists():
                tmp_path.unlink()

    def put_bytes(self, data: bytes, *, mime: str = "application/octet-stream") -> StoredObject:
        digest = content_hash_bytes(data)
        path = self._shard_path(digest)
        if path.exists():
            existing = path.read_bytes()
            if content_hash_bytes(existing) == digest:
                return StoredObject(content_hash=digest, path=path, size_bytes=len(data), mime=mime)
        self._atomic_write(path, data)
        return StoredObject(content_hash=digest, path=path, size_bytes=len(data), mime=mime)

    def put_text(self, text: str, *, mime: str = "text/plain; charset=utf-8") -> StoredObject:
        data = text.encode("utf-8")
        return self.put_bytes(data, mime=mime)

    def put_file(self, path: Path, *, mime: str = "application/octet-stream") -> StoredObject:
        source = Path(path).resolve()
        if not source.is_file():
            raise FileNotFoundError(str(source))
        digest = content_hash_file(source)
        target = self._shard_path(digest)
        size = source.stat().st_size
        if target.exists() and content_hash_file(target) == digest:
            return StoredObject(content_hash=digest, path=target, size_bytes=size, mime=mime)
        target.parent.mkdir(parents=True, exist_ok=True)
        fd, tmp_name = tempfile.mkstemp(prefix=f".{target.name}.", suffix=".tmp", dir=str(target.parent))
        tmp_path = Path(tmp_name)
        try:
            with os.fdopen(fd, "wb") as out_fh, source.open("rb") as in_fh:
                shutil.copyfileobj(in_fh, out_fh, length=1024 * 1024)
                out_fh.flush()
                os.fsync(out_fh.fileno())
            os.replace(tmp_path, target)
        finally:
            if tmp_path.exists():
                tmp_path.unlink()
        return StoredObject(content_hash=digest, path=target, size_bytes=size, mime=mime)

    def get_bytes(self, content_hash: str) -> bytes | None:
        digest = self._validate_digest(content_hash)
        path = self._shard_path(digest)
        if not path.is_file():
            return None
        data = path.read_bytes()
        actual = content_hash_bytes(data)
        if actual != digest:
            raise ObjectStoreIntegrityError(f"object digest mismatch: expected {digest}, got {actual}")
        return data

    def get_text(self, content_hash: str) -> str | None:
        raw = self.get_bytes(content_hash)
        return raw.decode("utf-8") if raw is not None else None

    def resolve_uri(self, uri: str) -> Path | None:
        if uri.startswith("atrium-object://"):
            digest = self._validate_digest(uri.removeprefix("atrium-object://"))
            path = self._shard_path(digest)
            return path if path.is_file() else None
        return None

    def verify_uri(self, uri: str) -> StoredObject | None:
        if not uri.startswith("atrium-object://"):
            return None
        digest = self._validate_digest(uri.removeprefix("atrium-object://"))
        data = self.get_bytes(digest)
        if data is None:
            return None
        return StoredObject(content_hash=digest, path=self._shard_path(digest), size_bytes=len(data))


def get_object_store(settings: Settings | None = None) -> ObjectStore:
    settings = settings or get_settings()
    return ObjectStore(settings.object_store_dir)
