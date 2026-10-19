"""Single-user development gateway and deterministic, non-streaming model server."""
import argparse
import http.client
import json
import os
import threading
import time
import uuid
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path

FIXTURES = {row["prompt"]: row["answer"] for row in
            json.loads(Path(__file__).with_name("fixtures.json").read_text())}
HOP_HEADERS = {"connection", "keep-alive", "proxy-authenticate", "proxy-authorization",
               "te", "trailer", "transfer-encoding", "upgrade", "host", "content-length"}


class Handler(BaseHTTPRequestHandler):
    journal = []
    lock = threading.Lock()

    def log_message(self, fmt, *args):
        # No URLs, payloads, headers or credentials in access logs.
        print(json.dumps({"service": self.server.mode, "event": "http"}), flush=True)

    def reply(self, status, data):
        body = json.dumps(data).encode()
        self.send_response(status)
        self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    def dispatch(self):
        self.connection.settimeout(35)
        try:
            if self.headers.get("Transfer-Encoding"):
                return self.reply(400, {"error": "chunked requests unsupported"})
            length = int(self.headers.get("Content-Length", "0"))
            if not 0 <= length <= 10 * 1024 * 1024:
                return self.reply(413, {"error": "request too large"})
            body = self.rfile.read(length)
            if self.server.mode == "gateway":
                return self.gateway(body)
            return self.mock(body)
        except (ValueError, KeyError, TypeError, IndexError, StopIteration):
            self.reply(400, {"error": "invalid request"})
        except (OSError, http.client.HTTPException):
            self.reply(502, {"error": "upstream unavailable"})

    def gateway(self, body):
        blocked = HOP_HEADERS | {"x-tenant", "x-user"}
        blocked |= {item.strip().lower() for item in self.headers.get("Connection", "").split(",")}
        headers = {k: v for k, v in self.headers.items() if k.lower() not in blocked}
        headers.update({"X-Tenant": os.environ["TENANT"], "X-User": "local-developer"})
        conn = http.client.HTTPConnection("127.0.0.1", 8080, timeout=35)
        try:
            conn.request(self.command, self.path, body=body, headers=headers)
            response = conn.getresponse()
            self.send_response(response.status)
            response_blocked = HOP_HEADERS | {
                item.strip().lower() for item in response.getheader("Connection", "").split(",")}
            for key, value in response.getheaders():
                if key.lower() not in response_blocked:
                    self.send_header(key, value)
            self.send_header("Connection", "close")
            self.end_headers()
            while chunk := response.read(65536):
                self.wfile.write(chunk)
        finally:
            conn.close()

    def mock(self, body):
        if self.command == "GET" and self.path in ("/health", "/v1", "/v1/"):
            return self.reply(200, {"status": "ok"})
        if self.command == "GET" and self.path == "/v1/models":
            return self.reply(200, {"object": "list", "data": [
                {"id": "local-mock", "object": "model", "created": 0, "owned_by": "local"}]})
        if self.command == "GET" and self.path == "/journal":
            with self.lock:
                data = list(self.journal)
            return self.reply(200, {"count": len(data), "requests": data})
        if self.command == "POST" and self.path == "/reset":
            with self.lock:
                self.journal.clear()
            return self.reply(200, {"count": 0})
        if self.command != "POST" or self.path != "/v1/chat/completions":
            return self.reply(404, {"error": "unsupported route"})
        request = json.loads(body)
        if request.get("stream"):
            return self.reply(400, {"error": "streaming unsupported; use stream=false"})
        prompt = next(m["content"] for m in reversed(request["messages"]) if m["role"] == "user")
        if prompt not in FIXTURES:
            return self.reply(400, {"error": "unknown fixture prompt"})
        request_id = "chatcmpl-" + uuid.uuid4().hex
        with self.lock:
            self.journal.append({"id": request_id, "prompt": prompt,
                                 "model": request.get("model"), "time": int(time.time())})
        self.reply(200, {
            "id": request_id, "object": "chat.completion", "created": int(time.time()),
            "model": request.get("model", "local-mock"),
            "choices": [{"index": 0, "message": {"role": "assistant", "content": FIXTURES[prompt]},
                         "finish_reason": "stop"}],
            "usage": {"prompt_tokens": 20, "completion_tokens": 1, "total_tokens": 21},
        })

    do_GET = do_POST = do_DELETE = do_PATCH = do_PUT = do_OPTIONS = do_HEAD = dispatch


def serve(mode, port):
    server = ThreadingHTTPServer(("0.0.0.0", port), Handler)
    server.mode = mode
    server.serve_forever()


if __name__ == "__main__":
    parser = argparse.ArgumentParser()
    parser.add_argument("mode", choices=["gateway", "mock"])
    args = parser.parse_args()
    serve(args.mode, 8088 if args.mode == "gateway" else 8000)
