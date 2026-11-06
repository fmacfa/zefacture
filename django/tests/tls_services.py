"""Local TLS fixtures; no third-party account or external message is used."""

import shutil
import socketserver
import ssl
import subprocess
import tempfile
import threading
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from unittest import SkipTest


class TlsServices:
    def __init__(self):
        if not shutil.which("openssl"):
            raise SkipTest("openssl is required by the local TLS integration fixtures")
        self.temp = tempfile.TemporaryDirectory()
        self.cert, self.key = (
            Path(self.temp.name) / "cert.pem",
            Path(self.temp.name) / "key.pem",
        )
        subprocess.run(
            [
                "openssl",
                "req",
                "-x509",
                "-newkey",
                "rsa:2048",
                "-nodes",
                "-keyout",
                str(self.key),
                "-out",
                str(self.cert),
                "-days",
                "1",
                "-subj",
                "/CN=localhost",
                "-addext",
                "subjectAltName=DNS:localhost,IP:127.0.0.1",
            ],
            check=True,
            capture_output=True,
        )
        self.context = ssl.SSLContext(ssl.PROTOCOL_TLS_SERVER)
        self.context.load_cert_chain(self.cert, self.key)
        self.trust = ssl.create_default_context(cafile=str(self.cert))
        self.smtp_messages, self.http_requests = [], []
        self.http_status = 200
        self.servers, self.threads = [], []

    def start(self, server):
        thread = threading.Thread(target=server.serve_forever, daemon=True)
        self.servers.append(server)
        self.threads.append(thread)
        thread.start()
        return server.server_address[1]

    def smtp(self):
        fixture = self

        class Handler(socketserver.StreamRequestHandler):
            def reply(self, raw):
                self.wfile.write(raw)
                self.wfile.flush()

            def handle(self):
                self.reply(b"220 localhost test SMTP\r\n")
                while line := self.rfile.readline():
                    command = line.split()[0].upper()
                    if command in {b"EHLO", b"HELO"}:
                        self.reply(
                            b"250-localhost\r\n250-STARTTLS\r\n250 AUTH PLAIN\r\n"
                        )
                    elif command == b"STARTTLS":
                        self.reply(b"220 Ready for TLS\r\n")
                        self.connection = fixture.context.wrap_socket(
                            self.connection, server_side=True
                        )
                        self.rfile, self.wfile = (
                            self.connection.makefile("rb"),
                            self.connection.makefile("wb"),
                        )
                    elif command == b"AUTH":
                        self.reply(b"235 Authenticated\r\n")
                    elif command == b"DATA":
                        self.reply(b"354 Send message\r\n")
                        lines = []
                        while (body := self.rfile.readline()) not in {b".\r\n", b""}:
                            lines.append(body)
                        fixture.smtp_messages.append(b"".join(lines))
                        self.reply(b"250 Accepted\r\n")
                    elif command == b"QUIT":
                        self.reply(b"221 Bye\r\n")
                        break
                    else:
                        self.reply(b"250 OK\r\n")

        class Server(socketserver.ThreadingTCPServer):
            allow_reuse_address = True
            daemon_threads = True

        return self.start(Server(("127.0.0.1", 0), Handler))

    def https(self):
        fixture = self

        class Handler(BaseHTTPRequestHandler):
            def do_POST(self):
                raw = self.rfile.read(int(self.headers.get("Content-Length", 0)))
                fixture.http_requests.append((self.path, dict(self.headers), raw))
                self.send_response(fixture.http_status)
                self.end_headers()
                self.wfile.write(b"{}")

            def log_message(self, *args):
                pass

        server = ThreadingHTTPServer(("127.0.0.1", 0), Handler)
        server.socket = self.context.wrap_socket(server.socket, server_side=True)
        return self.start(server)

    def close(self):
        for server in self.servers:
            server.shutdown()
            server.server_close()
        for thread in self.threads:
            thread.join(timeout=5)
        self.temp.cleanup()
