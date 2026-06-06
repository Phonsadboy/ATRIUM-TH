"""File upload, extraction, and attachment helpers.

The system stores original bytes for every upload, then creates the richest
text preview it can without making heavyweight document libraries mandatory.
Unsupported binaries still become durable artifacts with useful metadata.
"""
from __future__ import annotations

import csv
import gzip
import html
import json
import mimetypes
import re
import shutil
import subprocess
import tarfile
import tempfile
import zipfile
from dataclasses import dataclass, field
from io import BytesIO, StringIO
from pathlib import Path
from typing import Any
from xml.etree import ElementTree as ET

from .storage.object_store import ObjectStoreIntegrityError, get_object_store

PREVIEW_CHAR_LIMIT = 80_000
MAX_INLINE_BINARY_BYTES = 2_000_000

TEXT_SUFFIXES = {
    ".txt",
    ".md",
    ".markdown",
    ".rst",
    ".log",
    ".csv",
    ".tsv",
    ".json",
    ".jsonl",
    ".xml",
    ".html",
    ".htm",
    ".css",
    ".js",
    ".jsx",
    ".ts",
    ".tsx",
    ".py",
    ".sql",
    ".sh",
    ".bash",
    ".zsh",
    ".yaml",
    ".yml",
    ".toml",
    ".ini",
    ".env",
}
CODE_SUFFIXES = {
    ".py",
    ".ts",
    ".tsx",
    ".js",
    ".jsx",
    ".css",
    ".html",
    ".json",
    ".sql",
    ".sh",
    ".bash",
    ".zsh",
    ".yaml",
    ".yml",
    ".toml",
    ".ini",
}
DATASET_SUFFIXES = {".csv", ".tsv", ".xls", ".xlsx", ".ods", ".parquet", ".ndjson", ".jsonl"}
DOC_SUFFIXES = {".pdf", ".doc", ".docx", ".rtf", ".odt", ".pages"}
PRESENTATION_SUFFIXES = {".ppt", ".pptx", ".key", ".odp"}
ARCHIVE_SUFFIXES = {".zip", ".tar", ".tgz", ".gz", ".7z", ".rar"}


@dataclass(frozen=True)
class FilePreview:
    text: str
    preview_kind: str | None = "md"
    extraction: str = "metadata"
    warnings: tuple[str, ...] = ()
    metadata: dict[str, Any] = field(default_factory=dict)


def safe_filename(name: str | None) -> str:
    value = Path(str(name or "upload.bin")).name.strip()
    value = re.sub(r"[\x00-\x1f/\\:]+", "_", value)
    value = re.sub(r"\s+", " ", value).strip(" .")
    return (value or "upload.bin")[:180]


def guess_mime(filename: str, provided: str | None = None) -> str:
    value = str(provided or "").strip()
    if value and value != "application/octet-stream":
        return value
    return mimetypes.guess_type(filename)[0] or value or "application/octet-stream"


def artifact_kind_for_file(filename: str, mime: str | None = None) -> str:
    suffix = Path(filename).suffix.lower()
    if suffix in {".md", ".markdown", ".txt", ".rst", ".log"}:
        return "report"
    if suffix in CODE_SUFFIXES:
        return "code"
    if mime and mime.startswith("image/"):
        return "image"
    if suffix in DATASET_SUFFIXES:
        return "dataset"
    if suffix in DOC_SUFFIXES or suffix in PRESENTATION_SUFFIXES:
        return "doc"
    return "file"


def message_attachment_from_artifact(artifact: dict[str, Any]) -> dict[str, Any]:
    return {
        key: value
        for key, value in {
            "artifactId": artifact.get("id"),
            "name": artifact.get("name"),
            "kind": artifact.get("kind"),
            "mime": artifact.get("mime") or artifact.get("contentMime"),
            "uri": artifact.get("uri"),
            "sizeBytes": artifact.get("contentSizeBytes"),
            "sourcePath": artifact.get("sourcePath"),
            "sourceSizeBytes": artifact.get("sourceSizeBytes"),
            "sampledBytes": artifact.get("sampledBytes"),
            "copyStatus": artifact.get("copyStatus"),
            "referenceKind": artifact.get("referenceKind"),
            "contextMaxChars": artifact.get("contextMaxChars"),
            "includeFullContext": artifact.get("includeFullContext"),
            "projectId": artifact.get("projectId"),
            "videoProjectId": artifact.get("videoProjectId"),
            "assetId": artifact.get("assetId"),
            "timelineId": artifact.get("timelineId"),
            "timelineVersion": artifact.get("timelineVersion"),
            "renderId": artifact.get("renderId"),
            "mediaHandle": artifact.get("mediaHandle"),
            "contextTool": artifact.get("contextTool"),
            "contextArgs": artifact.get("contextArgs"),
            "reviewStatus": artifact.get("reviewStatus"),
            "videoReviewId": artifact.get("videoReviewId"),
            "approvalId": artifact.get("approvalId"),
        }.items()
        if value is not None
    }


