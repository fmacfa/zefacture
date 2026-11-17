"""Exercise the optional MCP connector over stdio and the HTTP boundary."""

import asyncio
import json
import secrets
import sys
import tempfile
import threading
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from urllib.parse import parse_qs, urlsplit

from mcp import StdioServerParameters
from mcp.client import Client


async def check_tools(origin, key_file):
    script = Path(__file__).resolve().parents[1] / "tools/zefacture_mcp.py"
    config = StdioServerParameters(
        command=sys.executable,
        args=[str(script), "--url", origin, "--key-file", str(key_file)],
    )
    async with Client(config, mode="legacy", read_timeout_seconds=10) as client:
        tools = (await client.list_tools()).tools
        assert {tool.name for tool in tools} == {
            "synthese_facturation",
            "lister_factures",
            "lister_justificatifs",
            "consulter_justificatif",
        }
        assert all(tool.annotations.read_only_hint for tool in tools)
        result = await client.call_tool(
            "synthese_facturation",
            {
                "date_from": "2026-11-01",
                "date_to": "2026-11-30",
                "compare": "year",
            },
        )
        assert not result.is_error, result.content
        assert (
            result.structured_content["data"]["totals"][0]["sales_excluding_tax"]
            == "100.00"
        )


def main():
    key = secrets.token_urlsafe(32)

    class Handler(BaseHTTPRequestHandler):
        def log_message(self, *_args):
            pass

        def do_GET(self):
            route = urlsplit(self.path)
            if (
                route.path != "/api/v1/assistant/summary"
                or self.headers.get("Authorization") != "Bearer " + key
                or parse_qs(route.query)
                != {
                    "date_from": ["2026-11-01"],
                    "date_to": ["2026-11-30"],
                    "compare": ["year"],
                }
            ):
                self.send_error(403)
                return
            body = json.dumps(
                {"data": {"totals": [{"sales_excluding_tax": "100.00"}]}}
            ).encode()
            self.send_response(200)
            self.send_header("Content-Type", "application/json")
            self.send_header("Content-Length", str(len(body)))
            self.end_headers()
            self.wfile.write(body)

    with (
        tempfile.TemporaryDirectory() as temp,
        ThreadingHTTPServer(("127.0.0.1", 0), Handler) as http,
    ):
        key_file = Path(temp) / "key"
        key_file.write_text(key)
        key_file.chmod(0o600)
        server = threading.Thread(target=http.serve_forever, daemon=True)
        server.start()
        try:
            asyncio.run(check_tools(f"http://127.0.0.1:{http.server_port}", key_file))
        finally:
            http.shutdown()
            server.join()
    print(
        "MCP stdio: tool discovery, read-only annotations and authenticated API request passed."
    )


if __name__ == "__main__":
    main()
