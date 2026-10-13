# -*- coding: utf-8 -*-
"""
叁月聚粮分身 · HTTP 路由层（前后端分离版）
- 业务逻辑全部在 api.py，本文件只做路由和静态文件
- 前端薄壳在 frontend/，本地依赖在 vendor/
用法：python web_server.py  然后浏览器打开 http://127.0.0.1:8778
"""
import json
import os
import sys
from io import BytesIO
from http.server import HTTPServer, BaseHTTPRequestHandler
from urllib.parse import urlparse, parse_qs, unquote

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

import api  # noqa: E402

FRONTEND = os.path.join(os.path.dirname(os.path.abspath(__file__)), "frontend")
VENDOR = os.path.join(os.path.dirname(os.path.abspath(__file__)), "vendor")
CTYPES = {".html": "text/html; charset=utf-8", ".js": "application/javascript; charset=utf-8",
          ".css": "text/css; charset=utf-8", ".png": "image/png", ".svg": "image/svg+xml"}

GET_ROUTES = {
    "/": lambda qs: (200, None, "chat.html"),
    "/index.html": lambda qs: (200, None, "chat.html"),
    "/admin": lambda qs: (200, None, "admin.html"),
    "/rag": lambda qs: (200, None, "rag.html"),
    "/star": lambda qs: (200, None, "star.html"),
    "/threads": lambda qs: (200, None, "threads.html"),
    "/api/stats": lambda qs: (200, api.stats(), None),
    "/api/system": lambda qs: (200, api.system(), None),
    "/api/sessions": lambda qs: (200, api.sessions_list(), None),
    "/api/categories": lambda qs: (200, api.categories(), None),
    "/api/history": lambda qs: (200, api.history(), None),
    "/api/memory": lambda qs: (200, api.memory_list(), None),
    "/api/files": lambda qs: (*api.file_read(unquote(qs.get("path", [""])[0])), None),
}


class _Headers(dict):
    """大小写不敏感的请求头，供 WSGI 适配器使用"""
    def get(self, key, default=None):
        return super().get(key.lower(), default)


def wsgi_app(environ, start_response):
    """把现有 Handler 适配成 WSGI 应用，供 waitress 等生产服务器直接使用"""
    outer_start = start_response
    handler = object.__new__(Handler)
    path = environ.get("PATH_INFO", "/")
    query = environ.get("QUERY_STRING", "")
    handler.path = f"{path}?{query}" if query else path
    handler.command = environ.get("REQUEST_METHOD", "GET")
    handler.request_version = environ.get("SERVER_PROTOCOL", "HTTP/1.1")
    handler.headers = _Headers({k.lower().replace("_", "-"): v
                                for k, v in environ.items() if k.startswith("HTTP_")})
    if environ.get("CONTENT_LENGTH"):
        handler.headers["content-length"] = environ["CONTENT_LENGTH"]
    length = int(environ.get("CONTENT_LENGTH") or 0)
    handler.rfile = BytesIO(environ["wsgi.input"].read(length) if length else b"")
    state = {"status": "", "headers": [], "body": BytesIO(), "started": False}

    def _start(status, response_headers, exc_info=None):
        if not state["started"]:
            state["started"] = True
            outer_start(status, response_headers, exc_info)

    import http
    handler.send_response = lambda code, message=None: state.__setitem__(
        "status", f"{code} {http.HTTPStatus(code).phrase}")
    handler.send_header = lambda k, v: state["headers"].append((k, v))
    handler.end_headers = lambda: _start(state["status"], state["headers"])
    handler.wfile = state["body"]
    handler.log_message = lambda *a: None
    handler.handle_expect_100 = lambda: True
    if handler.command == "POST":
        handler.do_POST()
    elif handler.command in ("GET", "HEAD"):
        handler.do_GET()
    else:
        handler._json(405, {"error": "method not allowed"})
    return [state["body"].getvalue()]

