"""ChatGPT account OAuth support for the Codex responses backend.

This follows the same PKCE shape OpenClaw uses for ChatGPT/Codex account auth,
but stores credentials in ATRIUM's own app-scoped token sink instead of reusing
or overwriting Codex CLI state.
"""
from __future__ import annotations

import base64
import contextlib
import hashlib
import json
import os
import secrets
import threading
import time
import urllib.parse
import webbrowser
from datetime import datetime
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from typing import Any

import httpx

from ..config import Settings
from .openai_provider import OpenAIResponsesProvider

try:
    import fcntl  # type: ignore[import-not-found]
except ImportError:  # pragma: no cover - Windows runtime path
    fcntl = None  # type: ignore[assignment]

try:
    import msvcrt  # type: ignore[import-not-found]
except ImportError:  # pragma: no cover - non-Windows runtime path
    msvcrt = None  # type: ignore[assignment]

CLIENT_ID = "app_EMoamEEZ73f0CkXaXp7hrann"
AUTHORIZE_URL = "https://auth.openai.com/oauth/authorize"
TOKEN_URL = "https://auth.openai.com/oauth/token"
CALLBACK_PORT = 1455
CALLBACK_PATH = "/auth/callback"
CALLBACK_HOST = "localhost"
REDIRECT_URI = f"http://{CALLBACK_HOST}:{CALLBACK_PORT}{CALLBACK_PATH}"
SCOPE = "openid profile email offline_access"
EXPIRY_SKEW_MS = 60_000
LOGIN_SESSION_TTL_MS = 5 * 60 * 1000


class ChatGPTAccountOAuthError(RuntimeError):
    pass


_ACTIVE_LOGIN: dict[str, Any] | None = None
_ACTIVE_LOGIN_LOCK = threading.Lock()


def _now_ms() -> int:
    return int(time.time() * 1000)


def _b64url(data: bytes) -> str:
    return base64.urlsafe_b64encode(data).decode("ascii").rstrip("=")


def _decode_b64url(value: str) -> bytes:
    padding = "=" * (-len(value) % 4)
    return base64.urlsafe_b64decode((value + padding).encode("ascii"))


def _decode_jwt_payload(token: str) -> dict[str, Any]:
    parts = token.split(".")
    if len(parts) != 3:
        return {}
    try:
        parsed = json.loads(_decode_b64url(parts[1]).decode("utf-8"))
    except Exception:
        return {}
    return parsed if isinstance(parsed, dict) else {}


def _jwt_expiry_ms(token: str) -> int | None:
    payload = _decode_jwt_payload(token)
    exp = payload.get("exp")
    try:
        value = int(exp)
    except (TypeError, ValueError):
        return None
    return value * 1000 if value > 0 else None


def _identity_from_access_token(access_token: str, fallback_email: str | None = None) -> dict[str, str]:
    payload = _decode_jwt_payload(access_token)
    auth = payload.get("https://api.openai.com/auth")
    profile = payload.get("https://api.openai.com/profile")
    out: dict[str, str] = {}
    if isinstance(auth, dict):
        account_id = auth.get("chatgpt_account_id")
        plan_type = auth.get("chatgpt_plan_type")
        if isinstance(account_id, str) and account_id.strip():
            out["accountId"] = account_id.strip()
        if isinstance(plan_type, str) and plan_type.strip():
            out["chatgptPlanType"] = plan_type.strip()
    email = profile.get("email") if isinstance(profile, dict) else None
    if not isinstance(email, str) or not email.strip():
        email = fallback_email
    if isinstance(email, str) and email.strip():
        out["email"] = email.strip()
    return out


def _parse_expiry_ms(value: Any) -> int | None:
    if value is None or value == "":
        return None
    if isinstance(value, (int, float)):
        raw = int(value)
        return raw * 1000 if 0 < raw < 10_000_000_000 else raw if raw > 0 else None
    if isinstance(value, str):
        text = value.strip()
        if not text:
            return None
        if text.isdigit():
            return _parse_expiry_ms(int(text))
        with contextlib.suppress(ValueError):
            parsed = datetime.fromisoformat(text.replace("Z", "+00:00"))
            return int(parsed.timestamp() * 1000)
    return None