def attachments_from_tool_runs(tool_runs: list[dict[str, Any]]) -> list[dict[str, Any]]:
    attachments: list[dict[str, Any]] = []
    seen: set[str] = set()

    def add_artifact(artifact: Any) -> None:
        if not isinstance(artifact, dict):
            return
        artifact_id = str(artifact.get("id") or "")
        if not artifact_id or artifact_id in seen:
            return
        seen.add(artifact_id)
        attachments.append(message_attachment_from_artifact(artifact))

    for run in tool_runs or []:
        result = run.get("result") if isinstance(run, dict) else None
        if not isinstance(result, dict):
            continue
        for artifact in iter_artifacts_in_value(result):
            add_artifact(artifact)
    return attachments


def extract_preview_from_bytes(
    data: bytes,
    *,
    filename: str,
    mime: str | None = None,
    limit: int = PREVIEW_CHAR_LIMIT,
) -> FilePreview:
    filename = safe_filename(filename)
    mime = guess_mime(filename, mime)
    suffix = Path(filename).suffix.lower()
    meta = {"filename": filename, "mime": mime, "sizeBytes": len(data)}
    warnings: list[str] = []

    try:
        if suffix in {".docx"}:
            return _preview("docx", _extract_docx(data, limit), meta, limit)
        if suffix in {".pptx"}:
            return _preview("pptx", _extract_pptx(data, limit), meta, limit)
        if suffix in {".xlsx"}:
            return _preview("xlsx", _extract_xlsx(data, limit), meta, limit, preview_kind="sheet")
        if suffix == ".pdf" or mime == "application/pdf":
            text = _extract_pdf(data, limit)
            if text:
                return _preview("pdf", text, meta, limit)
            warnings.append("PDF text extractor unavailable or produced no text")
        if suffix == ".rtf" or mime in {"application/rtf", "text/rtf"}:
            return _preview("rtf", _strip_rtf(_decode_text(data)), meta, limit)
        if suffix in {".zip"}:
            return _preview("zip", _list_zip(data, limit), meta, limit)
        if suffix in {".tar", ".tgz"} or filename.endswith(".tar.gz"):
            return _preview("tar", _list_tar(data, limit), meta, limit)
        if suffix == ".gz" and not filename.endswith(".tar.gz"):
            with gzip.GzipFile(fileobj=BytesIO(data)) as gz:
                return _preview("gzip", _decode_text(gz.read(MAX_INLINE_BINARY_BYTES)), meta, limit)
        if suffix in {".csv", ".tsv"}:
            return _preview("table", _extract_delimited(data, delimiter="\t" if suffix == ".tsv" else ","), meta, limit, preview_kind="sheet")
        if suffix == ".json":
            parsed = json.loads(_decode_text(data))
            return _preview("json", json.dumps(parsed, ensure_ascii=False, indent=2), meta, limit)
        if suffix == ".jsonl":
            return _preview("jsonl", _extract_jsonl(data, limit), meta, limit)
        if suffix in {".html", ".htm"} or mime == "text/html":
            return _preview("html", _strip_html(_decode_text(data)), meta, limit)
        if _looks_textual(data, suffix, mime):
            return _preview("text", _decode_text(data), meta, limit)
    except Exception as exc:
        warnings.append(f"{type(exc).__name__}: {exc}")

    text = _metadata_preview(meta, warnings=warnings)
    return FilePreview(text=text, preview_kind="md", extraction="metadata", warnings=tuple(warnings), metadata=meta)


