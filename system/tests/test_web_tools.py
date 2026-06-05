import unittest
from unittest import mock

from app import web_tools


class WebSearchFallbackTest(unittest.TestCase):
    def setUp(self) -> None:
        web_tools.SEARCH_CACHE.clear()

    def test_auto_search_falls_back_to_bing_when_duckduckgo_is_challenged(self) -> None:
        bing_payload = {
            "ok": True,
            "provider": "bing",
            "query": "robotics stocks",
            "count": 1,
            "tookMs": 1,
            "results": [{"title": "Example", "url": "https://example.com", "description": ""}],
            "externalContent": {"untrusted": True, "source": "web.search", "provider": "bing"},
        }

        with (
            mock.patch.object(web_tools, "_duckduckgo_search", side_effect=ValueError("DuckDuckGo returned a bot-detection challenge")),
            mock.patch.object(web_tools, "_bing_search", return_value=bing_payload),
        ):
            result = web_tools.execute_web_search({"query": "robotics stocks", "count": 1})

        self.assertEqual(result["provider"], "bing")
        self.assertEqual(result["count"], 1)
        self.assertEqual(result["fallbackErrors"], ["duckduckgo: DuckDuckGo returned a bot-detection challenge"])

    def test_bing_provider_parses_rss_results(self) -> None:
        rss = b"""<?xml version="1.0" encoding="utf-8" ?>
<rss version="2.0">
  <channel>
    <item>
      <title>Example &amp; Result</title>
      <link>https://example.com/a</link>
      <description>Readable snippet</description>
    </item>
  </channel>
</rss>"""

        with mock.patch.object(
            web_tools,
            "_bounded_get",
            return_value={"status": 200, "headers": {}, "body": rss},
        ):
            result = web_tools.execute_web_search({"provider": "bing", "query": "example", "count": 3})

        self.assertEqual(result["provider"], "bing")
        self.assertEqual(result["count"], 1)
        self.assertEqual(result["results"][0]["title"], "Example & Result")
        self.assertEqual(result["results"][0]["url"], "https://example.com/a")
        self.assertEqual(result["results"][0]["engine"], "bing")


if __name__ == "__main__":
    unittest.main()
