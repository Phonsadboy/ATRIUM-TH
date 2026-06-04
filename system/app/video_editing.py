"""Non-destructive video editing project tools for Owner Mode."""
from __future__ import annotations

import copy
import hashlib
import asyncio
import html
import json
import mimetypes
import os
import re
import shutil
import subprocess
import time
from pathlib import Path
from typing import Any
from urllib.parse import unquote, urlparse

from .atrium_domain import agent_message_metadata
from .clock import now_ms
from .config import get_settings
from .events import hub
from .file_intake import artifact_kind_for_file, guess_mime, safe_filename
from .ids import uid
from .schema import Artifact, ArtifactVersion
from .threads import is_exec


VIDEO_TOOL_NAMES = {
    "video.create_project",
    "video.add_asset",
    "video.list_fonts",
    "video.inspect",
    "video.sample_frames",
    "video.storyboard",
    "video.inspect_segment",
    "video.context_packet",
    "video.plan_edit",
    "video.suggest_edits",
    "video.track_subject",
    "video.render_edit",
    "video.render_motion",
    "video.patch_timeline",
    "video.transcribe",
    "video.generate_captions",
    "video.quality_check",
    "video.request_review",
    "video.approve_render",
    "video.job_status",
    "video.cancel_job",
    "video.resume_job",
    "video.list_templates",
}

VIDEO_BACKGROUND_TOOL_NAMES = {"video.render_edit", "video.render_motion", "video.transcribe"}
VIDEO_JOB_TERMINAL_STATUSES = {"done", "failed", "cancelled"}
PATCH_CONTROL_KEYS = {
    "op",
    "type",
    "id",
    "clipId",
    "textId",
    "captionId",
    "overlayId",
    "audioId",
    "transitionId",
    "effectId",
    "targetId",
    "collection",
}

VIDEO_ASSET_TYPES = {
    "video",
    "image",
    "audio",
    "music",
    "sfx",
    "voiceover",
    "font",
    "subtitle",
    "data",
    "other",
}

_VIDEO_PROJECT_RE = re.compile(r"^vidproj_[A-Za-z0-9_:-]+$")
_VIDEO_TIMELINE_RE = re.compile(r"^tl_[A-Za-z0-9_:-]+$")


def _workspace(dept_id: str) -> Path:
    root = (get_settings().workspace_dir / dept_id).resolve()
    root.mkdir(parents=True, exist_ok=True)
    return root


def _video_root(dept_id: str) -> Path:
    root = _workspace(dept_id) / "video_projects"
    root.mkdir(parents=True, exist_ok=True)
    return root


def _project_id(value: Any | None = None) -> str:
    raw = str(value or "").strip()
    if raw:
        if not _VIDEO_PROJECT_RE.fullmatch(raw):
            raise ValueError("invalid video project id")
        return raw
    return uid("vidproj")


def _timeline_id(value: Any | None = None) -> str:
    raw = str(value or "").strip()
    if raw:
        if not _VIDEO_TIMELINE_RE.fullmatch(raw):
            raise ValueError("invalid timeline id")
        return raw
    return uid("tl")


def _project_dir(dept_id: str, project_id: str) -> Path:
    return (_video_root(dept_id) / _project_id(project_id)).resolve()


def _project_manifest_path(dept_id: str, project_id: str) -> Path:
    return _project_dir(dept_id, project_id) / "project.json"


def _project_audit_path(project: dict[str, Any]) -> Path:
    return _project_dir(str(project["ownerDept"]), str(project["id"])) / "logs" / "audit.jsonl"


def _load_json(path: Path, default: Any) -> Any:
    if not path.is_file():
        return copy.deepcopy(default)
    return json.loads(path.read_text(encoding="utf-8"))


def _write_json(path: Path, value: Any) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(value, ensure_ascii=False, indent=2, sort_keys=True), encoding="utf-8")


def _load_project(dept_id: str, project_id: str) -> dict[str, Any]:
    path = _project_manifest_path(dept_id, project_id)
    if not path.is_file():
        raise ValueError(f"video project not found: {project_id}")
    project = _load_json(path, {})
    if not isinstance(project, dict) or project.get("id") != project_id:
        raise ValueError(f"invalid video project manifest: {project_id}")
    return project


def _save_project(project: dict[str, Any]) -> None:
    dept_id = str(project["ownerDept"])
    project_id = str(project["id"])
    project["updatedAt"] = now_ms()
    _write_json(_project_manifest_path(dept_id, project_id), project)


def _sha256_file(path: Path) -> str:
    h = hashlib.sha256()
    with path.open("rb") as fh:
        for chunk in iter(lambda: fh.read(1024 * 1024), b""):
            h.update(chunk)
    return h.hexdigest()


def _copy_file_best_effort(source: Path, destination: Path) -> None:
    try:
        shutil.copy2(source, destination)
    except PermissionError:
        shutil.copyfile(source, destination)


def _object_store_file_ref(path: Path, *, mime: str | None = None) -> dict[str, Any] | None:
    settings = get_settings()
    if not settings.object_store_enabled:
        return None
    try:
        from .storage.object_store import get_object_store

        stored = get_object_store(settings).put_file(path, mime=mime or guess_mime(path.name))
    except Exception as exc:
        return {"storage": "object_store", "error": f"{type(exc).__name__}: {exc}"}
    return {
        "storage": "object_store",
        "uri": stored.uri,
        "contentHash": stored.content_hash,
        "sizeBytes": stored.size_bytes,
        "mime": mime or stored.mime,
        "path": str(stored.path),
    }


def _object_store_preview_uri(raw_uri: str, *, mime: str | None = None) -> tuple[str, dict[str, Any] | None]:
    raw = str(raw_uri or "").strip()
    if not raw or raw.startswith(("atrium-object://", "atrium://", "http://", "https://")):
        return raw, None
    path = Path(raw[7:] if raw.startswith("file://") else raw).expanduser().resolve()
    if not path.is_file():
        return raw, None
    ref = _object_store_file_ref(path, mime=mime or guess_mime(path.name))
    if ref and not ref.get("error") and ref.get("uri"):
        return str(ref["uri"]), ref
    return raw, ref


def _append_project_audit(
    project: dict[str, Any],
    action: str,
    *,
    run: dict[str, Any] | None = None,
    entity_type: str | None = None,
    entity_id: str | None = None,
    summary: str | None = None,
    refs: dict[str, Any] | None = None,
    paths: dict[str, Any] | None = None,
    checksum: str | None = None,
) -> dict[str, Any]:
    event = {
        "id": uid("vaudit"),
        "ts": now_ms(),
        "action": action,
        "projectId": project.get("id"),
        "ownerDept": project.get("ownerDept"),
        **({"tool": run.get("tool")} if run and run.get("tool") else {}),
        **({"toolRunId": run.get("id")} if run and run.get("id") else {}),
        **({"requestedBy": run.get("requestedBy")} if run and run.get("requestedBy") else {}),
        **({"entityType": entity_type} if entity_type else {}),
        **({"entityId": entity_id} if entity_id else {}),
        **({"summary": summary} if summary else {}),
        **({"refs": refs} if refs else {}),
        **({"paths": paths} if paths else {}),
        **({"sha256": checksum} if checksum else {}),
    }
    audit_path = _project_audit_path(project)
    audit_path.parent.mkdir(parents=True, exist_ok=True)
    with audit_path.open("a", encoding="utf-8") as fh:
        fh.write(json.dumps(event, ensure_ascii=False, sort_keys=True) + "\n")
    trail = project.setdefault("auditTrail", [])
    if isinstance(trail, list):
        trail.append(event)
        if len(trail) > 80:
            del trail[:-80]
    audit = project.setdefault("audit", {})
    if isinstance(audit, dict):
        audit["path"] = str(audit_path)
        audit["eventCount"] = int(audit.get("eventCount") or 0) + 1
        audit["lastEventId"] = event["id"]
        audit["updatedAt"] = event["ts"]
    return event


def _run(command: list[str], *, cwd: Path | None = None, timeout: float = 120.0) -> dict[str, Any]:
    try:
        completed = subprocess.run(
            command,
            cwd=str(cwd) if cwd else None,
            text=True,
            capture_output=True,
            timeout=timeout,
            check=False,
        )
    except FileNotFoundError:
        raise ValueError(f"executable not found: {command[0]}") from None
    except subprocess.TimeoutExpired as exc:
        return {
            "command": command,
            "returnCode": 124,
            "stdout": exc.stdout or "",
            "stderr": exc.stderr or f"command timed out after {timeout}s",
        }
    return {
        "command": command,
        "returnCode": completed.returncode,
        "stdout": completed.stdout,
        "stderr": completed.stderr,
    }


def _require_ffmpeg() -> str:
    executable = shutil.which("ffmpeg")
    if not executable:
        raise ValueError("ffmpeg is required for video editing tools")
    return executable


def _require_ffprobe() -> str:
    executable = shutil.which("ffprobe")
    if not executable:
        raise ValueError("ffprobe is required for video inspection tools")
    return executable


def _ffprobe(path: Path) -> dict[str, Any]:
    result = _run(
        [
            _require_ffprobe(),
            "-v",
            "error",
            "-show_format",
            "-show_streams",
            "-of",
            "json",
            str(path),
        ],
        timeout=60.0,
    )
    if result["returnCode"] != 0:
        raise ValueError((result.get("stderr") or "ffprobe failed").strip())
    try:
        raw = json.loads(result.get("stdout") or "{}")
    except json.JSONDecodeError as exc:
        raise ValueError(f"invalid ffprobe JSON: {exc}") from exc
    return _normalize_probe(raw)


def _normalize_probe(raw: dict[str, Any]) -> dict[str, Any]:
    streams = raw.get("streams") if isinstance(raw.get("streams"), list) else []
    fmt = raw.get("format") if isinstance(raw.get("format"), dict) else {}
    video_stream = next((s for s in streams if s.get("codec_type") == "video"), None)
    audio_streams = [s for s in streams if s.get("codec_type") == "audio"]
    return {
        "format": {
            "filename": fmt.get("filename"),
            "duration": _float_or_none(fmt.get("duration")),
            "sizeBytes": _int_or_none(fmt.get("size")),
            "bitRate": _int_or_none(fmt.get("bit_rate")),
            "formatName": fmt.get("format_name"),
        },
        "video": {
            "codec": (video_stream or {}).get("codec_name"),
            "width": _int_or_none((video_stream or {}).get("width")),
            "height": _int_or_none((video_stream or {}).get("height")),
            "duration": _float_or_none((video_stream or {}).get("duration") or fmt.get("duration")),
            "fps": _fps((video_stream or {}).get("avg_frame_rate") or (video_stream or {}).get("r_frame_rate")),
            "pixFmt": (video_stream or {}).get("pix_fmt"),
        } if video_stream else None,
        "audio": [
            {
                "codec": stream.get("codec_name"),
                "channels": _int_or_none(stream.get("channels")),
                "sampleRate": _int_or_none(stream.get("sample_rate")),
                "duration": _float_or_none(stream.get("duration") or fmt.get("duration")),
            }
            for stream in audio_streams
        ],
        "streams": streams,
    }


def _float_or_none(value: Any) -> float | None:
    try:
        return float(value)
    except (TypeError, ValueError):
        return None


def _int_or_none(value: Any) -> int | None:
    try:
        return int(float(value))
    except (TypeError, ValueError):
        return None


def _fps(value: Any) -> float | None:
    text = str(value or "").strip()
    if not text or text == "0/0":
        return None
    if "/" in text:
        a, b = text.split("/", 1)
        try:
            denom = float(b)
            return None if denom == 0 else round(float(a) / denom, 3)
        except ValueError:
            return None
    return _float_or_none(text)


def _source_from_artifact(repo: Any, artifact: dict[str, Any]) -> Path:
    uri = str(artifact.get("uri") or "")
    if uri.startswith("file://"):
        uri = uri[7:]
    if uri.startswith("atrium-object://"):
        from .storage.object_store import get_object_store

        path = get_object_store().resolve_uri(uri)
        if path is None:
            raise ValueError(f"object-store artifact is unavailable: {artifact.get('id')}")
        return path.resolve()
    path = Path(uri).expanduser().resolve()
    if not path.is_file():
        raise ValueError(f"artifact file not found: {artifact.get('id')}")
    return path


async def _resolve_source_path(repo: Any, dept_id: str, args: dict[str, Any], project: dict[str, Any] | None = None) -> Path:
    artifact_id = str(args.get("artifactId") or args.get("artifact_id") or "").strip()
    if artifact_id:
        artifact = await repo.get_entity("artifact", artifact_id)
        if not artifact:
            raise ValueError(f"artifact not found: {artifact_id}")
        return _source_from_artifact(repo, artifact)

    if project is not None:
        asset_id = str(
            args.get("assetId")
            or args.get("asset_id")
            or args.get("sourceAssetId")
            or args.get("source_asset_id")
            or ""
        ).strip()
        if asset_id:
            asset = _asset(project, asset_id)
            return Path(asset["path"]).expanduser().resolve()
        primary = _primary_video_asset(project)
        if primary is not None and not (args.get("sourcePath") or args.get("source") or args.get("path")):
            return Path(primary["path"]).expanduser().resolve()

    raw = str(args.get("sourcePath") or args.get("source") or args.get("path") or "").strip()
    if not raw:
        raise ValueError("sourcePath, artifactId, or assetId is required")
    path = Path(raw).expanduser()
    if not path.is_absolute():
        cwd_relative = path.resolve()
        path = cwd_relative if cwd_relative.is_file() else _workspace(dept_id) / path
    path = path.resolve()
    if not path.is_file():
        raise ValueError(f"source file not found: {raw}")
    return path


def _asset(project: dict[str, Any], asset_id: str) -> dict[str, Any]:
    for item in project.get("assets") or []:
        if str(item.get("id")) == asset_id:
            return item
    raise ValueError(f"video asset not found: {asset_id}")


def _primary_video_asset(project: dict[str, Any]) -> dict[str, Any] | None:
    for item in project.get("assets") or []:
        if item.get("role") == "source":
            return item
    for item in project.get("assets") or []:
        if item.get("type") == "video":
            return item
    return None


def _asset_type(path: Path, raw: Any = None) -> str:
    requested = str(raw or "").strip().lower()
    if requested in VIDEO_ASSET_TYPES:
        return requested
    mime = guess_mime(path.name)
    if mime.startswith("video/"):
        return "video"
    if mime.startswith("image/"):
        return "image"
    if mime.startswith("audio/"):
        return "audio"
    if path.suffix.lower() in {".ttf", ".otf", ".ttc", ".woff", ".woff2"}:
        return "font"
    if path.suffix.lower() in {".srt", ".vtt", ".ass"}:
        return "subtitle"
    return "other"


def _asset_destination(project: dict[str, Any], asset_id: str, source: Path, asset_type: str) -> Path:
    filename = safe_filename(source.name)
    return _project_dir(str(project["ownerDept"]), str(project["id"])) / "assets" / asset_type / f"{asset_id}_{filename}"


def _asset_manifest_path(project: dict[str, Any], asset_id: str) -> Path:
    return _project_dir(str(project["ownerDept"]), str(project["id"])) / "assets" / "_manifests" / f"{asset_id}.json"


def _base_file_metadata(path: Path) -> dict[str, Any]:
    stat = path.stat()
    return {
        "filename": str(path),
        "extension": path.suffix.lower(),
        "sizeBytes": stat.st_size,
        "modifiedAt": int(stat.st_mtime * 1000),
    }


def _image_file_metadata(path: Path) -> dict[str, Any]:
    from PIL import Image

    with Image.open(path) as image:
        bands = image.getbands()
        return {
            "width": int(image.width),
            "height": int(image.height),
            "mode": image.mode,
            "format": image.format,
            "hasAlpha": "A" in bands or image.mode in {"RGBA", "LA"} or "transparency" in image.info,
        }


def _font_file_metadata(path: Path) -> dict[str, Any]:
    metadata: dict[str, Any] = {
        "extension": path.suffix.lower(),
        "familyHint": path.stem.replace("-", " "),
        "postscriptHint": path.stem,
    }
    try:
        from fontTools.ttLib import TTFont  # type: ignore
    except ModuleNotFoundError:
        metadata["parser"] = "filename"
        return metadata
    try:
        font = TTFont(str(path), lazy=True)
    except Exception as exc:
        metadata["parser"] = "filename"
        metadata["parseWarning"] = f"{type(exc).__name__}: {exc}"
        return metadata
    try:
        name_map = {
            1: "family",
            2: "subfamily",
            4: "fullName",
            6: "postscriptName",
        }
        for record in font["name"].names:
            key = name_map.get(record.nameID)
            if key and key not in metadata:
                try:
                    value = record.toUnicode().strip()
                except Exception:
                    value = ""
                if value:
                    metadata[key] = value
        metadata["parser"] = "fontTools"
        return metadata
    finally:
        try:
            font.close()
        except Exception:
            pass


def _subtitle_file_metadata(path: Path) -> dict[str, Any]:
    suffix = path.suffix.lower()
    text = path.read_text(encoding="utf-8", errors="ignore")
    if suffix == ".ass":
        dialogue_lines = [line for line in text.splitlines() if line.strip().lower().startswith("dialogue:")]
        body = " ".join(line.split(",", 9)[-1].strip() for line in dialogue_lines if "," in line)
        return {
            "format": "ass",
            "segmentCount": len(dialogue_lines),
            "wordCount": len(re.findall(r"\S+", body)),
            "textPreview": body[:240],
        }
    transcript = _load_transcript_file(path)
    segments = [item for item in transcript.get("segments") or [] if isinstance(item, dict)]
    words = [item for item in transcript.get("words") or [] if isinstance(item, dict)]
    duration = None
    if segments:
        duration = max((_float_or_none(item.get("end")) or 0.0) for item in segments)
    return {
        "format": suffix.lstrip(".") or "text",
        "language": transcript.get("language"),
        "segmentCount": len(segments),
        "wordCount": len(words) or len(re.findall(r"\S+", str(transcript.get("text") or ""))),
        "duration": duration,
        "speakers": transcript.get("speakers") or [],
        "textPreview": str(transcript.get("text") or "")[:240],
    }


def _asset_metadata(path: Path, asset_type: str) -> tuple[dict[str, Any], str, str | None]:
    metadata: dict[str, Any] = {"file": _base_file_metadata(path)}
    profile = "file"
    errors: list[str] = []
    if asset_type in {"video", "audio", "music", "sfx", "voiceover"}:
        try:
            metadata.update(_ffprobe(path))
            profile = "ffprobe_video" if asset_type == "video" else "ffprobe_audio"
        except Exception as exc:
            errors.append(f"ffprobe: {type(exc).__name__}: {exc}")
    elif asset_type == "image":
        try:
            metadata["image"] = _image_file_metadata(path)
            metadata["format"] = {"filename": str(path), "sizeBytes": metadata["file"]["sizeBytes"], "formatName": metadata["image"].get("format")}
            profile = "pillow_image"
        except Exception as exc:
            errors.append(f"image: {type(exc).__name__}: {exc}")
    elif asset_type == "font":
        metadata["font"] = _font_file_metadata(path)
        profile = str(metadata["font"].get("parser") or "filename_font")
    elif asset_type == "subtitle":
        try:
            metadata["subtitle"] = _subtitle_file_metadata(path)
            profile = "subtitle"
        except Exception as exc:
            errors.append(f"subtitle: {type(exc).__name__}: {exc}")
    else:
        metadata["format"] = {
            "filename": str(path),
            "sizeBytes": metadata["file"]["sizeBytes"],
            "formatName": path.suffix.lower().lstrip(".") or "data",
        }
    return metadata, profile, "; ".join(errors) if errors else None


def _write_asset_manifest(project: dict[str, Any], asset: dict[str, Any]) -> dict[str, Any]:
    manifest_path = _asset_manifest_path(project, str(asset["id"]))
    asset["manifestPath"] = str(manifest_path)
    manifest = {
        "id": uid("vassetmanifest"),
        "type": "video_asset_manifest",
        "projectId": project.get("id"),
        "ownerDept": project.get("ownerDept"),
        "assetId": asset.get("id"),
        "asset": {
            key: asset.get(key)
            for key in ("id", "type", "role", "name", "path", "sourcePath", "uri", "storage", "mime", "sizeBytes", "sha256", "handle", "createdAt")
            if asset.get(key) is not None
        },
        "metadataProfile": asset.get("metadataProfile"),
        "metadata": asset.get("metadata") if isinstance(asset.get("metadata"), dict) else {},
        "metadataError": asset.get("metadataError"),
        "createdAt": now_ms(),
        "updatedAt": now_ms(),
    }
    _write_json(manifest_path, manifest)
    manifest_ref = _object_store_file_ref(manifest_path, mime="application/json")
    if manifest_ref:
        asset["manifestObjectStore"] = manifest_ref
        if not manifest_ref.get("error") and manifest_ref.get("uri"):
            asset["manifestUri"] = manifest_ref["uri"]
    return manifest


async def _persist_file_artifact(
    repo: Any,
    *,
    path: Path,
    owner_dept: str,
    created_by: str,
    name: str | None = None,
    tags: list[str] | None = None,
    project_id: str | None = None,
    preview_kind: str | None = None,
    preview_uri: str | None = None,
    note: str = "created by video tool",
) -> dict[str, Any]:
    path = path.resolve()
    if not path.is_file():
        raise ValueError(f"artifact file not found: {path}")
    now = now_ms()
    artifact_id = uid("art")
    artifact_name = safe_filename(name or path.name)
    mime = guess_mime(artifact_name)
    suffix = path.suffix or mimetypes.guess_extension(mime) or ".bin"
    stored_path = (get_settings().workspace_dir / owner_dept / "artifacts" / artifact_id / f"v1{suffix}").resolve()
    stored_path.parent.mkdir(parents=True, exist_ok=True)
    if stored_path != path:
        shutil.copy2(path, stored_path)
    object_ref = _object_store_file_ref(stored_path, mime=mime)
    artifact_uri = str(stored_path)
    storage = "filesystem"
    data_hash = _sha256_file(stored_path)
    size = stored_path.stat().st_size
    if object_ref and not object_ref.get("error") and object_ref.get("uri"):
        artifact_uri = str(object_ref["uri"])
        storage = "object_store"
        data_hash = str(object_ref.get("contentHash") or data_hash)
        size = int(object_ref.get("sizeBytes") or size)
    kind = artifact_kind_for_file(artifact_name, mime)
    preview = None
    preview_storage_ref = None
    if preview_kind and preview_uri:
        stored_preview_uri, preview_storage_ref = _object_store_preview_uri(preview_uri, mime=guess_mime(str(preview_uri)))
        preview = {"kind": preview_kind, "uri": stored_preview_uri}
    elif mime.startswith("image/"):
        preview = {"kind": "image", "uri": artifact_uri}
    artifact_tags = list(dict.fromkeys([*(tags or []), "video_tool"]))
    artifact = Artifact(
        id=artifact_id,
        name=artifact_name,
        kind=kind,
        mime=mime,
        owner_dept=owner_dept,
        task_ids=[],
        project_id=project_id,
        version=1,
        status="approved",
        uri=artifact_uri,
        storage=storage,
        content_hash=data_hash,
        content_size_bytes=size,
        content_mime=mime,
        tags=artifact_tags,
        links=list(dict.fromkeys([str(stored_path), artifact_uri])),
        preview=preview,
        created_at=now,
        created_by=created_by,
        updated_at=now,
        updated_by=created_by,
    ).dump()
    version = ArtifactVersion(
        artifact_id=artifact_id,
        version=1,
        author=created_by,
        ts=now,
        note=note,
        uri=artifact_uri,
        storage=storage,
        content_hash=data_hash,
        content_size_bytes=size,
        content_mime=mime,
        preview=preview,
    ).dump()
    artifact["localPath"] = str(stored_path)
    if object_ref:
        artifact["objectStore"] = object_ref
    if preview_storage_ref:
        artifact["previewObjectStore"] = preview_storage_ref
    version["localPath"] = str(stored_path)
    if object_ref:
        version["objectStore"] = object_ref
    await repo.put_entity("artifact", artifact, dept=owner_dept, project=project_id, status="approved", ts=now)
    await repo.put_entity(
        "artifact_version",
        {**version, "id": f"{artifact_id}:1"},
        dept=owner_dept,
        project=project_id,
        status="approved",
        ts=now,
    )
    return artifact


def _video_artifact_context_fields(
    project: dict[str, Any],
    artifact_id: str,
    *,
    timeline: dict[str, Any] | None = None,
    render: dict[str, Any] | None = None,
    asset_id: str | None = None,
) -> dict[str, Any]:
    project_id = str(project["id"])
    primary = _primary_video_asset(project)
    resolved_asset_id = asset_id or str((primary or {}).get("id") or "") or None
    timeline_id = str((timeline or {}).get("id") or (render or {}).get("timelineId") or "") or None
    timeline_version = _int_or_none((timeline or {}).get("version") or (render or {}).get("timelineVersion"))
    render_id = str((render or {}).get("id") or (render or {}).get("renderId") or "") or None
    context_args: dict[str, Any] = {
        "projectId": project_id,
        "artifactId": artifact_id,
    }
    if timeline_id:
        context_args["timelineId"] = timeline_id
    if timeline_version is not None:
        context_args["version"] = timeline_version
        context_args["timelineVersion"] = timeline_version
    if render_id:
        context_args["renderId"] = render_id
    if resolved_asset_id:
        context_args["assetId"] = resolved_asset_id
    fields: dict[str, Any] = {
        "projectId": project_id,
        "videoProjectId": project_id,
        "assetId": resolved_asset_id,
        "timelineId": timeline_id,
        "timelineVersion": timeline_version,
        "renderId": render_id,
        "mediaHandle": f"atrium://video/projects/{project_id}/artifacts/{artifact_id}",
        "contextTool": "video.context_packet",
        "contextArgs": context_args,
    }
    return {key: value for key, value in fields.items() if value is not None}


def _wants_background(args: dict[str, Any]) -> bool:
    if args.get("waitForResult") or args.get("wait_for_result"):
        return False
    return bool(args.get("asyncMode") or args.get("async") or args.get("background"))


def _job_project_id(args: dict[str, Any]) -> str | None:
    raw = str(args.get("projectId") or args.get("project_id") or "").strip()
    return raw or None


def _job_dir(dept_id: str, project_id: str | None, job_id: str) -> Path:
    if project_id:
        return _project_dir(dept_id, project_id) / "jobs" / job_id
    return _workspace(dept_id) / "video_jobs" / job_id


def _job_manifest_path(record: dict[str, Any]) -> Path | None:
    raw = str(record.get("manifestPath") or "").strip()
    return Path(raw) if raw else None


def _job_log_path(record: dict[str, Any]) -> Path | None:
    raw = str(record.get("logPath") or "").strip()
    return Path(raw) if raw else None


def _append_job_event(
    record: dict[str, Any],
    message: str,
    *,
    phase: str | None = None,
    percent: int | float | None = None,
    level: str = "info",
    **fields: Any,
) -> dict[str, Any]:
    ts = now_ms()
    event = {
        "ts": ts,
        "level": level,
        "message": str(message or "").strip()[:1000],
        **({"phase": phase} if phase else {}),
        **({"percent": max(0, min(int(percent), 100))} if percent is not None else {}),
        **{key: value for key, value in fields.items() if value is not None},
    }
    events = record.setdefault("events", [])
    if isinstance(events, list):
        events.append(event)
        if len(events) > 200:
            del events[:-200]
    logs = record.setdefault("logs", [])
    if isinstance(logs, list):
        prefix = f"[{phase}] " if phase else ""
        logs.append(f"{prefix}{event['message']}")
        if len(logs) > 200:
            del logs[:-200]
    log_path = _job_log_path(record)
    if log_path is not None:
        log_path.parent.mkdir(parents=True, exist_ok=True)
        with log_path.open("a", encoding="utf-8") as fh:
            fh.write(json.dumps(event, ensure_ascii=False, sort_keys=True) + "\n")
    return event


def _set_job_progress(
    record: dict[str, Any],
    phase: str,
    percent: int | float,
    message: str,
    **fields: Any,
) -> None:
    percent_i = max(0, min(int(percent), 100))
    record["progress"] = {"phase": phase, "percent": percent_i, "message": str(message or "").strip()[:500]}
    _append_job_event(record, message, phase=phase, percent=percent_i, **fields)


def _write_job_manifest(record: dict[str, Any]) -> None:
    path = _job_manifest_path(record)
    if path is not None:
        _write_json(path, record)


async def _put_video_job(repo: Any, record: dict[str, Any]) -> dict[str, Any]:
    record["updatedAt"] = now_ms()
    _write_job_manifest(record)
    await repo.put_entity(
        "video_job",
        record,
        dept=record.get("ownerDept"),
        project=record.get("projectId"),
        status=record.get("status"),
        ts=record.get("updatedAt"),
    )
    return record


def _video_job_public(record: dict[str, Any] | None, queue: dict[str, Any] | None = None) -> dict[str, Any] | None:
    if not record and not queue:
        return None
    record = dict(record or {})
    queue = dict(queue or {})
    status = queue.get("status") or record.get("status")
    progress = record.get("progress") if isinstance(record.get("progress"), dict) else {}
    logs = record.get("logs") if isinstance(record.get("logs"), list) else []
    events = record.get("events") if isinstance(record.get("events"), list) else []
    queue_payload = queue.get("payload") if isinstance(queue.get("payload"), dict) else {}
    return {
        "id": record.get("id") or queue.get("id"),
        "kind": record.get("kind") or queue.get("kind"),
        "tool": record.get("tool") or queue_payload.get("tool"),
        "status": status,
        "queueStatus": queue.get("status"),
        "projectId": record.get("projectId") or queue_payload.get("projectId"),
        "ownerDept": record.get("ownerDept") or queue_payload.get("departmentId"),
        "progress": progress,
        "result": record.get("result") if status in VIDEO_JOB_TERMINAL_STATUSES else None,
        "error": record.get("error") or queue.get("lastError"),
        "attempts": queue.get("attempts"),
        "priority": queue.get("priority"),
        "runAfter": queue.get("runAfter"),
        "createdAt": record.get("createdAt") or queue.get("createdAt"),
        "updatedAt": record.get("updatedAt") or queue.get("updatedAt"),
        "startedAt": record.get("startedAt"),
        "completedAt": record.get("completedAt"),
        "manifestPath": record.get("manifestPath"),
        "logPath": record.get("logPath"),
        "logs": logs[-20:],
        "events": events[-20:],
        "wake": record.get("wake") if isinstance(record.get("wake"), dict) else None,
        "resumeOf": record.get("resumeOf") or queue_payload.get("resumeOf"),
        "statusUrl": record.get("statusUrl") or f"atrium://video/jobs/{record.get('id') or queue.get('id')}",
        "cancelSupported": status not in VIDEO_JOB_TERMINAL_STATUSES,
        "resumeSupported": status in {"failed", "cancelled"},
    }


async def _queue_video_job(repo: Any, run: dict[str, Any], args: dict[str, Any]) -> dict[str, Any]:
    tool = str(run.get("tool") or "")
    if tool not in VIDEO_BACKGROUND_TOOL_NAMES:
        raise ValueError(f"{tool} does not support background mode")
    dept_id = run["departmentId"]
    project_id = _job_project_id(args)
    if project_id:
        _load_project(dept_id, project_id)
    now = now_ms()
    job_id = uid("vjob")
    clean_args = copy.deepcopy(args)
    for key in ("asyncMode", "async", "background", "waitForResult", "wait_for_result"):
        clean_args.pop(key, None)
    job_path = _job_dir(dept_id, project_id, job_id)
    job_path.mkdir(parents=True, exist_ok=True)
    log_path = job_path / "events.jsonl"
    payload = {
        "jobId": job_id,
        "tool": tool,
        "departmentId": dept_id,
        "projectId": project_id,
        "threadId": run.get("threadId") or args.get("threadId") or args.get("thread_id"),
        "requestedBy": run.get("requestedBy") or dept_id,
        "parentToolRunId": run.get("id"),
        "args": clean_args,
        "wakeOnComplete": bool(args.get("wakeOnComplete") or args.get("wake_on_complete")),
        "statusUrl": f"atrium://video/jobs/{job_id}",
        "logPath": str(log_path),
    }
    record = {
        "id": job_id,
        "kind": "video_tool",
        "tool": tool,
        "status": "queued",
        "projectId": project_id,
        "threadId": run.get("threadId") or args.get("threadId") or args.get("thread_id"),
        "ownerDept": dept_id,
        "requestedBy": payload["requestedBy"],
        "parentToolRunId": run.get("id"),
        "args": clean_args,
        "payload": payload,
        "createdAt": now,
        "updatedAt": now,
        "progress": {"phase": "queued", "percent": 0, "message": f"queued {tool}"},
        "logs": [],
        "events": [],
        "manifestPath": str(job_path / "job.json"),
        "logPath": str(log_path),
        "statusUrl": f"atrium://video/jobs/{job_id}",
    }
    _append_job_event(record, f"queued {tool}", phase="queued", percent=0, queueKind="video_tool")
    await repo.enqueue(job_id, "video_tool", payload, now, priority=int(args.get("priority") or 4))
    await _put_video_job(repo, record)
    public = _video_job_public(record, await repo.get_job(job_id))
    return {
        "ok": True,
        "background": True,
        "status": "queued",
        "jobId": job_id,
        "statusUrl": f"atrium://video/jobs/{job_id}",
        "job": public,
    }


async def _job_status(repo: Any, args: dict[str, Any]) -> dict[str, Any]:
    job_id = str(args.get("jobId") or args.get("job_id") or args.get("id") or "").strip()
    if not job_id:
        raise ValueError("jobId is required")
    record = await repo.get_entity("video_job", job_id)
    queue = await repo.get_job(job_id)
    public = _video_job_public(record if isinstance(record, dict) else None, queue)
    if not public:
        raise ValueError(f"video job not found: {job_id}")
    include_result = args.get("includeResult", args.get("include_result", True)) is not False
    if not include_result:
        public.pop("result", None)
    tail = max(1, min(int(args.get("tailLogs") or args.get("tail_logs") or 20), 200))
    if isinstance(public.get("logs"), list):
        public["logs"] = public["logs"][-tail:]
    if isinstance(public.get("events"), list):
        public["events"] = public["events"][-tail:]
    manifest = None
    if bool(args.get("includeManifest") or args.get("include_manifest")) and isinstance(record, dict):
        manifest = record
    return {"ok": True, "job": public, "queue": queue, **({"manifest": manifest} if manifest is not None else {})}


async def _queue_video_completion_wake(
    repo: Any,
    payload: dict[str, Any],
    record: dict[str, Any],
    *,
    error: str | None = None,
) -> dict[str, Any] | None:
    wake = record.get("wake") if isinstance(record.get("wake"), dict) else {}
    if wake.get("jobId"):
        return None
    thread_id = str(payload.get("threadId") or record.get("threadId") or "").strip()
    if not thread_id:
        record["wake"] = {"status": "skipped", "reason": "no_thread", "checkedAt": now_ms()}
        await _put_video_job(repo, record)
        return None
    now = now_ms()
    job_id = str(record.get("id") or payload.get("jobId") or "")
    dept_id = str(record.get("ownerDept") or payload.get("departmentId") or "")
    dept = await repo.get_department(dept_id) if dept_id else None
    dept = dept or {"id": dept_id or "exec", "name": "ATRIUM", "agentName": "ATRIUM"}
    source = await repo.latest_user_message(thread_id)
    source_id = str((source or {}).get("id") or uid("msg"))
    source_text = str((source or {}).get("text") or "")
    status = str(record.get("status") or "done")
    instruction = (
        f"Video job {job_id} finished with status {status}. Inspect the video job ground truth, result data, logs, and any artifact paths before answering the user."
    )
    reply_id = uid("msg")
    wake_job_id = uid("job")
    video_wake = {
        "jobId": job_id,
        "status": status,
        "tool": record.get("tool") or payload.get("tool"),
        "parentToolRunId": record.get("parentToolRunId") or payload.get("parentToolRunId"),
        "error": error,
        "queuedAt": now,
    }
    reply = {
        "id": reply_id,
        "threadId": thread_id,
        "role": "executive" if is_exec(str(dept.get("id") or "")) else "agent",
        "authorName": dept.get("agentName") or dept.get("name") or "ATRIUM",
        "text": (
            "งานวิดีโอเสร็จแล้ว กำลังให้ AI กลับมาตรวจผลลัพธ์และสรุปให้..."
            if status == "done"
            else "งานวิดีโอจบด้วยข้อผิดพลาด กำลังให้ AI กลับมาตรวจและแนะนำขั้นตอนถัดไป..."
        ),
        "ts": now,
        "pending": True,
        "status": "queued",
        "replyToMessageId": source_id,
        "videoJobWake": video_wake,
        **agent_message_metadata(dept),
    }
    await repo.add_message(reply)
    await repo.enqueue(
        wake_job_id,
        "chat_reply",
        {
            "threadId": thread_id,
            "departmentId": dept.get("id") or dept_id,
            "userMessageId": source_id,
            "replyMessageId": reply_id,
            "text": source_text or instruction,
            "userTs": int((source or {}).get("ts") or record.get("completedAt") or now),
            "replyTs": now,
            "thinkingEffort": "low",
            "speed": "fast",
            "attachments": (source or {}).get("attachments") or [],
            "mentions": (source or {}).get("mentions") or [],
            "videoJobWake": video_wake,
            "statusMessage": str(payload.get("statusMessage") or "").strip()[:500],
        },
        now,
        priority=1,
    )
    record["wake"] = {
        "status": "queued",
        "jobId": wake_job_id,
        "replyMessageId": reply_id,
        "queuedAt": now,
    }
    await _put_video_job(repo, record)
    hub.pulse({
        "kind": "video_job_wake_queued",
        "threadId": thread_id,
        "msgId": reply_id,
        "departmentId": dept.get("id"),
        "jobId": job_id,
        "reply": reply,
    })
    hub.mark_dirty()
    return reply


