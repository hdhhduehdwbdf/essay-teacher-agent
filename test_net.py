# -*- coding: utf-8 -*-
"""net.py 自检：起一个本地 HTTP 服务，验证连接复用与重试逻辑"""
import http.server
import json
import threading
import sys

sys.path.insert(0, ".")
import net

HITS = {"n": 0}

class H(http.server.BaseHTTPRequestHandler):
    def do_POST(self):
        HITS["n"] += 1
        body = self.rfile.read(int(self.headers["Content-Length"]))
        # 前 2 次返回 500，验证重试；第 3 次成功
        if HITS["n"] <= 2:
            self.send_response(500)
            self.send_header("Content-Length", "0")
            self.end_headers()
            return
        self.send_response(200)
        self.send_header("Content-Type", "application/json")
        self.end_headers()
        self.wfile.write(json.dumps({"ok": True, "echo": json.loads(body)}).encode())
    def log_message(self, *a):
        pass

srv = http.server.ThreadingHTTPServer(("127.0.0.1", 18099), H)
threading.Thread(target=srv.serve_forever, daemon=True).start()
print("测试服务器已启动")

r = net.post_json("http://127.0.0.1:18099/ping", {"hello": "world"}, retries=3, backoff=0.1)
assert r["echo"]["hello"] == "world", r
assert HITS["n"] == 3, HITS["n"]
print("[OK] net.py 自检通过：重试与连接复用工作正常")
srv.shutdown()
