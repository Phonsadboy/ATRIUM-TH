from pathlib import Path
import subprocess
from types import SimpleNamespace
import tempfile
import unittest
from unittest import mock

from app import video_editing


class FakeRepo:
    def __init__(self) -> None:
        self.entities: dict[tuple[str, str], dict] = {}
        self.jobs: dict[str, dict] = {}
        self.activities: list[dict] = []

    async def get_entity(self, etype: str, eid: str) -> dict | None:
        value = self.entities.get((etype, eid))
        return dict(value) if isinstance(value, dict) else None

    async def put_entity(
        self,
        etype: str,
        obj: dict,
        *,
        dept: str | None = None,
        project: str | None = None,
        status: str | None = None,
        ts: int | None = None,
    ) -> dict:
        del dept, project, status, ts
        self.entities[(etype, obj["id"])] = dict(obj)
        return obj

    async def get_job(self, job_id: str) -> dict | None:
        value = self.jobs.get(job_id)
        return dict(value) if isinstance(value, dict) else None

    async def mark_job(self, job_id: str, status: str, error: str | None = None, run_after: int | None = None) -> None:
        del run_after
        row = self.jobs.setdefault(job_id, {"id": job_id})
        if row.get("status") == "cancelled" and status != "cancelled":
            return
        row["status"] = status
        if error is not None:
            row["lastError"] = error

    async def add_activity(self, ev: dict) -> None:
        self.activities.append(ev)