async def _cancel_job(repo: Any, run: dict[str, Any], args: dict[str, Any]) -> dict[str, Any]:
    job_id = str(args.get("jobId") or args.get("job_id") or args.get("id") or "").strip()
    if not job_id:
        raise ValueError("jobId is required")
    record = await repo.get_entity("video_job", job_id)
    queue = await repo.get_job(job_id)
    if not record and not queue:
        raise ValueError(f"video job not found: {job_id}")
    reason = str(args.get("reason") or "cancelled by video.cancel_job").strip()[:500]
    if queue and queue.get("status") not in VIDEO_JOB_TERMINAL_STATUSES:
        await repo.mark_job(job_id, "cancelled", error=reason)
    now = now_ms()
    updated = dict(record or {
        "id": job_id,
        "kind": "video_tool",
        "tool": (queue or {}).get("payload", {}).get("tool"),
        "projectId": (queue or {}).get("payload", {}).get("projectId"),
        "ownerDept": (queue or {}).get("payload", {}).get("departmentId") or run.get("departmentId"),
        "createdAt": (queue or {}).get("createdAt") or now,
        "manifestPath": str(_job_dir(run["departmentId"], (queue or {}).get("payload", {}).get("projectId"), job_id) / "job.json"),
    })
    updated["status"] = "cancelled"
    updated["error"] = reason
    updated["completedAt"] = now
    if not updated.get("logPath"):
        updated["logPath"] = str(_job_dir(str(updated.get("ownerDept") or run["departmentId"]), updated.get("projectId"), job_id) / "events.jsonl")
    updated["statusUrl"] = updated.get("statusUrl") or f"atrium://video/jobs/{job_id}"
    _set_job_progress(updated, "cancelled", int((updated.get("progress") or {}).get("percent") or 0), reason, reason=reason)
    await _put_video_job(repo, updated)
    return {"ok": True, "cancelled": True, "job": _video_job_public(updated, await repo.get_job(job_id))}


async def _resume_job(repo: Any, run: dict[str, Any], args: dict[str, Any]) -> dict[str, Any]:
    previous_id = str(args.get("jobId") or args.get("job_id") or args.get("id") or "").strip()
    if not previous_id:
        raise ValueError("jobId is required")
    previous = await repo.get_entity("video_job", previous_id)
    previous_queue = await repo.get_job(previous_id)
    if not previous and not previous_queue:
        raise ValueError(f"video job not found: {previous_id}")
    previous_payload = {}
    if isinstance((previous or {}).get("payload"), dict):
        previous_payload = copy.deepcopy((previous or {}).get("payload") or {})
    elif isinstance((previous_queue or {}).get("payload"), dict):
        previous_payload = copy.deepcopy((previous_queue or {}).get("payload") or {})
    status = str((previous or {}).get("status") or (previous_queue or {}).get("status") or "").strip()
    if status not in {"failed", "cancelled"}:
        raise ValueError(f"video job {previous_id} cannot be resumed from status {status or 'unknown'}")
    tool = str((previous or {}).get("tool") or previous_payload.get("tool") or "").strip()
    if tool not in VIDEO_BACKGROUND_TOOL_NAMES:
        raise ValueError(f"{tool or 'unknown'} does not support video job resume")
    dept_id = str((previous or {}).get("ownerDept") or previous_payload.get("departmentId") or run.get("departmentId") or "").strip()
    project_id = str((previous or {}).get("projectId") or previous_payload.get("projectId") or "").strip() or None
    if project_id:
        _load_project(dept_id, project_id)
    clean_args = copy.deepcopy((previous or {}).get("args") if isinstance((previous or {}).get("args"), dict) else previous_payload.get("args") if isinstance(previous_payload.get("args"), dict) else {})
    for key in ("asyncMode", "async", "background", "waitForResult", "wait_for_result"):
        clean_args.pop(key, None)
    now = now_ms()
    job_id = uid("vjob")
    job_path = _job_dir(dept_id, project_id, job_id)
    job_path.mkdir(parents=True, exist_ok=True)
    log_path = job_path / "events.jsonl"
    wake_on_complete = bool(args.get("wakeOnComplete") or args.get("wake_on_complete") or previous_payload.get("wakeOnComplete"))
    payload = {
        "jobId": job_id,
        "tool": tool,
        "departmentId": dept_id,
        "projectId": project_id,
        "threadId": previous_payload.get("threadId") or (previous or {}).get("threadId") or args.get("threadId") or args.get("thread_id"),
        "requestedBy": run.get("requestedBy") or (previous or {}).get("requestedBy") or previous_payload.get("requestedBy") or dept_id,
        "parentToolRunId": run.get("id"),
        "args": clean_args,
        "wakeOnComplete": wake_on_complete,
        "statusMessage": str(args.get("statusMessage") or args.get("status_message") or previous_payload.get("statusMessage") or "").strip()[:500],
        "resumeOf": previous_id,
        "statusUrl": f"atrium://video/jobs/{job_id}",
        "logPath": str(log_path),
    }
    record = {
        "id": job_id,
        "kind": "video_tool",
        "tool": tool,
        "status": "queued",
        "projectId": project_id,
        "threadId": payload.get("threadId"),
        "ownerDept": dept_id,
        "requestedBy": payload["requestedBy"],
        "parentToolRunId": run.get("id"),
        "args": clean_args,
        "payload": payload,
        "resumeOf": previous_id,
        "createdAt": now,
        "updatedAt": now,
        "progress": {"phase": "queued", "percent": 0, "message": f"resumed {tool} from {previous_id}"},
        "logs": [],
        "events": [],
        "manifestPath": str(job_path / "job.json"),
        "logPath": str(log_path),
        "statusUrl": f"atrium://video/jobs/{job_id}",
    }
    _append_job_event(record, f"resumed {tool} from {previous_id}", phase="queued", percent=0, resumeOf=previous_id)
    priority = int(args.get("priority") or (previous_queue or {}).get("priority") or 4)
    await repo.enqueue(job_id, "video_tool", payload, now, priority=priority)
    await _put_video_job(repo, record)
    if isinstance(previous, dict):
        previous.setdefault("resumes", []).append({"jobId": job_id, "queuedAt": now})
        if not previous.get("logPath"):
            previous["logPath"] = str(_job_dir(str(previous.get("ownerDept") or dept_id), previous.get("projectId") or project_id, previous_id) / "events.jsonl")
        _append_job_event(previous, f"queued resume job {job_id}", phase="resumed", percent=(previous.get("progress") or {}).get("percent"), resumeJobId=job_id)
        await _put_video_job(repo, previous)
    return {
        "ok": True,
        "resumed": True,
        "jobId": job_id,
        "job": _video_job_public(record, await repo.get_job(job_id)),
        "previousJob": _video_job_public(previous if isinstance(previous, dict) else None, previous_queue),
    }


async def process_video_job(repo: Any, payload: dict[str, Any], now: int | None = None) -> dict[str, Any]:
    job_id = str(payload.get("jobId") or "").strip()
    tool = str(payload.get("tool") or "").strip()
    dept_id = str(payload.get("departmentId") or "").strip()
    if not job_id or not tool or not dept_id:
        raise ValueError("video job payload requires jobId, tool, and departmentId")
    if tool not in VIDEO_BACKGROUND_TOOL_NAMES:
        raise ValueError(f"unsupported video job tool: {tool}")
    record = await repo.get_entity("video_job", job_id)
    if not isinstance(record, dict):
        job_path = _job_dir(dept_id, _job_project_id(payload), job_id)
        record = {
            "id": job_id,
            "kind": "video_tool",
            "tool": tool,
            "status": "running",
            "projectId": payload.get("projectId"),
            "ownerDept": dept_id,
            "createdAt": now or now_ms(),
            "manifestPath": str(job_path / "job.json"),
            "logPath": str(job_path / "events.jsonl"),
            "statusUrl": f"atrium://video/jobs/{job_id}",
            "logs": [],
            "events": [],
        }
    if not record.get("logPath"):
        record["logPath"] = str(_job_dir(dept_id, record.get("projectId") or payload.get("projectId"), job_id) / "events.jsonl")
    record["statusUrl"] = record.get("statusUrl") or f"atrium://video/jobs/{job_id}"
    record["payload"] = record.get("payload") if isinstance(record.get("payload"), dict) else payload
    record["status"] = "running"
    record["startedAt"] = record.get("startedAt") or now_ms()
    _set_job_progress(record, "running", 10, f"started {tool}")
    await _put_video_job(repo, record)
    args = copy.deepcopy(payload.get("args") if isinstance(payload.get("args"), dict) else {})
    args["_videoJobId"] = job_id
    run = {
        "id": payload.get("parentToolRunId") or job_id,
        "tool": tool,
        "departmentId": dept_id,
        "requestedBy": payload.get("requestedBy") or dept_id,
        "args": args,
    }
    try:
        if tool == "video.render_edit":
            work_phase = "rendering"
        elif tool == "video.render_motion":
            work_phase = "rendering" if args.get("render") or args.get("renderNow") or args.get("render_now") else "packaging"
        else:
            work_phase = "transcribing"
        _set_job_progress(record, work_phase, 35, f"{work_phase} with {tool}")
        await _put_video_job(repo, record)
        if tool == "video.render_edit":
            result = await _render_edit(repo, run, args)
        elif tool == "video.render_motion":
            result = await _render_motion(repo, run, args)
        elif tool == "video.transcribe":
            result = await _transcribe(repo, run, args)
        else:
            raise ValueError(f"unsupported video job tool: {tool}")
        if result.get("ok") is False:
            raise ValueError(str(result.get("dependencyMissing") or result.get("error") or f"{tool} returned ok=false"))
        _set_job_progress(record, "finalizing", 90, f"persisting {tool} result")
        await _put_video_job(repo, record)
        record["status"] = "done"
        record["completedAt"] = now_ms()
        record["result"] = result
        _set_job_progress(record, "done", 100, f"completed {tool}")
        await _put_video_job(repo, record)
        if payload.get("wakeOnComplete"):
            await _queue_video_completion_wake(repo, payload, record)
            await repo.add_activity({
                "id": uid("ev"),
                "ts": record["completedAt"],
                "type": "autonomous",
                "departmentId": dept_id,
                "text": f"Video job {job_id} completed: {tool}",
                "severity": "good",
            })
        return result
    except Exception as exc:
        record["status"] = "failed"
        record["completedAt"] = now_ms()
        record["error"] = f"{type(exc).__name__}: {exc}"
        _set_job_progress(record, "failed", int((record.get("progress") or {}).get("percent") or 10), record["error"], level="error")
        await _put_video_job(repo, record)
        if payload.get("wakeOnComplete"):
            await _queue_video_completion_wake(repo, payload, record, error=record["error"])
            await repo.add_activity({
                "id": uid("ev"),
                "ts": record["completedAt"],
                "type": "autonomous",
                "departmentId": dept_id,
                "text": f"Video job {job_id} failed: {record['error']}",
                "severity": "warn",
            })
        raise


async def _create_project(repo: Any, run: dict[str, Any], args: dict[str, Any]) -> dict[str, Any]:
    dept_id = run["departmentId"]
    project_id = _project_id(args.get("projectId") or args.get("project_id"))
    now = now_ms()
    name = str(args.get("name") or "AI video project").strip()[:160]
    root = _project_dir(dept_id, project_id)
    for child in ("source", "assets", "specs", "renders", "frames", "logs", "transcripts", "storyboards", "segments", "jobs"):
        (root / child).mkdir(parents=True, exist_ok=True)
    project = {
        "id": project_id,
        "name": name,
        "ownerDept": dept_id,
        "workspace": str(root),
        "assets": [],
        "timelines": [],
        "renders": [],
        "createdAt": now,
        "createdBy": run.get("requestedBy") or dept_id,
        "updatedAt": now,
        "updatedBy": run.get("requestedBy") or dept_id,
        "version": 1,
    }
    _append_project_audit(project, "project.create", run=run, entity_type="video_project", entity_id=project_id, summary=f"Created video project {name}", paths={"workspace": str(root)})
    _save_project(project)
    await repo.put_entity("video_project", project, dept=dept_id, project=project_id, status="active", ts=now)
    if args.get("sourcePath") or args.get("source") or args.get("artifactId") or args.get("artifact_id"):
        add_result = await _add_asset(repo, run, {**args, "projectId": project_id, "role": "source", "assetType": "video"})
        project = _load_project(dept_id, project_id)
        return {"ok": True, "project": project, "asset": add_result.get("asset"), "context": _media_context(project)}
    return {"ok": True, "project": project, "context": _media_context(project)}


async def _add_asset(repo: Any, run: dict[str, Any], args: dict[str, Any]) -> dict[str, Any]:
    dept_id = run["departmentId"]
    project_id = str(args.get("projectId") or args.get("project_id") or "").strip()
    if not project_id:
        raise ValueError("projectId is required")
    project = _load_project(dept_id, project_id)
    source = await _resolve_source_path(repo, dept_id, args, project=None)
    asset_id = uid("asset")
    asset_type = _asset_type(source, args.get("assetType") or args.get("type"))
    destination = _asset_destination(project, asset_id, source, asset_type)
    destination.parent.mkdir(parents=True, exist_ok=True)
    _copy_file_best_effort(source, destination)
    stat = destination.stat()
    mime = guess_mime(destination.name)
    object_ref = _object_store_file_ref(destination, mime=mime)
    metadata, metadata_profile, metadata_error = _asset_metadata(destination, asset_type)
    asset = {
        "id": asset_id,
        "type": asset_type,
        "role": str(args.get("role") or "").strip() or None,
        "name": safe_filename(str(args.get("name") or source.name)),
        "path": str(destination),
        "sourcePath": str(source),
        "handle": f"atrium://video/projects/{project_id}/assets/{asset_id}",
        "uri": object_ref.get("uri") if object_ref and not object_ref.get("error") else str(destination),
        "storage": "object_store" if object_ref and not object_ref.get("error") else "filesystem",
        "mime": mime,
        "sizeBytes": stat.st_size,
        "sha256": _sha256_file(destination),
        "metadataProfile": metadata_profile,
        "metadata": metadata,
        "createdAt": now_ms(),
    }
    if object_ref:
        asset["objectStore"] = object_ref
    if metadata_error:
        asset["metadataError"] = metadata_error
    manifest = _write_asset_manifest(project, asset)
    clean_asset = {k: v for k, v in asset.items() if v is not None}
    project.setdefault("assets", []).append(clean_asset)
    project.setdefault("assetManifests", []).append({
        "assetId": asset_id,
        "type": asset_type,
        "manifestPath": asset.get("manifestPath"),
        "manifestUri": asset.get("manifestUri"),
        "metadataProfile": metadata_profile,
        "sha256": asset.get("sha256"),
        "uri": asset.get("uri"),
        "storage": asset.get("storage"),
        "createdAt": asset.get("createdAt"),
    })
    _append_project_audit(
        project,
        "asset.add",
        run=run,
        entity_type="video_asset",
        entity_id=asset_id,
        summary=f"Imported {asset_type} asset {asset.get('name')}",
        refs={"assetType": asset_type, "role": asset.get("role"), "mime": mime, "metadataProfile": metadata_profile},
        paths={"source": str(source), "asset": str(destination), "manifest": asset.get("manifestPath")},
        checksum=asset.get("sha256"),
    )
    _append_project_audit(
        project,
        "asset.manifest",
        run=run,
        entity_type="video_asset_manifest",
        entity_id=str(manifest.get("id") or ""),
        summary=f"Wrote metadata manifest for asset {asset_id}",
        refs={"assetId": asset_id, "assetType": asset_type, "metadataProfile": metadata_profile},
        paths={"manifest": asset.get("manifestPath")},
        checksum=asset.get("sha256"),
    )
    _save_project(project)
    await repo.put_entity("video_project", project, dept=dept_id, project=project_id, status="active", ts=project["updatedAt"])
    return {"ok": True, "projectId": project_id, "asset": asset, "context": _media_context(project)}


def _font_dirs() -> list[Path]:
    home = Path.home()
    dirs = [
        Path("/System/Library/Fonts"),
        Path("/Library/Fonts"),
        home / "Library" / "Fonts",
    ]
    windir = os.environ.get("WINDIR") or os.environ.get("SystemRoot")
    if windir:
        dirs.append(Path(windir) / "Fonts")
    local_app_data = os.environ.get("LOCALAPPDATA")
    if local_app_data:
        dirs.append(Path(local_app_data) / "Microsoft/Windows/Fonts")
    return dirs


def _font_records(extra_dirs: list[Path] | None = None) -> list[dict[str, Any]]:
    records: list[dict[str, Any]] = []
    seen: set[str] = set()
    for folder in [*(_font_dirs()), *(extra_dirs or [])]:
        if not folder.is_dir():
            continue
        for path in folder.rglob("*"):
            if path.suffix.lower() not in {".ttf", ".otf", ".ttc"}:
                continue
            key = str(path.resolve())
            if key in seen:
                continue
            seen.add(key)
            records.append({
                "name": path.stem,
                "path": key,
                "familyHint": path.stem.replace("-", " "),
            })
    return sorted(records, key=lambda item: item["name"].lower())


async def _list_fonts(run: dict[str, Any], args: dict[str, Any]) -> dict[str, Any]:
    project_dirs: list[Path] = []
    project_id = str(args.get("projectId") or args.get("project_id") or "").strip()
    if project_id:
        project_dirs.append(_project_dir(run["departmentId"], project_id) / "assets" / "font")
    fonts = _font_records(project_dirs)
    query = str(args.get("query") or "").strip().lower()
    if query:
        fonts = [font for font in fonts if query in font["name"].lower() or query in font["familyHint"].lower()]
    limit = max(1, min(int(args.get("limit") or 100), 500))
    return {"ok": True, "fonts": fonts[:limit], "count": min(len(fonts), limit), "total": len(fonts)}


async def _inspect(repo: Any, run: dict[str, Any], args: dict[str, Any]) -> dict[str, Any]:
    dept_id = run["departmentId"]
    project = None
    project_id = str(args.get("projectId") or args.get("project_id") or "").strip()
    if project_id:
        project = _load_project(dept_id, project_id)
    source = await _resolve_source_path(repo, dept_id, args, project=project)
    metadata = _ffprobe(source)
    return {
        "ok": True,
        "source": str(source),
        "metadata": metadata,
        "context": _media_context(project, asset_path=source) if project else {"sourcePath": str(source), "metadata": metadata},
    }


async def _sample_frames(repo: Any, run: dict[str, Any], args: dict[str, Any]) -> dict[str, Any]:
    dept_id = run["departmentId"]
    project_id = str(args.get("projectId") or args.get("project_id") or "").strip()
    project = _load_project(dept_id, project_id) if project_id else None
    source = await _resolve_source_path(repo, dept_id, args, project=project)
    probe = _ffprobe(source)
    duration = float((probe.get("format") or {}).get("duration") or (probe.get("video") or {}).get("duration") or 0)
    timestamps = _timestamps(args, duration)
    if not timestamps:
        timestamps = [0.0]
    out_dir = (_project_dir(dept_id, project_id) / "frames" if project else _workspace(dept_id) / "video_frames" / uid("frames"))
    out_dir.mkdir(parents=True, exist_ok=True)
    ffmpeg = _require_ffmpeg()
    artifacts: list[dict[str, Any]] = []
    frames: list[dict[str, Any]] = []
    errors: list[dict[str, Any]] = []
    for idx, ts in enumerate(timestamps, start=1):
        frame_path = out_dir / f"frame_{idx:03d}_{int(ts * 1000):08d}ms.png"
        result = _run(
            [ffmpeg, "-y", "-ss", f"{ts:.3f}", "-i", str(source), "-frames:v", "1", "-q:v", "2", str(frame_path)],
            timeout=60.0,
        )
        if result["returnCode"] != 0 or not frame_path.is_file():
            errors.append({"timestamp": ts, "error": (result.get("stderr") or "frame extraction failed")[-1000:]})
            continue
        artifact = await _persist_file_artifact(
            repo,
            path=frame_path,
            owner_dept=dept_id,
            created_by=str(run.get("requestedBy") or dept_id),
            name=frame_path.name,
            tags=["video_frame", "sample_frame"],
            project_id=project_id or None,
            note=f"sampled video frame at {ts:.3f}s",
        )
        artifacts.append(artifact)
        frames.append({"t": ts, "path": str(frame_path), "artifactId": artifact["id"], "uri": artifact["uri"]})
    return {
        "ok": not errors,
        "source": str(source),
        "frames": frames,
        "artifacts": artifacts,
        "errors": errors,
        "context": _media_context(project, frames=frames) if project else {"sourcePath": str(source), "frames": frames},
    }


async def _storyboard(repo: Any, run: dict[str, Any], args: dict[str, Any]) -> dict[str, Any]:
    dept_id = run["departmentId"]
    project_id = str(args.get("projectId") or args.get("project_id") or "").strip()
    project = _load_project(dept_id, project_id) if project_id else None
    source = await _resolve_source_path(repo, dept_id, args, project=project)
    count = max(1, min(int(args.get("count") or 6), 24))
    frame_args = {**args, "count": count}
    if "timestamps" not in frame_args and "times" not in frame_args:
        frame_args.setdefault("start", args.get("start", 0))
    frame_result = await _sample_frames(repo, run, frame_args)
    frames = frame_result.get("frames") if isinstance(frame_result.get("frames"), list) else []
    if not frames:
        raise ValueError("no frames available for storyboard")
    out_dir = (_project_dir(dept_id, project_id) / "storyboards" if project else _workspace(dept_id) / "video_storyboards" / uid("storyboard"))
    out_dir.mkdir(parents=True, exist_ok=True)
    storyboard_path = out_dir / f"storyboard_{uid('sb')}.png"
    _make_storyboard_image(frames, storyboard_path, columns=max(1, min(int(args.get("columns") or 3), 8)))
    artifact = await _persist_file_artifact(
        repo,
        path=storyboard_path,
        owner_dept=dept_id,
        created_by=str(run.get("requestedBy") or dept_id),
        name=str(args.get("artifactName") or args.get("artifact_name") or storyboard_path.name),
        tags=["video_storyboard", "visual_inspection"],
        project_id=project_id or None,
        note=f"created storyboard from {safe_filename(source.name)}",
    )
    context = _media_context(project, frames=frames, storyboard={"artifactId": artifact["id"], "uri": artifact["uri"]}) if project else {
        "type": "video_context",
        "sourcePath": str(source),
        "frames": frames,
        "storyboard": {"artifactId": artifact["id"], "uri": artifact["uri"]},
    }
    return {
        "ok": True,
        "source": str(source),
        "storyboard": {"path": str(storyboard_path), "artifactId": artifact["id"], "uri": artifact["uri"]},
        "frames": frames,
        "artifact": artifact,
        "artifacts": [artifact, *(frame_result.get("artifacts") or [])],
        "context": context,
    }


def _make_storyboard_image(frames: list[dict[str, Any]], output_path: Path, *, columns: int) -> None:
    try:
        from PIL import Image, ImageDraw, ImageFont
    except Exception as exc:
        raise ValueError("Pillow is required for storyboard generation") from exc
    images: list[tuple[float, Any]] = []
    for frame in frames:
        path = Path(str(frame.get("path") or ""))
        if not path.is_file():
            continue
        try:
            images.append((float(frame.get("t") or 0), Image.open(path).convert("RGB")))
        except Exception:
            continue
    if not images:
        raise ValueError("storyboard frame files are unavailable")
    thumb_w = 320
    thumb_h = 180
    label_h = 28
    rows = (len(images) + columns - 1) // columns
    sheet = Image.new("RGB", (columns * thumb_w, rows * (thumb_h + label_h)), "#101010")
    draw = ImageDraw.Draw(sheet)
    try:
        font = ImageFont.load_default(size=14)
    except TypeError:
        font = ImageFont.load_default()
    for idx, (timestamp, image) in enumerate(images):
        row, col = divmod(idx, columns)
        image.thumbnail((thumb_w, thumb_h))
        x = col * thumb_w + (thumb_w - image.width) // 2
        y = row * (thumb_h + label_h)
        sheet.paste(image, (x, y))
        draw.rectangle((col * thumb_w, y + thumb_h, (col + 1) * thumb_w, y + thumb_h + label_h), fill="#191919")
        draw.text((col * thumb_w + 8, y + thumb_h + 7), f"{timestamp:.2f}s", fill="#f5f5f5", font=font)
    output_path.parent.mkdir(parents=True, exist_ok=True)
    sheet.save(output_path)


async def _inspect_segment(repo: Any, run: dict[str, Any], args: dict[str, Any]) -> dict[str, Any]:
    dept_id = run["departmentId"]
    project_id = str(args.get("projectId") or args.get("project_id") or "").strip()
    project = _load_project(dept_id, project_id) if project_id else None
    source = await _resolve_source_path(repo, dept_id, args, project=project)
    probe = _ffprobe(source)
    duration = float((probe.get("format") or {}).get("duration") or (probe.get("video") or {}).get("duration") or 0)
    start = max(0.0, _float_or_none(args.get("start")) or 0.0)
    end = _float_or_none(args.get("end"))
    if end is None:
        end = min(duration or start + 10.0, start + float(args.get("duration") or 10.0))
    end = max(start + 0.1, min(float(end), duration or float(end)))
    preview_artifact = None
    preview_path = None
    if args.get("preview", True) is not False:
        out_dir = (_project_dir(dept_id, project_id) / "segments" if project else _workspace(dept_id) / "video_segments" / uid("segment"))
        out_dir.mkdir(parents=True, exist_ok=True)
        preview_path = out_dir / f"segment_{int(start * 1000)}_{int(end * 1000)}.mp4"
        ffmpeg = _require_ffmpeg()
        result = _run(
            [
                ffmpeg,
                "-y",
                "-ss",
                f"{start:.3f}",
                "-to",
                f"{end:.3f}",
                "-i",
                str(source),
                "-c:v",
                "libx264",
                "-preset",
                "veryfast",
                "-crf",
                "22",
                "-c:a",
                "aac",
                "-movflags",
                "+faststart",
                str(preview_path),
            ],
            timeout=float(args.get("timeoutSeconds") or 300),
        )
        if result["returnCode"] != 0 or not preview_path.is_file():
            raise ValueError(f"segment preview render failed: {(result.get('stderr') or '')[-1500:]}")
        preview_artifact = await _persist_file_artifact(
            repo,
            path=preview_path,
            owner_dept=dept_id,
            created_by=str(run.get("requestedBy") or dept_id),
            name=str(args.get("previewName") or f"segment-{start:.2f}-{end:.2f}.mp4"),
            tags=["video_segment", "visual_inspection"],
            project_id=project_id or None,
            note=f"created segment preview {start:.3f}-{end:.3f}s",
        )
    storyboard_result = await _storyboard(
        repo,
        run,
        {
            **args,
            "start": start,
            "end": end,
            "count": int(args.get("frameCount") or args.get("count") or 4),
            "columns": int(args.get("columns") or 2),
        },
    )
    frames = storyboard_result.get("frames") or []
    segment = {"start": start, "end": end, "duration": round(end - start, 3)}
    context = _media_context(
        project,
        frames=frames,
        segment=segment,
        storyboard=storyboard_result.get("storyboard"),
        preview={"artifactId": (preview_artifact or {}).get("id"), "uri": (preview_artifact or {}).get("uri"), "path": str(preview_path) if preview_path else None},
    ) if project else {
        "type": "video_context",
        "sourcePath": str(source),
        "segment": segment,
        "frames": frames,
        "storyboard": storyboard_result.get("storyboard"),
    }
    artifacts = []
    if preview_artifact:
        artifacts.append(preview_artifact)
    artifacts.extend(storyboard_result.get("artifacts") or [])
    return {
        "ok": True,
        "source": str(source),
        "segment": segment,
        "preview": {"path": str(preview_path) if preview_path else None, "artifactId": (preview_artifact or {}).get("id"), "uri": (preview_artifact or {}).get("uri")},
        "storyboard": storyboard_result.get("storyboard"),
        "frames": frames,
        "artifacts": artifacts,
        "context": context,
    }


def _timestamps(args: dict[str, Any], duration: float) -> list[float]:
    raw = args.get("timestamps") or args.get("times")
    if isinstance(raw, list):
        out = []
        for item in raw[:50]:
            value = _float_or_none(item)
            if value is not None:
                out.append(max(0.0, min(value, max(duration, value))))
        return out
    interval = _float_or_none(args.get("intervalSeconds") or args.get("interval"))
    count = int(args.get("count") or 0)
    if interval and interval > 0 and duration > 0:
        values = []
        t = 0.0
        while t <= duration and len(values) < min(count or 50, 50):
            values.append(round(t, 3))
            t += interval
        return values
    if count > 0 and duration > 0:
        count = max(1, min(count, 50))
        if count == 1:
            return [min(duration, max(0.0, _float_or_none(args.get("start")) or 0.0))]
        start = max(0.0, _float_or_none(args.get("start")) or 0.0)
        end = min(duration, _float_or_none(args.get("end")) or duration)
        step = (end - start) / max(count - 1, 1)
        return [round(start + step * idx, 3) for idx in range(count)]
    return []


async def _render_edit(repo: Any, run: dict[str, Any], args: dict[str, Any]) -> dict[str, Any]:
    dept_id = run["departmentId"]
    project_id = str(args.get("projectId") or args.get("project_id") or "").strip()
    if not project_id:
        raise ValueError("projectId is required")
    project = _load_project(dept_id, project_id)
    spec = args.get("timeline") or args.get("spec") or args.get("editSpec")
    if not isinstance(spec, dict):
        timeline_id = str(args.get("timelineId") or args.get("timeline_id") or "").strip()
        version = str(args.get("version") or args.get("timelineVersion") or args.get("timeline_version") or "").strip()
        if timeline_id:
            spec = _load_timeline_spec(project, timeline_id, version or None)
        else:
            raise ValueError("timeline spec or timelineId is required")
    spec = _normalize_timeline_spec(project, spec, args)
    render_id = uid("render")
    render_dir = _project_dir(dept_id, project_id) / "renders" / render_id
    render_dir.mkdir(parents=True, exist_ok=True)
    spec_path = _save_timeline_spec(project, spec, source="render_edit")
    rendered = await asyncio.to_thread(_render_ffmpeg, project, spec, render_dir)
    render_hash = _sha256_file(rendered)
    spec_hash = _sha256_file(spec_path)
    manifest = {
        "id": render_id,
        "projectId": project_id,
        "timelineId": spec["id"],
        "timelineVersion": spec["version"],
        "kind": str(args.get("kind") or "preview"),
        "path": str(rendered),
        "specPath": str(spec_path),
        "sha256": render_hash,
        "specSha256": spec_hash,
        "createdAt": now_ms(),
        "renderer": "ffmpeg",
        "mediaContext": _media_context(project, timeline=spec),
    }
    manifest_path = render_dir / "manifest.json"
    _write_json(manifest_path, manifest)
    artifact = await _persist_file_artifact(
        repo,
        path=rendered,
        owner_dept=dept_id,
        created_by=str(run.get("requestedBy") or dept_id),
        name=str(args.get("outputName") or args.get("output_name") or f"{project['name']}-{render_id}.mp4"),
        tags=["video_render", str(args.get("kind") or "preview")],
        project_id=project_id,
        preview_kind="md",
        preview_uri=str(manifest_path),
        note=f"rendered video timeline {spec['id']} v{spec['version']}",
    )
    manifest["artifactId"] = artifact["id"]
    manifest["artifactUri"] = artifact.get("uri")
    manifest["artifactStorage"] = artifact.get("storage")
    manifest["artifactObjectStore"] = artifact.get("objectStore") if isinstance(artifact.get("objectStore"), dict) else None
    manifest["downloadUrl"] = f"/api/artifacts/{artifact['id']}/download"
    manifest["previewUrl"] = f"/api/artifacts/{artifact['id']}/preview"
    artifact.update(_video_artifact_context_fields(project, artifact["id"], timeline=spec, render=manifest))
    await repo.put_entity("artifact", artifact, dept=dept_id, project=project_id, status=artifact.get("status"), ts=manifest["createdAt"])
    manifest["manifestPath"] = str(manifest_path)
    _write_json(manifest_path, manifest)
    project.setdefault("renders", []).append(manifest)
    _append_project_audit(
        project,
        "render.done",
        run=run,
        entity_type="video_render",
        entity_id=render_id,
        summary=f"Rendered {manifest['kind']} video from {spec['id']} v{spec['version']}",
        refs={"timelineId": spec["id"], "timelineVersion": spec["version"], "artifactId": artifact["id"], "renderer": "ffmpeg"},
        paths={"render": str(rendered), "manifest": str(manifest_path), "spec": str(spec_path)},
        checksum=render_hash,
    )
    _save_project(project)
    await repo.put_entity("video_project", project, dept=dept_id, project=project_id, status="active", ts=project["updatedAt"])
    await repo.put_entity("video_render", {**manifest, "id": render_id}, dept=dept_id, project=project_id, status="done", ts=manifest["createdAt"])
    return {
        "ok": True,
        "render": manifest,
        "artifact": artifact,
        "artifacts": [artifact],
        "context": manifest["mediaContext"],
    }


def _normalize_timeline_spec(project: dict[str, Any], spec: dict[str, Any], args: dict[str, Any]) -> dict[str, Any]:
    now = now_ms()
    out = copy.deepcopy(spec)
    out["id"] = _timeline_id(out.get("id") or args.get("timelineId") or args.get("timeline_id"))
    out["projectId"] = project["id"]
    out["version"] = int(out.get("version") or 1)
    out["createdAt"] = out.get("createdAt") or now
    out["updatedAt"] = now
    out.setdefault("canvas", {})
    out["canvas"]["width"] = int(out["canvas"].get("width") or args.get("width") or 1080)
    out["canvas"]["height"] = int(out["canvas"].get("height") or args.get("height") or 1920)
    out["canvas"]["fps"] = float(out["canvas"].get("fps") or args.get("fps") or 30)
    out.setdefault("clips", [])
    out.setdefault("text", out.pop("texts", []))
    out.setdefault("captions", [])
    out.setdefault("audio", [])
    out.setdefault("overlays", [])
    out.setdefault("effects", [])
    style_guide = _timeline_style_from_args(args, "", out["canvas"], spec=out)
    if style_guide:
        _apply_timeline_style(out, style_guide)
    out.setdefault("export", {})
    out["export"].setdefault("format", "mp4")
    out["export"].setdefault("quality", "social_1080p")
    return out


def _save_timeline_spec(project: dict[str, Any], spec: dict[str, Any], *, source: str) -> Path:
    project_id = str(project["id"])
    dept_id = str(project["ownerDept"])
    existing_versions = [
        int(item.get("version") or 0)
        for item in project.get("timelines") or []
        if item.get("id") == spec["id"]
    ]
    spec["version"] = max([int(spec.get("version") or 1), *(v + 1 for v in existing_versions)] or [1])
    spec["updatedAt"] = now_ms()
    spec_path = _project_dir(dept_id, project_id) / "specs" / spec["id"] / f"v{spec['version']}.json"
    _write_json(spec_path, spec)
    spec_hash = _sha256_file(spec_path)
    spec_object_ref = _object_store_file_ref(spec_path, mime="application/json")
    timeline_entry = {
        "id": spec["id"],
        "version": spec["version"],
        "path": str(spec_path),
        "source": source,
        "sha256": spec_hash,
        **({"parent": spec.get("parent")} if isinstance(spec.get("parent"), dict) else {}),
        "createdAt": spec["updatedAt"],
    }
    if spec_object_ref:
        timeline_entry["objectStore"] = spec_object_ref
        if not spec_object_ref.get("error") and spec_object_ref.get("uri"):
            timeline_entry["uri"] = spec_object_ref["uri"]
            timeline_entry["storage"] = "object_store"
    project.setdefault("timelines", []).append(timeline_entry)
    _append_project_audit(
        project,
        "timeline.save",
        entity_type="video_timeline",
        entity_id=spec["id"],
        summary=f"Saved timeline {spec['id']} v{spec['version']} from {source}",
        refs={"timelineId": spec["id"], "version": spec["version"], "source": source, "uri": timeline_entry.get("uri"), **({"parent": spec.get("parent")} if isinstance(spec.get("parent"), dict) else {})},
        paths={"spec": str(spec_path), **({"object": (spec_object_ref or {}).get("path")} if spec_object_ref and not spec_object_ref.get("error") else {})},
        checksum=spec_hash,
    )
    _save_project(project)
    return spec_path


