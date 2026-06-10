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

    def test_http_get_json_reports_connection_reset_without_traceback(self) -> None:
        with mock.patch.object(atrium_cli.urllib.request, "urlopen", side_effect=ConnectionResetError("reset by peer")):
            ok, raw, payload = atrium_cli.http_get_json("http://127.0.0.1:8787/api/runtime")

        self.assertFalse(ok)
        self.assertIn("reset by peer", raw)
        self.assertIsNone(payload)

    def test_http_json_request_reports_connection_reset_without_traceback(self) -> None:
        with mock.patch.object(atrium_cli.urllib.request, "urlopen", side_effect=ConnectionResetError("reset by peer")):
            ok, raw, payload = atrium_cli.http_json_request(
                "http://127.0.0.1:8787/api/permissions/mode",
                method="PATCH",
                payload={"permissionMode": "full_auto"},
            )

        self.assertFalse(ok)
        self.assertIn("reset by peer", raw)
        self.assertIsNone(payload)

    def test_windows_docs_artifact_validation_examples_require_source_identity(self) -> None:
        for rel_path in ("README.md", "ops/README.md"):
            text = (ROOT / rel_path).read_text(encoding="utf-8")
            for line in text.splitlines():
                if ".\\atrium.ps1 automation artifact --label windows" not in line:
                    continue
                self.assertIn("--expect-parity-run-id", line, rel_path)
                self.assertIn("--expect-source-fingerprint", line, rel_path)
                self.assertIn("--expect-source-manifest-sha256", line, rel_path)
                self.assertIn("--expect-source-file-count", line, rel_path)
                self.assertIn("--max-artifact-age-hours 24.0", line, rel_path)
                self.assertIn("--json", line, rel_path)

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

    def test_windows_powershell_command_accepts_standard_install_path(self) -> None:
        def fake_exists(path: Path) -> bool:
            return str(path).replace("\\", "/").endswith("Windows/System32/WindowsPowerShell/v1.0/powershell.exe")

        with (
            mock.patch.object(atrium_cli.shutil, "which", return_value=None),
            mock.patch.object(Path, "exists", fake_exists),
            mock.patch.object(atrium_cli.os, "environ", {"SystemRoot": "C:/Windows"}),
        ):
            self.assertEqual(
                atrium_cli.powershell_command(),
                "C:/Windows/System32/WindowsPowerShell/v1.0/powershell.exe",
            )

    def test_winget_install_retries_after_source_refresh(self) -> None:
        calls: list[list[str]] = []

        def fake_run_interactive(command: list[str], *, dry_run: bool = False, **_kwargs: object) -> atrium_cli.CommandResult:
            calls.append(command)
            if command[:2] == ["winget.exe", "install"] and len(calls) == 1:
                raise atrium_cli.StepFailure("source not initialized")
            return atrium_cli.CommandResult(0, "", "")

        with mock.patch.object(atrium_cli, "run_interactive", side_effect=fake_run_interactive):
            atrium_cli.run_winget_install("winget.exe", "Google.Chrome", "Google Chrome", dry_run=False)

        self.assertEqual(calls[0][:4], ["winget.exe", "install", "--id", "Google.Chrome"])
        self.assertEqual(calls[1], ["winget.exe", "source", "update"])
        self.assertEqual(calls[2][:4], ["winget.exe", "install", "--id", "Google.Chrome"])
        self.assertIn("--accept-source-agreements", calls[2])
        self.assertIn("--accept-package-agreements", calls[2])

    def test_doctor_json_includes_windows_native_preflight_surfaces(self) -> None:
        def fake_backend_json(path: str, *, timeout: float = 5.0):
            payloads = {
                "/health": {"ok": True},
                "/api/runtime": {"ok": True, "running": True},
                "/api/provider-auth/status": {"ok": True, "chatgptAccount": {"ready": True}},
                "/api/provider-auth/reference": {"credentials": [{"id": "chatgpt_account", "configured": True}], "providers": [], "subsystems": []},
                "/api/provider-auth/env": {"envPath": "system/.env", "groups": [{"fields": [{"key": "ATRIUM_OPENAI_API_KEY", "configured": True}]}]},
                "/api/permissions/mode": {"ok": True, "permissionMode": "owner"},
                "/api/connectors": [{"id": "browser"}, {"id": "mcp", "status": "configured", "readReady": True, "writeReady": True, "localFallback": False}],
                "/api/tools/catalog": [{"name": "browser.open"}],
                "/api/tools/mcp-gateway?probe=true": {
                    "ok": True,
                    "ready": True,
                    "gatewayHealth": {"ok": True},
                    "connector": {"id": "mcp", "status": "configured", "readReady": True, "writeReady": True, "localFallback": False},
                    "requirements": [],
                },
                "/api/host-bridge/parity": {"ok": False, "status": "cross_os_unverified", "commands": {}},
            }
            return True, "ok", payloads[path]

        with (
            mock.patch.object(atrium_cli, "ensure_repo_root"),
            mock.patch.object(atrium_cli.platform, "system", return_value="Windows"),
            mock.patch.object(atrium_cli, "is_i_cloud_risky", return_value=(False, "ok")),
            mock.patch.object(atrium_cli, "remote_ok", return_value=(True, "origin")),
            mock.patch.object(atrium_cli, "git_status_summary", return_value="clean"),
            mock.patch.object(atrium_cli, "report_command_path", side_effect=lambda name: f"{name}.exe"),
            mock.patch.object(atrium_cli, "docker_compose_cmd", return_value=["docker", "compose"]),
            mock.patch.object(atrium_cli, "browser_installed", return_value=True),
            mock.patch.object(atrium_cli, "port_open", return_value=False),
            mock.patch.object(atrium_cli, "backend_json", side_effect=fake_backend_json),
            mock.patch.object(atrium_cli, "current_source_summary", return_value={"sourceFingerprint": "a" * 64}),
            mock.patch.object(atrium_cli, "collect_windows_runtime_payload", return_value={"windowsNative": True, "powershell": {"command": "powershell.exe"}}),
            mock.patch.object(atrium_cli, "collect_windows_entrypoints_payload", return_value={"ok": True, "checks": {"atriumCmdForwarder": True}}),
            redirect_stdout(io.StringIO()) as output,
        ):
            args = type("Args", (), {"json": True})()
            self.assertEqual(atrium_cli.command_doctor(args), 0)

        payload = json.loads(output.getvalue())
        self.assertEqual(payload["launcherMode"], "windows-native")
        self.assertIn("/api/runtime", payload["runtimeHttp"])
        self.assertIn("providerAuth", payload)
        self.assertEqual(payload["providerReference"]["credentials"][0]["id"], "chatgpt_account")
        self.assertEqual(payload["providerEnv"]["envPath"], "system/.env")
        self.assertEqual(payload["toolCatalog"][0]["name"], "browser.open")
        self.assertTrue(payload["mcpGateway"]["ready"])
        self.assertTrue(payload["mcpProbe"]["ready"])
        self.assertIn("automationPermission", payload)
        self.assertEqual(payload["nativeNextChecks"]["host"], "windows")
        self.assertIn("nativeBrowserDesktopSmoke", payload["nativeNextChecks"]["commands"])
        self.assertEqual(payload["nativeNextChecks"]["commands"]["nativeProviderDisconnectChatGPT"], ".\\atrium.ps1 provider disconnect chatgpt")
        checklist_ids = {item["id"] for item in payload["nativeNextChecks"]["operatorChecklist"]}
        self.assertIn("native_provider_ai_tools", checklist_ids)
        self.assertIn("native_browser_desktop_smoke", checklist_ids)
        provider_step = next(item for item in payload["nativeNextChecks"]["operatorChecklist"] if item["id"] == "native_provider_ai_tools")
        self.assertIn(".\\atrium.ps1 provider disconnect claude-code", provider_step["accountSwitchCommands"])
        matrix_surfaces = {item["id"] for item in payload["nativeParityMatrix"]["surfaces"]}
        self.assertIn("install", matrix_surfaces)
        self.assertIn("provider_login", matrix_surfaces)
        self.assertIn("browser_desktop_tools", matrix_surfaces)
        self.assertTrue(payload["nativeParityMatrix"]["windowsNativeHostOnly"])
        self.assertTrue(payload["windowsRuntime"]["windowsNative"])
        self.assertTrue(payload["windowsEntryPoints"]["checks"]["atriumCmdForwarder"])

    def test_launcher_mode_uses_specific_native_runtime_labels(self) -> None:
        with mock.patch.object(atrium_cli.platform, "system", return_value="Windows"):
            self.assertEqual(atrium_cli.launcher_mode(), "windows-native")
        with mock.patch.object(atrium_cli.platform, "system", return_value="Darwin"):
            self.assertEqual(atrium_cli.launcher_mode(), "macos-screen")
        with mock.patch.object(atrium_cli.platform, "system", return_value="Linux"):
            self.assertEqual(atrium_cli.launcher_mode(), "linux-screen")

    def test_native_next_checks_print_windows_openclaw_checklist(self) -> None:
        output = io.StringIO()
        with (
            mock.patch.object(atrium_cli, "windows_native", return_value=True),
            redirect_stdout(output),
        ):
            atrium_cli.print_native_next_checks()

        text = output.getvalue()
        self.assertIn("Next Native Checks", text)
        self.assertIn("native_provider_ai_tools", text)
        self.assertIn(".\\atrium.ps1 provider status --probe --json", text)
        self.assertIn(".\\atrium.ps1 provider login chatgpt", text)
        self.assertIn(".\\atrium.ps1 provider disconnect chatgpt", text)
        self.assertIn("native_provider_ai_tools.accountSwitch", text)
        self.assertIn("native_browser_desktop_smoke", text)
        self.assertIn(".\\atrium.ps1 automation smoke", text)
        self.assertIn("requiredGate", text)

    def test_native_next_checks_print_macos_checklist(self) -> None:
        output = io.StringIO()
        with (
            mock.patch.object(atrium_cli, "windows_native", return_value=False),
            redirect_stdout(output),
        ):
            atrium_cli.print_native_next_checks()

        text = output.getvalue()
        self.assertIn("Next Native Checks", text)
        self.assertIn("./atrium provider status --probe --json", text)
        self.assertIn("./atrium provider disconnect chatgpt", text)
        self.assertIn("./atrium provider disconnect claude-code", text)
        self.assertIn("./atrium permissions set full_auto --agent-full-access true", text)
        self.assertIn("./atrium tools mcp-gateway --json", text)
        self.assertIn("./atrium automation smoke --browser-url http://127.0.0.1:5173", text)
        self.assertIn("./atrium report --bundle", text)

    def test_setup_no_start_prints_next_native_checks_before_return(self) -> None:
        args = type("Args", (), {"dry_run": False, "yes": True, "no_start": True, "no_open": True, "force": False, "wait_seconds": 1})()
        output = io.StringIO()
        with (
            mock.patch.object(atrium_cli, "ensure_repo_root"),
            mock.patch.object(atrium_cli, "command_doctor", return_value=0),
            mock.patch.object(atrium_cli, "command_bootstrap", return_value=0),
            mock.patch.object(atrium_cli, "print_native_next_checks") as next_checks,
            redirect_stdout(output),
        ):
            self.assertEqual(atrium_cli.command_setup(args), 0)

        next_checks.assert_called_once()
        self.assertIn("Setup prepared ATRIUM without starting services.", output.getvalue())

    def test_setup_dry_run_prints_next_native_checks_before_return(self) -> None:
        args = type("Args", (), {"dry_run": True, "yes": True, "no_start": False, "no_open": True, "force": False, "wait_seconds": 1})()
        output = io.StringIO()
        with (
            mock.patch.object(atrium_cli, "ensure_repo_root"),
            mock.patch.object(atrium_cli, "command_doctor", return_value=0),
            mock.patch.object(atrium_cli, "command_bootstrap", return_value=0),
            mock.patch.object(atrium_cli, "print_native_next_checks") as next_checks,
            redirect_stdout(output),
        ):
            self.assertEqual(atrium_cli.command_setup(args), 0)

        next_checks.assert_called_once()
        self.assertIn("[DRY-RUN]", output.getvalue())

    def test_doctor_human_prints_windows_entrypoint_proof_on_non_windows_host(self) -> None:
        def fake_backend_json(path: str, *, timeout: float = 5.0):
            if path == "/api/host-bridge/parity":
                return True, "{}", {"ok": False, "status": "cross_os_unverified", "commands": {}}
            if path in {"/health", "/api/runtime", "/api/provider-auth/status", "/api/permissions/mode", "/api/connectors"}:
                return True, "{}", {"ok": True}
            return False, "unexpected", None

        entrypoints = {
            "ok": True,
            "checks": {"openclawLifecycleProofPackage": True},
            "openclawLifecycleProof": {
                "ok": True,
                "commands": {
                    "nativeBrowserDesktopSmoke": ".\\atrium.ps1 automation smoke --browser-url http://127.0.0.1:5173 --browser-profile atrium --output C:\\Temp\\atrium_host_bridge_windows_smoke.json",
                    "nativeToolsStatusJson": ".\\atrium.ps1 tools status --json",
                },
                "operatorChecklist": [{"id": "native_browser_desktop_smoke"}],
            },
        }
        args = type("Args", (), {"json": False})()
        output = io.StringIO()
        with (
            mock.patch.object(atrium_cli, "ensure_repo_root"),
            mock.patch.object(atrium_cli, "is_i_cloud_risky", return_value=(False, "ok")),
            mock.patch.object(atrium_cli, "remote_ok", return_value=(True, "origin")),
            mock.patch.object(atrium_cli, "git_status_summary", return_value="clean"),
            mock.patch.object(atrium_cli, "report_command_path", return_value=None),
            mock.patch.object(atrium_cli, "docker_compose_cmd", return_value=None),
            mock.patch.object(atrium_cli, "browser_installed", return_value=True),
            mock.patch.object(atrium_cli, "port_open", return_value=False),
            mock.patch.object(atrium_cli, "render_env_status", return_value=[]),
            mock.patch.object(atrium_cli, "provider_status_from_env", return_value=[]),
            mock.patch.object(atrium_cli, "backend_json", side_effect=fake_backend_json),
            mock.patch.object(atrium_cli, "collect_windows_entrypoints_payload", return_value=entrypoints),
            mock.patch.object(atrium_cli, "current_source_summary", return_value={"sourceFingerprint": "b" * 64}),
            mock.patch.object(atrium_cli, "collect_local_proof_artifacts", return_value={}),
            redirect_stdout(output),
        ):
            self.assertEqual(atrium_cli.command_doctor(args), 0)

        text = output.getvalue()
        self.assertIn("Windows Native Entrypoints", text)
        self.assertIn("windows.entrypoints.openclawLifecycleProof.nativeBrowserDesktopSmoke", text)
        self.assertIn(".\\atrium.ps1 automation smoke", text)
        self.assertIn("windows.entrypoints.openclawLifecycleProof.nativeToolsStatusJson", text)
        self.assertIn("Native Parity Matrix", text)
        self.assertIn("native.parity.provider_login", text)

    def test_windows_entrypoints_payload_checks_live_proof_failure_artifact_runner(self) -> None:
        payload = atrium_cli.collect_windows_entrypoints_payload()

        self.assertIn("liveProofRunner", payload["checks"])
        self.assertTrue(payload["checks"]["liveProofRunner"])
        self.assertTrue(payload["checks"]["postStartReadiness"])
        self.assertTrue(payload["checks"]["nativeInstallerNextChecks"])
        self.assertTrue(payload["checks"]["openclawLifecycleProofPackage"])
        self.assertTrue(payload["openclawLifecycleProof"]["ok"])
        proof_commands = payload["openclawLifecycleProof"]["commands"]
        self.assertEqual(proof_commands["nativeSetup"], ".\\atrium.ps1 setup --yes")
        self.assertEqual(proof_commands["nativeStart"], ".\\atrium.ps1 start")
        self.assertEqual(proof_commands["nativeStop"], ".\\atrium.ps1 stop")
        self.assertEqual(proof_commands["nativeRestart"], ".\\atrium.ps1 restart --force")
        self.assertEqual(proof_commands["nativeStatusJson"], ".\\atrium.ps1 status --json")
        self.assertEqual(proof_commands["nativeLogsJson"], ".\\atrium.ps1 logs --json")
        self.assertEqual(proof_commands["nativeReportBundle"], ".\\atrium.ps1 report --bundle")
        self.assertEqual(proof_commands["nativePermissionsStatusJson"], ".\\atrium.ps1 permissions status --json")
        self.assertEqual(proof_commands["nativePermissionsSetFullAuto"], ".\\atrium.ps1 permissions set full_auto --agent-full-access true")
        self.assertEqual(proof_commands["nativeProviderStatusProbeJson"], ".\\atrium.ps1 provider status --probe --json")
        self.assertEqual(proof_commands["nativeProviderReferenceJson"], ".\\atrium.ps1 provider reference --json")
        self.assertEqual(proof_commands["nativeProviderEnvJson"], ".\\atrium.ps1 provider env --json")
        self.assertEqual(proof_commands["nativeProviderLoginChatGPT"], ".\\atrium.ps1 provider login chatgpt")
        self.assertEqual(proof_commands["nativeProviderLoginClaudeCode"], ".\\atrium.ps1 provider login claude-code")
        self.assertEqual(proof_commands["nativeProviderDisconnectChatGPT"], ".\\atrium.ps1 provider disconnect chatgpt")
        self.assertEqual(proof_commands["nativeProviderDisconnectClaudeCode"], ".\\atrium.ps1 provider disconnect claude-code")
        self.assertEqual(proof_commands["nativeToolsStatusJson"], ".\\atrium.ps1 tools status --json")
        self.assertEqual(proof_commands["nativeToolsCatalogJson"], ".\\atrium.ps1 tools catalog --json")
        self.assertEqual(proof_commands["mcpGatewaySetupJson"], ".\\atrium.ps1 tools mcp-gateway --json")
        self.assertEqual(proof_commands["mcpGatewayProbeJson"], ".\\atrium.ps1 tools mcp-probe --json")
        self.assertEqual(proof_commands["mcpGatewayStatusJson"], ".\\atrium.ps1 tools status --json")
        self.assertIn(".\\atrium.ps1 automation smoke", proof_commands["nativeBrowserDesktopSmoke"])
        self.assertIn("C:\\Temp\\atrium_host_bridge_windows_smoke.json", proof_commands["nativeBrowserDesktopSmoke"])
        self.assertIn(".\\atrium.ps1 automation source", proof_commands["sourceValidate"])
        self.assertIn(".\\atrium.ps1 automation windows-live-proof", proof_commands["windowsLiveProof"])
        self.assertIn(".\\atrium.ps1 automation artifact", proof_commands["windowsArtifactValidate"])
        self.assertEqual(proof_commands["windowsArtifactValidate"].count("--expect-parity-run-id"), 1)
        self.assertIn("automation report", proof_commands["report"])
        self.assertIn("automation audit", proof_commands["audit"])
        self.assertEqual(
            [item["id"] for item in payload["openclawLifecycleProof"]["operatorChecklist"]],
            [
                "native_setup_start",
                "native_permissions",
                "native_provider_ai_tools",
                "native_logs_report",
                "native_stop_restart",
                "mcp_gateway_setup",
                "mcp_gateway_probe",
                "mcp_gateway_status",
                "native_browser_desktop_smoke",
                "windows_raw_probe",
                "source_validate",
                "windows_live_proof",
                "artifact_validate_on_windows",
                "copy_to_repo_host",
                "accept_windows_artifact",
                "generate_report",
                "audit_gate",
            ],
        )
        summary = "\n".join(atrium_cli.summarize_windows_entrypoints_payload(payload))
        self.assertIn("windows.entrypoints.openclawLifecycleProof.nativeSetup=.\\atrium.ps1 setup --yes", summary)
        self.assertIn("windows.entrypoints.openclawLifecycleProof.nativeProviderStatusProbeJson=.\\atrium.ps1 provider status --probe --json", summary)
        self.assertIn("windows.entrypoints.openclawLifecycleProof.nativeProviderLoginChatGPT=.\\atrium.ps1 provider login chatgpt", summary)
        self.assertIn("windows.entrypoints.openclawLifecycleProof.nativeProviderDisconnectChatGPT=.\\atrium.ps1 provider disconnect chatgpt", summary)
        self.assertIn("windows.entrypoints.openclawLifecycleProof.nativeToolsStatusJson=.\\atrium.ps1 tools status --json", summary)
        self.assertIn("windows.entrypoints.openclawLifecycleProof.mcpGatewayProbeJson=.\\atrium.ps1 tools mcp-probe --json", summary)
        self.assertIn("windows.entrypoints.openclawLifecycleProof.nativePermissionsSetFullAuto=.\\atrium.ps1 permissions set full_auto --agent-full-access true", summary)
        self.assertIn("windows.entrypoints.openclawLifecycleProof.nativeBrowserDesktopSmoke=.\\atrium.ps1 automation smoke", summary)
        self.assertIn("windows.entrypoints.openclawLifecycleProof.checklist=native_setup_start, native_permissions, native_provider_ai_tools", summary)

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

    def test_windows_process_identity_redacts_command_line(self) -> None:
        result_payload = {
            "ProcessId": 1234,
            "ParentProcessId": 1200,
            "Name": "python.exe",
            "ExecutablePath": "C:\\Python312\\python.exe",
            "CommandLine": "python -m uvicorn app.main --ATRIUM_OPENAI_API_KEY=sk-secret",
        }
        with (
            mock.patch.object(atrium_cli.platform, "system", return_value="Windows"),
            mock.patch.object(atrium_cli, "powershell_command", return_value="powershell.exe"),
            mock.patch.object(
                atrium_cli,
                "run",
                return_value=atrium_cli.CommandResult(0, json.dumps(result_payload), ""),
            ) as run,
        ):
            identity = atrium_cli.windows_process_identity(1234)

        self.assertIsNotNone(identity)
        assert identity is not None
        self.assertTrue(identity["ok"])
        self.assertEqual(identity["CommandLine"], "python -m uvicorn app.main --ATRIUM_OPENAI_API_KEY=set")
        self.assertNotIn("sk-secret", json.dumps(identity))
        command = run.call_args.args[0]
        self.assertEqual(command[0], "powershell.exe")
        self.assertIn("Win32_Process", command[-1])
        self.assertIn("ProcessId = 1234", command[-1])

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

    def test_windows_popen_command_wraps_cmd_shims(self) -> None:
        with (
            mock.patch.object(atrium_cli.platform, "system", return_value="Windows"),
            mock.patch.object(atrium_cli, "command_path", side_effect=lambda name: "C:/Windows/System32/cmd.exe" if name == "cmd.exe" else None),
        ):
            command = atrium_cli.windows_popen_command(["C:/Users/me/AppData/Roaming/npm/pnpm.cmd", "dev", "--port", "5173"])

        self.assertEqual(command[:4], ["C:/Windows/System32/cmd.exe", "/D", "/S", "/C"])
        self.assertIn("pnpm.cmd", command[4])
        self.assertIn("--port", command[4])

    def test_windows_popen_command_keeps_exe_direct(self) -> None:
        with mock.patch.object(atrium_cli.platform, "system", return_value="Windows"):
            command = atrium_cli.windows_popen_command(["C:/tools/uv.exe", "run", "python"])

        self.assertEqual(command, ["C:/tools/uv.exe", "run", "python"])

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

    def test_provider_reference_json_is_redacted(self) -> None:
        args = type("Args", (), {"provider_action": "reference", "json": True})()
        payload = {
            "credentials": [
                {
                    "id": "chatgpt_account_oauth",
                    "configured": True,
                    "email": "owner@example.com",
                    "accessToken": "secret-token",
                }
            ],
            "providers": [{"id": "chatgpt_account"}],
        }
        output = io.StringIO()
        with (
            mock.patch.object(atrium_cli, "ensure_repo_root"),
            mock.patch.object(atrium_cli, "backend_json", return_value=(True, "{}", payload)) as backend_json,
            redirect_stdout(output),
        ):
            self.assertEqual(atrium_cli.command_provider(args), 0)

        backend_json.assert_called_once_with("/api/provider-auth/reference", timeout=10.0)
        redacted = output.getvalue()
        self.assertIn('"email": "set"', redacted)
        self.assertIn('"accessToken": "set"', redacted)
        self.assertNotIn("owner@example.com", redacted)
        self.assertNotIn("secret-token", redacted)

    def test_provider_env_summary_does_not_print_secret_values(self) -> None:
        args = type("Args", (), {"provider_action": "env", "json": False})()
        payload = {
            "envPath": "C:/repo/system/.env",
            "groups": [
                {
                    "id": "openai",
                    "fields": [
                        {
                            "key": "ATRIUM_OPENAI_API_KEY",
                            "kind": "secret",
                            "configured": True,
                            "maskedValue": "sk-****cret",
                        },
                        {"key": "ATRIUM_OPENAI_BASE_URL", "configured": True, "value": "https://api.example.test"},
                    ],
                }
            ],
            "restartRecommended": False,
            "processOverrides": ["ATRIUM_OPENAI_API_KEY"],
        }
        output = io.StringIO()
        with (
            mock.patch.object(atrium_cli, "ensure_repo_root"),
            mock.patch.object(atrium_cli, "backend_json", return_value=(True, "{}", payload)) as backend_json,
            redirect_stdout(output),
        ):
            self.assertEqual(atrium_cli.command_provider(args), 0)

        backend_json.assert_called_once_with("/api/provider-auth/env", timeout=10.0)
        text = output.getvalue()
        self.assertIn("configuredFields=2", text)
        self.assertIn("ATRIUM_OPENAI_API_KEY", text)
        self.assertNotIn("sk-****cret", text)

    def test_permissions_status_json_is_redacted(self) -> None:
        args = type("Args", (), {"permissions_action": "status", "json": True})()
        payload = {
            "mode": "full_auto",
            "agentFullAccess": True,
            "fullAutonomyStatus": {"active": True, "credentials": {"apiKey": "sk-secret"}},
        }
        output = io.StringIO()
        with (
            mock.patch.object(atrium_cli, "ensure_repo_root"),
            mock.patch.object(atrium_cli, "backend_json", return_value=(True, "{}", payload)) as backend_json,
            redirect_stdout(output),
        ):
            self.assertEqual(atrium_cli.command_permissions(args), 0)

        backend_json.assert_called_once_with("/api/permissions/mode", timeout=5.0)
        redacted = json.loads(output.getvalue())
        self.assertEqual(redacted["fullAutonomyStatus"]["credentials"]["apiKey"], "set")
        self.assertNotIn("sk-secret", output.getvalue())

    def test_permissions_set_patches_owner_permission_mode(self) -> None:
        args = type(
            "Args",
            (),
            {
                "permissions_action": "set",
                "mode": "full_auto",
                "agent_full_access": True,
                "allowed_tools": ["browser.open,desktop.act"],
                "denied_tools": ["dangerous.tool"],
                "allowed_risk_classes": ["read,desktop"],
                "denied_risk_classes": [],
                "command_allowlist": ["git status"],
                "command_denylist": ["rm -rf"],
                "ask_fallback": "deny",
                "strict_inline_eval": True,
                "updated_by": "windows-cli",
                "json": True,
            },
        )()
        result = {
            "mode": "full_auto",
            "agentFullAccess": True,
            "fullAutonomyStatus": {"active": True, "credentials": {"apiKey": "sk-secret"}},
        }
        output = io.StringIO()
        with (
            mock.patch.object(atrium_cli, "ensure_repo_root"),
            mock.patch.object(atrium_cli, "backend_json_request", return_value=(True, "{}", result)) as backend_request,
            redirect_stdout(output),
        ):
            self.assertEqual(atrium_cli.command_permissions(args), 0)

        backend_request.assert_called_once()
        path = backend_request.call_args.args[0]
        kwargs = backend_request.call_args.kwargs
        self.assertEqual(path, "/api/permissions/mode")
        self.assertEqual(kwargs["method"], "PATCH")
        self.assertEqual(kwargs["payload"]["mode"], "full_auto")
        self.assertTrue(kwargs["payload"]["agentFullAccess"])
        self.assertEqual(kwargs["payload"]["allowedTools"], ["browser.open", "desktop.act"])
        self.assertEqual(kwargs["payload"]["deniedTools"], ["dangerous.tool"])
        self.assertEqual(kwargs["payload"]["allowedRiskClasses"], ["read", "desktop"])
        self.assertEqual(kwargs["payload"]["commandAllowlist"], ["git status"])
        self.assertEqual(kwargs["payload"]["commandDenylist"], ["rm -rf"])
        self.assertEqual(kwargs["payload"]["askFallback"], "deny")
        self.assertTrue(kwargs["payload"]["strictInlineEval"])
        self.assertEqual(kwargs["payload"]["updatedBy"], "windows-cli")
        self.assertNotIn("sk-secret", output.getvalue())

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

    def test_status_human_prints_windows_entrypoint_proof_on_non_windows_host(self) -> None:
        def fake_backend_json(path: str, *, timeout: float = 5.0):
            if path == "/api/host-bridge/parity":
                return True, "{}", {"ok": False, "status": "cross_os_unverified", "commands": {}}
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

        entrypoints = {
            "ok": True,
            "checks": {"openclawLifecycleProofPackage": True},
            "openclawLifecycleProof": {
                "ok": True,
                "commands": {
                    "nativeBrowserDesktopSmoke": ".\\atrium.ps1 automation smoke --browser-url http://127.0.0.1:5173 --browser-profile atrium --output C:\\Temp\\atrium_host_bridge_windows_smoke.json",
                    "nativeProviderStatusProbeJson": ".\\atrium.ps1 provider status --probe --json",
                },
                "operatorChecklist": [{"id": "native_browser_desktop_smoke"}],
            },
        }
        args = type("Args", (), {"json": False})()
        output = io.StringIO()
        with (
            mock.patch.object(atrium_cli, "ensure_repo_root"),
            mock.patch.object(atrium_cli, "windows_native", return_value=False),
            mock.patch.object(atrium_cli, "screen_sessions", return_value=""),
            mock.patch.object(atrium_cli, "port_open", return_value=False),
            mock.patch.object(atrium_cli, "command_path", return_value=None),
            mock.patch.object(atrium_cli, "docker_compose_cmd", return_value=None),
            mock.patch.object(atrium_cli, "backend_json", side_effect=fake_backend_json),
            mock.patch.object(atrium_cli, "provider_status_from_env", return_value=[]),
            mock.patch.object(atrium_cli, "collect_windows_entrypoints_payload", return_value=entrypoints),
            mock.patch.object(atrium_cli, "collect_local_proof_artifacts", return_value={}),
            mock.patch.object(atrium_cli, "current_source_summary", return_value={"sourceFingerprint": "b" * 64}),
            redirect_stdout(output),
        ):
            self.assertEqual(atrium_cli.command_status(args), 0)

        text = output.getvalue()
        self.assertIn("Windows Native Entrypoints", text)
        self.assertIn("windows.entrypoints.openclawLifecycleProof.nativeBrowserDesktopSmoke", text)
        self.assertIn(".\\atrium.ps1 automation smoke", text)
        self.assertIn("windows.entrypoints.openclawLifecycleProof.nativeProviderStatusProbeJson", text)
        self.assertIn("Native Parity Matrix", text)
        self.assertIn("native.parity.logs_support_report", text)

    def test_status_payload_includes_local_proof_artifacts(self) -> None:
        local_artifacts = {
            "currentSourceFingerprint": "b" * 64,
            "macos": {"exists": True, "ok": True, "sourceStatus": "current", "usable": True, "parityRunId": "atrium-run-1"},
            "handoff": {"exists": True, "ok": True, "sourceStatus": "current", "usable": True, "parityRunId": "atrium-run-1"},
            "windowsLocal": {"exists": False, "status": "missing"},
        }
        def fake_backend_json(path: str, *, timeout: float = 5.0):
            if path == "/api/host-bridge/parity":
                return True, "{}", {"ok": False, "status": "cross_os_unverified"}
            if path == "/api/tools/catalog":
                return True, "{}", [{"name": "browser.open"}]
            if path == "/api/provider-auth/reference":
                return True, "{}", {"credentials": [{"id": "claude_code", "configured": True}], "providers": [], "subsystems": []}
            if path == "/api/provider-auth/env":
                return True, "{}", {"envPath": "system/.env", "groups": [{"fields": [{"key": "ATRIUM_ANTHROPIC_AUTH_TOKEN", "configured": False}]}]}
            if path == "/api/tools/mcp-gateway?probe=true":
                return True, "{}", {
                    "ok": True,
                    "ready": False,
                    "gatewayHealth": {"ok": False},
                    "connector": {"id": "mcp", "status": "needs_config", "readReady": True, "writeReady": False, "localFallback": True},
                    "requirements": ["ATRIUM_MCP_GATEWAY_URL"],
                }
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
            mock.patch.object(atrium_cli, "collect_windows_runtime_payload", return_value={"windowsNative": False}),
            mock.patch.object(atrium_cli, "collect_windows_entrypoints_payload", return_value={"ok": True, "checks": {"atriumCmdForwarder": True}}),
        ):
            payload = atrium_cli.collect_status_payload()

        self.assertEqual(payload["automationPermission"]["localArtifacts"]["windowsLocal"]["status"], "missing")
        self.assertEqual(payload["localProofArtifacts"]["windowsLocal"]["status"], "missing")
        self.assertEqual(payload["automationPermission"]["localArtifacts"]["macos"]["sourceStatus"], "current")
        self.assertEqual(payload["hostBridgeSource"]["sourceFingerprint"], "b" * 64)
        self.assertEqual(payload["toolCatalog"][0]["name"], "browser.open")
        self.assertEqual(payload["providerReference"]["credentials"][0]["id"], "claude_code")
        self.assertEqual(payload["providerEnv"]["envPath"], "system/.env")
        self.assertFalse(payload["mcpGateway"]["ready"])
        self.assertFalse(payload["mcpProbe"]["ready"])
        self.assertEqual(payload["toolsStatus"]["toolCatalog"][0]["name"], "browser.open")
        self.assertEqual(payload["nativeNextChecks"]["host"], "macos")
        checklist_ids = {item["id"] for item in payload["nativeNextChecks"]["operatorChecklist"]}
        self.assertIn("provider_disconnect_chatgpt", checklist_ids)
        self.assertIn("provider_disconnect_claude_code", checklist_ids)
        self.assertIn("automation_smoke", checklist_ids)
        self.assertIn("report_bundle", checklist_ids)
        matrix_surfaces = {item["id"] for item in payload["nativeParityMatrix"]["surfaces"]}
        self.assertIn("lifecycle", matrix_surfaces)
        self.assertIn("logs_support_report", matrix_surfaces)
        browser_surface = next(item for item in payload["nativeParityMatrix"]["surfaces"] if item["id"] == "browser_desktop_tools")
        self.assertIn("ops/macos_host_bridge_probe.py --full", " ".join(browser_surface["macos"]))
        provider_surface = next(item for item in payload["nativeParityMatrix"]["surfaces"] if item["id"] == "provider_login")
        self.assertIn("./atrium provider disconnect chatgpt", provider_surface["macos"])
        ai_tools_surface = next(item for item in payload["nativeParityMatrix"]["surfaces"] if item["id"] == "ai_tools")
        self.assertIn("./atrium tools mcp-gateway --json", ai_tools_surface["macos"])
        self.assertIn("./atrium tools mcp-probe --json", ai_tools_surface["macos"])
        self.assertIn(".\\atrium.ps1 tools mcp-probe --json", ai_tools_surface["windows"])
        self.assertTrue(payload["nativeParityMatrix"]["windowsNativeHostOnly"])
        self.assertFalse(payload["windowsRuntime"]["windowsNative"])
        self.assertTrue(payload["windowsEntryPoints"]["checks"]["atriumCmdForwarder"])

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

    def test_collect_docker_payload_includes_daemon_error(self) -> None:
        with (
            mock.patch.object(atrium_cli, "command_path", return_value="docker.exe"),
            mock.patch.object(atrium_cli, "docker_compose_cmd", return_value=["docker.exe", "compose"]),
            mock.patch.object(atrium_cli, "run", return_value=atrium_cli.CommandResult(1, "", "DOCKER_TOKEN=secret\nfailed")),
        ):
            payload = atrium_cli.collect_docker_payload()

        self.assertEqual(payload["cli"], "docker.exe")
        self.assertEqual(payload["compose"], ["docker.exe", "compose"])
        self.assertFalse(payload["running"])
        self.assertIn("DOCKER_TOKEN=set", str(payload["error"]))
        self.assertNotIn("secret", str(payload["error"]))

    def test_report_lines_include_local_proof_artifacts(self) -> None:
        local_artifacts = {
            "currentSourceFingerprint": "b" * 64,
            "macos": {"exists": True, "ok": True, "sourceStatus": "current", "usable": True, "parityRunId": "atrium-run-1"},
            "handoff": {"exists": True, "ok": True, "sourceStatus": "current", "usable": True, "parityRunId": "atrium-run-1"},
            "windowsLocal": {"exists": False, "status": "missing"},
        }
        entrypoints = {
            "ok": True,
            "checks": {"openclawLifecycleProofPackage": True},
            "openclawLifecycleProof": {
                "ok": True,
                "commands": {
                    "nativeProviderStatusProbeJson": ".\\atrium.ps1 provider status --probe --json",
                    "nativeBrowserDesktopSmoke": ".\\atrium.ps1 automation smoke --browser-url http://127.0.0.1:5173 --browser-profile atrium --output C:\\Temp\\atrium_host_bridge_windows_smoke.json",
                    "windowsProbe": ".\\atrium.ps1 automation windows-probe --full --browser-url http://127.0.0.1:5173 --browser-profile atrium --output C:\\Temp\\atrium_host_bridge_windows_probe.json",
                    "acceptWindowsArtifact": "./atrium automation accept-windows /tmp/atrium_host_bridge_windows_live.json --handoff /tmp/atrium_windows_handoff.json --max-artifact-age-hours 24.0 --windows-source-path 'C:\\Temp\\atrium_host_bridge_windows_live.json'",
                    "report": ".\\atrium.ps1 automation report --macos /tmp/macos.json --windows /tmp/windows.json",
                },
                "operatorChecklist": [
                    {"id": "native_provider_ai_tools"},
                    {"id": "native_browser_desktop_smoke"},
                    {"id": "generate_report"},
                ],
            },
        }

        def fake_backend_json(path: str, *, timeout: float = 5.0):
            if path == "/api/host-bridge/parity":
                return True, "{}", {"ok": False, "commands": {"verify": "./atrium automation audit"}}
            if path == "/api/provider-auth/reference":
                return True, "{}", {
                    "credentials": [{"id": "chatgpt_account_oauth", "configured": True, "email": "owner@example.com"}],
                    "providers": [{"id": "chatgpt_account"}],
                    "subsystems": [{"id": "runtime"}],
                }
            if path == "/api/provider-auth/env":
                return True, "{}", {
                    "envPath": "system/.env",
                    "groups": [{"fields": [{"key": "ATRIUM_OPENAI_API_KEY", "configured": True}]}],
                    "restartRecommended": False,
                }
            if path == "/api/connectors":
                return True, "{}", [
                    {"id": "browser", "status": "available", "readReady": True, "writeReady": True},
                    {"id": "mcp", "status": "configured", "readReady": True, "writeReady": True, "localFallback": False},
                ]
            if path == "/api/tools/mcp-gateway?probe=true":
                return True, "{}", {
                    "ok": True,
                    "ready": True,
                    "gatewayHealth": {"ok": True},
                    "connector": {"id": "mcp", "status": "configured", "readReady": True, "writeReady": True, "localFallback": False},
                    "requirements": [],
                    "successCondition": "MCP probe reports ready=true",
                }
            if path in {
                "/health",
                "/api/runtime",
                "/api/provider-auth/status",
                "/api/permissions/mode",
                "/api/tools/catalog",
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
            mock.patch.object(atrium_cli, "collect_windows_entrypoints_payload", return_value=entrypoints),
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
        self.assertIn("macos: exists=true, ok=true, source=current, usable=true, run=atrium-run-1", lines)
        self.assertIn("windowsLocal: exists=false, status=missing", lines)
        self.assertIn("provider_reference:", lines)
        self.assertIn("credentials=1", lines)
        self.assertIn("provider_env:", lines)
        self.assertIn("configuredFields=1", lines)
        self.assertIn("mcp_external_tools:", lines)
        self.assertIn("MCP external-write: ready=true, status=configured, write=true, localFallback=false", lines)
        self.assertIn("mcp_external_probe:", lines)
        self.assertIn("windows.entrypoints.ok=true", lines)
        self.assertIn("windows.entrypoint.openclawLifecycleProofPackage=true", lines)
        self.assertIn("windows.entrypoints.openclawLifecycleProof.nativeProviderStatusProbeJson=.\\atrium.ps1 provider status --probe --json", lines)
        self.assertIn("windows.entrypoints.openclawLifecycleProof.nativeBrowserDesktopSmoke=.\\atrium.ps1 automation smoke --browser-url http://127.0.0.1:5173 --browser-profile atrium --output C:\\Temp\\atrium_host_bridge_windows_smoke.json", lines)
        self.assertIn("windows.entrypoints.openclawLifecycleProof.windowsProbe=.\\atrium.ps1 automation windows-probe --full --browser-url http://127.0.0.1:5173 --browser-profile atrium --output C:\\Temp\\atrium_host_bridge_windows_probe.json", lines)
        self.assertIn("windows.entrypoints.openclawLifecycleProof.acceptWindowsArtifact=./atrium automation accept-windows /tmp/atrium_host_bridge_windows_live.json --handoff /tmp/atrium_windows_handoff.json --max-artifact-age-hours 24.0 --windows-source-path 'C:\\Temp\\atrium_host_bridge_windows_live.json'", lines)
        self.assertIn("windows.entrypoints.openclawLifecycleProof.checklist=native_provider_ai_tools, native_browser_desktop_smoke, generate_report", lines)
        self.assertIn("native.parity.provider_login=macos:6 commands; windows:6 commands", lines)
        self.assertIn("native.parity.windowsNativeHostOnly=true", lines)

    def test_windows_report_lines_include_entrypoint_file_truth(self) -> None:
        def fake_backend_json(path: str, *, timeout: float = 5.0):
            if path == "/api/host-bridge/parity":
                return True, "{}", {"ok": False, "commands": {}}
            if path == "/api/provider-auth/reference":
                return True, "{}", {"credentials": [], "providers": [], "subsystems": []}
            if path == "/api/provider-auth/env":
                return True, "{}", {"envPath": "system/.env", "groups": [], "restartRecommended": False}
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

        entrypoints = {
            "ok": True,
            "checks": {"atriumCmdForwarder": True, "openclawLifecycleProofPackage": True},
            "files": {
                "atriumCmd": {
                    "exists": True,
                    "relativePath": "atrium.cmd",
                    "bytes": 420,
                    "sha256": "a" * 64,
                }
            },
            "openclawLifecycleProof": {
                "commands": {
                    "nativeProviderStatusProbeJson": ".\\atrium.ps1 provider status --probe --json",
                    "nativeBrowserDesktopSmoke": ".\\atrium.ps1 automation smoke --browser-url http://127.0.0.1:5173 --browser-profile atrium --output C:\\Temp\\atrium_host_bridge_windows_smoke.json",
                    "windowsProbe": ".\\atrium.ps1 automation windows-probe --full --browser-url http://127.0.0.1:5173 --browser-profile atrium --output C:\\Temp\\atrium_host_bridge_windows_probe.json",
                    "acceptWindowsArtifact": "./atrium automation accept-windows /tmp/atrium_host_bridge_windows_live.json --handoff /tmp/atrium_windows_handoff.json --max-artifact-age-hours 24.0 --windows-source-path 'C:\\Temp\\atrium_host_bridge_windows_live.json'",
                },
                "operatorChecklist": [
                    {"id": "native_provider_ai_tools"},
                    {"id": "native_browser_desktop_smoke"},
                    {"id": "generate_report"},
                ],
            },
        }
        with (
            mock.patch.object(atrium_cli, "git_status_summary", return_value="clean"),
            mock.patch.object(atrium_cli, "remote_ok", return_value=(True, "origin/main")),
            mock.patch.object(atrium_cli, "is_i_cloud_risky", return_value=(False, "ok")),
            mock.patch.object(atrium_cli, "memory_gb", return_value="16GB"),
            mock.patch.object(atrium_cli, "windows_native", return_value=True),
            mock.patch.object(atrium_cli, "windows_process_status", return_value="backend pid=123 running"),
            mock.patch.object(atrium_cli, "collect_windows_runtime_payload", return_value={"sessionName": "Console", "powershell": {"command": "powershell.exe", "version": "5.1"}}),
            mock.patch.object(atrium_cli, "collect_windows_entrypoints_payload", return_value=entrypoints),
            mock.patch.object(atrium_cli, "command_path", return_value=None),
            mock.patch.object(atrium_cli, "docker_report_lines", return_value=[]),
            mock.patch.object(atrium_cli, "port_open", return_value=False),
            mock.patch.object(atrium_cli, "backend_json", side_effect=fake_backend_json),
            mock.patch.object(atrium_cli, "render_env_status", return_value=[]),
            mock.patch.object(atrium_cli, "provider_status_from_env", return_value=[]),
            mock.patch.object(atrium_cli, "current_source_summary", return_value={"sourceFingerprint": "b" * 64}),
            mock.patch.object(atrium_cli, "collect_local_proof_artifacts", return_value={}),
        ):
            lines = atrium_cli.report_lines()

        self.assertIn("windows.entrypoints.ok=true", lines)
        self.assertIn("windows.entrypoint.atriumCmdForwarder=true", lines)
        self.assertIn("windows.entrypoint.openclawLifecycleProofPackage=true", lines)
        self.assertIn("windows.entrypoint.file.atriumCmd=exists=true;path=atrium.cmd;bytes=420;sha256=aaaaaaaaaaaa", lines)
        self.assertIn("windows.entrypoints.openclawLifecycleProof.nativeProviderStatusProbeJson=.\\atrium.ps1 provider status --probe --json", lines)
        self.assertIn("windows.entrypoints.openclawLifecycleProof.nativeBrowserDesktopSmoke=.\\atrium.ps1 automation smoke --browser-url http://127.0.0.1:5173 --browser-profile atrium --output C:\\Temp\\atrium_host_bridge_windows_smoke.json", lines)
        self.assertIn("windows.entrypoints.openclawLifecycleProof.windowsProbe=.\\atrium.ps1 automation windows-probe --full --browser-url http://127.0.0.1:5173 --browser-profile atrium --output C:\\Temp\\atrium_host_bridge_windows_probe.json", lines)
        self.assertIn("windows.entrypoints.openclawLifecycleProof.acceptWindowsArtifact=./atrium automation accept-windows /tmp/atrium_host_bridge_windows_live.json --handoff /tmp/atrium_windows_handoff.json --max-artifact-age-hours 24.0 --windows-source-path 'C:\\Temp\\atrium_host_bridge_windows_live.json'", lines)
        self.assertIn("windows.entrypoints.openclawLifecycleProof.checklist=native_provider_ai_tools, native_browser_desktop_smoke, generate_report", lines)
        self.assertIn("native.parity.ai_tools=macos:4 commands; windows:4 commands", lines)
        self.assertIn("native.parity.browser_desktop_tools=macos:3 commands; windows:3 commands", lines)

    def test_local_proof_artifact_summary_marks_stale_missing_windows_refresh_required(self) -> None:
        lines = atrium_cli.summarize_local_proof_artifacts({
            "currentSourceFingerprint": "b" * 64,
            "macos": {
                "exists": True,
                "ok": True,
                "sourceStatus": "stale",
                "usable": False,
                "refreshRequired": True,
                "failedChecks": ["interactiveCalculator"],
                "parityRunId": "atrium-run-1",
            },
            "handoff": {
                "exists": True,
                "ok": True,
                "sourceStatus": "stale",
                "usable": False,
                "refreshRequired": True,
                "parityRunId": "atrium-run-1",
            },
            "windowsLocal": {
                "exists": False,
                "status": "missing",
                "sourceStatus": "stale",
                "refreshRequired": True,
                "copySourcePath": "C:\\Temp\\atrium_host_bridge_windows_live.json",
                "expectedParityRunId": "atrium-run-1",
            },
        })

        self.assertIn("macos: exists=true, ok=true, source=stale, usable=false, refreshRequired=true, failed=interactiveCalculator, run=atrium-run-1", lines)
        self.assertIn("handoff: exists=true, ok=true, source=stale, usable=false, refreshRequired=true, run=atrium-run-1", lines)
        self.assertIn(
            "windowsLocal: exists=false, status=missing, source=stale, refreshRequired=true, copyFrom=C:\\Temp\\atrium_host_bridge_windows_live.json, run=atrium-run-1",
            lines,
        )

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
                        "diagnostics/doctor.json": {"tools": {"powershell": "powershell.exe"}},
                        "diagnostics/status.json": {"runtime": {"provider": {"apiKey": "sk-secret"}}},
                        "diagnostics/process.json": {
                            "mode": "windows-native",
                            "details": {
                                "backend": {
                                    "pid": 1234,
                                    "running": True,
                                    "processIdentity": {
                                        "CommandLine": "python app.py ATRIUM_OPENAI_API_KEY=sk-secret"
                                    },
                                }
                            },
                        },
                        "diagnostics/windows-runtime.json": {
                            "windowsNative": True,
                            "powershell": {"command": "powershell.exe", "version": "5.1"},
                        },
                        "diagnostics/windows-entrypoints.json": {
                            "ok": True,
                            "checks": {"standardPowerShellPathFallback": True},
                        },
                        "diagnostics/native-next-checks.json": {
                            "host": "windows",
                            "commands": {"nativeBrowserDesktopSmoke": ".\\atrium.ps1 automation smoke token=sk-secret"},
                            "operatorChecklist": [{"id": "native_browser_desktop_smoke", "command": ".\\atrium.ps1 automation smoke"}],
                        },
                        "diagnostics/native-parity-matrix.json": {
                            "nativeOnly": True,
                            "windowsNativeHostOnly": True,
                            "surfaces": [{"id": "provider_login", "windows": [".\\atrium.ps1 provider login chatgpt --token sk-secret"]}],
                        },
                        "diagnostics/docker.json": {"daemon": {"error": "token=sk-secret"}},
                        "diagnostics/host-bridge-source.json": {"sourceFingerprint": "abc", "apiKey": "sk-secret"},
                        "diagnostics/local-proof-artifacts.json": {"macos": {"error": "apiKey=sk-secret"}},
                        "diagnostics/windows-proof-handoff.json": {
                            "ok": True,
                            "windowsProof": {
                                "requiredProofFacets": ["windowsLiveProofRunner"],
                                "commands": {"liveProof": ".\\atrium.ps1 automation windows-live-proof --token sk-secret"},
                            },
                        },
                        "diagnostics/logs.json": {"logs": {"backend": {"lines": ["ATRIUM_OPENAI_API_KEY=sk-secret"]}}},
                        "diagnostics/runtime.json": {"provider": {"apiKey": "sk-secret"}},
                        "diagnostics/connectors.json": [{"id": "browser", "secret": "sk-secret"}],
                        "diagnostics/tools-catalog.json": [{"name": "browser.open", "apiKey": "sk-secret"}],
                        "diagnostics/tools-mcp-gateway.json": {
                            "ready": False,
                            "setupCommand": ".\\atrium.ps1 tools mcp-gateway --token sk-secret",
                        },
                        "diagnostics/tools-mcp-probe.json": {
                            "ready": False,
                            "gatewayHealth": {"error": "token=sk-secret"},
                        },
                        "diagnostics/host-bridge-parity.json": {"commands": {"token": "sk-secret"}},
                        "diagnostics/permission-mode.json": {"fullAutonomyStatus": {"credentials": {"apiKey": "sk-secret"}}},
                        "diagnostics/provider-status.json": {"chatgptAccount": {"email": "owner@example.com"}},
                        "diagnostics/provider-reference.json": {"credentials": [{"email": "owner@example.com", "accessToken": "sk-secret"}]},
                        "diagnostics/provider-env.json": {"groups": [{"fields": [{"key": "ATRIUM_OPENAI_API_KEY", "apiKey": "sk-secret"}]}]},
                    },
                ),
                redirect_stdout(io.StringIO()),
            ):
                self.assertEqual(atrium_cli.command_report(args), 0)

            with zipfile.ZipFile(bundle) as archive:
                names = set(archive.namelist())
                report = archive.read("support-report.txt").decode("utf-8")
                backend_log = archive.read("logs/backend.log").decode("utf-8")
                doctor_json = archive.read("diagnostics/doctor.json").decode("utf-8")
                status_json = archive.read("diagnostics/status.json").decode("utf-8")
                process_json = archive.read("diagnostics/process.json").decode("utf-8")
                windows_runtime_json = archive.read("diagnostics/windows-runtime.json").decode("utf-8")
                windows_entrypoints_json = archive.read("diagnostics/windows-entrypoints.json").decode("utf-8")
                native_next_checks_json = archive.read("diagnostics/native-next-checks.json").decode("utf-8")
                native_parity_matrix_json = archive.read("diagnostics/native-parity-matrix.json").decode("utf-8")
                docker_json = archive.read("diagnostics/docker.json").decode("utf-8")
                host_bridge_source_json = archive.read("diagnostics/host-bridge-source.json").decode("utf-8")
                local_proof_artifacts_json = archive.read("diagnostics/local-proof-artifacts.json").decode("utf-8")
                windows_proof_handoff_json = archive.read("diagnostics/windows-proof-handoff.json").decode("utf-8")
                logs_json = archive.read("diagnostics/logs.json").decode("utf-8")
                runtime_json = archive.read("diagnostics/runtime.json").decode("utf-8")
                connectors_json = archive.read("diagnostics/connectors.json").decode("utf-8")
                tools_catalog_json = archive.read("diagnostics/tools-catalog.json").decode("utf-8")
                tools_mcp_gateway_json = archive.read("diagnostics/tools-mcp-gateway.json").decode("utf-8")
                tools_mcp_probe_json = archive.read("diagnostics/tools-mcp-probe.json").decode("utf-8")
                host_bridge_parity_json = archive.read("diagnostics/host-bridge-parity.json").decode("utf-8")
                permission_json = archive.read("diagnostics/permission-mode.json").decode("utf-8")
                provider_json = archive.read("diagnostics/provider-status.json").decode("utf-8")
                provider_reference_json = archive.read("diagnostics/provider-reference.json").decode("utf-8")
                provider_env_json = archive.read("diagnostics/provider-env.json").decode("utf-8")
                manifest = json.loads(archive.read("manifest.json").decode("utf-8"))

        self.assertIn("support-report.txt", names)
        self.assertIn("logs/backend.log", names)
        self.assertIn("diagnostics/doctor.json", names)
        self.assertIn("diagnostics/status.json", names)
        self.assertIn("diagnostics/process.json", names)
        self.assertIn("diagnostics/windows-runtime.json", names)
        self.assertIn("diagnostics/windows-entrypoints.json", names)
        self.assertIn("diagnostics/native-next-checks.json", names)
        self.assertIn("diagnostics/native-parity-matrix.json", names)
        self.assertIn("diagnostics/docker.json", names)
        self.assertIn("diagnostics/host-bridge-source.json", names)
        self.assertIn("diagnostics/local-proof-artifacts.json", names)
        self.assertIn("diagnostics/windows-proof-handoff.json", names)
        self.assertIn("diagnostics/logs.json", names)
        self.assertIn("diagnostics/runtime.json", names)
        self.assertIn("diagnostics/connectors.json", names)
        self.assertIn("diagnostics/tools-catalog.json", names)
        self.assertIn("diagnostics/tools-mcp-gateway.json", names)
        self.assertIn("diagnostics/tools-mcp-probe.json", names)
        self.assertIn("diagnostics/host-bridge-parity.json", names)
        self.assertIn("diagnostics/permission-mode.json", names)
        self.assertIn("diagnostics/provider-status.json", names)
        self.assertIn("diagnostics/provider-reference.json", names)
        self.assertIn("diagnostics/provider-env.json", names)
        self.assertIn("manifest.json", names)
        self.assertIn("ATRIUM_OPENAI_API_KEY=set", report)
        self.assertNotIn("sk-secret", report)
        self.assertNotIn("sk-secret", backend_log)
        self.assertNotIn("sk-secret", doctor_json)
        self.assertNotIn("sk-secret", status_json)
        self.assertNotIn("sk-secret", windows_runtime_json)
        self.assertNotIn("sk-secret", windows_entrypoints_json)
        self.assertNotIn("sk-secret", native_next_checks_json)
        self.assertIn("native_browser_desktop_smoke", native_next_checks_json)
        self.assertNotIn("sk-secret", native_parity_matrix_json)
        self.assertIn("provider_login", native_parity_matrix_json)
        self.assertNotIn("sk-secret", docker_json)
        self.assertNotIn("sk-secret", host_bridge_source_json)
        self.assertNotIn("sk-secret", local_proof_artifacts_json)
        self.assertNotIn("sk-secret", windows_proof_handoff_json)
        self.assertIn("--token <redacted>", windows_proof_handoff_json)
        self.assertIn("windowsLiveProofRunner", windows_proof_handoff_json)
        self.assertIn('"mode": "windows-native"', process_json)
        self.assertIn("ATRIUM_OPENAI_API_KEY=set", process_json)
        self.assertNotIn("sk-secret", process_json)
        self.assertNotIn("sk-secret", logs_json)
        self.assertNotIn("sk-secret", runtime_json)
        self.assertNotIn("sk-secret", connectors_json)
        self.assertNotIn("sk-secret", tools_catalog_json)
        self.assertNotIn("sk-secret", tools_mcp_gateway_json)
        self.assertNotIn("sk-secret", tools_mcp_probe_json)
        self.assertIn("--token <redacted>", tools_mcp_gateway_json)
        self.assertNotIn("sk-secret", host_bridge_parity_json)
        self.assertNotIn("sk-secret", permission_json)
        self.assertNotIn("owner@example.com", provider_json)
        self.assertNotIn("owner@example.com", provider_reference_json)
        self.assertNotIn("sk-secret", provider_reference_json)
        self.assertNotIn("sk-secret", provider_env_json)
        self.assertTrue(manifest["redacted"])
        self.assertIn("manifest.json", manifest["included"])

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
                        "windowsVisualPreflightChecks": {
                            "dpiAwareness": False,
                            "sendInputHelper": False,
                            "virtualScreen": True,
                            "winForms": True,
                        },
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
        self.assertIn("Windows automation checks: passed=2/4", text)
        self.assertIn("failed=dpiAwareness, sendInputHelper", text)

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
                {
                    "id": "mcp",
                    "status": "available",
                    "readReady": True,
                    "writeReady": False,
                    "runtimeStatus": "local fallback ready",
                    "localFallback": True,
                    "externalWriteRequires": [
                        "ATRIUM_MCP_GATEWAY_URL configured for write-capable external MCP servers",
                        "ATRIUM_MCP_GATEWAY_TOKEN or Keychain token configured for the MCP gateway",
                    ],
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

        self.assertEqual(len(connector_lines), 3)
        self.assertIn("connector.browser", connector_lines[0])
        self.assertIn("proof=cross_os_unverified", connector_lines[0])
        self.assertIn("connector.mcp", "\n".join(connector_lines))
        self.assertIn("connector.mcp.externalWrite", "\n".join(connector_lines))
        self.assertIn("ready=false", "\n".join(connector_lines))
        self.assertIn("localFallbackOnly=true", "\n".join(connector_lines))
        self.assertIn("ATRIUM_MCP_GATEWAY_URL", "\n".join(connector_lines))
        self.assertIn("/api/connectors", "\n".join(connector_lines))
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
                            "currentDetails": {
                                "gatewayConfigured": False,
                                "gatewayHealthy": False,
                                "externalWriteReady": False,
                                "localFallbackOnly": True,
                                "requiredEnvironment": [
                                    "ATRIUM_MCP_GATEWAY_URL",
                                    "ATRIUM_MCP_GATEWAY_TOKEN or ATRIUM_MCP_GATEWAY_TOKEN_KEYCHAIN_SERVICE",
                                    "ATRIUM_MCP_ENABLED_SERVERS",
                                ],
                                "statusCommand": "curl -fsS http://127.0.0.1:8787/api/connectors | jq '.[] | select(.kind==\"mcp\")'",
                            },
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
        self.assertIn("gateway not configured", text)
        self.assertIn("ATRIUM_MCP_GATEWAY_URL", text)
        self.assertIn("ATRIUM_MCP_ENABLED_SERVERS", text)
        self.assertIn("/api/connectors", text)
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
            "windowsProofOperatorChecklist": [
                {"id": "source_validate", "command": ".\\atrium.ps1 automation source --expect-source-fingerprint " + "a" * 64},
                {"id": "native_provider_ai_tools", "command": ".\\atrium.ps1 provider status --probe --json; .\\atrium.ps1 tools status --json"},
                {"id": "mcp_gateway_status", "command": ".\\atrium.ps1 tools mcp-probe --json"},
                {"id": "native_browser_desktop_smoke", "command": ".\\atrium.ps1 automation smoke --browser-url http://127.0.0.1:5173 --browser-profile atrium --output C:\\Temp\\atrium_host_bridge_windows_smoke.json"},
                {"id": "windows_live_proof", "command": "powershell -File .\\ops\\windows_host_bridge_live_proof.ps1 ..."},
                {"id": "artifact_validate_on_windows", "command": "uv --project system run python ops/host_bridge_artifact_summary.py --label windows C:\\Temp\\probe.json"},
                {"id": "copy_to_repo_host", "command": "Copy C:\\Temp\\probe.json to /tmp/probe.json"},
                {"id": "generate_report", "command": "./atrium automation report --macos /tmp/macos.json --windows /tmp/windows.json"},
                {"id": "audit_gate", "command": "./atrium automation audit"},
            ],
        }
        output = io.StringIO()
        with (
            mock.patch.object(atrium_cli, "ensure_repo_root"),
            mock.patch.object(atrium_cli, "backend_json", return_value=(True, "{}", payload)) as backend_json,
            mock.patch.object(atrium_cli, "current_source_summary", return_value={"sourceFingerprint": "b" * 64}),
            mock.patch.object(
                atrium_cli,
                "collect_local_proof_artifacts",
                return_value={
                    "currentSourceFingerprint": "b" * 64,
                    "macos": {"exists": True, "ok": True, "sourceStatus": "stale", "usable": False, "refreshRequired": True, "parityRunId": "atrium-old"},
                    "handoff": {"exists": True, "ok": True, "sourceStatus": "stale", "usable": False, "refreshRequired": True, "parityRunId": "atrium-old"},
                    "windowsLocal": {"exists": False, "status": "missing", "sourceStatus": "stale", "refreshRequired": True},
                },
            ),
            redirect_stdout(output),
        ):
            self.assertEqual(atrium_cli.command_automation(args), 2)

        backend_json.assert_called_once_with(
            "/api/host-bridge/parity",
            timeout=atrium_cli.HOST_BRIDGE_PARITY_TIMEOUT_SECONDS,
        )
        text = output.getvalue()
        self.assertIn("Local Proof Artifacts", text)
        self.assertIn("macos: exists=true, ok=true, source=stale, usable=false, refreshRequired=true, run=atrium-old", text)
        self.assertIn("windowsLocal: exists=false, status=missing, source=stale, refreshRequired=true", text)
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
        self.assertIn("--max-artifact-age-hours 24.0", normalized["gaps"][0])
        self.assertIn("automation report", normalized["report"]["findings"][0])
        self.assertIn("--max-artifact-age-hours 24.0", normalized["report"]["findings"][0])
        self.assertIn("automation report", normalized["connectors"][0]["proofGaps"][0])
        self.assertIn("--max-artifact-age-hours 24.0", normalized["connectors"][0]["proofGaps"][0])
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
                "windowsSourceValidate": ".\\atrium.ps1 automation source --expect-source-fingerprint " + "a" * 64,
                "mcpGatewaySetupJson": ".\\atrium.ps1 tools mcp-gateway --json",
                "mcpGatewayProbeJson": ".\\atrium.ps1 tools mcp-probe --json",
                "mcpGatewayStatusJson": ".\\atrium.ps1 tools status --json",
                "nativeBrowserDesktopSmoke": ".\\atrium.ps1 automation smoke --browser-url http://127.0.0.1:5173 --browser-profile atrium --output C:\\Temp\\atrium_host_bridge_windows_smoke.json",
                "windowsProbe": ".\\atrium.ps1 automation windows-probe --full --output C:\\Temp\\atrium_host_bridge_windows_probe.json",
                "macosSmoke": "./atrium automation smoke --browser-url http://127.0.0.1:5173 --browser-profile atrium --output /tmp/atrium_host_bridge_macos_smoke.json",
                "macosArtifact": "/tmp/atrium_host_bridge_macos_live.json",
                "macosArtifactValidate": "uv --project system run python ops/host_bridge_artifact_summary.py --label macos ...",
                "windowsHandoff": "./atrium automation handoff --macos /tmp/macos.json --output /tmp/handoff.json",
                "windowsLiveProofRunner": "powershell -File .\\ops\\windows_host_bridge_live_proof.ps1 ...",
                "windowsArtifactValidateOnWindows": "uv --project system run python ops/host_bridge_artifact_summary.py --label windows C:\\Temp\\probe.json",
                "windowsArtifactSource": "C:\\Temp\\atrium_host_bridge_windows_live.json",
                "windowsArtifactLocal": "/tmp/atrium_host_bridge_windows_live.json",
                "windowsArtifactCopyHint": "Copy C:\\Temp\\probe.json to /tmp/probe.json",
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
                    "macos": {"exists": True, "ok": True, "sourceStatus": "current", "usable": True, "parityRunId": "atrium-run-1"},
                    "handoff": {"exists": True, "ok": True, "sourceStatus": "current", "usable": True, "parityRunId": "atrium-run-1"},
                    "windowsLocal": {"exists": False, "status": "missing"},
                },
            ),
            redirect_stdout(output),
        ):
            self.assertEqual(atrium_cli.command_automation(args), 0)

        paths = [call.args[0] for call in backend_json.call_args_list]
        self.assertEqual(paths, ["/api/host-bridge/parity", "/api/permissions/mode"])
        self.assertEqual(
            backend_json.call_args_list[0].kwargs["timeout"],
            atrium_cli.HOST_BRIDGE_PARITY_TIMEOUT_SECONDS,
        )
        text = output.getvalue()
        self.assertIn("HostBridge parity: ok=false", text)
        self.assertIn("Local Proof Artifacts", text)
        self.assertIn("macos: exists=true, ok=true, source=current, usable=true, run=atrium-run-1", text)
        self.assertIn("windowsLocal: exists=false, status=missing", text)
        self.assertIn("Owner Permissions", text)
        self.assertIn("windowsSourceValidate", text)
        self.assertIn("windowsLiveProofRunner", text)
        self.assertIn("windowsHandoff", text)
        self.assertIn("macosSmoke", text)
        self.assertIn("macosArtifact", text)
        self.assertIn("macosArtifactValidate", text)
        self.assertIn("windowsArtifactValidateOnWindows", text)
        self.assertIn("windowsArtifactSource", text)
        self.assertIn("windowsArtifactLocal", text)
        self.assertIn("windowsArtifactValidateLocal", text)
        self.assertIn("automationReport", text)
        self.assertIn("windowsProofChecklist[1].source_validate", text)
        self.assertIn("windowsProofChecklist[2].mcp_gateway_setup", text)
        self.assertIn("windowsProofChecklist[3].mcp_gateway_probe", text)
        self.assertIn("windowsProofChecklist[4].mcp_gateway_status", text)
        self.assertIn("windowsProofChecklist[5].native_browser_desktop_smoke", text)
        self.assertIn("windowsProofChecklist[6].windows_raw_probe", text)
        self.assertIn("windowsProofChecklist[7].windows_live_proof", text)
        self.assertIn("windowsProofChecklist[8].artifact_validate_on_windows", text)
        self.assertIn("run Windows full probe", text)

    def test_automation_status_json_prints_normalized_redacted_payload(self) -> None:
        args = type("Args", (), {"automation_action": "status", "commands": True, "json": True})()
        payload = {
            "ok": False,
            "status": "cross_os_unverified",
            "summary": "missing Windows proof",
            "providerAuth": {"email": "owner@example.com", "accessToken": "secret-token"},
            "commands": {
                "windowsLiveProofRunner": "powershell -File .\\ops\\windows_host_bridge_live_proof.ps1",
                "verify": "./atrium automation audit",
            },
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
        self.assertEqual(
            [item["id"] for item in redacted["windowsProofOperatorChecklist"]],
            ["windows_live_proof", "copy_to_repo_host", "audit_gate"],
        )
        self.assertEqual(redacted["windowsProofOperatorChecklist"][1]["from"], "C:\\Temp\\atrium_host_bridge_windows_live.json")
        self.assertEqual(redacted["windowsProofOperatorChecklist"][1]["to"], "/tmp/atrium_host_bridge_windows_live.json")
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
                        "windowsProof": {
                            "outputPath": "C:\\Temp\\atrium_host_bridge_windows_live.json",
                            "copyInstruction": "Copy C:\\Temp\\atrium_host_bridge_windows_live.json from Windows",
                            "proofFacetCount": 2,
                            "requiredProofFacets": ["mcpExternalWriteReady", "windowsLiveProofRunner"],
                            "commands": {
                                "liveProof": ".\\atrium.ps1 automation windows-live-proof ...",
                                "artifactValidate": ".\\atrium.ps1 automation artifact --label windows --json C:\\Temp\\atrium_host_bridge_windows_live.json",
                                "mcpGatewaySetupJson": ".\\atrium.ps1 tools mcp-gateway --json",
                                "mcpGatewayProbeJson": ".\\atrium.ps1 tools mcp-probe --json",
                            },
                            "operatorChecklist": [
                                {"step": 1, "id": "native_setup_start", "command": ".\\atrium.ps1 setup --yes; .\\atrium.ps1 start; .\\atrium.ps1 status --json"},
                                {"step": 2, "id": "native_permissions", "command": ".\\atrium.ps1 permissions status --json; .\\atrium.ps1 permissions set full_auto --agent-full-access true; .\\atrium.ps1 permissions status --json"},
                                {"step": 3, "id": "native_provider_ai_tools", "command": ".\\atrium.ps1 provider status --probe --json; .\\atrium.ps1 provider reference --json; .\\atrium.ps1 provider env --json; .\\atrium.ps1 tools status --json; .\\atrium.ps1 tools catalog --json", "loginCommands": [".\\atrium.ps1 provider login chatgpt", ".\\atrium.ps1 provider login claude-code"]},
                                {"step": 4, "id": "native_logs_report", "command": ".\\atrium.ps1 logs --json; .\\atrium.ps1 report --bundle"},
                                {"step": 5, "id": "native_stop_restart", "command": ".\\atrium.ps1 stop; .\\atrium.ps1 restart --force; .\\atrium.ps1 status --json"},
                                {"step": 6, "id": "mcp_gateway_setup", "command": ".\\atrium.ps1 tools mcp-gateway --json"},
                                {"step": 7, "id": "mcp_gateway_probe", "command": ".\\atrium.ps1 tools mcp-probe --json"},
                                {"step": 8, "id": "mcp_gateway_status", "command": ".\\atrium.ps1 tools status --json"},
                                {"step": 9, "id": "native_browser_desktop_smoke", "command": ".\\atrium.ps1 automation smoke --browser-url http://127.0.0.1:5173 --browser-profile atrium --output C:\\Temp\\atrium_host_bridge_windows_smoke.json"},
                                {"step": 10, "id": "source_validate", "command": ".\\atrium.ps1 automation source --json"},
                                {"step": 11, "id": "windows_live_proof", "command": ".\\atrium.ps1 automation windows-live-proof ..."},
                                {"step": 12, "id": "artifact_validate_on_windows", "command": ".\\atrium.ps1 automation artifact --label windows"},
                                {"step": 13, "id": "copy_to_repo_host", "from": "C:\\Temp\\atrium_host_bridge_windows_live.json", "to": "/tmp/atrium_host_bridge_windows_live.json"},
                                {"step": 14, "id": "accept_windows_artifact", "command": "./atrium automation accept-windows /tmp/atrium_host_bridge_windows_live.json --handoff /tmp/atrium_windows_handoff.json"},
                                {"step": 15, "id": "generate_report", "command": "./atrium automation report"},
                                {"step": 16, "id": "audit_gate", "command": "./atrium automation audit"},
                            ],
                        },
                    },
                    None,
                )
            return None, "missing"

        with mock.patch.object(atrium_cli, "_load_json_file", side_effect=fake_load_json_file):
            artifacts = atrium_cli.collect_local_proof_artifacts(current)

        self.assertEqual(artifacts["currentSourceFingerprint"], "b" * 64)
        self.assertEqual(artifacts["macos"]["sourceStatus"], "stale")
        self.assertFalse(artifacts["macos"]["usable"])
        self.assertTrue(artifacts["macos"]["refreshRequired"])
        self.assertEqual(artifacts["handoff"]["sourceStatus"], "stale")
        self.assertEqual(artifacts["handoff"]["contractStatus"], "stale")
        self.assertIn("requiredProofFacets mismatch", artifacts["handoff"]["contractFindings"][0])
        self.assertFalse(artifacts["handoff"]["usable"])
        self.assertTrue(artifacts["handoff"]["refreshRequired"])
        self.assertEqual(artifacts["handoff"]["proofFacetCount"], 2)
        self.assertEqual(artifacts["handoff"]["requiredProofFacets"], ["mcpExternalWriteReady", "windowsLiveProofRunner"])
        self.assertFalse(artifacts["windowsLocal"]["exists"])
        self.assertEqual(artifacts["windowsLocal"]["status"], "missing")
        self.assertEqual(artifacts["windowsLocal"]["sourceStatus"], "stale")
        self.assertEqual(artifacts["windowsLocal"]["expectedContractStatus"], "stale")
        self.assertEqual(artifacts["windowsLocal"]["contractStatus"], "stale")
        self.assertFalse(artifacts["windowsLocal"]["usable"])
        self.assertTrue(artifacts["windowsLocal"]["refreshRequired"])
        self.assertEqual(artifacts["windowsLocal"]["expectedProofFacetCount"], 2)
        self.assertEqual(artifacts["windowsLocal"]["requiredProofFacets"], ["mcpExternalWriteReady", "windowsLiveProofRunner"])
        self.assertEqual(artifacts["windowsLocal"]["copySourcePath"], "C:\\Temp\\atrium_host_bridge_windows_live.json")
        self.assertEqual(artifacts["windowsLocal"]["expectedParityRunId"], "atrium-old")
        self.assertEqual(
            [item["id"] for item in artifacts["windowsLocal"]["operatorChecklist"]],
            [
                "native_setup_start",
                "native_permissions",
                "native_provider_ai_tools",
                "native_logs_report",
                "native_stop_restart",
                "mcp_gateway_setup",
                "mcp_gateway_probe",
                "mcp_gateway_status",
                "native_browser_desktop_smoke",
                "source_validate",
                "windows_live_proof",
                "artifact_validate_on_windows",
                "copy_to_repo_host",
                "accept_windows_artifact",
                "generate_report",
                "audit_gate",
            ],
        )
        self.assertIn("automation artifact", artifacts["windowsLocal"]["validateOnWindowsCommand"])
        self.assertIn("windows-live-proof", artifacts["windowsLocal"]["liveProofCommand"])
        lines = atrium_cli.summarize_local_proof_artifacts(artifacts)
        self.assertIn("macos: exists=true, ok=true, source=stale, usable=false, refreshRequired=true, run=atrium-old", lines)
        self.assertTrue(any(line.startswith("handoff: exists=true, ok=true, source=stale, usable=false, contract=stale, refreshRequired=true") for line in lines))
        self.assertIn("windowsLocal: exists=false, status=missing, source=stale, contract=stale, refreshRequired=true, copyFrom=C:\\Temp\\atrium_host_bridge_windows_live.json, run=atrium-old, proofFacets=2", lines)

    def test_local_proof_artifacts_preserve_windows_live_failure_artifact_details(self) -> None:
        current = {"sourceFingerprint": "c" * 64}
        failure_payload = {
            "ok": False,
            "mode": "windows_live_proof_failed",
            "parityRunId": "atrium-run-failed",
            "sourceFingerprint": "c" * 64,
            "generatedAt": 1781020000000,
            "error": "Windows HostBridge live proof requires an interactive desktop session, not Services.",
            "failedStage": "interactive_session",
            "nextSteps": {
                "failedStage": "interactive_session",
                "commands": [".\\atrium.ps1 status --json"],
            },
            "partialArtifact": {"preserved": False},
            "preflight": {
                "os": {
                    "isWindows": True,
                    "sessionName": "Services",
                    "isElevated": False,
                },
            },
        }

        def fake_load_json_file(path: Path):
            if str(path) == "/tmp/atrium_host_bridge_windows_live.json":
                return failure_payload, None
            return None, "missing"

        with mock.patch.object(atrium_cli, "_load_json_file", side_effect=fake_load_json_file):
            artifacts = atrium_cli.collect_local_proof_artifacts(current)

        self.assertTrue(artifacts["windowsLocal"]["exists"])
        self.assertFalse(artifacts["windowsLocal"]["ok"])
        self.assertFalse(artifacts["windowsLocal"]["usable"])
        self.assertEqual(artifacts["windowsLocal"]["mode"], "windows_live_proof_failed")
        self.assertEqual(artifacts["windowsLocal"]["sourceStatus"], "current")
        self.assertEqual(artifacts["windowsLocal"]["failedChecks"], ["windows_live_proof_failed"])
        self.assertEqual(artifacts["windowsLocal"]["failureStage"], "interactive_session")
        self.assertEqual(artifacts["windowsLocal"]["failureNextSteps"]["failedStage"], "interactive_session")
        self.assertFalse(artifacts["windowsLocal"]["failurePartialArtifact"]["preserved"])
        self.assertEqual(artifacts["windowsLocal"]["preflight"]["sessionName"], "Services")
        lines = atrium_cli.summarize_local_proof_artifacts(artifacts)
        text = "\n".join(lines)
        self.assertIn("windowsLocal: exists=true, ok=false, source=current, usable=false", text)
        self.assertIn("failed=windows_live_proof_failed", text)
        self.assertIn("failureStage=interactive_session", text)
        self.assertIn("session=Services", text)
        self.assertIn("isWindows=true", text)

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

    def test_post_start_readiness_prints_runtime_provider_tools_and_automation(self) -> None:
        def fake_backend_json(path: str, *, timeout: float = 5.0):
            payloads = {
                "/api/runtime": {"ok": True, "running": True, "provider": {"ready": True}, "v2": {"toolRegistryCount": 2}},
                "/api/provider-auth/status": {"chatgptAccount": {"ready": True}, "claudeCode": {"ready": False}},
                "/api/permissions/mode": {"mode": "full_auto", "fullAutonomyStatus": {"active": True}},
                "/api/tools/catalog": [{"name": "browser.open", "executor": "browser", "riskClass": "desktop"}],
                "/api/connectors": [{"id": "browser", "status": "available", "readReady": True, "writeReady": True}],
                "/api/host-bridge/parity": {"ok": False, "status": "cross_os_unverified", "commands": {}},
            }
            return True, "{}", payloads[path]

        output = io.StringIO()
        with (
            mock.patch.object(atrium_cli, "backend_json", side_effect=fake_backend_json),
            mock.patch.object(atrium_cli, "current_source_summary", return_value={"sourceFingerprint": "a" * 64}),
            redirect_stdout(output),
        ):
            atrium_cli.print_post_start_readiness()

        text = output.getvalue()
        self.assertIn("Post-start Readiness", text)
        self.assertIn("runtime", text)
        self.assertIn("provider auth", text)
        self.assertIn("AI tool catalog", text)
        self.assertIn("connectors", text)
        self.assertIn("automation permission", text)

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

    def test_tools_mcp_gateway_json_prints_redacted_external_write_setup(self) -> None:
        args = type("Args", (), {"tools_action": "mcp-gateway", "limit": 3, "json": True})()

        def fake_backend_json(path: str, *, timeout: float = 5.0):
            if path == "/api/runtime":
                return True, "{}", {
                    "v2": {
                        "credentialReadiness": {
                            "externalWriteChannels": {
                                "mcpExternalWrites": {
                                    "ready": False,
                                    "requirements": ["ATRIUM_MCP_GATEWAY_URL configured for write-capable external MCP servers"],
                                },
                            },
                        },
                    },
                }
            if path == "/api/tools/catalog":
                return True, "[]", []
            if path == "/api/connectors":
                return True, "[]", [
                    {
                        "id": "mcp",
                        "status": "available",
                        "readReady": True,
                        "writeReady": False,
                        "localFallback": True,
                        "accessToken": "connector-secret",
                        "externalWriteRequires": ["ATRIUM_MCP_GATEWAY_URL configured for write-capable external MCP servers"],
                    },
                ]
            return False, "unexpected", None

        output = io.StringIO()
        with (
            mock.patch.object(atrium_cli, "ensure_repo_root"),
            mock.patch.object(atrium_cli, "backend_json", side_effect=fake_backend_json),
            redirect_stdout(output),
        ):
            self.assertEqual(atrium_cli.command_tools(args), 0)

        payload = json.loads(output.getvalue())
        self.assertFalse(payload["ready"])
        self.assertIn("ATRIUM_MCP_GATEWAY_URL", payload["env"])
        self.assertTrue(payload["proofBlocker"]["blocked"])
        self.assertEqual(payload["proofBlocker"]["stage"], "mcp_external_write")
        self.assertEqual(payload["proofBlocker"]["proofFacet"], "mcpExternalWriteReady")
        self.assertIn(".\\atrium.ps1 restart --force", payload["windowsPowerShell"])
        self.assertIn(".\\atrium.ps1 tools status --json", payload["windowsPowerShell"])
        self.assertIn("windows-live-proof", payload["requiredBefore"])
        self.assertEqual(payload["connector"]["accessToken"], "set")
        self.assertNotIn("connector-secret", output.getvalue())

    def test_tools_mcp_gateway_requires_configured_read_write_external_connector(self) -> None:
        payload = atrium_cli.mcp_gateway_setup_payload(
            runtime_payload={},
            connectors_payload=[
                {
                    "id": "mcp",
                    "status": "available",
                    "readReady": False,
                    "writeReady": True,
                    "localFallback": False,
                    "externalWriteRequires": [],
                }
            ],
        )

        self.assertFalse(payload["ready"])
        self.assertTrue(payload["proofBlocker"]["blocked"])

    def test_tools_mcp_probe_json_calls_probe_endpoint_and_redacts_secret(self) -> None:
        args = type("Args", (), {"tools_action": "mcp-probe", "limit": 3, "json": True})()
        probe_payload = {
            "ok": True,
            "ready": False,
            "probe": True,
            "gatewayHealth": {"ok": False, "token": "secret-token"},
            "connector": {"id": "mcp", "writeReady": False},
            "requirements": ["MCP gateway list_tools health probe succeeds"],
        }

        def fake_backend_json(path: str, *, timeout: float = 5.0):
            if path == "/api/runtime":
                return True, "{}", {}
            if path == "/api/tools/catalog":
                return True, "[]", []
            if path == "/api/connectors":
                return True, "[]", []
            if path == "/api/tools/mcp-gateway?probe=true":
                return True, "{}", probe_payload
            return False, "unexpected", None

        output = io.StringIO()
        with (
            mock.patch.object(atrium_cli, "ensure_repo_root"),
            mock.patch.object(atrium_cli, "backend_json", side_effect=fake_backend_json) as backend_json,
            redirect_stdout(output),
        ):
            self.assertEqual(atrium_cli.command_tools(args), 0)

        paths = [call.args[0] for call in backend_json.call_args_list]
        self.assertIn("/api/tools/mcp-gateway?probe=true", paths)
        payload = json.loads(output.getvalue())
        self.assertTrue(payload["probe"])
        self.assertEqual(payload["gatewayHealth"]["token"], "set")
        self.assertNotIn("secret-token", output.getvalue())

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
        self.assertIn("--max-artifact-age-hours 24.0", text)
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
                    f"--expect-source-fingerprint {old_fingerprint} "
                    "--output C:\\Temp\\atrium_host_bridge_windows_live.json"
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
        self.assertIn("--output C:\\Temp\\atrium_host_bridge_windows_probe.json", normalized["windowsProbe"])
        self.assertNotIn("windows_live.json", normalized["windowsProbe"])
        self.assertIn(f"--source-fingerprint {current_fingerprint}", normalized["windowsLiveProofRunner"])
        self.assertIn(f"--source-manifest-sha256 {current_manifest}", normalized["windowsLiveProofRunner"])
        self.assertIn("--source-file-count 17", normalized["windowsLiveProofRunner"])
        self.assertEqual(
            normalized["windowsHandoff"],
            ".\\atrium.ps1 automation handoff --macos /tmp/atrium_host_bridge_macos_live.json --output /tmp/atrium_windows_handoff.json",
        )

    def test_parity_commands_reuse_current_local_handoff_run_id(self) -> None:
        commands = {
            "parityRunId": "atrium-new",
            "windowsLiveProofRunner": ".\\atrium.ps1 automation windows-live-proof --parity-run-id atrium-new",
            "windowsArtifactValidateOnWindows": ".\\atrium.ps1 automation artifact --expect-parity-run-id atrium-new",
        }
        local_artifacts = {
            "handoff": {
                "usable": True,
                "sourceStatus": "current",
                "parityRunId": "atrium-handoff",
            }
        }

        normalized = atrium_cli.align_parity_commands_with_local_artifacts(commands, local_artifacts)

        self.assertEqual(normalized["parityRunId"], "atrium-handoff")
        self.assertEqual(normalized["backendParityRunId"], "atrium-new")
        self.assertEqual(normalized["parityRunIdSource"], "local_handoff")
        self.assertIn("atrium-handoff", normalized["windowsLiveProofRunner"])
        self.assertIn("atrium-handoff", normalized["windowsArtifactValidateOnWindows"])
        self.assertNotIn("atrium-new", normalized["windowsLiveProofRunner"])

    def test_parity_commands_do_not_reuse_stale_handoff_run_id(self) -> None:
        commands = {
            "parityRunId": "atrium-new",
            "windowsLiveProofRunner": ".\\atrium.ps1 automation windows-live-proof --parity-run-id atrium-new",
        }
        local_artifacts = {
            "handoff": {
                "usable": False,
                "sourceStatus": "stale",
                "parityRunId": "atrium-old",
            }
        }

        normalized = atrium_cli.align_parity_commands_with_local_artifacts(commands, local_artifacts)

        self.assertEqual(normalized["parityRunId"], "atrium-new")
        self.assertNotIn("backendParityRunId", normalized)
        self.assertIn("atrium-new", normalized["windowsLiveProofRunner"])

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

    def test_automation_smoke_builds_windows_probe_command(self) -> None:
        args = type(
            "Args",
            (),
            {
                "automation_action": "smoke",
                "simulate": False,
                "browser_url": "http://127.0.0.1:5173",
                "browser_profile": "atrium",
                "output": None,
            },
        )()
        with (
            mock.patch.object(atrium_cli, "ensure_repo_root"),
            mock.patch.object(atrium_cli.platform, "system", return_value="Windows"),
            mock.patch.object(atrium_cli, "command_path", return_value="/bin/uv"),
            mock.patch.object(atrium_cli, "run_interactive") as run_interactive,
            redirect_stdout(io.StringIO()),
        ):
            self.assertEqual(atrium_cli.command_automation(args), 0)

        command = run_interactive.call_args.args[0]
        self.assertEqual(command[:5], ["/bin/uv", "--project", "system", "run", "python"])
        self.assertIn("ops/windows_host_bridge_probe.py", command)
        self.assertIn("--full", command)
        self.assertIn("--browser-url", command)
        self.assertIn("http://127.0.0.1:5173", command)
        self.assertIn("--browser-profile", command)
        self.assertIn("atrium", command)
        self.assertIn("C:\\Temp\\atrium_host_bridge_windows_smoke.json", command)

    def test_automation_smoke_builds_macos_probe_command(self) -> None:
        args = type(
            "Args",
            (),
            {
                "automation_action": "smoke",
                "simulate": True,
                "browser_url": "http://127.0.0.1:5173",
                "browser_profile": "atrium",
                "output": None,
            },
        )()
        with (
            mock.patch.object(atrium_cli, "ensure_repo_root"),
            mock.patch.object(atrium_cli.platform, "system", return_value="Darwin"),
            mock.patch.object(atrium_cli, "command_path", return_value="/bin/uv"),
            mock.patch.object(atrium_cli, "run_interactive") as run_interactive,
            redirect_stdout(io.StringIO()),
        ):
            self.assertEqual(atrium_cli.command_automation(args), 0)

        command = run_interactive.call_args.args[0]
        self.assertIn("ops/macos_host_bridge_probe.py", command)
        self.assertIn("--full", command)
        self.assertIn("--simulate", command)
        self.assertIn("/tmp/atrium_host_bridge_macos_smoke.json", command)

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

    def test_automation_windows_probe_full_defaults_to_standard_output_path(self) -> None:
        args = type(
            "Args",
            (),
            {
                "automation_action": "windows-probe",
                "simulate": False,
                "full": True,
                "screenshot": False,
                "notification": False,
                "interactive": False,
                "browser_url": None,
                "browser_profile": "atrium",
                "parity_run_id": None,
                "expect_source_fingerprint": None,
                "expect_source_manifest_sha256": None,
                "expect_source_file_count": None,
                "output": None,
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
        self.assertIn("--full", command)
        self.assertIn("--output", command)
        self.assertIn(atrium_cli.DEFAULT_WINDOWS_PROBE_PATH, command)

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
        self.assertIn("-MaxArtifactAgeHours", command)
        self.assertIn("24.0", command)

    def test_automation_handoff_validates_macos_artifact_and_writes_packet(self) -> None:
        macos_artifact = Path(tempfile.mkdtemp()) / "atrium_host_bridge_macos_live.json"
        macos_artifact.write_bytes(b"macos-proof")
        args = type(
            "Args",
            (),
            {
                "automation_action": "handoff",
                "macos": str(macos_artifact),
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
        self.assertEqual(payload["macosArtifact"]["artifactSha256"], "c" * 64)
        self.assertEqual(payload["macosArtifact"]["artifactBytes"], len(b"macos-proof"))
        self.assertEqual(payload["macosArtifact"]["path"], str(macos_artifact))
        self.assertEqual(payload["windowsProof"]["commands"]["nativeSetup"], ".\\atrium.ps1 setup --yes")
        self.assertEqual(payload["windowsProof"]["commands"]["nativeStart"], ".\\atrium.ps1 start")
        self.assertEqual(payload["windowsProof"]["commands"]["nativeStatusJson"], ".\\atrium.ps1 status --json")
        self.assertEqual(payload["windowsProof"]["commands"]["nativeLogsJson"], ".\\atrium.ps1 logs --json")
        self.assertEqual(payload["windowsProof"]["commands"]["nativeReportBundle"], ".\\atrium.ps1 report --bundle")
        self.assertEqual(payload["windowsProof"]["commands"]["nativeStop"], ".\\atrium.ps1 stop")
        self.assertEqual(payload["windowsProof"]["commands"]["nativeRestart"], ".\\atrium.ps1 restart --force")
        self.assertEqual(payload["windowsProof"]["commands"]["nativePermissionsStatusJson"], ".\\atrium.ps1 permissions status --json")
        self.assertEqual(payload["windowsProof"]["commands"]["nativePermissionsSetFullAuto"], ".\\atrium.ps1 permissions set full_auto --agent-full-access true")
        self.assertEqual(payload["windowsProof"]["commands"]["nativeProviderStatusProbeJson"], ".\\atrium.ps1 provider status --probe --json")
        self.assertEqual(payload["windowsProof"]["commands"]["nativeProviderReferenceJson"], ".\\atrium.ps1 provider reference --json")
        self.assertEqual(payload["windowsProof"]["commands"]["nativeProviderEnvJson"], ".\\atrium.ps1 provider env --json")
        self.assertEqual(payload["windowsProof"]["commands"]["nativeProviderLoginChatGPT"], ".\\atrium.ps1 provider login chatgpt")
        self.assertEqual(payload["windowsProof"]["commands"]["nativeProviderLoginClaudeCode"], ".\\atrium.ps1 provider login claude-code")
        self.assertEqual(payload["windowsProof"]["commands"]["nativeProviderDisconnectChatGPT"], ".\\atrium.ps1 provider disconnect chatgpt")
        self.assertEqual(payload["windowsProof"]["commands"]["nativeProviderDisconnectClaudeCode"], ".\\atrium.ps1 provider disconnect claude-code")
        self.assertEqual(payload["windowsProof"]["commands"]["nativeToolsStatusJson"], ".\\atrium.ps1 tools status --json")
        self.assertEqual(payload["windowsProof"]["commands"]["nativeToolsCatalogJson"], ".\\atrium.ps1 tools catalog --json")
        self.assertEqual(payload["windowsProof"]["commands"]["mcpGatewaySetupJson"], ".\\atrium.ps1 tools mcp-gateway --json")
        self.assertEqual(payload["windowsProof"]["commands"]["mcpGatewayProbeJson"], ".\\atrium.ps1 tools mcp-probe --json")
        self.assertEqual(payload["windowsProof"]["commands"]["mcpGatewayStatusJson"], ".\\atrium.ps1 tools status --json")
        self.assertEqual(payload["windowsProof"]["proofFacetCount"], len(atrium_cli.REQUIRED_WINDOWS_PROOF_FACETS))
        self.assertIn("windowsLiveProofRunner", payload["windowsProof"]["requiredProofFacets"])
        self.assertIn("mcpExternalWriteReady", payload["windowsProof"]["requiredProofFacets"])
        self.assertEqual(
            payload["windowsProof"]["failureStages"]["mcp_external_write"]["proofFacet"],
            "mcpExternalWriteReady",
        )
        self.assertEqual(
            payload["windowsProof"]["readinessGates"]["mcpExternalWrite"]["stage"],
            "mcp_external_write",
        )
        self.assertEqual(
            payload["windowsProof"]["readinessGates"]["browserDesktopSmoke"]["diagnosticCommandKey"],
            "windowsProbe",
        )
        self.assertIn(".\\atrium.ps1 automation smoke", payload["windowsProof"]["commands"]["nativeBrowserDesktopSmoke"])
        self.assertIn("C:\\Temp\\atrium_host_bridge_windows_smoke.json", payload["windowsProof"]["commands"]["nativeBrowserDesktopSmoke"])
        self.assertIn(".\\atrium.ps1 automation windows-live-proof", payload["windowsProof"]["commands"]["liveProof"])
        self.assertIn("--json", payload["windowsProof"]["commands"]["sourceValidate"])
        self.assertIn("--max-artifact-age-hours 24.0", payload["windowsProof"]["commands"]["artifactValidate"])
        self.assertIn("--json", payload["windowsProof"]["commands"]["artifactValidate"])
        self.assertIn("--source-manifest-sha256 " + "a" * 64, payload["windowsProof"]["commands"]["liveProof"])
        self.assertIn("--max-artifact-age-hours 24.0", payload["windowsProof"]["commands"]["liveProof"])
        checklist = payload["windowsProof"]["operatorChecklist"]
        provider_step = next(item for item in checklist if item["id"] == "native_provider_ai_tools")
        self.assertEqual(
            provider_step["accountSwitchCommands"],
            [".\\atrium.ps1 provider disconnect chatgpt", ".\\atrium.ps1 provider disconnect claude-code"],
        )
        self.assertEqual(
            [item["id"] for item in checklist],
            [
                "native_setup_start",
                "native_permissions",
                "native_provider_ai_tools",
                "native_logs_report",
                "native_stop_restart",
                "mcp_gateway_setup",
                "mcp_gateway_probe",
                "mcp_gateway_status",
                "native_browser_desktop_smoke",
                "windows_raw_probe",
                "source_validate",
                "windows_live_proof",
                "artifact_validate_on_windows",
                "copy_to_repo_host",
                "accept_windows_artifact",
                "generate_report",
                "audit_gate",
            ],
        )
        self.assertIn(".\\atrium.ps1 setup --yes", checklist[0]["command"])
        self.assertIn(".\\atrium.ps1 permissions set full_auto", checklist[1]["command"])
        self.assertIn(".\\atrium.ps1 provider status --probe --json", checklist[2]["command"])
        self.assertEqual(checklist[2]["loginCommands"], [".\\atrium.ps1 provider login chatgpt", ".\\atrium.ps1 provider login claude-code"])
        self.assertIn(".\\atrium.ps1 logs --json", checklist[3]["command"])
        self.assertIn(".\\atrium.ps1 restart --force", checklist[4]["command"])
        self.assertIn(payload["windowsProof"]["commands"]["mcpGatewaySetupJson"], checklist[5]["command"])
        self.assertIn(payload["windowsProof"]["commands"]["mcpGatewayProbeJson"], checklist[6]["command"])
        self.assertEqual(checklist[6]["failureStage"], "mcp_external_write")
        self.assertEqual(checklist[6]["proofFacet"], "mcpExternalWriteReady")
        self.assertIn(payload["windowsProof"]["commands"]["mcpGatewayStatusJson"], checklist[7]["command"])
        self.assertIn(payload["windowsProof"]["commands"]["nativeBrowserDesktopSmoke"], checklist[8]["command"])
        self.assertEqual(checklist[8]["failureStage"], "windows_full_probe")
        self.assertIn(payload["windowsProof"]["commands"]["windowsProbe"], checklist[9]["command"])
        self.assertEqual(checklist[9]["failureStage"], "windows_full_probe")
        self.assertIn("C:\\Temp\\atrium_host_bridge_windows_probe.json", checklist[9]["command"])
        self.assertEqual(checklist[10]["command"], payload["windowsProof"]["commands"]["sourceValidate"])
        self.assertEqual(checklist[10]["failureStage"], "source_validate")
        self.assertEqual(checklist[11]["command"], payload["windowsProof"]["commands"]["liveProof"])
        self.assertIn("mcp_external_write", checklist[11]["failureStages"])
        self.assertIn("artifact_validate", checklist[11]["failureStages"])
        self.assertEqual(checklist[12]["command"], payload["windowsProof"]["commands"]["artifactValidate"])
        self.assertEqual(checklist[12]["failureStage"], "artifact_validate")
        self.assertEqual(checklist[13]["from"], "C:\\Temp\\atrium_host_bridge_windows_live.json")
        self.assertEqual(checklist[13]["to"], "/tmp/atrium_host_bridge_windows_live.json")
        self.assertIn("accept-windows", checklist[14]["command"])
        self.assertIn("--handoff '/tmp/atrium_windows_handoff_test.json'", checklist[14]["command"])
        self.assertIn("accept-windows", payload["windowsProof"]["commands"]["acceptWindowsArtifact"])
        self.assertIn("--handoff '/tmp/atrium_windows_handoff_test.json'", payload["windowsProof"]["commands"]["acceptWindowsArtifact"])
        self.assertIn("accept-windows", payload["finalVerification"]["commands"]["acceptWindowsArtifact"])
        self.assertIn("--handoff '/tmp/atrium_windows_handoff_test.json'", payload["finalVerification"]["commands"]["acceptWindowsArtifact"])
        self.assertIn("./atrium automation report", payload["finalVerification"]["commands"]["report"])
        self.assertIn("--max-artifact-age-hours 24.0", payload["finalVerification"]["commands"]["report"])
        self.assertEqual(
            [item["id"] for item in payload["finalVerification"]["operatorChecklist"]],
            ["accept_windows_artifact", "generate_report", "audit_gate"],
        )
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

    def test_automation_report_treats_relative_default_output_as_backend_report(self) -> None:
        relative_default = str(atrium_cli.HOST_BRIDGE_PARITY_REPORT.relative_to(atrium_cli.ROOT))
        args = type(
            "Args",
            (),
            {
                "automation_action": "report",
                "macos": "/tmp/macos.json",
                "windows": "/tmp/windows.json",
                "output": relative_default,
                "max_artifact_age_hours": 24.0,
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
        output_index = command.index("--output") + 1
        self.assertEqual(command[output_index], str(atrium_cli.HOST_BRIDGE_PARITY_REPORT))
        self.assertIn("backend default report path", output.getvalue())

    def test_automation_accept_windows_validates_imports_reports_and_audits(self) -> None:
        source = {
            "sourceFingerprint": "a" * 64,
            "sourceManifestSha256": "a" * 64,
            "sourceFileCount": 21,
            "gitHead": "b" * 40,
        }
        handoff = {
            "ok": True,
            "kind": "atrium.hostBridge.windowsProofHandoff",
            "source": source,
            "macosArtifact": {
                "path": "/tmp/atrium_host_bridge_macos_live.json",
                "parityRunId": "atrium-run-1",
            },
            "windowsProof": {
                "outputPath": "C:\\Temp\\atrium_host_bridge_windows_live.json",
                "localCopyPath": "/tmp/atrium_host_bridge_windows_live.json",
                "proofFacetCount": 25,
                "requiredProofFacets": list(atrium_cli.REQUIRED_WINDOWS_PROOF_FACETS),
                "failureStages": {
                    stage: {"stage": stage}
                    for stage in atrium_cli.WINDOWS_LIVE_PROOF_FAILURE_STAGE_IDS
                },
                "readinessGates": {
                    "source": {"stage": "source_validate"},
                    "mcpExternalWrite": {"stage": "mcp_external_write", "proofFacet": "mcpExternalWriteReady"},
                    "browserDesktopSmoke": {"stage": "windows_full_probe"},
                    "artifactValidation": {"stage": "artifact_validate"},
                },
            },
        }
        artifact_summaries = [
            {
                "ok": True,
                "label": "macos",
                "proofFacetCount": 19,
                "parityRunId": "atrium-run-1",
                "artifactSha256": "1" * 64,
                "artifactBytes": 1024,
            },
            {
                "ok": True,
                "label": "windows",
                "proofFacetCount": 25,
                "requiredProofFacets": list(atrium_cli.REQUIRED_WINDOWS_PROOF_FACETS),
                "missingProofFacetCount": 0,
                "parityRunId": "atrium-run-1",
                "artifactSha256": "2" * 64,
                "artifactBytes": 2048,
            },
        ]
        run_commands: list[list[str]] = []
        proof_id = "d" * 64
        verified_audit = {
            "ok": True,
            "status": "cross_os_verified",
            "contract": {"status": "cross_os_verified"},
            "commands": {},
            "report": {
                "ok": True,
                "details": {
                    "proofId": proof_id,
                    "expectedProofId": proof_id,
                    "currentSourceFingerprint": "a" * 64,
                    "currentSourceManifestSha256": "a" * 64,
                    "currentSourceFileCount": 21,
                    "currentGitHead": "b" * 40,
                    "artifactSha256": {"macos": "1" * 64, "windows": "2" * 64},
                    "artifactBytes": {"macos": 1024, "windows": 2048},
                    "parityRunId": "atrium-run-1",
                    "hostFingerprint": {"macos": "3" * 64, "windows": "4" * 64},
                    "hostPlatform": {"macos": "darwin", "windows": "win32"},
                    "hostName": {"macos": "mac-host", "windows": "win-host"},
                },
            },
        }

        def fake_run(command: list[str], **_kwargs: object) -> atrium_cli.CommandResult:
            run_commands.append(command)
            text = " ".join(command)
            if "ops/host_bridge_artifact_summary.py" in text:
                return atrium_cli.CommandResult(0, json.dumps(artifact_summaries.pop(0)), "")
            if "ops/host_bridge_parity_report.py" in text:
                return atrium_cli.CommandResult(0, json.dumps({"ok": True}), "")
            raise AssertionError(f"unexpected command: {command}")

        args = type(
            "Args",
            (),
            {
                "automation_action": "accept-windows",
                "artifact": "/incoming/windows.json",
                "handoff": "/tmp/atrium_windows_handoff.json",
                "output": str(atrium_cli.HOST_BRIDGE_PARITY_REPORT),
                "max_artifact_age_hours": 24.0,
                "windows_source_path": "C:\\Temp\\atrium_host_bridge_windows_live.json",
                "json": True,
            },
        )()
        with (
            mock.patch.object(atrium_cli, "ensure_repo_root"),
            mock.patch.object(atrium_cli, "current_source_summary", return_value=source),
            mock.patch.object(atrium_cli, "_load_json_file", return_value=(handoff, None)),
            mock.patch.object(atrium_cli, "command_path", return_value="/bin/uv"),
            mock.patch.object(atrium_cli, "run", side_effect=fake_run),
            mock.patch.object(atrium_cli.shutil, "copy2") as copy2,
            mock.patch.object(
                atrium_cli,
                "backend_json",
                return_value=(True, "{}", verified_audit),
            ),
            mock.patch.object(
                atrium_cli,
                "collect_local_proof_artifacts",
                return_value={
                    "currentSourceFingerprint": "a" * 64,
                    "macos": {"usable": True, "parityRunId": "atrium-run-1"},
                    "handoff": {"usable": True, "parityRunId": "atrium-run-1"},
                    "windowsLocal": {
                        "usable": True,
                        "parityRunId": "atrium-run-1",
                        "sourceFingerprint": "a" * 64,
                        "sourceStatus": "current",
                    },
                },
            ),
            redirect_stdout(io.StringIO()) as output,
        ):
            self.assertEqual(atrium_cli.command_automation(args), 0)

        payload = json.loads(output.getvalue())
        self.assertTrue(payload["ok"])
        self.assertEqual(payload["status"], "cross_os_verified")
        self.assertEqual(payload["windowsProofFacetCount"], 25)
        copy2.assert_called_once()
        self.assertEqual(sum("ops/host_bridge_artifact_summary.py" in " ".join(cmd) for cmd in run_commands), 2)
        report_command = next(cmd for cmd in run_commands if "ops/host_bridge_parity_report.py" in " ".join(cmd))
        self.assertIn("--windows-source-path", report_command)
        self.assertIn("C:\\Temp\\atrium_host_bridge_windows_live.json", report_command)

    def test_automation_accept_windows_requires_installed_local_artifact_matches_handoff(self) -> None:
        source = {
            "sourceFingerprint": "a" * 64,
            "sourceManifestSha256": "a" * 64,
            "sourceFileCount": 21,
            "gitHead": "b" * 40,
        }
        handoff = {
            "ok": True,
            "kind": "atrium.hostBridge.windowsProofHandoff",
            "source": source,
            "macosArtifact": {
                "path": "/tmp/atrium_host_bridge_macos_live.json",
                "parityRunId": "atrium-run-1",
            },
            "windowsProof": {
                "outputPath": "C:\\Temp\\atrium_host_bridge_windows_live.json",
                "localCopyPath": "/tmp/atrium_host_bridge_windows_live.json",
                "proofFacetCount": 25,
                "requiredProofFacets": list(atrium_cli.REQUIRED_WINDOWS_PROOF_FACETS),
                "failureStages": {
                    stage: {"stage": stage}
                    for stage in atrium_cli.WINDOWS_LIVE_PROOF_FAILURE_STAGE_IDS
                },
                "readinessGates": {
                    "source": {"stage": "source_validate"},
                    "mcpExternalWrite": {"stage": "mcp_external_write", "proofFacet": "mcpExternalWriteReady"},
                    "browserDesktopSmoke": {"stage": "windows_full_probe"},
                    "artifactValidation": {"stage": "artifact_validate"},
                },
            },
        }
        artifact_summaries = [
            {
                "ok": True,
                "label": "macos",
                "proofFacetCount": 19,
                "parityRunId": "atrium-run-1",
                "artifactSha256": "1" * 64,
                "artifactBytes": 1024,
            },
            {
                "ok": True,
                "label": "windows",
                "proofFacetCount": 25,
                "requiredProofFacets": list(atrium_cli.REQUIRED_WINDOWS_PROOF_FACETS),
                "missingProofFacetCount": 0,
                "parityRunId": "atrium-run-1",
                "artifactSha256": "2" * 64,
                "artifactBytes": 2048,
            },
        ]
        proof_id = "d" * 64
        verified_audit = {
            "ok": True,
            "status": "cross_os_verified",
            "contract": {"status": "cross_os_verified"},
            "commands": {},
            "report": {
                "ok": True,
                "details": {
                    "proofId": proof_id,
                    "expectedProofId": proof_id,
                    "currentSourceFingerprint": "a" * 64,
                    "currentSourceManifestSha256": "a" * 64,
                    "currentSourceFileCount": 21,
                    "currentGitHead": "b" * 40,
                    "artifactSha256": {"macos": "1" * 64, "windows": "2" * 64},
                    "artifactBytes": {"macos": 1024, "windows": 2048},
                    "parityRunId": "atrium-run-1",
                    "hostFingerprint": {"macos": "3" * 64, "windows": "4" * 64},
                    "hostPlatform": {"macos": "darwin", "windows": "win32"},
                    "hostName": {"macos": "mac-host", "windows": "win-host"},
                },
            },
        }

        def fake_run(command: list[str], **_kwargs: object) -> atrium_cli.CommandResult:
            text = " ".join(command)
            if "ops/host_bridge_artifact_summary.py" in text:
                return atrium_cli.CommandResult(0, json.dumps(artifact_summaries.pop(0)), "")
            if "ops/host_bridge_parity_report.py" in text:
                return atrium_cli.CommandResult(0, json.dumps({"ok": True}), "")
            raise AssertionError(f"unexpected command: {command}")

        with (
            mock.patch.object(atrium_cli, "ensure_repo_root"),
            mock.patch.object(atrium_cli, "current_source_summary", return_value=source),
            mock.patch.object(atrium_cli, "_load_json_file", return_value=(handoff, None)),
            mock.patch.object(atrium_cli, "command_path", return_value="/bin/uv"),
            mock.patch.object(atrium_cli, "run", side_effect=fake_run),
            mock.patch.object(atrium_cli.shutil, "copy2"),
            mock.patch.object(
                atrium_cli,
                "backend_json",
                return_value=(True, "{}", verified_audit),
            ),
            mock.patch.object(
                atrium_cli,
                "collect_local_proof_artifacts",
                return_value={
                    "currentSourceFingerprint": "a" * 64,
                    "macos": {"usable": True, "parityRunId": "atrium-run-1"},
                    "handoff": {"usable": True, "parityRunId": "atrium-run-1"},
                    "windowsLocal": {
                        "usable": True,
                        "parityRunId": "atrium-run-old",
                        "sourceFingerprint": "c" * 64,
                        "sourceStatus": "stale",
                    },
                },
            ),
            redirect_stdout(io.StringIO()) as output,
        ):
            self.assertEqual(atrium_cli.command_automation(
                type(
                    "Args",
                    (),
                    {
                        "automation_action": "accept-windows",
                        "artifact": "/incoming/windows.json",
                        "handoff": "/tmp/atrium_windows_handoff.json",
                        "output": str(atrium_cli.HOST_BRIDGE_PARITY_REPORT),
                        "max_artifact_age_hours": 24.0,
                        "windows_source_path": "C:\\Temp\\atrium_host_bridge_windows_live.json",
                        "json": True,
                    },
                )()
            ), 2)

        payload = json.loads(output.getvalue())
        self.assertFalse(payload["ok"])
        self.assertEqual(payload["status"], "cross_os_unverified")
        self.assertIn("windowsLocal.parityRunId mismatch", "\n".join(payload["audit"]["gaps"]))

    def test_automation_accept_windows_requires_report_proof_id_matches_current_source(self) -> None:
        source = {
            "sourceFingerprint": "a" * 64,
            "sourceManifestSha256": "a" * 64,
            "sourceFileCount": 21,
            "gitHead": "b" * 40,
        }
        handoff = {
            "ok": True,
            "kind": "atrium.hostBridge.windowsProofHandoff",
            "source": source,
            "macosArtifact": {
                "path": "/tmp/atrium_host_bridge_macos_live.json",
                "parityRunId": "atrium-run-1",
            },
            "windowsProof": {
                "outputPath": "C:\\Temp\\atrium_host_bridge_windows_live.json",
                "localCopyPath": "/tmp/atrium_host_bridge_windows_live.json",
                "proofFacetCount": 25,
                "requiredProofFacets": list(atrium_cli.REQUIRED_WINDOWS_PROOF_FACETS),
                "failureStages": {
                    stage: {"stage": stage}
                    for stage in atrium_cli.WINDOWS_LIVE_PROOF_FAILURE_STAGE_IDS
                },
                "readinessGates": {
                    "source": {"stage": "source_validate"},
                    "mcpExternalWrite": {"stage": "mcp_external_write", "proofFacet": "mcpExternalWriteReady"},
                    "browserDesktopSmoke": {"stage": "windows_full_probe"},
                    "artifactValidation": {"stage": "artifact_validate"},
                },
            },
        }
        artifact_summaries = [
            {
                "ok": True,
                "label": "macos",
                "proofFacetCount": 19,
                "parityRunId": "atrium-run-1",
                "artifactSha256": "1" * 64,
                "artifactBytes": 1024,
            },
            {
                "ok": True,
                "label": "windows",
                "proofFacetCount": 25,
                "requiredProofFacets": list(atrium_cli.REQUIRED_WINDOWS_PROOF_FACETS),
                "missingProofFacetCount": 0,
                "parityRunId": "atrium-run-1",
                "artifactSha256": "2" * 64,
                "artifactBytes": 2048,
            },
        ]

        def fake_run(command: list[str], **_kwargs: object) -> atrium_cli.CommandResult:
            text = " ".join(command)
            if "ops/host_bridge_artifact_summary.py" in text:
                return atrium_cli.CommandResult(0, json.dumps(artifact_summaries.pop(0)), "")
            if "ops/host_bridge_parity_report.py" in text:
                return atrium_cli.CommandResult(0, json.dumps({"ok": True}), "")
            raise AssertionError(f"unexpected command: {command}")

        with (
            mock.patch.object(atrium_cli, "ensure_repo_root"),
            mock.patch.object(atrium_cli, "current_source_summary", return_value=source),
            mock.patch.object(atrium_cli, "_load_json_file", return_value=(handoff, None)),
            mock.patch.object(atrium_cli, "command_path", return_value="/bin/uv"),
            mock.patch.object(atrium_cli, "run", side_effect=fake_run),
            mock.patch.object(atrium_cli.shutil, "copy2"),
            mock.patch.object(
                atrium_cli,
                "backend_json",
                return_value=(
                    True,
                    "{}",
                    {
                        "ok": True,
                        "status": "cross_os_verified",
                        "contract": {"status": "cross_os_verified"},
                        "commands": {},
                        "report": {
                            "ok": True,
                            "details": {
                                "proofId": "d" * 64,
                                "expectedProofId": "e" * 64,
                                "currentSourceFingerprint": "c" * 64,
                                "currentSourceManifestSha256": "a" * 64,
                                "currentSourceFileCount": 21,
                                "currentGitHead": "b" * 40,
                                "artifactSha256": {"macos": "1" * 64, "windows": "3" * 64},
                                "artifactBytes": {"macos": 1024, "windows": 4096},
                                "parityRunId": "atrium-run-old",
                                "hostFingerprint": {"macos": "3" * 64, "windows": "not-hex"},
                                "hostPlatform": {"macos": "darwin", "windows": "linux"},
                                "hostName": {"macos": "mac-host", "windows": ""},
                            },
                        },
                    },
                ),
            ),
            mock.patch.object(
                atrium_cli,
                "collect_local_proof_artifacts",
                return_value={
                    "currentSourceFingerprint": "a" * 64,
                    "macos": {"usable": True, "parityRunId": "atrium-run-1"},
                    "handoff": {"usable": True, "parityRunId": "atrium-run-1"},
                    "windowsLocal": {
                        "usable": True,
                        "parityRunId": "atrium-run-1",
                        "sourceFingerprint": "a" * 64,
                        "sourceStatus": "current",
                    },
                },
            ),
            redirect_stdout(io.StringIO()) as output,
        ):
            self.assertEqual(atrium_cli.command_automation(
                type(
                    "Args",
                    (),
                    {
                        "automation_action": "accept-windows",
                        "artifact": "/incoming/windows.json",
                        "handoff": "/tmp/atrium_windows_handoff.json",
                        "output": str(atrium_cli.HOST_BRIDGE_PARITY_REPORT),
                        "max_artifact_age_hours": 24.0,
                        "windows_source_path": "C:\\Temp\\atrium_host_bridge_windows_live.json",
                        "json": True,
                    },
                )()
            ), 2)

        payload = json.loads(output.getvalue())
        self.assertFalse(payload["ok"])
        text = "\n".join(payload["audit"]["gaps"])
        self.assertIn("report.details.proofId", text)
        self.assertIn("report.details.currentSourceFingerprint", text)
        self.assertIn("report.details.artifactSha256.windows", text)
        self.assertIn("report.details.artifactBytes.windows", text)
        self.assertIn("report.details.parityRunId", text)
        self.assertIn("report.details.hostFingerprint.windows", text)
        self.assertIn("report.details.hostPlatform.windows", text)
        self.assertIn("report.details.hostName.windows", text)

    def test_automation_accept_windows_refuses_handoff_without_openclaw_contract(self) -> None:
        source = {
            "sourceFingerprint": "a" * 64,
            "sourceManifestSha256": "a" * 64,
            "sourceFileCount": 21,
            "gitHead": "b" * 40,
        }
        handoff = {
            "ok": True,
            "kind": "atrium.hostBridge.windowsProofHandoff",
            "source": source,
            "macosArtifact": {
                "path": "/tmp/atrium_host_bridge_macos_live.json",
                "parityRunId": "atrium-run-1",
            },
            "windowsProof": {
                "outputPath": "C:\\Temp\\atrium_host_bridge_windows_live.json",
                "localCopyPath": "/tmp/atrium_host_bridge_windows_live.json",
                "proofFacetCount": 2,
                "requiredProofFacets": ["mcpExternalWriteReady", "windowsLiveProofRunner"],
            },
        }
        with (
            mock.patch.object(atrium_cli, "current_source_summary", return_value=source),
            mock.patch.object(atrium_cli, "_load_json_file", return_value=(handoff, None)),
            mock.patch.object(atrium_cli, "run") as run_command,
            self.assertRaises(atrium_cli.StepFailure) as raised,
        ):
            atrium_cli.accept_windows_artifact(
                artifact="/incoming/windows.json",
                handoff_path="/tmp/atrium_windows_handoff.json",
                output=str(atrium_cli.HOST_BRIDGE_PARITY_REPORT),
                max_artifact_age_hours=24.0,
                windows_source_path="C:\\Temp\\atrium_host_bridge_windows_live.json",
            )

        self.assertIn("handoff contract is not OpenClaw-complete", str(raised.exception))
        run_command.assert_not_called()

    def test_automation_accept_windows_refuses_handoff_without_artifact_paths(self) -> None:
        source = {
            "sourceFingerprint": "a" * 64,
            "sourceManifestSha256": "a" * 64,
            "sourceFileCount": 21,
            "gitHead": "b" * 40,
        }
        handoff = {
            "ok": True,
            "kind": "atrium.hostBridge.windowsProofHandoff",
            "source": source,
            "macosArtifact": {
                "path": "/tmp/atrium_host_bridge_macos_live.json",
                "parityRunId": "atrium-run-1",
            },
            "windowsProof": {
                "outputPath": "C:\\Temp\\atrium_host_bridge_windows_live.json",
                "proofFacetCount": len(atrium_cli.REQUIRED_WINDOWS_PROOF_FACETS),
                "requiredProofFacets": list(atrium_cli.REQUIRED_WINDOWS_PROOF_FACETS),
                "failureStages": {
                    stage: {"stage": stage}
                    for stage in atrium_cli.WINDOWS_LIVE_PROOF_FAILURE_STAGE_IDS
                },
                "readinessGates": {
                    "source": {"stage": "source_validate"},
                    "mcpExternalWrite": {"stage": "mcp_external_write", "proofFacet": "mcpExternalWriteReady"},
                    "browserDesktopSmoke": {"stage": "windows_full_probe"},
                    "artifactValidation": {"stage": "artifact_validate"},
                },
            },
        }
        with (
            mock.patch.object(atrium_cli, "current_source_summary", return_value=source),
            mock.patch.object(atrium_cli, "_load_json_file", return_value=(handoff, None)),
            mock.patch.object(atrium_cli, "run") as run_command,
            self.assertRaises(atrium_cli.StepFailure) as raised,
        ):
            atrium_cli.accept_windows_artifact(
                artifact="/incoming/windows.json",
                handoff_path="/tmp/atrium_windows_handoff.json",
                output=str(atrium_cli.HOST_BRIDGE_PARITY_REPORT),
                max_artifact_age_hours=24.0,
                windows_source_path="C:\\Temp\\atrium_host_bridge_windows_live.json",
            )

        self.assertIn("handoff contract is not OpenClaw-complete", str(raised.exception))
        self.assertIn("localCopyPath", raised.exception.next_step or "")
        run_command.assert_not_called()

    def test_automation_accept_windows_refuses_artifact_summary_mismatched_to_handoff_contract(self) -> None:
        source = {
            "sourceFingerprint": "a" * 64,
            "sourceManifestSha256": "a" * 64,
            "sourceFileCount": 21,
            "gitHead": "b" * 40,
        }
        handoff = {
            "ok": True,
            "kind": "atrium.hostBridge.windowsProofHandoff",
            "source": source,
            "macosArtifact": {
                "path": "/tmp/atrium_host_bridge_macos_live.json",
                "parityRunId": "atrium-run-1",
            },
            "windowsProof": {
                "outputPath": "C:\\Temp\\atrium_host_bridge_windows_live.json",
                "localCopyPath": "/tmp/atrium_host_bridge_windows_live.json",
                "proofFacetCount": len(atrium_cli.REQUIRED_WINDOWS_PROOF_FACETS),
                "requiredProofFacets": list(atrium_cli.REQUIRED_WINDOWS_PROOF_FACETS),
                "failureStages": {
                    stage: {"stage": stage}
                    for stage in atrium_cli.WINDOWS_LIVE_PROOF_FAILURE_STAGE_IDS
                },
                "readinessGates": {
                    "source": {"stage": "source_validate"},
                    "mcpExternalWrite": {"stage": "mcp_external_write", "proofFacet": "mcpExternalWriteReady"},
                    "browserDesktopSmoke": {"stage": "windows_full_probe"},
                    "artifactValidation": {"stage": "artifact_validate"},
                },
            },
        }
        artifact_summaries = [
            {"ok": True, "label": "macos", "proofFacetCount": 19, "parityRunId": "atrium-run-1"},
            {
                "ok": True,
                "label": "windows",
                "proofFacetCount": 2,
                "requiredProofFacets": ["mcpExternalWriteReady", "windowsLiveProofRunner"],
                "missingProofFacetCount": 0,
                "parityRunId": "atrium-run-1",
            },
        ]

        def fake_run(command: list[str], **_kwargs: object) -> atrium_cli.CommandResult:
            text = " ".join(command)
            if "ops/host_bridge_artifact_summary.py" in text:
                return atrium_cli.CommandResult(0, json.dumps(artifact_summaries.pop(0)), "")
            raise AssertionError(f"unexpected command: {command}")

        with (
            mock.patch.object(atrium_cli, "current_source_summary", return_value=source),
            mock.patch.object(atrium_cli, "_load_json_file", return_value=(handoff, None)),
            mock.patch.object(atrium_cli, "command_path", return_value="/bin/uv"),
            mock.patch.object(atrium_cli, "run", side_effect=fake_run),
            mock.patch.object(atrium_cli.shutil, "copy2") as copy2,
            self.assertRaises(atrium_cli.StepFailure) as raised,
        ):
            atrium_cli.accept_windows_artifact(
                artifact="/incoming/windows.json",
                handoff_path="/tmp/atrium_windows_handoff.json",
                output=str(atrium_cli.HOST_BRIDGE_PARITY_REPORT),
                max_artifact_age_hours=24.0,
                windows_source_path="C:\\Temp\\atrium_host_bridge_windows_live.json",
            )

        self.assertIn("does not satisfy the current OpenClaw handoff contract", str(raised.exception))
        copy2.assert_not_called()

    def test_automation_accept_windows_refuses_mismatched_windows_source_path(self) -> None:
        source = {
            "sourceFingerprint": "a" * 64,
            "sourceManifestSha256": "a" * 64,
            "sourceFileCount": 21,
            "gitHead": "b" * 40,
        }
        handoff = {
            "ok": True,
            "kind": "atrium.hostBridge.windowsProofHandoff",
            "source": source,
            "macosArtifact": {
                "path": "/tmp/atrium_host_bridge_macos_live.json",
                "parityRunId": "atrium-run-1",
            },
            "windowsProof": {
                "outputPath": "C:\\Temp\\atrium_host_bridge_windows_live.json",
                "localCopyPath": "/tmp/atrium_host_bridge_windows_live.json",
                "proofFacetCount": len(atrium_cli.REQUIRED_WINDOWS_PROOF_FACETS),
                "requiredProofFacets": list(atrium_cli.REQUIRED_WINDOWS_PROOF_FACETS),
                "failureStages": {
                    stage: {"stage": stage}
                    for stage in atrium_cli.WINDOWS_LIVE_PROOF_FAILURE_STAGE_IDS
                },
                "readinessGates": {
                    "source": {"stage": "source_validate"},
                    "mcpExternalWrite": {"stage": "mcp_external_write", "proofFacet": "mcpExternalWriteReady"},
                    "browserDesktopSmoke": {"stage": "windows_full_probe"},
                    "artifactValidation": {"stage": "artifact_validate"},
                },
            },
        }
        with (
            mock.patch.object(atrium_cli, "current_source_summary", return_value=source),
            mock.patch.object(atrium_cli, "_load_json_file", return_value=(handoff, None)),
            mock.patch.object(atrium_cli, "run") as run_command,
            self.assertRaises(atrium_cli.StepFailure) as raised,
        ):
            atrium_cli.accept_windows_artifact(
                artifact="/incoming/windows.json",
                handoff_path="/tmp/atrium_windows_handoff.json",
                output=str(atrium_cli.HOST_BRIDGE_PARITY_REPORT),
                max_artifact_age_hours=24.0,
                windows_source_path="D:\\Other\\atrium_host_bridge_windows_live.json",
            )

        self.assertIn("source path does not match the current handoff", str(raised.exception))
        run_command.assert_not_called()

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

    def test_automation_report_refuses_historical_override_for_relative_backend_default_path(self) -> None:
        args = type(
            "Args",
            (),
            {
                "automation_action": "report",
                "macos": "/tmp/macos.json",
                "windows": "/tmp/windows.json",
                "output": str(atrium_cli.HOST_BRIDGE_PARITY_REPORT.relative_to(atrium_cli.ROOT)),
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
