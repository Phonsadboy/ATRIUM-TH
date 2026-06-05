import tempfile
import unittest
from pathlib import Path
from types import SimpleNamespace
from unittest import mock

import httpx

from app import audio_transcription
from app.audio_transcription import AudioTranscriptionResult, execute_audio_transcription_tool
from app.db.repo import TOOL_CATALOG


class FakeRepo:
    def __init__(self) -> None:
        self.entities: dict[tuple[str, str], dict] = {}
        self.knowledge: list[tuple[str, dict, str | None]] = []
        self.activities: list[dict] = []

    async def get_entity(self, etype: str, eid: str) -> dict | None:
        return self.entities.get((etype, eid))

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
        self.entities[(etype, obj["id"])] = obj
        return obj

    async def add_knowledge(self, dept_id: str, item: dict, *, source: str | None = None) -> None:
        self.knowledge.append((dept_id, item, source))

    async def add_activity(self, ev: dict) -> None:
        self.activities.append(ev)


def _settings(tmp: str) -> SimpleNamespace:
    return SimpleNamespace(
        object_store_enabled=False,
        workspace_dir=Path(tmp) / "workspace",
        audio_transcription_enabled=True,
        audio_transcription_max_bytes=1024 * 1024,
        audio_transcription_provider="openai",
        audio_transcription_model="gpt-4o-transcribe",
        audio_transcription_base_url="https://audio.test/v1",
        audio_transcription_timeout_s=5.0,
        audio_transcription_retry_attempts=3,
        audio_transcription_retry_delay_s=0.0,
        audio_transcription_auth_order="api_key",
        audio_transcription_language="",
        audio_transcription_prompt="",
        openai_api_key="sk-test",
        openai_base_url="",
    )


def _transcription(text: str = "สวัสดีจากไฟล์เสียง") -> AudioTranscriptionResult:
    return AudioTranscriptionResult(
        text=text,
        model="gpt-4o-transcribe",
        provider="openai",
        source="ATRIUM_OPENAI_API_KEY",
        mime="audio/wav",
        filename="voice.wav",
    )