def extract_text_from_uri(
    uri: Any,
    *,
    filename: str | None = None,
    mime: str | None = None,
    limit: int = PREVIEW_CHAR_LIMIT,
) -> str:
    raw = str(uri or "")
    if raw and not raw.startswith("atrium-object://") and not raw.startswith("atrium://"):
        if raw.startswith("file://"):
            raw = raw[7:]
        try:
            path = Path(raw).expanduser().resolve()
            if path.is_file():
                preview = extract_preview_from_path(path, filename=filename, mime=mime, limit=limit)
                return preview.text[:limit]
        except Exception:
            return ""
    data = bytes_from_uri(uri)
    if data is None:
        return ""
    preview = extract_preview_from_bytes(data, filename=filename or "artifact.bin", mime=mime, limit=limit)
    return preview.text[:limit]


def bytes_from_uri(uri: Any, *, max_bytes: int | None = None) -> bytes | None:
    if not uri:
        return None
    raw = str(uri)
    if raw.startswith("atrium-object://"):
        digest = raw.removeprefix("atrium-object://")
        try:
            if max_bytes is not None:
                path = get_object_store().resolve_uri(raw)
                if path is not None and path.stat().st_size > max_bytes:
                    return None
            return get_object_store().get_bytes(digest)
        except (ObjectStoreIntegrityError, ValueError):
            raise
        except Exception:
            return None
    if raw.startswith("atrium://"):
        return None
    if raw.startswith("file://"):
        raw = raw[7:]
    try:
        path = Path(raw).expanduser().resolve()
        if not path.exists() or not path.is_file():
            return None
        if max_bytes is not None and path.stat().st_size > max_bytes:
            return None
        return path.read_bytes()
    except Exception:
        return None


def extract_preview_from_path(
    path: Path,
    *,
    filename: str | None = None,
    mime: str | None = None,
    limit: int = PREVIEW_CHAR_LIMIT,
    max_bytes: int | None = MAX_INLINE_BINARY_BYTES,
) -> FilePreview:
    path = path.expanduser().resolve()
    stat = path.stat()
    read_limit = stat.st_size if max_bytes is None or max_bytes <= 0 else min(stat.st_size, max_bytes)
    with path.open("rb") as fh:
        data = fh.read(read_limit)
    preview = extract_preview_from_bytes(data, filename=filename or path.name, mime=mime, limit=limit)
    metadata = {**preview.metadata, "sourcePath": str(path), "sourceSizeBytes": stat.st_size, "sampledBytes": len(data)}
    warnings = list(preview.warnings)
    if len(data) < stat.st_size:
        warnings.append(f"Preview sampled first {len(data)} bytes of {stat.st_size} bytes")
    return FilePreview(
        text=preview.text,
        preview_kind=preview.preview_kind,
        extraction=preview.extraction,
        warnings=tuple(warnings),
        metadata=metadata,
    )


def _artifact_like(value: dict[str, Any]) -> bool:
    return bool(value.get("id") and value.get("uri") and (value.get("kind") or value.get("mime") or value.get("contentMime")))


def iter_artifacts_in_value(value: Any) -> list[dict[str, Any]]:
    artifacts: list[dict[str, Any]] = []
    seen: set[str] = set()

    def visit(item: Any) -> None:
        if isinstance(item, dict):
            if _artifact_like(item):
                artifact_id = str(item.get("id") or "")
                if artifact_id and artifact_id not in seen:
                    seen.add(artifact_id)
                    artifacts.append(item)
            for child in item.values():
                visit(child)
        elif isinstance(item, list):
            for child in item:
                visit(child)

    visit(value)
    return artifacts


def _preview(
    extraction: str,
    text: str,
    meta: dict[str, Any],
    limit: int,
    *,
    preview_kind: str = "md",
) -> FilePreview:
    body = str(text or "").strip()
    if len(body) > limit:
        body = body[:limit].rstrip() + "\n\n[truncated]"
    if not body:
        body = _metadata_preview(meta)
        extraction = "metadata"
    return FilePreview(text=body, preview_kind=preview_kind, extraction=extraction, metadata=meta)


def _metadata_preview(meta: dict[str, Any], *, warnings: list[str] | None = None) -> str:
    lines = [
        "# File attachment",
        f"- Name: {meta.get('filename')}",
        f"- MIME: {meta.get('mime')}",
        f"- Size: {meta.get('sizeBytes')} bytes",
    ]
    if warnings:
        lines.append("- Extraction notes: " + "; ".join(warnings[:3]))
    else:
        lines.append("- Extraction notes: no text preview available; original bytes are stored as an artifact.")
    return "\n".join(lines)


