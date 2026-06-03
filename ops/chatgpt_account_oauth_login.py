#!/usr/bin/env python3
"""Create ATRIUM's app-scoped ChatGPT account OAuth profile."""
from __future__ import annotations

import argparse
import json
import os
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
SYSTEM = ROOT / "system"
sys.path.insert(0, str(SYSTEM))
os.chdir(SYSTEM)

from app.config import get_settings  # noqa: E402
from app.provider.chatgpt_oauth import ChatGPTAccountOAuthError, run_chatgpt_oauth_login  # noqa: E402


def main() -> int:
    parser = argparse.ArgumentParser(description="Log ATRIUM into ChatGPT account OAuth using the Codex PKCE flow.")
    parser.add_argument("--no-browser", action="store_true", help="Print the login URL without opening the browser.")
    parser.add_argument("--timeout-s", type=float, default=300.0, help="Seconds to wait for the OAuth callback.")
    args = parser.parse_args()
    try:
        status = run_chatgpt_oauth_login(
            get_settings(),
            open_browser=not args.no_browser,
            timeout_s=max(args.timeout_s, 30.0),
        )
    except ChatGPTAccountOAuthError as exc:
        print(json.dumps({"ok": False, "error": str(exc)}, ensure_ascii=False, indent=2))
        return 1
    safe_status = {
        "ok": True,
        "source": status.get("source"),
        "storePath": status.get("storePath"),
        "expiresAt": status.get("expiresAt"),
        "accountId": status.get("accountId"),
        "email": status.get("email"),
        "chatgptPlanType": status.get("chatgptPlanType"),
    }
    print(json.dumps(safe_status, ensure_ascii=False, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
