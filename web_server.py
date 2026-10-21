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
from http.server import HTTPServer, ThreadingHTTPServer, BaseHTTPRequestHandler
from urllib.parse import urlparse, parse_qs, unquote
import queue
import threading

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

import api  # noqa: E402
import auth  # noqa: E402
import store  # noqa: E402
import worker  # noqa: E402

FRONTEND = os.path.join(os.path.dirname(os.path.abspath(__file__)), "frontend")
VENDOR = os.path.join(os.path.dirname(os.path.abspath(__file__)), "vendor")
CTYPES = {".html": "text/html; charset=utf-8", ".js": "application/javascript; charset=utf-8",
          ".css": "text/css; charset=utf-8", ".png": "image/png", ".svg": "image/svg+xml", ".ttf": "font/ttf"}

GET_ROUTES = {
    "/": lambda qs, user: (200, None, "chat.html"),
    "/chat": lambda qs, user: (200, None, "chat.html"),
    "/system-flow": lambda qs, user: (200, None, "system_flow.html"),
    "/index.html": lambda qs, user: (200, None, "chat.html"),
    "/admin": lambda qs, user: (200, None, "admin.html"),
    "/admin/login": lambda qs, user: (200, None, "admin_login.html"),
    "/coach": lambda qs, user: (200, None, "coach.html"),
    "/api/coach/state": lambda qs, user: api.coach_state(qs, user),
    "/rag": lambda qs, user: (200, None, "rag.html"),
    "/star": lambda qs, user: (200, None, "star.html"),
    "/threads": lambda qs, user: (200, None, "threads.html"),
    "/flows": lambda qs, user: (200, None, "flows.html"),
    "/architecture": lambda qs, user: (200, None, "architecture.html"),
    "/memory-plan": lambda qs, user: (200, None, "memory_plan.html"),
    "/ops": lambda qs, user: (200, None, "ops.html"),
    "/optimization-map": lambda qs, user: (200, None, "optimization_map.html"),
    "/api/auth/status": lambda qs, user: (200, api.auth_status(user), None),
    "/api/me": lambda qs, user: (200, api.auth_status(user), None),
    "/api/stats": lambda qs, user: (200, api.stats(user, auth.role(user) == "admin"), None),
    "/api/system": lambda qs, user: (200, api.system(), None),
    "/api/metrics": lambda qs, user: (200, api.metrics(), None),
    "/api/sessions": lambda qs, user: (200, api.sessions_list(user, False), None),
    "/api/session": lambda qs, user: (200, api.session_current(user), None),
    "/api/categories": lambda qs, user: (200, api.categories(), None),
    "/api/history": lambda qs, user: (200, api.history(user), None),
    "/api/memory": lambda qs, user: (403, {"error": "仅管理员可查看长期记忆"}, None) if auth.role(user) != "admin" else (200, api.memory_list(), None),
    "/api/admin/users": lambda qs, user: (200, api.admin_users_payload(), None),
    "/api/weakpoints": lambda qs, user: api.weakpoints_list(qs, user),
    "/api/commands": lambda qs, user: api.commands_catalog(),
    "/api/admin/user": lambda qs, user: api.admin_user_overview(unquote((qs.get("username") or [""])[0])),
    "/api/admin/user/history": lambda qs, user: api.admin_user_history(
        unquote((qs.get("username") or [""])[0]), unquote((qs.get("sid") or [""])[0])),
    "/api/files": lambda qs, user: (*api.file_read(unquote(qs.get("path", [""])[0])), None),
}

# 需要管理员身份的路径
ADMIN_ONLY_PAGES = ("/admin", "/ops")
ADMIN_ONLY_API = ("/api/rebuild", "/api/delete", "/api/files/save", "/api/files/upload",
                  "/api/memory/delete", "/api/memory/clear", "/api/memory", "/api/files", "/api/categories",
                  "/api/metrics", "/api/admin/users")
ADMIN_ONLY_API += ("/api/admin/user", "/api/admin/user/history", "/api/system")


class _Headers(dict):
    """大小写不敏感的请求头，供 WSGI 适配器使用"""
    def get(self, key, default=None):
        k = key.lower()
        if k in self:
            return super().get(k, default)
        return super().get("http-" + k, default)


