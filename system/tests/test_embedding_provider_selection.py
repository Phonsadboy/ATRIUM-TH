import json
import math
import tempfile
import unittest
from pathlib import Path

import httpx

from app.config import Settings
from app.memory.embeddings import OpenAIEmbedder, resolve_embedder
from app.provider.env_settings import provider_env_allowed_keys, provider_env_settings


class EmbeddingProviderSelectionTest(unittest.IsolatedAsyncioTestCase):
    async def test_openai_embedder_uses_latest_model_dimensions_and_preserves_order(self) -> None:
        seen: dict[str, object] = {}

        def handler(request: httpx.Request) -> httpx.Response:
            seen["url"] = str(request.url)
            seen["authorization"] = request.headers.get("Authorization")
            seen["payload"] = json.loads(request.content.decode("utf-8"))
            return httpx.Response(
                200,
                json={
                    "data": [
                        {"index": 1, "embedding": [0.0, 2.0]},
                        {"index": 0, "embedding": [3.0, 4.0]},
                    ]
                },
            )

        embedder = OpenAIEmbedder(
            "sk-test-embedding",
            "text-embedding-3-large",
            "https://api.openai.test/v1",
            dimensions=1024,
            transport=httpx.MockTransport(handler),
        )

        vectors = await embedder.embed(["first", "second"])

        self.assertEqual(seen["url"], "https://api.openai.test/v1/embeddings")
        self.assertEqual(seen["authorization"], "Bearer sk-test-embedding")
        self.assertEqual(
            seen["payload"],
            {
                "input": ["first", "second"],
                "model": "text-embedding-3-large",
                "encoding_format": "float",
                "dimensions": 1024,
            },
        )
        self.assertEqual(len(vectors), 2)
        self.assertEqual(embedder.dim, 2)
        self.assertAlmostEqual(math.sqrt(sum(v * v for v in vectors[0])), 1.0)
        self.assertGreater(vectors[0][0], vectors[1][0])

    async def test_explicit_openai_mode_resolves_openai_embedder(self) -> None:
        settings = Settings(
            ATRIUM_EMBEDDING_PROVIDER="openai",
            ATRIUM_OPENAI_API_KEY="sk-test-openai",
            openai_embedding_model="text-embedding-3-large",
            openai_embedding_dimensions=1024,
        )

        embedder = await resolve_embedder(settings)

        self.assertIsInstance(embedder, OpenAIEmbedder)
        self.assertEqual(embedder.name, "openai:text-embedding-3-large")
        self.assertEqual(embedder.dim, 1024)

    def test_owner_env_settings_expose_embedding_mode_fields(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            env_path = Path(tmp) / ".env"
            settings = Settings(ATRIUM_EMBEDDING_PROVIDER="openai", openai_embedding_dimensions=1024)

            payload = provider_env_settings(settings, env_path=env_path)

        groups = {group["id"]: group for group in payload["groups"]}
        self.assertIn("embeddings", groups)
        fields = {field["key"]: field for field in groups["embeddings"]["fields"]}
        self.assertEqual(fields["ATRIUM_EMBEDDING_PROVIDER"]["kind"], "select")
        self.assertIn("openai", fields["ATRIUM_EMBEDDING_PROVIDER"]["options"])
        self.assertIn("ATRIUM_OPENAI_EMBEDDING_MODEL", fields)
        self.assertIn("ATRIUM_OPENAI_EMBEDDING_DIMENSIONS", fields)
        self.assertIn("ATRIUM_EMBEDDING_PROVIDER", provider_env_allowed_keys())


if __name__ == "__main__":
    unittest.main()