def _decode_text(data: bytes) -> str:
    for encoding in ("utf-8-sig", "utf-8", "utf-16", "cp874", "latin-1"):
        try:
            return data.decode(encoding)
        except UnicodeDecodeError:
            continue
    return data.decode("utf-8", errors="ignore")


def _looks_textual(data: bytes, suffix: str, mime: str) -> bool:
    if suffix in TEXT_SUFFIXES:
        return True
    if mime.startswith("text/"):
        return True
    if not data:
        return True
    sample = data[:4096]
    if b"\x00" in sample:
        return False
    printable = sum(1 for b in sample if b in b"\n\r\t" or 32 <= b <= 126 or b >= 128)
    return printable / max(len(sample), 1) > 0.9


def _strip_html(text: str) -> str:
    text = re.sub(r"(?is)<(script|style).*?>.*?</\1>", " ", text)
    text = re.sub(r"(?s)<[^>]+>", " ", text)
    text = html.unescape(text)
    return re.sub(r"[ \t]{2,}", " ", re.sub(r"\n{3,}", "\n\n", text)).strip()


def _strip_rtf(text: str) -> str:
    text = re.sub(r"\\'[0-9a-fA-F]{2}", " ", text)
    text = re.sub(r"\\[a-zA-Z]+-?\d* ?", " ", text)
    text = text.replace("{", " ").replace("}", " ")
    return re.sub(r"\s+", " ", text).strip()


def _extract_delimited(data: bytes, *, delimiter: str) -> str:
    text = _decode_text(data)
    reader = csv.reader(StringIO(text), delimiter=delimiter)
    rows: list[list[str]] = []
    for idx, row in enumerate(reader):
        if idx >= 200:
            rows.append(["..."])
            break
        rows.append([cell.strip()[:300] for cell in row[:30]])
    return _rows_to_gfm_table(rows)


def _rows_to_gfm_table(rows: list[list[str]]) -> str:
    rows = [row for row in rows if any(cell.strip() for cell in row)]
    if not rows:
        return ""
    width = min(max(len(row) for row in rows), 30)
    normalized = [(row + [""] * width)[:width] for row in rows]
    header, body = normalized[0], normalized[1:]
    if not body:
        body = [[""] * width]
    return "\n".join(
        [
            "| " + " | ".join(_escape_table_cell(cell) for cell in header) + " |",
            "| " + " | ".join("---" for _ in range(width)) + " |",
            *("| " + " | ".join(_escape_table_cell(cell) for cell in row) + " |" for row in body),
        ]
    )


def _escape_table_cell(value: str) -> str:
    return re.sub(r"\s+", " ", value.replace("|", r"\|")).strip()


def _extract_jsonl(data: bytes, limit: int) -> str:
    lines = []
    for idx, line in enumerate(_decode_text(data).splitlines()):
        if idx >= 200:
            lines.append("...")
            break
        stripped = line.strip()
        if not stripped:
            continue
        try:
            parsed = json.loads(stripped)
            lines.append(json.dumps(parsed, ensure_ascii=False))
        except json.JSONDecodeError:
            lines.append(stripped)
        if sum(len(x) for x in lines) > limit:
            break
    return "\n".join(lines)


def _extract_pdf(data: bytes, limit: int) -> str:
    try:
        from pypdf import PdfReader  # type: ignore

        reader = PdfReader(BytesIO(data))
        pages = []
        for page in reader.pages[:50]:
            pages.append(page.extract_text() or "")
            if sum(len(x) for x in pages) > limit:
                break
        return "\n\n".join(page.strip() for page in pages if page.strip())
    except Exception:
        pass
    if not shutil.which("pdftotext"):
        return ""
    with tempfile.TemporaryDirectory(prefix="atrium-pdf-") as td:
        src = Path(td) / "input.pdf"
        out = Path(td) / "output.txt"
        src.write_bytes(data)
        proc = subprocess.run(
            ["pdftotext", "-layout", str(src), str(out)],
            text=True,
            capture_output=True,
            timeout=15,
            check=False,
        )
        if proc.returncode != 0 or not out.exists():
            return ""
        return out.read_text(encoding="utf-8", errors="ignore")