class AudioTranscriptionToolTest(unittest.IsolatedAsyncioTestCase):
    async def test_audio_transcribe_source_path_persists_transcript_artifact(self) -> None:
        with tempfile.TemporaryDirectory(prefix="atrium-audio-tool-") as tmp:
            source = Path(tmp) / "voice.wav"
            source.write_bytes(b"RIFFfake-wave")
            repo = FakeRepo()

            with (
                mock.patch.object(audio_transcription, "get_settings", return_value=_settings(tmp)),
                mock.patch.object(audio_transcription, "transcribe_audio_bytes", return_value=_transcription()),
            ):
                result = await execute_audio_transcription_tool(
                    repo,
                    {
                        "tool": "audio.transcribe",
                        "departmentId": "exec",
                        "requestedBy": "tester",
                        "args": {"sourcePath": str(source), "ownerDept": "exec"},
                    },
                )

            artifact = result["artifact"]
            self.assertTrue(result["ok"])
            self.assertEqual(result["tool"], "audio.transcribe")
            self.assertIn("สวัสดีจากไฟล์เสียง", result["text"])
            self.assertEqual(artifact["kind"], "report")
            self.assertEqual(artifact["ownerDept"], "exec")
            self.assertEqual(artifact["storage"], "filesystem")
            self.assertTrue(Path(artifact["uri"]).is_file())
            self.assertIn("Audio transcript", Path(artifact["uri"]).read_text(encoding="utf-8"))
            self.assertIn(("artifact", artifact["id"]), repo.entities)
            self.assertEqual(repo.knowledge[0][0], "exec")

    async def test_audio_transcribe_artifact_updates_source_context(self) -> None:
        with tempfile.TemporaryDirectory(prefix="atrium-audio-tool-") as tmp:
            source = Path(tmp) / "note.m4a"
            source.write_bytes(b"fake-m4a")
            repo = FakeRepo()
            repo.entities[("artifact", "art_audio")] = {
                "id": "art_audio",
                "name": "note.m4a",
                "kind": "file",
                "mime": "audio/mp4",
                "ownerDept": "exec",
                "projectId": None,
                "status": "approved",
                "uri": str(source),
                "tags": ["upload"],
            }

            with (
                mock.patch.object(audio_transcription, "get_settings", return_value=_settings(tmp)),
                mock.patch.object(audio_transcription, "transcribe_audio_bytes", return_value=_transcription("ถอดความแล้ว")),
            ):
                result = await execute_audio_transcription_tool(
                    repo,
                    {
                        "tool": "audio.transcribe",
                        "departmentId": "exec",
                        "requestedBy": "tester",
                        "args": {"artifactId": "art_audio"},
                    },
                )

            source_artifact = repo.entities[("artifact", "art_audio")]
            self.assertEqual(result["sourceArtifactId"], "art_audio")
            self.assertEqual(source_artifact["audioTranscription"]["status"], "succeeded")
            self.assertEqual(source_artifact["audioTranscription"]["text"], "ถอดความแล้ว")
            self.assertEqual(source_artifact["transcriptArtifactId"], result["artifact"]["id"])
            self.assertEqual(source_artifact["extraction"]["status"], "audio_transcription")
            self.assertIn("transcribed", source_artifact["tags"])

    def test_audio_transcribe_is_registered_as_external_credential_tool(self) -> None:
        row = next(item for item in TOOL_CATALOG if item["tool"] == "audio.transcribe")

        self.assertEqual(row["riskClass"], "external_send")
        self.assertTrue(row["mutatesState"])
        self.assertTrue(row["externalSystem"])
        self.assertTrue(row["canUseCredentials"])
        self.assertIn("artifactId", row["inputSchema"])

    async def test_transcribe_audio_bytes_retries_transient_http_error(self) -> None:
        with tempfile.TemporaryDirectory(prefix="atrium-audio-retry-") as tmp:
            request = httpx.Request("POST", "https://audio.test/v1/audio/transcriptions")
            responses = [
                httpx.Response(500, request=request, text="server unavailable"),
                httpx.Response(200, request=request, json={"text": "retry succeeded", "duration": 1.25}),
            ]
            calls: list[dict] = []

            class FakeAsyncClient:
                def __init__(self, timeout):
                    self.timeout = timeout

                async def __aenter__(self):
                    return self

                async def __aexit__(self, *_exc):
                    return None

                async def post(self, url, **kwargs):
                    calls.append({"url": url, **kwargs})
                    return responses.pop(0)

            with mock.patch.object(audio_transcription.httpx, "AsyncClient", FakeAsyncClient):
                result = await audio_transcription.transcribe_audio_bytes(
                    b"RIFFfake-wave",
                    filename="voice.wav",
                    mime="audio/wav",
                    settings=_settings(tmp),
                )

        self.assertEqual(result.text, "retry succeeded")
        self.assertEqual(len(calls), 2)
        self.assertEqual(result.raw["atriumRetry"]["attempts"], 2)
        self.assertEqual(result.raw["atriumRetry"]["transientErrors"][0]["statusCode"], 500)

    async def test_transcribe_audio_bytes_does_not_retry_non_transient_http_error(self) -> None:
        with tempfile.TemporaryDirectory(prefix="atrium-audio-no-retry-") as tmp:
            request = httpx.Request("POST", "https://audio.test/v1/audio/transcriptions")
            responses = [httpx.Response(400, request=request, text="bad request")]
            calls: list[str] = []

            class FakeAsyncClient:
                def __init__(self, timeout):
                    self.timeout = timeout

                async def __aenter__(self):
                    return self

                async def __aexit__(self, *_exc):
                    return None

                async def post(self, url, **kwargs):
                    del kwargs
                    calls.append(url)
                    return responses.pop(0)

            with mock.patch.object(audio_transcription.httpx, "AsyncClient", FakeAsyncClient):
                with self.assertRaisesRegex(audio_transcription.AudioTranscriptionError, "HTTP 400"):
                    await audio_transcription.transcribe_audio_bytes(
                        b"RIFFfake-wave",
                        filename="voice.wav",
                        mime="audio/wav",
                        settings=_settings(tmp),
                    )

        self.assertEqual(len(calls), 1)


if __name__ == "__main__":
    unittest.main()
