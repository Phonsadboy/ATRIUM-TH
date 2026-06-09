import unittest
from pathlib import Path
from tempfile import TemporaryDirectory
from unittest import mock


class VersionStatusTest(unittest.TestCase):
    def _fake_git_value(self, local: str, remote: str):
        def fake(_path: Path, *args: str, timeout: float = 4.0) -> tuple[str, str | None]:
            values = {
                ("rev-parse", "--is-inside-work-tree"): ("true", None),
                ("rev-parse", "HEAD"): (local, None),
                ("rev-parse", "--abbrev-ref", "HEAD"): ("main", None),
                ("rev-parse", "--abbrev-ref", "--symbolic-full-name", "@{u}"): ("origin/main", None),
                ("remote", "get-url", "origin"): ("git@github.com-atrium-th:Phonsadboy/ATRIUM-TH.git", None),
                ("ls-remote", "origin", "refs/heads/main"): (f"{remote}\trefs/heads/main", None),
                ("status", "--porcelain"): ("", None),
                ("cat-file", "-e", f"{remote}^{{commit}}"): ("", None),
            }
            return values.get(args, ("", f"unexpected git call: {args!r}"))

        return fake

    def test_current_when_local_matches_github_commit(self) -> None:
        from app import main

        sha = "a" * 40
        with mock.patch.object(main, "_git_value", self._fake_git_value(sha, sha)):
            status = main._version_status(Path("/repo"))

        self.assertTrue(status["ok"])
        self.assertEqual(status["status"], "current")
        self.assertEqual(status["relation"], "identical")
        self.assertEqual(status["localShort"], "a" * 12)
        self.assertEqual(status["remoteShort"], "a" * 12)

    def test_outdated_when_github_compare_reports_remote_ahead(self) -> None:
        from app import main

        local = "a" * 40
        remote = "b" * 40
        with (
            mock.patch.object(main, "_git_value", self._fake_git_value(local, remote)),
            mock.patch.object(main, "_github_compare", return_value=("ahead", None)),
        ):
            status = main._version_status(Path("/repo"))

        self.assertFalse(status["ok"])
        self.assertEqual(status["status"], "outdated")
        self.assertEqual(status["relation"], "ahead")
        self.assertIn("Phonsadboy/ATRIUM-TH", status["compareUrl"])

    def test_ahead_when_github_compare_reports_remote_behind(self) -> None:
        from app import main

        local = "b" * 40
        remote = "a" * 40
        with (
            mock.patch.object(main, "_git_value", self._fake_git_value(local, remote)),
            mock.patch.object(main, "_github_compare", return_value=("behind", None)),
        ):
            status = main._version_status(Path("/repo"))

        self.assertTrue(status["ok"])
        self.assertEqual(status["status"], "ahead")
        self.assertEqual(status["message"], "เครื่องนี้มี commit ใหม่กว่า GitHub หรือยังไม่ได้ push")

    def test_outdated_when_local_fallback_finds_local_ancestor(self) -> None:
        from app import main

        local = "a" * 40
        remote = "b" * 40

        def fake_git_read(_path: Path, *args: str, timeout: float = 4.0):
            completed = mock.Mock()
            completed.returncode = 0 if args == ("merge-base", "--is-ancestor", local, remote) else 1
            completed.stdout = ""
            completed.stderr = ""
            return completed

        with (
            mock.patch.object(main, "_git_value", self._fake_git_value(local, remote)),
            mock.patch.object(main, "_github_compare", return_value=("unknown", "api unavailable")),
            mock.patch.object(main, "_git_read", fake_git_read),
        ):
            status = main._version_status(Path("/repo"))

        self.assertFalse(status["ok"])
        self.assertEqual(status["status"], "outdated")
        self.assertEqual(status["relation"], "ahead")

    def test_update_blocks_dirty_worktree(self) -> None:
        from app import main

        before = {
            "ok": False,
            "status": "outdated",
            "relation": "ahead",
            "message": "old",
            "checkedAt": 1,
            "workspacePath": "/repo",
            "dirty": True,
        }
        with mock.patch.object(main, "_version_status", return_value=before):
            result = main._version_update(Path("/repo"))

        self.assertFalse(result["ok"])
        self.assertEqual(result["status"], "blocked")
        self.assertEqual(result["error"], "working tree is dirty")

    def test_update_fast_forwards_and_schedules_restart(self) -> None:
        from app import main

        before = {
            "ok": False,
            "status": "outdated",
            "relation": "ahead",
            "message": "old",
            "checkedAt": 1,
            "workspacePath": "/repo",
            "remote": "origin",
            "remoteRef": "refs/heads/main",
            "dirty": False,
        }
        after = {
            "ok": True,
            "status": "current",
            "relation": "identical",
            "message": "current",
            "checkedAt": 2,
            "workspacePath": "/repo",
            "dirty": False,
        }
        calls: list[tuple[str, ...]] = []

        def fake_git_read(_path: Path, *args: str, timeout: float = 4.0):
            calls.append(args)
            completed = mock.Mock()
            completed.returncode = 0
            completed.stdout = "ok"
            completed.stderr = ""
            return completed

        with (
            mock.patch.object(main, "_version_status", side_effect=[before, after]),
            mock.patch.object(main, "_run_pre_update_backup", return_value={"ok": True, "backend": "sqlite"}),
            mock.patch.object(main, "_run_update_migrations", return_value={"ok": True, "schema": {}, "dataMigrations": {}}),
            mock.patch.object(main, "_git_read", fake_git_read),
            mock.patch.object(
                main,
                "_schedule_version_restart",
                return_value={"scheduled": True, "mode": "screen", "logPath": "/repo/system/logs/self-update-restart.log"},
            ),
        ):
            result = main._version_update(Path("/repo"))

        self.assertTrue(result["ok"])
        self.assertEqual(result["status"], "updated")
        self.assertEqual(result["backup"], {"ok": True, "backend": "sqlite"})
        self.assertTrue(result["migrations"]["ok"])
        self.assertTrue(result["restartScheduled"])
        self.assertIn(("fetch", "--prune", "origin", "main"), calls)
        self.assertIn(("merge", "--ff-only", "FETCH_HEAD"), calls)

    def test_windows_update_restart_uses_native_powershell_launcher(self) -> None:
        from app import main

        with TemporaryDirectory() as tmp:
            repo = Path(tmp)
            (repo / "atrium.ps1").write_text("param($Command)\n", encoding="utf-8")
            with (
                mock.patch.object(main.sys, "platform", "win32"),
                mock.patch.object(main, "_powershell_command", return_value="powershell.exe"),
                mock.patch.object(main.subprocess, "Popen") as popen,
            ):
                result = main._schedule_version_restart(repo)

        self.assertTrue(result["scheduled"])
        self.assertEqual(result["mode"], "windows_native")
        command = popen.call_args.args[0]
        self.assertEqual(command[:4], ["powershell.exe", "-NoProfile", "-ExecutionPolicy", "Bypass"])
        self.assertIn(".\\atrium.ps1 restart --force", command[-1])
        self.assertIn("self-update-restart.log", command[-1])

    def test_update_stops_before_fetch_when_backup_fails(self) -> None:
        from app import main

        before = {
            "ok": False,
            "status": "outdated",
            "relation": "ahead",
            "message": "old",
            "checkedAt": 1,
            "workspacePath": "/repo",
            "remote": "origin",
            "remoteRef": "refs/heads/main",
            "dirty": False,
        }
        with (
            mock.patch.object(main, "_version_status", return_value=before),
            mock.patch.object(main, "_run_pre_update_backup", return_value={"ok": False, "error": "backup failed"}),
            mock.patch.object(main, "_git_read") as git_read,
        ):
            result = main._version_update(Path("/repo"))

        self.assertFalse(result["ok"])
        self.assertEqual(result["status"], "failed")
        self.assertEqual(result["backup"]["error"], "backup failed")
        git_read.assert_not_called()


if __name__ == "__main__":
    unittest.main()