def _extract_docx(data: bytes, limit: int) -> str:
    with zipfile.ZipFile(BytesIO(data)) as zf:
        names = [
            "word/document.xml",
            "word/footnotes.xml",
            "word/endnotes.xml",
        ]
        parts = []
        for name in names:
            if name in zf.namelist():
                parts.append(_xml_text(zf.read(name)))
                if sum(len(x) for x in parts) > limit:
                    break
        return "\n\n".join(part for part in parts if part)


def _extract_pptx(data: bytes, limit: int) -> str:
    with zipfile.ZipFile(BytesIO(data)) as zf:
        slide_names = sorted(n for n in zf.namelist() if re.match(r"ppt/slides/slide\d+\.xml$", n))
        parts = []
        for idx, name in enumerate(slide_names[:80], start=1):
            text = _xml_text(zf.read(name))
            if text:
                parts.append(f"Slide {idx}:\n{text}")
            if sum(len(x) for x in parts) > limit:
                break
        return "\n\n".join(parts)


def _extract_xlsx(data: bytes, limit: int) -> str:
    with zipfile.ZipFile(BytesIO(data)) as zf:
        shared = _xlsx_shared_strings(zf)
        sheet_names = sorted(n for n in zf.namelist() if re.match(r"xl/worksheets/sheet\d+\.xml$", n))
        sections = []
        for idx, name in enumerate(sheet_names[:20], start=1):
            rows = _xlsx_rows(zf.read(name), shared)
            if rows:
                rendered = _rows_to_gfm_table(rows[:200])
                sections.append(f"Sheet {idx}:\n{rendered}")
            if sum(len(x) for x in sections) > limit:
                break
        return "\n\n".join(sections)


def _xml_text(data: bytes) -> str:
    root = ET.fromstring(data)
    texts: list[str] = []
    for el in root.iter():
        if el.text and el.text.strip():
            texts.append(el.text.strip())
    return re.sub(r"\n{3,}", "\n\n", "\n".join(texts)).strip()


def _xlsx_shared_strings(zf: zipfile.ZipFile) -> list[str]:
    if "xl/sharedStrings.xml" not in zf.namelist():
        return []
    root = ET.fromstring(zf.read("xl/sharedStrings.xml"))
    out = []
    for si in root:
        texts = [el.text or "" for el in si.iter() if el.tag.endswith("}t") or el.tag == "t"]
        out.append("".join(texts))
    return out


def _xlsx_rows(data: bytes, shared: list[str]) -> list[list[str]]:
    root = ET.fromstring(data)
    rows: list[list[str]] = []
    for row in root.iter():
        if not row.tag.endswith("}row") and row.tag != "row":
            continue
        cells: list[str] = []
        for cell in row:
            if not cell.tag.endswith("}c") and cell.tag != "c":
                continue
            cell_type = cell.attrib.get("t")
            value = ""
            for child in cell:
                if child.tag.endswith("}v") or child.tag == "v":
                    value = child.text or ""
                    break
                if child.tag.endswith("}is") or child.tag == "is":
                    value = _xml_text(ET.tostring(child))
                    break
            if cell_type == "s" and value.isdigit():
                idx = int(value)
                value = shared[idx] if 0 <= idx < len(shared) else value
            cells.append(value)
        if any(cell.strip() for cell in cells):
            rows.append(cells)
        if len(rows) >= 300:
            break
    return rows


def _list_zip(data: bytes, limit: int) -> str:
    with zipfile.ZipFile(BytesIO(data)) as zf:
        lines = ["ZIP archive contents:"]
        for info in zf.infolist()[:500]:
            lines.append(f"- {info.filename} ({info.file_size} bytes)")
            if sum(len(x) for x in lines) > limit:
                break
        return "\n".join(lines)


def _list_tar(data: bytes, limit: int) -> str:
    mode = "r:gz" if data[:2] == b"\x1f\x8b" else "r:*"
    with tarfile.open(fileobj=BytesIO(data), mode=mode) as tf:
        lines = ["TAR archive contents:"]
        for item in tf.getmembers()[:500]:
            lines.append(f"- {item.name} ({item.size} bytes)")
            if sum(len(x) for x in lines) > limit:
                break
        return "\n".join(lines)