def _load_timeline_spec(project: dict[str, Any], timeline_id: str, version: str | None = None) -> dict[str, Any]:
    timeline_id = _timeline_id(timeline_id)
    candidates = [item for item in project.get("timelines") or [] if item.get("id") == timeline_id]
    if version:
        candidates = [item for item in candidates if str(item.get("version")) == str(version).lstrip("v")]
    if not candidates:
        raise ValueError(f"timeline not found: {timeline_id}")
    item = sorted(candidates, key=lambda row: int(row.get("version") or 0))[-1]
    return _load_json(Path(str(item["path"])), {})


def _render_ffmpeg(project: dict[str, Any], spec: dict[str, Any], render_dir: Path) -> Path:
    ffmpeg = _require_ffmpeg()
    clips = spec.get("clips")
    if not isinstance(clips, list) or not clips:
        primary = _primary_video_asset(project)
        if primary is None:
            raise ValueError("timeline clips are required when the project has no source video asset")
        clips = [{"assetId": primary["id"], "in": 0}]
    canvas = spec.get("canvas") or {}
    width = int(canvas.get("width") or 1080)
    height = int(canvas.get("height") or 1920)
    fps = float(canvas.get("fps") or 30)
    segment_paths: list[Path] = []
    for idx, clip in enumerate(clips):
        if not isinstance(clip, dict):
            raise ValueError("timeline clip entries must be objects")
        source = _clip_source(project, clip)
        out_path = render_dir / f"segment_{idx:03d}.mp4"
        start = max(0.0, _float_or_none(clip.get("in") or clip.get("startTime") or clip.get("sourceStart")) or 0.0)
        end = _float_or_none(clip.get("out") or clip.get("endTime") or clip.get("sourceEnd"))
        command = [ffmpeg, "-y", "-ss", f"{start:.3f}"]
        if end is not None and end > start:
            command.extend(["-to", f"{end:.3f}"])
        speed = max(0.05, min(_float_or_none(clip.get("speed") or clip.get("playbackRate") or clip.get("rate")) or 1.0, 8.0))
        vfilter = _clip_video_filter(width, height, str(clip.get("fit") or spec.get("fit") or "cover"), clip.get("crop"), speed)
        source_probe = _ffprobe(source)
        has_audio = bool(source_probe.get("audio"))
        if abs(speed - 1.0) > 0.001 and has_audio:
            command.extend([
                "-i",
                str(source),
                "-filter_complex",
                f"[0:v]{vfilter}[v];[0:a]{_atempo_filter(speed)}[a]",
                "-map",
                "[v]",
                "-map",
                "[a]",
            ])
        else:
            command.extend(["-i", str(source), "-vf", vfilter, "-map", "0:v", "-map", "0:a?"])
        command.extend([
            "-r",
            str(fps),
            "-c:v",
            "libx264",
            "-preset",
            "veryfast",
            "-crf",
            str((spec.get("export") or {}).get("crf") or 22),
            "-c:a",
            "aac",
            "-movflags",
            "+faststart",
            str(out_path),
        ])
        result = _run(command, cwd=render_dir, timeout=float(clip.get("timeoutSeconds") or 900))
        if result["returnCode"] != 0 or not out_path.is_file():
            raise ValueError(f"ffmpeg segment render failed: {(result.get('stderr') or '')[-1500:]}")
        segment_paths.append(out_path)
    base_path = _join_segments(ffmpeg, segment_paths, spec, render_dir)
    current = _apply_image_layers(ffmpeg, project, spec, base_path, render_dir)
    current = _apply_text_layers(ffmpeg, project, spec, current, render_dir)
    current = _apply_audio_layers(ffmpeg, project, spec, current, render_dir)
    final_path = render_dir / str((spec.get("export") or {}).get("filename") or "render.mp4")
    if final_path.suffix.lower() != ".mp4":
        final_path = final_path.with_suffix(".mp4")
    if current != final_path:
        shutil.copy2(current, final_path)
    return final_path


def _clip_source(project: dict[str, Any], clip: dict[str, Any]) -> Path:
    asset_id = str(clip.get("assetId") or clip.get("sourceAssetId") or "").strip()
    if asset_id:
        return Path(_asset(project, asset_id)["path"]).resolve()
    raw = str(clip.get("source") or clip.get("sourcePath") or "").strip()
    if raw:
        path = Path(raw).expanduser()
        if not path.is_absolute():
            path = _project_dir(str(project["ownerDept"]), str(project["id"])) / path
        path = path.resolve()
        if path.is_file():
            return path
    primary = _primary_video_asset(project)
    if primary is None:
        raise ValueError("clip source is required")
    return Path(primary["path"]).resolve()


def _clip_video_filter(width: int, height: int, fit: str, crop: Any, speed: float = 1.0) -> str:
    filters: list[str] = []
    crop_filter = _crop_filter(crop)
    if crop_filter:
        filters.append(crop_filter)
    filters.append(_video_scale_filter(width, height, fit))
    if abs(speed - 1.0) > 0.001:
        filters.append(f"setpts=PTS/{speed:.6f}")
    return ",".join(filters)


def _video_scale_filter(width: int, height: int, fit: str) -> str:
    if fit == "contain":
        return f"scale={width}:{height}:force_original_aspect_ratio=decrease,pad={width}:{height}:(ow-iw)/2:(oh-ih)/2,setsar=1"
    return f"scale={width}:{height}:force_original_aspect_ratio=increase,crop={width}:{height},setsar=1"


def _crop_filter(crop: Any) -> str | None:
    if not isinstance(crop, dict):
        return None
    w = _crop_expr(crop.get("width") or crop.get("w"), "iw", default="iw")
    h = _crop_expr(crop.get("height") or crop.get("h"), "ih", default="ih")
    x = _crop_expr(crop.get("x") or crop.get("left"), "iw", default="(iw-ow)/2")
    y = _crop_expr(crop.get("y") or crop.get("top"), "ih", default="(ih-oh)/2")
    return f"crop={w}:{h}:{x}:{y}"


def _crop_expr(value: Any, axis_ref: str, *, default: str) -> str:
    if value is None or value == "":
        return default
    if isinstance(value, (int, float)):
        number = float(value)
        if 0 < number <= 1:
            return f"{axis_ref}*{number:.6f}"
        return str(max(0.0, number))
    text = str(value).strip()
    if text.endswith("%"):
        try:
            return f"{axis_ref}*{max(0.0, min(float(text[:-1]) / 100.0, 1.0)):.6f}"
        except ValueError:
            return default
    if re.fullmatch(r"\d+(\.\d+)?", text):
        return text
    return default


def _atempo_filter(speed: float) -> str:
    speed = max(0.05, min(float(speed or 1.0), 8.0))
    parts: list[str] = []
    while speed > 2.0:
        parts.append("atempo=2.000000")
        speed /= 2.0
    while speed < 0.5:
        parts.append("atempo=0.500000")
        speed /= 0.5
    parts.append(f"atempo={speed:.6f}")
    return ",".join(parts)


def _join_segments(ffmpeg: str, segment_paths: list[Path], spec: dict[str, Any], render_dir: Path) -> Path:
    base_path = render_dir / "base.mp4"
    if not segment_paths:
        raise ValueError("no rendered video segments")
    transitions = _transition_specs(spec, len(segment_paths))
    if len(segment_paths) == 1 or not transitions:
        if len(segment_paths) == 1:
            shutil.copy2(segment_paths[0], base_path)
            return base_path
        concat_path = render_dir / "concat.txt"
        concat_path.write_text("".join(f"file '{path.as_posix()}'\n" for path in segment_paths), encoding="utf-8")
        result = _run([ffmpeg, "-y", "-f", "concat", "-safe", "0", "-i", str(concat_path), "-c", "copy", str(base_path)], timeout=900)
        if result["returnCode"] != 0 or not base_path.is_file():
            raise ValueError(f"ffmpeg concat failed: {(result.get('stderr') or '')[-1500:]}")
        return base_path
    return _join_segments_with_transitions(ffmpeg, segment_paths, transitions, render_dir)


def _transition_specs(spec: dict[str, Any], segment_count: int) -> list[dict[str, Any]]:
    raw = spec.get("transitions") if isinstance(spec.get("transitions"), list) else []
    transitions: list[dict[str, Any]] = []
    for idx in range(max(0, segment_count - 1)):
        item = raw[idx] if idx < len(raw) and isinstance(raw[idx], dict) else None
        if not item:
            continue
        duration = max(0.0, min(_float_or_none(item.get("duration") or item.get("durationSeconds")) or 0.0, 3.0))
        if duration <= 0:
            continue
        kind = str(item.get("type") or item.get("name") or "fade").strip().lower()
        if not re.fullmatch(r"[a-z0-9_]+", kind):
            kind = "fade"
        transitions.append({"index": idx, "type": kind, "duration": duration})
    if len(transitions) != max(0, segment_count - 1):
        return []
    return transitions


def _join_segments_with_transitions(ffmpeg: str, segment_paths: list[Path], transitions: list[dict[str, Any]], render_dir: Path) -> Path:
    if not _ffmpeg_has_filter(ffmpeg, "xfade"):
        raise ValueError("ffmpeg xfade filter is required for timeline transitions")
    probes = [_ffprobe(path) for path in segment_paths]
    durations = [float((probe.get("format") or {}).get("duration") or (probe.get("video") or {}).get("duration") or 0.0) for probe in probes]
    all_have_audio = all(bool(probe.get("audio")) for probe in probes)
    inputs: list[str] = []
    for path in segment_paths:
        inputs.extend(["-i", str(path)])
    filters: list[str] = []
    current_v = "[0:v]"
    current_duration = durations[0]
    for transition in transitions:
        next_idx = int(transition["index"]) + 1
        duration = min(float(transition["duration"]), max(0.05, current_duration - 0.05), max(0.05, durations[next_idx] - 0.05))
        offset = max(0.0, current_duration - duration)
        label = f"[vxf{next_idx}]"
        filters.append(f"{current_v}[{next_idx}:v]xfade=transition={transition['type']}:duration={duration:.3f}:offset={offset:.3f}{label}")
        current_v = label
        current_duration = current_duration + durations[next_idx] - duration
    audio_label = None
    if all_have_audio:
        current_a = "[0:a]"
        for transition in transitions:
            next_idx = int(transition["index"]) + 1
            duration = min(float(transition["duration"]), max(0.05, durations[next_idx] - 0.05))
            label = f"[axf{next_idx}]"
            filters.append(f"{current_a}[{next_idx}:a]acrossfade=d={duration:.3f}:c1=tri:c2=tri{label}")
            current_a = label
        audio_label = current_a
    out_path = render_dir / "base.mp4"
    command = [ffmpeg, "-y", *inputs, "-filter_complex", ";".join(filters), "-map", current_v]
    if audio_label:
        command.extend(["-map", audio_label])
    else:
        command.append("-an")
    command.extend(["-c:v", "libx264", "-preset", "veryfast", "-crf", "22", "-c:a", "aac", "-movflags", "+faststart", str(out_path)])
    result = _run(command, timeout=900)
    if result["returnCode"] != 0 or not out_path.is_file():
        raise ValueError(f"ffmpeg transition render failed: {(result.get('stderr') or '')[-1500:]}")
    return out_path


def _timeline_duration_seconds(project: dict[str, Any], spec: dict[str, Any]) -> float:
    clips = spec.get("clips") if isinstance(spec.get("clips"), list) else []
    total = 0.0
    for clip in clips:
        if not isinstance(clip, dict):
            continue
        source = _clip_source(project, clip)
        probe = _ffprobe(source)
        source_duration = float((probe.get("format") or {}).get("duration") or (probe.get("video") or {}).get("duration") or 0.0)
        start = max(0.0, _float_or_none(clip.get("in") or clip.get("startTime") or clip.get("sourceStart")) or 0.0)
        end = _float_or_none(clip.get("out") or clip.get("endTime") or clip.get("sourceEnd")) or source_duration
        speed = max(0.05, min(_float_or_none(clip.get("speed") or clip.get("playbackRate") or clip.get("rate")) or 1.0, 8.0))
        total += max(0.1, end - start) / speed
    for collection in ("text", "captions", "overlays", "audio"):
        for layer in spec.get(collection) or []:
            if isinstance(layer, dict):
                total = max(total, _float_or_none(layer.get("end")) or _float_or_none(layer.get("out")) or 0.0)
    return max(1.0, total)


def _motion_layer_position(layer: dict[str, Any], *, default_y: str = "50%") -> dict[str, Any]:
    position = layer.get("position") if isinstance(layer.get("position"), dict) else {}
    return {
        "x": position.get("x") or layer.get("x") or "50%",
        "y": position.get("y") or layer.get("y") or default_y,
        "anchor": position.get("anchor") or layer.get("anchor") or "center",
    }


def _motion_timeline_payload(project: dict[str, Any], spec: dict[str, Any]) -> dict[str, Any]:
    canvas = spec.get("canvas") if isinstance(spec.get("canvas"), dict) else {}
    fps = float(canvas.get("fps") or 30)
    clips_payload: list[dict[str, Any]] = []
    current = 0.0
    for clip in spec.get("clips") or []:
        if not isinstance(clip, dict):
            continue
        source = _clip_source(project, clip)
        start = max(0.0, _float_or_none(clip.get("in") or clip.get("startTime") or clip.get("sourceStart")) or 0.0)
        probe = _ffprobe(source)
        source_duration = float((probe.get("format") or {}).get("duration") or (probe.get("video") or {}).get("duration") or 0.0)
        end = _float_or_none(clip.get("out") or clip.get("endTime") or clip.get("sourceEnd")) or source_duration
        speed = max(0.05, min(_float_or_none(clip.get("speed") or clip.get("playbackRate") or clip.get("rate")) or 1.0, 8.0))
        duration = max(0.1, end - start) / speed
        clips_payload.append({
            "id": str(clip.get("id") or uid("clip")),
            "src": source.as_uri(),
            "startFrame": int(round(current * fps)),
            "durationFrames": max(1, int(round(duration * fps))),
            "sourceStartFrame": int(round(start * fps)),
            "speed": speed,
            "fit": clip.get("fit") or spec.get("fit") or "cover",
            "hasAudio": bool(probe.get("audio")),
        })
        current += duration
    text_layers: list[dict[str, Any]] = []
    for layer in _timeline_text_layers(spec):
        start = max(0.0, _float_or_none(layer.get("start")) or 0.0)
        end = max(start + 0.1, _float_or_none(layer.get("end")) or start + 3.0)
        style = layer.get("style") if isinstance(layer.get("style"), dict) else {}
        text_layers.append({
            "id": str(layer.get("id") or uid("txt")),
            "text": str(layer.get("text") or ""),
            "startFrame": int(round(start * fps)),
            "durationFrames": max(1, int(round((end - start) * fps))),
            "position": _motion_layer_position(layer, default_y="82%" if str(layer.get("id") or "").startswith("cap") else "18%"),
            "size": int(layer.get("size") or layer.get("fontSize") or style.get("size") or 52),
            "color": layer.get("color") or style.get("color") or "#ffffff",
            "fontFamily": layer.get("fontFamily") or layer.get("font") or style.get("fontFamily") or style.get("font") or "Inter, Arial, sans-serif",
            "fontWeight": layer.get("fontWeight") or style.get("fontWeight") or 800,
            "maxWidth": layer.get("maxWidth") or style.get("maxWidth") or "88%",
            "lineSpacing": layer.get("lineSpacing") or style.get("lineSpacing"),
            "stroke": layer.get("stroke") if isinstance(layer.get("stroke"), dict) else {},
            "shadow": layer.get("shadow") if isinstance(layer.get("shadow"), dict) else {},
            "box": layer.get("box") if isinstance(layer.get("box"), dict) else {},
            "animation": layer.get("animation") or "fade-up",
        })
    overlay_layers: list[dict[str, Any]] = []
    for layer in spec.get("overlays") or []:
        if not isinstance(layer, dict):
            continue
        try:
            source = _image_source(project, layer)
        except Exception:
            continue
        start = max(0.0, _float_or_none(layer.get("start")) or 0.0)
        end = max(start + 0.1, _float_or_none(layer.get("end")) or _timeline_duration_seconds(project, spec))
        overlay_layers.append({
            "id": str(layer.get("id") or uid("ovl")),
            "src": source.as_uri(),
            "startFrame": int(round(start * fps)),
            "durationFrames": max(1, int(round((end - start) * fps))),
            "position": _motion_layer_position(layer),
            "width": layer.get("width") or layer.get("w") or "28%",
            "height": layer.get("height") or layer.get("h") or "auto",
            "opacity": max(0.0, min(float(layer.get("opacity") or 1.0), 1.0)),
            "animation": layer.get("animation") or "fade",
        })
    audio_layers: list[dict[str, Any]] = []
    timeline_duration = _timeline_duration_seconds(project, spec)
    for layer in spec.get("audio") or []:
        if not isinstance(layer, dict):
            continue
        try:
            source = _audio_source(project, layer)
        except Exception:
            continue
        start = max(0.0, _float_or_none(layer.get("start")) or 0.0)
        media_start = max(0.0, _float_or_none(layer.get("mediaStart") or layer.get("sourceStart") or layer.get("in")) or 0.0)
        end = _float_or_none(layer.get("end") or layer.get("out"))
        if end is None:
            probe = _ffprobe(source)
            source_duration = float((probe.get("format") or {}).get("duration") or (probe.get("audio") or [{}])[0].get("duration") or timeline_duration)
            end = min(timeline_duration, start + max(0.1, source_duration - media_start))
        audio_layers.append({
            "id": str(layer.get("id") or uid("aud")),
            "src": source.as_uri(),
            "startFrame": int(round(start * fps)),
            "durationFrames": max(1, int(round((max(start + 0.1, end) - start) * fps))),
            "sourceStartFrame": int(round(media_start * fps)),
            "volume": max(0.0, min(float(layer.get("volume") or 1.0), 1.0)),
            "role": layer.get("role") or "audio",
        })
    duration_seconds = max(timeline_duration, current)
    return {
        "projectId": project["id"],
        "timelineId": spec.get("id"),
        "timelineVersion": spec.get("version"),
        "templateId": spec.get("templateId") or (spec.get("styleGuide") or {}).get("id") if isinstance(spec.get("styleGuide"), dict) else spec.get("templateId"),
        "styleGuide": spec.get("styleGuide") if isinstance(spec.get("styleGuide"), dict) else None,
        "compositionId": "AtriumVideo",
        "width": int(canvas.get("width") or 1080),
        "height": int(canvas.get("height") or 1920),
        "fps": fps,
        "durationFrames": max(1, int(round(duration_seconds * fps))),
        "clips": clips_payload,
        "textLayers": text_layers,
        "overlayLayers": overlay_layers,
        "audioLayers": audio_layers,
    }


def _motion_package_files(payload: dict[str, Any]) -> dict[str, str]:
    timeline_json = json.dumps(payload, ensure_ascii=False, indent=2)
    package_json = json.dumps({
        "private": True,
        "type": "module",
        "scripts": {
            "render": "remotion render src/index.tsx AtriumVideo out/atrium-video.mp4 --overwrite",
            "preview": "remotion studio src/index.tsx",
        },
        "dependencies": {
            "@remotion/cli": "^4.0.0",
            "remotion": "^4.0.0",
            "react": "^19.0.0",
            "react-dom": "^19.0.0",
        },
        "devDependencies": {
            "typescript": "^5.0.0",
        },
    }, indent=2)
    root_tsx = """import {Composition} from 'remotion';
import {AtriumVideo} from './Video';
import {timeline} from './timeline';

export const RemotionRoot = () => {
  return (
    <Composition
      id={timeline.compositionId}
      component={AtriumVideo}
      durationInFrames={timeline.durationFrames}
      fps={timeline.fps}
      width={timeline.width}
      height={timeline.height}
    />
  );
};
"""
    index_tsx = """import {registerRoot} from 'remotion';
import {RemotionRoot} from './Root';

registerRoot(RemotionRoot);
"""
    video_tsx = """import React from 'react';
import {AbsoluteFill, Img, Sequence, Video, interpolate, useCurrentFrame} from 'remotion';
import {timeline} from './timeline';

const pos = (value: string | number | undefined, fallback: string) =>
  value === undefined ? fallback : typeof value === 'number' ? `${value}px` : value;

const layerStyle = (layer: any): React.CSSProperties => {
  const p = layer.position || {};
  const x = pos(p.x, '50%');
  const y = pos(p.y, '50%');
  const anchor = String(p.anchor || 'center');
  const tx = anchor.includes('center') ? '-50%' : anchor.includes('right') ? '-100%' : '0%';
  const ty = anchor.includes('center') ? '-50%' : anchor.includes('bottom') ? '-100%' : '0%';
  return {position: 'absolute', left: x, top: y, transform: `translate(${tx}, ${ty})`};
};

const animated = (frame: number, duration: number, animation: string): React.CSSProperties => {
  const fadeIn = Math.min(12, Math.max(1, Math.floor(duration / 3)));
  const opacity = interpolate(frame, [0, fadeIn, Math.max(fadeIn + 1, duration - 8), duration], [0, 1, 1, 0], {
    extrapolateLeft: 'clamp',
    extrapolateRight: 'clamp',
  });
  const y = animation === 'fade-up' ? interpolate(frame, [0, fadeIn], [26, 0], {extrapolateLeft: 'clamp', extrapolateRight: 'clamp'}) : 0;
  const scale = animation === 'pop' ? interpolate(frame, [0, fadeIn], [0.92, 1], {extrapolateLeft: 'clamp', extrapolateRight: 'clamp'}) : 1;
  return {opacity, translate: `0 ${y}px`, scale};
};

const fitStyle = (fit: string): React.CSSProperties => ({
  width: '100%',
  height: '100%',
  objectFit: fit === 'contain' ? 'contain' : 'cover',
});

export const AtriumVideo: React.FC = () => {
  const frame = useCurrentFrame();
  const styleGuide = timeline.styleGuide || {};
  const motion = styleGuide.motion || {};
  const backgroundColor = motion.backgroundColor || styleGuide.colors?.background || '#000';
  return (
    <AbsoluteFill style={{backgroundColor}}>
      {timeline.clips.map((clip: any) => (
        <Sequence key={clip.id} from={clip.startFrame} durationInFrames={clip.durationFrames}>
          <Video src={clip.src} startFrom={clip.sourceStartFrame} playbackRate={clip.speed || 1} style={fitStyle(clip.fit)} />
        </Sequence>
      ))}
      {timeline.overlayLayers.map((layer: any) => {
        const local = frame - layer.startFrame;
        return (
          <Sequence key={layer.id} from={layer.startFrame} durationInFrames={layer.durationFrames}>
            <Img
              src={layer.src}
              style={{
                ...layerStyle(layer),
                ...animated(local, layer.durationFrames, layer.animation || 'fade'),
                width: layer.width,
                height: layer.height,
                opacity: layer.opacity,
              }}
            />
          </Sequence>
        );
      })}
      {timeline.textLayers.map((layer: any) => {
        const local = frame - layer.startFrame;
        const stroke = layer.stroke || {};
        const shadow = layer.shadow || {};
        const box = layer.box || {};
        const textShadow = shadow.color
          ? `${shadow.x || 0}px ${shadow.y || 4}px ${shadow.blur || 10}px ${shadow.color}`
          : undefined;
        return (
          <Sequence key={layer.id} from={layer.startFrame} durationInFrames={layer.durationFrames}>
            <div
              style={{
                ...layerStyle(layer),
                ...animated(local, layer.durationFrames, layer.animation || 'fade-up'),
                color: layer.color,
                fontFamily: layer.fontFamily,
                fontSize: layer.size,
                fontWeight: layer.fontWeight || 800,
                lineHeight: 1.08,
                maxWidth: layer.maxWidth || '88%',
                textAlign: 'center',
                whiteSpace: 'pre-wrap',
                padding: box.padding || 0,
                borderRadius: box.radius || 0,
                backgroundColor: box.color ? `${box.color}${Math.round((box.opacity ?? 0.5) * 255).toString(16).padStart(2, '0')}` : undefined,
                WebkitTextStroke: stroke.width ? `${stroke.width}px ${stroke.color || '#000'}` : undefined,
                textShadow,
              }}
            >
              {layer.text}
            </div>
          </Sequence>
        );
      })}
    </AbsoluteFill>
  );
};
"""
    timeline_ts = f"export const timeline = {timeline_json} as const;\n"
    config_ts = """import {Config} from '@remotion/cli/config';

Config.setVideoImageFormat('jpeg');
Config.setOverwriteOutput(true);
"""
    tsconfig = json.dumps({
        "compilerOptions": {
            "target": "ES2022",
            "jsx": "react-jsx",
            "module": "ESNext",
            "moduleResolution": "Bundler",
            "strict": True,
            "skipLibCheck": True,
            "types": ["node"],
        },
        "include": ["src", "remotion.config.ts"],
    }, indent=2)
    return {
        "package.json": package_json + "\n",
        "tsconfig.json": tsconfig + "\n",
        "remotion.config.ts": config_ts,
        "src/index.tsx": index_tsx,
        "src/Root.tsx": root_tsx,
        "src/Video.tsx": video_tsx,
        "src/timeline.ts": timeline_ts,
    }


def _write_motion_package(package_dir: Path, payload: dict[str, Any]) -> list[str]:
    written: list[str] = []
    for rel, content in _motion_package_files(payload).items():
        path = package_dir / rel
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(content, encoding="utf-8")
        written.append(str(path))
    (package_dir / "out").mkdir(parents=True, exist_ok=True)
    return written


def _revideo_package_files(payload: dict[str, Any]) -> dict[str, str]:
    timeline_json = json.dumps(payload, ensure_ascii=False, indent=2)
    package_json = json.dumps({
        "private": True,
        "type": "module",
        "scripts": {
            "render": "tsx src/render.ts",
        },
        "dependencies": {
            "@revideo/2d": "^0.12.0",
            "@revideo/core": "^0.12.0",
            "@revideo/renderer": "^0.12.0",
            "tsx": "^4.0.0",
            "typescript": "^5.0.0",
        },
        "devDependencies": {},
    }, indent=2)
    project_ts = """import {makeProject} from '@revideo/core';
import atrium from './scenes/atrium?scene';
import {timeline} from './timeline';

export default makeProject({
  scenes: [atrium],
  variables: {
    timeline,
  },
});
"""
    scene_tsx = """import {Img, Txt, Video, makeScene2D} from '@revideo/2d';
import {all, createRef, waitFor} from '@revideo/core';
import {timeline} from '../timeline';

const seconds = (frames: number) => Math.max(0, frames || 0) / timeline.fps;

const numericPosition = (value: string | number | undefined, fallback: number, total: number) => {
  if (typeof value === 'number') return value;
  if (typeof value === 'string' && value.endsWith('%')) return (Number(value.slice(0, -1)) / 100) * total;
  if (typeof value === 'string' && value.trim()) return Number(value) || fallback;
  return fallback;
};

const layerX = (layer: any) => numericPosition(layer.position?.x, timeline.width / 2, timeline.width);
const layerY = (layer: any) => numericPosition(layer.position?.y, timeline.height / 2, timeline.height);

function* showClip(view: any, clip: any) {
  yield* waitFor(seconds(clip.startFrame));
  view.add(
    <Video
      src={clip.src}
      play={true}
      time={seconds(clip.sourceStartFrame || 0)}
      size={[timeline.width, timeline.height]}
    />,
  );
  yield* waitFor(seconds(clip.durationFrames));
}

function* showOverlay(view: any, layer: any) {
  const ref = createRef<Img>();
  yield* waitFor(seconds(layer.startFrame));
  view.add(
    <Img
      ref={ref}
      src={layer.src}
      x={layerX(layer)}
      y={layerY(layer)}
      width={layer.width}
      height={layer.height}
      opacity={0}
    />,
  );
  yield* ref().opacity(layer.opacity ?? 1, 0.16);
  yield* waitFor(seconds(layer.durationFrames));
  yield* ref().opacity(0, 0.16);
}

function* showText(view: any, layer: any) {
  const ref = createRef<Txt>();
  const stroke = layer.stroke || {};
  const box = layer.box || {};
  yield* waitFor(seconds(layer.startFrame));
  view.add(
    <Txt
      ref={ref}
      text={layer.text}
      x={layerX(layer)}
      y={layerY(layer)}
      fill={layer.color || '#ffffff'}
      fontFamily={layer.fontFamily}
      fontSize={layer.size}
      fontWeight={layer.fontWeight || 800}
      stroke={stroke.color}
      lineWidth={stroke.width || 0}
      width={layer.maxWidth || '88%'}
      padding={box.padding || 0}
      radius={box.radius || 0}
      opacity={0}
    />,
  );
  yield* ref().opacity(1, 0.16);
  yield* waitFor(seconds(layer.durationFrames));
  yield* ref().opacity(0, 0.16);
}

export default makeScene2D(function* (view) {
  yield* all(
    ...timeline.clips.map((clip: any) => showClip(view, clip)),
    ...timeline.overlayLayers.map((layer: any) => showOverlay(view, layer)),
    ...timeline.textLayers.map((layer: any) => showText(view, layer)),
  );
});
"""
    render_ts = """import {renderVideo} from '@revideo/renderer';

async function render() {
  const file = await renderVideo({
    projectFile: './src/project.ts',
    settings: {logProgress: true},
  });
  console.log(`Rendered video to ${file}`);
}

render().catch((error) => {
  console.error(error);
  process.exit(1);
});
"""
    timeline_ts = f"export const timeline = {timeline_json} as const;\n"
    tsconfig = json.dumps({
        "compilerOptions": {
            "target": "ES2022",
            "jsx": "react-jsx",
            "module": "ESNext",
            "moduleResolution": "Bundler",
            "strict": True,
            "skipLibCheck": True,
            "types": ["node"],
        },
        "include": ["src"],
    }, indent=2)
    return {
        "package.json": package_json + "\n",
        "tsconfig.json": tsconfig + "\n",
        "src/project.ts": project_ts,
        "src/render.ts": render_ts,
        "src/scenes/atrium.tsx": scene_tsx,
        "src/timeline.ts": timeline_ts,
    }


def _write_revideo_package(package_dir: Path, payload: dict[str, Any]) -> list[str]:
    written: list[str] = []
    for rel, content in _revideo_package_files(payload).items():
        path = package_dir / rel
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(content, encoding="utf-8")
        written.append(str(path))
    (package_dir / "out").mkdir(parents=True, exist_ok=True)
    return written


def _media_src_path(value: Any) -> Path | None:
    raw = str(value or "").strip()
    if not raw or raw.startswith(("http://", "https://", "data:")):
        return None
    if raw.startswith("file://"):
        parsed = urlparse(raw)
        path = Path(unquote(parsed.path)).expanduser()
    else:
        path = Path(raw).expanduser()
    try:
        path = path.resolve()
    except OSError:
        return None
    return path if path.is_file() else None


def _copy_hyperframes_media(package_dir: Path, payload: dict[str, Any]) -> tuple[dict[str, Any], list[str]]:
    packaged = copy.deepcopy(payload)
    copied: list[str] = []
    assets_dir = package_dir / "assets"
    for collection in ("clips", "overlayLayers", "audioLayers"):
        layers = packaged.get(collection) if isinstance(packaged.get(collection), list) else []
        for layer in layers:
            if not isinstance(layer, dict):
                continue
            source = _media_src_path(layer.get("src"))
            if source is None:
                continue
            filename = safe_filename(f"{layer.get('id') or collection}_{source.name}")
            target = assets_dir / filename
            target.parent.mkdir(parents=True, exist_ok=True)
            if source != target:
                shutil.copy2(source, target)
            layer["src"] = target.relative_to(package_dir).as_posix()
            copied.append(str(target))
    return packaged, copied


def _hf_seconds(frames: Any, fps: float) -> float:
    return round(max(0.0, float(frames or 0) / max(float(fps or 30), 1.0)), 4)


def _hf_duration(layer: dict[str, Any], fps: float) -> float:
    return round(max(0.1, float(layer.get("durationFrames") or 1) / max(float(fps or 30), 1.0)), 4)


def _hf_id(value: Any, prefix: str) -> str:
    raw = re.sub(r"[^A-Za-z0-9_-]+", "-", str(value or "").strip()).strip("-")
    if not raw:
        raw = uid(prefix)
    if not re.match(r"^[A-Za-z]", raw):
        raw = f"{prefix}-{raw}"
    return raw[:96]


def _hf_attr(value: Any) -> str:
    return html.escape(str(value or ""), quote=True)


def _hf_text(value: Any) -> str:
    return html.escape(str(value or ""), quote=False)


def _css_len(value: Any, fallback: str) -> str:
    if value is None or value == "":
        return fallback
    if isinstance(value, (int, float)):
        return f"{float(value):g}px"
    text = str(value).strip()
    if re.fullmatch(r"-?\d+(\.\d+)?", text):
        return f"{text}px"
    return text


def _css_hex_rgba(value: Any, opacity: Any, fallback: str = "rgba(0,0,0,0.5)") -> str:
    raw = str(value or "").strip()
    match = re.fullmatch(r"#?([0-9A-Fa-f]{6})", raw)
    if not match:
        return fallback
    rgb = match.group(1)
    alpha = max(0.0, min(_float_or_none(opacity) if _float_or_none(opacity) is not None else 1.0, 1.0))
    return f"rgba({int(rgb[0:2], 16)},{int(rgb[2:4], 16)},{int(rgb[4:6], 16)},{alpha:.3f})"


def _hf_layer_position_style(layer: dict[str, Any], *, default_y: str = "50%") -> str:
    position = layer.get("position") if isinstance(layer.get("position"), dict) else {}
    anchor = str(position.get("anchor") or layer.get("anchor") or "center").lower()
    tx = "-50%" if "center" in anchor else "-100%" if "right" in anchor else "0%"
    ty = "-50%" if "center" in anchor else "-100%" if "bottom" in anchor else "0%"
    return (
        f"left:{_css_len(position.get('x') or layer.get('x'), '50%')};"
        f"top:{_css_len(position.get('y') or layer.get('y'), default_y)};"
        f"translate:{tx} {ty};"
    )


def _hf_text_style(layer: dict[str, Any]) -> str:
    stroke = layer.get("stroke") if isinstance(layer.get("stroke"), dict) else {}
    shadow = layer.get("shadow") if isinstance(layer.get("shadow"), dict) else {}
    box = layer.get("box") if isinstance(layer.get("box"), dict) else {}
    styles = [
        _hf_layer_position_style(layer, default_y="82%" if str(layer.get("id") or "").startswith("cap") else "18%"),
        f"font-family:{_hf_attr(layer.get('fontFamily') or 'Inter, Arial, sans-serif')};",
        f"font-size:{int(layer.get('size') or layer.get('fontSize') or 52)}px;",
        f"font-weight:{int(_float_or_none(layer.get('fontWeight')) or 800)};",
        f"color:{_hf_attr(layer.get('color') or '#ffffff')};",
        f"max-width:{_css_len(layer.get('maxWidth'), '88%')};",
    ]
    if stroke.get("width"):
        styles.append(f"-webkit-text-stroke:{int(stroke.get('width') or 0)}px {_hf_attr(stroke.get('color') or '#000000')};")
    if shadow.get("color"):
        styles.append(
            "text-shadow:"
            f"{int(shadow.get('x') or 0)}px {int(shadow.get('y') or 4)}px {int(shadow.get('blur') or 10)}px {_hf_attr(shadow.get('color'))};"
        )
    if box:
        styles.extend([
            f"background:{_css_hex_rgba(box.get('color') or '#000000', box.get('opacity'), fallback='rgba(0,0,0,0.5)')};",
            f"padding:{int(box.get('padding') or 0)}px;",
            f"border-radius:{int(box.get('radius') or 0)}px;",
        ])
    return "".join(styles)


def _hf_overlay_style(layer: dict[str, Any]) -> str:
    width = _css_len(layer.get("width"), "28%")
    height = "auto" if str(layer.get("height") or "auto").strip() == "auto" else _css_len(layer.get("height"), "auto")
    opacity = max(0.0, min(float(layer.get("opacity") or 1.0), 1.0))
    return f"{_hf_layer_position_style(layer)}width:{width};height:{height};opacity:{opacity:.3f};"