def wsgi_app(environ, start_response):
    """把现有 Handler 适配成 WSGI 应用，供 waitress 等生产服务器直接使用"""
    if environ.get("REQUEST_METHOD") == "POST" and environ.get("PATH_INFO", "").startswith("/api/chat/stream"):
        return _wsgi_stream(environ, start_response)
    return _wsgi_plain(environ, start_response)


def _wsgi_stream(environ, start_response):
    """流式聊天：逐块 yield SSE 数据。"""
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
    if handler.command == "POST" and handler.path.startswith("/api/chat/stream"):
        body_q = queue.Queue()

        class _QWriter:
            def write(self, b):
                body_q.put(b)
                return len(b)
            def flush(self):
                pass

        def _run():
            try:
                handler.do_POST()
            finally:
                body_q.put(None)

        handler.send_response = lambda code, message=None: None
        handler.send_header = lambda k, v: state["headers"].append((k, v))
        handler.end_headers = lambda: _start(state["status"] or "200 OK", state["headers"])
        handler.wfile = _QWriter()
        t = threading.Thread(target=_run, daemon=True)
        t.start()
        while True:
            item = body_q.get()
            if item is None:
                break
            yield item
        t.join(timeout=5)
        return


def _wsgi_plain(environ, start_response):
    """普通请求：一次性返回完整响应体。"""
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
    "/api/chat": lambda req, user: api.chat(req, user),
    "/api/search": lambda req, user: api.search(req.get("message") or ""),
    "/api/rebuild": lambda req, user: api.rebuild(),
    "/api/delete": lambda req, user: api.file_delete(req),
    "/api/files/save": lambda req, user: api.file_save(req),
    "/api/files/upload": lambda req, user: api.file_upload(req),
    "/api/history/clear": lambda req, user: (200, api.history_clear(user)),
    "/api/session/new": lambda req, user: api.session_new(user, req),
    "/api/session/switch": lambda req, user: api.session_switch(user, req),
    "/api/session/rename": lambda req, user: api.session_rename(user, req),
    "/api/session/delete": lambda req, user: api.session_delete(user, req),
    "/api/memory/delete": lambda req, user: api.memory_delete(req, user, auth.role(user) == "admin"),
    "/api/memory/clear": lambda req, user: api.memory_clear(req, user, auth.role(user) == "admin"),
    "/api/admin/users": lambda req, user: api.admin_users_list(req),
    "/api/admin/users/create": lambda req, user: api.admin_user_create(req),
    "/api/admin/users/password": lambda req, user: api.admin_user_password(req),
    "/api/admin/users/quota": lambda req, user: api.admin_user_quota(req),
    "/api/admin/users/delete": lambda req, user: api.admin_user_delete(req),
    "/api/admin/users/approve": lambda req, user: api.admin_user_approve(req),
    "/api/admin/users/reject": lambda req, user: api.admin_user_reject(req),
    "/api/admin/user/history/clear": lambda req, user: api.admin_user_history_clear(req),
    "/api/chat/stop": lambda req, user: api.chat_stop(req, user),
    "/api/weakpoints/suggest": lambda req, user: api.weakpoints_suggest(req, user),
    "/api/weakpoints/add": lambda req, user: api.weakpoints_add(req, user),
    "/api/weakpoints/add_many": lambda req, user: api.weakpoints_add_many(req, user),
    "/api/weakpoints/update": lambda req, user: api.weakpoints_update(req, user),
    "/api/weakpoints/toggle": lambda req, user: api.weakpoints_toggle(req, user),
    "/api/weakpoints/delete": lambda req, user: api.weakpoints_delete(req, user),
    "/api/weakpoints/clear": lambda req, user: api.weakpoints_clear(req, user),
    "/api/weakpoints/decks/create": lambda req, user: api.weakpoints_deck_create(req, user),
    "/api/weakpoints/decks/rename": lambda req, user: api.weakpoints_deck_rename(req, user),
    "/api/weakpoints/decks/delete": lambda req, user: api.weakpoints_deck_delete(req, user),
    "/api/weakpoints/decks/clear": lambda req, user: api.weakpoints_deck_clear(req, user),
    "/api/coach/enter": lambda req, user: api.coach_enter(req, user),
    "/api/coach/submit": lambda req, user: api.coach_submit(req, user),
    "/api/coach/ask": lambda req, user: api.coach_ask(req, user),
    "/api/coach/task": lambda req, user: api.coach_task_create(req, user),
    "/api/coach/stop": lambda req, user: api.coach_stop(req, user),
}

