"""Runtime configuration.

Reads from environment / `.env`. Storage and embeddings have local defaults,
but LLM work always requires a live provider token.
Add DATABASE_URL / VOYAGE_API_KEY to upgrade each layer independently — the
"best-fit per layer" principle from the plan.
"""
from __future__ import annotations

import os
from functools import lru_cache
from pathlib import Path
from typing import Any

from pydantic import AliasChoices, Field
from pydantic_settings import BaseSettings, SettingsConfigDict


def _unquote_env_value(value: str) -> str:
    value = value.strip()
    if len(value) >= 2 and value[0] == value[-1] and value[0] in {"'", '"'}:
        return value[1:-1]
    return value


def _read_dotenv_values(path: Path) -> dict[str, str]:
    if not path.exists():
        return {}
    values: dict[str, str] = {}
    for raw in path.read_text(encoding="utf-8").splitlines():
        line = raw.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        if line.startswith("export "):
            line = line[7:].lstrip()
        key, value = line.split("=", 1)
        key = key.strip()
        if not key:
            continue
        values[key] = _unquote_env_value(value)
    return values


class Settings(BaseSettings):
    model_config = SettingsConfigDict(
        env_prefix="ATRIUM_",
        env_file=".env",
        env_file_encoding="utf-8",
        extra="ignore",
        case_sensitive=False,
    )

    # ---- server ----
    company_name: str = "ATRIUM"
    host: str = "127.0.0.1"
    port: int = 8787
    cors_origins: str = "http://localhost:5173,http://127.0.0.1:5173,http://localhost:5174,http://127.0.0.1:5174"

    # ---- storage ----
    # Empty -> SQLite file under data_dir. Set to a postgresql+asyncpg:// URL for the full stack.
    database_url: str = ""
    data_dir: Path = Path("./data")
    graph_backend: str = "auto"  # auto | kuzu | relational
    seed_on_start: bool = True

    # ---- provider (direct Anthropic Claude) ----
    anthropic_auth_token: str = Field(
        default="",
        validation_alias=AliasChoices("ATRIUM_ANTHROPIC_AUTH_TOKEN"),
    )
    anthropic_base_url: str = Field(
        default="https://api.anthropic.com",
        validation_alias=AliasChoices("ATRIUM_ANTHROPIC_BASE_URL"),
    )
    # ---- provider (ChatGPT account OAuth / Codex responses backend) ----
    # This is intentionally app-scoped and stored separately from Codex CLI.
    chatgpt_account_base_url: str = Field(
        default="https://chatgpt.com/backend-api/codex",
        validation_alias=AliasChoices("ATRIUM_CHATGPT_ACCOUNT_BASE_URL", "CHATGPT_ACCOUNT_BASE_URL"),
    )
    chatgpt_account_oauth_store: Path = Path("./data/auth/chatgpt-account.json")
    chatgpt_account_access_token: str = Field(
        default="",
        validation_alias=AliasChoices("ATRIUM_CHATGPT_ACCOUNT_ACCESS_TOKEN", "CHATGPT_ACCOUNT_ACCESS_TOKEN"),
    )
    chatgpt_account_refresh_token: str = Field(
        default="",
        validation_alias=AliasChoices("ATRIUM_CHATGPT_ACCOUNT_REFRESH_TOKEN", "CHATGPT_ACCOUNT_REFRESH_TOKEN"),
    )
    chatgpt_account_expires_at: str = Field(
        default="",
        validation_alias=AliasChoices("ATRIUM_CHATGPT_ACCOUNT_EXPIRES_AT", "CHATGPT_ACCOUNT_EXPIRES_AT"),
    )
    # ---- provider (public OpenAI platform APIs: audio, future TTS/realtime fallbacks) ----
    # Keep the ATRIUM-scoped name primary. Generic OPENAI_API_KEY is accepted from
    # system/.env in model_post_init, not from arbitrary parent shells.
    openai_api_key: str = Field(
        default="",
        validation_alias=AliasChoices("ATRIUM_OPENAI_API_KEY"),
    )
    openai_base_url: str = Field(
        default="https://api.openai.com/v1",
        validation_alias=AliasChoices("ATRIUM_OPENAI_BASE_URL"),
    )
    audio_transcription_enabled: bool = Field(
        default=True,
        validation_alias=AliasChoices("ATRIUM_AUDIO_TRANSCRIPTION_ENABLED"),
    )
    audio_transcription_auto_on_upload: bool = Field(
        default=True,
        validation_alias=AliasChoices("ATRIUM_AUDIO_TRANSCRIPTION_AUTO_ON_UPLOAD"),
    )
    audio_transcription_provider: str = Field(
        default="openai",
        validation_alias=AliasChoices("ATRIUM_AUDIO_TRANSCRIPTION_PROVIDER"),
    )
    audio_transcription_model: str = Field(
        default="gpt-4o-transcribe",
        validation_alias=AliasChoices("ATRIUM_AUDIO_TRANSCRIPTION_MODEL"),
    )
    audio_transcription_base_url: str = Field(
        default="",
        validation_alias=AliasChoices("ATRIUM_AUDIO_TRANSCRIPTION_BASE_URL"),
    )
    audio_transcription_timeout_s: float = Field(
        default=120.0,
        validation_alias=AliasChoices("ATRIUM_AUDIO_TRANSCRIPTION_TIMEOUT_S"),
    )
    audio_transcription_max_bytes: int = Field(
        default=20 * 1024 * 1024,
        validation_alias=AliasChoices("ATRIUM_AUDIO_TRANSCRIPTION_MAX_BYTES"),
    )
    audio_transcription_auth_order: str = Field(
        default="api_key",
        validation_alias=AliasChoices("ATRIUM_AUDIO_TRANSCRIPTION_AUTH_ORDER"),
    )
    audio_transcription_language: str = Field(
        default="",
        validation_alias=AliasChoices("ATRIUM_AUDIO_TRANSCRIPTION_LANGUAGE"),
    )
    audio_transcription_prompt: str = Field(
        default="",
        validation_alias=AliasChoices("ATRIUM_AUDIO_TRANSCRIPTION_PROMPT"),
    )
    # ---- provider (Claude account through Claude Code CLI) ----
    claude_code_command: str = Field(
        default="claude",
        validation_alias=AliasChoices("ATRIUM_CLAUDE_CODE_COMMAND", "CLAUDE_CODE_COMMAND"),
    )
    claude_code_timeout_s: float | None = Field(
        default=300.0,
        validation_alias=AliasChoices("ATRIUM_CLAUDE_CODE_TIMEOUT_S", "CLAUDE_CODE_TIMEOUT_S"),
    )
    image_generation_api_key: str = Field(
        default="",
        validation_alias=AliasChoices("ATRIUM_IMAGE_GENERATION_API_KEY", "IMAGE_GENERATION_API_KEY"),
    )
    image_generation_base_url: str = Field(
        default="https://api.openai.com/v1",
        validation_alias=AliasChoices("ATRIUM_IMAGE_GENERATION_BASE_URL", "IMAGE_GENERATION_BASE_URL"),
    )
    image_generation_timeout_s: float | None = Field(
        default=600.0,
        validation_alias=AliasChoices("ATRIUM_IMAGE_GENERATION_TIMEOUT_S", "IMAGE_GENERATION_TIMEOUT_S"),
    )
    image_generation_worker_concurrency: int = Field(
        default=3,
        validation_alias=AliasChoices("ATRIUM_IMAGE_GENERATION_WORKER_CONCURRENCY", "IMAGE_GENERATION_WORKER_CONCURRENCY"),
    )
    # <= 0 means ATRIUM does not impose a byte cap before sending an image to
    # a vision-capable provider; the provider may still reject oversized input.
    image_context_max_bytes: int = 0
    image_context_max_images: int = 2
    disable_nonessential_traffic: bool = Field(
        default=True,
        validation_alias=AliasChoices(
            "CLAUDE_CODE_DISABLE_NONESSENTIAL_TRAFFIC",
            "ATRIUM_DISABLE_NONESSENTIAL_TRAFFIC",
        ),
    )
    # Provider calls must be bounded so one slow upstream response cannot block
    # the durable job worker for the full engine timeout. Set <= 0 only for
    # deliberate local debugging of very long model turns.
    request_timeout_s: float | None = 300.0

    # ---- agent runtime (native) ----
    # ATRIUM owns provider turns, tool loops, memory, checkpoints, and jobs in-process.
    agent_backend: str = Field(
        default="native",
        validation_alias=AliasChoices("ATRIUM_AGENT_BACKEND", "AGENT_BACKEND"),
    )
    runtime_degraded_queue: bool = True
    runtime_degraded_retry_s: float = 30.0
    runtime_provider_fallback_enabled: bool = True

    # ---- embeddings ----
    # Mode: auto preserves the legacy priority (local Ollama → Voyage → hash).
    # Choose openai explicitly when the owner wants OpenAI Platform embeddings.
    embedding_provider: str = Field(
        default="auto",
        validation_alias=AliasChoices("ATRIUM_EMBEDDING_PROVIDER", "EMBEDDING_PROVIDER"),
    )
    ollama_base_url: str = Field(
        default="http://127.0.0.1:11434",
        validation_alias=AliasChoices("ATRIUM_OLLAMA_BASE_URL", "OLLAMA_HOST"),
    )
    ollama_embedding_model: str = "bge-m3"
    openai_embedding_api_key: str = Field(
        default="",
        validation_alias=AliasChoices("ATRIUM_OPENAI_EMBEDDING_API_KEY"),
    )
    openai_embedding_base_url: str = Field(
        default="",
        validation_alias=AliasChoices("ATRIUM_OPENAI_EMBEDDING_BASE_URL"),
    )
    openai_embedding_model: str = "text-embedding-3-large"
    # Keep the default compatible with the existing bge-m3/pgvector dimension.
    # Set <= 0 to request the OpenAI model's native dimension.
    openai_embedding_dimensions: int = 1024
    voyage_api_key: str = Field(
        default="",
        validation_alias=AliasChoices("VOYAGE_API_KEY", "ATRIUM_VOYAGE_API_KEY"),
    )
    voyage_model: str = "voyage-3.5"
    voyage_base_url: str = "https://api.voyageai.com/v1"
    embedding_dim: int = 1024  # bge-m3 / pgvector default; hash fallback uses same dim when configured

    # ---- durable blobs (v2) ----
    object_store_enabled: bool = True
    # <= 0 means no ATRIUM-side hard upload cap. Preview extraction and model
    # context still apply their own sampling/token/provider constraints.
    max_upload_bytes: int = 0

    # ---- backups (v2 Phase 0 durability) ----
    backup_dir: Path = Path("./data/backups")
    backup_offsite_dir: str = ""
    backup_require_offsite: bool = False
    backup_max_age_hours: float = 30.0
    backup_use_launchd_env: bool = True

    # ---- entitlements (host OS / external; audit only, not approval gates) ----
    entitlement_host_shell: bool = True
    entitlement_host_filesystem: bool = True
    entitlement_browser_automation: bool = True
    entitlement_desktop_automation: bool = True
    entitlement_external_send: bool = True
    entitlement_credentials: bool = True
    host_bridge_parity_report_path: Path = Path("./data/host-bridge-parity-report.json")
    host_bridge_parity_report_max_age_hours: float = 24.0

    # ---- eval harness (v2 regression scoring; not an approval gate) ----
    eval_harness_enabled: bool = True
    eval_golden_tasks_path: str = ""

    # ---- MCP gateway ----
    # Empty uses ATRIUM's local MCP fallback (status/capability calls plus
    # local CLIs/synced folders where available). When set, mcp.call posts to
    # {gateway}/call unless the URL already ends with /call.
    mcp_gateway_url: str = ""
    mcp_gateway_token: str = Field(
        default="",
        validation_alias=AliasChoices("MCP_GATEWAY_TOKEN", "ATRIUM_MCP_GATEWAY_TOKEN"),
    )
    mcp_gateway_token_keychain_service: str = Field(
        default="",
        validation_alias=AliasChoices("MCP_GATEWAY_TOKEN_KEYCHAIN_SERVICE", "ATRIUM_MCP_GATEWAY_TOKEN_KEYCHAIN_SERVICE"),
    )
    mcp_gateway_token_keychain_account: str = Field(
        default="atrium",
        validation_alias=AliasChoices("MCP_GATEWAY_TOKEN_KEYCHAIN_ACCOUNT", "ATRIUM_MCP_GATEWAY_TOKEN_KEYCHAIN_ACCOUNT"),
    )
    mcp_enabled_servers: str = ""
    mcp_timeout_s: float = 20.0

    # ---- Telegram gateway ----
    # The executive can connect a user's Telegram bot token from chat through
    # the connect_telegram_gateway tool. If a gateway URL is configured, the
    # tool forwards the verified bot token to that gateway; otherwise it stores
    # a local auth file and a redacted public status record.
    telegram_gateway_url: str = Field(
        default="",
        validation_alias=AliasChoices("ATRIUM_TELEGRAM_GATEWAY_URL", "TELEGRAM_GATEWAY_URL"),
    )
    telegram_gateway_token: str = Field(
        default="",
        validation_alias=AliasChoices("ATRIUM_TELEGRAM_GATEWAY_TOKEN", "TELEGRAM_GATEWAY_TOKEN"),
    )
    telegram_gateway_public_base_url: str = Field(
        default="",
        validation_alias=AliasChoices("ATRIUM_TELEGRAM_PUBLIC_BASE_URL", "TELEGRAM_PUBLIC_BASE_URL"),
    )
    telegram_gateway_timeout_s: float = 20.0

    # ---- key-free web research tools ----
    # Search defaults to DuckDuckGo's HTML endpoint with Bing RSS as a fallback,
    # and can prefer a self-hosted SearXNG instance when
    # ATRIUM_WEB_SEARCH_SEARXNG_URL is set.
    web_search_provider: str = Field(
        default="auto",
        validation_alias=AliasChoices("ATRIUM_WEB_SEARCH_PROVIDER", "WEB_SEARCH_PROVIDER"),
    )
    web_search_searxng_url: str = Field(
        default="",
        validation_alias=AliasChoices(
            "ATRIUM_WEB_SEARCH_SEARXNG_URL",
            "ATRIUM_SEARXNG_URL",
            "SEARXNG_URL",
            "SEARXNG_BASE_URL",
        ),
    )
    web_search_timeout_s: float = 12.0
    web_search_cache_ttl_s: int = 15 * 60
    web_fetch_timeout_s: float = 15.0
    web_fetch_max_bytes: int = 750_000
    web_fetch_max_chars: int = 20_000

    # ---- company / guardrails ----
    daily_cap_usd: float = 500.0
    executive_daily_budget_usd: float = Field(
        default=500.0,
        validation_alias=AliasChoices("ATRIUM_EXECUTIVE_DAILY_BUDGET_USD", "ATRIUM_DEPARTMENT_DAILY_CAP_USD"),
    )
    permission_mode: str = "full_auto"
    sandbox_local_fallback: bool = Field(
        default=True,
        validation_alias=AliasChoices("ATRIUM_SANDBOX_LOCAL_FALLBACK", "ATRIUM_ALLOW_LOCAL_SANDBOX_FALLBACK"),
    )
    rag_top_k: int = 5
    # <= 0 disables the app-level hard cap; the provider/context window then becomes the limiting layer.
    chat_max_input_chars: int = 0
    # <= 0 disables the UI/API warning threshold for estimated input tokens.
    chat_recommended_input_tokens: int = 0
    # Recent chat messages sent directly to the model before long-term memory/RAG.
    # Raw thread history remains stored separately; this only controls prompt assembly.
    # <= 0 sends every fetched thread message into prompt assembly.
    chat_history_messages: int = 0
    # Recent completed tool runs summarized into chat context. Full tool payloads
    # remain in agent_tool_run records and are fetched only when needed.
    chat_tool_memory_runs: int = 20
    # Full chat/task history remains durable in storage. The live state snapshot
    # is intentionally compact because it is sent over REST and WebSocket on
    # every reconnect/state refresh.
    state_thread_count: int = 32
    state_thread_messages: int = 20
    state_task_count: int = 300
    state_approval_count: int = 80
    state_task_detail_chars: int = 320
    state_task_handoff_reason_chars: int = 180
    state_task_handoff_messages: int = 3
    state_task_handoff_message_chars: int = 320
    state_task_log_entries: int = 5
    state_task_log_chars: int = 160
    state_task_deliverable_chars: int = 12_000
    # Auto compaction is primarily based on estimated prompt/context tokens.
    # Keep the legacy message-count trigger opt-in only; <= 0 disables it.
    compact_message_threshold: int = 0
    compact_claude_context_tokens: int = 180_000
    compact_gpt_context_tokens: int = 230_000
    compact_context_window_ratio: float = 0.8
    # <= 0 disables the app-level per-user chat rate gate.
    chat_rate_limit_per_minute: int = 0
    chat_budget_warning_ratio: float = 0.85
    autonomy_enabled: bool = True
    engine_enabled: bool = True
    tick_seconds: float = 2.0
    # A single queued job should not be able to freeze the always-on engine
    # forever. Long model turns can still run for many minutes; exceeding this
    # marks the job failed so the next tick can continue with other work.
    engine_job_timeout_s: float = 1800.0
    engine_tick_timeout_s: float = 1800.0
    engine_stale_after_s: float = 600.0
    # Visibility-only threshold for surfacing active jobs that have retried often.
    # This is not a max-attempt cap and never stops/rejects job execution.
    engine_retry_visibility_attempts: int = 5
    # Timeout recovery delay before non-chat jobs are retried. This is not a cap.
    engine_timeout_retry_delay_s: float = 60.0
    chat_reply_worker_concurrency: int = Field(
        default=5,
        validation_alias=AliasChoices("ATRIUM_CHAT_REPLY_WORKER_CONCURRENCY", "CHAT_REPLY_WORKER_CONCURRENCY"),
    )
    department_worker_concurrency: int = Field(
        default=5,
        validation_alias=AliasChoices("ATRIUM_DEPARTMENT_WORKER_CONCURRENCY", "DEPARTMENT_WORKER_CONCURRENCY"),
    )
    max_handoff_depth: int = 5
    max_consult_rounds: int = 3
    max_handoff_rejects: int = 2
    # <= 0 disables the app-level runtime tool-round ceiling for one model turn.
    runtime_max_tool_rounds: int = 0

    # ---- learning loop (v2 Phase 2) ----
    reflection_enabled: bool = True
    consolidation_enabled: bool = True
    consolidation_interval_hours: float = 24.0

    # ---- org (v2 Phase 3) ----
    dynamic_routing_enabled: bool = True
    dynamic_route_min_score: float = 0.12
    org_lifecycle_enabled: bool = True
    org_lifecycle_interval_hours: float = 12.0
    org_autospawn_enabled: bool = True
    org_automerge_enabled: bool = True
    org_idle_retire_hours: float = 168.0  # 7 days idle before retire candidate

    def model_post_init(self, __context: Any) -> None:
        """Keep local provider credentials pinned to this app's `.env`.

        Generic OpenAI environment variables are commonly set by other tools in
        the parent shell. ATRIUM accepts generic OpenAI keys only when they are
        explicitly written to `.env`, while app-scoped `ATRIUM_*` process env
        still wins.
        """
        dotenv = _read_dotenv_values(Path(".env"))
        if not dotenv:
            return
        if "ATRIUM_ANTHROPIC_AUTH_TOKEN" in os.environ:
            self.anthropic_auth_token = os.environ["ATRIUM_ANTHROPIC_AUTH_TOKEN"]
        else:
            auth_token = dotenv.get("ATRIUM_ANTHROPIC_AUTH_TOKEN")
            if auth_token:
                self.anthropic_auth_token = auth_token
        if "ATRIUM_ANTHROPIC_BASE_URL" not in os.environ:
            anthropic_base_url = dotenv.get("ATRIUM_ANTHROPIC_BASE_URL")
            if anthropic_base_url:
                self.anthropic_base_url = anthropic_base_url
        if "ATRIUM_OPENAI_API_KEY" in os.environ:
            self.openai_api_key = os.environ["ATRIUM_OPENAI_API_KEY"]
        else:
            openai_key = dotenv.get("ATRIUM_OPENAI_API_KEY") or dotenv.get("OPENAI_API_KEY")
            if openai_key:
                self.openai_api_key = openai_key
        if "ATRIUM_OPENAI_BASE_URL" not in os.environ:
            public_openai_base_url = dotenv.get("ATRIUM_OPENAI_BASE_URL") or dotenv.get("OPENAI_BASE_URL")
            if public_openai_base_url:
                self.openai_base_url = public_openai_base_url
        if "ATRIUM_OPENAI_EMBEDDING_API_KEY" in os.environ:
            self.openai_embedding_api_key = os.environ["ATRIUM_OPENAI_EMBEDDING_API_KEY"]
        else:
            openai_embedding_key = (
                dotenv.get("ATRIUM_OPENAI_EMBEDDING_API_KEY")
                or dotenv.get("OPENAI_EMBEDDING_API_KEY")
            )
            if openai_embedding_key:
                self.openai_embedding_api_key = openai_embedding_key
        if "ATRIUM_OPENAI_EMBEDDING_BASE_URL" not in os.environ:
            openai_embedding_base_url = (
                dotenv.get("ATRIUM_OPENAI_EMBEDDING_BASE_URL")
                or dotenv.get("OPENAI_EMBEDDING_BASE_URL")
            )
            if openai_embedding_base_url:
                self.openai_embedding_base_url = openai_embedding_base_url
        if "ATRIUM_AUDIO_TRANSCRIPTION_BASE_URL" not in os.environ:
            audio_base_url = dotenv.get("ATRIUM_AUDIO_TRANSCRIPTION_BASE_URL") or dotenv.get("OPENAI_AUDIO_BASE_URL")
            if audio_base_url:
                self.audio_transcription_base_url = audio_base_url
        if "ATRIUM_IMAGE_GENERATION_API_KEY" in os.environ:
            self.image_generation_api_key = os.environ["ATRIUM_IMAGE_GENERATION_API_KEY"]
        else:
            image_key = dotenv.get("ATRIUM_IMAGE_GENERATION_API_KEY") or dotenv.get("IMAGE_GENERATION_API_KEY")
            if image_key:
                self.image_generation_api_key = image_key
        if "ATRIUM_IMAGE_GENERATION_BASE_URL" not in os.environ:
            image_base_url = (
                dotenv.get("ATRIUM_IMAGE_GENERATION_BASE_URL")
                or dotenv.get("IMAGE_GENERATION_BASE_URL")
            )
            if image_base_url:
                self.image_generation_base_url = image_base_url

    # ---- derived ----
    @property
    def cors_list(self) -> list[str]:
        return [o.strip() for o in self.cors_origins.split(",") if o.strip()]

    @property
    def effective_database_url(self) -> str:
        if self.database_url:
            return self.database_url
        self.data_dir.mkdir(parents=True, exist_ok=True)
        db_path = (self.data_dir / "atrium.db").resolve()
        return f"sqlite+aiosqlite:///{db_path}"

    @property
    def is_postgres(self) -> bool:
        return self.effective_database_url.startswith("postgresql")

    @property
    def workspace_dir(self) -> Path:
        p = self.data_dir / "workspace"
        p.mkdir(parents=True, exist_ok=True)
        return p

    @property
    def provider_request_timeout_s(self) -> float | None:
        if self.request_timeout_s is None or self.request_timeout_s <= 0:
            return None
        return self.request_timeout_s

    @property
    def object_store_dir(self) -> Path:
        p = self.data_dir / "object_store"
        p.mkdir(parents=True, exist_ok=True)
        return p

    @property
    def eval_golden_tasks_file(self) -> Path | None:
        raw = (self.eval_golden_tasks_path or "").strip()
        if raw:
            return Path(raw).expanduser()
        default = Path(__file__).resolve().parents[2] / "ops" / "eval" / "golden_tasks.json"
        return default if default.exists() else None

    @property
    def use_external_agent_runtime(self) -> bool:
        return False

    @property
    def agent_backend_mode(self) -> str:
        mode = self.agent_backend.strip().lower()
        return "native" if mode in {"", "native", "engine", "atrium"} else "native"

    @property
    def live_embeddings(self) -> bool:
        mode = self.embedding_provider_mode
        return mode == "voyage" or (mode == "auto" and bool(self.voyage_api_key))

    @property
    def prefer_ollama_embeddings(self) -> bool:
        mode = self.embedding_provider_mode
        return mode in {"auto", "local", "ollama"} and bool(self.ollama_base_url.strip())

    @property
    def embedding_provider_mode(self) -> str:
        mode = (self.embedding_provider or "auto").strip().lower()
        aliases = {
            "ollama": "local",
            "local_ai": "local",
            "local-ai": "local",
            "openai_platform": "openai",
            "none": "hash",
            "offline": "hash",
        }
        mode = aliases.get(mode, mode)
        return mode if mode in {"auto", "local", "openai", "voyage", "hash"} else "auto"

    @property
    def effective_openai_embedding_api_key(self) -> str:
        return (self.openai_embedding_api_key or self.openai_api_key or "").strip()

    @property
    def effective_openai_embedding_base_url(self) -> str:
        return (self.openai_embedding_base_url or self.openai_base_url or "https://api.openai.com/v1").strip()

    @property
    def effective_openai_embedding_dimensions(self) -> int:
        return max(0, int(self.openai_embedding_dimensions or 0))

    @property
    def openai_embeddings_enabled(self) -> bool:
        return self.embedding_provider_mode == "openai"

    @property
    def configured_embedding_label(self) -> str:
        mode = self.embedding_provider_mode
        if mode == "openai":
            dim = self.effective_openai_embedding_dimensions
            suffix = f":{dim}" if dim else ""
            return f"openai:{self.openai_embedding_model}{suffix}"
        if mode == "voyage":
            return f"voyage:{self.voyage_model}"
        if mode == "hash":
            return f"hash-{self.embedding_dim}"
        if mode == "local":
            return f"ollama:{self.ollama_embedding_model}"
        if self.prefer_ollama_embeddings:
            return f"auto:ollama:{self.ollama_embedding_model}"
        if self.live_embeddings:
            return f"auto:voyage:{self.voyage_model}"
        return f"auto:hash-{self.embedding_dim}"

    @property
    def chat_max_input_char_limit(self) -> int:
        return max(0, int(self.chat_max_input_chars))

    @property
    def chat_recommended_input_token_limit(self) -> int:
        return max(0, int(self.chat_recommended_input_tokens))

    @property
    def chat_history_message_limit(self) -> int:
        return max(0, int(self.chat_history_messages))

    @property
    def chat_tool_memory_run_limit(self) -> int:
        return max(0, min(int(self.chat_tool_memory_runs), 100))

    @property
    def runtime_max_tool_round_limit(self) -> int:
        return max(0, int(self.runtime_max_tool_rounds))

    @property
    def state_thread_message_limit(self) -> int:
        return max(1, min(int(self.state_thread_messages), 60))

    @property
    def state_thread_count_limit(self) -> int:
        return max(1, min(int(self.state_thread_count), 200))

    @property
    def state_task_count_limit(self) -> int:
        return max(1, min(int(self.state_task_count), 1000))

    @property
    def state_approval_count_limit(self) -> int:
        return max(0, min(int(self.state_approval_count), 1000))

    @property
    def state_task_detail_char_limit(self) -> int:
        return max(80, min(int(self.state_task_detail_chars), 2000))

    @property
    def state_task_handoff_reason_char_limit(self) -> int:
        return max(80, min(int(self.state_task_handoff_reason_chars), 2000))

    @property
    def state_task_handoff_message_limit(self) -> int:
        return max(0, min(int(self.state_task_handoff_messages), 20))

    @property
    def state_task_handoff_message_char_limit(self) -> int:
        return max(40, min(int(self.state_task_handoff_message_chars), 4000))

    @property
    def state_task_log_entry_limit(self) -> int:
        return max(0, min(int(self.state_task_log_entries), 20))

    @property
    def state_task_log_char_limit(self) -> int:
        return max(40, min(int(self.state_task_log_chars), 1000))

    @property
    def state_task_deliverable_char_limit(self) -> int:
        return max(0, min(int(self.state_task_deliverable_chars), 30_000))


@lru_cache
def get_settings() -> Settings:
    return Settings()