def _hyperframes_design_markdown(payload: dict[str, Any]) -> str:
    style = payload.get("styleGuide") if isinstance(payload.get("styleGuide"), dict) else {}
    colors = style.get("colors") if isinstance(style.get("colors"), dict) else {}
    fonts = style.get("fonts") if isinstance(style.get("fonts"), dict) else {}
    color_lines = "\n".join(
        f"- {key}: {value}"
        for key, value in colors.items()
        if isinstance(key, str) and isinstance(value, str)
    ) or "- background: #080808\n- text: #ffffff\n- primary: #38bdf8\n- accent: #fbbf24"
    font_lines = "\n".join(
        f"- {key}: {value}"
        for key, value in fonts.items()
        if isinstance(key, str) and isinstance(value, str)
    ) or "- heading: Inter\n- body: Inter"
    return (
        "# HyperFrames Design\n\n"
        "## Style Prompt\n"
        "ATRIUM motion package rendered as clean HTML-native video: high contrast, readable Thai-capable typography, deliberate motion, and no generic default palette.\n\n"
        "## Colors\n"
        f"{color_lines}\n\n"
        "## Typography\n"
        f"{font_lines}\n\n"
        "## Motion Rules\n"
        "- Use short, seekable GSAP timelines registered on window.__timelines.\n"
        "- Keep text within safe areas and avoid overlap during hero frames.\n"
        "- Prefer fade-up or pop entrances with quick exits near clip end.\n\n"
        "## What NOT to Do\n"
        "- Do not use unbounded repeat loops.\n"
        "- Do not animate media playback directly.\n"
        "- Do not depend on wall-clock time or random values.\n"
    )


def _hyperframes_index_html(payload: dict[str, Any]) -> str:
    fps = float(payload.get("fps") or 30)
    width = int(payload.get("width") or 1080)
    height = int(payload.get("height") or 1920)
    duration = _hf_seconds(payload.get("durationFrames"), fps)
    style = payload.get("styleGuide") if isinstance(payload.get("styleGuide"), dict) else {}
    colors = style.get("colors") if isinstance(style.get("colors"), dict) else {}
    motion = style.get("motion") if isinstance(style.get("motion"), dict) else {}
    background = motion.get("backgroundColor") or colors.get("background") or "#080808"
    composition_id = _hf_id(payload.get("compositionId") or "AtriumVideo", "comp")
    track = 0
    body_parts: list[str] = []
    animation_layers: list[dict[str, Any]] = []
    for clip in payload.get("clips") or []:
        if not isinstance(clip, dict):
            continue
        track += 1
        clip_id = _hf_id(clip.get("id"), "clip")
        start = _hf_seconds(clip.get("startFrame"), fps)
        clip_duration = _hf_duration(clip, fps)
        media_start = _hf_seconds(clip.get("sourceStartFrame"), fps)
        fit_class = "contain" if str(clip.get("fit") or "").lower() == "contain" else "cover"
        src = _hf_attr(clip.get("src"))
        body_parts.append(
            f'<video id="{_hf_attr(clip_id)}" class="clip hf-video hf-{fit_class}" data-start="{start}" data-duration="{clip_duration}" '
            f'data-track-index="{track}" data-media-start="{media_start}" src="{src}" muted playsinline></video>'
        )
        if clip.get("hasAudio"):
            track += 1
            body_parts.append(
                f'<audio id="{_hf_attr(clip_id)}-audio" class="clip" data-start="{start}" data-duration="{clip_duration}" '
                f'data-track-index="{track}" data-media-start="{media_start}" data-volume="1" src="{src}"></audio>'
            )
    for layer in payload.get("audioLayers") or []:
        if not isinstance(layer, dict):
            continue
        track += 1
        audio_id = _hf_id(layer.get("id"), "audio")
        body_parts.append(
            f'<audio id="{_hf_attr(audio_id)}" class="clip" data-start="{_hf_seconds(layer.get("startFrame"), fps)}" '
            f'data-duration="{_hf_duration(layer, fps)}" data-track-index="{track}" '
            f'data-media-start="{_hf_seconds(layer.get("sourceStartFrame"), fps)}" '
            f'data-volume="{max(0.0, min(float(layer.get("volume") or 1.0), 1.0)):.3f}" src="{_hf_attr(layer.get("src"))}"></audio>'
        )
    for layer in payload.get("overlayLayers") or []:
        if not isinstance(layer, dict):
            continue
        track += 1
        layer_id = _hf_id(layer.get("id"), "overlay")
        body_parts.append(
            f'<img id="{_hf_attr(layer_id)}" class="clip hf-overlay" data-start="{_hf_seconds(layer.get("startFrame"), fps)}" '
            f'data-duration="{_hf_duration(layer, fps)}" data-track-index="{track}" src="{_hf_attr(layer.get("src"))}" '
            f'style="{_hf_attr(_hf_overlay_style(layer))}" />'
        )
        animation_layers.append({"id": layer_id, "start": _hf_seconds(layer.get("startFrame"), fps), "duration": _hf_duration(layer, fps), "animation": layer.get("animation") or "fade"})
    for layer in payload.get("textLayers") or []:
        if not isinstance(layer, dict):
            continue
        track += 1
        layer_id = _hf_id(layer.get("id"), "text")
        body_parts.append(
            f'<div id="{_hf_attr(layer_id)}" class="clip hf-text" data-start="{_hf_seconds(layer.get("startFrame"), fps)}" '
            f'data-duration="{_hf_duration(layer, fps)}" data-track-index="{track}" style="{_hf_attr(_hf_text_style(layer))}">'
            f'{_hf_text(layer.get("text"))}</div>'
        )
        animation_layers.append({"id": layer_id, "start": _hf_seconds(layer.get("startFrame"), fps), "duration": _hf_duration(layer, fps), "animation": layer.get("animation") or "fade-up"})
    timeline_lines = [
        "window.__timelines = window.__timelines || {};",
        "const tl = gsap.timeline({ paused: true });",
    ]
    for layer in animation_layers:
        selector = json.dumps(f"#{layer['id']}")
        start = float(layer["start"])
        layer_duration = float(layer["duration"])
        entrance = max(0.12, min(0.45, layer_duration / 3.0))
        exit_at = max(start + entrance + 0.05, start + layer_duration - min(0.35, layer_duration / 3.0))
        animation = str(layer.get("animation") or "fade")
        if animation == "pop":
            from_state = "{ opacity: 0, scale: 0.92 }"
            to_state = f"{{ opacity: 1, scale: 1, duration: {entrance:.3f}, ease: 'back.out(1.6)' }}"
            exit_state = "{ opacity: 0, scale: 0.96, duration: 0.22, ease: 'power2.in' }"
        elif animation == "fade-up":
            from_state = "{ opacity: 0, y: 26 }"
            to_state = f"{{ opacity: 1, y: 0, duration: {entrance:.3f}, ease: 'power3.out' }}"
            exit_state = "{ opacity: 0, y: -18, duration: 0.24, ease: 'power2.in' }"
        else:
            from_state = "{ opacity: 0 }"
            to_state = f"{{ opacity: 1, duration: {entrance:.3f}, ease: 'power2.out' }}"
            exit_state = "{ opacity: 0, duration: 0.24, ease: 'power2.in' }"
        timeline_lines.append(f"tl.fromTo({selector}, {from_state}, {to_state}, {start:.3f});")
        if layer_duration > entrance + 0.3:
            timeline_lines.append(f"tl.to({selector}, {exit_state}, {exit_at:.3f});")
    timeline_lines.append(f"window.__timelines[{json.dumps(composition_id)}] = tl;")
    body = "\n    ".join(body_parts)
    timeline_script = "\n      ".join(timeline_lines)
    return f"""<!doctype html>
<html lang="en">
<head>
  <meta charset="utf-8" />
  <meta name="viewport" content="width=device-width, initial-scale=1" />
  <title>{_hf_text(payload.get("projectId") or "ATRIUM HyperFrames")}</title>
  <style>
    html, body {{
      margin: 0;
      width: 100%;
      height: 100%;
      background: {_hf_attr(background)};
      overflow: hidden;
    }}
    #stage {{
      position: relative;
      width: 100%;
      height: 100%;
      overflow: hidden;
      background: {_hf_attr(background)};
      color: {_hf_attr(colors.get("text") or "#ffffff")};
      font-family: Inter, Arial, sans-serif;
    }}
    .hf-video {{
      position: absolute;
      inset: 0;
      width: 100%;
      height: 100%;
    }}
    .hf-cover {{ object-fit: cover; }}
    .hf-contain {{ object-fit: contain; background: {_hf_attr(background)}; }}
    .hf-overlay {{
      position: absolute;
      object-fit: contain;
      will-change: transform, opacity;
    }}
    .hf-text {{
      position: absolute;
      box-sizing: border-box;
      line-height: 1.08;
      text-align: center;
      white-space: pre-wrap;
      overflow-wrap: anywhere;
      will-change: transform, opacity;
    }}
  </style>
</head>
<body>
  <div id="stage" data-composition-id="{_hf_attr(composition_id)}" data-start="0" data-duration="{duration}" data-width="{width}" data-height="{height}">
    {body}
  </div>
  <script src="https://cdn.jsdelivr.net/npm/gsap@3/dist/gsap.min.js"></script>
  <script>
    {timeline_script}
  </script>
</body>
</html>
"""


def _hyperframes_package_files(payload: dict[str, Any]) -> dict[str, str]:
    timeline_json = json.dumps(payload, ensure_ascii=False, indent=2)
    package_json = json.dumps({
        "private": True,
        "type": "module",
        "scripts": {
            "doctor": "npx --yes hyperframes doctor",
            "lint": "npx --yes hyperframes lint",
            "inspect": "npx --yes hyperframes inspect",
            "preview": "npx --yes hyperframes preview",
            "render": "npx --yes hyperframes render --output out/atrium-video.mp4",
        },
        "devDependencies": {
            "hyperframes": "^0.6.72",
        },
    }, indent=2)
    readme = (
        "# ATRIUM HyperFrames Package\n\n"
        "This package is generated by ATRIUM `video.render_motion` with `renderer=hyperframes`.\n\n"
        "## Commands\n\n"
        "- `npm run lint`\n"
        "- `npm run inspect`\n"
        "- `npm run preview`\n"
        "- `npm run render`\n\n"
        "Source of truth: `index.html` and `timeline.json`.\n"
    )
    return {
        "package.json": package_json + "\n",
        "README.md": readme,
        "DESIGN.md": _hyperframes_design_markdown(payload),
        "timeline.json": timeline_json + "\n",
        "index.html": _hyperframes_index_html(payload),
    }


def _write_hyperframes_package(package_dir: Path, payload: dict[str, Any]) -> tuple[list[str], dict[str, Any]]:
    package_payload, copied = _copy_hyperframes_media(package_dir, payload)
    written: list[str] = []
    for rel, content in _hyperframes_package_files(package_payload).items():
        path = package_dir / rel
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(content, encoding="utf-8")
        written.append(str(path))
    (package_dir / "out").mkdir(parents=True, exist_ok=True)
    return [*copied, *written], package_payload


def _motion_renderers(args: dict[str, Any]) -> list[str]:
    raw = str(args.get("renderer") or args.get("engine") or args.get("motionRenderer") or "remotion").strip().lower()
    if raw in {"remotion", ""}:
        return ["remotion"]
    if raw in {"revideo", "redot", "redotvideo"}:
        return ["revideo"]
    if raw in {"hyperframes", "hyperframe", "hf", "html", "html-video"}:
        return ["hyperframes"]
    if raw in {"both", "remotion+revideo", "revideo+remotion"}:
        return ["remotion", "revideo"]
    if raw in {"all", "remotion+revideo+hyperframes", "hyperframes+remotion+revideo"}:
        return ["remotion", "revideo", "hyperframes"]
    raise ValueError("renderer must be remotion, revideo, hyperframes, both, or all")


def _motion_package_commands(renderer: str) -> dict[str, str]:
    if renderer == "revideo":
        return {
            "install": "npm install",
            "render": "npm run render",
        }
    if renderer == "hyperframes":
        return {
            "doctor": "npx --yes hyperframes doctor",
            "lint": "npx --yes hyperframes lint",
            "inspect": "npx --yes hyperframes inspect",
            "preview": "npx --yes hyperframes preview",
            "render": "npx --yes hyperframes render --output out/atrium-video.mp4",
        }
    return {
        "install": "npm install",
        "render": "npx remotion render src/index.tsx AtriumVideo out/atrium-video.mp4 --overwrite",
        "preview": "npx remotion studio src/index.tsx",
    }


async def _render_motion(repo: Any, run: dict[str, Any], args: dict[str, Any]) -> dict[str, Any]:
    dept_id = run["departmentId"]
    project_id = str(args.get("projectId") or args.get("project_id") or "").strip()
    if not project_id:
        raise ValueError("projectId is required")
    project = _load_project(dept_id, project_id)
    spec = args.get("timeline") if isinstance(args.get("timeline"), dict) else None
    if not spec:
        timeline_id = str(args.get("timelineId") or args.get("timeline_id") or "").strip()
        version = str(args.get("version") or args.get("timelineVersion") or args.get("timeline_version") or "").strip()
        if not timeline_id:
            raise ValueError("timeline or timelineId is required")
        spec = _load_timeline_spec(project, timeline_id, version or None)
    spec = _normalize_timeline_spec(project, spec, args)
    motion_id = uid("motion")
    package_dir = _project_dir(dept_id, project_id) / "motion" / motion_id
    payload = _motion_timeline_payload(project, spec)
    renderers = _motion_renderers(args)
    renderer = renderers[0] if len(renderers) == 1 else "multi"
    packages: list[dict[str, Any]] = []
    written: list[str] = []
    for package_renderer in renderers:
        target_dir = package_dir if len(renderers) == 1 else package_dir / package_renderer
        if package_renderer == "revideo":
            package_files = _write_revideo_package(target_dir, payload)
        elif package_renderer == "hyperframes":
            package_files, _ = _write_hyperframes_package(target_dir, payload)
        else:
            package_files = _write_motion_package(target_dir, payload)
        written.extend(package_files)
        entry_point = {
            "revideo": "src/project.ts",
            "hyperframes": "index.html",
        }.get(package_renderer, "src/index.tsx")
        packages.append({
            "renderer": package_renderer,
            "packageDir": str(target_dir),
            "entryPoint": str(target_dir / entry_point),
            "files": package_files,
            "commands": _motion_package_commands(package_renderer),
        })
    manifest = {
        "id": motion_id,
        "projectId": project_id,
        "timelineId": spec["id"],
        "timelineVersion": spec["version"],
        "renderer": renderer,
        "renderers": renderers,
        "compositionId": payload["compositionId"],
        "templateId": payload.get("templateId"),
        "styleGuide": {
            "id": (payload.get("styleGuide") or {}).get("id"),
            "name": (payload.get("styleGuide") or {}).get("name"),
            "type": (payload.get("styleGuide") or {}).get("type"),
            "fonts": (payload.get("styleGuide") or {}).get("fonts"),
            "colors": (payload.get("styleGuide") or {}).get("colors"),
            "safeArea": (payload.get("styleGuide") or {}).get("safeArea"),
        } if isinstance(payload.get("styleGuide"), dict) else None,
        "packageDir": str(package_dir),
        "entryPoint": packages[0]["entryPoint"] if packages else None,
        "createdAt": now_ms(),
        "files": written,
        "commands": packages[0]["commands"] if packages else {},
        "packages": packages,
        "status": "packaged",
    }
    manifest_path = package_dir / "motion_manifest.json"
    _write_json(manifest_path, manifest)
    artifact = await _persist_file_artifact(
        repo,
        path=manifest_path,
        owner_dept=dept_id,
        created_by=str(run.get("requestedBy") or dept_id),
        name=f"{motion_id}-{renderer}-manifest.json",
        tags=["video_motion", *renderers, "motion_package"],
        project_id=project_id,
        note=f"created {'/'.join(renderers)} motion package for timeline {spec['id']} v{spec['version']}",
    )
    manifest["artifactId"] = artifact["id"]
    manifest["artifactUri"] = artifact.get("uri")
    manifest["artifactStorage"] = artifact.get("storage")
    manifest["artifactObjectStore"] = artifact.get("objectStore") if isinstance(artifact.get("objectStore"), dict) else None
    manifest["downloadUrl"] = f"/api/artifacts/{artifact['id']}/download"
    manifest["previewUrl"] = f"/api/artifacts/{artifact['id']}/preview"
    artifact.update(_video_artifact_context_fields(project, artifact["id"], timeline=spec))
    await repo.put_entity("artifact", artifact, dept=dept_id, project=project_id, status=artifact.get("status"), ts=manifest["createdAt"])
    render_result = None
    render_artifact = None
    if bool(args.get("render") or args.get("renderNow") or args.get("render_now")):
        if "remotion" in renderers and len(renderers) == 1:
            render_result, render_artifact = await _maybe_render_remotion(repo, run, project, manifest, package_dir, args)
            manifest["renderResult"] = render_result
            if render_artifact:
                manifest["renderArtifactId"] = render_artifact["id"]
        elif "hyperframes" in renderers and len(renderers) == 1:
            render_result, render_artifact = await _maybe_render_hyperframes(repo, run, project, manifest, package_dir, args)
            manifest["renderResult"] = render_result
            if render_artifact:
                manifest["renderArtifactId"] = render_artifact["id"]
        else:
            render_result = {"ok": False, "status": "skipped", "reason": "Automatic rendering is currently supported for a single renderer=remotion or renderer=hyperframes only."}
            manifest["renderResult"] = render_result
    project.setdefault("motionPackages", []).append(manifest)
    _save_project(project)
    await repo.put_entity("video_project", project, dept=dept_id, project=project_id, status="active", ts=project["updatedAt"])
    await repo.put_entity("video_motion", manifest, dept=dept_id, project=project_id, status=manifest.get("status"), ts=manifest["createdAt"])
    return {
        "ok": True,
        "motion": manifest,
        "artifact": artifact,
        "renderArtifact": render_artifact,
        "renderResult": render_result,
        "context": _media_context(project, timeline=spec),
    }


async def _maybe_render_remotion(
    repo: Any,
    run: dict[str, Any],
    project: dict[str, Any],
    manifest: dict[str, Any],
    package_dir: Path,
    args: dict[str, Any],
) -> tuple[dict[str, Any], dict[str, Any] | None]:
    npm = shutil.which("npm")
    npx = shutil.which("npx")
    if not npx:
        return {"ok": False, "status": "skipped", "reason": "npx is unavailable"}, None
    if not (package_dir / "node_modules" / "remotion").exists():
        if not args.get("allowInstall") and not args.get("allow_install"):
            return {"ok": False, "status": "skipped", "reason": "Remotion dependencies are not installed; rerun with allowInstall=true or run npm install in packageDir."}, None
        if not npm:
            return {"ok": False, "status": "skipped", "reason": "npm is unavailable for installing Remotion dependencies"}, None
        install = await asyncio.to_thread(_run, [npm, "install"], cwd=package_dir, timeout=float(args.get("installTimeoutSeconds") or 300))
        if install["returnCode"] != 0:
            return {"ok": False, "status": "install_failed", "stderr": install.get("stderr", "")[-2000:]}, None
    output_path = package_dir / "out" / str(args.get("outputName") or "atrium-video.mp4")
    if output_path.suffix.lower() != ".mp4":
        output_path = output_path.with_suffix(".mp4")
    result = await asyncio.to_thread(
        _run,
        [npx, "remotion", "render", "src/index.tsx", str(manifest.get("compositionId") or "AtriumVideo"), str(output_path), "--overwrite"],
        cwd=package_dir,
        timeout=float(args.get("timeoutSeconds") or 1800),
    )
    if result["returnCode"] != 0 or not output_path.is_file():
        return {"ok": False, "status": "render_failed", "returnCode": result["returnCode"], "stderr": result.get("stderr", "")[-3000:]}, None
    render_id = uid("render")
    artifact = await _persist_file_artifact(
        repo,
        path=output_path,
        owner_dept=str(project["ownerDept"]),
        created_by=str(run.get("requestedBy") or project["ownerDept"]),
        name=output_path.name,
        tags=["video_render", "remotion", str(args.get("kind") or "preview")],
        project_id=str(project["id"]),
        note=f"rendered Remotion package {manifest['id']}",
    )
    render_manifest = {
        "id": render_id,
        "projectId": project["id"],
        "timelineId": manifest.get("timelineId"),
        "timelineVersion": manifest.get("timelineVersion"),
        "kind": str(args.get("kind") or "preview"),
        "path": str(output_path),
        "artifactId": artifact["id"],
        "artifactUri": artifact.get("uri"),
        "artifactStorage": artifact.get("storage"),
        "artifactObjectStore": artifact.get("objectStore") if isinstance(artifact.get("objectStore"), dict) else None,
        "downloadUrl": f"/api/artifacts/{artifact['id']}/download",
        "previewUrl": f"/api/artifacts/{artifact['id']}/preview",
        "createdAt": now_ms(),
        "renderer": "remotion",
        "motionId": manifest["id"],
    }
    artifact.update(_video_artifact_context_fields(project, artifact["id"], render=render_manifest))
    await repo.put_entity("artifact", artifact, dept=str(project["ownerDept"]), project=str(project["id"]), status=artifact.get("status"), ts=render_manifest["createdAt"])
    project.setdefault("renders", []).append(render_manifest)
    await repo.put_entity("video_render", render_manifest, dept=str(project["ownerDept"]), project=str(project["id"]), status="done", ts=render_manifest["createdAt"])
    return {"ok": True, "status": "rendered", "render": render_manifest, "stdout": result.get("stdout", "")[-1200:]}, artifact


def _node_major_version(value: str) -> int | None:
    match = re.search(r"v?(\d+)", str(value or ""))
    if not match:
        return None
    try:
        return int(match.group(1))
    except ValueError:
        return None


def _hyperframes_cli_command(package_dir: Path, args: dict[str, Any]) -> tuple[list[str] | None, str | None]:
    local = package_dir / "node_modules" / ".bin" / "hyperframes"
    if local.exists():
        return [str(local)], None
    global_cli = shutil.which("hyperframes")
    if global_cli:
        return [global_cli], None
    npx = shutil.which("npx")
    if not npx:
        return None, "npx is unavailable"
    if not (args.get("allowInstall") or args.get("allow_install")):
        return None, "HyperFrames CLI is not installed; rerun with allowInstall=true or run npm install in packageDir."
    return [npx, "--yes", "hyperframes"], None


async def _maybe_render_hyperframes(
    repo: Any,
    run: dict[str, Any],
    project: dict[str, Any],
    manifest: dict[str, Any],
    package_dir: Path,
    args: dict[str, Any],
) -> tuple[dict[str, Any], dict[str, Any] | None]:
    node = shutil.which("node")
    if not node:
        return {"ok": False, "status": "skipped", "reason": "Node.js 22+ is required for HyperFrames"}, None
    node_check = await asyncio.to_thread(_run, [node, "--version"], timeout=20)
    node_major = _node_major_version(str(node_check.get("stdout") or node_check.get("stderr") or ""))
    if node_check["returnCode"] != 0 or node_major is None or node_major < 22:
        return {
            "ok": False,
            "status": "skipped",
            "reason": "Node.js 22+ is required for HyperFrames",
            "nodeVersion": str(node_check.get("stdout") or node_check.get("stderr") or "").strip(),
        }, None
    try:
        ffmpeg = _require_ffmpeg()
    except ValueError as exc:
        return {"ok": False, "status": "skipped", "reason": str(exc)}, None
    cli, reason = _hyperframes_cli_command(package_dir, args)
    if not cli:
        return {
            "ok": False,
            "status": "skipped",
            "reason": reason or "HyperFrames CLI is unavailable",
            "nodeVersion": str(node_check.get("stdout") or "").strip(),
            "ffmpeg": ffmpeg,
        }, None
    lint = await asyncio.to_thread(
        _run,
        [*cli, "lint"],
        cwd=package_dir,
        timeout=float(args.get("lintTimeoutSeconds") or 180),
    )
    if lint["returnCode"] != 0:
        return {
            "ok": False,
            "status": "lint_failed",
            "returnCode": lint["returnCode"],
            "stdout": str(lint.get("stdout") or "")[-3000:],
            "stderr": str(lint.get("stderr") or "")[-3000:],
            "nodeVersion": str(node_check.get("stdout") or "").strip(),
            "ffmpeg": ffmpeg,
        }, None
    inspect_result = None
    if args.get("inspect") or args.get("runInspect") or args.get("run_inspect"):
        inspect_result = await asyncio.to_thread(
            _run,
            [*cli, "inspect"],
            cwd=package_dir,
            timeout=float(args.get("inspectTimeoutSeconds") or 300),
        )
        if inspect_result["returnCode"] != 0 and args.get("strictInspect"):
            return {
                "ok": False,
                "status": "inspect_failed",
                "returnCode": inspect_result["returnCode"],
                "stdout": str(inspect_result.get("stdout") or "")[-3000:],
                "stderr": str(inspect_result.get("stderr") or "")[-3000:],
                "lint": {"returnCode": lint["returnCode"], "stdout": str(lint.get("stdout") or "")[-1200:], "stderr": str(lint.get("stderr") or "")[-1200:]},
            }, None
    output_name = safe_filename(str(args.get("outputName") or args.get("output_name") or "atrium-video.mp4"))
    output_path = package_dir / "out" / output_name
    if output_path.suffix.lower() not in {".mp4", ".webm"}:
        output_path = output_path.with_suffix(".mp4")
    render = await asyncio.to_thread(
        _run,
        [*cli, "render", "--output", str(output_path)],
        cwd=package_dir,
        timeout=float(args.get("timeoutSeconds") or 1800),
    )
    if not output_path.is_file():
        candidates = sorted(
            [
                *(package_dir / "out").glob("*.mp4"),
                *(package_dir / "out").glob("*.webm"),
                *(package_dir / "renders").glob("*.mp4"),
                *(package_dir / "renders").glob("*.webm"),
            ],
            key=lambda path: path.stat().st_mtime if path.is_file() else 0,
            reverse=True,
        )
        if candidates:
            output_path = candidates[0]
    if render["returnCode"] != 0 or not output_path.is_file():
        return {
            "ok": False,
            "status": "render_failed",
            "returnCode": render["returnCode"],
            "stdout": str(render.get("stdout") or "")[-3000:],
            "stderr": str(render.get("stderr") or "")[-3000:],
            "lint": {"returnCode": lint["returnCode"], "stdout": str(lint.get("stdout") or "")[-1200:], "stderr": str(lint.get("stderr") or "")[-1200:]},
            **({"inspect": {"returnCode": inspect_result["returnCode"], "stdout": str(inspect_result.get("stdout") or "")[-1200:], "stderr": str(inspect_result.get("stderr") or "")[-1200:]}} if inspect_result else {}),
        }, None
    render_id = uid("render")
    artifact = await _persist_file_artifact(
        repo,
        path=output_path,
        owner_dept=str(project["ownerDept"]),
        created_by=str(run.get("requestedBy") or project["ownerDept"]),
        name=output_path.name,
        tags=["video_render", "hyperframes", str(args.get("kind") or "preview")],
        project_id=str(project["id"]),
        note=f"rendered HyperFrames package {manifest['id']}",
    )
    render_manifest = {
        "id": render_id,
        "projectId": project["id"],
        "timelineId": manifest.get("timelineId"),
        "timelineVersion": manifest.get("timelineVersion"),
        "kind": str(args.get("kind") or "preview"),
        "path": str(output_path),
        "artifactId": artifact["id"],
        "artifactUri": artifact.get("uri"),
        "artifactStorage": artifact.get("storage"),
        "artifactObjectStore": artifact.get("objectStore") if isinstance(artifact.get("objectStore"), dict) else None,
        "downloadUrl": f"/api/artifacts/{artifact['id']}/download",
        "previewUrl": f"/api/artifacts/{artifact['id']}/preview",
        "createdAt": now_ms(),
        "renderer": "hyperframes",
        "motionId": manifest["id"],
        "lint": {"returnCode": lint["returnCode"], "stdout": str(lint.get("stdout") or "")[-1200:], "stderr": str(lint.get("stderr") or "")[-1200:]},
        **({"inspect": {"returnCode": inspect_result["returnCode"], "stdout": str(inspect_result.get("stdout") or "")[-1200:], "stderr": str(inspect_result.get("stderr") or "")[-1200:]}} if inspect_result else {}),
    }
    artifact.update(_video_artifact_context_fields(project, artifact["id"], render=render_manifest))
    await repo.put_entity("artifact", artifact, dept=str(project["ownerDept"]), project=str(project["id"]), status=artifact.get("status"), ts=render_manifest["createdAt"])
    project.setdefault("renders", []).append(render_manifest)
    await repo.put_entity("video_render", render_manifest, dept=str(project["ownerDept"]), project=str(project["id"]), status="done", ts=render_manifest["createdAt"])
    return {
        "ok": True,
        "status": "rendered",
        "render": render_manifest,
        "stdout": str(render.get("stdout") or "")[-1200:],
        "stderr": str(render.get("stderr") or "")[-1200:],
        "nodeVersion": str(node_check.get("stdout") or "").strip(),
        "ffmpeg": ffmpeg,
    }, artifact


def _apply_image_layers(ffmpeg: str, project: dict[str, Any], spec: dict[str, Any], input_path: Path, render_dir: Path) -> Path:
    overlays = [item for item in (spec.get("overlays") or []) if isinstance(item, dict)]
    if not overlays:
        return input_path
    if not _ffmpeg_has_filter(ffmpeg, "overlay"):
        raise ValueError("ffmpeg overlay filter is required for image overlay layers")
    canvas = spec.get("canvas") if isinstance(spec.get("canvas"), dict) else {}
    width = int(canvas.get("width") or 1080)
    height = int(canvas.get("height") or 1920)
    inputs: list[str] = []
    filter_parts: list[str] = []
    current = "[0:v]"
    for idx, layer in enumerate(overlays, start=1):
        source = _image_source(project, layer)
        inputs.extend(["-loop", "1", "-i", str(source)])
        target_w, target_h = _overlay_size(layer, width, height)
        opacity = max(0.0, min(float(layer.get("opacity") or 1.0), 1.0))
        filter_parts.append(
            f"[{idx}:v]scale={target_w}:{target_h}:force_original_aspect_ratio=decrease,"
            f"format=rgba,colorchannelmixer=aa={opacity:.4f}[ov{idx}]"
        )
        x = _overlay_position_expr(layer, axis="x", canvas_size=width, item_size=target_w)
        y = _overlay_position_expr(layer, axis="y", canvas_size=height, item_size=target_h)
        start = max(0.0, _float_or_none(layer.get("start")) or 0.0)
        end = _float_or_none(layer.get("end"))
        enable = f"gte(t,{start:.3f})" if end is None else f"between(t,{start:.3f},{max(start, end):.3f})"
        label = f"[vo{idx}]"
        filter_parts.append(f"{current}[ov{idx}]overlay={x}:{y}:enable='{enable}'{label}")
        current = label
    out_path = render_dir / "overlays.mp4"
    command = [
        ffmpeg,
        "-y",
        "-i",
        str(input_path),
        *inputs,
        "-filter_complex",
        ";".join(filter_parts),
        "-map",
        current,
        "-map",
        "0:a?",
        "-c:v",
        "libx264",
        "-preset",
        "veryfast",
        "-crf",
        "20",
        "-c:a",
        "copy",
        "-shortest",
        str(out_path),
    ]
    result = _run(command, timeout=900)
    if result["returnCode"] != 0 or not out_path.is_file():
        raise ValueError(f"ffmpeg image overlay render failed: {(result.get('stderr') or '')[-1500:]}")
    return out_path


def _image_source(project: dict[str, Any], layer: dict[str, Any]) -> Path:
    asset_id = str(layer.get("assetId") or layer.get("sourceAssetId") or "").strip()
    if asset_id:
        return Path(_asset(project, asset_id)["path"]).resolve()
    raw = str(layer.get("source") or layer.get("sourcePath") or "").strip()
    if raw:
        path = Path(raw).expanduser()
        if not path.is_absolute():
            path = _project_dir(str(project["ownerDept"]), str(project["id"])) / path
        if path.is_file():
            return path.resolve()
    raise ValueError("overlay layer requires assetId or sourcePath")


def _overlay_size(layer: dict[str, Any], canvas_w: int, canvas_h: int) -> tuple[int, int]:
    raw_w = layer.get("width") or layer.get("w")
    raw_h = layer.get("height") or layer.get("h")
    width = _dimension_px(raw_w, canvas_w) if raw_w is not None else max(1, int(canvas_w * 0.25))
    height = _dimension_px(raw_h, canvas_h) if raw_h is not None else max(1, int(canvas_h * 0.25))
    return max(1, min(width, canvas_w)), max(1, min(height, canvas_h))


def _dimension_px(value: Any, base: int) -> int:
    if isinstance(value, (int, float)):
        return int(value)
    text = str(value or "").strip()
    if text.endswith("%"):
        try:
            return int(base * max(0.0, min(float(text[:-1]) / 100.0, 1.0)))
        except ValueError:
            return base
    try:
        return int(float(text))
    except ValueError:
        return base


def _overlay_position_expr(layer: dict[str, Any], *, axis: str, canvas_size: int, item_size: int) -> str:
    position = layer.get("position") if isinstance(layer.get("position"), dict) else {}
    raw = position.get(axis) or layer.get(axis)
    anchor = str(position.get("anchor") or layer.get("anchor") or "center")
    default = f"({canvas_size}-{item_size})/2"
    if raw is None:
        return default
    if isinstance(raw, (int, float)):
        base = float(raw)
    else:
        text = str(raw).strip()
        if text.endswith("%"):
            try:
                base = canvas_size * max(0.0, min(float(text[:-1]) / 100.0, 1.0))
            except ValueError:
                return default
        else:
            try:
                base = float(text)
            except ValueError:
                return default
    if "center" in anchor:
        base -= item_size / 2
    elif ("right" in anchor and axis == "x") or ("bottom" in anchor and axis == "y"):
        base -= item_size
    return str(max(0.0, min(base, max(0.0, canvas_size - item_size))))


def _apply_text_layers(ffmpeg: str, project: dict[str, Any], spec: dict[str, Any], input_path: Path, render_dir: Path) -> Path:
    texts = _timeline_text_layers(spec)
    if not texts:
        return input_path
    if not _ffmpeg_has_filter(ffmpeg, "drawtext"):
        return _apply_text_layers_with_overlay(ffmpeg, project, spec, input_path, render_dir, texts)
    filters = [_drawtext_filter(project, item) for item in texts]
    out_path = render_dir / "text.mp4"
    result = _run(
        [ffmpeg, "-y", "-i", str(input_path), "-vf", ",".join(filters), "-c:v", "libx264", "-preset", "veryfast", "-crf", "20", "-c:a", "copy", str(out_path)],
        timeout=900,
    )
    if result["returnCode"] != 0 or not out_path.is_file():
        raise ValueError(f"ffmpeg text render failed: {(result.get('stderr') or '')[-1500:]}")
    return out_path


def _timeline_text_layers(spec: dict[str, Any]) -> list[dict[str, Any]]:
    layers = [item for item in (spec.get("text") or []) if isinstance(item, dict) and str(item.get("text") or "").strip()]
    caption_style = spec.get("captionStyle") if isinstance(spec.get("captionStyle"), dict) else {}
    for caption in spec.get("captions") or []:
        if not isinstance(caption, dict) or not str(caption.get("text") or "").strip():
            continue
        style = caption.get("style") if isinstance(caption.get("style"), dict) else {}
        layer = {
            "id": caption.get("id") or uid("cap"),
            "text": caption.get("text"),
            "start": caption.get("start"),
            "end": caption.get("end"),
            "size": style.get("size") or caption_style.get("size") or 44,
            "font": style.get("font") or caption_style.get("font"),
            "fontFamily": style.get("fontFamily") or caption_style.get("fontFamily"),
            "color": style.get("color") or caption_style.get("color") or "#ffffff",
            "position": style.get("position") or caption_style.get("position") or {"x": "50%", "y": "84%", "anchor": "center"},
            "stroke": style.get("stroke") or caption_style.get("stroke") or {"color": "#000000", "width": 4},
            "shadow": style.get("shadow") or caption_style.get("shadow"),
            "box": style.get("box") or caption_style.get("box"),
            "lineSpacing": style.get("lineSpacing") or caption_style.get("lineSpacing"),
            "safeArea": style.get("safeArea") or caption_style.get("safeArea"),
            "animation": style.get("animation") or caption_style.get("animation"),
            "maxWidth": style.get("maxWidth") or caption_style.get("maxWidth"),
            "fontWeight": style.get("fontWeight") or caption_style.get("fontWeight"),
        }
        layers.append(layer)
    return [item for item in layers if isinstance(item, dict) and str(item.get("text") or "").strip()]


def _ffmpeg_has_filter(ffmpeg: str, name: str) -> bool:
    result = _run([ffmpeg, "-hide_banner", "-filters"], timeout=30)
    if result["returnCode"] != 0:
        return False
    return any(line.split()[1:2] == [name] for line in str(result.get("stdout") or "").splitlines())