def _resolve_store_path(settings: Settings) -> Path:
    configured = Path(settings.chatgpt_account_oauth_store).expanduser()
    return configured if configured.is_absolute() else (Path.cwd() / configured).resolve()


def _read_json_file(path: Path) -> dict[str, Any]:
    try:
        parsed = json.loads(path.read_text(encoding="utf-8"))
    except FileNotFoundError:
        return {}
    except Exception as exc:
        raise ChatGPTAccountOAuthError(f"ChatGPT OAuth store is unreadable: {path}: {exc}") from exc
    return parsed if isinstance(parsed, dict) else {}


def _write_json_file(path: Path, payload: dict[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_name(f"{path.name}.tmp")
    tmp.write_text(json.dumps(payload, ensure_ascii=False, indent=2, sort_keys=True) + "\n", encoding="utf-8")
    os.chmod(tmp, 0o600)
    os.replace(tmp, path)
    os.chmod(path, 0o600)


@contextlib.contextmanager
def _credential_file_lock(path: Path):
    path.parent.mkdir(parents=True, exist_ok=True)
    lock_path = path.with_name(f"{path.name}.lock")
    with lock_path.open("a+", encoding="utf-8") as lock_file:
        if fcntl is not None:
            fcntl.flock(lock_file.fileno(), fcntl.LOCK_EX)
            try:
                yield
            finally:
                fcntl.flock(lock_file.fileno(), fcntl.LOCK_UN)
            return
        if msvcrt is not None:
            lock_file.seek(0)
            if not lock_file.read(1):
                lock_file.write("0")
                lock_file.flush()
            lock_file.seek(0)
            msvcrt.locking(lock_file.fileno(), msvcrt.LK_LOCK, 1)
            try:
                yield
            finally:
                lock_file.seek(0)
                msvcrt.locking(lock_file.fileno(), msvcrt.LK_UNLCK, 1)
            return
        yield


def _normalize_credentials(payload: dict[str, Any]) -> dict[str, Any]:
    access = payload.get("access") or payload.get("access_token")
    refresh = payload.get("refresh") or payload.get("refresh_token")
    if not isinstance(access, str) or not access.strip():
        return {}
    creds: dict[str, Any] = {
        "type": "oauth",
        "provider": "openai",
        "access": access.strip(),
    }
    if isinstance(refresh, str) and refresh.strip():
        creds["refresh"] = refresh.strip()
    expires = (
        _parse_expiry_ms(payload.get("expires"))
        or _parse_expiry_ms(payload.get("expiresAt"))
        or _parse_expiry_ms(payload.get("expires_at"))
        or _jwt_expiry_ms(access)
    )
    if expires:
        creds["expires"] = expires
    for key in ("idToken", "accountId", "email", "chatgptPlanType"):
        value = payload.get(key)
        if isinstance(value, str) and value.strip():
            creds[key] = value.strip()
    creds.update({k: v for k, v in _identity_from_access_token(access, creds.get("email")).items() if v})
    return creds


def _env_credentials(settings: Settings) -> dict[str, Any]:
    if not settings.chatgpt_account_access_token.strip():
        return {}
    return _normalize_credentials({
        "access": settings.chatgpt_account_access_token,
        "refresh": settings.chatgpt_account_refresh_token,
        "expires": settings.chatgpt_account_expires_at,
    })


def _load_credentials(settings: Settings) -> tuple[dict[str, Any], str]:
    env_creds = _env_credentials(settings)
    if env_creds:
        return env_creds, "env"
    store_path = _resolve_store_path(settings)
    store_creds = _normalize_credentials(_read_json_file(store_path))
    if store_creds:
        return store_creds, "store"
    return {}, "none"


def _credentials_expired(creds: dict[str, Any]) -> bool:
    expires = _parse_expiry_ms(creds.get("expires"))
    return bool(expires and expires <= _now_ms() + EXPIRY_SKEW_MS)


def _credentials_ready(creds: dict[str, Any]) -> bool:
    if not creds.get("access"):
        return False
    return not _credentials_expired(creds) or bool(creds.get("refresh"))


def _credentials_from_token_response(data: dict[str, Any], previous: dict[str, Any] | None = None) -> dict[str, Any]:
    access = data.get("access_token") or data.get("access")
    if not isinstance(access, str) or not access.strip():
        raise ChatGPTAccountOAuthError("OpenAI OAuth token response did not include access_token")
    refresh = data.get("refresh_token") or data.get("refresh")
    if not isinstance(refresh, str) or not refresh.strip():
        refresh = (previous or {}).get("refresh")
    expires = _jwt_expiry_ms(access)
    expires_in = data.get("expires_in")
    with contextlib.suppress(TypeError, ValueError):
        expires = _now_ms() + int(float(expires_in)) * 1000
    creds: dict[str, Any] = {
        "type": "oauth",
        "provider": "openai",
        "access": access.strip(),
        "expires": expires or (_now_ms() + 3_600_000),
        "updatedAt": _now_ms(),
    }
    if isinstance(refresh, str) and refresh.strip():
        creds["refresh"] = refresh.strip()
    id_token = data.get("id_token") or data.get("idToken")
    if isinstance(id_token, str) and id_token.strip():
        creds["idToken"] = id_token.strip()
    creds.update({k: v for k, v in _identity_from_access_token(access).items() if v})
    return creds


async def refresh_chatgpt_oauth_credentials(
    refresh_token: str,
    *,
    previous: dict[str, Any] | None = None,
    timeout: float | None = None,
) -> dict[str, Any]:
    try:
        async with httpx.AsyncClient(timeout=timeout or 60.0) as client:
            resp = await client.post(
                TOKEN_URL,
                data={
                    "grant_type": "refresh_token",
                    "refresh_token": refresh_token,
                    "client_id": CLIENT_ID,
                },
                headers={"Content-Type": "application/x-www-form-urlencoded"},
            )
            resp.raise_for_status()
    except httpx.HTTPStatusError as exc:
        detail = exc.response.text[:800]
        raise ChatGPTAccountOAuthError(f"ChatGPT OAuth refresh failed {exc.response.status_code}: {detail}") from exc
    except httpx.RequestError as exc:
        raise ChatGPTAccountOAuthError(f"ChatGPT OAuth refresh request failed: {type(exc).__name__}: {exc}") from exc
    return _credentials_from_token_response(resp.json(), previous=previous)


def chatgpt_oauth_status(settings: Settings) -> dict[str, Any]:
    store_path = _resolve_store_path(settings)
    creds, source = _load_credentials(settings)
    expires = _parse_expiry_ms(creds.get("expires"))
    return {
        "ready": _credentials_ready(creds),
        "source": source,
        "storePath": str(store_path),
        "storeExists": store_path.exists(),
        "baseUrl": settings.chatgpt_account_base_url,
        "hasAccessToken": bool(creds.get("access")),
        "hasRefreshToken": bool(creds.get("refresh")),
        "expired": _credentials_expired(creds) if creds else False,
        "expiresAt": datetime.fromtimestamp(expires / 1000).isoformat() if expires else None,
        "accountId": creds.get("accountId"),
        "email": creds.get("email"),
        "chatgptPlanType": creds.get("chatgptPlanType"),
    }


def disconnect_chatgpt_oauth(settings: Settings) -> dict[str, Any]:
    """Remove ATRIUM's app-scoped ChatGPT OAuth store and cancel pending UI login."""
    global _ACTIVE_LOGIN
    store_path = _resolve_store_path(settings)
    before = chatgpt_oauth_status(settings)
    removed = False
    with _credential_file_lock(store_path):
        if store_path.exists():
            store_path.unlink()
            removed = True
    with _ACTIVE_LOGIN_LOCK:
        if _ACTIVE_LOGIN and _ACTIVE_LOGIN.get("status") == "pending":
            _ACTIVE_LOGIN.update({
                "status": "cancelled",
                "error": "ChatGPT OAuth login cancelled by logout",
                "completedAt": _now_ms(),
            })
    after = chatgpt_oauth_status(settings)
    env_backed = after.get("source") == "env"
    return {
        "ok": not after.get("ready"),
        "provider": "chatgpt_account",
        "removedStore": removed,
        "storePath": str(store_path),
        "sourceBefore": before.get("source"),
        "sourceAfter": after.get("source"),
        "envBacked": env_backed,
        "warning": (
            "ChatGPT account auth is still supplied by ATRIUM_CHATGPT_ACCOUNT_ACCESS_TOKEN or CHATGPT_ACCOUNT_ACCESS_TOKEN"
            if env_backed
            else None
        ),
        "status": after,
    }


class ChatGPTCodexOAuthTokenProvider:
    def __init__(self, settings: Settings):
        self.settings = settings

    async def access_token(self) -> str:
        store_path = _resolve_store_path(self.settings)
        with _credential_file_lock(store_path):
            creds, source = _load_credentials(self.settings)
            if not creds:
                raise ChatGPTAccountOAuthError(
                    "ChatGPT account OAuth is not configured; run "
                    "`uv --project system run python ops/chatgpt_account_oauth_login.py` "
                    "from the repo root."
                )
            if not _credentials_expired(creds):
                return str(creds["access"])
            refresh_token = creds.get("refresh")
            if not isinstance(refresh_token, str) or not refresh_token.strip():
                raise ChatGPTAccountOAuthError("ChatGPT account OAuth access token is expired and no refresh token is stored")
            refreshed = await refresh_chatgpt_oauth_credentials(
                refresh_token,
                previous=creds,
                timeout=self.settings.provider_request_timeout_s,
            )
            if source == "store":
                _write_json_file(store_path, refreshed)
            return str(refreshed["access"])


class ChatGPTAccountResponsesProvider(OpenAIResponsesProvider):
    def __init__(self, provider_id: str, base_url: str, token_provider: ChatGPTCodexOAuthTokenProvider, timeout: float | None = None):
        super().__init__(provider_id, base_url, api_key="", timeout=timeout)
        self._token_provider = token_provider

    def _url(self, path: str) -> str:
        return f"{self.base_url.rstrip('/')}{path}"

    def _provider_label(self) -> str:
        return "ChatGPT account"

    async def _bearer_token(self) -> str:
        return await self._token_provider.access_token()

    def _request_retry_attempts(self) -> int:
        return 4

    def _request_retry_delay_s(self, attempt: int) -> float:
        return min(0.75 * (2 ** attempt), 3.0)

    def _payload(self, **kwargs: Any) -> dict[str, Any]:
        payload = super()._payload(**kwargs)
        payload["stream"] = True
        return payload

    @staticmethod
    def _response_from_sse(body: str, *, fallback_model: str) -> dict[str, Any]:
        completed: dict[str, Any] | None = None
        output_items: list[dict[str, Any]] = []
        text_parts: list[str] = []
        for line in body.splitlines():
            if not line.startswith("data:"):
                continue
            raw = line[5:].strip()
            if not raw or raw == "[DONE]":
                continue
            try:
                event = json.loads(raw)
            except json.JSONDecodeError:
                continue
            if not isinstance(event, dict):
                continue
            event_type = str(event.get("type") or "")
            if event_type in {"response.failed", "error"}:
                error = event.get("error") if isinstance(event.get("error"), dict) else {}
                message = str(error.get("message") or event.get("message") or "ChatGPT account response failed")
                raise ChatGPTAccountOAuthError(message)
            if event_type == "response.completed" and isinstance(event.get("response"), dict):
                completed = event["response"]
                continue
            if event_type == "response.output_item.done" and isinstance(event.get("item"), dict):
                output_items.append(event["item"])
                continue
            if event_type in {"response.output_text.delta", "response.text.delta"}:
                delta = event.get("delta")
                if isinstance(delta, str):
                    text_parts.append(delta)
        if completed is not None:
            if output_items and not completed.get("output"):
                completed = {**completed, "output": output_items}
            return completed
        if output_items:
            return {"model": fallback_model, "status": "completed", "output": output_items}
        if text_parts:
            return {
                "model": fallback_model,
                "status": "completed",
                "output": [{
                    "type": "message",
                    "role": "assistant",
                    "content": [{"type": "output_text", "text": "".join(text_parts)}],
                }],
            }
        raise ChatGPTAccountOAuthError("ChatGPT account stream completed without response output")

    async def complete(
        self,
        *,
        system: str,
        messages: list[Any],
        model: str,
        effort: str = "high",
        speed: str = "standard",
        tools: list[dict[str, Any]] | None = None,
    ):
        client = self._ensure_client()
        requested_effort = self._effort(effort)
        payload = self._payload(
            system=system,
            messages=messages,
            model=model,
            effort=requested_effort,
            tools=tools,
        )
        started = time.perf_counter()
        try:
            resp = await self._post_response_with_retry(client, payload)
        except httpx.HTTPStatusError as exc:
            detail = exc.response.text[:800]
            raise RuntimeError(f"{self._provider_label()} Responses provider error {exc.response.status_code}: {detail}") from exc
        except httpx.RequestError as exc:
            raise RuntimeError(f"{self._provider_label()} Responses provider request failed: {type(exc).__name__}: {exc}") from exc
        generation_ms = int((time.perf_counter() - started) * 1000)
        data = self._response_from_sse(resp.text, fallback_model=model)
        result = self._result_from_response(
            data,
            fallback_model=model,
            effort=requested_effort,
            generation_ms=generation_ms,
        )
        if not result.tool_calls and result.text.strip() == "(ไม่มีเนื้อหาตอบกลับ)":
            raise RuntimeError(f"{self._provider_label()} Responses provider returned empty output")
        result.meta["wireApi"] = "chatgpt-codex-responses"
        result.meta["stream"] = True
        return result

    async def stream_complete(
        self,
        *,
        system: str,
        messages: list[Any],
        model: str,
        effort: str = "high",
        speed: str = "standard",
        tools: list[dict[str, Any]] | None = None,
    ):
        async for event in super().stream_complete(
            system=system,
            messages=messages,
            model=model,
            effort=effort,
            speed=speed,
            tools=tools,
        ):
            if event.kind == "message_stop" and event.result is not None:
                event.result.meta["wireApi"] = "chatgpt-codex-responses"
                event.result.meta["stream"] = True
            yield event


def _pkce_pair() -> tuple[str, str]:
    verifier = _b64url(secrets.token_bytes(64))
    challenge = _b64url(hashlib.sha256(verifier.encode("ascii")).digest())
    return verifier, challenge


def _authorization_url(*, state: str, challenge: str, originator: str = "openclaw") -> str:
    params = {
        "response_type": "code",
        "client_id": CLIENT_ID,
        "redirect_uri": REDIRECT_URI,
        "scope": SCOPE,
        "code_challenge": challenge,
        "code_challenge_method": "S256",
        "state": state,
        "id_token_add_organizations": "true",
        "codex_cli_simplified_flow": "true",
        "originator": originator,
    }
    return f"{AUTHORIZE_URL}?{urllib.parse.urlencode(params)}"


def _public_login_state() -> dict[str, Any] | None:
    with _ACTIVE_LOGIN_LOCK:
        if not _ACTIVE_LOGIN:
            return None
        return {key: value for key, value in _ACTIVE_LOGIN.items() if key not in {"verifier"}}


def start_chatgpt_oauth_login_session(settings: Settings, *, timeout_s: float = 300.0) -> dict[str, Any]:
    """Start a background OAuth callback listener for the UI flow."""
    global _ACTIVE_LOGIN
    timeout_s = max(30.0, min(float(timeout_s or 300.0), 900.0))
    existing = _public_login_state()
    if existing and existing.get("status") == "pending" and int(existing.get("expiresAt") or 0) > _now_ms():
        return existing

    verifier, challenge = _pkce_pair()
    state = secrets.token_hex(16)
    auth_url = _authorization_url(state=state, challenge=challenge)
    started_at = _now_ms()
    session: dict[str, Any] = {
        "id": state,
        "status": "pending",
        "authorizationUrl": auth_url,
        "redirectUri": REDIRECT_URI,
        "startedAt": started_at,
        "expiresAt": started_at + min(int(timeout_s * 1000), LOGIN_SESSION_TTL_MS),
        "verifier": verifier,
    }

    class CallbackHandler(BaseHTTPRequestHandler):
        def log_message(self, format: str, *args: Any) -> None:  # noqa: A002
            return

        def do_GET(self) -> None:  # noqa: N802
            parsed = urllib.parse.urlparse(self.path)
            params = urllib.parse.parse_qs(parsed.query)
            if parsed.path != CALLBACK_PATH:
                self.send_response(404)
                self.end_headers()
                return
            error: str | None = None
            if params.get("state", [""])[0] != state:
                error = "OAuth state mismatch"
            elif params.get("error"):
                error = params.get("error_description", params.get("error", ["OAuth error"]))[0]
            code = params.get("code", [""])[0]
            if not error and not code:
                error = "OAuth callback did not include a code"
            ok = False
            if error:
                with _ACTIVE_LOGIN_LOCK:
                    session.update({"status": "failed", "error": error, "completedAt": _now_ms()})
            else:
                try:
                    creds = _exchange_authorization_code(
                        code,
                        verifier,
                        timeout=settings.provider_request_timeout_s,
                    )
                    store_path = _resolve_store_path(settings)
                    with _credential_file_lock(store_path):
                        _write_json_file(store_path, creds)
                    status = chatgpt_oauth_status(settings)
                    with _ACTIVE_LOGIN_LOCK:
                        session.update({
                            "status": "succeeded",
                            "completedAt": _now_ms(),
                            "accountId": status.get("accountId"),
                            "email": status.get("email"),
                            "chatgptPlanType": status.get("chatgptPlanType"),
                        })
                    ok = True
                except Exception as exc:
                    with _ACTIVE_LOGIN_LOCK:
                        session.update({"status": "failed", "error": str(exc), "completedAt": _now_ms()})
            body = (
                "<html><body><h1>ATRIUM ChatGPT login complete</h1>"
                "<p>You can close this tab and return to ATRIUM.</p></body></html>"
                if ok
                else "<html><body><h1>ATRIUM ChatGPT login failed</h1><p>Return to ATRIUM.</p></body></html>"
            )
            self.send_response(200 if ok else 400)
            self.send_header("Content-Type", "text/html; charset=utf-8")
            self.end_headers()
            self.wfile.write(body.encode("utf-8"))

    try:
        server = ThreadingHTTPServer((CALLBACK_HOST, CALLBACK_PORT), CallbackHandler)
    except OSError as exc:
        raise ChatGPTAccountOAuthError(
            f"Cannot bind OAuth callback {REDIRECT_URI}; another Codex/OpenClaw login may be using port {CALLBACK_PORT}"
        ) from exc
    server.timeout = 1
    with _ACTIVE_LOGIN_LOCK:
        _ACTIVE_LOGIN = session

    def serve() -> None:
        try:
            while _now_ms() < int(session["expiresAt"]):
                with _ACTIVE_LOGIN_LOCK:
                    if session.get("status") != "pending":
                        break
                server.handle_request()
            with _ACTIVE_LOGIN_LOCK:
                if session.get("status") == "pending":
                    session.update({"status": "expired", "error": "Timed out waiting for ChatGPT OAuth callback", "completedAt": _now_ms()})
        finally:
            server.server_close()

    threading.Thread(target=serve, name="atrium-chatgpt-oauth", daemon=True).start()
    return _public_login_state() or {}


def chatgpt_oauth_login_state() -> dict[str, Any] | None:
    return _public_login_state()


def _exchange_authorization_code(code: str, verifier: str, *, timeout: float | None = None) -> dict[str, Any]:
    try:
        resp = httpx.post(
            TOKEN_URL,
            data={
                "grant_type": "authorization_code",
                "client_id": CLIENT_ID,
                "code": code,
                "code_verifier": verifier,
                "redirect_uri": REDIRECT_URI,
            },
            headers={"Content-Type": "application/x-www-form-urlencoded"},
            timeout=timeout or 60.0,
        )
        resp.raise_for_status()
    except httpx.HTTPStatusError as exc:
        detail = exc.response.text[:800]
        raise ChatGPTAccountOAuthError(f"ChatGPT OAuth code exchange failed {exc.response.status_code}: {detail}") from exc
    except httpx.RequestError as exc:
        raise ChatGPTAccountOAuthError(f"ChatGPT OAuth code exchange request failed: {type(exc).__name__}: {exc}") from exc
    return _credentials_from_token_response(resp.json())


def run_chatgpt_oauth_login(
    settings: Settings,
    *,
    open_browser: bool = True,
    timeout_s: float = 300.0,
) -> dict[str, Any]:
    verifier, challenge = _pkce_pair()
    state = secrets.token_hex(16)
    auth_url = _authorization_url(state=state, challenge=challenge)
    result: dict[str, str] = {}

    class CallbackHandler(BaseHTTPRequestHandler):
        def log_message(self, format: str, *args: Any) -> None:  # noqa: A002
            return

        def do_GET(self) -> None:  # noqa: N802
            parsed = urllib.parse.urlparse(self.path)
            params = urllib.parse.parse_qs(parsed.query)
            if parsed.path != CALLBACK_PATH:
                self.send_response(404)
                self.end_headers()
                return
            if params.get("state", [""])[0] != state:
                result["error"] = "OAuth state mismatch"
            elif params.get("error"):
                result["error"] = params.get("error_description", params.get("error", ["OAuth error"]))[0]
            elif params.get("code"):
                result["code"] = params["code"][0]
            else:
                result["error"] = "OAuth callback did not include a code"
            ok = "code" in result
            body = (
                "<html><body><h1>ATRIUM ChatGPT login complete</h1>"
                "<p>You can close this tab and return to the terminal.</p></body></html>"
                if ok
                else "<html><body><h1>ATRIUM ChatGPT login failed</h1><p>Return to the terminal.</p></body></html>"
            )
            self.send_response(200 if ok else 400)
            self.send_header("Content-Type", "text/html; charset=utf-8")
            self.end_headers()
            self.wfile.write(body.encode("utf-8"))

    try:
        server = ThreadingHTTPServer((CALLBACK_HOST, CALLBACK_PORT), CallbackHandler)
    except OSError as exc:
        raise ChatGPTAccountOAuthError(
            f"Cannot bind OAuth callback {REDIRECT_URI}; another Codex/OpenClaw login may be using port {CALLBACK_PORT}"
        ) from exc
    server.timeout = 1
    print(f"Open this URL to sign in with ChatGPT:\n{auth_url}\n")
    if open_browser:
        webbrowser.open(auth_url)
    deadline = time.time() + timeout_s
    try:
        while time.time() < deadline and not result:
            server.handle_request()
    finally:
        server.server_close()
    if not result:
        raise ChatGPTAccountOAuthError("Timed out waiting for ChatGPT OAuth callback")
    if result.get("error"):
        raise ChatGPTAccountOAuthError(result["error"])
    creds = _exchange_authorization_code(result["code"], verifier, timeout=settings.provider_request_timeout_s)
    store_path = _resolve_store_path(settings)
    with _credential_file_lock(store_path):
        _write_json_file(store_path, creds)
    return chatgpt_oauth_status(settings)