POST_ROUTES = {
    "/api/chat": "chat",
    "/api/search": "search",
    "/api/rebuild": "rebuild",
    "/api/delete": "file_delete",
    "/api/files/save": "file_save",
    "/api/files/upload": "file_upload",
    "/api/history/clear": "history_clear",
    "/api/memory/delete": "memory_delete",
    "/api/memory/clear": "memory_clear",
}


class Handler(BaseHTTPRequestHandler):
    def log_message(self, *a):
        pass

    def _send(self, code, body, ctype="application/json; charset=utf-8"):
        self.send_response(code)
        self.send_header("Content-Type", ctype)
        self.send_header("Cache-Control", "no-cache, no-store, must-revalidate")
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    def _json(self, code, payload):
        self._send(code, json.dumps(payload, ensure_ascii=False).encode("utf-8"))

    def _static(self, name, root):
        p = os.path.join(root, name)
        if not os.path.isfile(p):
            self._send(404, b"not found", "text/plain; charset=utf-8")
            return
        ext = os.path.splitext(name)[1]
        with open(p, "rb") as f:
            self._send(200, f.read(), CTYPES.get(ext, "application/octet-stream"))

    def handle_one_request(self):
        try:
            super().handle_one_request()
        except (BrokenPipeError, ConnectionResetError):
            pass

    def do_GET(self):
        try:
            parsed = urlparse(self.path)
            qs = parse_qs(parsed.query)
            route = GET_ROUTES.get(parsed.path)
            if route:
                code, payload, static_name = route(qs)
                if static_name:
                    with open(os.path.join(FRONTEND, static_name), encoding="utf-8") as f:
                        self._send(200, f.read().encode("utf-8"), "text/html; charset=utf-8")
                else:
                    self._json(code, payload)
            elif parsed.path.startswith("/vendor/"):
                self._static(os.path.basename(parsed.path), VENDOR)
            else:
                self._json(404, {"error": "not found"})
        except Exception as e:
            self._json(500, {"error": f"{type(e).__name__}: {e}"})

    def do_POST(self):
        try:
            fn_name = POST_ROUTES.get(self.path)
            if not fn_name:
                self._json(404, {"error": "not found"})
                return
            n = int(self.headers.get("Content-Length", 0))
            req = json.loads(self.rfile.read(n).decode("utf-8")) if n else {}
            if self.path == "/api/chat":
                q = (req.get("message") or "").strip()
                if req.get("action") == "reset":
                    self._json(200, api.history_clear())
                    return
                if not q:
                    self._json(400, {"error": "empty"})
                    return
                steps, reply = api.chat(q)
                self._json(200, {"reply": reply, "steps": steps})
                return
            q = (req.get("message") or "").strip()
            if fn_name == "search":
                if not q:
                    self._json(400, {"error": "empty"})
                    return
                self._json(200, api.search(q))
                return
            code, payload = getattr(api, fn_name)(req)
            self._json(code, payload)
        except SystemExit as e:
            self._json(500, {"error": str(e)})
        except Exception as e:
            self._json(500, {"error": f"{type(e).__name__}: {e}"})


if __name__ == "__main__":
    api.load_session()
    port = int(os.environ.get("PORT", "8778"))
    host = os.environ.get("HOST", "127.0.0.1")  # 上线时设 HOST=0.0.0.0
    try:
        from waitress import serve
    except ImportError:
        HTTPServer.allow_reuse_address = False
        print(f"叁月聚粮分身 · 前后端分离版已启动（http.server 开发模式）→ http://{host}:{port}")
        HTTPServer((host, port), Handler).serve_forever()
    else:
        threads = int(os.environ.get("THREADS", "8"))
        print(f"叁月聚粮分身 · 已启动（waitress 生产模式，{threads} 线程）→ http://{host}:{port}")
        serve(wsgi_app, host=host, port=port, threads=threads)