def _apply_text_layers_with_overlay(
    ffmpeg: str,
    project: dict[str, Any],
    spec: dict[str, Any],
    input_path: Path,
    render_dir: Path,
    texts: list[dict[str, Any]],
) -> Path:
    try:
        from PIL import Image, ImageColor, ImageDraw, ImageFont
    except Exception as exc:
        raise ValueError("Pillow is required for text overlay fallback when ffmpeg drawtext is unavailable") from exc

    canvas = spec.get("canvas") if isinstance(spec.get("canvas"), dict) else {}
    width = int(canvas.get("width") or 1080)
    height = int(canvas.get("height") or 1920)
    overlay_paths: list[Path] = []
    for idx, layer in enumerate(texts, start=1):
        img = Image.new("RGBA", (width, height), (0, 0, 0, 0))
        draw = ImageDraw.Draw(img)
        text = str(layer.get("text") or "")
        size = int(layer.get("size") or layer.get("fontSize") or 64)
        font_file = _resolve_font(project, layer.get("fontFile") or layer.get("fontPath") or layer.get("font"))
        try:
            font = ImageFont.truetype(str(font_file), size=size) if font_file else ImageFont.load_default(size=size)
        except TypeError:
            font = ImageFont.load_default()
        except Exception:
            font = ImageFont.load_default()
        max_width = _dimension_px(layer.get("maxWidth") or layer.get("max_width") or "90%", width)
        text = _wrap_text_for_image(draw, text, font, max_width=max_width)
        bbox = draw.multiline_textbbox((0, 0), text, font=font, stroke_width=int((layer.get("stroke") or {}).get("width") or layer.get("strokeWidth") or 0))
        text_w = bbox[2] - bbox[0]
        text_h = bbox[3] - bbox[1]
        position = layer.get("position") if isinstance(layer.get("position"), dict) else {}
        anchor = str(position.get("anchor") or layer.get("anchor") or "center")
        x = _pixel_position(position.get("x"), axis="x", anchor=anchor, canvas_size=width, text_size=text_w, default=(width - text_w) / 2)
        y = _pixel_position(position.get("y"), axis="y", anchor=anchor, canvas_size=height, text_size=text_h, default=height * 0.82 - text_h / 2)
        box = layer.get("box") if isinstance(layer.get("box"), dict) else {}
        if box or layer.get("background"):
            padding = int(box.get("padding") or 16)
            opacity = max(0.0, min(float(box.get("opacity") or layer.get("backgroundOpacity") or 0.5), 1.0))
            box_color = _pil_color(ImageColor, box.get("color") or layer.get("background") or "#000000", alpha=int(255 * opacity))
            draw.rounded_rectangle(
                (x - padding, y - padding, x + text_w + padding, y + text_h + padding),
                radius=int(box.get("radius") or 0),
                fill=box_color,
            )
        stroke = layer.get("stroke") if isinstance(layer.get("stroke"), dict) else {}
        stroke_width = int(stroke.get("width") or layer.get("strokeWidth") or 0)
        draw.multiline_text(
            (x, y),
            text,
            font=font,
            fill=_pil_color(ImageColor, layer.get("color") or "#ffffff"),
            stroke_width=stroke_width,
            stroke_fill=_pil_color(ImageColor, stroke.get("color") or layer.get("strokeColor") or "#000000"),
            spacing=int(layer.get("lineSpacing") or 4),
        )
        overlay_path = render_dir / f"text_layer_{idx:03d}.png"
        img.save(overlay_path)
        overlay_paths.append(overlay_path)

    out_path = render_dir / "text.mp4"
    inputs: list[str] = []
    for path in overlay_paths:
        inputs.extend(["-i", str(path)])
    filters: list[str] = []
    current = "[0:v]"
    for idx, layer in enumerate(texts, start=1):
        start = max(0.0, _float_or_none(layer.get("start")) or 0.0)
        end = _float_or_none(layer.get("end"))
        enable = f"gte(t,{start:.3f})" if end is None else f"between(t,{start:.3f},{max(start, end):.3f})"
        label = f"[v{idx}]"
        filters.append(f"{current}[{idx}:v]overlay=0:0:enable='{enable}'{label}")
        current = label
    command = [
        ffmpeg,
        "-y",
        "-i",
        str(input_path),
        *inputs,
        "-filter_complex",
        ";".join(filters),
        "-map",
        current,
        "-map",
        "0:a?",
        "-c:v",
        "libx264",
        "-preset",
        "veryfast",
        "-crf",
        "20",
        "-c:a",
        "copy",
        str(out_path),
    ]
    result = _run(command, timeout=900)
    if result["returnCode"] != 0 or not out_path.is_file():
        raise ValueError(f"ffmpeg text overlay render failed: {(result.get('stderr') or '')[-1500:]}")
    return out_path


def _pil_color(image_color: Any, value: Any, *, alpha: int = 255) -> tuple[int, int, int, int]:
    try:
        rgb = image_color.getrgb(str(value or "#ffffff"))
    except Exception:
        rgb = (255, 255, 255)
    if len(rgb) == 4:
        return (int(rgb[0]), int(rgb[1]), int(rgb[2]), int(rgb[3]))
    return (int(rgb[0]), int(rgb[1]), int(rgb[2]), alpha)


def _wrap_text_for_image(draw: Any, text: str, font: Any, *, max_width: int) -> str:
    lines: list[str] = []
    for original_line in str(text or "").splitlines() or [""]:
        words = original_line.split()
        if not words:
            lines.append("")
            continue
        current = words[0]
        for word in words[1:]:
            candidate = f"{current} {word}"
            bbox = draw.textbbox((0, 0), candidate, font=font)
            if bbox[2] - bbox[0] <= max_width:
                current = candidate
            else:
                lines.append(current)
                current = word
        lines.append(current)
    return "\n".join(lines)


def _pixel_position(value: Any, *, axis: str, anchor: str, canvas_size: int, text_size: int, default: float) -> float:
    if value is None:
        return max(0.0, default)
    if isinstance(value, (int, float)):
        base = float(value)
    else:
        text = str(value).strip()
        if text.endswith("%"):
            try:
                base = canvas_size * max(0.0, min(float(text[:-1]) / 100.0, 1.0))
            except ValueError:
                return max(0.0, default)
        else:
            try:
                base = float(text)
            except ValueError:
                return max(0.0, default)
    if "center" in anchor:
        base -= text_size / 2
    elif ("right" in anchor and axis == "x") or ("bottom" in anchor and axis == "y"):
        base -= text_size
    return max(0.0, min(base, max(0.0, canvas_size - text_size)))


def _drawtext_filter(project: dict[str, Any], layer: dict[str, Any]) -> str:
    font_path = _resolve_font(project, layer.get("fontFile") or layer.get("fontPath") or layer.get("font"))
    position = layer.get("position") if isinstance(layer.get("position"), dict) else {}
    start = max(0.0, _float_or_none(layer.get("start")) or 0.0)
    end = _float_or_none(layer.get("end"))
    enable = f":enable='gte(t,{start:.3f})'" if end is None else f":enable='between(t,{start:.3f},{max(start, end):.3f})'"
    stroke = layer.get("stroke") if isinstance(layer.get("stroke"), dict) else {}
    box = layer.get("box") if isinstance(layer.get("box"), dict) else {}
    parts = []
    if font_path:
        parts.append(f"fontfile='{_ff_escape(str(font_path))}'")
    parts.extend([
        f"text='{_ff_escape(str(layer.get('text') or ''))}'",
        f"x={_position_expr(position.get('x'), axis='x', anchor=str(position.get('anchor') or layer.get('anchor') or 'center'))}",
        f"y={_position_expr(position.get('y'), axis='y', anchor=str(position.get('anchor') or layer.get('anchor') or 'center'))}",
        f"fontsize={int(layer.get('size') or layer.get('fontSize') or 64)}",
        f"fontcolor={_color(layer.get('color') or '#ffffff')}",
        f"borderw={int(stroke.get('width') or layer.get('strokeWidth') or 0)}",
        f"bordercolor={_color(stroke.get('color') or layer.get('strokeColor') or '#000000')}",
    ])
    if box or layer.get("background"):
        parts.extend([
            "box=1",
            f"boxcolor={_color(box.get('color') or layer.get('background') or '#000000')}@{float(box.get('opacity') or layer.get('backgroundOpacity') or 0.5):.3f}",
            f"boxborderw={int(box.get('padding') or 16)}",
        ])
    return "drawtext=" + ":".join(parts) + enable


def _resolve_font(project: dict[str, Any], value: Any) -> Path | None:
    raw = str(value or "").strip()
    if raw:
        path = Path(raw).expanduser()
        if not path.is_absolute():
            path = (_project_dir(str(project["ownerDept"]), str(project["id"])) / raw).resolve()
        if path.is_file():
            return path
        lowered = raw.lower()
        for font in _font_records([_project_dir(str(project["ownerDept"]), str(project["id"])) / "assets" / "font"]):
            if lowered in font["name"].lower() or lowered in font["familyHint"].lower():
                return Path(font["path"])
    for preferred in ("NotoSansThai", "Noto Sans Thai", "Prompt", "Sarabun", "Kanit", "Arial Unicode"):
        for font in _font_records([_project_dir(str(project["ownerDept"]), str(project["id"])) / "assets" / "font"]):
            if preferred.lower() in font["name"].lower() or preferred.lower() in font["familyHint"].lower():
                return Path(font["path"])
    return None


def _ff_escape(value: str) -> str:
    return value.replace("\\", "\\\\").replace(":", "\\:").replace("'", "\\'").replace("%", "\\%").replace("\n", "\\n")


def _color(value: Any) -> str:
    text = str(value or "#ffffff").strip()
    return text if re.fullmatch(r"#[0-9a-fA-F]{6}|[A-Za-z]+", text) else "#ffffff"


def _position_expr(value: Any, *, axis: str, anchor: str) -> str:
    center_term = "text_w" if axis == "x" else "text_h"
    full = "w" if axis == "x" else "h"
    default = f"({full}-{center_term})/2" if axis == "x" else f"{full}*0.82-{center_term}/2"
    if value is None:
        return default
    if isinstance(value, (int, float)):
        return str(float(value))
    text = str(value).strip()
    if text.endswith("%"):
        frac = max(0.0, min(float(text[:-1]) / 100.0, 1.0))
        if "center" in anchor:
            return f"{full}*{frac:.4f}-{center_term}/2"
        if ("right" in anchor and axis == "x") or ("bottom" in anchor and axis == "y"):
            return f"{full}*{frac:.4f}-{center_term}"
        return f"{full}*{frac:.4f}"
    if re.fullmatch(r"-?\d+(\.\d+)?", text):
        return text
    return default


def _apply_audio_layers(ffmpeg: str, project: dict[str, Any], spec: dict[str, Any], input_path: Path, render_dir: Path) -> Path:
    audio_layers = [item for item in (spec.get("audio") or []) if isinstance(item, dict)]
    if not audio_layers:
        return input_path
    probe = _ffprobe(input_path)
    duration = float((probe.get("format") or {}).get("duration") or (probe.get("video") or {}).get("duration") or 0)
    if duration <= 0:
        return input_path
    inputs = [str(input_path)]
    filters: list[str] = []
    mix_labels: list[str] = []
    has_base_audio = bool(probe.get("audio"))
    if has_base_audio:
        filters.append("[0:a]volume=1[a0]")
        mix_labels.append("[a0]")
    for idx, layer in enumerate(audio_layers, start=1):
        source = _audio_source(project, layer)
        inputs.extend(["-stream_loop", "-1", "-i", str(source)])
        volume = max(0.0, min(float(layer.get("volume") or 1.0), 4.0))
        start_ms = int(max(0.0, _float_or_none(layer.get("start")) or 0.0) * 1000)
        fade_out = max(0.0, _float_or_none(layer.get("fadeOut") or layer.get("fade_out")) or 0.0)
        chain = f"[{idx}:a]volume={volume},atrim=0:{duration:.3f},asetpts=PTS-STARTPTS"
        if start_ms:
            chain += f",adelay={start_ms}:all=1"
        if fade_out > 0:
            chain += f",afade=t=out:st={max(0.0, duration - fade_out):.3f}:d={fade_out:.3f}"
        label = f"[a{idx}]"
        filters.append(chain + label)
        mix_labels.append(label)
    out_path = render_dir / "audio.mp4"
    filter_complex = ";".join(filters)
    if len(mix_labels) > 1:
        filter_complex += ";" + "".join(mix_labels) + f"amix=inputs={len(mix_labels)}:duration=first:dropout_transition=2[aout]"
        audio_map = "[aout]"
    else:
        audio_map = mix_labels[0]
    command = [ffmpeg, "-y", "-i", str(input_path), *inputs[1:], "-filter_complex", filter_complex, "-map", "0:v", "-map", audio_map, "-c:v", "copy", "-c:a", "aac", "-shortest", str(out_path)]
    result = _run(command, timeout=900)
    if result["returnCode"] != 0 or not out_path.is_file():
        raise ValueError(f"ffmpeg audio render failed: {(result.get('stderr') or '')[-1500:]}")
    return out_path


def _audio_source(project: dict[str, Any], layer: dict[str, Any]) -> Path:
    asset_id = str(layer.get("assetId") or layer.get("sourceAssetId") or "").strip()
    if asset_id:
        return Path(_asset(project, asset_id)["path"]).resolve()
    raw = str(layer.get("source") or layer.get("sourcePath") or "").strip()
    if raw:
        path = Path(raw).expanduser()
        if not path.is_absolute():
            path = _project_dir(str(project["ownerDept"]), str(project["id"])) / path
        if path.is_file():
            return path.resolve()
    raise ValueError("audio layer requires assetId or sourcePath")


async def _patch_timeline(repo: Any, run: dict[str, Any], args: dict[str, Any]) -> dict[str, Any]:
    dept_id = run["departmentId"]
    project_id = str(args.get("projectId") or args.get("project_id") or "").strip()
    if not project_id:
        raise ValueError("projectId is required")
    project = _load_project(dept_id, project_id)
    base_id = str(args.get("timelineId") or args.get("timeline_id") or args.get("baseTimelineId") or "").strip()
    base_version = str(args.get("baseVersion") or args.get("version") or "").strip() or None
    spec = _load_timeline_spec(project, base_id, base_version) if base_id else _normalize_timeline_spec(project, args.get("timeline") or {}, args)
    patched = copy.deepcopy(spec)
    patched["parent"] = {"timelineId": spec.get("id"), "version": spec.get("version")}
    patched["id"] = _timeline_id(args.get("newTimelineId") or args.get("new_timeline_id") or spec.get("id"))
    patched["version"] = int(spec.get("version") or 1) + 1
    patch_ops = args.get("patch") or args.get("ops") or []
    if not isinstance(patch_ops, list):
        raise ValueError("patch must be a list")
    for op in patch_ops:
        if isinstance(op, dict):
            _apply_patch_op(patched, op)
    path = _save_timeline_spec(project, patched, source="patch_timeline")
    _append_project_audit(
        project,
        "timeline.patch",
        run=run,
        entity_type="video_timeline",
        entity_id=patched["id"],
        summary=f"Patched timeline {patched['id']} v{patched['version']} with {len([op for op in patch_ops if isinstance(op, dict)])} operations",
        refs={"timelineId": patched["id"], "version": patched["version"], "parent": patched.get("parent"), "operationCount": len([op for op in patch_ops if isinstance(op, dict)])},
        paths={"spec": str(path)},
        checksum=_sha256_file(path),
    )
    _save_project(project)
    await repo.put_entity("video_project", project, dept=dept_id, project=project_id, status="active", ts=project["updatedAt"])
    return {"ok": True, "timeline": patched, "path": str(path), "context": _media_context(project, timeline=patched)}


def _apply_patch_op(spec: dict[str, Any], op: dict[str, Any]) -> None:
    action = str(op.get("op") or op.get("type") or "").strip()
    if action == "set_canvas":
        spec.setdefault("canvas", {}).update({k: v for k, v in op.items() if k not in {"op", "type"}})
    elif action in {"set_style_guide", "setStyleGuide", "set_template", "setTemplate", "set_brand_style", "setBrandStyle"}:
        canvas = spec.get("canvas") if isinstance(spec.get("canvas"), dict) else {}
        style = op.get("styleGuide") if isinstance(op.get("styleGuide"), dict) else None
        template_id = op.get("templateId") or op.get("template") or op.get("brandStylePreset") or op.get("brandPreset")
        style = _deep_merge_dict(_timeline_style_template(template_id, canvas=canvas), style)
        if not style:
            style = _patch_payload(op)
        _apply_timeline_style(spec, style)
    elif action in {"add_text", "addText"}:
        item = _patch_payload(op, keep_id=True)
        item.setdefault("id", uid("txt"))
        spec.setdefault("text", []).append(item)
    elif action in {"update_text", "updateText"}:
        _update_by_id_or_first(spec.setdefault("text", []), _target_id(op, "textId"), _patch_payload(op))
    elif action in {"move_text", "moveText"}:
        _update_by_id_or_first(spec.setdefault("text", []), _target_id(op, "textId"), _position_patch(op))
    elif action in {"style_text", "styleText", "set_text_style", "setTextStyle"}:
        _update_by_id_or_first(spec.setdefault("text", []), _target_id(op, "textId"), _text_style_patch(op))
    elif action in {"retime_text", "retimeText"}:
        _update_by_id_or_first(spec.setdefault("text", []), _target_id(op, "textId"), _time_patch(op))
    elif action in {"remove_text", "removeText"}:
        _remove_by_id(spec.setdefault("text", []), _target_id(op, "textId"))
    elif action in {"add_overlay", "addOverlay"}:
        item = _patch_payload(op, keep_id=True, keep_type=True)
        item.setdefault("id", uid("ovl"))
        spec.setdefault("overlays", []).append(item)
    elif action in {"update_overlay", "updateOverlay"}:
        _update_by_id_or_first(spec.setdefault("overlays", []), _target_id(op, "overlayId"), _patch_payload(op))
    elif action in {"move_overlay", "moveOverlay"}:
        _update_by_id_or_first(spec.setdefault("overlays", []), _target_id(op, "overlayId"), _position_patch(op))
    elif action in {"retime_overlay", "retimeOverlay"}:
        _update_by_id_or_first(spec.setdefault("overlays", []), _target_id(op, "overlayId"), _time_patch(op))
    elif action in {"remove_overlay", "removeOverlay"}:
        _remove_by_id(spec.setdefault("overlays", []), _target_id(op, "overlayId"))
    elif action in {"add_caption", "addCaption"}:
        item = _patch_payload(op, keep_id=True)
        item.setdefault("id", uid("cap"))
        spec.setdefault("captions", []).append(item)
    elif action in {"update_caption", "updateCaption"}:
        _update_by_id_or_first(spec.setdefault("captions", []), _target_id(op, "captionId"), _patch_payload(op))
    elif action in {"retime_caption", "retimeCaption"}:
        _update_by_id_or_first(spec.setdefault("captions", []), _target_id(op, "captionId"), _time_patch(op))
    elif action in {"shift_captions", "shiftCaptions"}:
        _shift_timed_items(spec.setdefault("captions", []), _float_or_none(op.get("by") or op.get("seconds") or op.get("delta")) or 0.0)
    elif action in {"remove_caption", "removeCaption"}:
        _remove_by_id(spec.setdefault("captions", []), _target_id(op, "captionId"))
    elif action in {"replace_captions", "replaceCaptions"}:
        captions = op.get("captions")
        if not isinstance(captions, list):
            raise ValueError("replace_captions requires captions list")
        spec["captions"] = [item for item in captions if isinstance(item, dict)]
    elif action in {"set_caption_style", "setCaptionStyle", "update_caption_style", "style_captions", "styleCaptions"}:
        spec["captionStyle"] = _patch_payload(op)
    elif action in {"add_clip", "addClip"}:
        item = _patch_payload(op, keep_id=True, keep_type=True)
        item.setdefault("id", uid("clip"))
        spec.setdefault("clips", []).append(item)
    elif action in {"trim_clip", "trimClip", "update_clip", "updateClip"}:
        _update_by_id_or_first(spec.setdefault("clips", []), _target_id(op, "clipId"), _patch_payload(op))
    elif action in {"cut_range", "cutRange", "remove_range", "removeRange", "delete_range", "deleteRange"}:
        _cut_range(spec.setdefault("clips", []), op)
    elif action in {"remove_clip", "removeClip"}:
        _remove_by_id(spec.setdefault("clips", []), _target_id(op, "clipId"))
    elif action in {"set_clip_speed", "setClipSpeed"}:
        _update_by_id_or_first(spec.setdefault("clips", []), _target_id(op, "clipId"), {"speed": op.get("speed") or op.get("playbackRate")})
    elif action in {"set_clip_crop", "setClipCrop"}:
        _update_by_id_or_first(spec.setdefault("clips", []), _target_id(op, "clipId"), {"crop": op.get("crop") or {k: v for k, v in op.items() if k in {"x", "y", "width", "height", "w", "h"}}})
    elif action in {"add_transition", "addTransition"}:
        item = _patch_payload(op, keep_id=True, keep_type=True)
        item.setdefault("id", uid("trn"))
        spec.setdefault("transitions", []).append(item)
    elif action in {"update_transition", "updateTransition"}:
        _update_by_id_or_first(spec.setdefault("transitions", []), _target_id(op, "transitionId"), _patch_payload(op))
    elif action in {"remove_transition", "removeTransition"}:
        _remove_by_id(spec.setdefault("transitions", []), _target_id(op, "transitionId"))
    elif action in {"add_audio", "addAudio"}:
        item = _patch_payload(op, keep_id=True, keep_type=True)
        item.setdefault("id", uid("aud"))
        spec.setdefault("audio", []).append(item)
    elif action in {"replace_audio", "replaceAudio"}:
        item = _patch_payload(op, keep_id=True, keep_type=True)
        item.setdefault("id", str(op.get("id") or op.get("audioId") or "aud_replacement"))
        spec["audio"] = [item]
    elif action in {"update_audio", "updateAudio", "set_audio", "setAudio"}:
        _update_by_id_or_first(spec.setdefault("audio", []), _target_id(op, "audioId"), _patch_payload(op))
    elif action in {"remove_audio", "removeAudio"}:
        _remove_by_id(spec.setdefault("audio", []), _target_id(op, "audioId"))
    elif action in {"set_audio_mix", "setAudioMix", "duck_audio", "duckAudio"}:
        spec.setdefault("audioMix", {}).update(_patch_payload(op))
    elif action in {"add_effect", "addEffect"}:
        item = _patch_payload(op, keep_id=True, keep_type=True)
        item.setdefault("id", uid("fx"))
        spec.setdefault("effects", []).append(item)
    elif action in {"update_effect", "updateEffect"}:
        _update_by_id_or_first(spec.setdefault("effects", []), _target_id(op, "effectId"), _patch_payload(op))
    elif action in {"remove_effect", "removeEffect"}:
        _remove_by_id(spec.setdefault("effects", []), _target_id(op, "effectId"))
    elif action in {"set_effects", "replace_effects", "replaceEffects"}:
        effects = op.get("effects")
        if not isinstance(effects, list):
            raise ValueError("set_effects requires effects list")
        spec["effects"] = [item for item in effects if isinstance(item, dict)]
    elif action in {"set_keyframes", "setKeyframes", "add_keyframes", "addKeyframes"}:
        _set_keyframes(spec, op)
    elif action in {"set_export", "setExport"}:
        spec.setdefault("export", {}).update({k: v for k, v in op.items() if k not in {"op", "type"}})


def _patch_payload(op: dict[str, Any], *, keep_id: bool = False, keep_type: bool = False) -> dict[str, Any]:
    skip = set(PATCH_CONTROL_KEYS)
    if keep_id:
        skip.discard("id")
    if keep_type:
        skip.discard("type")
    return {key: value for key, value in op.items() if key not in skip}


def _target_id(op: dict[str, Any], *aliases: str) -> str:
    for key in ("id", "targetId", *aliases):
        value = str(op.get(key) or "").strip()
        if value:
            return value
    return ""


def _position_patch(op: dict[str, Any]) -> dict[str, Any]:
    payload = _patch_payload(op)
    position = payload.pop("position", None)
    if not isinstance(position, dict):
        position = {}
    for key in ("x", "y", "anchor"):
        if key in payload:
            position[key] = payload.pop(key)
    out: dict[str, Any] = {}
    if position:
        out["position"] = position
    for key in ("start", "end", "duration", "animation", "maxWidth", "maxHeight"):
        if key in payload:
            out[key] = payload[key]
    return out


def _text_style_patch(op: dict[str, Any]) -> dict[str, Any]:
    payload = _patch_payload(op)
    out: dict[str, Any] = {}
    aliases = {
        "font": "font",
        "fontFile": "fontFile",
        "fontPath": "fontPath",
        "size": "size",
        "fontSize": "size",
        "color": "color",
        "textColor": "color",
        "maxWidth": "maxWidth",
        "lineSpacing": "lineSpacing",
        "animation": "animation",
    }
    for source, target in aliases.items():
        if source in payload:
            out[target] = payload[source]
    if isinstance(payload.get("stroke"), dict):
        out["stroke"] = payload["stroke"]
    else:
        stroke = {key: payload[key] for key in ("strokeColor", "strokeWidth") if key in payload}
        if stroke:
            out["stroke"] = {"color": stroke.get("strokeColor"), "width": stroke.get("strokeWidth")}
    if isinstance(payload.get("box"), dict):
        out["box"] = payload["box"]
    if isinstance(payload.get("shadow"), dict):
        out["shadow"] = payload["shadow"]
    return {key: value for key, value in out.items() if value is not None}


def _time_patch(op: dict[str, Any]) -> dict[str, Any]:
    out = {}
    start = _float_or_none(op.get("start"))
    end = _float_or_none(op.get("end"))
    duration = _float_or_none(op.get("duration"))
    if start is not None:
        out["start"] = max(0.0, start)
    if end is None and start is not None and duration is not None:
        end = start + max(0.0, duration)
    if end is not None:
        out["end"] = max(0.0, end)
    return out


def _update_by_id_or_first(items: list[Any], item_id: str, patch: dict[str, Any]) -> None:
    if item_id:
        _update_by_id(items, item_id, patch)
        return
    for item in items:
        if isinstance(item, dict):
            item.update(patch)
            return
    raise ValueError("timeline item not found: no target id and collection is empty")


def _update_by_id(items: list[Any], item_id: str, op: dict[str, Any]) -> None:
    if not item_id:
        raise ValueError("patch op requires id")
    for item in items:
        if isinstance(item, dict) and str(item.get("id")) == item_id:
            for key, value in op.items():
                if key not in PATCH_CONTROL_KEYS:
                    item[key] = value
            return
    raise ValueError(f"timeline item not found: {item_id}")


def _remove_by_id(items: list[Any], item_id: str) -> None:
    before = len(items)
    items[:] = [item for item in items if not (isinstance(item, dict) and str(item.get("id")) == item_id)]
    if len(items) == before:
        raise ValueError(f"timeline item not found: {item_id}")


def _shift_timed_items(items: list[Any], delta: float) -> None:
    for item in items:
        if not isinstance(item, dict):
            continue
        for key in ("start", "end"):
            value = _float_or_none(item.get(key))
            if value is not None:
                item[key] = max(0.0, round(value + delta, 3))


def _cut_range(clips: list[Any], op: dict[str, Any]) -> None:
    target_id = _target_id(op, "clipId")
    remove_start = _float_or_none(op.get("start") if op.get("start") is not None else op.get("in"))
    remove_end = _float_or_none(op.get("end") if op.get("end") is not None else op.get("out"))
    if remove_start is None or remove_end is None or remove_end <= remove_start:
        raise ValueError("cut_range requires start/end")
    next_clips: list[Any] = []
    changed = False
    for clip in clips:
        if not isinstance(clip, dict) or (target_id and str(clip.get("id")) != target_id):
            next_clips.append(clip)
            continue
        clip_start = _float_or_none(clip.get("in") if clip.get("in") is not None else clip.get("start")) or 0.0
        clip_end = _float_or_none(clip.get("out") if clip.get("out") is not None else clip.get("end"))
        if clip_end is None:
            clip_end = remove_end
        if remove_end <= clip_start or remove_start >= clip_end:
            next_clips.append(clip)
            continue
        changed = True
        if remove_start > clip_start:
            before = copy.deepcopy(clip)
            before["id"] = f"{clip.get('id') or uid('clip')}_before"
            before["out"] = round(remove_start, 3)
            next_clips.append(before)
        if remove_end < clip_end:
            after = copy.deepcopy(clip)
            after["id"] = f"{clip.get('id') or uid('clip')}_after"
            after["in"] = round(remove_end, 3)
            next_clips.append(after)
    if not changed:
        raise ValueError("cut_range target did not overlap any clip")
    clips[:] = next_clips


def _set_keyframes(spec: dict[str, Any], op: dict[str, Any]) -> None:
    keyframes = op.get("keyframes")
    if not isinstance(keyframes, list):
        raise ValueError("set_keyframes requires keyframes list")
    collection_name = str(op.get("collection") or op.get("target") or "text").strip()
    collection_map = {
        "text": "text",
        "texts": "text",
        "caption": "captions",
        "captions": "captions",
        "overlay": "overlays",
        "overlays": "overlays",
        "clip": "clips",
        "clips": "clips",
        "effect": "effects",
        "effects": "effects",
        "audio": "audio",
    }
    collection = collection_map.get(collection_name, collection_name)
    target_id = _target_id(op)
    _update_by_id_or_first(spec.setdefault(collection, []), target_id, {"keyframes": [item for item in keyframes if isinstance(item, dict)]})


def _format_srt_time(seconds: Any) -> str:
    value = max(0.0, _float_or_none(seconds) or 0.0)
    millis = int(round(value * 1000))
    hours, rem = divmod(millis, 3_600_000)
    minutes, rem = divmod(rem, 60_000)
    secs, ms = divmod(rem, 1000)
    return f"{hours:02d}:{minutes:02d}:{secs:02d},{ms:03d}"


def _format_vtt_time(seconds: Any) -> str:
    return _format_srt_time(seconds).replace(",", ".")


def _write_srt(captions: list[dict[str, Any]], path: Path) -> None:
    lines: list[str] = []
    for idx, caption in enumerate(captions, start=1):
        text = str(caption.get("text") or "").strip()
        if not text:
            continue
        lines.extend([
            str(idx),
            f"{_format_srt_time(caption.get('start'))} --> {_format_srt_time(caption.get('end'))}",
            text,
            "",
        ])
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text("\n".join(lines).strip() + "\n", encoding="utf-8")


def _write_vtt(captions: list[dict[str, Any]], path: Path) -> None:
    lines = ["WEBVTT", ""]
    for caption in captions:
        text = str(caption.get("text") or "").strip()
        if not text:
            continue
        lines.extend([
            f"{_format_vtt_time(caption.get('start'))} --> {_format_vtt_time(caption.get('end'))}",
            text,
            "",
        ])
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text("\n".join(lines).strip() + "\n", encoding="utf-8")


def _deep_merge_dict(base: dict[str, Any] | None, overlay: dict[str, Any] | None) -> dict[str, Any]:
    merged = copy.deepcopy(base or {})
    for key, value in (overlay or {}).items():
        if isinstance(value, dict) and isinstance(merged.get(key), dict):
            merged[key] = _deep_merge_dict(merged[key], value)
        else:
            merged[key] = copy.deepcopy(value)
    return merged


def _caption_style_preset(name: Any, *, canvas: dict[str, Any] | None = None) -> dict[str, Any] | None:
    preset = str(name or "").strip().lower().replace("_", ".").replace("-", ".")
    if not preset:
        return None
    aliases = {
        "shorts": "shorts.bold",
        "tiktok": "shorts.bold",
        "reels": "shorts.bold",
        "bold": "shorts.bold",
        "thai": "thai.bold",
        "karaoke": "karaoke.highlight",
        "highlight": "karaoke.highlight",
        "lower.third": "lower_third.clean",
        "lower.third.clean": "lower_third.clean",
        "lowerthird": "lower_third.clean",
        "lower": "lower_third.clean",
        "minimal": "minimal.clean",
    }
    preset = aliases.get(preset, preset)
    width = int(_float_or_none((canvas or {}).get("width")) or 1080)
    height = int(_float_or_none((canvas or {}).get("height")) or 1920)
    large = height >= 1600 or width >= 1000
    presets: dict[str, dict[str, Any]] = {
        "shorts.bold": {
            "preset": "shorts.bold",
            "fontFamily": "Kanit",
            "size": 64 if large else 34,
            "color": "#ffffff",
            "position": {"x": "50%", "y": "82%", "anchor": "center"},
            "stroke": {"color": "#000000", "width": 6 if large else 4},
            "shadow": {"color": "#000000", "opacity": 0.45, "blur": 10, "x": 0, "y": 4},
            "box": {"color": "#000000", "opacity": 0.16, "padding": 14, "radius": 8},
            "safeArea": {"top": 120, "bottom": 180, "left": 72, "right": 72},
        },
        "thai.bold": {
            "preset": "thai.bold",
            "fontFamily": "Noto Sans Thai",
            "size": 58 if large else 32,
            "color": "#ffffff",
            "position": {"x": "50%", "y": "80%", "anchor": "center"},
            "stroke": {"color": "#111111", "width": 5 if large else 4},
            "box": {"color": "#111111", "opacity": 0.2, "padding": 12, "radius": 8},
            "safeArea": {"top": 120, "bottom": 190, "left": 72, "right": 72},
        },
        "karaoke.highlight": {
            "preset": "karaoke.highlight",
            "fontFamily": "Kanit",
            "size": 62 if large else 34,
            "color": "#ffffff",
            "highlightColor": "#ffd23f",
            "karaoke": True,
            "position": {"x": "50%", "y": "82%", "anchor": "center"},
            "stroke": {"color": "#000000", "width": 6 if large else 4},
            "shadow": {"color": "#000000", "opacity": 0.5, "blur": 8, "x": 0, "y": 3},
        },
        "lower_third.clean": {
            "preset": "lower_third.clean",
            "fontFamily": "Inter",
            "size": 42 if large else 26,
            "color": "#ffffff",
            "position": {"x": "50%", "y": "78%", "anchor": "center"},
            "stroke": {"color": "#000000", "width": 2},
            "box": {"color": "#101010", "opacity": 0.72, "padding": 16, "radius": 8},
            "safeArea": {"top": 96, "bottom": 150, "left": 64, "right": 64},
        },
        "minimal.clean": {
            "preset": "minimal.clean",
            "fontFamily": "Inter",
            "size": 38 if large else 24,
            "color": "#ffffff",
            "position": {"x": "50%", "y": "86%", "anchor": "center"},
            "stroke": {"color": "#000000", "width": 3},
        },
    }
    result = presets.get(preset)
    if not result:
        return None
    style = copy.deepcopy(result)
    style["canvas"] = {"width": width, "height": height}
    return style


