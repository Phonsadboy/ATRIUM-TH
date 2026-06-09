import importlib.util
import io
import json
import sys
import tempfile
import unittest
import zipfile
from contextlib import redirect_stdout
from pathlib import Path
from unittest import mock


ROOT = Path(__file__).resolve().parents[2]
CLI_PATH = ROOT / "ops" / "atrium_cli.py"
SPEC = importlib.util.spec_from_file_location("atrium_cli", CLI_PATH)
atrium_cli = importlib.util.module_from_spec(SPEC)
assert SPEC.loader is not None
sys.modules["atrium_cli"] = atrium_cli
SPEC.loader.exec_module(atrium_cli)


class AtriumCliEnvTests(unittest.TestCase):
    def test_merge_env_preserves_existing_values_and_fills_empty_defaults(self) -> None:
        merged, changed, preserved = atrium_cli.merge_env_text(
            "ATRIUM_AGENT_BACKEND=engine\n"
            "ATRIUM_DATABASE_URL=\n"
            "ATRIUM_OPENAI_API_KEY=sk-secret\n",
            {
                "ATRIUM_AGENT_BACKEND": "native",
                "ATRIUM_DATABASE_URL": "postgresql+asyncpg://atrium:atrium@127.0.0.1:5432/atrium",
                "ATRIUM_GRAPH_BACKEND": "auto",
            },
        )

        self.assertIn("ATRIUM_AGENT_BACKEND=engine", merged)
        self.assertIn("ATRIUM_DATABASE_URL=postgresql+asyncpg://atrium:atrium@127.0.0.1:5432/atrium", merged)
        self.assertIn("ATRIUM_GRAPH_BACKEND=auto", merged)
        self.assertIn("ATRIUM_OPENAI_API_KEY=sk-secret", merged)
        self.assertEqual(changed, ["ATRIUM_DATABASE_URL", "ATRIUM_GRAPH_BACKEND"])
        self.assertEqual(preserved, ["ATRIUM_AGENT_BACKEND"])

    def test_update_env_file_dry_run_does_not_write(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            env_path = Path(tmp) / ".env"
            update = atrium_cli.update_env_file(
                env_path,
                {"ATRIUM_AGENT_BACKEND": "native"},
                dry_run=True,
            )

            self.assertTrue(update.created)
            self.assertEqual(update.changed_keys, ["ATRIUM_AGENT_BACKEND"])
            self.assertFalse(env_path.exists())

    def test_redaction_hides_secret_values_and_database_passwords(self) -> None:
        text = atrium_cli.redact_text(
            "ATRIUM_OPENAI_API_KEY=sk-live-secret\n"
            "ATRIUM_DATABASE_URL=postgresql+asyncpg://atrium:atrium@127.0.0.1:5432/atrium\n"
            "ATRIUM_PORT=8787"
        )

        self.assertIn("ATRIUM_OPENAI_API_KEY=set", text)
        self.assertIn("ATRIUM_DATABASE_URL=postgresql+asyncpg://atrium:***@127.0.0.1:5432/atrium", text)
        self.assertIn("ATRIUM_PORT=8787", text)
        self.assertNotIn("sk-live-secret", text)

    def test_external_ollama_detection_keeps_in_stack_urls_local(self) -> None:
        self.assertFalse(atrium_cli.uses_external_ollama(""))
        self.assertFalse(atrium_cli.uses_external_ollama("http://127.0.0.1:11434"))
        self.assertFalse(atrium_cli.uses_external_ollama("http://localhost:11434"))
        self.assertFalse(atrium_cli.uses_external_ollama("http://ollama:11434"))

    def test_external_ollama_detection_accepts_non_loopback_urls(self) -> None:
        self.assertTrue(atrium_cli.uses_external_ollama("http://172.26.96.1:11434"))
        self.assertTrue(atrium_cli.uses_external_ollama("https://ollama.example.com"))

    def test_windows_browser_detection_accepts_brave_or_chromium_paths(self) -> None:
        def fake_exists(path: Path) -> bool:
            return str(path).endswith("BraveSoftware/Brave-Browser/Application/brave.exe")

        with (
            mock.patch.object(atrium_cli.platform, "system", return_value="Windows"),
            mock.patch.object(atrium_cli.os, "environ", {"ProgramFiles": "C:/Program Files", "ProgramFiles(x86)": "C:/Program Files (x86)", "LocalAppData": "C:/Users/me/AppData/Local"}),
            mock.patch.object(Path, "exists", fake_exists),
            mock.patch.object(atrium_cli, "command_path", return_value=None),
        ):
            self.assertTrue(atrium_cli.browser_installed())

    def test_windows_native_tool_names_exclude_unix_session_dependencies(self) -> None:
        with mock.patch.object(atrium_cli.platform, "system", return_value="Windows"):
            self.assertEqual(
                atrium_cli.doctor_tool_names(),
                ("git", "node", "pnpm", "uv", "python3", "docker", "winget", "powershell", "claude"),
            )
            self.assertNotIn("screen", atrium_cli.report_tool_names())
            self.assertNotIn("brew", atrium_cli.report_tool_names())

    def test_windows_powershell_tool_status_accepts_pwsh(self) -> None:
        def fake_command_path(name: str) -> str | None:
            return "pwsh.exe" if name == "pwsh" else None

        with mock.patch.object(atrium_cli, "command_path", side_effect=fake_command_path):
            self.assertEqual(atrium_cli.report_command_path("powershell"), "pwsh.exe")

    def test_windows_tool_install_falls_back_to_npm_for_claude_code(self) -> None:
        def fake_command_path(name: str) -> str | None:
            paths = {
                "git": "/bin/git",
                "node": "/bin/node",
                "pnpm": "/bin/pnpm",
                "uv": "/bin/uv",
                "python3": "/bin/python3",
                "docker": "/bin/docker",
                "winget": "winget.exe",
                "npm": "npm.cmd",
            }
            return paths.get(name)

        def fake_run_interactive(args: list[str], **_kwargs: object) -> atrium_cli.CommandResult:
            if "Anthropic.ClaudeCode" in args:
                raise atrium_cli.StepFailure("winget claude failed")
            return atrium_cli.CommandResult(0, "", "")

        with (
            mock.patch.object(atrium_cli, "command_path", side_effect=fake_command_path),
            mock.patch.object(atrium_cli, "browser_installed", return_value=True),
            mock.patch.object(atrium_cli, "ensure_common_paths"),
            mock.patch.object(atrium_cli, "run_interactive", side_effect=fake_run_interactive) as run_interactive,
        ):
            atrium_cli.install_missing_windows_tools(False)

        commands = [call.args[0] for call in run_interactive.call_args_list]
        self.assertIn(["winget.exe", "install", "--id", "Anthropic.ClaudeCode", "--exact", "--accept-source-agreements", "--accept-package-agreements"], commands)
        self.assertIn(["npm.cmd", "install", "-g", "@anthropic-ai/claude-code"], commands)

    def test_windows_tool_install_warns_when_browser_winget_fails(self) -> None:
        def fake_command_path(name: str) -> str | None:
            paths = {
                "git": "/bin/git",
                "node": "/bin/node",
                "pnpm": "/bin/pnpm",
                "uv": "/bin/uv",
                "python3": "/bin/python3",
                "docker": "/bin/docker",
                "winget": "winget.exe",
                "claude": "/bin/claude",
            }
            return paths.get(name)

        def fake_run_interactive(args: list[str], **_kwargs: object) -> atrium_cli.CommandResult:
            if "Google.Chrome" in args:
                raise atrium_cli.StepFailure("chrome install blocked")
            return atrium_cli.CommandResult(0, "", "")

        output = io.StringIO()
        with (
            mock.patch.object(atrium_cli, "command_path", side_effect=fake_command_path),
            mock.patch.object(atrium_cli, "browser_installed", return_value=False),
            mock.patch.object(atrium_cli, "run_interactive", side_effect=fake_run_interactive),
            redirect_stdout(output),
        ):
            atrium_cli.install_missing_windows_tools(False)

        self.assertIn("Chromium browser", output.getvalue())
        self.assertIn("Chrome/Edge/Brave/Chromium", output.getvalue())
        self.assertIn("automation status --commands", output.getvalue())

    def test_windows_python3_command_requires_runnable_python3(self) -> None:
        def fake_which(name: str) -> str | None:
            return {"python.exe": "python.exe", "py.exe": "py.exe"}.get(name)

        def fake_subprocess_run(args: list[str], **_kwargs: object):
            completed = mock.Mock()
            if args[0] == "py.exe":
                completed.returncode = 0
                completed.stdout = "Python 3.12.4"
                completed.stderr = ""
            else:
                completed.returncode = 0
                completed.stdout = "Install Python from the Microsoft Store"
                completed.stderr = ""
            return completed

        with (
            mock.patch.object(atrium_cli.platform, "system", return_value="Windows"),
            mock.patch.object(atrium_cli.shutil, "which", side_effect=fake_which),
            mock.patch.object(atrium_cli.subprocess, "run", side_effect=fake_subprocess_run),
        ):
            self.assertEqual(atrium_cli.command_path("python3"), "py.exe")

    def test_windows_tool_install_installs_python_when_missing(self) -> None:
        installed_python = False

        def fake_command_path(name: str) -> str | None:
            if name == "python3":
                return "/bin/python3" if installed_python else None
            paths = {
                "git": "/bin/git",
                "node": "/bin/node",
                "pnpm": "/bin/pnpm",
                "uv": "/bin/uv",
                "docker": "/bin/docker",
                "winget": "winget.exe",
                "claude": "/bin/claude",
            }
            return paths.get(name)

        def fake_run_interactive(args: list[str], **_kwargs: object) -> atrium_cli.CommandResult:
            nonlocal installed_python
            if "Python.Python.3.12" in args:
                installed_python = True
            return atrium_cli.CommandResult(0, "", "")

        with (
            mock.patch.object(atrium_cli, "command_path", side_effect=fake_command_path),
            mock.patch.object(atrium_cli, "browser_installed", return_value=True),
            mock.patch.object(atrium_cli, "ensure_common_paths"),
            mock.patch.object(atrium_cli, "run_interactive", side_effect=fake_run_interactive) as run_interactive,
        ):
            atrium_cli.install_missing_windows_tools(False)

        commands = [call.args[0] for call in run_interactive.call_args_list]
        self.assertIn(["winget.exe", "install", "--id", "Python.Python.3.12", "--exact", "--accept-source-agreements", "--accept-package-agreements"], commands)

    def test_start_dispatches_to_windows_native_lifecycle(self) -> None:
        args = type("Args", (), {"force": False, "wait_seconds": 0})()
        with (
            mock.patch.object(atrium_cli, "ensure_repo_root"),
            mock.patch.object(atrium_cli, "windows_native", return_value=True),
            mock.patch.object(atrium_cli, "command_start_windows", return_value=0) as command_start_windows,
        ):
            self.assertEqual(atrium_cli.command_start(args), 0)
        command_start_windows.assert_called_once_with(args)

    def test_windows_start_docker_stack_waits_for_docker_desktop(self) -> None:
        with (
            mock.patch.object(atrium_cli, "windows_native", return_value=True),
            mock.patch.object(atrium_cli, "command_path", return_value="docker.exe"),
            mock.patch.object(atrium_cli, "docker_compose_cmd", return_value=["docker.exe", "compose"]),
            mock.patch.object(atrium_cli, "run", return_value=atrium_cli.CommandResult(1, "", "not running")),
            mock.patch.object(atrium_cli, "wait_for_docker_ready", return_value=True) as wait_ready,
            mock.patch.object(atrium_cli, "configured_ollama_url", return_value=""),
            mock.patch.object(atrium_cli, "compose") as compose,
            redirect_stdout(io.StringIO()),
        ):
            atrium_cli.start_docker_stack()

        wait_ready.assert_called_once_with(seconds=120, assume_yes=True, prompt=False)
        compose.assert_called_once_with(["up", "-d", "postgres", "ollama"], timeout=300)

    def test_windows_start_docker_stack_blocks_without_compose(self) -> None:
        with (
            mock.patch.object(atrium_cli, "windows_native", return_value=True),
            mock.patch.object(atrium_cli, "command_path", return_value="docker.exe"),
            mock.patch.object(atrium_cli, "docker_compose_cmd", return_value=None),
            redirect_stdout(io.StringIO()),
        ):
            with self.assertRaises(atrium_cli.StepFailure) as failure:
                atrium_cli.start_docker_stack()

        self.assertIn("Docker Compose is unavailable", str(failure.exception))
        self.assertIn(".\\atrium.ps1 doctor", failure.exception.next_step or "")

    def test_pid_status_reports_windows_native_pid_truth(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            pid_path = Path(tmp) / "backend.pid"
            log_path = Path(tmp) / "backend.log"
            pid_path.write_text("1234\n", encoding="utf-8")
            with mock.patch.object(atrium_cli, "process_running", return_value=True):
                status = atrium_cli.pid_status("backend", pid_path, log_path)

        self.assertIn("backend pid=1234 running", status)
        self.assertIn("backend.log", status)

    def test_windows_stop_process_tree_uses_powershell_descendant_shutdown(self) -> None:
        with (
            mock.patch.object(atrium_cli, "command_path", side_effect=lambda name: "powershell.exe" if name == "powershell.exe" else None),
            mock.patch.object(atrium_cli, "run", return_value=atrium_cli.CommandResult(0, "22,11", "")) as run,
        ):
            result = atrium_cli.windows_stop_process_tree(11)

        self.assertEqual(result.returncode, 0)
        command = run.call_args.args[0]
        self.assertEqual(command[0], "powershell.exe")
        self.assertIn("Get-CimInstance Win32_Process", command[-1])
        self.assertIn("Stop-Process", command[-1])
        self.assertIn("$root = 11", command[-1])

    def test_windows_stop_process_keeps_pid_file_when_process_survives(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            log_dir = Path(tmp)
            pid_path = log_dir / "ui.pid"
            pid_path.write_text("44\n", encoding="utf-8")
            with (
                mock.patch.object(atrium_cli, "LOG_DIR", log_dir),
                mock.patch.object(atrium_cli, "process_running", return_value=True),
                mock.patch.object(atrium_cli, "windows_stop_process_tree", return_value=atrium_cli.CommandResult(0, "44", "")),
                mock.patch.object(atrium_cli.time, "time", side_effect=[0, 6]),
                redirect_stdout(io.StringIO()),
            ):
                stopped = atrium_cli.windows_stop_process("frontend", pid_path)

            self.assertFalse(stopped)
            self.assertTrue(pid_path.exists())

    def test_windows_command_stop_returns_nonzero_when_process_survives(self) -> None:
        args = type("Args", (), {"launchd": False})()
        with (
            mock.patch.object(atrium_cli, "ensure_repo_root"),
            mock.patch.object(atrium_cli, "windows_native", return_value=True),
            mock.patch.object(atrium_cli, "windows_stop_process", side_effect=[False, True]),
            redirect_stdout(io.StringIO()),
        ):
            self.assertEqual(atrium_cli.command_stop(args), 2)

    def test_open_docker_desktop_uses_resolved_powershell_and_single_quoted_path(self) -> None:
        def fake_exists(path: Path) -> bool:
            return "Docker Desktop.exe" in str(path)

        with (
            mock.patch.object(atrium_cli.platform, "system", return_value="Windows"),
            mock.patch.object(atrium_cli, "powershell_command", return_value="pwsh.exe"),
            mock.patch.object(atrium_cli.Path, "exists", fake_exists),
            mock.patch.object(atrium_cli, "run", return_value=atrium_cli.CommandResult(0, "", "")) as run,
        ):
            self.assertTrue(atrium_cli.open_docker_desktop())

        command = run.call_args.args[0]
        self.assertEqual(command[0], "pwsh.exe")
        self.assertIn("Start-Process -FilePath", command[-1])
        self.assertIn("'C:/Program Files/Docker/Docker/Docker Desktop.exe'", command[-1])

    def test_open_url_falls_back_to_powershell_when_cmd_is_missing(self) -> None:
        def fake_command_path(name: str) -> str | None:
            return "pwsh.exe" if name in {"powershell.exe", "powershell"} else None

        with (
            mock.patch.object(atrium_cli.platform, "system", return_value="Windows"),
            mock.patch.object(atrium_cli, "command_path", side_effect=fake_command_path),
            mock.patch.object(atrium_cli, "run", return_value=atrium_cli.CommandResult(0, "", "")) as run,
        ):
            atrium_cli.open_url("https://auth.example.test/login?next=a b")

        command = run.call_args.args[0]
        self.assertEqual(command[0], "pwsh.exe")
        self.assertIn("Start-Process", command[-1])
        self.assertIn("'https://auth.example.test/login?next=a b'", command[-1])

    def test_restart_stops_then_starts_with_wait_seconds(self) -> None:
        args = type("Args", (), {"force": True, "wait_seconds": 7})()
        with (
            mock.patch.object(atrium_cli, "ensure_repo_root"),
            mock.patch.object(atrium_cli, "command_stop", return_value=0) as stop,
            mock.patch.object(atrium_cli, "command_start", return_value=0) as start,
        ):
            self.assertEqual(atrium_cli.command_restart(args), 0)

        stop.assert_called_once()
        start_args = start.call_args.args[0]
        self.assertTrue(start_args.force)
        self.assertEqual(start_args.wait_seconds, 7)

    def test_restart_aborts_when_stop_verification_fails(self) -> None:
        args = type("Args", (), {"force": True, "wait_seconds": 7})()
        with (
            mock.patch.object(atrium_cli, "ensure_repo_root"),
            mock.patch.object(atrium_cli, "command_stop", return_value=2) as stop,
            mock.patch.object(atrium_cli, "command_start") as start,
        ):
            self.assertEqual(atrium_cli.command_restart(args), 2)

        stop.assert_called_once()
        start.assert_not_called()

    def test_provider_auth_summary_omits_account_identity(self) -> None:
        lines = atrium_cli.summarize_provider_auth_payload(
            {
                "chatgptAccount": {
                    "ready": True,
                    "source": "store",
                    "chatgptPlanType": "pro",
                    "expired": False,
                    "email": "owner@example.com",
                    "accountId": "acct-secret",
                    "login": {
                        "status": "pending",
                        "authorizationUrl": "https://auth.example.test/secret",
                        "redirectUri": "http://localhost:1455/auth/callback",
                        "expiresAt": 123,
                        "email": "owner@example.com",
                        "accountId": "acct-secret",
                    },
                },
                "claudeCode": {
                    "ready": True,
                    "installed": True,
                    "loggedIn": True,
                    "subscriptionType": "max",
                    "stale": False,
                    "email": "owner@example.com",
                },
            }
        )
        text = "\n".join(lines)

        self.assertIn("ChatGPT account login: ready=true", text)
        self.assertIn("ChatGPT login session: status=pending", text)
        self.assertIn("redirectUri=http://localhost:1455/auth/callback", text)
        self.assertIn("Claude Code account login: ready=true", text)
        self.assertNotIn("owner@example.com", text)
        self.assertNotIn("acct-secret", text)
        self.assertNotIn("authorizationUrl", text)
        self.assertNotIn("auth.example.test", text)

    def test_provider_status_from_env_uses_resolved_claude_command(self) -> None:
        with (
            mock.patch.object(atrium_cli, "parse_env_file", return_value={}),
            mock.patch.object(atrium_cli, "command_path", side_effect=lambda name: "C:/Users/me/AppData/Roaming/npm/claude.cmd" if name == "claude" else None),
            mock.patch.object(atrium_cli, "run", return_value=atrium_cli.CommandResult(0, "{}", "")) as run,
        ):
            lines = atrium_cli.provider_status_from_env()

        run.assert_called_once_with(["C:/Users/me/AppData/Roaming/npm/claude.cmd", "auth", "status", "--json"], timeout=8)
        self.assertIn("Claude Code account: command available", "\n".join(lines))

    def test_provider_status_json_is_redacted(self) -> None:
        args = type("Args", (), {"provider_action": "status", "probe": True, "json": True})()
        payload = {
            "chatgptAccount": {
                "ready": True,
                "email": "owner@example.com",
                "accountId": "acct-secret",
                "login": {
                    "authorizationUrl": "https://auth.example.test/secret",
                    "redirectUri": "http://localhost:1455/auth/callback",
                    "status": "pending",
                },
            }
        }
        output = io.StringIO()
        with (
            mock.patch.object(atrium_cli, "ensure_repo_root"),
            mock.patch.object(atrium_cli, "backend_json", return_value=(True, "{}", payload)),
            redirect_stdout(output),
        ):
            self.assertEqual(atrium_cli.command_provider(args), 0)

        redacted = output.getvalue()
        self.assertIn('"email": "set"', redacted)
        self.assertIn('"accountId": "set"', redacted)
        self.assertIn('"authorizationUrl": "set"', redacted)
        self.assertIn('"redirectUri": "http://localhost:1455/auth/callback"', redacted)
        self.assertNotIn("owner@example.com", redacted)
        self.assertNotIn("acct-secret", redacted)
        self.assertNotIn("auth.example.test", redacted)

    def test_windows_open_url_uses_native_startfile_before_cmd_shell(self) -> None:
        startfile = mock.Mock()
        with (
            mock.patch.object(atrium_cli.platform, "system", return_value="Windows"),
            mock.patch.object(atrium_cli.os, "startfile", startfile, create=True),
            mock.patch.object(atrium_cli, "run") as run,
        ):
            atrium_cli.open_url("https://auth.example.test/login?state=a&code=b")

        startfile.assert_called_once_with("https://auth.example.test/login?state=a&code=b")
        run.assert_not_called()

    def test_status_json_is_redacted(self) -> None:
        args = type("Args", (), {"json": True})()
        payload = {
            "providerAuth": {
                "chatgptAccount": {
                    "ready": True,
                    "email": "owner@example.com",
                    "accountId": "acct-secret",
                    "login": {"access_token": "access-secret", "refreshToken": "refresh-secret"},
                }
            },
            "permissionMode": {
                "mode": "full_auto",
                "fullAutonomyStatus": {"active": True, "credentials": {"apiKey": "sk-secret"}},
            },
            "runtime": {"provider": {"apiKey": "sk-secret"}, "status": "ready"},
        }
        output = io.StringIO()
        with (
            mock.patch.object(atrium_cli, "ensure_repo_root"),
            mock.patch.object(atrium_cli, "collect_status_payload", return_value=payload),
            redirect_stdout(output),
        ):
            self.assertEqual(atrium_cli.command_status(args), 0)

        redacted = json.loads(output.getvalue())
        self.assertEqual(redacted["providerAuth"]["chatgptAccount"]["email"], "set")
        self.assertEqual(redacted["providerAuth"]["chatgptAccount"]["accountId"], "set")
        self.assertEqual(redacted["providerAuth"]["chatgptAccount"]["login"]["access_token"], "set")
        self.assertEqual(redacted["providerAuth"]["chatgptAccount"]["login"]["refreshToken"], "set")
        self.assertEqual(redacted["permissionMode"]["fullAutonomyStatus"]["credentials"]["apiKey"], "set")
        self.assertEqual(redacted["runtime"]["provider"]["apiKey"], "set")
        self.assertNotIn("owner@example.com", output.getvalue())
        self.assertNotIn("access-secret", output.getvalue())
        self.assertNotIn("sk-secret", output.getvalue())

    def test_status_payload_includes_local_proof_artifacts(self) -> None:
        local_artifacts = {
            "currentSourceFingerprint": "b" * 64,
            "macos": {"exists": True, "ok": True, "sourceStatus": "current", "parityRunId": "atrium-run-1"},
            "handoff": {"exists": True, "ok": True, "sourceStatus": "current", "parityRunId": "atrium-run-1"},
            "windowsLocal": {"exists": False, "status": "missing"},
        }

        def fake_backend_json(path: str, *, timeout: float = 5.0):
            if path == "/api/host-bridge/parity":
                return True, "{}", {"ok": False, "status": "cross_os_unverified"}
            if path in {"/health", "/api/runtime", "/api/provider-auth/status", "/api/permissions/mode", "/api/connectors"}:
                return True, "{}", {"ok": True}
            return False, "unexpected", None

        with (
            mock.patch.object(atrium_cli, "windows_native", return_value=False),
            mock.patch.object(atrium_cli, "screen_sessions", return_value=""),
            mock.patch.object(atrium_cli, "port_open", return_value=False),
            mock.patch.object(atrium_cli, "command_path", return_value=None),
            mock.patch.object(atrium_cli, "backend_json", side_effect=fake_backend_json),
            mock.patch.object(atrium_cli, "current_source_summary", return_value={"sourceFingerprint": "b" * 64}),
            mock.patch.object(atrium_cli, "collect_local_proof_artifacts", return_value=local_artifacts),
        ):
            payload = atrium_cli.collect_status_payload()

        self.assertEqual(payload["automationPermission"]["localArtifacts"]["windowsLocal"]["status"], "missing")
        self.assertEqual(payload["automationPermission"]["localArtifacts"]["macos"]["sourceStatus"], "current")

    def test_docker_report_lines_include_daemon_truth(self) -> None:
        with (
            mock.patch.object(atrium_cli, "command_path", return_value="docker.exe"),
            mock.patch.object(atrium_cli, "docker_compose_cmd", return_value=["docker.exe", "compose"]),
            mock.patch.object(atrium_cli, "run", return_value=atrium_cli.CommandResult(1, "", "daemon down")),
        ):
            lines = atrium_cli.docker_report_lines()

        self.assertIn("docker.cli=present", lines)
        self.assertIn("docker.compose=docker.exe compose", lines)
        self.assertIn("docker.running=false", lines)

    def test_report_lines_include_local_proof_artifacts(self) -> None:
        local_artifacts = {
            "currentSourceFingerprint": "b" * 64,
            "macos": {"exists": True, "ok": True, "sourceStatus": "current", "parityRunId": "atrium-run-1"},
            "handoff": {"exists": True, "ok": True, "sourceStatus": "current", "parityRunId": "atrium-run-1"},
            "windowsLocal": {"exists": False, "status": "missing"},
        }

        def fake_backend_json(path: str, *, timeout: float = 5.0):
            if path == "/api/host-bridge/parity":
                return True, "{}", {"ok": False, "commands": {"verify": "./atrium automation audit"}}
            if path in {
                "/health",
                "/api/runtime",
                "/api/provider-auth/status",
                "/api/permissions/mode",
                "/api/tools/catalog",
                "/api/connectors",
            }:
                return True, "{}", {"ok": True}
            return False, "unexpected", None

        with (
            mock.patch.object(atrium_cli, "git_status_summary", return_value="clean"),
            mock.patch.object(atrium_cli, "remote_ok", return_value=(True, "origin/main")),
            mock.patch.object(atrium_cli, "is_i_cloud_risky", return_value=(False, "ok")),
            mock.patch.object(atrium_cli, "memory_gb", return_value="16GB"),
            mock.patch.object(atrium_cli, "windows_native", return_value=False),
            mock.patch.object(atrium_cli, "screen_sessions", return_value=""),
            mock.patch.object(atrium_cli, "command_path", return_value=None),
            mock.patch.object(atrium_cli, "docker_report_lines", return_value=[]),
            mock.patch.object(atrium_cli, "port_open", return_value=False),
            mock.patch.object(atrium_cli, "backend_json", side_effect=fake_backend_json),
            mock.patch.object(atrium_cli, "render_env_status", return_value=[]),
            mock.patch.object(atrium_cli, "provider_status_from_env", return_value=[]),
            mock.patch.object(atrium_cli, "current_source_summary", return_value={"sourceFingerprint": "b" * 64}),
            mock.patch.object(atrium_cli, "collect_local_proof_artifacts", return_value=local_artifacts),
        ):
            lines = atrium_cli.report_lines()

        self.assertIn("automation_permission.local_artifacts:", lines)
        self.assertIn("macos: exists=true, ok=true, source=current, run=atrium-run-1", lines)
        self.assertIn("windowsLocal: exists=false, status=missing", lines)

    def test_report_bundle_writes_redacted_report_and_logs(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            tmp_path = Path(tmp)
            log_dir = tmp_path / "logs"
            log_dir.mkdir()
            (log_dir / "backend.log").write_text("ATRIUM_OPENAI_API_KEY=sk-secret\nready\n", encoding="utf-8")
            bundle = tmp_path / "support.zip"
            args = type("Args", (), {"output": None, "bundle": str(bundle)})()
            with (
                mock.patch.object(atrium_cli, "ensure_repo_root"),
                mock.patch.object(atrium_cli, "LOG_DIR", log_dir),
                mock.patch.object(atrium_cli, "report_lines", return_value=["ATRIUM_OPENAI_API_KEY=sk-secret", "ok=true"]),
                mock.patch.object(
                    atrium_cli,
                    "support_bundle_json_payloads",
                    return_value={
                        "diagnostics/status.json": {"runtime": {"provider": {"apiKey": "sk-secret"}}},
                        "diagnostics/process.json": {
                            "mode": "windows-native",
                            "details": {"backend": {"pid": 1234, "running": True}},
                        },
                        "diagnostics/logs.json": {"logs": {"backend": {"lines": ["ATRIUM_OPENAI_API_KEY=sk-secret"]}}},
                        "diagnostics/permission-mode.json": {"fullAutonomyStatus": {"credentials": {"apiKey": "sk-secret"}}},
                        "diagnostics/provider-status.json": {"chatgptAccount": {"email": "owner@example.com"}},
                    },
                ),
                redirect_stdout(io.StringIO()),
            ):
                self.assertEqual(atrium_cli.command_report(args), 0)

            with zipfile.ZipFile(bundle) as archive:
                names = set(archive.namelist())
                report = archive.read("support-report.txt").decode("utf-8")
                backend_log = archive.read("logs/backend.log").decode("utf-8")
                status_json = archive.read("diagnostics/status.json").decode("utf-8")
                process_json = archive.read("diagnostics/process.json").decode("utf-8")
                logs_json = archive.read("diagnostics/logs.json").decode("utf-8")
                permission_json = archive.read("diagnostics/permission-mode.json").decode("utf-8")
                provider_json = archive.read("diagnostics/provider-status.json").decode("utf-8")
                manifest = json.loads(archive.read("manifest.json").decode("utf-8"))

        self.assertIn("support-report.txt", names)
        self.assertIn("logs/backend.log", names)
        self.assertIn("diagnostics/status.json", names)
        self.assertIn("diagnostics/process.json", names)
        self.assertIn("diagnostics/logs.json", names)
        self.assertIn("diagnostics/permission-mode.json", names)
        self.assertIn("diagnostics/provider-status.json", names)
        self.assertIn("manifest.json", names)
        self.assertIn("ATRIUM_OPENAI_API_KEY=set", report)
        self.assertNotIn("sk-secret", report)
        self.assertNotIn("sk-secret", backend_log)
        self.assertNotIn("sk-secret", status_json)
        self.assertIn('"mode": "windows-native"', process_json)
        self.assertNotIn("sk-secret", logs_json)
        self.assertNotIn("sk-secret", permission_json)
        self.assertNotIn("owner@example.com", provider_json)
        self.assertTrue(manifest["redacted"])

    def test_logs_json_returns_redacted_log_payload(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            log_dir = Path(tmp) / "logs"
            log_dir.mkdir()
            (log_dir / "backend.log").write_text("ATRIUM_OPENAI_API_KEY=sk-secret\nready\n", encoding="utf-8")
            args = type("Args", (), {"service": "all", "lines": 5, "json": True})()
            output = io.StringIO()
            with (
                mock.patch.object(atrium_cli, "ensure_repo_root"),
                mock.patch.object(atrium_cli, "LOG_DIR", log_dir),
                redirect_stdout(output),
            ):
                self.assertEqual(atrium_cli.command_logs(args), 0)

        payload = json.loads(output.getvalue())
        self.assertTrue(payload["logs"]["backend"]["exists"])
        self.assertFalse(payload["logs"]["ui"]["exists"])
        self.assertIn("ATRIUM_OPENAI_API_KEY=set", "\n".join(payload["logs"]["backend"]["lines"]))
        self.assertNotIn("sk-secret", output.getvalue())

    def test_report_output_creates_parent_directory(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            output = Path(tmp) / "nested" / "support.txt"
            args = type("Args", (), {"output": str(output), "bundle": None})()
            with (
                mock.patch.object(atrium_cli, "ensure_repo_root"),
                mock.patch.object(atrium_cli, "report_lines", return_value=["ok=true"]),
                redirect_stdout(io.StringIO()),
            ):
                self.assertEqual(atrium_cli.command_report(args), 0)

            self.assertEqual(output.read_text(encoding="utf-8"), "ok=true\n")

    def test_runtime_ai_tools_summary_includes_windows_automation_readiness(self) -> None:
        lines = atrium_cli.summarize_runtime_ai_tools(
            {
                "provider": {
                    "ready": True,
                    "hasOpenAIKey": True,
                    "hasChatGPTAccountOAuth": True,
                    "hasClaudeCodeAccount": False,
                    "embeddings": "openai:text-embedding-3-large:1024",
                },
                "v2": {
                    "toolRegistryCount": 42,
                    "customToolCount": 2,
                    "fullAutonomy": {
                        "mode": "full_auto",
                        "active": True,
                        "agentFullAccess": True,
                        "approvalGatesDisabled": True,
                        "effectivePolicyDecision": "auto_approved",
                        "entitlements": {
                            "hostShell": True,
                            "hostFilesystem": True,
                            "browserAutomation": True,
                            "desktopAutomation": True,
                            "externalSend": True,
                            "credentials": True,
                        },
                    },
                    "hostBridge": {
                        "platform": "win32",
                        "shellReady": True,
                        "browserAutomationReady": True,
                        "desktopAutomationReady": False,
                        "browserPlaywrightReady": True,
                        "isolatedBrowserProfileReady": True,
                        "interactiveSession": True,
                        "interactiveSessionName": "Console",
                        "windowsVisualPreflightChecked": True,
                        "windowsVisualPreflightOk": False,
                        "windowsVisualPreflightError": "dpi helper failed",
                    },
                },
            }
        )
        text = "\n".join(lines)

        self.assertIn("AI providers: ready=true", text)
        self.assertIn("Tool registry: default=42, custom=2", text)
        self.assertIn("Owner permissions: mode=full_auto", text)
        self.assertIn("browserAutomation=true", text)
        self.assertIn("HostBridge: platform=win32", text)
        self.assertIn("Windows automation preflight: interactiveSession=true", text)
        self.assertIn("error=dpi helper failed", text)

    def test_tool_catalog_summary_surfaces_executor_and_risk_counts(self) -> None:
        lines = atrium_cli.summarize_tool_catalog_payload(
            [
                {"name": "browser.open", "executor": "browser", "riskClass": "desktop", "mutates": True},
                {"name": "fs.read", "executor": "host", "riskClass": "safe_read", "mutates": False},
                {"name": "desktop.act", "executor": "desktop", "riskClass": "desktop", "mutates": True},
            ]
        )
        text = "\n".join(lines)

        self.assertIn("count=3", text)
        self.assertIn("browser=1", text)
        self.assertIn("desktop=2", text)
        self.assertIn("browser.open", text)

    def test_connector_and_parity_summaries_surface_cross_os_gap(self) -> None:
        connector_lines = atrium_cli.summarize_connectors_payload(
            [
                {
                    "id": "browser",
                    "status": "available",
                    "readReady": True,
                    "writeReady": True,
                    "runtimeStatus": "ready",
                    "proofStatus": "cross_os_unverified",
                },
                {"id": "local_file", "status": "available"},
            ]
        )
        parity_lines = atrium_cli.summarize_parity_payload(
            {
                "ok": False,
                "status": "cross_os_unverified",
                "summary": "full parity report missing",
                "gaps": ["run Windows full probe", "first hidden gap", "OpenClaw contract gap: MCP external tools"],
                "contract": {
                    "status": "cross_os_unverified",
                    "summary": "OpenClaw-level Windows contract incomplete",
                    "localRequirements": [{"id": "windows_visual_preflight", "currentHostApplies": True, "currentReady": False}],
                    "apiSurfaceRequirements": [{"id": "provider_status", "registered": False}],
                    "featureRequirements": [{"id": "git", "required": True, "ready": False}],
                    "connectorRequirements": [{"id": "browser", "proved": False}],
                    "proofRequirements": [{"id": "notepadNativeAct", "proved": False}],
                },
            }
        )

        self.assertEqual(len(connector_lines), 1)
        self.assertIn("connector.browser", connector_lines[0])
        self.assertIn("proof=cross_os_unverified", connector_lines[0])
        self.assertIn("HostBridge parity: ok=false", parity_lines[0])
        self.assertIn("run Windows full probe", "\n".join(parity_lines))
        self.assertIn("OpenClaw Windows contract: status=cross_os_unverified", "\n".join(parity_lines))
        self.assertIn("windows_visual_preflight", "\n".join(parity_lines))
        self.assertIn("provider_status", "\n".join(parity_lines))
        self.assertIn("git", "\n".join(parity_lines))
        self.assertIn("OpenClaw contract gap: MCP external tools", "\n".join(parity_lines))

    def test_parity_summary_surfaces_missing_openclaw_contract_payload(self) -> None:
        parity_lines = atrium_cli.summarize_parity_payload(
            {
                "ok": False,
                "status": "cross_os_unverified",
                "summary": "legacy backend payload",
                "gaps": ["run automation report"],
            }
        )

        text = "\n".join(parity_lines)
        self.assertIn("OpenClaw Windows contract: missing from backend payload", text)

    def test_openclaw_contract_gap_lines_include_all_contract_groups(self) -> None:
        gaps = atrium_cli.openclaw_contract_gap_lines(
            {
                "gaps": ["run Windows full probe"],
                "contract": {
                    "localRequirements": [{"id": "interactive_desktop_session", "currentHostApplies": True, "currentReady": False}],
                    "apiSurfaceRequirements": [{"id": "provider_status", "registered": False, "path": "/api/provider-auth/status"}],
                    "reportRequirements": [{"id": "proof_id_bound_to_artifacts", "currentReady": False}],
                    "featureRequirements": [
                        {"id": "git", "required": True, "ready": False, "runtimeStatus": "git missing"},
                        {
                            "id": "mcp",
                            "required": True,
                            "ready": False,
                            "requiresWriteReady": True,
                            "writeReady": False,
                            "localFallback": True,
                            "degradedByLocalFallback": True,
                            "externalWriteRequires": ["ATRIUM_MCP_GATEWAY_URL configured"],
                        },
                    ],
                    "connectorRequirements": [{"id": "browser", "proved": False}],
                    "windowsProofRequirements": [{"id": "notepadNativeAct", "proved": False}],
                },
            }
        )

        text = "\n".join(gaps)
        self.assertIn("run Windows full probe", text)
        self.assertIn("local.interactive_desktop_session", text)
        self.assertIn("api.provider_status", text)
        self.assertIn("report.proof_id_bound_to_artifacts", text)
        self.assertIn("feature.git", text)
        self.assertIn("feature.mcp", text)
        self.assertIn("local fallback only", text)
        self.assertIn("write not ready", text)
        self.assertIn("ATRIUM_MCP_GATEWAY_URL", text)
        self.assertIn("connector.browser", text)
        self.assertIn("proof.notepadNativeAct", text)

    def test_automation_audit_fails_when_openclaw_contract_unverified(self) -> None:
        args = type("Args", (), {"automation_action": "audit", "json": False})()
        payload = {
            "ok": False,
            "status": "cross_os_unverified",
            "summary": "missing proof",
            "gaps": [],
            "contract": {
                "status": "cross_os_unverified",
                "summary": "contract missing proof",
                "featureRequirements": [{"id": "git", "required": True, "ready": False, "runtimeStatus": "git missing"}],
            },
            "commands": {
                "windowsLiveProofRunner": "powershell ...",
                "automationReport": "./atrium automation report --macos /tmp/macos.json --windows /tmp/windows.json",
                "verify": "./atrium automation audit",
            },
        }
        output = io.StringIO()
        with (
            mock.patch.object(atrium_cli, "ensure_repo_root"),
            mock.patch.object(atrium_cli, "backend_json", return_value=(True, "{}", payload)),
            mock.patch.object(atrium_cli, "current_source_summary", return_value={"sourceFingerprint": "b" * 64}),
            mock.patch.object(
                atrium_cli,
                "collect_local_proof_artifacts",
                return_value={
                    "currentSourceFingerprint": "b" * 64,
                    "macos": {"exists": True, "ok": True, "sourceStatus": "stale", "parityRunId": "atrium-old"},
                    "handoff": {"exists": True, "ok": True, "sourceStatus": "stale", "parityRunId": "atrium-old"},
                    "windowsLocal": {"exists": False, "status": "missing"},
                },
            ),
            redirect_stdout(output),
        ):
            self.assertEqual(atrium_cli.command_automation(args), 2)

        text = output.getvalue()
        self.assertIn("Local Proof Artifacts", text)
        self.assertIn("macos: exists=true, ok=true, source=stale, run=atrium-old", text)
        self.assertIn("windowsLocal: exists=false, status=missing", text)
        self.assertIn("OpenClaw Windows Gaps", text)
        self.assertIn("feature.git", text)
        self.assertIn("windowsLiveProofRunner", text)
        self.assertIn("automationReport", text)

    def test_automation_audit_passes_only_when_payload_and_contract_are_verified(self) -> None:
        args = type("Args", (), {"automation_action": "audit", "json": False})()
        payload = {
            "ok": True,
            "status": "cross_os_verified",
            "summary": "verified",
            "contract": {"status": "cross_os_verified", "summary": "verified"},
        }
        output = io.StringIO()
        with (
            mock.patch.object(atrium_cli, "ensure_repo_root"),
            mock.patch.object(atrium_cli, "backend_json", return_value=(True, "{}", payload)),
            redirect_stdout(output),
        ):
            self.assertEqual(atrium_cli.command_automation(args), 0)

        self.assertIn("OpenClaw Windows contract", output.getvalue())

    def test_automation_audit_json_normalizes_stale_backend_payload(self) -> None:
        args = type("Args", (), {"automation_action": "audit", "json": True})()
        old_fingerprint = "a" * 64
        current_fingerprint = "b" * 64
        payload = {
            "ok": False,
            "status": "cross_os_unverified",
            "summary": "missing proof",
            "gaps": ["Run ops/host_bridge_parity_report.py with macOS and Windows --full probe artifacts before claiming cross-OS HostBridge parity."],
            "commands": {
                "sourceFingerprint": old_fingerprint,
                "verify": (
                    "uv --project system run python ops/host_bridge_parity_report.py "
                    "--macos /tmp/macos.json --windows /tmp/windows.json "
                    "--windows-source-path 'C:\\Temp\\windows.json' --output data/host-bridge-parity-report.json"
                ),
            },
            "report": {
                "findings": ["Run ops/host_bridge_parity_report.py with macOS and Windows --full probe artifacts before claiming cross-OS HostBridge parity."]
            },
            "connectors": [
                {
                    "id": "browser",
                    "proofGaps": [
                        "Run ops/host_bridge_parity_report.py with macOS and Windows --full probe artifacts before claiming cross-OS HostBridge parity."
                    ],
                }
            ],
        }
        output = io.StringIO()
        with (
            mock.patch.object(atrium_cli, "ensure_repo_root"),
            mock.patch.object(atrium_cli, "backend_json", return_value=(True, "{}", payload)),
            mock.patch.object(atrium_cli, "windows_native", return_value=False),
            mock.patch.object(
                atrium_cli,
                "current_source_summary",
                return_value={
                    "sourceFingerprint": current_fingerprint,
                    "sourceManifestSha256": current_fingerprint,
                    "sourceFileCount": 17,
                    "gitHead": "c" * 40,
                    "gitDirty": True,
                },
            ),
            mock.patch.object(
                atrium_cli,
                "collect_local_proof_artifacts",
                return_value={
                    "currentSourceFingerprint": current_fingerprint,
                    "macos": {"exists": True, "ok": True, "sourceStatus": "stale", "parityRunId": "atrium-old"},
                    "handoff": {"exists": True, "ok": True, "sourceStatus": "stale", "parityRunId": "atrium-old"},
                    "windowsLocal": {"exists": False, "status": "missing"},
                },
            ),
            redirect_stdout(output),
        ):
            self.assertEqual(atrium_cli.command_automation(args), 2)

        normalized = json.loads(output.getvalue())
        self.assertTrue(normalized["cliNormalized"])
        self.assertFalse(normalized["cliContractPresent"])
        self.assertEqual(normalized["commands"]["sourceFingerprint"], current_fingerprint)
        self.assertEqual(normalized["commands"]["sourceManifestSha256"], current_fingerprint)
        self.assertEqual(normalized["commands"]["sourceFileCount"], "17")
        self.assertEqual(normalized["commands"]["backendSourceFingerprint"], old_fingerprint)
        self.assertEqual(normalized["commands"]["verify"], "./atrium automation audit")
        self.assertIn("automation report", normalized["commands"]["automationReport"])
        self.assertIn("automation report", normalized["gaps"][0])
        self.assertIn("automation report", normalized["report"]["findings"][0])
        self.assertIn("automation report", normalized["connectors"][0]["proofGaps"][0])
        self.assertEqual(normalized["cliSource"]["sourceFileCount"], 17)
        self.assertEqual(normalized["localArtifacts"]["macos"]["sourceStatus"], "stale")
        self.assertEqual(normalized["localArtifacts"]["handoff"]["sourceStatus"], "stale")
        self.assertEqual(normalized["localArtifacts"]["windowsLocal"]["status"], "missing")

    def test_automation_audit_json_is_redacted(self) -> None:
        args = type("Args", (), {"automation_action": "audit", "json": True})()
        payload = {
            "ok": False,
            "status": "cross_os_unverified",
            "providerAuth": {"email": "owner@example.com", "refresh_token": "refresh-secret"},
            "contract": {"status": "cross_os_unverified"},
        }
        output = io.StringIO()
        with (
            mock.patch.object(atrium_cli, "ensure_repo_root"),
            mock.patch.object(atrium_cli, "backend_json", return_value=(True, "{}", payload)),
            mock.patch.object(
                atrium_cli,
                "current_source_summary",
                return_value={"sourceFingerprint": "a" * 64, "sourceManifestSha256": "b" * 64, "sourceFileCount": 17},
            ),
            redirect_stdout(output),
        ):
            self.assertEqual(atrium_cli.command_automation(args), 2)

        redacted = json.loads(output.getvalue())
        self.assertEqual(redacted["providerAuth"]["email"], "set")
        self.assertEqual(redacted["providerAuth"]["refresh_token"], "set")
        self.assertNotIn("owner@example.com", output.getvalue())
        self.assertNotIn("refresh-secret", output.getvalue())

    def test_provider_target_normalization_accepts_cli_aliases(self) -> None:
        self.assertEqual(atrium_cli.normalize_provider_auth_target("chatgpt-account"), "chatgpt")
        self.assertEqual(atrium_cli.normalize_provider_auth_target("claude"), "claude-code")

    def test_provider_status_uses_backend_truth_without_identity_leak(self) -> None:
        args = type("Args", (), {"provider_action": "status", "probe": True})()
        payload = {
            "chatgptAccount": {
                "ready": True,
                "source": "store",
                "chatgptPlanType": "pro",
                "expired": False,
                "email": "owner@example.com",
                "accountId": "acct-secret",
            },
            "claudeCode": {"ready": False, "installed": True, "loggedIn": False},
        }
        output = io.StringIO()
        with (
            mock.patch.object(atrium_cli, "ensure_repo_root"),
            mock.patch.object(atrium_cli, "backend_json", return_value=(True, "{}", payload)) as backend_json,
            redirect_stdout(output),
        ):
            self.assertEqual(atrium_cli.command_provider(args), 0)

        backend_json.assert_called_once_with("/api/provider-auth/status?probe=true", timeout=15.0)
        text = output.getvalue()
        self.assertIn("ChatGPT account login: ready=true", text)
        self.assertNotIn("owner@example.com", text)
        self.assertNotIn("acct-secret", text)

    def test_chatgpt_provider_login_opens_url_and_polls_ready(self) -> None:
        args = type(
            "Args",
            (),
            {
                "provider_action": "login",
                "provider": "chatgpt",
                "timeout_seconds": 300,
                "wait_seconds": 5,
                "no_open": False,
                "no_interactive": False,
            },
        )()
        login_payload = {
            "status": "pending",
            "authorizationUrl": "https://auth.example.test/login",
            "redirectUri": "http://127.0.0.1:1455/callback",
            "expiresAt": 123,
        }
        status_payload = {"chatgptAccount": {"ready": True, "source": "store"}, "claudeCode": {"ready": False}}
        output = io.StringIO()
        with (
            mock.patch.object(atrium_cli, "ensure_repo_root"),
            mock.patch.object(atrium_cli, "backend_json_request", return_value=(True, "{}", login_payload)) as request,
            mock.patch.object(atrium_cli, "open_url") as open_url,
            mock.patch.object(atrium_cli, "wait_provider_ready", return_value=(True, status_payload)) as wait_ready,
            redirect_stdout(output),
        ):
            self.assertEqual(atrium_cli.command_provider(args), 0)

        request.assert_called_once_with(
            "/api/provider-auth/chatgpt/start",
            method="POST",
            payload={"timeoutS": 300},
            timeout=20.0,
        )
        open_url.assert_called_once_with("https://auth.example.test/login")
        wait_ready.assert_called_once_with("chatgpt", 5)
        self.assertIn("ChatGPT account login: ready=true", output.getvalue())

    def test_claude_provider_login_runs_interactive_manual_flow(self) -> None:
        args = type(
            "Args",
            (),
            {
                "provider_action": "login",
                "provider": "claude-code",
                "timeout_seconds": 300,
                "wait_seconds": 5,
                "no_open": False,
                "no_interactive": False,
            },
        )()
        login_payload = {
            "started": False,
            "mode": "manual",
            "command": "claude auth login --claudeai",
            "status": {"ready": False, "installed": True, "loggedIn": False},
        }
        status_payload = {"chatgptAccount": {"ready": False}, "claudeCode": {"ready": True, "installed": True}}
        with (
            mock.patch.object(atrium_cli, "ensure_repo_root"),
            mock.patch.object(atrium_cli, "backend_json_request", return_value=(True, "{}", login_payload)),
            mock.patch.object(atrium_cli, "command_path", return_value="/bin/claude"),
            mock.patch.object(atrium_cli, "run_interactive") as run_interactive,
            mock.patch.object(atrium_cli, "wait_provider_ready", return_value=(True, status_payload)),
            redirect_stdout(io.StringIO()),
        ):
            self.assertEqual(atrium_cli.command_provider(args), 0)

        run_interactive.assert_called_once_with(["/bin/claude", "auth", "login", "--claudeai"], cwd=atrium_cli.ROOT)

    def test_provider_disconnect_uses_backend_endpoint(self) -> None:
        args = type("Args", (), {"provider_action": "disconnect", "provider": "claude"})()
        payload = {"ok": True, "mode": "cli", "started": True, "status": {"ready": False, "installed": True}}
        with (
            mock.patch.object(atrium_cli, "ensure_repo_root"),
            mock.patch.object(atrium_cli, "backend_json_request", return_value=(True, "{}", payload)) as request,
            redirect_stdout(io.StringIO()),
        ):
            self.assertEqual(atrium_cli.command_provider(args), 0)

        request.assert_called_once_with("/api/provider-auth/claude-code/disconnect", method="POST", timeout=30.0)

    def test_automation_status_prints_parity_commands(self) -> None:
        args = type("Args", (), {"automation_action": "status", "commands": True})()
        payload = {
            "ok": False,
            "status": "cross_os_unverified",
            "summary": "missing Windows proof",
            "gaps": ["run Windows full probe"],
            "commands": {
                "parityRunId": "atrium-run-1",
                "sourceFingerprint": "a" * 64,
                "macosArtifactValidate": "uv --project system run python ops/host_bridge_artifact_summary.py --label macos ...",
                "windowsHandoff": "./atrium automation handoff --macos /tmp/macos.json --output /tmp/handoff.json",
                "windowsLiveProofRunner": "powershell -File .\\ops\\windows_host_bridge_live_proof.ps1 ...",
                "windowsArtifactValidateOnWindows": "uv --project system run python ops/host_bridge_artifact_summary.py --label windows C:\\Temp\\probe.json",
                "windowsArtifactValidateLocal": "uv --project system run python ops/host_bridge_artifact_summary.py --label windows /tmp/probe.json",
                "automationReport": "./atrium automation report --macos /tmp/macos.json --windows /tmp/windows.json",
                "verify": "./atrium automation audit",
            },
        }
        permission_payload = {
            "mode": "full_auto",
            "fullAutonomyStatus": {
                "active": True,
                "entitlements": {"browserAutomation": True, "desktopAutomation": True},
            },
        }

        def fake_backend_json(path: str, *, timeout: float = 5.0):
            if path == "/api/host-bridge/parity":
                return True, "{}", payload
            if path == "/api/permissions/mode":
                return True, "{}", permission_payload
            return False, "unexpected", None

        output = io.StringIO()
        with (
            mock.patch.object(atrium_cli, "ensure_repo_root"),
            mock.patch.object(atrium_cli, "backend_json", side_effect=fake_backend_json) as backend_json,
            mock.patch.object(atrium_cli, "current_source_summary", return_value={"sourceFingerprint": "a" * 64}),
            mock.patch.object(
                atrium_cli,
                "collect_local_proof_artifacts",
                return_value={
                    "currentSourceFingerprint": "a" * 64,
                    "macos": {"exists": True, "ok": True, "sourceStatus": "current", "parityRunId": "atrium-run-1"},
                    "handoff": {"exists": True, "ok": True, "sourceStatus": "current", "parityRunId": "atrium-run-1"},
                    "windowsLocal": {"exists": False, "status": "missing"},
                },
            ),
            redirect_stdout(output),
        ):
            self.assertEqual(atrium_cli.command_automation(args), 0)

        paths = [call.args[0] for call in backend_json.call_args_list]
        self.assertEqual(paths, ["/api/host-bridge/parity", "/api/permissions/mode"])
        text = output.getvalue()
        self.assertIn("HostBridge parity: ok=false", text)
        self.assertIn("Local Proof Artifacts", text)
        self.assertIn("macos: exists=true, ok=true, source=current, run=atrium-run-1", text)
        self.assertIn("windowsLocal: exists=false, status=missing", text)
        self.assertIn("Owner Permissions", text)
        self.assertIn("windowsLiveProofRunner", text)
        self.assertIn("windowsHandoff", text)
        self.assertIn("macosArtifactValidate", text)
        self.assertIn("windowsArtifactValidateOnWindows", text)
        self.assertIn("windowsArtifactValidateLocal", text)
        self.assertIn("automationReport", text)
        self.assertIn("run Windows full probe", text)

    def test_automation_status_json_prints_normalized_redacted_payload(self) -> None:
        args = type("Args", (), {"automation_action": "status", "commands": True, "json": True})()
        payload = {
            "ok": False,
            "status": "cross_os_unverified",
            "summary": "missing Windows proof",
            "providerAuth": {"email": "owner@example.com", "accessToken": "secret-token"},
            "commands": {"verify": "./atrium automation audit"},
        }
        permission_payload = {"fullAutonomyStatus": {"credentials": {"apiKey": "sk-permission-secret"}}}

        def fake_backend_json(path: str, *, timeout: float = 5.0):
            if path == "/api/host-bridge/parity":
                return True, "{}", payload
            if path == "/api/permissions/mode":
                return True, "{}", permission_payload
            return False, "unexpected", None

        output = io.StringIO()
        with (
            mock.patch.object(atrium_cli, "ensure_repo_root"),
            mock.patch.object(atrium_cli, "backend_json", side_effect=fake_backend_json),
            mock.patch.object(atrium_cli, "current_source_summary", return_value={"sourceFingerprint": "b" * 64}),
            mock.patch.object(
                atrium_cli,
                "collect_local_proof_artifacts",
                return_value={
                    "currentSourceFingerprint": "b" * 64,
                    "macos": {"exists": True, "ok": True, "sourceStatus": "stale", "parityRunId": "atrium-old"},
                    "handoff": {"exists": True, "ok": True, "sourceStatus": "stale", "parityRunId": "atrium-old"},
                    "windowsLocal": {"exists": False, "status": "missing"},
                },
            ),
            redirect_stdout(output),
        ):
            self.assertEqual(atrium_cli.command_automation(args), 0)

        redacted = json.loads(output.getvalue())
        self.assertEqual(redacted["providerAuth"]["email"], "set")
        self.assertEqual(redacted["providerAuth"]["accessToken"], "set")
        self.assertEqual(redacted["commands"]["verify"], "./atrium automation audit")
        self.assertEqual(redacted["localArtifacts"]["macos"]["sourceStatus"], "stale")
        self.assertEqual(redacted["localArtifacts"]["handoff"]["sourceStatus"], "stale")
        self.assertEqual(redacted["localArtifacts"]["windowsLocal"]["status"], "missing")
        self.assertEqual(redacted["permissionMode"]["fullAutonomyStatus"]["credentials"]["apiKey"], "set")
        self.assertNotIn("owner@example.com", output.getvalue())
        self.assertNotIn("secret-token", output.getvalue())
        self.assertNotIn("sk-permission-secret", output.getvalue())

    def test_local_proof_artifacts_mark_stale_and_missing_sources(self) -> None:
        current = {"sourceFingerprint": "b" * 64}
        old = "a" * 64

        def fake_load_json_file(path: Path):
            if str(path) == "/tmp/atrium_host_bridge_macos_live.json":
                return (
                    {
                        "ok": True,
                        "mode": "full",
                        "parityRunId": "atrium-old",
                        "source": {"sourceFingerprint": old},
                        "generatedAt": 1781010000000,
                    },
                    None,
                )
            if str(path) == "/tmp/atrium_windows_handoff.json":
                return (
                    {
                        "ok": True,
                        "kind": "atrium.hostBridge.windowsProofHandoff",
                        "source": {"sourceFingerprint": old},
                        "macosArtifact": {"parityRunId": "atrium-old"},
                    },
                    None,
                )
            return None, "missing"

        with mock.patch.object(atrium_cli, "_load_json_file", side_effect=fake_load_json_file):
            artifacts = atrium_cli.collect_local_proof_artifacts(current)

        self.assertEqual(artifacts["currentSourceFingerprint"], "b" * 64)
        self.assertEqual(artifacts["macos"]["sourceStatus"], "stale")
        self.assertEqual(artifacts["handoff"]["sourceStatus"], "stale")
        self.assertFalse(artifacts["windowsLocal"]["exists"])
        self.assertEqual(artifacts["windowsLocal"]["status"], "missing")
        lines = atrium_cli.summarize_local_proof_artifacts(artifacts)
        self.assertIn("macos: exists=true, ok=true, source=stale, run=atrium-old", lines)
        self.assertIn("handoff: exists=true, ok=true, source=stale, run=atrium-old", lines)
        self.assertIn("windowsLocal: exists=false, status=missing", lines)

    def test_tools_status_reads_runtime_catalog_and_connectors(self) -> None:
        args = type("Args", (), {"tools_action": "status", "limit": 3})()

        def fake_backend_json(path: str, *, timeout: float = 5.0):
            if path == "/api/runtime":
                return True, "{}", {"provider": {"ready": True}, "v2": {"toolRegistryCount": 2, "customToolCount": 0}}
            if path == "/api/tools/catalog":
                return True, "[]", [{"name": "browser.open", "executor": "browser", "riskClass": "desktop"}]
            if path == "/api/connectors":
                return True, "[]", [{"id": "browser", "status": "available", "readReady": True, "writeReady": True}]
            return False, "unexpected", None

        output = io.StringIO()
        with (
            mock.patch.object(atrium_cli, "ensure_repo_root"),
            mock.patch.object(atrium_cli, "backend_json", side_effect=fake_backend_json),
            redirect_stdout(output),
        ):
            self.assertEqual(atrium_cli.command_tools(args), 0)

        text = output.getvalue()
        self.assertIn("AI Tools", text)
        self.assertIn("Tool catalog", text)
        self.assertIn("connector.browser", text)

    def test_tools_status_json_reads_runtime_catalog_and_connectors(self) -> None:
        args = type("Args", (), {"tools_action": "status", "limit": 3, "json": True})()

        def fake_backend_json(path: str, *, timeout: float = 5.0):
            if path == "/api/runtime":
                return True, "{}", {"provider": {"apiKey": "sk-secret"}, "v2": {"toolRegistryCount": 2}}
            if path == "/api/tools/catalog":
                return True, "[]", [{"name": "browser.open", "clientSecret": "catalog-secret"}]
            if path == "/api/connectors":
                return True, "[]", [{"id": "browser", "status": "available", "accessToken": "connector-secret"}]
            return False, "unexpected", None

        output = io.StringIO()
        with (
            mock.patch.object(atrium_cli, "ensure_repo_root"),
            mock.patch.object(atrium_cli, "backend_json", side_effect=fake_backend_json) as backend_json,
            redirect_stdout(output),
        ):
            self.assertEqual(atrium_cli.command_tools(args), 0)

        paths = [call.args[0] for call in backend_json.call_args_list]
        self.assertEqual(paths, ["/api/runtime", "/api/tools/catalog", "/api/connectors"])
        redacted = json.loads(output.getvalue())
        self.assertEqual(redacted["runtime"]["provider"]["apiKey"], "set")
        self.assertEqual(redacted["toolCatalog"][0]["clientSecret"], "set")
        self.assertEqual(redacted["connectors"][0]["accessToken"], "set")
        self.assertNotIn("sk-secret", output.getvalue())
        self.assertNotIn("catalog-secret", output.getvalue())
        self.assertNotIn("connector-secret", output.getvalue())

    def test_tools_catalog_json_prints_redacted_catalog_payload(self) -> None:
        args = type("Args", (), {"tools_action": "catalog", "limit": 3, "json": True})()

        def fake_backend_json(path: str, *, timeout: float = 5.0):
            if path == "/api/runtime":
                return True, "{}", {"provider": {"ready": True}}
            if path == "/api/tools/catalog":
                return True, "[]", [{"name": "desktop.click", "password": "catalog-secret"}]
            return False, "unexpected", None

        output = io.StringIO()
        with (
            mock.patch.object(atrium_cli, "ensure_repo_root"),
            mock.patch.object(atrium_cli, "backend_json", side_effect=fake_backend_json),
            redirect_stdout(output),
        ):
            self.assertEqual(atrium_cli.command_tools(args), 0)

        redacted = json.loads(output.getvalue())
        self.assertEqual(redacted[0]["password"], "set")
        self.assertNotIn("catalog-secret", output.getvalue())

    def test_automation_status_upgrades_legacy_parity_commands_from_stale_backend(self) -> None:
        args = type("Args", (), {"automation_action": "status", "commands": True})()
        old_fingerprint = "a" * 64
        current_fingerprint = "b" * 64
        current_manifest = "c" * 64
        legacy_verify = (
            "uv --project system run python ops/host_bridge_parity_report.py "
            "--macos /tmp/atrium_host_bridge_macos_live.json "
            "--windows /tmp/atrium_host_bridge_windows_live.json "
            "--windows-source-path 'C:\\Temp\\atrium_host_bridge_windows_live.json' "
            "--output data/host-bridge-parity-report.json"
        )
        payload = {
            "ok": False,
            "status": "cross_os_unverified",
            "summary": "missing Windows proof",
            "gaps": [
                "Run ops/host_bridge_parity_report.py with macOS and Windows --full probe artifacts before claiming cross-OS HostBridge parity."
            ],
            "commands": {
                "parityRunId": "atrium-run-1",
                "sourceFingerprint": old_fingerprint,
                "sourceManifestSha256": old_fingerprint,
                "sourceFileCount": "13",
                "macosSourceValidate": (
                    "uv --project system run python ops/host_bridge_source_summary.py "
                    f"--expect-source-fingerprint {old_fingerprint}"
                ),
                "macosProbe": (
                    "uv --project system run python ops/macos_host_bridge_probe.py "
                    f"--full --expect-source-fingerprint {old_fingerprint}"
                ),
                "macosArtifactValidate": (
                    "uv --project system run python ops/host_bridge_artifact_summary.py "
                    f"--label macos --expect-source-fingerprint {old_fingerprint}"
                ),
                "windowsProbe": (
                    "uv --project system run python ops/windows_host_bridge_probe.py "
                    f"--full --expect-source-fingerprint {old_fingerprint}"
                ),
                "windowsArtifactValidateOnWindows": (
                    "uv --project system run python ops/host_bridge_artifact_summary.py "
                    f"--label windows --expect-source-fingerprint {old_fingerprint}"
                ),
                "windowsLiveProofRunner": (
                    "powershell -File .\\ops\\windows_host_bridge_live_proof.ps1 "
                    f"-SourceFingerprint {old_fingerprint}"
                ),
                "windowsHandoff": (
                    "./atrium automation handoff --macos /tmp/atrium_host_bridge_macos_live.json "
                    "--output /tmp/atrium_windows_handoff.json"
                ),
                "verify": legacy_verify,
            },
        }
        output = io.StringIO()
        with (
            mock.patch.object(atrium_cli, "ensure_repo_root"),
            mock.patch.object(atrium_cli, "backend_json", return_value=(True, "{}", payload)),
            mock.patch.object(atrium_cli, "windows_native", return_value=False),
            mock.patch.object(
                atrium_cli,
                "current_source_summary",
                return_value={
                    "sourceFingerprint": current_fingerprint,
                    "sourceManifestSha256": current_manifest,
                    "sourceFileCount": 17,
                },
            ),
            redirect_stdout(output),
        ):
            self.assertEqual(atrium_cli.command_automation(args), 0)

        text = output.getvalue()
        self.assertIn(f"sourceFingerprint - {current_fingerprint}", text)
        self.assertIn(f"sourceManifestSha256 - {current_manifest}", text)
        self.assertIn("sourceFileCount - 17", text)
        self.assertIn(f"backendSourceFingerprint - {old_fingerprint}", text)
        self.assertIn(f"backendSourceManifestSha256 - {old_fingerprint}", text)
        self.assertIn("sourceFingerprintStatus - rewritten_from_stale_backend", text)
        self.assertIn(f"--expect-source-fingerprint {current_fingerprint}", text)
        self.assertIn(f"--expect-source-manifest-sha256 {current_manifest}", text)
        self.assertIn("--expect-source-file-count 17", text)
        self.assertIn("macosProbe -", text)
        self.assertIn("macosArtifactValidate -", text)
        self.assertIn("windowsProbe -", text)
        self.assertIn("windowsHandoff -", text)
        self.assertIn("windowsArtifactValidateOnWindows -", text)
        self.assertIn(f"-SourceFingerprint {current_fingerprint}", text)
        self.assertIn(f"-SourceManifestSha256 {current_manifest}", text)
        self.assertIn("-SourceFileCount 17", text)
        self.assertIn("Run ./atrium automation report", text)
        self.assertIn("automationReport", text)
        self.assertIn("./atrium automation report", text)
        self.assertIn("--windows-source-path 'C:\\Temp\\atrium_host_bridge_windows_live.json'", text)
        self.assertIn("verify - ./atrium automation audit", text)
        self.assertIn("legacyParityReport", text)
        self.assertIn("ops/host_bridge_parity_report.py", text)

    def test_normalize_parity_commands_rewrites_windows_native_live_proof_flags(self) -> None:
        old_fingerprint = "a" * 64
        current_fingerprint = "b" * 64
        current_manifest = "c" * 64
        normalized = atrium_cli.normalize_parity_commands(
            {
                "sourceFingerprint": old_fingerprint,
                "sourceManifestSha256": old_fingerprint,
                "sourceFileCount": "13",
                "windowsSourceValidate": (
                    ".\\atrium.ps1 automation source "
                    f"--expect-source-fingerprint {old_fingerprint}"
                ),
                "windowsProbe": (
                    ".\\atrium.ps1 automation windows-probe --full "
                    f"--expect-source-fingerprint {old_fingerprint}"
                ),
                "windowsLiveProofRunner": (
                    ".\\atrium.ps1 automation windows-live-proof "
                    f"--source-fingerprint {old_fingerprint} "
                    f"--source-manifest-sha256 {old_fingerprint} "
                    "--source-file-count 13"
                ),
                "windowsHandoff": (
                    ".\\atrium.ps1 automation handoff --macos /tmp/atrium_host_bridge_macos_live.json "
                    "--output /tmp/atrium_windows_handoff.json"
                ),
            },
            current_source={
                "sourceFingerprint": current_fingerprint,
                "sourceManifestSha256": current_manifest,
                "sourceFileCount": 17,
            },
        )

        self.assertIn(f"--expect-source-fingerprint {current_fingerprint}", normalized["windowsSourceValidate"])
        self.assertIn(f"--expect-source-manifest-sha256 {current_manifest}", normalized["windowsSourceValidate"])
        self.assertIn("--expect-source-file-count 17", normalized["windowsSourceValidate"])
        self.assertIn("--json", normalized["windowsSourceValidate"])
        self.assertIn(f"--expect-source-fingerprint {current_fingerprint}", normalized["windowsProbe"])
        self.assertIn(f"--expect-source-manifest-sha256 {current_manifest}", normalized["windowsProbe"])
        self.assertIn("--expect-source-file-count 17", normalized["windowsProbe"])
        self.assertIn(f"--source-fingerprint {current_fingerprint}", normalized["windowsLiveProofRunner"])
        self.assertIn(f"--source-manifest-sha256 {current_manifest}", normalized["windowsLiveProofRunner"])
        self.assertIn("--source-file-count 17", normalized["windowsLiveProofRunner"])
        self.assertEqual(
            normalized["windowsHandoff"],
            ".\\atrium.ps1 automation handoff --macos /tmp/atrium_host_bridge_macos_live.json --output /tmp/atrium_windows_handoff.json",
        )

    def test_parity_command_summary_normalizes_legacy_report_for_support_report(self) -> None:
        legacy_verify = (
            "uv --project system run python ops/host_bridge_parity_report.py "
            "--macos /tmp/atrium_host_bridge_macos_live.json "
            "--windows /tmp/atrium_host_bridge_windows_live.json "
            "--windows-source-path 'C:\\Temp\\atrium_host_bridge_windows_live.json' "
            "--output data/host-bridge-parity-report.json"
        )
        payload = {"commands": {"parityRunId": "atrium-run-1", "verify": legacy_verify}}

        with (
            mock.patch.object(atrium_cli, "windows_native", return_value=True),
            mock.patch.object(
                atrium_cli,
                "current_source_summary",
                return_value={"sourceFingerprint": "b" * 64, "sourceManifestSha256": "b" * 64, "sourceFileCount": 17},
            ),
        ):
            lines = atrium_cli.summarize_parity_command_payload(payload)

        text = "\n".join(lines)
        self.assertIn("automationReport=.\\atrium.ps1 automation report", text)
        self.assertIn("sourceManifestSha256=" + "b" * 64, text)
        self.assertIn("sourceFileCount=17", text)
        self.assertIn("verify=.\\atrium.ps1 automation audit", text)
        self.assertIn("legacyParityReport=uv --project system run python ops/host_bridge_parity_report.py", text)

    def test_automation_source_json_prints_pure_verifier_payload(self) -> None:
        args = type(
            "Args",
            (),
            {
                "automation_action": "source",
                "expect_source_fingerprint": "a" * 64,
                "expect_source_manifest_sha256": "b" * 64,
                "expect_source_file_count": 17,
                "json": True,
            },
        )()
        payload = {"ok": True, "sourceFingerprint": "a" * 64, "sourceManifestSha256": "b" * 64, "sourceFileCount": 17}
        output = io.StringIO()
        with (
            mock.patch.object(atrium_cli, "ensure_repo_root"),
            mock.patch.object(atrium_cli, "command_path", return_value="/bin/uv"),
            mock.patch.object(atrium_cli, "run", return_value=atrium_cli.CommandResult(0, json.dumps(payload), "")) as run,
            redirect_stdout(output),
        ):
            self.assertEqual(atrium_cli.command_automation(args), 0)

        printed = output.getvalue()
        self.assertNotIn("$ ", printed)
        self.assertEqual(json.loads(printed), payload)
        command = run.call_args.args[0]
        self.assertIn("ops/host_bridge_source_summary.py", command)
        self.assertIn("--expect-source-fingerprint", command)
        self.assertIn("a" * 64, command)
        self.assertIn("--expect-source-manifest-sha256", command)
        self.assertIn("b" * 64, command)
        self.assertIn("--expect-source-file-count", command)
        self.assertIn("17", command)

    def test_automation_source_json_returns_verifier_failure_code(self) -> None:
        args = type(
            "Args",
            (),
            {
                "automation_action": "source",
                "expect_source_fingerprint": "a" * 64,
                "expect_source_manifest_sha256": None,
                "expect_source_file_count": None,
                "json": True,
            },
        )()
        payload = {"ok": False, "findings": ["sourceFingerprint mismatch"]}
        output = io.StringIO()
        with (
            mock.patch.object(atrium_cli, "ensure_repo_root"),
            mock.patch.object(atrium_cli, "command_path", return_value="/bin/uv"),
            mock.patch.object(atrium_cli, "run", return_value=atrium_cli.CommandResult(1, json.dumps(payload), "")),
            redirect_stdout(output),
        ):
            self.assertEqual(atrium_cli.command_automation(args), 1)

        self.assertEqual(json.loads(output.getvalue()), payload)

    def test_automation_windows_probe_builds_uv_command(self) -> None:
        args = type(
            "Args",
            (),
            {
                "automation_action": "windows-probe",
                "simulate": True,
                "full": False,
                "screenshot": False,
                "notification": False,
                "interactive": False,
                "browser_url": "https://example.com",
                "browser_profile": "atrium",
                "parity_run_id": "atrium-run-1",
                "expect_source_fingerprint": "a" * 64,
                "expect_source_manifest_sha256": "b" * 64,
                "expect_source_file_count": 17,
                "output": "C:\\Temp\\probe.json",
            },
        )()
        with (
            mock.patch.object(atrium_cli, "ensure_repo_root"),
            mock.patch.object(atrium_cli, "command_path", return_value="/bin/uv"),
            mock.patch.object(atrium_cli, "run_interactive") as run_interactive,
            redirect_stdout(io.StringIO()),
        ):
            self.assertEqual(atrium_cli.command_automation(args), 0)

        command = run_interactive.call_args.args[0]
        self.assertEqual(command[:5], ["/bin/uv", "--project", "system", "run", "python"])
        self.assertIn("ops/windows_host_bridge_probe.py", command)
        self.assertIn("--simulate", command)
        self.assertIn("--browser-url", command)
        self.assertIn("https://example.com", command)
        self.assertIn("--expect-source-fingerprint", command)
        self.assertIn("--expect-source-manifest-sha256", command)
        self.assertIn("b" * 64, command)
        self.assertIn("--expect-source-file-count", command)
        self.assertIn("17", command)

    def test_automation_artifact_builds_uv_command(self) -> None:
        args = type(
            "Args",
            (),
            {
                "automation_action": "artifact",
                "label": "windows",
                "expect_parity_run_id": "atrium-run-1",
                "expect_source_fingerprint": "a" * 64,
                "expect_source_manifest_sha256": "b" * 64,
                "expect_source_file_count": 17,
                "max_artifact_age_hours": 12.0,
                "json": False,
                "artifact": "C:\\Temp\\atrium_host_bridge_windows_live.json",
            },
        )()
        with (
            mock.patch.object(atrium_cli, "ensure_repo_root"),
            mock.patch.object(atrium_cli, "command_path", return_value="/bin/uv"),
            mock.patch.object(atrium_cli, "run_interactive") as run_interactive,
            redirect_stdout(io.StringIO()),
        ):
            self.assertEqual(atrium_cli.command_automation(args), 0)

        command = run_interactive.call_args.args[0]
        self.assertEqual(command[:5], ["/bin/uv", "--project", "system", "run", "python"])
        self.assertIn("ops/host_bridge_artifact_summary.py", command)
        self.assertIn("--label", command)
        self.assertIn("windows", command)
        self.assertIn("--expect-parity-run-id", command)
        self.assertIn("atrium-run-1", command)
        self.assertIn("--expect-source-fingerprint", command)
        self.assertIn("a" * 64, command)
        self.assertIn("--expect-source-manifest-sha256", command)
        self.assertIn("b" * 64, command)
        self.assertIn("--expect-source-file-count", command)
        self.assertIn("17", command)
        self.assertIn("--max-artifact-age-hours", command)
        self.assertIn("12.0", command)
        self.assertEqual(command[-1], "C:\\Temp\\atrium_host_bridge_windows_live.json")

    def test_automation_artifact_json_prints_pure_verifier_payload(self) -> None:
        args = type(
            "Args",
            (),
            {
                "automation_action": "artifact",
                "label": "windows",
                "expect_parity_run_id": "atrium-run-1",
                "expect_source_fingerprint": "a" * 64,
                "expect_source_manifest_sha256": "b" * 64,
                "expect_source_file_count": 17,
                "max_artifact_age_hours": 12.0,
                "json": True,
                "artifact": "C:\\Temp\\atrium_host_bridge_windows_live.json",
            },
        )()
        payload = {"ok": True, "label": "windows", "sourceFileCount": 17}
        output = io.StringIO()
        with (
            mock.patch.object(atrium_cli, "ensure_repo_root"),
            mock.patch.object(atrium_cli, "command_path", return_value="/bin/uv"),
            mock.patch.object(atrium_cli, "run", return_value=atrium_cli.CommandResult(0, json.dumps(payload), "")) as run,
            redirect_stdout(output),
        ):
            self.assertEqual(atrium_cli.command_automation(args), 0)

        self.assertEqual(json.loads(output.getvalue()), payload)
        self.assertNotIn("$ ", output.getvalue())
        command = run.call_args.args[0]
        self.assertIn("--max-artifact-age-hours", command)
        self.assertIn("12.0", command)
        self.assertEqual(command[-1], "C:\\Temp\\atrium_host_bridge_windows_live.json")

    def test_automation_artifact_json_returns_verifier_failure_code(self) -> None:
        args = type(
            "Args",
            (),
            {
                "automation_action": "artifact",
                "label": "windows",
                "expect_parity_run_id": "atrium-run-1",
                "expect_source_fingerprint": None,
                "expect_source_manifest_sha256": None,
                "expect_source_file_count": None,
                "max_artifact_age_hours": 24.0,
                "json": True,
                "artifact": "C:\\Temp\\atrium_host_bridge_windows_live.json",
            },
        )()
        payload = {"ok": False, "findings": ["artifact is stale"]}
        output = io.StringIO()
        with (
            mock.patch.object(atrium_cli, "ensure_repo_root"),
            mock.patch.object(atrium_cli, "command_path", return_value="/bin/uv"),
            mock.patch.object(atrium_cli, "run", return_value=atrium_cli.CommandResult(1, json.dumps(payload), "")),
            redirect_stdout(output),
        ):
            self.assertEqual(atrium_cli.command_automation(args), 1)

        self.assertEqual(json.loads(output.getvalue()), payload)

    def test_automation_windows_live_proof_builds_powershell_command(self) -> None:
        args = type(
            "Args",
            (),
            {
                "automation_action": "windows-live-proof",
                "parity_run_id": "atrium-run-1",
                "source_fingerprint": "a" * 64,
                "source_manifest_sha256": "b" * 64,
                "source_file_count": 17,
                "output": "C:\\Temp\\atrium_host_bridge_windows_live.json",
                "max_artifact_age_hours": 24.0,
            },
        )()
        with (
            mock.patch.object(atrium_cli, "ensure_repo_root"),
            mock.patch.object(atrium_cli, "command_path", side_effect=lambda name: "powershell.exe" if name == "powershell.exe" else None),
            mock.patch.object(atrium_cli, "run_interactive") as run_interactive,
            redirect_stdout(io.StringIO()),
        ):
            self.assertEqual(atrium_cli.command_automation(args), 0)

        command = run_interactive.call_args.args[0]
        self.assertEqual(command[0], "powershell.exe")
        self.assertIn("-File", command)
        self.assertIn("windows_host_bridge_live_proof.ps1", " ".join(command))
        self.assertIn("-ParityRunId", command)
        self.assertIn("atrium-run-1", command)
        self.assertIn("-SourceManifestSha256", command)
        self.assertIn("b" * 64, command)
        self.assertIn("-SourceFileCount", command)
        self.assertIn("17", command)

    def test_automation_handoff_validates_macos_artifact_and_writes_packet(self) -> None:
        args = type(
            "Args",
            (),
            {
                "automation_action": "handoff",
                "macos": "/tmp/atrium_host_bridge_macos_live.json",
                "output": "/tmp/atrium_windows_handoff_test.json",
                "windows_output": "C:\\Temp\\atrium_host_bridge_windows_live.json",
                "windows_local_copy": "/tmp/atrium_host_bridge_windows_live.json",
                "max_artifact_age_hours": 24.0,
                "json": False,
            },
        )()
        source = {
            "sourceFingerprint": "a" * 64,
            "sourceManifestSha256": "a" * 64,
            "sourceFileCount": 18,
            "gitHead": "b" * 40,
        }
        artifact_summary = {
            "ok": True,
            "artifactSha256": "c" * 64,
            "parityRunId": "atrium-run-1",
            "hostPlatform": "darwin",
            "hostFingerprint": "d" * 64,
            "generatedAt": 1781010000000,
        }
        writes: dict[str, str] = {}

        def fake_run(command: list[str], **_kwargs: object) -> atrium_cli.CommandResult:
            text = " ".join(command)
            self.assertIn("ops/host_bridge_artifact_summary.py", text)
            self.assertIn("--expect-source-fingerprint", command)
            self.assertIn("a" * 64, command)
            self.assertIn("--expect-source-file-count", command)
            self.assertIn("18", command)
            return atrium_cli.CommandResult(0, json.dumps(artifact_summary), "")

        def fake_write_text(self_path: Path, text: str, **_kwargs: object) -> int:
            writes[str(self_path)] = text
            return len(text)

        with (
            mock.patch.object(atrium_cli, "ensure_repo_root"),
            mock.patch.object(atrium_cli, "current_source_summary", return_value=source),
            mock.patch.object(atrium_cli, "command_path", return_value="/bin/uv"),
            mock.patch.object(atrium_cli, "run", side_effect=fake_run),
            mock.patch.object(Path, "mkdir"),
            mock.patch.object(Path, "write_text", fake_write_text),
            redirect_stdout(io.StringIO()) as output,
        ):
            self.assertEqual(atrium_cli.command_automation(args), 0)

        self.assertIn("/tmp/atrium_windows_handoff_test.json", writes)
        payload = json.loads(writes["/tmp/atrium_windows_handoff_test.json"])
        self.assertTrue(payload["ok"])
        self.assertEqual(payload["source"]["sourceFileCount"], 18)
        self.assertEqual(payload["macosArtifact"]["parityRunId"], "atrium-run-1")
        self.assertIn(".\\atrium.ps1 automation windows-live-proof", payload["windowsProof"]["commands"]["liveProof"])
        self.assertIn("--json", payload["windowsProof"]["commands"]["sourceValidate"])
        self.assertIn("--max-artifact-age-hours 24.0", payload["windowsProof"]["commands"]["artifactValidate"])
        self.assertIn("--json", payload["windowsProof"]["commands"]["artifactValidate"])
        self.assertIn("--source-manifest-sha256 " + "a" * 64, payload["windowsProof"]["commands"]["liveProof"])
        self.assertIn("./atrium automation report", payload["finalVerification"]["commands"]["report"])
        self.assertIn("--max-artifact-age-hours 24.0", payload["finalVerification"]["commands"]["report"])
        self.assertIn("Windows proof handoff", output.getvalue())

    def test_automation_handoff_refuses_stale_macos_artifact(self) -> None:
        args = type(
            "Args",
            (),
            {
                "automation_action": "handoff",
                "macos": "/tmp/old_macos.json",
                "output": "/tmp/atrium_windows_handoff_test.json",
                "windows_output": "C:\\Temp\\atrium_host_bridge_windows_live.json",
                "windows_local_copy": "/tmp/atrium_host_bridge_windows_live.json",
                "max_artifact_age_hours": 24.0,
                "json": False,
            },
        )()
        source = {
            "sourceFingerprint": "a" * 64,
            "sourceManifestSha256": "a" * 64,
            "sourceFileCount": 18,
        }
        artifact_summary = {
            "ok": False,
            "findings": ["sourceFingerprint mismatch: artifact=old; expected=" + "a" * 64],
        }
        with (
            mock.patch.object(atrium_cli, "ensure_repo_root"),
            mock.patch.object(atrium_cli, "current_source_summary", return_value=source),
            mock.patch.object(atrium_cli, "command_path", return_value="/bin/uv"),
            mock.patch.object(atrium_cli, "run", return_value=atrium_cli.CommandResult(1, json.dumps(artifact_summary), "")),
            mock.patch.object(Path, "write_text") as write_text,
            self.assertRaises(atrium_cli.StepFailure) as raised,
        ):
            atrium_cli.command_automation(args)

        self.assertIn("not valid for handoff", str(raised.exception))
        write_text.assert_not_called()

    def test_automation_report_builds_parity_report_command(self) -> None:
        args = type(
            "Args",
            (),
            {
                "automation_action": "report",
                "macos": "/tmp/atrium_host_bridge_macos_live.json",
                "windows": "/tmp/atrium_host_bridge_windows_live.json",
                "output": str(atrium_cli.HOST_BRIDGE_PARITY_REPORT),
                "max_artifact_age_hours": 12.0,
                "windows_source_path": "C:\\Temp\\atrium_host_bridge_windows_live.json",
                "skip_current_source_check": False,
            },
        )()
        output = io.StringIO()
        with (
            mock.patch.object(atrium_cli, "ensure_repo_root"),
            mock.patch.object(atrium_cli, "command_path", return_value="/bin/uv"),
            mock.patch.object(atrium_cli, "run_interactive") as run_interactive,
            redirect_stdout(output),
        ):
            self.assertEqual(atrium_cli.command_automation(args), 0)

        command = run_interactive.call_args.args[0]
        self.assertEqual(command[:5], ["/bin/uv", "--project", "system", "run", "python"])
        self.assertIn("ops/host_bridge_parity_report.py", command)
        self.assertIn("--macos", command)
        self.assertIn("/tmp/atrium_host_bridge_macos_live.json", command)
        self.assertIn("--windows", command)
        self.assertIn("/tmp/atrium_host_bridge_windows_live.json", command)
        self.assertIn("--output", command)
        self.assertIn(str(atrium_cli.HOST_BRIDGE_PARITY_REPORT), command)
        self.assertIn("--max-artifact-age-hours", command)
        self.assertIn("12.0", command)
        self.assertIn("--windows-source-path", command)
        self.assertNotIn("--skip-current-source-check", command)
        self.assertIn("HostBridge parity report", output.getvalue())
        self.assertIn("backend default report path", output.getvalue())

    def test_automation_report_can_forward_historical_source_override(self) -> None:
        args = type(
            "Args",
            (),
            {
                "automation_action": "report",
                "macos": "/tmp/macos.json",
                "windows": "/tmp/windows.json",
                "output": "/tmp/report.json",
                "max_artifact_age_hours": 0,
                "windows_source_path": "D:\\proof\\windows.json",
                "skip_current_source_check": True,
            },
        )()
        with (
            mock.patch.object(atrium_cli, "ensure_repo_root"),
            mock.patch.object(atrium_cli, "command_path", return_value="/bin/uv"),
            mock.patch.object(atrium_cli, "run_interactive") as run_interactive,
            redirect_stdout(io.StringIO()),
        ):
            self.assertEqual(atrium_cli.command_automation(args), 0)

        command = run_interactive.call_args.args[0]
        self.assertIn("--skip-current-source-check", command)
        self.assertIn("D:\\proof\\windows.json", command)

    def test_automation_report_refuses_historical_override_for_backend_default_path(self) -> None:
        args = type(
            "Args",
            (),
            {
                "automation_action": "report",
                "macos": "/tmp/macos.json",
                "windows": "/tmp/windows.json",
                "output": str(atrium_cli.HOST_BRIDGE_PARITY_REPORT),
                "max_artifact_age_hours": 0,
                "windows_source_path": "D:\\proof\\windows.json",
                "skip_current_source_check": True,
            },
        )()
        with (
            mock.patch.object(atrium_cli, "ensure_repo_root"),
            mock.patch.object(atrium_cli, "command_path", return_value="/bin/uv"),
            mock.patch.object(atrium_cli, "run_interactive") as run_interactive,
            self.assertRaises(atrium_cli.StepFailure) as raised,
        ):
            atrium_cli.command_automation(args)

        self.assertIn("custom --output path", str(raised.exception))
        run_interactive.assert_not_called()


if __name__ == "__main__":
    unittest.main()