ADMIN_ONLY_POST = ("/api/rebuild", "/api/delete", "/api/files/save", "/api/files/upload",
                   "/api/memory/delete", "/api/memory/clear", "/api/admin/users", "/api/admin/users/create",
                   "/api/admin/users/password", "/api/admin/users/quota", "/api/admin/users/delete",
                   "/api/admin/users/approve", "/api/admin/users/reject",
                   "/api/admin/user/history/clear")


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
        cache = "public, max-age=86400" if ext in (".js", ".css", ".woff2", ".svg", ".png") else "no-cache"
        with open(p, "rb") as f:
            self.send_response(200)
            self.send_header("Content-Type", CTYPES.get(ext, "application/octet-stream"))
            self.send_header("Cache-Control", cache)
            body = f.read()
            self.send_header("Content-Length", str(len(body)))
            self.end_headers()
            self.wfile.write(body)

    def handle_one_request(self):
        try:
            super().handle_one_request()
        except (BrokenPipeError, ConnectionResetError):
            pass

    def do_GET(self):
        try:
            parsed = urlparse(self.path)
            qs = parse_qs(parsed.query)
            print(f"[access] GET {parsed.path}", flush=True)
            if parsed.path == "/api/health":
                code, payload = api.health()
                self._json(code, payload)
                return
            if parsed.path.startswith("/vendor/"):
                self._static(os.path.basename(parsed.path), VENDOR)
                return
            if parsed.path in ("/login", "/login.html"):
                self._static("login.html", FRONTEND)
                return
            if parsed.path in ("/admin/login", "/admin_login.html"):
                self._static("admin_login.html", FRONTEND)
                return
            user = auth.from_headers(self.headers)
            if user is None and parsed.path.startswith("/api/"):
                self._json(401, {"need_login": True, "error": "未登录"})
                return
            if user is None:
                self._redirect("/login")
                return
            if (parsed.path in ADMIN_ONLY_PAGES or parsed.path in ADMIN_ONLY_API) and auth.role(user) != "admin":
                self._send(403, "<h2>403 无权限</h2><p>该页面仅管理员可见。</p>".encode("utf-8"),
                           "text/html; charset=utf-8")
                return
            route = GET_ROUTES.get(parsed.path)
            if route:
                code, payload, static_name = route(qs, user)
                if static_name:
                    with open(os.path.join(FRONTEND, static_name), encoding="utf-8") as f:
                        self._send(200, f.read().encode("utf-8"), "text/html; charset=utf-8")
                else:
                    self._json(code, payload)
            else:
                self._json(404, {"error": "not found"})
        except Exception as e:
            self._json(500, {"error": f"{type(e).__name__}: {e}"})

    def _redirect(self, location):
        self.send_response(302)
        self.send_header("Location", location)
        self.send_header("Content-Length", "0")
        self.end_headers()

    def _json_ex(self, code, payload, extra_headers=()):
        self.send_response(code)
        self.send_header("Content-Type", "application/json; charset=utf-8")
        for k, v in extra_headers:
            self.send_header(k, v)
        body = json.dumps(payload, ensure_ascii=False).encode("utf-8")
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    def _sse(self, obj):
        self.wfile.write(("data: " + json.dumps(obj, ensure_ascii=False) + "\n\n").encode("utf-8"))
        self.wfile.flush()

    def _chat_gate(self, user):
        """对话前置检查：频率 + 每日额度。返回 True 表示放行。"""
        throttled, wait_sec = auth.throttled(user)
        if throttled:
            self._json(429, {"error": f"请求太频繁，请 {wait_sec} 秒后再试"})
            return False
        ok, used, quota = auth.usage_check(user)
        if not ok:
            self._json(429, {"error": f"今日额度已用完（{used}/{quota}），明天自动重置"})
            return False
        return True

    def _chat_stream_post(self, req, user):
        q = (req.get("message") or "").strip()
        if req.get("action") == "reset":
            self._json(200, api.history_clear(user))
            return
        if not q:
            self._json(400, {"error": "empty"})
            return
        if not self._chat_gate(user):
            return
        self.send_response(200)
        self.send_header("Content-Type", "text/event-stream; charset=utf-8")
        self.send_header("Cache-Control", "no-cache")
        self.end_headers()
        try:
            api.chat_stream(req, user, lambda ev: self._sse(ev))
        except Exception as e:
            try:
                self._sse({"type": "error", "error": f"{type(e).__name__}: {e}"})
            except Exception:
                pass

    def _coach_stream_post(self, req, user):
        self.send_response(200)
        self.send_header("Content-Type", "text/event-stream; charset=utf-8")
        self.send_header("Cache-Control", "no-cache")
        self.end_headers()
        try:
            api.coach_stream(req, user, lambda ev: self._sse(ev))
        except Exception as e:
            try:
                self._sse({"type": "error", "error": f"{type(e).__name__}: {e}"})
            except Exception:
                pass

    def do_POST(self):
        try:
            path = urlparse(self.path).path
            print(f"[access] POST {path}", flush=True)
            n = int(self.headers.get("Content-Length", 0))
            req = json.loads(self.rfile.read(n).decode("utf-8")) if n else {}
            if path == "/api/auth/login":
                code, payload = api.auth_login(req)
                headers = []
                if code == 200:
                    headers.append(("Set-Cookie", auth.set_cookie(payload.pop("token"))))
                self._json_ex(code, payload, headers)
                return
            if path == "/api/auth/logout":
                self._json_ex(200, {"ok": True}, [("Set-Cookie", auth.clear_cookie())])
                return
            if path == "/api/auth/register":
                code, payload = api.auth_register(req)
                self._json_ex(code, payload)
                return
            user = auth.from_headers(self.headers)
            if user is None:
                self._json(401, {"need_login": True, "error": "未登录"})
                return
            if path == "/api/chat/stream":
                self._chat_stream_post(req, user)
                return
            if path == "/api/coach/stream":
                self._coach_stream_post(req, user)
                return
            fn = POST_ROUTES.get(path)
            if not fn:
                self._json(404, {"error": "not found"})
                return
            if path in ADMIN_ONLY_POST and auth.role(user) != "admin":
                self._json(403, {"error": "仅管理员可操作"})
                return
            if path == "/api/chat":
                q = (req.get("message") or "").strip()
                if req.get("action") == "reset":
                    self._json(200, api.history_clear(user))
                    return
                if not q:
                    self._json(400, {"error": "empty"})
                    return
                if not self._chat_gate(user):
                    return
                code, payload = api.chat(req, user)
                self._json(code, payload)
                return
            if path == "/api/search" and not (req.get("message") or "").strip():
                self._json(400, {"error": "empty"})
                return
            code, payload = fn(req, user)
            self._json(code, payload)
        except SystemExit as e:
            self._json(500, {"error": str(e)})
        except Exception as e:
            self._json(500, {"error": f"{type(e).__name__}: {e}"})