def _timeline_style_template(name: Any, *, canvas: dict[str, Any] | None = None) -> dict[str, Any] | None:
    raw = str(name or "").strip().lower().replace("_", ".").replace("-", ".").replace(" ", ".")
    if not raw:
        return None
    aliases = {
        "social": "brand.social.bold",
        "shorts": "brand.social.bold",
        "tiktok": "brand.social.bold",
        "reels": "brand.social.bold",
        "creator": "brand.thai.creator",
        "thai": "brand.thai.creator",
        "corporate": "brand.clean.corporate",
        "clean": "brand.clean.corporate",
        "business": "brand.clean.corporate",
        "news": "brand.news.flash",
        "podcast": "brand.podcast.clean",
    }
    preset = aliases.get(raw, raw)
    width = int(_float_or_none((canvas or {}).get("width")) or 1080)
    height = int(_float_or_none((canvas or {}).get("height")) or 1920)
    large = height >= 1600 or width >= 1000
    templates: dict[str, dict[str, Any]] = {
        "brand.social.bold": {
            "id": "brand.social.bold",
            "type": "brandStyle",
            "name": "Social bold creator",
            "fonts": {"heading": "Kanit", "body": "Inter", "caption": "Kanit"},
            "colors": {"background": "#050505", "text": "#ffffff", "primary": "#00d1ff", "accent": "#ffd23f", "stroke": "#000000"},
            "safeArea": {"top": 150 if large else 76, "bottom": 260 if large else 128, "left": 72 if large else 36, "right": 72 if large else 36},
            "hookTextStyle": {
                "fontFamily": "Kanit",
                "size": 76 if large else 46,
                "color": "#ffffff",
                "position": {"x": "50%", "y": "15%", "anchor": "center"},
                "stroke": {"color": "#000000", "width": 7 if large else 5},
                "shadow": {"color": "#00d1ff", "opacity": 0.38, "blur": 12, "x": 0, "y": 4},
                "maxWidth": "86%",
                "animation": "pop",
                "fontWeight": 900,
            },
            "captionStyle": _deep_merge_dict(_caption_style_preset("karaoke.highlight", canvas=canvas), {"fontFamily": "Kanit"}),
            "lowerThirdStyle": {
                "fontFamily": "Kanit",
                "size": 44 if large else 28,
                "color": "#ffffff",
                "position": {"x": "50%", "y": "76%", "anchor": "center"},
                "box": {"color": "#050505", "opacity": 0.72, "padding": 18 if large else 12, "radius": 8},
                "maxWidth": "82%",
                "animation": "fade-up",
            },
            "motion": {"textAnimation": "pop", "captionAnimation": "fade-up", "overlayAnimation": "fade", "backgroundColor": "#050505"},
        },
        "brand.thai.creator": {
            "id": "brand.thai.creator",
            "type": "brandStyle",
            "name": "Thai creator bold",
            "fonts": {"heading": "Noto Sans Thai", "body": "Inter", "caption": "Noto Sans Thai"},
            "colors": {"background": "#101010", "text": "#ffffff", "primary": "#ff477e", "accent": "#ffe66d", "stroke": "#111111"},
            "safeArea": {"top": 140 if large else 72, "bottom": 240 if large else 118, "left": 72 if large else 36, "right": 72 if large else 36},
            "hookTextStyle": {
                "fontFamily": "Noto Sans Thai",
                "size": 70 if large else 42,
                "color": "#ffffff",
                "position": {"x": "50%", "y": "16%", "anchor": "center"},
                "stroke": {"color": "#111111", "width": 6 if large else 4},
                "maxWidth": "88%",
                "animation": "fade-up",
                "fontWeight": 900,
            },
            "captionStyle": _caption_style_preset("thai.bold", canvas=canvas),
            "lowerThirdStyle": {
                "fontFamily": "Noto Sans Thai",
                "size": 42 if large else 26,
                "color": "#ffffff",
                "position": {"x": "50%", "y": "78%", "anchor": "center"},
                "box": {"color": "#111111", "opacity": 0.68, "padding": 16 if large else 10, "radius": 8},
                "maxWidth": "84%",
                "animation": "fade-up",
            },
            "motion": {"textAnimation": "fade-up", "captionAnimation": "fade-up", "overlayAnimation": "fade", "backgroundColor": "#101010"},
        },
        "brand.clean.corporate": {
            "id": "brand.clean.corporate",
            "type": "brandStyle",
            "name": "Clean corporate",
            "fonts": {"heading": "Inter", "body": "Inter", "caption": "Inter"},
            "colors": {"background": "#0c1116", "text": "#f8fafc", "primary": "#38bdf8", "accent": "#a7f3d0", "stroke": "#0f172a"},
            "safeArea": {"top": 120 if large else 60, "bottom": 190 if large else 96, "left": 80 if large else 40, "right": 80 if large else 40},
            "hookTextStyle": {
                "fontFamily": "Inter",
                "size": 60 if large else 36,
                "color": "#f8fafc",
                "position": {"x": "50%", "y": "18%", "anchor": "center"},
                "stroke": {"color": "#0f172a", "width": 3 if large else 2},
                "maxWidth": "82%",
                "animation": "fade-up",
                "fontWeight": 800,
            },
            "captionStyle": _caption_style_preset("lower_third.clean", canvas=canvas),
            "lowerThirdStyle": _caption_style_preset("lower_third.clean", canvas=canvas),
            "motion": {"textAnimation": "fade-up", "captionAnimation": "fade", "overlayAnimation": "fade", "backgroundColor": "#0c1116"},
        },
        "brand.news.flash": {
            "id": "brand.news.flash",
            "type": "brandStyle",
            "name": "News flash",
            "fonts": {"heading": "Inter", "body": "Inter", "caption": "Inter"},
            "colors": {"background": "#080808", "text": "#ffffff", "primary": "#e11d48", "accent": "#f8fafc", "stroke": "#000000"},
            "safeArea": {"top": 112 if large else 58, "bottom": 210 if large else 104, "left": 72 if large else 36, "right": 72 if large else 36},
            "hookTextStyle": {
                "fontFamily": "Inter",
                "size": 66 if large else 40,
                "color": "#ffffff",
                "position": {"x": "50%", "y": "15%", "anchor": "center"},
                "stroke": {"color": "#000000", "width": 5 if large else 3},
                "box": {"color": "#e11d48", "opacity": 0.88, "padding": 16 if large else 10, "radius": 6},
                "maxWidth": "86%",
                "animation": "pop",
                "fontWeight": 900,
            },
            "captionStyle": _deep_merge_dict(_caption_style_preset("lower_third.clean", canvas=canvas), {"box": {"color": "#080808", "opacity": 0.82}}),
            "lowerThirdStyle": {
                "fontFamily": "Inter",
                "size": 42 if large else 26,
                "color": "#ffffff",
                "position": {"x": "50%", "y": "80%", "anchor": "center"},
                "box": {"color": "#e11d48", "opacity": 0.86, "padding": 14 if large else 9, "radius": 4},
                "maxWidth": "88%",
                "animation": "fade-up",
            },
            "motion": {"textAnimation": "pop", "captionAnimation": "fade-up", "overlayAnimation": "fade", "backgroundColor": "#080808"},
        },
        "brand.podcast.clean": {
            "id": "brand.podcast.clean",
            "type": "brandStyle",
            "name": "Podcast clean",
            "fonts": {"heading": "Inter", "body": "Inter", "caption": "Inter"},
            "colors": {"background": "#111827", "text": "#ffffff", "primary": "#f59e0b", "accent": "#60a5fa", "stroke": "#000000"},
            "safeArea": {"top": 128 if large else 64, "bottom": 220 if large else 110, "left": 72 if large else 36, "right": 72 if large else 36},
            "hookTextStyle": {
                "fontFamily": "Inter",
                "size": 58 if large else 34,
                "color": "#ffffff",
                "position": {"x": "50%", "y": "17%", "anchor": "center"},
                "stroke": {"color": "#000000", "width": 4 if large else 3},
                "maxWidth": "84%",
                "animation": "fade-up",
                "fontWeight": 850,
            },
            "captionStyle": _caption_style_preset("minimal.clean", canvas=canvas),
            "lowerThirdStyle": _caption_style_preset("lower_third.clean", canvas=canvas),
            "motion": {"textAnimation": "fade-up", "captionAnimation": "fade", "overlayAnimation": "fade", "backgroundColor": "#111827"},
        },
    }
    result = templates.get(preset)
    if not result:
        return None
    style = copy.deepcopy(result)
    style["canvas"] = {"width": width, "height": height}
    return style


def _timeline_style_template_catalog(*, canvas: dict[str, Any] | None = None) -> list[dict[str, Any]]:
    return [
        item for item in (
            _timeline_style_template("brand.social.bold", canvas=canvas),
            _timeline_style_template("brand.thai.creator", canvas=canvas),
            _timeline_style_template("brand.clean.corporate", canvas=canvas),
            _timeline_style_template("brand.news.flash", canvas=canvas),
            _timeline_style_template("brand.podcast.clean", canvas=canvas),
        )
        if item
    ]


def _timeline_style_name_from_prompt(prompt: str) -> str | None:
    text = prompt.lower()
    if any(key in text for key in ("tiktok", "reels", "shorts", "ไวรัล", "viral", "creator")):
        return "brand.social.bold"
    if any(key in text for key in ("ไทย", "thai", "อ่านง่าย")):
        return "brand.thai.creator"
    if any(key in text for key in ("corporate", "business", "บริษัท", "professional")):
        return "brand.clean.corporate"
    if any(key in text for key in ("news", "breaking", "ข่าว")):
        return "brand.news.flash"
    if any(key in text for key in ("podcast", "interview", "สัมภาษณ์")):
        return "brand.podcast.clean"
    return None


def _timeline_style_from_args(
    args: dict[str, Any],
    prompt: str,
    canvas: dict[str, Any],
    *,
    spec: dict[str, Any] | None = None,
) -> dict[str, Any] | None:
    explicit_name = (
        args.get("templateId")
        or args.get("template_id")
        or args.get("template")
        or args.get("timelineTemplate")
        or args.get("timeline_template")
        or args.get("brandStylePreset")
        or args.get("brand_style_preset")
        or args.get("brandPreset")
        or args.get("brand_preset")
    )
    style_preset = args.get("stylePreset") or args.get("style_preset")
    existing = spec.get("styleGuide") if isinstance((spec or {}).get("styleGuide"), dict) else None
    if not explicit_name and style_preset and _timeline_style_template(style_preset, canvas=canvas):
        explicit_name = style_preset
    if not explicit_name and isinstance(existing, dict):
        explicit_name = existing.get("id") or existing.get("templateId")
    if not explicit_name and isinstance(spec, dict):
        explicit_name = spec.get("templateId") or spec.get("template") or spec.get("brandStylePreset") or spec.get("brandPreset")
    if not explicit_name:
        explicit_name = _timeline_style_name_from_prompt(prompt)
    style = _timeline_style_template(explicit_name, canvas=canvas) if explicit_name else None
    if isinstance(existing, dict):
        style = _deep_merge_dict(style, existing)
    for key in ("styleGuide", "style_guide", "brandStyle", "brand_style"):
        if isinstance(args.get(key), dict):
            style = _deep_merge_dict(style, args[key])
    return style


def _merge_timeline_layer_style(base: dict[str, Any] | None, layer: dict[str, Any]) -> dict[str, Any]:
    if not isinstance(base, dict) or not base:
        return layer
    merged = _deep_merge_dict(base, layer)
    for key in ("id", "text", "start", "end", "assetId", "source", "sourcePath"):
        if key in layer:
            merged[key] = layer[key]
    return merged


def _apply_timeline_style(spec: dict[str, Any], style_guide: dict[str, Any]) -> None:
    if not style_guide:
        return
    existing = spec.get("styleGuide") if isinstance(spec.get("styleGuide"), dict) else {}
    style = _deep_merge_dict(existing, style_guide)
    spec["styleGuide"] = style
    spec["templateId"] = style.get("id") or spec.get("templateId")
    if isinstance(style.get("safeArea"), dict):
        spec.setdefault("canvas", {}).setdefault("safeArea", copy.deepcopy(style["safeArea"]))
    if isinstance(style.get("captionStyle"), dict):
        spec["captionStyle"] = _deep_merge_dict(style["captionStyle"], spec.get("captionStyle") if isinstance(spec.get("captionStyle"), dict) else {})
    hook_style = style.get("hookTextStyle") if isinstance(style.get("hookTextStyle"), dict) else {}
    lower_style = style.get("lowerThirdStyle") if isinstance(style.get("lowerThirdStyle"), dict) else {}
    text_layers: list[dict[str, Any]] = []
    for idx, layer in enumerate(spec.get("text") or []):
        if not isinstance(layer, dict):
            continue
        role = str(layer.get("role") or layer.get("type") or layer.get("templateRole") or "").lower()
        layer_id = str(layer.get("id") or "").lower()
        if role in {"lower_third", "lower-third", "lowerthird"} or "lower" in layer_id:
            text_layers.append(_merge_timeline_layer_style(lower_style, layer))
        elif role == "hook" or "hook" in layer_id or idx == 0:
            text_layers.append(_merge_timeline_layer_style(hook_style, layer))
        else:
            text_layers.append(layer)
    spec["text"] = text_layers


def _caption_style_from_tool_args(args: dict[str, Any], *, canvas: dict[str, Any] | None = None) -> dict[str, Any] | None:
    preset_name = (
        args.get("captionStylePreset")
        or args.get("caption_style_preset")
        or args.get("stylePreset")
        or args.get("style_preset")
        or args.get("captionPreset")
        or args.get("caption_preset")
    )
    style = _caption_style_preset(preset_name, canvas=canvas)
    for key in ("captionStyle", "caption_style", "style"):
        if isinstance(args.get(key), dict):
            style = _deep_merge_dict(style, args[key])
    return style


def _ass_time(seconds: Any) -> str:
    value = max(0.0, _float_or_none(seconds) or 0.0)
    centis = int(round(value * 100))
    hours, rem = divmod(centis, 360_000)
    minutes, rem = divmod(rem, 6_000)
    secs, cs = divmod(rem, 100)
    return f"{hours:d}:{minutes:02d}:{secs:02d}.{cs:02d}"


def _ass_color(value: Any, *, alpha: int = 0) -> str:
    raw = str(value or "#ffffff").strip()
    match = re.fullmatch(r"#?([0-9A-Fa-f]{6})", raw)
    if not match:
        raw = "ffffff"
    else:
        raw = match.group(1)
    rr, gg, bb = raw[0:2], raw[2:4], raw[4:6]
    return f"&H{max(0, min(int(alpha), 255)):02X}{bb.upper()}{gg.upper()}{rr.upper()}"


def _ass_alignment(style: dict[str, Any] | None) -> int:
    position = (style or {}).get("position") if isinstance((style or {}).get("position"), dict) else {}
    x_raw = str(position.get("x") or "50%").strip().rstrip("%")
    y_raw = str(position.get("y") or "82%").strip().rstrip("%")
    x = _float_or_none(x_raw)
    y = _float_or_none(y_raw)
    col = "center" if x is None or 35 <= x <= 65 else "left" if x < 35 else "right"
    row = "middle" if y is None or 35 <= y <= 65 else "top" if y < 35 else "bottom"
    return {
        ("bottom", "left"): 1,
        ("bottom", "center"): 2,
        ("bottom", "right"): 3,
        ("middle", "left"): 4,
        ("middle", "center"): 5,
        ("middle", "right"): 6,
        ("top", "left"): 7,
        ("top", "center"): 8,
        ("top", "right"): 9,
    }.get((row, col), 2)


def _ass_text(value: Any) -> str:
    return str(value or "").replace("\\", r"\\").replace("{", "(").replace("}", ")").replace("\n", r"\N")


def _ass_dialogue_text(caption: dict[str, Any], style: dict[str, Any] | None) -> str:
    words = [word for word in (caption.get("words") or []) if isinstance(word, dict) and str(word.get("text") or "").strip()]
    if (style or {}).get("karaoke") and words:
        pieces: list[str] = []
        for word in words:
            start = _float_or_none(word.get("start")) or _float_or_none(caption.get("start")) or 0.0
            end = _float_or_none(word.get("end")) or start + 0.1
            pieces.append(r"{\k%d}%s" % (max(1, int(round(max(0.05, end - start) * 100))), _ass_text(word.get("text"))))
        return " ".join(pieces)
    return _ass_text(caption.get("text"))


def _write_ass(captions: list[dict[str, Any]], path: Path, style: dict[str, Any] | None = None) -> None:
    style = copy.deepcopy(style or {})
    canvas = style.get("canvas") if isinstance(style.get("canvas"), dict) else {}
    width = int(_float_or_none(canvas.get("width")) or 1080)
    height = int(_float_or_none(canvas.get("height")) or 1920)
    box = style.get("box") if isinstance(style.get("box"), dict) else {}
    stroke = style.get("stroke") if isinstance(style.get("stroke"), dict) else {}
    font = str(style.get("fontFamily") or style.get("font") or "Arial").replace(",", " ").strip() or "Arial"
    size = int(_float_or_none(style.get("size") or style.get("fontSize")) or (58 if height >= 1600 else 30))
    outline = max(0, int(_float_or_none(stroke.get("width")) or 4))
    shadow = 1 if isinstance(style.get("shadow"), dict) else 0
    border_style = 3 if box else 1
    box_alpha = int(round(255 * (1.0 - max(0.0, min(float(box.get("opacity") or 0.0), 1.0))))) if box else 0
    lines = [
        "[Script Info]",
        "ScriptType: v4.00+",
        f"PlayResX: {width}",
        f"PlayResY: {height}",
        "ScaledBorderAndShadow: yes",
        "",
        "[V4+ Styles]",
        "Format: Name, Fontname, Fontsize, PrimaryColour, SecondaryColour, OutlineColour, BackColour, Bold, Italic, Underline, StrikeOut, ScaleX, ScaleY, Spacing, Angle, BorderStyle, Outline, Shadow, Alignment, MarginL, MarginR, MarginV, Encoding",
        (
            "Style: Default,"
            f"{font},{size},{_ass_color(style.get('color'))},{_ass_color(style.get('highlightColor') or '#ffd23f')},"
            f"{_ass_color(stroke.get('color') or '#000000')},{_ass_color(box.get('color') or '#000000', alpha=box_alpha)},"
            f"{-1 if bool(style.get('bold', True)) else 0},0,0,0,100,100,0,0,{border_style},{outline},{shadow},"
            f"{_ass_alignment(style)},48,48,72,1"
        ),
        "",
        "[Events]",
        "Format: Layer, Start, End, Style, Name, MarginL, MarginR, MarginV, Effect, Text",
    ]
    for caption in captions:
        text = str(caption.get("text") or "").strip()
        if not text:
            continue
        speaker = str(caption.get("speaker") or "").replace(",", " ").strip()
        lines.append(
            f"Dialogue: 0,{_ass_time(caption.get('start'))},{_ass_time(caption.get('end'))},Default,{speaker},0,0,0,,{_ass_dialogue_text(caption, style)}"
        )
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text("\n".join(lines).strip() + "\n", encoding="utf-8")


def _parse_srt_time(value: str) -> float | None:
    match = re.match(r"\s*(\d+):(\d+):(\d+)[,.](\d+)\s*", value)
    if not match:
        return None
    hours, minutes, seconds, millis = [int(part) for part in match.groups()]
    return hours * 3600 + minutes * 60 + seconds + millis / 1000.0


def _parse_srt_text(text: str) -> list[dict[str, Any]]:
    clean = text.replace("\ufeff", "").replace("\r\n", "\n").replace("\r", "\n")
    clean = re.sub(r"^WEBVTT.*?\n\n", "", clean, flags=re.I | re.S)
    blocks = re.split(r"\n{2,}", clean.strip())
    segments: list[dict[str, Any]] = []
    for block in blocks:
        lines = [line.strip() for line in block.splitlines() if line.strip()]
        if not lines:
            continue
        if re.fullmatch(r"\d+", lines[0]):
            lines = lines[1:]
        if not lines or "-->" not in lines[0]:
            continue
        start_raw, end_raw = lines[0].split("-->", 1)
        start = _parse_srt_time(start_raw.strip())
        end = _parse_srt_time(end_raw.strip().split()[0])
        if start is None or end is None:
            continue
        body = " ".join(lines[1:]).strip()
        if body:
            segments.append({"start": start, "end": max(start + 0.1, end), "text": body})
    return segments


def _estimated_words_from_text(text: str, start: float, end: float, *, speaker: Any = None) -> list[dict[str, Any]]:
    tokens = [token for token in re.findall(r"\S+", str(text or "").strip()) if token]
    if not tokens:
        return []
    start = max(0.0, float(start))
    end = max(start + 0.1, float(end))
    duration = end - start
    weights = [max(1, len(token.strip())) for token in tokens]
    total = max(1, sum(weights))
    cursor = start
    words: list[dict[str, Any]] = []
    elapsed = 0
    for idx, token in enumerate(tokens):
        elapsed += weights[idx]
        next_time = end if idx == len(tokens) - 1 else start + duration * elapsed / total
        word = {
            "text": token,
            "word": token,
            "start": round(cursor, 3),
            "end": round(max(cursor + 0.03, next_time), 3),
            "estimated": True,
        }
        if speaker:
            word["speaker"] = speaker
        words.append(word)
        cursor = max(cursor + 0.03, next_time)
    return words


def _words_with_timing(words: list[dict[str, Any]], text: str, start: float, end: float, *, speaker: Any = None) -> list[dict[str, Any]]:
    clean_words = [copy.deepcopy(word) for word in words if isinstance(word, dict) and str(word.get("text") or word.get("word") or "").strip()]
    if not clean_words:
        return _estimated_words_from_text(text, start, end, speaker=speaker)
    for word in clean_words:
        word_text = str(word.get("text") or word.get("word") or "").strip()
        word["text"] = word_text
        word["word"] = word_text
        if speaker and not word.get("speaker"):
            word["speaker"] = speaker
    if all(_float_or_none(word.get("start")) is not None and _float_or_none(word.get("end")) is not None for word in clean_words):
        for word in clean_words:
            word["start"] = round(max(0.0, _float_or_none(word.get("start")) or 0.0), 3)
            word["end"] = round(max(_float_or_none(word.get("start")) or 0.0, _float_or_none(word.get("end")) or 0.0), 3)
        return clean_words
    estimated = _estimated_words_from_text(text or " ".join(str(word.get("text") or "").strip() for word in clean_words), start, end, speaker=speaker)
    for idx, word in enumerate(estimated):
        if idx < len(clean_words):
            source = clean_words[idx]
            for key in ("score", "confidence", "speaker"):
                if source.get(key) is not None and not word.get(key):
                    word[key] = source[key]
    return estimated


def _normalize_word(raw: Any) -> dict[str, Any] | None:
    if not isinstance(raw, dict):
        return None
    text = str(raw.get("word") or raw.get("text") or "").strip()
    start = _float_or_none(raw.get("start") if raw.get("start") is not None else raw.get("startTime"))
    end = _float_or_none(raw.get("end") if raw.get("end") is not None else raw.get("endTime"))
    if not text:
        return None
    out = {"text": text, "word": text}
    if start is not None:
        out["start"] = round(max(0.0, start), 3)
    if end is not None:
        out["end"] = round(max(start or 0.0, end), 3)
    if raw.get("speaker"):
        out["speaker"] = raw.get("speaker")
    if raw.get("score") is not None:
        out["score"] = raw.get("score")
    if raw.get("confidence") is not None:
        out["confidence"] = raw.get("confidence")
    if raw.get("estimated") is not None:
        out["estimated"] = bool(raw.get("estimated"))
    return out


def _normalize_segment(raw: Any, idx: int = 0) -> dict[str, Any] | None:
    if isinstance(raw, str):
        text = raw.strip()
        if not text:
            return None
        start = float(idx)
        end = float(idx + 1)
        return {
            "id": f"seg_{idx + 1:04d}",
            "start": start,
            "end": end,
            "text": text,
            "words": _estimated_words_from_text(text, start, end),
        }
    if not isinstance(raw, dict):
        return None
    text = str(raw.get("text") or raw.get("caption") or "").strip()
    speaker = raw.get("speaker")
    start = _float_or_none(raw.get("start") if raw.get("start") is not None else raw.get("startTime"))
    end = _float_or_none(raw.get("end") if raw.get("end") is not None else raw.get("endTime"))
    words = [_normalize_word(item) for item in (raw.get("words") or []) if isinstance(item, dict)]
    words = [item for item in words if item]
    if not text and words:
        text = " ".join(str(word.get("text") or "").strip() for word in words).strip()
    if not text:
        return None
    if start is None:
        word_starts = [_float_or_none(word.get("start")) for word in words]
        start = next((value for value in word_starts if value is not None), float(idx))
    if end is None:
        word_ends = [_float_or_none(word.get("end")) for word in words]
        end = next((value for value in reversed(word_ends) if value is not None), start + 1.0)
    start = max(0.0, float(start))
    end = max(start + 0.1, float(end))
    words = _words_with_timing(words, text, start, end, speaker=speaker)
    return {
        "id": str(raw.get("id") or f"seg_{idx + 1:04d}"),
        "start": start,
        "end": end,
        "text": text,
        **({"speaker": speaker} if speaker else {}),
        **({"words": words} if words else {}),
    }


def _normalize_transcript_payload(payload: Any) -> dict[str, Any]:
    if isinstance(payload, list):
        raw_segments = payload
        language = None
    elif isinstance(payload, dict):
        raw_segments = payload.get("segments") or payload.get("captions") or payload.get("items") or []
        language = payload.get("language")
        if not raw_segments and payload.get("text"):
            raw_segments = [{"start": 0, "end": _float_or_none(payload.get("duration")) or 1.0, "text": payload.get("text")}]
    else:
        raw_segments = []
        language = None
    segments = [_normalize_segment(item, idx) for idx, item in enumerate(raw_segments)]
    segments = [item for item in segments if item]
    words = [word for segment in segments for word in (segment.get("words") or []) if isinstance(word, dict)]
    speakers: list[str] = []
    for item in [*segments, *words]:
        speaker = str(item.get("speaker") or "").strip() if isinstance(item, dict) else ""
        if speaker and speaker not in speakers:
            speakers.append(speaker)
    return {
        "language": language,
        "segments": segments,
        "words": words,
        "speakers": speakers,
        "text": " ".join(str(segment.get("text") or "").strip() for segment in segments).strip(),
    }


def _load_transcript_file(path: Path) -> dict[str, Any]:
    if path.suffix.lower() == ".json":
        return _normalize_transcript_payload(_load_json(path, {}))
    if path.suffix.lower() in {".srt", ".vtt"}:
        return _normalize_transcript_payload({"segments": _parse_srt_text(path.read_text(encoding="utf-8", errors="ignore"))})
    if path.suffix.lower() == ".txt":
        text = path.read_text(encoding="utf-8", errors="ignore").strip()
        return _normalize_transcript_payload({"text": text})
    return {"segments": [], "words": [], "text": ""}


def _best_transcript_output(out_dir: Path) -> dict[str, Any]:
    candidates = [path for path in out_dir.iterdir() if path.is_file() and path.name not in {"transcript.json", "transcript.normalized.json"}]
    preferred = [".json", ".srt", ".vtt", ".txt"]
    for suffix in preferred:
        for path in candidates:
            if path.suffix.lower() == suffix:
                normalized = _load_transcript_file(path)
                if normalized.get("segments") or normalized.get("text"):
                    normalized["sourceFile"] = str(path)
                    return normalized
    return {"segments": [], "words": [], "text": ""}


def _caption_entries_from_segments(
    segments: list[dict[str, Any]],
    *,
    max_chars: int = 42,
    max_duration: float = 3.0,
    min_duration: float = 0.45,
    style: dict[str, Any] | None = None,
) -> list[dict[str, Any]]:
    captions: list[dict[str, Any]] = []
    for segment in segments:
        words = [word for word in (segment.get("words") or []) if isinstance(word, dict) and str(word.get("text") or "").strip()]
        if words and all(_float_or_none(word.get("start")) is not None and _float_or_none(word.get("end")) is not None for word in words):
            bucket: list[dict[str, Any]] = []
            for word in words:
                candidate_text = " ".join(str(item.get("text") or "").strip() for item in [*bucket, word]).strip()
                start = _float_or_none((bucket[0] if bucket else word).get("start")) or 0.0
                end = _float_or_none(word.get("end")) or start + min_duration
                if bucket and (len(candidate_text) > max_chars or end - start > max_duration):
                    captions.append(_caption_from_words(bucket, style=style, min_duration=min_duration))
                    bucket = [word]
                else:
                    bucket.append(word)
            if bucket:
                captions.append(_caption_from_words(bucket, style=style, min_duration=min_duration))
            continue
        start = max(0.0, _float_or_none(segment.get("start")) or 0.0)
        end = max(start + min_duration, _float_or_none(segment.get("end")) or start + max_duration)
        text = str(segment.get("text") or "").strip()
        if not text:
            continue
        chunks = _split_caption_text(text, max_chars=max_chars)
        duration = max(min_duration, end - start)
        step = duration / max(len(chunks), 1)
        for idx, chunk in enumerate(chunks):
            c_start = start + step * idx
            c_end = start + step * (idx + 1)
            captions.append({
                "id": f"cap_{len(captions) + 1:04d}",
                "start": round(c_start, 3),
                "end": round(max(c_start + min_duration, c_end), 3),
                "text": chunk,
                **({"style": copy.deepcopy(style)} if style else {}),
                **({"speaker": segment.get("speaker")} if segment.get("speaker") else {}),
            })
    return captions


def _caption_from_words(words: list[dict[str, Any]], *, style: dict[str, Any] | None, min_duration: float) -> dict[str, Any]:
    start = _float_or_none(words[0].get("start")) or 0.0
    end = _float_or_none(words[-1].get("end")) or start + min_duration
    return {
        "id": "",
        "start": round(start, 3),
        "end": round(max(start + min_duration, end), 3),
        "text": " ".join(str(word.get("text") or "").strip() for word in words).strip(),
        "words": [copy.deepcopy(word) for word in words],
        **({"style": copy.deepcopy(style)} if style else {}),
        **({"speaker": words[0].get("speaker")} if words[0].get("speaker") else {}),
    }


def _split_caption_text(text: str, *, max_chars: int) -> list[str]:
    words = str(text or "").split()
    if not words:
        return []
    chunks: list[str] = []
    current = words[0]
    for word in words[1:]:
        candidate = f"{current} {word}"
        if len(candidate) <= max_chars:
            current = candidate
        else:
            chunks.append(current)
            current = word
    chunks.append(current)
    return chunks


async def _transcribe(repo: Any, run: dict[str, Any], args: dict[str, Any]) -> dict[str, Any]:
    dept_id = run["departmentId"]
    project_id = str(args.get("projectId") or args.get("project_id") or "").strip()
    project = _load_project(dept_id, project_id) if project_id else None
    source = await _resolve_source_path(repo, dept_id, args, project=project)
    out_dir = (_project_dir(dept_id, project_id) / "transcripts" if project else _workspace(dept_id) / "video_transcripts" / uid("transcript"))
    out_dir.mkdir(parents=True, exist_ok=True)
    engine = shutil.which("whisperx") or shutil.which("whisper")
    audio_path = out_dir / "audio.wav"
    extract = await asyncio.to_thread(
        _run,
        [_require_ffmpeg(), "-y", "-i", str(source), "-vn", "-ac", "1", "-ar", "16000", str(audio_path)],
        timeout=900,
    )
    if extract["returnCode"] != 0:
        raise ValueError(f"audio extraction failed: {(extract.get('stderr') or '')[-1500:]}")
    if not engine:
        transcript = {
            "id": uid("transcript"),
            "projectId": project_id or None,
            "source": str(source),
            "status": "dependency_missing",
            "message": "Install whisperx or whisper to enable transcript generation.",
            "audioPath": str(audio_path),
            "createdAt": now_ms(),
        }
        transcript_path = out_dir / "transcript.json"
        _write_json(transcript_path, transcript)
        return {"ok": False, "transcript": transcript, "path": str(transcript_path), "dependencyMissing": "whisperx|whisper"}
    if Path(engine).name == "whisperx":
        command = [engine, str(audio_path), "--output_dir", str(out_dir), "--output_format", "all"]
    else:
        command = [engine, str(audio_path), "--output_dir", str(out_dir), "--output_format", "all"]
    language = str(args.get("language") or "").strip()
    if language and language.lower() != "auto":
        command.extend(["--language", language])
    result = await asyncio.to_thread(_run, command, timeout=float(args.get("timeoutSeconds") or 3600))
    normalized = _best_transcript_output(out_dir) if result["returnCode"] == 0 else {"segments": [], "words": [], "text": ""}
    normalized["id"] = uid("transcript")
    normalized["projectId"] = project_id or None
    normalized["source"] = str(source)
    normalized["engine"] = Path(engine).name
    if language and not normalized.get("language"):
        normalized["language"] = language
    normalized["createdAt"] = now_ms()
    normalized_path = out_dir / "transcript.normalized.json"
    srt_path = out_dir / "transcript.srt"
    vtt_path = out_dir / "transcript.vtt"
    ass_path = out_dir / "transcript.ass"
    style = _caption_style_from_tool_args(args)
    caption_candidates = _caption_entries_from_segments(
        normalized.get("segments") or [],
        max_chars=max(12, min(int(args.get("maxCaptionChars") or args.get("max_caption_chars") or 42), 90)),
        max_duration=max(0.6, min(float(args.get("maxCaptionDuration") or args.get("max_caption_duration") or 3.0), 8.0)),
        style=style,
    )
    for idx, caption in enumerate(caption_candidates, start=1):
        caption["id"] = caption.get("id") or f"cap_{idx:04d}"
    _write_json(normalized_path, normalized)
    if caption_candidates:
        _write_srt(caption_candidates, srt_path)
        _write_vtt(caption_candidates, vtt_path)
        _write_ass(caption_candidates, ass_path, style)
    artifacts: list[dict[str, Any]] = []
    if result["returnCode"] == 0:
        for file_path, tag in ((normalized_path, "transcript_json"), (srt_path, "subtitle_srt"), (vtt_path, "subtitle_vtt"), (ass_path, "subtitle_ass")):
            if file_path.is_file():
                artifacts.append(await _persist_file_artifact(
                    repo,
                    path=file_path,
                    owner_dept=dept_id,
                    created_by=str(run.get("requestedBy") or dept_id),
                    name=file_path.name,
                    tags=["video_transcript", tag],
                    project_id=project_id or None,
                    note=f"created {tag} from video transcript",
                ))
    transcript = {
        "id": normalized["id"],
        "projectId": project_id or None,
        "source": str(source),
        "engine": Path(engine).name,
        "status": "done" if result["returnCode"] == 0 else "failed",
        "returnCode": result["returnCode"],
        "stdout": result.get("stdout", "")[-4000:],
        "stderr": result.get("stderr", "")[-4000:],
        "files": [str(path) for path in out_dir.iterdir() if path.is_file()],
        "normalizedPath": str(normalized_path),
        "srtPath": str(srt_path) if srt_path.is_file() else None,
        "vttPath": str(vtt_path) if vtt_path.is_file() else None,
        "assPath": str(ass_path) if ass_path.is_file() else None,
        "segmentCount": len(normalized.get("segments") or []),
        "wordCount": len(normalized.get("words") or []),
        "speakers": normalized.get("speakers") or [],
        "captionStyle": style,
        "captionStylePreset": (style or {}).get("preset"),
        "language": normalized.get("language"),
        "createdAt": now_ms(),
    }
    transcript_path = out_dir / "transcript.json"
    _write_json(transcript_path, transcript)
    if project:
        project.setdefault("transcripts", []).append(transcript)
        _save_project(project)
    return {
        "ok": result["returnCode"] == 0,
        "transcript": transcript,
        "normalized": normalized,
        "captions": caption_candidates,
        "path": str(transcript_path),
        "artifacts": artifacts,
    }


def _transcript_payload_from_args(project: dict[str, Any], args: dict[str, Any]) -> dict[str, Any]:
    if isinstance(args.get("segments"), list):
        return _normalize_transcript_payload({"segments": args["segments"], "language": args.get("language")})
    if isinstance(args.get("words"), list):
        words = [_normalize_word(item) for item in args["words"] if isinstance(item, dict)]
        words = [item for item in words if item]
        return _normalize_transcript_payload({"segments": [{"words": words}]})
    if args.get("text"):
        duration = _float_or_none(args.get("duration")) or 3.0
        return _normalize_transcript_payload({"text": args.get("text"), "duration": duration, "language": args.get("language")})
    transcript_path = str(args.get("transcriptPath") or args.get("transcript_path") or "").strip()
    if transcript_path:
        path = Path(transcript_path).expanduser()
        if not path.is_absolute():
            path = _project_dir(str(project["ownerDept"]), str(project["id"])) / path
        if not path.is_file():
            raise ValueError(f"transcript file not found: {transcript_path}")
        return _load_transcript_file(path.resolve())
    transcript_id = str(args.get("transcriptId") or args.get("transcript_id") or "").strip()
    if transcript_id:
        for item in project.get("transcripts") or []:
            if isinstance(item, dict) and str(item.get("id")) == transcript_id:
                for key in ("normalizedPath", "srtPath", "vttPath", "path"):
                    raw_path = str(item.get(key) or "").strip()
                    if raw_path and Path(raw_path).is_file():
                        return _load_transcript_file(Path(raw_path))
                files = [Path(str(path)) for path in item.get("files") or [] if Path(str(path)).is_file()]
                for suffix in (".json", ".srt", ".vtt", ".txt"):
                    for path in files:
                        if path.suffix.lower() == suffix:
                            return _load_transcript_file(path)
        raise ValueError(f"transcript not found: {transcript_id}")
    raise ValueError("segments, words, text, transcriptPath, or transcriptId is required")


