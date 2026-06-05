from http.server import BaseHTTPRequestHandler, HTTPServer
import threading
import unittest
from unittest import mock

from app import main
from app import web_tools
from app.web_tools import execute_web_fetch


class _Handler(BaseHTTPRequestHandler):
    def do_GET(self) -> None:  # noqa: N802
        body = b"<html><title>Local</title><body>private host fetch test</body></html>"
        self.send_response(200)
        self.send_header("Content-Type", "text/html; charset=utf-8")
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    def log_message(self, *_args) -> None:
        return None


class _LocalServer:
    def __enter__(self) -> str:
        self.server = HTTPServer(("127.0.0.1", 0), _Handler)
        self.thread = threading.Thread(target=self.server.serve_forever, daemon=True)
        self.thread.start()
        host, port = self.server.server_address
        return f"http://{host}:{port}/page"

    def __exit__(self, *_exc) -> None:
        self.server.shutdown()
        self.server.server_close()
        self.thread.join(timeout=2)


class WebFetchPrivateVisibilityTest(unittest.TestCase):
    def test_private_fetch_without_allow_private_hosts_still_blocks(self) -> None:
        with _LocalServer() as url:
            with self.assertRaisesRegex(ValueError, "blocks localhost"):
                execute_web_fetch({"url": url})

    def test_private_fetch_with_allow_private_hosts_returns_visibility_metadata(self) -> None:
        with _LocalServer() as url:
            result = execute_web_fetch({"url": url, "allowPrivateHosts": True, "maxChars": 1000})

        self.assertTrue(result["ok"])
        self.assertIn("private host fetch test", result["text"])
        network = result["networkAudit"]
        self.assertTrue(network["allowPrivateHosts"])
        self.assertTrue(network["privateNetwork"])
        self.assertTrue(network["privateAccessAllowed"])
        self.assertEqual(network["finalHost"], "127.0.0.1")
        self.assertIn("127.0.0.1", network["resolvedPrivateAddresses"])
        self.assertTrue(network["visibilityOnly"])
        self.assertTrue(result["externalContent"]["privateNetwork"])

    def test_fetch_records_post_fetch_dns_reresolve_visibility(self) -> None:
        resolves = [
            (["93.184.216.34"], None),
            (["127.0.0.1"], None),
        ]

        def fake_resolve(_host):
            return resolves.pop(0) if resolves else (["127.0.0.1"], None)

        with _LocalServer() as url:
            with mock.patch.object(web_tools, "_resolve_host_addresses", side_effect=fake_resolve):
                result = execute_web_fetch({"url": url, "allowPrivateHosts": True, "maxChars": 1000})

        network = result["networkAudit"]
        self.assertTrue(network["privateNetwork"])
        self.assertEqual(network["resolvedAddresses"], ["93.184.216.34"])
        self.assertEqual(network["postFetchResolvedAddresses"], ["127.0.0.1"])
        self.assertEqual(network["postFetchResolvedPrivateAddresses"], ["127.0.0.1"])
        dns = network["dnsReResolve"]
        self.assertTrue(dns["visibilityOnly"])
        self.assertTrue(dns["changed"])
        self.assertEqual(dns["addedAddresses"], ["127.0.0.1"])
        self.assertEqual(dns["removedAddresses"], ["93.184.216.34"])
        self.assertTrue(dns["postFetchPrivateNetwork"])

    def test_private_fetch_audit_event_is_visibility_only(self) -> None:
        run = {
            "id": "tool_1",
            "tool": "web.fetch",
            "departmentId": "exec",
            "threadId": "executive",
            "status": "succeeded",
            "args": {"url": "http://127.0.0.1:8787/health", "allowPrivateHosts": True},
            "result": {
                "url": "http://127.0.0.1:8787/health",
                "networkAudit": {
                    "allowPrivateHosts": True,
                    "privateNetwork": True,
                    "privateAccessAllowed": True,
                    "finalHost": "127.0.0.1",
                    "resolvedPrivateAddresses": ["127.0.0.1"],
                    "visibilityOnly": True,
                },
            },
        }

        event = main._web_fetch_private_audit_event(run)

        self.assertIsNotNone(event)
        assert event is not None
        self.assertIn("web.fetch private-host visibility", event["text"])
        self.assertTrue(event["privateNetwork"])
        self.assertTrue(event["allowPrivateHosts"])
        self.assertTrue(event["visibilityOnly"])
        self.assertEqual(event["resolvedPrivateAddresses"], ["127.0.0.1"])

    def test_public_fetch_does_not_create_private_audit_event(self) -> None:
        event = main._web_fetch_private_audit_event({
            "id": "tool_2",
            "tool": "web.fetch",
            "args": {"url": "https://example.com"},
            "result": {"networkAudit": {"privateNetwork": False}},
        })

        self.assertIsNone(event)


if __name__ == "__main__":
    unittest.main()
