"""Expose a fixed remote workspace through this computer's localhost origin.

Run with Python 3.11+: python3 workspace_gateway.py --port 1781
The upstream still owns all data; this process only streams HTTP requests.
"""

from __future__ import annotations

import argparse
import http.client
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
import os
from urllib.parse import urlsplit, urlunsplit


DEFAULT_UPSTREAM = "http://1.vm-ai.lev-maltsev.kc.idzn.ru:1681"
CHUNK_SIZE = 64 * 1024
MAX_REQUEST_SIZE = 210 * 1024 * 1024
HOP_HEADERS = {
    "connection", "keep-alive", "proxy-authenticate", "proxy-authorization",
    "te", "trailer", "transfer-encoding", "upgrade", "proxy-connection",
}


def parse_upstream(value: str):
    parsed = urlsplit(value)
    if (parsed.scheme not in {"http", "https"} or not parsed.hostname
            or parsed.username is not None or parsed.password is not None
            or parsed.path not in {"", "/"} or parsed.query or parsed.fragment
            or any(char.isspace() for char in value)):
        raise ValueError("Upstream должен быть HTTP(S)-адресом без пути и учётных данных")
    if parsed.port is not None and not 1 <= parsed.port <= 65535:
        raise ValueError("Некорректный порт upstream")
    return parsed


def blocked_headers(headers):
    blocked = set(HOP_HEADERS)
    for value in headers.get_all("Connection", []):
        blocked.update(part.strip().lower() for part in value.split(","))
    return blocked


class GatewayServer(ThreadingHTTPServer):
    daemon_threads = True
    allow_reuse_address = True

    def __init__(self, port: int = 1781, upstream: str = DEFAULT_UPSTREAM):
        self.upstream = parse_upstream(upstream)
        super().__init__(("127.0.0.1", port), GatewayHandler)


class GatewayHandler(BaseHTTPRequestHandler):
    # Closing each response also frames streamed upstream responses without length.
    protocol_version = "HTTP/1.0"
    server_version = "WorkspaceLocalGateway"

    def setup(self):
        super().setup()
        self.connection.settimeout(180)

    def log_message(self, *_):
        # Paths, query strings and request headers may contain private information.
        pass

    def parse_request(self):
        if not super().parse_request():
            return False
        target = self.requestline.split()[1]
        if not target.startswith("/") or target.startswith("//") or "\\" in target:
            self.send_error(400, "Only relative workspace paths are accepted")
            return False
        return True

    def _validate_request(self):
        hosts = self.headers.get_all("Host", [])
        port = self.server.server_port
        allowed = {f"localhost:{port}", f"127.0.0.1:{port}"}
        if len(hosts) != 1 or hosts[0].lower() not in allowed:
            self.send_error(403, "Use this gateway through localhost")
            return None
        if self.command not in {"GET", "HEAD"}:
            own_origin = f"http://{hosts[0].lower()}"
            origins = self.headers.get_all("Origin", [])
            if len(origins) > 1 or (origins and origins[0].lower() != own_origin):
                self.send_error(403, "Cross-origin writes are not accepted")
                return None
            referer = self.headers.get("Referer")
            if referer:
                try:
                    parsed = urlsplit(referer)
                    same_origin = f"{parsed.scheme}://{parsed.netloc}".lower() == own_origin
                except ValueError:
                    same_origin = False
                if not same_origin:
                    self.send_error(403, "Cross-origin writes are not accepted")
                    return None
            if self.headers.get("Sec-Fetch-Site") == "cross-site":
                self.send_error(403, "Cross-site writes are not accepted")
                return None
        if self.headers.get("Transfer-Encoding") is not None:
            self.send_error(501, "Chunked request uploads are not supported")
            return None
        lengths = self.headers.get_all("Content-Length", [])
        if len(lengths) > 1 or (lengths and not lengths[0].isascii()):
            self.send_error(400, "Invalid Content-Length")
            return None
        value = lengths[0] if lengths else "0"
        if not value.isdecimal():
            self.send_error(400, "Invalid Content-Length")
            return None
        if len(value) > 9:
            self.send_error(413, "Request too large")
            return None
        length = int(value)
        if length > MAX_REQUEST_SIZE:
            self.send_error(413, "Request too large")
            return None
        return length

    def _location(self, value: str):
        upstream = self.server.upstream
        default_port = lambda item: item.port or (443 if item.scheme == "https" else 80)
        try:
            redirect = urlsplit(value)
            same_origin = (redirect.scheme == upstream.scheme and redirect.hostname == upstream.hostname
                           and default_port(redirect) == default_port(upstream))
            same_host = not redirect.scheme and redirect.netloc.lower() == upstream.netloc.lower()
            if same_origin or same_host:
                return urlunsplit(("", "", redirect.path or "/", redirect.query, redirect.fragment))
        except ValueError:
            pass
        return value

    def _proxy(self):
        length = self._validate_request()
        if length is None:
            return
        upstream = self.server.upstream
        connection_type = http.client.HTTPSConnection if upstream.scheme == "https" else http.client.HTTPConnection
        connection = connection_type(upstream.hostname, upstream.port, timeout=180)
        response_started = False
        try:
            connection.putrequest(self.command, self.path, skip_host=True, skip_accept_encoding=True)
            connection.putheader("Host", upstream.netloc)
            blocked = blocked_headers(self.headers) | {"host", "expect", "content-length"}
            for name, value in self.headers.items():
                if name.lower() not in blocked:
                    connection.putheader(name, value)
            if self.headers.get("Content-Length") is not None:
                connection.putheader("Content-Length", str(length))
            connection.putheader("Connection", "close")
            connection.endheaders()
            remaining = length
            while remaining:
                chunk = self.rfile.read(min(CHUNK_SIZE, remaining))
                if not chunk:
                    raise ConnectionError("Incomplete upload")
                connection.send(chunk)
                remaining -= len(chunk)
            response = connection.getresponse()
            self.send_response_only(response.status)
            blocked = blocked_headers(response.headers)
            for name, value in response.getheaders():
                if name.lower() not in blocked:
                    self.send_header(name, self._location(value) if name.lower() == "location" else value)
            self.send_header("Connection", "close")
            self.end_headers()
            response_started = True
            if self.command != "HEAD":
                while chunk := response.read(CHUNK_SIZE):
                    self.wfile.write(chunk)
        except (OSError, http.client.HTTPException, ValueError):
            if not response_started:
                self.send_error(502, "Workspace server is unavailable")
        finally:
            connection.close()
            self.close_connection = True

    do_GET = do_HEAD = do_POST = do_PUT = do_PATCH = do_DELETE = _proxy


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--upstream", default=os.environ.get("WORKSPACE_UPSTREAM", DEFAULT_UPSTREAM))
    parser.add_argument("--port", type=int, default=int(os.environ.get("WORKSPACE_LOCAL_PORT", "1781")))
    args = parser.parse_args()
    if not 1 <= args.port <= 65535:
        parser.error("Порт должен быть от 1 до 65535")
    try:
        server = GatewayServer(args.port, args.upstream)
    except (ValueError, OSError) as error:
        parser.error(str(error))
    print(f"Локальный вход: http://localhost:{server.server_port}/efficiency/pomodoro", flush=True)
    print("Данные и файлы остаются на удалённом сервере. Ctrl+C — остановить.", flush=True)
    try:
        server.serve_forever()
    except KeyboardInterrupt:
        pass
    finally:
        server.server_close()


if __name__ == "__main__":
    main()