async def _generate_captions(repo: Any, run: dict[str, Any], args: dict[str, Any]) -> dict[str, Any]:
    dept_id = run["departmentId"]
    project_id = str(args.get("projectId") or args.get("project_id") or "").strip()
    if not project_id:
        raise ValueError("projectId is required")
    project = _load_project(dept_id, project_id)
    payload = _transcript_payload_from_args(project, args)
    canvas_arg = args.get("canvas") if isinstance(args.get("canvas"), dict) else {}
    if isinstance(args.get("timeline"), dict) and isinstance(args["timeline"].get("canvas"), dict):
        canvas_arg = args["timeline"]["canvas"]
    style = _caption_style_from_tool_args(args, canvas=canvas_arg)
    captions = _caption_entries_from_segments(
        payload.get("segments") or [],
        max_chars=max(12, min(int(args.get("maxChars") or args.get("maxCaptionChars") or 42), 90)),
        max_duration=max(0.6, min(float(args.get("maxDuration") or args.get("maxCaptionDuration") or 3.0), 8.0)),
        min_duration=max(0.2, min(float(args.get("minDuration") or args.get("minCaptionDuration") or 0.45), 3.0)),
        style=style,
    )
    for idx, caption in enumerate(captions, start=1):
        caption["id"] = str(caption.get("id") or f"cap_{idx:04d}")
    if not captions:
        raise ValueError("no captions could be generated from transcript input")
    track_id = uid("captrack")
    out_dir = _project_dir(dept_id, project_id) / "transcripts" / "caption_tracks" / track_id
    out_dir.mkdir(parents=True, exist_ok=True)
    track = {
        "id": track_id,
        "projectId": project_id,
        "source": {
            "transcriptId": args.get("transcriptId") or args.get("transcript_id"),
            "transcriptPath": args.get("transcriptPath") or args.get("transcript_path"),
            "input": "segments" if isinstance(args.get("segments"), list) else "words" if isinstance(args.get("words"), list) else "text" if args.get("text") else "transcript",
        },
        "captionStyle": style,
        "captionStylePreset": (style or {}).get("preset"),
        "transcript": {
            "language": payload.get("language"),
            "segmentCount": len(payload.get("segments") or []),
            "wordCount": len(payload.get("words") or []),
            "speakers": payload.get("speakers") or [],
        },
        "captions": captions,
        "createdAt": now_ms(),
    }
    json_path = out_dir / "captions.json"
    srt_path = out_dir / "captions.srt"
    vtt_path = out_dir / "captions.vtt"
    ass_path = out_dir / "captions.ass"
    _write_json(json_path, track)
    _write_srt(captions, srt_path)
    _write_vtt(captions, vtt_path)
    _write_ass(captions, ass_path, style)
    artifacts = []
    for file_path, tag in ((json_path, "caption_track"), (srt_path, "subtitle_srt"), (vtt_path, "subtitle_vtt"), (ass_path, "subtitle_ass")):
        artifacts.append(await _persist_file_artifact(
            repo,
            path=file_path,
            owner_dept=dept_id,
            created_by=str(run.get("requestedBy") or dept_id),
            name=file_path.name,
            tags=["video_caption", tag],
            project_id=project_id,
            note=f"created {tag} for caption track {track_id}",
        ))
    timeline = None
    timeline_path = None
    if bool(args.get("writeTimeline") or args.get("write_timeline") or args.get("timelineId") or args.get("timeline")):
        base_id = str(args.get("timelineId") or args.get("timeline_id") or args.get("baseTimelineId") or "").strip()
        base_version = str(args.get("baseVersion") or args.get("version") or "").strip() or None
        if base_id:
            base = _load_timeline_spec(project, base_id, base_version)
        elif isinstance(args.get("timeline"), dict):
            base = _normalize_timeline_spec(project, args["timeline"], args)
        else:
            primary = _primary_video_asset(project)
            base = _normalize_timeline_spec(project, {
                "canvas": {"width": 1080, "height": 1920, "fps": 30},
                "clips": [{"id": "clip_main", "assetId": (primary or {}).get("id"), "in": 0}],
            }, args)
        timeline = copy.deepcopy(base)
        timeline["parent"] = {"timelineId": base.get("id"), "version": base.get("version")}
        timeline["id"] = _timeline_id(args.get("newTimelineId") or args.get("new_timeline_id") or base.get("id"))
        timeline["version"] = int(base.get("version") or 1) + 1
        timeline["captions"] = captions
        if style:
            timeline["captionStyle"] = style
        timeline_path = _save_timeline_spec(project, timeline, source="generate_captions")
    project.setdefault("captionTracks", []).append({
        "id": track_id,
        "path": str(json_path),
        "srtPath": str(srt_path),
        "vttPath": str(vtt_path),
        "assPath": str(ass_path),
        "captionCount": len(captions),
        "wordCount": len(payload.get("words") or []),
        "speakers": payload.get("speakers") or [],
        "captionStylePreset": (style or {}).get("preset"),
        "createdAt": track["createdAt"],
    })
    _append_project_audit(
        project,
        "caption_track.create",
        run=run,
        entity_type="video_caption_track",
        entity_id=track_id,
        summary=f"Created caption track {track_id} with {len(captions)} captions",
        refs={"captionCount": len(captions), "wordCount": len(payload.get("words") or []), "speakers": payload.get("speakers") or [], "timelineId": (timeline or {}).get("id"), "timelineVersion": (timeline or {}).get("version")},
        paths={"json": str(json_path), "srt": str(srt_path), "vtt": str(vtt_path), "ass": str(ass_path), **({"timeline": str(timeline_path)} if timeline_path else {})},
        checksum=_sha256_file(json_path),
    )
    _save_project(project)
    await repo.put_entity("video_project", project, dept=dept_id, project=project_id, status="active", ts=project["updatedAt"])
    return {
        "ok": True,
        "captionTrack": track,
        "captions": captions,
        "paths": {"json": str(json_path), "srt": str(srt_path), "vtt": str(vtt_path), "ass": str(ass_path), "timeline": str(timeline_path) if timeline_path else None},
        "timeline": timeline,
        "artifacts": artifacts,
        "context": _media_context(project, timeline=timeline),
    }


def _latest_render(project: dict[str, Any]) -> dict[str, Any] | None:
    renders = [item for item in project.get("renders") or [] if isinstance(item, dict)]
    if not renders:
        return None
    return sorted(renders, key=lambda item: int(item.get("createdAt") or 0))[-1]


def _render_from_args(project: dict[str, Any], args: dict[str, Any]) -> dict[str, Any] | None:
    render_id = str(args.get("renderId") or args.get("render_id") or "").strip()
    artifact_id = str(args.get("artifactId") or args.get("artifact_id") or "").strip()
    if render_id:
        for item in project.get("renders") or []:
            if isinstance(item, dict) and str(item.get("id")) == render_id:
                return item
        return None
    if artifact_id:
        for item in project.get("renders") or []:
            if isinstance(item, dict) and str(item.get("artifactId")) == artifact_id:
                return item
    return _latest_render(project)


async def _request_review(repo: Any, run: dict[str, Any], args: dict[str, Any]) -> dict[str, Any]:
    dept_id = run["departmentId"]
    project_id = str(args.get("projectId") or args.get("project_id") or "").strip()
    if not project_id:
        raise ValueError("projectId is required")
    project = _load_project(dept_id, project_id)
    render = _render_from_args(project, args)
    artifact_id = str(args.get("artifactId") or args.get("artifact_id") or (render or {}).get("artifactId") or "").strip()
    if not artifact_id:
        raise ValueError("artifactId or a rendered video version is required")
    artifact = await repo.get_entity("artifact", artifact_id)
    if not artifact:
        raise ValueError(f"artifact not found: {artifact_id}")
    now = now_ms()
    review_id = uid("vrev")
    approve_run_id = uid("tool")
    final = bool(args.get("final") or args.get("finalize") or str(args.get("kind") or "").lower() == "final")
    approve_run = {
        "id": approve_run_id,
        "tool": "video.approve_render",
        "departmentId": dept_id,
        "taskId": args.get("taskId") or args.get("task_id"),
        "requestedBy": run.get("requestedBy") or dept_id,
        "args": {
            "projectId": project_id,
            "reviewId": review_id,
            "renderId": (render or {}).get("id"),
            "artifactId": artifact_id,
            "final": final,
        },
        "status": "pending_approval",
        "createdAt": now,
        "executor": "host",
        "riskClass": "local_write",
        "policyDecision": "approval_required",
    }
    await repo.put_entity("tool_run", approve_run, dept=dept_id, project=project_id, status="pending_approval", ts=now)
    approval = {
        "id": uid("apr"),
        "ts": now,
        "kind": "publish",
        "title": str(args.get("title") or ("Approve final video render" if final else "Review video preview")),
        "detail": str(args.get("detail") or args.get("notes") or f"Review video artifact {artifact_id} for project {project_id}"),
        "departmentId": dept_id,
        "status": "pending",
        "action": {
            "action": "run_tool",
            "departmentId": dept_id,
            "toolRunId": approve_run_id,
            "projectId": project_id,
            "artifactId": artifact_id,
            "requestedBy": run.get("requestedBy") or dept_id,
        },
    }
    review = {
        "id": review_id,
        "projectId": project_id,
        "renderId": (render or {}).get("id"),
        "artifactId": artifact_id,
        "approvalId": approval["id"],
        "approveToolRunId": approve_run_id,
        "status": "pending",
        "kind": "final" if final else "preview",
        "downloadUrl": f"/api/artifacts/{artifact_id}/download",
        "previewUrl": f"/api/artifacts/{artifact_id}/preview",
        "createdAt": now,
        "createdBy": run.get("requestedBy") or dept_id,
        "notes": str(args.get("notes") or ""),
    }
    artifact.update({
        "reviewStatus": "pending_video_review",
        "videoReviewId": review_id,
        "approvalId": approval["id"],
        "updatedAt": now,
        "updatedBy": run.get("requestedBy") or dept_id,
    })
    artifact.update(_video_artifact_context_fields(project, artifact_id, render=render))
    await repo.put_entity("artifact", artifact, dept=artifact.get("ownerDept") or dept_id, project=project_id, status=artifact.get("status"), ts=now)
    await repo.put_entity("video_review", review, dept=dept_id, project=project_id, status="pending", ts=now)
    await repo.add_approval(approval)
    _append_project_audit(
        project,
        "review.request",
        run=run,
        entity_type="video_review",
        entity_id=review_id,
        summary=f"Requested {'final' if final else 'preview'} review for artifact {artifact_id}",
        refs={"renderId": (render or {}).get("id"), "artifactId": artifact_id, "approvalId": approval["id"], "approveToolRunId": approve_run_id, "final": final},
    )
    _save_project(project)
    await repo.put_entity("video_project", project, dept=dept_id, project=project_id, status="active", ts=project["updatedAt"])
    await repo.add_activity({
        "id": uid("ev"),
        "ts": now,
        "type": "approval",
        "departmentId": dept_id,
        "text": f"Video review requested for artifact {artifact_id}",
        "severity": "warn",
        "approvalId": approval["id"],
    })
    return {"ok": True, "review": review, "approval": approval, "approveToolRun": approve_run, "artifact": artifact}


async def _approve_render(repo: Any, run: dict[str, Any], args: dict[str, Any]) -> dict[str, Any]:
    dept_id = run["departmentId"]
    project_id = str(args.get("projectId") or args.get("project_id") or "").strip()
    if not project_id:
        raise ValueError("projectId is required")
    project = _load_project(dept_id, project_id)
    review_id = str(args.get("reviewId") or args.get("review_id") or "").strip()
    review = await repo.get_entity("video_review", review_id) if review_id else None
    render = _render_from_args(project, args)
    artifact_id = str(args.get("artifactId") or args.get("artifact_id") or (review or {}).get("artifactId") or (render or {}).get("artifactId") or "").strip()
    if not artifact_id:
        raise ValueError("artifactId or renderId is required")
    artifact = await repo.get_entity("artifact", artifact_id)
    if not artifact:
        raise ValueError(f"artifact not found: {artifact_id}")
    now = now_ms()
    final = bool(args.get("final") or args.get("finalize") or (review or {}).get("kind") == "final")
    approved_by = str(args.get("approvedBy") or args.get("approved_by") or run.get("requestedBy") or dept_id)
    artifact.update({
        "status": "approved",
        "reviewStatus": "approved_video_review",
        "approvalTier": "user" if args.get("userApproved", True) is not False else "department",
        "approvedBy": approved_by,
        "approvedAt": now,
        "updatedAt": now,
        "updatedBy": approved_by,
        "tags": list(dict.fromkeys([*artifact.get("tags", []), "video-approved", *(["video-final"] if final else [])])),
    })
    artifact.update(_video_artifact_context_fields(project, artifact_id, render=render))
    await repo.put_entity("artifact", artifact, dept=artifact.get("ownerDept") or dept_id, project=project_id, status="approved", ts=now)
    target_render_id = str((render or {}).get("id") or "")
    updated_render: dict[str, Any] | None = None
    for item in project.get("renders") or []:
        if isinstance(item, dict) and (str(item.get("artifactId")) == artifact_id or (target_render_id and str(item.get("id")) == target_render_id)):
            item["reviewStatus"] = "approved"
            item["approvedAt"] = now
            item["approvedBy"] = approved_by
            if final:
                item["kind"] = "final"
                project["finalRenderId"] = item.get("id")
                project["finalArtifactId"] = artifact_id
            updated_render = copy.deepcopy(item)
    if review:
        review.update({"status": "approved", "approvedAt": now, "approvedBy": approved_by})
        await repo.put_entity("video_review", review, dept=dept_id, project=project_id, status="approved", ts=now)
    _append_project_audit(
        project,
        "render.approve",
        run=run,
        entity_type="video_render",
        entity_id=str((updated_render or render or {}).get("id") or artifact_id),
        summary=f"Approved video render artifact {artifact_id}",
        refs={"renderId": (updated_render or render or {}).get("id"), "artifactId": artifact_id, "reviewId": (review or {}).get("id") or review_id, "final": final, "approvedBy": approved_by},
    )
    _save_project(project)
    await repo.put_entity("video_project", project, dept=dept_id, project=project_id, status="active", ts=project["updatedAt"])
    if updated_render:
        await repo.put_entity("video_render", updated_render, dept=dept_id, project=project_id, status="done", ts=now)
    await repo.add_activity({
        "id": uid("ev"),
        "ts": now,
        "type": "task_done" if final else "approval",
        "departmentId": dept_id,
        "text": f"Video render approved: {artifact_id}",
        "severity": "good",
    })
    return {
        "ok": True,
        "artifact": artifact,
        "review": review,
        "render": updated_render or render,
        "downloadUrl": f"/api/artifacts/{artifact_id}/download",
        "previewUrl": f"/api/artifacts/{artifact_id}/preview",
        "final": final,
    }


async def _quality_check(repo: Any, run: dict[str, Any], args: dict[str, Any]) -> dict[str, Any]:
    dept_id = run["departmentId"]
    project_id = str(args.get("projectId") or args.get("project_id") or "").strip()
    project = _load_project(dept_id, project_id) if project_id else None
    source = await _resolve_source_path(repo, dept_id, args, project=project)
    probe = _ffprobe(source)
    decode = _run([_require_ffmpeg(), "-v", "error", "-i", str(source), "-f", "null", "-"], timeout=float(args.get("timeoutSeconds") or 300))
    issues: list[str] = []
    if not probe.get("video"):
        issues.append("no video stream detected")
    if decode["returnCode"] != 0:
        issues.append("ffmpeg decode validation failed")
    loudness = _audio_loudness(source, timeout=float(args.get("timeoutSeconds") or 300)) if probe.get("audio") else {"available": False, "reason": "no audio stream"}
    if isinstance(loudness, dict) and loudness.get("available"):
        max_volume = _float_or_none(loudness.get("maxVolumeDb"))
        mean_volume = _float_or_none(loudness.get("meanVolumeDb"))
        if max_volume is not None and max_volume > -0.1:
            issues.append("audio peak is near clipping")
        if mean_volume is not None and mean_volume < -35:
            issues.append("audio mean volume is very low")
    return {
        "ok": not issues,
        "source": str(source),
        "metadata": probe,
        "decode": {"returnCode": decode["returnCode"], "stderr": decode.get("stderr", "")[-4000:]},
        "audioLoudness": loudness,
        "issues": issues,
    }


def _audio_loudness(path: Path, *, timeout: float) -> dict[str, Any]:
    ffmpeg = _require_ffmpeg()
    if not _ffmpeg_has_filter(ffmpeg, "volumedetect"):
        return {"available": False, "reason": "ffmpeg volumedetect filter is unavailable"}
    result = _run([ffmpeg, "-i", str(path), "-vn", "-af", "volumedetect", "-f", "null", "-"], timeout=timeout)
    stderr = str(result.get("stderr") or "")
    mean_match = re.search(r"mean_volume:\s*(-?\d+(?:\.\d+)?)\s*dB", stderr)
    max_match = re.search(r"max_volume:\s*(-?\d+(?:\.\d+)?)\s*dB", stderr)
    return {
        "available": result["returnCode"] == 0 and bool(mean_match or max_match),
        "returnCode": result["returnCode"],
        "meanVolumeDb": float(mean_match.group(1)) if mean_match else None,
        "maxVolumeDb": float(max_match.group(1)) if max_match else None,
        "stderrTail": stderr[-1200:],
    }


def _list_templates(args: dict[str, Any]) -> dict[str, Any]:
    canvas = args.get("canvas") if isinstance(args.get("canvas"), dict) else None
    templates = [
        *[
            {
                "id": item["id"],
                "type": "brandStyle",
                "name": item.get("name"),
                "templateId": item["id"],
                "fonts": item.get("fonts"),
                "colors": item.get("colors"),
                "safeArea": item.get("safeArea"),
                "hookTextStyle": item.get("hookTextStyle"),
                "captionStyle": item.get("captionStyle"),
                "lowerThirdStyle": item.get("lowerThirdStyle"),
                "motion": item.get("motion"),
            }
            for item in _timeline_style_template_catalog(canvas=canvas)
        ],
        {
            "id": "shorts.bold",
            "type": "captionStyle",
            "name": "Bold shorts captions",
            "stylePreset": "shorts.bold",
            "style": _caption_style_preset("shorts.bold"),
        },
        {
            "id": "karaoke.highlight",
            "type": "captionStyle",
            "name": "Karaoke highlighted captions",
            "stylePreset": "karaoke.highlight",
            "style": _caption_style_preset("karaoke.highlight"),
        },
        {
            "id": "thai.bold",
            "type": "captionStyle",
            "name": "Thai bold captions",
            "stylePreset": "thai.bold",
            "style": _caption_style_preset("thai.bold"),
        },
        {
            "id": "minimal.clean",
            "type": "captionStyle",
            "name": "Minimal clean captions",
            "stylePreset": "minimal.clean",
            "style": _caption_style_preset("minimal.clean"),
        },
        {
            "id": "hook.top_center",
            "type": "text",
            "name": "Top hook text",
            "layer": {
                "start": 0,
                "end": 3.5,
                "size": 64,
                "color": "#ffffff",
                "position": {"x": "50%", "y": "16%", "anchor": "center"},
                "stroke": {"color": "#111111", "width": 6},
                "maxWidth": "86%",
            },
        },
        {
            "id": "lower_third.clean",
            "type": "captionStyle",
            "name": "Clean lower-third",
            "stylePreset": "lower_third.clean",
            "style": _caption_style_preset("lower_third.clean"),
        },
        {
            "id": "text.lower_third.clean",
            "type": "text",
            "name": "Clean lower-third text",
            "layer": {
                "size": 42,
                "color": "#ffffff",
                "position": {"x": "8%", "y": "78%", "anchor": "left"},
                "box": {"color": "#111111", "opacity": 0.62, "padding": 18, "radius": 8},
                "maxWidth": "72%",
            },
        },
        {
            "id": "safe_area.shorts_9_16",
            "type": "canvasGuide",
            "name": "Shorts/Reels safe area",
            "canvas": {"width": 1080, "height": 1920, "fps": 30},
            "safeArea": {"top": 160, "bottom": 260, "left": 72, "right": 72},
        },
    ]
    query = str(args.get("query") or "").strip().lower()
    if query:
        templates = [
            item for item in templates
            if query in item["id"].lower() or query in str(item.get("name") or "").lower() or query in item["type"].lower()
        ]
    return {"ok": True, "templates": templates, "count": len(templates)}


def _timeline_ref(project: dict[str, Any], timeline: dict[str, Any] | None) -> dict[str, Any] | None:
    if not timeline:
        return None
    timeline_id = str(timeline.get("id") or "")
    version = int(timeline.get("version") or 0)
    path = None
    uri = None
    storage = None
    object_store = None
    for item in project.get("timelines") or []:
        if item.get("id") == timeline_id and int(item.get("version") or 0) == version:
            path = item.get("path")
            uri = item.get("uri")
            storage = item.get("storage")
            object_store = item.get("objectStore") if isinstance(item.get("objectStore"), dict) else None
            break
    return {
        "timelineId": timeline_id,
        "version": version,
        "handle": f"atrium://video/projects/{project['id']}/timelines/{timeline_id}/versions/{version}",
        "path": path,
        "uri": uri,
        "storage": storage,
        "objectStore": object_store,
        "templateId": timeline.get("templateId") or ((timeline.get("styleGuide") or {}).get("id") if isinstance(timeline.get("styleGuide"), dict) else None),
        "styleGuide": {
            "id": (timeline.get("styleGuide") or {}).get("id"),
            "name": (timeline.get("styleGuide") or {}).get("name"),
            "type": (timeline.get("styleGuide") or {}).get("type"),
        } if isinstance(timeline.get("styleGuide"), dict) else None,
    }


def _latest_timeline_meta(project: dict[str, Any]) -> dict[str, Any] | None:
    items = [item for item in project.get("timelines") or [] if isinstance(item, dict)]
    if not items:
        return None
    return sorted(items, key=lambda row: (str(row.get("id") or ""), int(row.get("version") or 0), int(row.get("createdAt") or 0)))[-1]


def _asset_summary(asset: dict[str, Any], *, include_paths: bool = False) -> dict[str, Any]:
    metadata = asset.get("metadata") if isinstance(asset.get("metadata"), dict) else {}
    video = metadata.get("video") if isinstance(metadata.get("video"), dict) else None
    image = metadata.get("image") if isinstance(metadata.get("image"), dict) else None
    subtitle = metadata.get("subtitle") if isinstance(metadata.get("subtitle"), dict) else None
    font = metadata.get("font") if isinstance(metadata.get("font"), dict) else None
    fmt = metadata.get("format") if isinstance(metadata.get("format"), dict) else None
    audio_streams = metadata.get("audio") if isinstance(metadata.get("audio"), list) else []
    first_audio = next((item for item in audio_streams if isinstance(item, dict)), None)
    handle = asset.get("handle") or f"atrium://video/assets/{asset.get('id')}"
    out = {
        "assetId": asset.get("id"),
        "type": asset.get("type"),
        "role": asset.get("role"),
        "name": asset.get("name"),
        "mime": asset.get("mime"),
        "sizeBytes": asset.get("sizeBytes"),
        "sha256": asset.get("sha256"),
        "uri": asset.get("uri"),
        "storage": asset.get("storage"),
        "metadataProfile": asset.get("metadataProfile"),
        "handle": handle,
        "manifestHandle": f"{handle}/manifest",
        "manifestUri": asset.get("manifestUri"),
        "media": {
            "duration": (fmt or {}).get("duration") or (video or {}).get("duration") or (first_audio or {}).get("duration") or (subtitle or {}).get("duration"),
            "width": (video or {}).get("width") or (image or {}).get("width"),
            "height": (video or {}).get("height") or (image or {}).get("height"),
            "fps": (video or {}).get("fps"),
            "codec": (video or {}).get("codec") or (first_audio or {}).get("codec"),
            "formatName": (fmt or {}).get("formatName") or (image or {}).get("format"),
            "channels": (first_audio or {}).get("channels"),
            "sampleRate": (first_audio or {}).get("sampleRate"),
            "audioStreams": len(audio_streams),
            "segmentCount": (subtitle or {}).get("segmentCount"),
            "wordCount": (subtitle or {}).get("wordCount"),
            "fontFamily": (font or {}).get("family") or (font or {}).get("familyHint"),
        },
    }
    if include_paths:
        out["path"] = asset.get("path")
        out["sourcePath"] = asset.get("sourcePath")
        out["manifestPath"] = asset.get("manifestPath")
        out["objectStore"] = asset.get("objectStore")
        out["manifestObjectStore"] = asset.get("manifestObjectStore")
    if asset.get("metadataError"):
        out["metadataError"] = asset.get("metadataError")
    return out


def _build_context_packet(
    project: dict[str, Any],
    *,
    timeline: dict[str, Any] | None = None,
    include_paths: bool = False,
) -> dict[str, Any]:
    primary = _primary_video_asset(project)
    timeline_meta = _latest_timeline_meta(project)
    if timeline is None and timeline_meta:
        try:
            timeline = _load_json(Path(str(timeline_meta.get("path") or "")), {})
        except Exception:
            timeline = None
    latest_render = None
    renders = [item for item in project.get("renders") or [] if isinstance(item, dict)]
    if renders:
        latest_render = sorted(renders, key=lambda row: int(row.get("createdAt") or 0))[-1]
    motion_packages = [item for item in project.get("motionPackages") or [] if isinstance(item, dict)]
    latest_motion = sorted(motion_packages, key=lambda row: int(row.get("createdAt") or 0))[-1] if motion_packages else None
    packet = {
        "type": "video_context_packet",
        "handle": f"atrium://video/projects/{project['id']}",
        "project": {
            "projectId": project["id"],
            "name": project.get("name"),
            "ownerDept": project.get("ownerDept"),
            "workspace": project.get("workspace") if include_paths else None,
            "version": project.get("version"),
            "createdAt": project.get("createdAt"),
            "updatedAt": project.get("updatedAt"),
        },
        "audit": {
            "eventCount": (project.get("audit") or {}).get("eventCount") if isinstance(project.get("audit"), dict) else 0,
            "lastEventId": (project.get("audit") or {}).get("lastEventId") if isinstance(project.get("audit"), dict) else None,
            "updatedAt": (project.get("audit") or {}).get("updatedAt") if isinstance(project.get("audit"), dict) else None,
            **({"path": (project.get("audit") or {}).get("path")} if include_paths and isinstance(project.get("audit"), dict) else {}),
        },
        "recentAudit": [
            item for item in (project.get("auditTrail") or [])[-10:]
            if isinstance(item, dict)
        ],
        "primaryAssetId": (primary or {}).get("id"),
        "assets": [_asset_summary(asset, include_paths=include_paths) for asset in project.get("assets") or [] if isinstance(asset, dict)],
        "assetManifests": [
            {
                "assetId": item.get("assetId"),
                "type": item.get("type"),
                "metadataProfile": item.get("metadataProfile"),
                "sha256": item.get("sha256"),
                "uri": item.get("uri"),
                "storage": item.get("storage"),
                "manifestUri": item.get("manifestUri"),
                "handle": f"atrium://video/projects/{project['id']}/assets/{item.get('assetId')}/manifest",
                **({"manifestPath": item.get("manifestPath")} if include_paths else {}),
            }
            for item in project.get("assetManifests") or []
            if isinstance(item, dict)
        ],
        "latestTimeline": _timeline_ref(project, timeline),
        "latestTimelineSpec": timeline,
        "renderVersions": [
            {
                "renderId": item.get("id"),
                "timelineId": item.get("timelineId"),
                "timelineVersion": item.get("timelineVersion"),
                "kind": item.get("kind"),
                "artifactId": item.get("artifactId"),
                "uri": item.get("artifactUri"),
                "storage": item.get("artifactStorage"),
                "downloadUrl": item.get("downloadUrl") or (f"/api/artifacts/{item.get('artifactId')}/download" if item.get("artifactId") else None),
                "previewUrl": item.get("previewUrl") or (f"/api/artifacts/{item.get('artifactId')}/preview" if item.get("artifactId") else None),
                "createdAt": item.get("createdAt"),
                "handle": f"atrium://video/projects/{project['id']}/renders/{item.get('id')}",
                **({"path": item.get("path"), "specPath": item.get("specPath")} if include_paths else {}),
            }
            for item in renders[-20:]
        ],
        "latestRender": None if not latest_render else {
            "renderId": latest_render.get("id"),
            "artifactId": latest_render.get("artifactId"),
            "timelineId": latest_render.get("timelineId"),
            "timelineVersion": latest_render.get("timelineVersion"),
            "kind": latest_render.get("kind"),
            "uri": latest_render.get("artifactUri"),
            "storage": latest_render.get("artifactStorage"),
            "downloadUrl": latest_render.get("downloadUrl") or (f"/api/artifacts/{latest_render.get('artifactId')}/download" if latest_render.get("artifactId") else None),
            "previewUrl": latest_render.get("previewUrl") or (f"/api/artifacts/{latest_render.get('artifactId')}/preview" if latest_render.get("artifactId") else None),
            "handle": f"atrium://video/projects/{project['id']}/renders/{latest_render.get('id')}",
        },
        "motionPackages": [
            {
                "motionId": item.get("id"),
                "renderer": item.get("renderer"),
                "renderers": item.get("renderers"),
                "timelineId": item.get("timelineId"),
                "timelineVersion": item.get("timelineVersion"),
                "artifactId": item.get("artifactId"),
                "uri": item.get("artifactUri"),
                "storage": item.get("artifactStorage"),
                "downloadUrl": item.get("downloadUrl"),
                "previewUrl": item.get("previewUrl"),
                "templateId": item.get("templateId"),
                "commands": item.get("commands"),
                "packages": [
                    {
                        "renderer": package.get("renderer"),
                        "commands": package.get("commands"),
                        **({"packageDir": package.get("packageDir"), "entryPoint": package.get("entryPoint")} if include_paths else {}),
                    }
                    for package in (item.get("packages") or [])
                    if isinstance(package, dict)
                ],
                "createdAt": item.get("createdAt"),
                "handle": f"atrium://video/projects/{project['id']}/motion/{item.get('id')}",
                **({"packageDir": item.get("packageDir"), "entryPoint": item.get("entryPoint")} if include_paths else {}),
            }
            for item in motion_packages[-20:]
        ],
        "latestMotionPackage": None if not latest_motion else {
            "motionId": latest_motion.get("id"),
            "renderer": latest_motion.get("renderer"),
            "renderers": latest_motion.get("renderers"),
            "artifactId": latest_motion.get("artifactId"),
            "timelineId": latest_motion.get("timelineId"),
            "timelineVersion": latest_motion.get("timelineVersion"),
            "downloadUrl": latest_motion.get("downloadUrl"),
            "handle": f"atrium://video/projects/{project['id']}/motion/{latest_motion.get('id')}",
        },
        "transcripts": [
            {
                "transcriptId": item.get("id"),
                "status": item.get("status"),
                "engine": item.get("engine"),
                "createdAt": item.get("createdAt"),
                "files": item.get("files") if include_paths else None,
            }
            for item in project.get("transcripts") or []
            if isinstance(item, dict)
        ],
        "toolReferences": {
            "inspect": {"tool": "video.inspect", "args": {"projectId": project["id"]}},
            "sampleFrames": {"tool": "video.sample_frames", "args": {"projectId": project["id"], "count": 6}},
            "storyboard": {"tool": "video.storyboard", "args": {"projectId": project["id"], "count": 8}},
            "suggestEdits": {"tool": "video.suggest_edits", "args": {"projectId": project["id"], "timelineId": (timeline or {}).get("id"), "baseVersion": (timeline or {}).get("version"), "aspectRatio": "9:16"}},
            "renderPreview": {"tool": "video.render_edit", "args": {"projectId": project["id"], "kind": "preview", "asyncMode": True}},
            "renderMotionPackage": {"tool": "video.render_motion", "args": {"projectId": project["id"], "timelineId": (timeline or {}).get("id"), "version": (timeline or {}).get("version"), "renderer": "remotion"}},
            "renderRevideoPackage": {"tool": "video.render_motion", "args": {"projectId": project["id"], "timelineId": (timeline or {}).get("id"), "version": (timeline or {}).get("version"), "renderer": "revideo"}},
            "renderHyperFramesPackage": {"tool": "video.render_motion", "args": {"projectId": project["id"], "timelineId": (timeline or {}).get("id"), "version": (timeline or {}).get("version"), "renderer": "hyperframes"}},
            "patchTimeline": {"tool": "video.patch_timeline", "args": {"projectId": project["id"], "timelineId": (timeline or {}).get("id"), "baseVersion": (timeline or {}).get("version")}},
        },
    }
    if not include_paths:
        packet["project"].pop("workspace", None)
    return packet


async def _context_packet(repo: Any, run: dict[str, Any], args: dict[str, Any]) -> dict[str, Any]:
    dept_id = run["departmentId"]
    project_id = str(args.get("projectId") or args.get("project_id") or "").strip()
    if not project_id:
        raise ValueError("projectId is required")
    project = _load_project(dept_id, project_id)
    timeline = None
    timeline_id = str(args.get("timelineId") or args.get("timeline_id") or "").strip()
    if timeline_id:
        timeline = _load_timeline_spec(project, timeline_id, str(args.get("version") or args.get("timelineVersion") or "").strip() or None)
    return {
        "ok": True,
        "context": _build_context_packet(project, timeline=timeline, include_paths=bool(args.get("includePaths") or args.get("include_paths"))),
    }


def _canvas_from_prompt(prompt: str, primary: dict[str, Any] | None, args: dict[str, Any]) -> dict[str, Any]:
    raw_ratio = str(args.get("aspectRatio") or args.get("aspect_ratio") or "").strip().lower()
    text = prompt.lower()
    if raw_ratio in {"9:16", "vertical"} or any(key in text for key in ("9:16", "vertical", "portrait", "shorts", "reels", "tiktok", "ตั้ง")):
        return {"width": 1080, "height": 1920, "fps": float(args.get("fps") or 30)}
    if raw_ratio in {"1:1", "square"}:
        return {"width": 1080, "height": 1080, "fps": float(args.get("fps") or 30)}
    if raw_ratio in {"16:9", "landscape"}:
        return {"width": 1920, "height": 1080, "fps": float(args.get("fps") or 30)}
    media = ((primary or {}).get("metadata") or {}).get("video") if isinstance((primary or {}).get("metadata"), dict) else {}
    return {
        "width": int(args.get("width") or (media or {}).get("width") or 1080),
        "height": int(args.get("height") or (media or {}).get("height") or 1920),
        "fps": float(args.get("fps") or (media or {}).get("fps") or 30),
    }


def _quoted_text(prompt: str) -> str | None:
    for pattern in (r'"([^"]{1,220})"', r"'([^']{1,220})'", r"“([^”]{1,220})”", r"‘([^’]{1,220})’"):
        match = re.search(pattern, prompt)
        if match:
            return match.group(1).strip()
    return None


def _latest_transcript_payload(project: dict[str, Any]) -> dict[str, Any] | None:
    for item in reversed([row for row in project.get("transcripts") or [] if isinstance(row, dict)]):
        for key in ("normalizedPath", "srtPath", "vttPath", "path"):
            raw_path = str(item.get(key) or "").strip()
            if raw_path and Path(raw_path).is_file():
                payload = _load_transcript_file(Path(raw_path))
                if payload.get("segments") or payload.get("text"):
                    payload["transcriptId"] = item.get("id")
                    return payload
        if item.get("segments") or item.get("text"):
            payload = _normalize_transcript_payload(item)
            payload["transcriptId"] = item.get("id")
            return payload
    return None


def _asset_matches(asset: dict[str, Any], roles: set[str], types: set[str]) -> bool:
    return str(asset.get("role") or "").lower() in roles or str(asset.get("type") or "").lower() in types


def _first_project_asset(project: dict[str, Any], *, roles: set[str], types: set[str]) -> dict[str, Any] | None:
    for asset in project.get("assets") or []:
        if isinstance(asset, dict) and _asset_matches(asset, roles, types):
            return asset
    return None


def _target_canvas(aspect_ratio: str, fps: float) -> dict[str, Any]:
    ratio = str(aspect_ratio or "9:16").strip().lower()
    if ratio in {"16:9", "landscape", "youtube"}:
        return {"width": 1920, "height": 1080, "fps": fps}
    if ratio in {"1:1", "square"}:
        return {"width": 1080, "height": 1080, "fps": fps}
    return {"width": 1080, "height": 1920, "fps": fps}


def _center_reframe_crop(video: dict[str, Any] | None, canvas: dict[str, Any]) -> dict[str, Any]:
    width = float((video or {}).get("width") or canvas.get("width") or 1080)
    height = float((video or {}).get("height") or canvas.get("height") or 1920)
    target_ratio = float(canvas.get("width") or 1080) / max(1.0, float(canvas.get("height") or 1920))
    source_ratio = width / max(1.0, height)
    if abs(source_ratio - target_ratio) < 0.03:
        return {"x": "0%", "y": "0%", "width": "100%", "height": "100%", "mode": "already_close"}
    if source_ratio > target_ratio:
        crop_width = target_ratio / source_ratio
        x = (1.0 - crop_width) / 2.0
        return {"x": f"{x * 100:.2f}%", "y": "0%", "width": f"{crop_width * 100:.2f}%", "height": "100%", "mode": "center_crop_width"}
    crop_height = source_ratio / target_ratio
    y = (1.0 - crop_height) / 2.0
    return {"x": "0%", "y": f"{y * 100:.2f}%", "width": "100%", "height": f"{crop_height * 100:.2f}%", "mode": "center_crop_height"}


def _percent_number(value: Any, default: float) -> float:
    if isinstance(value, (int, float)):
        raw = float(value)
        return max(0.0, min(raw * 100.0 if 0.0 <= raw <= 1.0 else raw, 100.0))
    text = str(value or "").strip()
    if text.endswith("%"):
        text = text[:-1]
    try:
        return max(0.0, min(float(text), 100.0))
    except (TypeError, ValueError):
        return default


def _subject_focus_from_args(prompt: str, args: dict[str, Any]) -> dict[str, Any]:
    focus = args.get("subjectFocus") if isinstance(args.get("subjectFocus"), dict) else {}
    text = prompt.lower()
    x = _percent_number(args.get("subjectX") or args.get("focusX") or focus.get("x"), 50.0)
    y = _percent_number(args.get("subjectY") or args.get("focusY") or focus.get("y"), 48.0)
    if not focus and args.get("subjectX") is None and args.get("focusX") is None:
        if any(key in text for key in ("left", "ซ้าย")):
            x = 38.0
        elif any(key in text for key in ("right", "ขวา")):
            x = 62.0
    if not focus and args.get("subjectY") is None and args.get("focusY") is None:
        if any(key in text for key in ("top", "upper", "บน", "head", "face", "หน้า")):
            y = 42.0
        elif any(key in text for key in ("bottom", "lower", "ล่าง")):
            y = 58.0
    target = str(args.get("target") or args.get("subject") or ("face" if any(key in text for key in ("face", "หน้า", "คน", "person")) else "subject")).strip() or "subject"
    return {"x": round(x, 2), "y": round(y, 2), "target": target, "source": "args_or_prompt"}


