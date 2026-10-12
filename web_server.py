# -*- coding: utf-8 -*-
"""
作文老师 Agent · 网页聊天服务
用法：python web_server.py  然后浏览器打开 http://127.0.0.1:8778
"""
import json
import os
import sys
from http.server import HTTPServer, BaseHTTPRequestHandler

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

import agent  # noqa: E402

HISTORY = []          # 全局会话记忆（点"新对话"清空）
PAGE = "web_chat.html"


class Handler(BaseHTTPRequestHandler):
    def log_message(self, *a):
        pass

    def _send(self, code, body, ctype="application/json; charset=utf-8"):
        self.send_response(code)
        self.send_header("Content-Type", ctype)
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    def do_GET(self):
        if self.path in ("/", "/index.html"):
            p = os.path.join(os.path.dirname(os.path.abspath(__file__)), PAGE)
            with open(p, encoding="utf-8") as f:
                self._send(200, f.read().encode("utf-8"), "text/html; charset=utf-8")
        else:
            self._send(404, b'{"error":"not found"}')

    def do_POST(self):
        if self.path != "/api/chat":
            self._send(404, b'{"error":"not found"}')
            return
        try:
            n = int(self.headers.get("Content-Length", 0))
            req = json.loads(self.rfile.read(n).decode("utf-8"))
        except Exception:
            self._send(400, json.dumps({"error": "bad request"}, ensure_ascii=False).encode("utf-8"))
            return

        q = (req.get("message") or "").strip()
        if req.get("action") == "reset":
            HISTORY.clear()
            self._send(200, json.dumps({"ok": True}, ensure_ascii=False).encode("utf-8"))
            return
        if not q:
            self._send(400, json.dumps({"error": "empty"}, ensure_ascii=False).encode("utf-8"))
            return

        steps = []  # 过程展示：检索命中/搜索动作
        try:
            # 复用 agent 的组装逻辑（收集检索命中）
            import knowledge_base
            hits = knowledge_base.retrieve(q)
            for h in hits[:3]:
                steps.append({"type": "retrieval", "text": f"知识库命中: {h['source']}（相关度 {h['score']}）"})
            if not hits:
                steps.append({"type": "retrieval", "text": "知识库未命中（空库或无相关语料）"})

            messages = agent.build_messages(HISTORY, q, verbose=False)
            # 工具循环：捕获每一步搜索动作
            from llm import chat_with_tool_loop_collector
            reply = chat_with_tool_loop_collector(messages, steps)
            HISTORY.append({"role": "user", "content": q})
            HISTORY.append({"role": "assistant", "content": reply})
            self._send(200, json.dumps({"reply": reply, "steps": steps}, ensure_ascii=False).encode("utf-8"))
        except SystemExit as e:
            self._send(500, json.dumps({"error": str(e)}, ensure_ascii=False).encode("utf-8"))
        except Exception as e:
            self._send(500, json.dumps({"error": f"{type(e).__name__}: {e}"}, ensure_ascii=False).encode("utf-8"))


if __name__ == "__main__":
    port = 8778
    print(f"作文老师 Agent · 网页版已启动 → http://127.0.0.1:{port}")
    HTTPServer(("127.0.0.1", port), Handler).serve_forever()
