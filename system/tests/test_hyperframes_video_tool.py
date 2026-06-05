import copy
from pathlib import Path
from types import SimpleNamespace
import tempfile
import unittest
from unittest import mock


class FakeRepo:
    def __init__(self) -> None:
        self.entities: dict[tuple[str, str], dict] = {}

    async def put_entity(self, type_, data, **kwargs):
        self.entities[(type_, data["id"])] = copy.deepcopy(data)

    async def get_entity(self, type_, id_):
        return self.entities.get((type_, id_))


class HyperFramesVideoToolTest(unittest.IsolatedAsyncioTestCase):
    async def test_render_motion_creates_hyperframes_package(self) -> None:
        from app import video_editing

        with tempfile.TemporaryDirectory(prefix="atrium-hyperframes-") as tmp:
            workspace = Path(tmp) / "workspace"
            settings = SimpleNamespace(workspace_dir=workspace, object_store_enabled=False)
            dept_id = "dept_video"
            project_id = "vidproj_hyperframes"
            project_dir = workspace / dept_id / "video_projects" / project_id
            project_dir.mkdir(parents=True)
            project = {
                "id": project_id,
                "name": "HyperFrames smoke",
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
            repo = FakeRepo()
            timeline = {
                "id": "tl_hyperframes",
                "canvas": {"width": 1080, "height": 1920, "fps": 30},
                "clips": [],
                "text": [
                    {
                        "id": "txt_hook",
                        "text": "ทดสอบ HyperFrames",
                        "start": 0,
                        "end": 2.5,
                        "size": 72,
                        "position": {"x": "50%", "y": "22%", "anchor": "center"},
                        "animation": "fade-up",
                    }
                ],
                "export": {"filename": "hyperframes-preview.mp4"},
            }

            with mock.patch.object(video_editing, "get_settings", return_value=settings):
                result = await video_editing.execute_video_tool(
                    repo,
                    {
                        "id": "tool_1",
                        "tool": "video.render_motion",
                        "departmentId": dept_id,
                        "requestedBy": dept_id,
                        "args": {"projectId": project_id, "timeline": timeline, "renderer": "hyperframes"},
                    },
                )

            motion = result["motion"]
            package_dir = Path(motion["packageDir"])
            self.assertTrue(result["ok"])
            self.assertEqual(motion["renderer"], "hyperframes")
            self.assertEqual(motion["renderers"], ["hyperframes"])
            self.assertEqual(Path(motion["entryPoint"]).name, "index.html")
            self.assertIn("lint", motion["commands"])
            self.assertIn("render", motion["commands"])
            self.assertTrue((package_dir / "index.html").is_file())
            self.assertTrue((package_dir / "DESIGN.md").is_file())
            html = (package_dir / "index.html").read_text(encoding="utf-8")
            self.assertIn('data-composition-id="AtriumVideo"', html)
            self.assertIn("window.__timelines", html)
            self.assertIn("ทดสอบ HyperFrames", html)
            self.assertIn(("video_motion", motion["id"]), repo.entities)
            self.assertIn(("artifact", result["artifact"]["id"]), repo.entities)

    async def test_hyperframes_render_uses_cli_and_persists_artifact(self) -> None:
        from app import video_editing

        with tempfile.TemporaryDirectory(prefix="atrium-hyperframes-render-") as tmp:
            workspace = Path(tmp) / "workspace"
            package_dir = Path(tmp) / "package"
            (package_dir / "out").mkdir(parents=True)
            (package_dir / ".cache").mkdir(parents=True)
            (package_dir / ".cache" / "frame.tmp").write_bytes(b"cache")
            (package_dir / "out" / "stale.webm").write_bytes(b"stale")
            (package_dir / "renders").mkdir(parents=True)
            (package_dir / "renders" / "scratch.mp4").write_bytes(b"scratch")
            (package_dir / "node_modules" / "hyperframes").mkdir(parents=True)
            (package_dir / "node_modules" / "hyperframes" / "package.json").write_text("{}", encoding="utf-8")
            (package_dir / "index.html").write_text("<html></html>", encoding="utf-8")
            settings = SimpleNamespace(workspace_dir=workspace, object_store_enabled=False)
            repo = FakeRepo()
            project = {
                "id": "vidproj_hyperframes",
                "ownerDept": "dept_video",
                "renders": [],
            }
            manifest = {
                "id": "motion_hyperframes",
                "timelineId": "tl_hyperframes",
                "timelineVersion": 1,
            }

            def fake_run(command, *, cwd=None, timeout=120.0):
                if command[0] == "/mock/node":
                    return {"command": command, "returnCode": 0, "stdout": "v25.8.0\n", "stderr": ""}
                if command[-1] == "lint":
                    return {"command": command, "returnCode": 0, "stdout": "0 errors\n", "stderr": ""}
                if "render" in command:
                    output = Path(command[command.index("--output") + 1])
                    output.parent.mkdir(parents=True, exist_ok=True)
                    output.write_bytes(b"fake mp4")
                    return {"command": command, "returnCode": 0, "stdout": "rendered\n", "stderr": ""}
                return {"command": command, "returnCode": 0, "stdout": "", "stderr": ""}

            with (
                mock.patch.object(video_editing, "get_settings", return_value=settings),
                mock.patch.object(video_editing.shutil, "which", side_effect=lambda name: "/mock/node" if name == "node" else None),
                mock.patch.object(video_editing, "_require_ffmpeg", return_value="/mock/ffmpeg"),
                mock.patch.object(video_editing, "_hyperframes_cli_command", return_value=(["hyperframes"], None)),
                mock.patch.object(video_editing, "_run", side_effect=fake_run),
            ):
                render_result, artifact = await video_editing._maybe_render_hyperframes(
                    repo,
                    {"id": "tool_1", "departmentId": "dept_video", "requestedBy": "dept_video"},
                    project,
                    manifest,
                    package_dir,
                    {"outputName": "final.mp4"},
                )

            self.assertTrue(render_result["ok"])
            self.assertEqual(render_result["render"]["renderer"], "hyperframes")
            self.assertTrue((package_dir / "out" / "final.mp4").is_file())
            self.assertTrue((package_dir / "index.html").is_file())
            self.assertTrue((package_dir / "node_modules" / "hyperframes" / "package.json").is_file())
            self.assertFalse((package_dir / ".cache").exists())
            self.assertFalse((package_dir / "out" / "stale.webm").exists())
            self.assertFalse((package_dir / "renders" / "scratch.mp4").exists())
            self.assertGreaterEqual(render_result["render"]["cleanup"]["deletedCount"], 3)
            self.assertIsNotNone(artifact)
            self.assertIn(("video_render", render_result["render"]["id"]), repo.entities)
            self.assertIn(("artifact", artifact["id"]), repo.entities)

    async def test_render_motion_reports_requested_render_failure_as_not_ok(self) -> None:
        from app import video_editing

        with tempfile.TemporaryDirectory(prefix="atrium-motion-render-fail-") as tmp:
            workspace = Path(tmp) / "workspace"
            settings = SimpleNamespace(workspace_dir=workspace, object_store_enabled=False)
            dept_id = "dept_video"
            project_id = "vidproj_motion_fail"
            project_dir = workspace / dept_id / "video_projects" / project_id
            project_dir.mkdir(parents=True)
            project = {
                "id": project_id,
                "name": "Motion failure smoke",
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
            repo = FakeRepo()
            timeline = {
                "id": "tl_motion_fail",
                "canvas": {"width": 1080, "height": 1920, "fps": 30},
                "clips": [],
                "text": [{"id": "txt_fail", "text": "render should fail", "start": 0, "end": 1}],
            }

            async def fake_render(*args, **kwargs):
                del args, kwargs
                return {"ok": False, "status": "render_failed", "returnCode": 1, "reason": "mock render failure"}, None

            with (
                mock.patch.object(video_editing, "get_settings", return_value=settings),
                mock.patch.object(video_editing, "_maybe_render_hyperframes", side_effect=fake_render),
            ):
                result = await video_editing.execute_video_tool(
                    repo,
                    {
                        "id": "tool_1",
                        "tool": "video.render_motion",
                        "departmentId": dept_id,
                        "requestedBy": dept_id,
                        "args": {"projectId": project_id, "timeline": timeline, "renderer": "hyperframes", "render": True},
                    },
                )

            motion = result["motion"]
            self.assertFalse(result["ok"])
            self.assertTrue(result["packageOk"])
            self.assertIn("render_failed", result["error"])
            self.assertEqual(motion["status"], "render_failed")
            self.assertTrue(motion["renderRequired"])
            self.assertFalse(motion["partialRenderAllowed"])
            self.assertEqual(motion["renderFailure"]["status"], "render_failed")
            self.assertIn(("video_motion", motion["id"]), repo.entities)
            self.assertEqual(repo.entities[("video_motion", motion["id"])]["status"], "render_failed")

    def test_tool_catalog_mentions_hyperframes_renderer(self) -> None:
        from app.db.repo import TOOL_CATALOG

        row = next(item for item in TOOL_CATALOG if item["tool"] == "video.render_motion")
        self.assertIn("hyperframes", row["description"].lower())
        self.assertIn("hyperframes", row["inputSchema"]["renderer"])