def _crop_with_focus(crop: dict[str, Any], focus_x: float, focus_y: float) -> dict[str, Any]:
    out = copy.deepcopy(crop)
    width = _percent_number(out.get("width") or out.get("w"), 100.0)
    height = _percent_number(out.get("height") or out.get("h"), 100.0)
    if width < 100.0:
        out["x"] = f"{max(0.0, min(focus_x - width / 2.0, 100.0 - width)):.2f}%"
    if height < 100.0:
        out["y"] = f"{max(0.0, min(focus_y - height / 2.0, 100.0 - height)):.2f}%"
    return out


def _subject_tracking_plan(
    video: dict[str, Any] | None,
    canvas: dict[str, Any],
    crop: dict[str, Any],
    selected: dict[str, Any],
    args: dict[str, Any],
    prompt: str,
    *,
    clip_id: str,
) -> dict[str, Any]:
    start_raw = _float_or_none(args.get("start")) if args.get("start") is not None else _float_or_none(selected.get("start"))
    start = max(0.0, start_raw or 0.0)
    end = _float_or_none(args.get("end")) if args.get("end") is not None else _float_or_none(selected.get("end"))
    duration = _float_or_none(args.get("duration")) or _float_or_none(selected.get("duration")) or _float_or_none((video or {}).get("duration")) or 3.0
    if end is None:
        end = start + max(0.5, duration)
    end = max(start + 0.5, float(end))
    focus = _subject_focus_from_args(prompt, args)
    focus_x = float(focus["x"])
    focus_y = float(focus["y"])
    drift = max(0.0, min(float(args.get("trackingDrift") or args.get("tracking_drift") or 3.0), 12.0))
    span = max(0.5, end - start)
    samples = max(3, min(int(args.get("keyframeCount") or args.get("keyframes") or 3), 12))
    keyframes: list[dict[str, Any]] = []
    for idx in range(samples):
        ratio = idx / max(1, samples - 1)
        wave = (ratio - 0.5) * 2.0
        k_focus_x = max(0.0, min(focus_x + wave * drift, 100.0))
        k_focus_y = max(0.0, min(focus_y + (0.5 - abs(ratio - 0.5)) * drift * 0.4, 100.0))
        time_at = start + span * ratio
        keyframes.append({
            "t": round(time_at - start, 3),
            "time": round(time_at, 3),
            "crop": _crop_with_focus(crop, k_focus_x, k_focus_y),
            "focus": {"x": f"{k_focus_x:.2f}%", "y": f"{k_focus_y:.2f}%", "target": focus["target"]},
        })
    confidence = 0.38 if crop.get("mode") == "already_close" else 0.46
    return {
        "target": focus["target"],
        "method": "heuristic_subject_focus",
        "clipId": clip_id,
        "start": round(start, 3),
        "end": round(end, 3),
        "duration": round(span, 3),
        "canvas": canvas,
        "baseCrop": crop,
        "keyframes": keyframes,
        "confidence": confidence,
        "requiresVisualConfirmation": True,
        "note": "Heuristic subject/face tracking plan from source geometry and prompt focus. Run video.inspect_segment or sample_frames before final render.",
    }


def _highlight_score(segment: dict[str, Any], prompt: str) -> float:
    text = str(segment.get("text") or "").lower()
    score = 1.0
    keywords = (
        "hook", "secret", "mistake", "why", "how", "best", "new", "important", "highlight",
        "สำคัญ", "วิธี", "เคล็ด", "พลาด", "สุด", "ใหม่", "ไฮไลต์", "เด็ด", "ด่วน",
    )
    score += sum(1.0 for keyword in keywords if keyword in text)
    prompt_tokens = [token for token in re.split(r"\W+", prompt.lower()) if len(token) >= 4]
    score += min(3.0, sum(0.35 for token in prompt_tokens if token in text))
    if "?" in str(segment.get("text") or ""):
        score += 0.5
    duration = max(0.1, (_float_or_none(segment.get("end")) or 0.0) - (_float_or_none(segment.get("start")) or 0.0))
    if 0.8 <= duration <= 8.0:
        score += 0.4
    return score


def _highlight_candidates(project: dict[str, Any], *, duration: float, args: dict[str, Any], prompt: str) -> list[dict[str, Any]]:
    count = max(1, min(int(args.get("maxHighlights") or args.get("count") or 3), 8))
    max_duration = max(0.8, min(float(args.get("maxClipDuration") or args.get("max_clip_duration") or 12.0), 90.0))
    min_duration = max(0.3, min(float(args.get("minClipDuration") or args.get("min_clip_duration") or 1.0), max_duration))
    transcript = _latest_transcript_payload(project)
    segments = [item for item in (transcript or {}).get("segments") or [] if isinstance(item, dict)]
    candidates: list[dict[str, Any]] = []
    if segments:
        scored: list[tuple[float, dict[str, Any]]] = []
        for segment in segments:
            start = max(0.0, _float_or_none(segment.get("start")) or 0.0)
            end = max(start + min_duration, _float_or_none(segment.get("end")) or start + min_duration)
            if duration:
                end = min(duration, end)
            if end <= start:
                continue
            if end - start > max_duration:
                end = start + max_duration
            scored.append((_highlight_score(segment, prompt), {"start": round(start, 3), "end": round(end, 3), "text": segment.get("text"), "source": "transcript", "transcriptId": (transcript or {}).get("transcriptId")}))
        for _, candidate in sorted(scored, key=lambda item: (item[0], -float(item[1]["start"])), reverse=True)[:count]:
            candidate["id"] = f"highlight_{len(candidates) + 1:02d}"
            candidate["duration"] = round(float(candidate["end"]) - float(candidate["start"]), 3)
            candidate["reason"] = "High-scoring transcript segment for hook/highlight editing."
            candidates.append(candidate)
    if candidates:
        return sorted(candidates, key=lambda item: float(item["start"]))
    safe_duration = max(duration or max_duration, min_duration)
    if safe_duration <= max_duration:
        spans = [(0.0, safe_duration)]
    else:
        window = min(max_duration, max(min_duration, safe_duration / max(count, 1)))
        stride = max(window * 0.85, (safe_duration - window) / max(count - 1, 1)) if count > 1 else 0.0
        spans = [(min(max(0.0, stride * idx), max(0.0, safe_duration - window)), min(safe_duration, min(max(0.0, stride * idx), max(0.0, safe_duration - window)) + window)) for idx in range(count)]
    for idx, (start, end) in enumerate(spans[:count], start=1):
        candidates.append({
            "id": f"highlight_{idx:02d}",
            "start": round(start, 3),
            "end": round(max(start + min_duration, end), 3),
            "duration": round(max(min_duration, end - start), 3),
            "source": "duration_heuristic",
            "reason": "No transcript highlight evidence was available, so this is an evenly sampled candidate range.",
        })
    return candidates


def _suggestion_hook_text(prompt: str, args: dict[str, Any]) -> str:
    return str(args.get("hookText") or args.get("hook_text") or args.get("text") or _quoted_text(prompt) or "Key moment").strip()


def _first_timeline_item_id(timeline: dict[str, Any] | None, collection: str) -> str:
    items = (timeline or {}).get(collection)
    if isinstance(items, list):
        for item in items:
            if isinstance(item, dict) and item.get("id"):
                return str(item["id"])
    return ""


def _position_from_prompt(prompt: str, args: dict[str, Any]) -> dict[str, Any] | None:
    explicit = args.get("position")
    if isinstance(explicit, dict):
        return explicit
    text = prompt.lower()
    if args.get("x") is not None or args.get("y") is not None:
        return {key: value for key, value in {"x": args.get("x"), "y": args.get("y"), "anchor": args.get("anchor") or "center"}.items() if value is not None}
    if not any(key in text for key in ("move", "position", "ตำแหน่ง", "ย้าย", "บน", "ล่าง", "ซ้าย", "ขวา", "กลาง")):
        return None
    x = "50%"
    y = "50%"
    anchor = "center"
    if any(key in text for key in ("top", "upper", "บน")):
        y = "14%"
    if any(key in text for key in ("bottom", "lower", "ล่าง")):
        y = "82%"
    if any(key in text for key in ("left", "ซ้าย")):
        x = "18%"
    if any(key in text for key in ("right", "ขวา")):
        x = "82%"
    if any(key in text for key in ("center", "middle", "กลาง")):
        if not any(key in text for key in ("top", "upper", "บน", "bottom", "lower", "ล่าง")):
            y = "50%"
        if not any(key in text for key in ("left", "ซ้าย", "right", "ขวา")):
            x = "50%"
    return {"x": x, "y": y, "anchor": anchor}


def _caption_style_from_args(args: dict[str, Any], prompt: str, canvas: dict[str, Any]) -> dict[str, Any] | None:
    explicit = _caption_style_from_tool_args(args, canvas=canvas)
    if explicit:
        return explicit
    text = prompt.lower()
    if not any(key in text for key in ("caption", "subtitle", "ซับ", "คำบรรยาย", "ตัวหนังสือ", "อ่านง่าย")):
        return None
    return _deep_merge_dict(_caption_style_preset("shorts.bold", canvas=canvas), {
        "color": str(args.get("captionColor") or args.get("caption_color") or "#ffffff"),
        "position": {"x": "50%", "y": "84%", "anchor": "center"},
        "stroke": {"color": "#000000", "width": 5},
        "box": {"color": "#000000", "opacity": 0.18, "padding": 12, "radius": 8},
    })


def _audio_patch_from_prompt(project: dict[str, Any], args: dict[str, Any], prompt: str, *, replace: bool = False) -> dict[str, Any] | None:
    asset_id = str(args.get("audioAssetId") or args.get("musicAssetId") or args.get("sfxAssetId") or args.get("assetId") or "").strip()
    if not asset_id:
        music_asset = _first_project_asset(project, roles={"music", "sfx", "soundtrack"}, types={"music", "audio", "sfx"})
        asset_id = str((music_asset or {}).get("id") or "")
    text = prompt.lower()
    if not asset_id or not any(key in text for key in ("audio", "music", "sound", "sfx", "เพลง", "เสียง", "ซาวน์")):
        return None
    replace_audio = replace or any(key in text for key in ("replace audio", "replace music", "replace sound", "เปลี่ยนเพลง", "เปลี่ยนเสียง", "เปลี่ยนซาวน์"))
    return {
        "op": "replace_audio" if replace_audio else "add_audio",
        "id": str(args.get("audioId") or args.get("audio_id") or "aud_planned_bed"),
        "assetId": asset_id,
        "start": _float_or_none(args.get("audioStart") or args.get("audio_start")) or 0,
        "volume": _float_or_none(args.get("volume")) or 0.32,
        "fadeIn": _float_or_none(args.get("fadeIn") or args.get("fade_in")) or 0.2,
        "fadeOut": _float_or_none(args.get("fadeOut") or args.get("fade_out")) or 0.8,
        "role": "music_bed",
    }


async def _track_subject(repo: Any, run: dict[str, Any], args: dict[str, Any]) -> dict[str, Any]:
    dept_id = run["departmentId"]
    project_id = str(args.get("projectId") or args.get("project_id") or "").strip()
    if not project_id:
        raise ValueError("projectId is required")
    project = _load_project(dept_id, project_id)
    source = await _resolve_source_path(repo, dept_id, args, project=project)
    metadata = _ffprobe(source)
    video_meta = metadata.get("video") if isinstance(metadata.get("video"), dict) else {}
    fmt = metadata.get("format") if isinstance(metadata.get("format"), dict) else {}
    duration = _float_or_none(fmt.get("duration")) or _float_or_none(video_meta.get("duration")) or 0.0
    fps = float(video_meta.get("fps") or args.get("fps") or 30)
    aspect_ratio = str(args.get("aspectRatio") or args.get("aspect_ratio") or "9:16")
    canvas = _target_canvas(aspect_ratio, fps)
    crop = _center_reframe_crop(video_meta, canvas)
    timeline_id = str(args.get("timelineId") or args.get("timeline_id") or args.get("baseTimelineId") or "").strip()
    base_timeline = _load_timeline_spec(project, timeline_id, str(args.get("baseVersion") or args.get("version") or "").strip() or None) if timeline_id else None
    first_clip_id = str(args.get("clipId") or args.get("clip_id") or ((base_timeline or {}).get("clips") or [{}])[0].get("id") or "clip_main")
    start = _float_or_none(args.get("start"))
    end = _float_or_none(args.get("end"))
    selected = {
        "start": max(0.0, start or 0.0),
        "end": max((start or 0.0) + 0.5, end if end is not None else duration or 3.0),
    }
    selected["duration"] = round(float(selected["end"]) - float(selected["start"]), 3)
    prompt = str(args.get("prompt") or args.get("instruction") or "").strip()
    plan = _subject_tracking_plan(video_meta, canvas, crop, selected, args, prompt, clip_id=first_clip_id)
    patch = [
        {"op": "update_clip", "id": first_clip_id, "crop": plan["baseCrop"], "fit": "cover", "in": plan["start"], "out": plan["end"]},
        {"op": "set_keyframes", "collection": "clips", "id": first_clip_id, "keyframes": plan["keyframes"]},
        {
            "op": "add_effect",
            "id": str(args.get("effectId") or args.get("effect_id") or "fx_subject_tracking"),
            "type": "subject_tracking",
            "target": plan["target"],
            "method": plan["method"],
            "confidence": plan["confidence"],
            "requiresVisualConfirmation": True,
            "keyframes": plan["keyframes"],
        },
    ]
    plan["patch"] = patch
    recommended: list[dict[str, Any]] = [
        {"tool": "video.inspect_segment", "args": {"projectId": project_id, "start": plan["start"], "end": plan["end"], "frameCount": max(3, min(int(args.get("frameCount") or 5), 12)), "preview": True}},
        {"tool": "video.sample_frames", "args": {"projectId": project_id, "start": plan["start"], "end": plan["end"], "count": max(3, min(int(args.get("frameCount") or 5), 12))}},
    ]
    if base_timeline:
        recommended.append({"tool": "video.patch_timeline", "args": {"projectId": project_id, "timelineId": base_timeline.get("id"), "baseVersion": base_timeline.get("version"), "patch": patch}})
    return {
        "ok": True,
        "tracking": plan,
        "patch": patch,
        "recommendedTools": recommended,
        "context": _build_context_packet(project, timeline=base_timeline, include_paths=bool(args.get("includePaths") or args.get("include_paths"))),
    }


async def _suggest_edits(repo: Any, run: dict[str, Any], args: dict[str, Any]) -> dict[str, Any]:
    dept_id = run["departmentId"]
    project_id = str(args.get("projectId") or args.get("project_id") or "").strip()
    if not project_id:
        raise ValueError("projectId is required")
    project = _load_project(dept_id, project_id)
    prompt = str(args.get("prompt") or args.get("instruction") or "").strip()
    primary = _primary_video_asset(project)
    primary_meta = (primary or {}).get("metadata") if isinstance((primary or {}).get("metadata"), dict) else {}
    video_meta = primary_meta.get("video") if isinstance(primary_meta.get("video"), dict) else {}
    duration = _float_or_none((primary_meta.get("format") or {}).get("duration") if isinstance(primary_meta.get("format"), dict) else None) or _float_or_none((video_meta or {}).get("duration")) or 0.0
    fps = float((video_meta or {}).get("fps") or args.get("fps") or 30)
    timeline_id = str(args.get("timelineId") or args.get("timeline_id") or args.get("baseTimelineId") or "").strip()
    base_timeline = _load_timeline_spec(project, timeline_id, str(args.get("baseVersion") or args.get("version") or "").strip() or None) if timeline_id else None
    aspect_ratio = str(args.get("aspectRatio") or args.get("aspect_ratio") or "9:16")
    canvas = _target_canvas(aspect_ratio, fps)
    style_guide = _timeline_style_from_args(args, prompt, canvas, spec=base_timeline)
    hook_style = (style_guide or {}).get("hookTextStyle") if isinstance((style_guide or {}).get("hookTextStyle"), dict) else {}
    caption_style = (style_guide or {}).get("captionStyle") if isinstance((style_guide or {}).get("captionStyle"), dict) else {}
    crop = _center_reframe_crop(video_meta, canvas)
    highlights = _highlight_candidates(project, duration=duration, args=args, prompt=prompt)
    selected = highlights[max(0, min(int(args.get("highlightIndex") or 0), len(highlights) - 1))] if highlights else {"start": 0.0, "end": max(1.0, duration), "duration": max(1.0, duration)}
    hook_text = _suggestion_hook_text(prompt, args)
    music_asset = _first_project_asset(project, roles={"music", "sfx", "soundtrack"}, types={"music", "audio", "sfx"})
    image_asset = _first_project_asset(project, roles={"broll", "b-roll", "overlay", "logo"}, types={"image"})
    goals = args.get("goals") if isinstance(args.get("goals"), list) else ["auto_highlight", "auto_reframe", "face_tracking", "caption_hook", "sound_mix", "broll_hint"]
    first_clip_id = str(((base_timeline or {}).get("clips") or [{}])[0].get("id") or "clip_main")
    subject_tracking = _subject_tracking_plan(video_meta, canvas, crop, selected, args, prompt, clip_id=first_clip_id)
    patch: list[dict[str, Any]] = [
        {"op": "set_canvas", **canvas},
        *([{"op": "set_style_guide", "templateId": style_guide.get("id"), "styleGuide": style_guide}] if style_guide else []),
        {"op": "update_clip", "id": first_clip_id, "in": selected["start"], "out": selected["end"], "crop": crop, "fit": "cover"},
        {"op": "set_keyframes", "collection": "clips", "id": first_clip_id, "keyframes": subject_tracking["keyframes"]},
        {
            "op": "add_effect",
            "id": "fx_subject_tracking",
            "type": "subject_tracking",
            "target": subject_tracking["target"],
            "method": subject_tracking["method"],
            "confidence": subject_tracking["confidence"],
            "requiresVisualConfirmation": True,
            "keyframes": subject_tracking["keyframes"],
        },
        {
            "op": "add_text",
            "id": "txt_auto_hook",
            "role": "hook",
            "text": hook_text,
            "start": 0,
            "end": min(3.2, max(1.0, float(selected.get("duration") or 1.0))),
            **_deep_merge_dict({
                "size": 64 if canvas["height"] >= 1600 else 44,
                "position": {"x": "50%", "y": "14%", "anchor": "center"},
                "stroke": {"color": "#111111", "width": 6},
                "animation": "fade-up",
                "maxWidth": "86%",
            }, hook_style),
        },
        {
            "op": "set_caption_style",
            **_deep_merge_dict({
                "size": 48 if canvas["height"] >= 1600 else 28,
                "color": "#ffffff",
                "position": {"x": "50%", "y": "84%", "anchor": "center"},
                "stroke": {"color": "#000000", "width": 5},
                "box": {"color": "#000000", "opacity": 0.18, "padding": 12, "radius": 8},
            }, caption_style),
        },
    ]
    if music_asset:
        patch.append({"op": "add_audio", "id": "aud_auto_bed", "assetId": music_asset.get("id"), "start": 0, "volume": 0.28, "fadeIn": 0.2, "fadeOut": 0.8, "role": "music_bed"})
    if image_asset:
        patch.append({"op": "add_overlay", "id": "ovl_auto_broll", "assetId": image_asset.get("id"), "start": 0.4, "end": min(2.8, max(1.0, float(selected.get("duration") or 1.0))), "width": "38%", "position": {"x": "76%", "y": "28%", "anchor": "center"}, "opacity": 0.92, "animation": "fade"})
    timeline = None
    if not base_timeline:
        timeline = {
            "id": _timeline_id(args.get("timelineId") or args.get("timeline_id")),
            "canvas": canvas,
            "clips": [{"id": first_clip_id, "assetId": (primary or {}).get("id"), "in": selected["start"], "out": selected["end"], "crop": crop, "fit": "cover", "keyframes": subject_tracking["keyframes"]}],
            "text": [item for item in patch if item.get("op") == "add_text"],
            "captions": [],
            "captionStyle": next((item for item in patch if item.get("op") == "set_caption_style"), None),
            "audio": [{"id": item["id"], "assetId": item.get("assetId"), "start": item.get("start"), "volume": item.get("volume"), "fadeIn": item.get("fadeIn"), "fadeOut": item.get("fadeOut")} for item in patch if item.get("op") == "add_audio"],
            "overlays": [{"id": item["id"], "assetId": item.get("assetId"), "start": item.get("start"), "end": item.get("end"), "width": item.get("width"), "position": item.get("position"), "opacity": item.get("opacity"), "animation": item.get("animation")} for item in patch if item.get("op") == "add_overlay"],
            "effects": [
                {"id": "fx_auto_reframe", "type": "auto_reframe", "mode": crop.get("mode"), "confidence": 0.55, "requiresVisualConfirmation": True},
                {"id": "fx_subject_tracking", "type": "subject_tracking", "target": subject_tracking["target"], "method": subject_tracking["method"], "confidence": subject_tracking["confidence"], "requiresVisualConfirmation": True, "keyframes": subject_tracking["keyframes"]},
            ],
            "export": {"format": "mp4", "quality": "social_1080p", "filename": str(args.get("outputName") or "suggested-preview.mp4")},
        }
        if style_guide:
            _apply_timeline_style(timeline, style_guide)
    suggestions = {
        "mode": "patch" if base_timeline else "timeline",
        "goals": goals,
        "templateId": (style_guide or {}).get("id"),
        "styleGuide": style_guide,
        "highlights": highlights,
        "selectedHighlight": selected,
        "autoReframe": {"targetAspectRatio": aspect_ratio, "canvas": canvas, "crop": crop, "confidence": 0.55, "note": "Center crop heuristic; run storyboard/inspect_segment before final if subject placement matters."},
        "subjectTracking": subject_tracking,
        "patch": patch,
        "timeline": timeline,
        "brollSuggestions": [
            {"type": "existing_asset_overlay", "assetId": image_asset.get("id"), "reason": "Usable image asset exists in the project."} if image_asset else {"type": "generate_or_import_broll", "prompt": f"Create a clean B-roll insert for: {prompt or hook_text}", "reason": "No B-roll image asset is available yet."}
        ],
        "soundSuggestions": [
            {"type": "existing_music_bed", "assetId": music_asset.get("id"), "volume": 0.28} if music_asset else {"type": "import_music_or_sfx", "query": "upbeat short-form background music, low vocal conflict", "reason": "No music/sfx asset is available yet."}
        ],
        "generativeInsertPrompts": [
            {"type": "image.generate", "prompt": f"Vertical social-video B-roll visual, clear subject, no text overlay, concept: {prompt or hook_text}"},
        ],
        "recommendedTools": [
            {"tool": "video.track_subject", "args": {"projectId": project_id, "timelineId": timeline_id or (base_timeline or timeline or {}).get("id"), "baseVersion": (base_timeline or timeline or {}).get("version"), "start": selected["start"], "end": selected["end"], "aspectRatio": aspect_ratio, "target": subject_tracking["target"]}},
            {"tool": "video.inspect_segment", "args": {"projectId": project_id, "start": selected["start"], "end": selected["end"], "frameCount": 4, "preview": True}},
            {"tool": "video.patch_timeline", "args": {"projectId": project_id, "timelineId": base_timeline.get("id"), "baseVersion": base_timeline.get("version"), "patch": patch}} if base_timeline else {"tool": "video.render_edit", "args": {"projectId": project_id, "timeline": timeline, "kind": "preview", "asyncMode": True}},
            {"tool": "video.render_motion" if any(item.get("op") == "add_text" for item in patch) else "video.render_edit", "args": {"projectId": project_id, **({"timelineId": base_timeline.get("id")} if base_timeline else {"timeline": timeline}), "kind": "preview", "render": False}},
            {"tool": "video.quality_check", "args": {"projectId": project_id}},
        ],
    }
    return {"ok": True, "suggestion": suggestions, "context": _build_context_packet(project, timeline=base_timeline or timeline, include_paths=bool(args.get("includePaths") or args.get("include_paths")))}


async def _plan_edit(repo: Any, run: dict[str, Any], args: dict[str, Any]) -> dict[str, Any]:
    dept_id = run["departmentId"]
    project_id = str(args.get("projectId") or args.get("project_id") or "").strip()
    if not project_id:
        raise ValueError("projectId is required")
    project = _load_project(dept_id, project_id)
    prompt = str(args.get("prompt") or args.get("instruction") or "").strip()
    primary = _primary_video_asset(project)
    primary_meta = (primary or {}).get("metadata") if isinstance((primary or {}).get("metadata"), dict) else {}
    duration = _float_or_none((primary_meta.get("format") or {}).get("duration") if isinstance(primary_meta.get("format"), dict) else None) or 0.0
    mode = str(args.get("mode") or "").strip().lower()
    base_timeline_id = str(args.get("timelineId") or args.get("timeline_id") or args.get("baseTimelineId") or "").strip()
    base_timeline = _load_timeline_spec(project, base_timeline_id, str(args.get("baseVersion") or args.get("version") or "").strip() or None) if base_timeline_id else None
    if not mode:
        mode = "patch" if base_timeline else "timeline"
    canvas = _canvas_from_prompt(prompt, primary, args)
    style_guide = _timeline_style_from_args(args, prompt, canvas, spec=base_timeline)
    start = max(0.0, _float_or_none(args.get("start")) or 0.0)
    requested_end = _float_or_none(args.get("end"))
    requested_duration = _float_or_none(args.get("duration"))
    end = requested_end if requested_end is not None else (start + requested_duration if requested_duration else duration)
    if not end or end <= start:
        end = start + 15.0
    hook_text = str(args.get("hookText") or args.get("hook_text") or args.get("text") or _quoted_text(prompt) or "").strip()
    caption_text = str(args.get("captionText") or args.get("caption_text") or "").strip()
    needs_visual = bool(args.get("needsVisualInspection") or args.get("needs_visual_inspection")) or any(
        key in prompt.lower()
        for key in ("ดู", "ภาพ", "scene", "frame", "หน้า", "บัง", "ตำแหน่ง", "object", "visual")
    )
    needs_advanced_suggestion = any(
        key in prompt.lower()
        for key in ("highlight", "ไฮไลต์", "reframe", "crop", "track", "tracking", "face", "subject", "หน้า", "ตามหน้า", "b-roll", "broll", "sound", "music", "เพลง", "เสียง", "9:16", "tiktok", "reels", "shorts")
    )
    if mode == "patch":
        patch_ops: list[dict[str, Any]] = []
        if args.get("patch") and isinstance(args.get("patch"), list):
            patch_ops = copy.deepcopy(args["patch"])
        elif base_timeline:
            if style_guide:
                patch_ops.append({"op": "set_style_guide", "templateId": style_guide.get("id"), "styleGuide": style_guide})
            prompt_position = _position_from_prompt(prompt, args)
            text_id = _first_timeline_item_id(base_timeline, "text")
            caption_id = _first_timeline_item_id(base_timeline, "captions")
            clip_id = _first_timeline_item_id(base_timeline, "clips")
            if args.get("start") is not None or args.get("end") is not None or args.get("duration") is not None:
                if clip_id:
                    if any(key in prompt.lower() for key in ("ตัดออก", "ลบช่วง", "remove range", "cut out", "delete range")):
                        patch_ops.append({"op": "cut_range", "id": clip_id, "start": start, "end": end})
                    else:
                        patch_ops.append({"op": "trim_clip", "id": clip_id, "in": start, "out": end})
            if hook_text:
                patch_ops.append({"op": "update_text" if text_id else "add_text", **({"id": text_id} if text_id else {}), "text": hook_text})
            if prompt_position and (text_id or hook_text):
                patch_ops.append({"op": "move_text", **({"id": text_id} if text_id else {}), "position": prompt_position})
            style_patch: dict[str, Any] = {}
            for key in ("font", "fontFile", "fontPath", "size", "fontSize", "color"):
                if args.get(key) is not None:
                    style_patch[key] = args[key]
            if style_patch and (text_id or hook_text):
                patch_ops.append({"op": "style_text", **({"id": text_id} if text_id else {}), **style_patch})
            if args.get("textStart") is not None or args.get("textEnd") is not None or args.get("textDuration") is not None:
                patch_ops.append({
                    "op": "retime_text",
                    **({"id": text_id} if text_id else {}),
                    "start": args.get("textStart"),
                    "end": args.get("textEnd"),
                    "duration": args.get("textDuration"),
                })
            if caption_text:
                if caption_id:
                    patch_ops.append({"op": "update_caption", "id": caption_id, "text": caption_text})
                else:
                    patch_ops.append({"op": "add_caption", "text": caption_text, "start": start, "end": min(end, start + 6.0)})
            caption_style = _caption_style_from_args(args, prompt, canvas)
            if caption_style:
                patch_ops.append({"op": "set_caption_style", **caption_style})
            audio_patch = _audio_patch_from_prompt(project, args, prompt)
            if audio_patch:
                patch_ops.append(audio_patch)
            if any(key in prompt.lower() for key in ("duck", "ลดเสียงพูด", "เสียงเบา", "voiceover")):
                patch_ops.append({"op": "set_audio_mix", "ducking": {"enabled": True, "target": "music", "sidechain": "voice", "amount": 0.35}})
            if any(key in prompt.lower() for key in ("keyframe", "animate", "animation", "ขยับ", "เคลื่อนไหว")) and (text_id or hook_text):
                patch_ops.append({
                    "op": "set_keyframes",
                    "collection": "text",
                    **({"id": text_id} if text_id else {}),
                    "keyframes": [
                        {"t": 0, "opacity": 0, "yOffset": 24},
                        {"t": 0.35, "opacity": 1, "yOffset": 0},
                    ],
                })
            if any(key in prompt.lower() for key in ("blur", "เบลอ", "effect", "ฟิลเตอร์")):
                patch_ops.append({"op": "add_effect", "id": "fx_planned_style", "type": "visual_filter", "name": "prompt_effect", "prompt": prompt[:240]})
        proposal = {
            "mode": "patch",
            "baseTimeline": _timeline_ref(project, base_timeline),
            "patch": patch_ops,
            "patchSchemaHints": [
                {"op": "move_text", "id": "txt_id", "position": {"x": "50%", "y": "18%", "anchor": "center"}},
                {"op": "style_text", "id": "txt_id", "font": "Kanit", "size": 56, "stroke": {"color": "#000000", "width": 5}},
                {"op": "update_caption", "id": "cap_id", "text": "new caption"},
                {"op": "trim_clip", "id": "clip_id", "in": 0, "out": 12.5},
                {"op": "cut_range", "id": "clip_id", "start": 4.0, "end": 6.0},
                {"op": "add_audio", "assetId": "asset_music", "start": 0, "volume": 0.35, "fadeOut": 1.2},
                {"op": "set_keyframes", "collection": "text", "id": "txt_id", "keyframes": [{"t": 0, "opacity": 0}, {"t": 0.35, "opacity": 1}]},
                {"op": "set_style_guide", "templateId": "brand.social.bold"},
            ],
        }
    else:
        timeline = {
            "id": _timeline_id(args.get("timelineId") or args.get("timeline_id")),
            "canvas": canvas,
            "clips": [{"id": "clip_main", "assetId": (primary or {}).get("id"), "in": start, "out": end, "fit": args.get("fit") or "cover"}],
            "text": [],
            "captions": [],
            "audio": [],
            "overlays": [],
            "effects": [],
            "export": {"format": "mp4", "quality": args.get("quality") or "social_1080p", "filename": str(args.get("outputName") or "planned-preview.mp4")},
        }
        if hook_text:
            timeline["text"].append({
                "id": "txt_hook",
                "role": "hook",
                "text": hook_text,
                "start": 0,
                "end": min(3.5, max(1.0, end - start)),
                "size": 64 if canvas["height"] >= 1600 else 44,
                "position": {"x": "50%", "y": "16%", "anchor": "center"},
                "stroke": {"color": "#111111", "width": 6},
                "maxWidth": "86%",
            })
        if caption_text:
            timeline["captions"].append({"id": "cap_1", "start": start, "end": min(end, start + 6.0), "text": caption_text})
        if style_guide:
            _apply_timeline_style(timeline, style_guide)
        proposal = {"mode": "timeline", "timeline": timeline}
    recommended_tools = [
        {"tool": "video.context_packet", "args": {"projectId": project_id}},
        {"tool": "video.suggest_edits", "args": {"projectId": project_id, "timelineId": (base_timeline or {}).get("id"), "baseVersion": (base_timeline or {}).get("version"), "prompt": prompt, "aspectRatio": args.get("aspectRatio") or args.get("aspect_ratio") or "9:16"}} if needs_advanced_suggestion else None,
        {"tool": "video.storyboard", "args": {"projectId": project_id, "count": 8}} if needs_visual else None,
        {"tool": "video.render_edit", "args": {"projectId": project_id, "kind": "preview", "asyncMode": True}},
        {"tool": "video.quality_check", "args": {"projectId": project_id}},
    ]
    return {
        "ok": True,
        "plan": {
            "summary": "Generated a structured video edit proposal from project context and user instruction.",
            "mode": proposal["mode"],
            "needsVisualInspection": needs_visual,
            "proposal": proposal,
            "recommendedTools": [item for item in recommended_tools if item],
        },
        "context": _build_context_packet(project, timeline=base_timeline, include_paths=bool(args.get("includePaths") or args.get("include_paths"))),
    }


def _media_context(
    project: dict[str, Any] | None,
    *,
    timeline: dict[str, Any] | None = None,
    frames: list[dict[str, Any]] | None = None,
    asset_path: Path | None = None,
    segment: dict[str, Any] | None = None,
    storyboard: dict[str, Any] | None = None,
    preview: dict[str, Any] | None = None,
) -> dict[str, Any]:
    if project is None:
        return {}
    primary = _primary_video_asset(project)
    edit_ref = _timeline_ref(project, timeline)
    context = {
        "type": "video_context",
        "mediaHandle": f"atrium://video/projects/{project['id']}",
        "projectId": project["id"],
        "ownerDept": project["ownerDept"],
        "assetId": (primary or {}).get("id"),
        "assetRef": f"atrium://video/assets/{(primary or {}).get('id')}" if primary else None,
        "sourcePath": str(asset_path or (primary or {}).get("path") or ""),
        "timelineId": (timeline or {}).get("id"),
        "timelineVersion": (timeline or {}).get("version"),
        "editSpecRef": edit_ref,
        "frames": frames or [],
    }
    if segment:
        context["segment"] = segment
    if storyboard:
        context["storyboard"] = storyboard
    if preview:
        context["preview"] = preview
    if timeline:
        context["canvas"] = timeline.get("canvas")
        if isinstance(timeline.get("styleGuide"), dict):
            context["templateId"] = timeline.get("templateId") or timeline["styleGuide"].get("id")
            context["styleGuide"] = {
                "id": timeline["styleGuide"].get("id"),
                "name": timeline["styleGuide"].get("name"),
                "type": timeline["styleGuide"].get("type"),
                "safeArea": timeline["styleGuide"].get("safeArea"),
            }
    return context


async def execute_video_tool(repo: Any, run: dict[str, Any]) -> dict[str, Any]:
    tool = str(run.get("tool") or "")
    args = run.get("args") if isinstance(run.get("args"), dict) else {}
    if tool in VIDEO_BACKGROUND_TOOL_NAMES and _wants_background(args):
        result = await _queue_video_job(repo, run, args)
    elif tool == "video.create_project":
        result = await _create_project(repo, run, args)
    elif tool == "video.add_asset":
        result = await _add_asset(repo, run, args)
    elif tool == "video.list_fonts":
        result = await _list_fonts(run, args)
    elif tool == "video.inspect":
        result = await _inspect(repo, run, args)
    elif tool == "video.sample_frames":
        result = await _sample_frames(repo, run, args)
    elif tool == "video.storyboard":
        result = await _storyboard(repo, run, args)
    elif tool == "video.inspect_segment":
        result = await _inspect_segment(repo, run, args)
    elif tool == "video.context_packet":
        result = await _context_packet(repo, run, args)
    elif tool == "video.plan_edit":
        result = await _plan_edit(repo, run, args)
    elif tool == "video.suggest_edits":
        result = await _suggest_edits(repo, run, args)
    elif tool == "video.track_subject":
        result = await _track_subject(repo, run, args)
    elif tool == "video.render_edit":
        result = await _render_edit(repo, run, args)
    elif tool == "video.render_motion":
        result = await _render_motion(repo, run, args)
    elif tool == "video.patch_timeline":
        result = await _patch_timeline(repo, run, args)
    elif tool == "video.transcribe":
        result = await _transcribe(repo, run, args)
    elif tool == "video.generate_captions":
        result = await _generate_captions(repo, run, args)
    elif tool == "video.quality_check":
        result = await _quality_check(repo, run, args)
    elif tool == "video.request_review":
        result = await _request_review(repo, run, args)
    elif tool == "video.approve_render":
        result = await _approve_render(repo, run, args)
    elif tool == "video.job_status":
        result = await _job_status(repo, args)
    elif tool == "video.cancel_job":
        result = await _cancel_job(repo, run, args)
    elif tool == "video.resume_job":
        result = await _resume_job(repo, run, args)
    elif tool == "video.list_templates":
        result = _list_templates(args)
    else:
        raise ValueError(f"unsupported video tool: {tool}")
    return {"tool": tool, **result}