class VideoJobCancellationTest(unittest.IsolatedAsyncioTestCase):
    def tearDown(self) -> None:
        with video_editing._VIDEO_RUNTIME_LOCK:
            video_editing._VIDEO_CANCEL_EVENTS.clear()
            video_editing._VIDEO_PROCESSES.clear()

    def test_run_terminates_active_process_when_cancel_event_is_set(self) -> None:
        job_id = "job_cancel_run"
        cancel_event = video_editing._register_video_job_runtime(job_id)
        token = video_editing._CURRENT_VIDEO_JOB_ID.set(job_id)
        signals: list[int] = []

        class FakeProcess:
            pid = 12345
            returncode = None
            communicate_calls = 0

            def poll(self):
                return self.returncode

            def communicate(self, timeout=None):
                self.communicate_calls += 1
                if self.communicate_calls == 1:
                    cancel_event.set()
                    raise subprocess.TimeoutExpired(["slow"], timeout=timeout)
                self.returncode = -15
                return "", "terminated"

        proc = FakeProcess()

        def fake_killpg(pid, sig):
            self.assertEqual(pid, proc.pid)
            signals.append(sig)
            proc.returncode = -15

        try:
            with (
                mock.patch.object(video_editing.subprocess, "Popen", return_value=proc),
                mock.patch.object(video_editing.os, "killpg", side_effect=fake_killpg),
            ):
                result = video_editing._run(["slow"], timeout=10)
        finally:
            video_editing._CURRENT_VIDEO_JOB_ID.reset(token)
            video_editing._unregister_video_job_runtime(job_id, cancel_event)

        self.assertEqual(result["returnCode"], 130)
        self.assertIn("terminated", result["stderr"])
        self.assertIn(video_editing.signal.SIGTERM, signals)

    async def test_process_video_job_preserves_cancelled_record_after_render_returns(self) -> None:
        repo = FakeRepo()
        job_id = "job_video_cancelled"
        repo.entities[("video_job", job_id)] = {
            "id": job_id,
            "kind": "video_tool",
            "tool": "video.render_edit",
            "status": "queued",
            "projectId": "vidproj_1",
            "ownerDept": "dept_video",
            "logs": [],
            "events": [],
        }
        repo.jobs[job_id] = {"id": job_id, "status": "cancelled"}
        payload = {
            "jobId": job_id,
            "tool": "video.render_edit",
            "departmentId": "dept_video",
            "projectId": "vidproj_1",
            "args": {},
        }

        async def fake_render(repo_arg, run, args):
            del repo_arg, run, args
            current = repo.entities[("video_job", job_id)]
            current["status"] = "cancelled"
            current["error"] = "cancelled by test"
            repo.entities[("video_job", job_id)] = current
            return {"ok": True, "render": {"id": "render_1"}}

        with mock.patch.object(video_editing, "_render_edit", side_effect=fake_render):
            with self.assertRaises(video_editing.VideoJobCancelledError):
                await video_editing.process_video_job(repo, payload, now=1)

        record = repo.entities[("video_job", job_id)]
        self.assertEqual(record["status"], "cancelled")
        self.assertNotIn("result", record)
        self.assertEqual(repo.jobs[job_id]["status"], "cancelled")

    def test_cleanup_video_render_intermediates_preserves_outputs_and_unrelated_files(self) -> None:
        with tempfile.TemporaryDirectory(prefix="atrium-video-cleanup-") as tmp:
            render_dir = Path(tmp)
            final = render_dir / "render.mp4"
            manifest = render_dir / "manifest.json"
            unrelated = render_dir / "notes.txt"
            for path in (
                final,
                manifest,
                unrelated,
                render_dir / "segment_000.mp4",
                render_dir / "segment_001.mp4",
                render_dir / "base.mp4",
                render_dir / "concat.txt",
                render_dir / "overlays.mp4",
                render_dir / "text.mp4",
                render_dir / "audio.mp4",
                render_dir / "text_layer_001.png",
            ):
                path.write_bytes(b"x" * 3)

            summary = video_editing._cleanup_video_render_intermediates(
                render_dir,
                preserve_paths={final, manifest},
            )

            self.assertTrue(summary["ok"])
            self.assertEqual(summary["deletedCount"], 8)
            self.assertTrue(final.is_file())
            self.assertTrue(manifest.is_file())
            self.assertTrue(unrelated.is_file())
            self.assertFalse((render_dir / "segment_000.mp4").exists())
            self.assertFalse((render_dir / "audio.mp4").exists())

    async def test_render_edit_cleans_intermediates_after_persisting_artifact(self) -> None:
        repo = FakeRepo()
        with tempfile.TemporaryDirectory(prefix="atrium-video-render-edit-") as tmp:
            workspace = Path(tmp) / "workspace"
            settings = SimpleNamespace(workspace_dir=workspace, object_store_enabled=False)
            dept_id = "dept_video"
            project_id = "vidproj_cleanup"
            project_dir = workspace / dept_id / "video_projects" / project_id
            project_dir.mkdir(parents=True)
            project = {
                "id": project_id,
                "name": "Cleanup test",
                "ownerDept": dept_id,
                "workspace": str(project_dir),
                "assets": [],
                "timelines": [],
                "renders": [],
                "createdAt": 1,
                "updatedAt": 1,
                "version": 1,
            }
            (project_dir / "project.json").write_text(video_editing.json.dumps(project), encoding="utf-8")

            def fake_render_ffmpeg(project_arg, spec_arg, render_dir):
                del project_arg, spec_arg
                for name in (
                    "segment_000.mp4",
                    "segment_001.mp4",
                    "base.mp4",
                    "concat.txt",
                    "overlays.mp4",
                    "text.mp4",
                    "audio.mp4",
                    "text_layer_001.png",
                ):
                    (render_dir / name).write_bytes(b"temp")
                final = render_dir / "render.mp4"
                final.write_bytes(b"final video")
                return final

            with (
                mock.patch.object(video_editing, "get_settings", return_value=settings),
                mock.patch.object(video_editing, "_render_ffmpeg", side_effect=fake_render_ffmpeg),
            ):
                result = await video_editing._render_edit(
                    repo,
                    {"id": "tool_1", "tool": "video.render_edit", "departmentId": dept_id, "requestedBy": dept_id},
                    {"projectId": project_id, "timeline": {"id": "tl_cleanup", "clips": []}},
                )

            render = result["render"]
            render_dir = Path(render["manifestPath"]).parent
            self.assertTrue(result["ok"])
            self.assertTrue(Path(render["path"]).is_file())
            self.assertTrue(Path(render["manifestPath"]).is_file())
            self.assertEqual(render["cleanup"]["deletedCount"], 8)
            self.assertFalse((render_dir / "segment_000.mp4").exists())
            self.assertFalse((render_dir / "audio.mp4").exists())
            artifact = result["artifact"]
            self.assertTrue(Path(artifact["localPath"]).is_file())
            self.assertIn(("video_render", render["id"]), repo.entities)

    async def test_render_edit_records_host_path_audit_without_blocking(self) -> None:
        repo = FakeRepo()
        with tempfile.TemporaryDirectory(prefix="atrium-video-host-path-") as tmp:
            workspace = Path(tmp) / "workspace"
            settings = SimpleNamespace(workspace_dir=workspace, object_store_enabled=False)
            dept_id = "dept_video"
            project_id = "vidproj_host_path"
            project_dir = workspace / dept_id / "video_projects" / project_id
            project_dir.mkdir(parents=True)
            host_source = Path(tmp) / "external-source.mp4"
            host_source.write_bytes(b"external video")
            project = {
                "id": project_id,
                "name": "Host path audit test",
                "ownerDept": dept_id,
                "workspace": str(project_dir),
                "assets": [],
                "timelines": [],
                "renders": [],
                "createdAt": 1,
                "updatedAt": 1,
                "version": 1,
            }
            (project_dir / "project.json").write_text(video_editing.json.dumps(project), encoding="utf-8")

            def fake_render_ffmpeg(project_arg, spec_arg, render_dir):
                del project_arg, spec_arg
                final = render_dir / "render.mp4"
                final.write_bytes(b"final video")
                return final

            with (
                mock.patch.object(video_editing, "get_settings", return_value=settings),
                mock.patch.object(video_editing, "_render_ffmpeg", side_effect=fake_render_ffmpeg),
            ):
                result = await video_editing._render_edit(
                    repo,
                    {"id": "tool_1", "tool": "video.render_edit", "departmentId": dept_id, "requestedBy": dept_id},
                    {"projectId": project_id, "timeline": {"id": "tl_host_path", "clips": [{"id": "clip_host", "sourcePath": str(host_source)}]}},
                )

            render = result["render"]
            audit = render["hostPathAudit"]
            self.assertTrue(result["ok"])
            self.assertEqual(audit["hostPathCount"], 1)
            self.assertEqual(audit["hostPaths"][0]["path"], str(host_source.resolve()))
            self.assertEqual(audit["hostPaths"][0]["scope"], "host")
            self.assertEqual(render["pathUsage"][0]["role"], "clip")
            saved_project = video_editing.json.loads((project_dir / "project.json").read_text(encoding="utf-8"))
            last_audit = saved_project["auditTrail"][-1]
            self.assertEqual(last_audit["refs"]["hostPathCount"], 1)
            self.assertEqual(last_audit["paths"]["hostPaths"], [str(host_source.resolve())])
            self.assertTrue(Path(render["path"]).is_file())

    async def test_transcribe_uses_isolated_output_dir_per_run(self) -> None:
        repo = FakeRepo()
        with tempfile.TemporaryDirectory(prefix="atrium-video-transcribe-") as tmp:
            workspace = Path(tmp) / "workspace"
            settings = SimpleNamespace(workspace_dir=workspace, object_store_enabled=False)
            dept_id = "dept_video"
            project_id = "vidproj_transcribe"
            project_dir = workspace / dept_id / "video_projects" / project_id
            project_dir.mkdir(parents=True)
            source = Path(tmp) / "source.mp4"
            source.write_bytes(b"fake video")
            project = {
                "id": project_id,
                "name": "Transcribe test",
                "ownerDept": dept_id,
                "workspace": str(project_dir),
                "assets": [],
                "timelines": [],
                "renders": [],
                "createdAt": 1,
                "updatedAt": 1,
                "version": 1,
            }
            (project_dir / "project.json").write_text(video_editing.json.dumps(project), encoding="utf-8")

            def fake_which(name: str) -> str | None:
                return "/mock/whisper" if name == "whisper" else None

            def fake_run(command, *, cwd=None, timeout=120.0):
                del cwd, timeout
                if command[0] == "/mock/ffmpeg":
                    Path(command[-1]).write_bytes(b"fake wav")
                    return {"command": command, "returnCode": 0, "stdout": "", "stderr": ""}
                if command[0] == "/mock/whisper":
                    out_dir = Path(command[command.index("--output_dir") + 1])
                    (out_dir / "audio.json").write_text(
                        video_editing.json.dumps({"segments": [{"start": 0, "end": 1, "text": "hello"}]}),
                        encoding="utf-8",
                    )
                    return {"command": command, "returnCode": 0, "stdout": "", "stderr": ""}
                raise AssertionError(f"unexpected command: {command}")

            with (
                mock.patch.object(video_editing, "get_settings", return_value=settings),
                mock.patch.object(video_editing, "_require_ffmpeg", return_value="/mock/ffmpeg"),
                mock.patch.object(video_editing.shutil, "which", side_effect=fake_which),
                mock.patch.object(video_editing, "_run", side_effect=fake_run),
            ):
                first = await video_editing._transcribe(
                    repo,
                    {"id": "tool_1", "tool": "video.transcribe", "departmentId": dept_id, "requestedBy": dept_id},
                    {"projectId": project_id, "sourcePath": str(source)},
                )
                second = await video_editing._transcribe(
                    repo,
                    {"id": "tool_2", "tool": "video.transcribe", "departmentId": dept_id, "requestedBy": dept_id},
                    {"projectId": project_id, "sourcePath": str(source)},
                )

            first_dir = Path(first["path"]).parent
            second_dir = Path(second["path"]).parent
            shared_transcripts_dir = (project_dir / "transcripts").resolve()
            self.assertTrue(first["ok"])
            self.assertTrue(second["ok"])
            self.assertNotEqual(first_dir, second_dir)
            self.assertEqual(first_dir.parent.resolve(), shared_transcripts_dir)
            self.assertEqual(second_dir.parent.resolve(), shared_transcripts_dir)
            self.assertEqual(first["transcript"]["workDir"], str(first_dir))
            self.assertEqual(second["transcript"]["workDir"], str(second_dir))
            self.assertTrue(first["transcript"]["isolatedWorkDir"])
            self.assertTrue(second["transcript"]["isolatedWorkDir"])
            self.assertFalse((shared_transcripts_dir / "transcript.json").exists())
            self.assertTrue((first_dir / "transcript.json").is_file())
            self.assertTrue((second_dir / "transcript.json").is_file())
            saved_project = video_editing.json.loads((project_dir / "project.json").read_text(encoding="utf-8"))
            self.assertEqual(len(saved_project.get("transcripts") or []), 2)
            self.assertEqual(
                {Path(item["workDir"]) for item in saved_project["transcripts"]},
                {first_dir, second_dir},
            )