def _warmup():
    """后台预热：索引/检索器/embedding/LLM 连接，不阻塞服务启动。"""
    import time
    time.sleep(0.5)
    try:
        import knowledge_base, rag, embeddings
        knowledge_base.get_engine()
        rag.get_retriever()
        if embeddings.available():
            embeddings.embed_query("预热")
        try:
            from llm import chat
            chat([{"role": "user", "content": "1"}], use_tools=False, max_tokens=1)
        except Exception:
            pass
        print("[预热] 检索与模型连接已就绪")
    except Exception as e:
        print(f"[预热] 失败（不影响服务）：{type(e).__name__}: {e}")


if __name__ == "__main__":
    store.init_schema()
    auth.init_admin()
    if os.environ.get("EMBED_WORKERS", "1") != "0":
        worker.start_threads(int(os.environ.get("EMBED_WORKER_THREADS", "2")))
        print(f"[worker] 已内嵌 2 个消费线程（EMBED_WORKERS=0 可关闭，改用独立 worker 进程）")
    threading.Thread(target=_warmup, daemon=True).start()
    port = int(os.environ.get("PORT", "8778"))
    host = os.environ.get("HOST", "127.0.0.1")  # 上线时设 HOST=0.0.0.0
    try:
        from waitress import serve
    except ImportError:
        ThreadingHTTPServer.allow_reuse_address = False
        ThreadingHTTPServer.daemon_threads = True
        print(f"叁月聚粮分身 · 前后端分离版已启动（多线程 http.server 模式）→ http://{host}:{port}")
        ThreadingHTTPServer((host, port), Handler).serve_forever()
    else:
        threads = int(os.environ.get("THREADS", "8"))
        print(f"叁月聚粮分身 · 已启动（waitress 生产模式，{threads} 线程）→ http://{host}:{port}")
        serve(wsgi_app, host=host, port=port, threads=threads)
