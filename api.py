# -*- coding: utf-8 -*-
"""业务 API 层：所有逻辑都在这里，web_server.py 只做 HTTP 路由。"""
import base64
import json
import os
import platform
import re
import shutil
import threading
import time

import agent  # noqa: E402

BASE_DIR = os.path.dirname(os.path.abspath(__file__))
SESSIONS_DIR = os.path.join(BASE_DIR, "sessions")
SESSION_LOCK = threading.Lock()
START_TIME = time.time()

HISTORY = []  # 当前会话记忆
CHAT_LOCK = threading.Lock()  # 串行化对话：防止多请求交叉写 HISTORY/会话文件


# ---------- 会话持久化 ----------

def _session_path(sid):
    safe = "".join(c for c in sid if c.isalnum() or c in "-_") or "default"
    return os.path.join(SESSIONS_DIR, safe + ".json")


def save_session(sid="default"):
    with SESSION_LOCK:
        os.makedirs(SESSIONS_DIR, exist_ok=True)
        tmp = {"id": sid, "updated": time.strftime("%Y-%m-%d %H:%M:%S"),
               "history": HISTORY}
        with open(_session_path(sid), "w", encoding="utf-8") as f:
            json.dump(tmp, f, ensure_ascii=False)


def load_session(sid="default"):
    p = _session_path(sid)
    if os.path.isfile(p):
        try:
            with open(p, encoding="utf-8") as f:
                data = json.load(f)
            HISTORY.clear()
            HISTORY.extend(data.get("history", []))
        except Exception:
            pass


# ---------- 只读接口 ----------

def stats():
    import knowledge_base
    import embeddings
    eng = knowledge_base.get_engine()
    return {"files": len(knowledge_base.read_docs()),
            "chunks": len(eng.meta),
            "mode": embeddings.describe(),
            "history": len(HISTORY) // 2}


def system():
    import config
    return {"python": platform.python_version(),
            "uptime_sec": int(time.time() - START_TIME),
            "has_vector_key": bool(getattr(config, "EMBEDDING_API_KEY", "")),
            "vector_provider": getattr(config, "EMBEDDING_PROVIDER", "")}


def sessions_list():
    items = []
    if os.path.isdir(SESSIONS_DIR):
        for fn in sorted(os.listdir(SESSIONS_DIR)):
            if not fn.endswith(".json"):
                continue
            try:
                with open(os.path.join(SESSIONS_DIR, fn), encoding="utf-8") as f:
                    d = json.load(f)
                items.append({"id": d.get("id", fn[:-5]),
                              "updated": d.get("updated", ""),
                              "turns": len(d.get("history", [])) // 2})
            except Exception:
                pass
    items.sort(key=lambda x: x["updated"], reverse=True)
    return {"sessions": items}


def categories():
    import knowledge_base
    cats = {}
    for root, _dirs, names in os.walk(knowledge_base.KNOWLEDGE_DIR):
        rel_dir = os.path.relpath(root, knowledge_base.KNOWLEDGE_DIR)
        if rel_dir == ".":
            rel_dir = "根目录"
        n = sum(1 for fn in names
                if fn.lower().endswith(knowledge_base.TEXT_EXTS + knowledge_base.BINARY_EXTS))
        if n:
            cats[rel_dir] = n
    return {"categories": cats}


def file_read(target):
    """target 为空返回文件列表，否则返回单文件内容（限制 20000 字符）。"""
    import knowledge_base
    base = os.path.abspath(knowledge_base.KNOWLEDGE_DIR)
    if target:
        full = os.path.abspath(os.path.join(base, target))
        if not full.startswith(base) or not os.path.isfile(full):
            return 404, {"error": "not found"}
        with open(full, encoding="utf-8", errors="ignore") as f:
            return 200, {"path": target, "content": f.read(20000)}
    eng = knowledge_base.get_engine()
    counts = {}
    for fn, _txt in eng.meta:
        counts[fn] = counts.get(fn, 0) + 1
    files = []
    for root, _dirs, names in os.walk(knowledge_base.KNOWLEDGE_DIR):
        for fn in sorted(names):
            if not fn.lower().endswith(knowledge_base.TEXT_EXTS + knowledge_base.BINARY_EXTS):
                continue
            full = os.path.join(root, fn)
            rel = os.path.relpath(full, knowledge_base.KNOWLEDGE_DIR).replace("\\", "/")
            files.append({"path": rel, "size": os.path.getsize(full),
                          "chunks": counts.get(rel, 0)})
    files.sort(key=lambda x: (-x["chunks"], x["path"]))
    return 200, {"files": files}


def history():
    return {"history": HISTORY[-40:]}


def history_clear():
    HISTORY.clear()
    save_session()
    return {"ok": True}


# ---------- 语料管理 ----------

def _safe_path(rel):
    import knowledge_base
    base = os.path.abspath(knowledge_base.KNOWLEDGE_DIR)
    rel = (rel or "").strip().replace("\\", "/")
    if not rel or ".." in rel:
        return None
    full = os.path.abspath(os.path.join(base, rel))
    return full if full.startswith(base) else None


def _rebuild_index():
    import knowledge_base
    knowledge_base._engine = None
    if os.path.exists(knowledge_base.INDEX_FILE):
        os.remove(knowledge_base.INDEX_FILE)
    eng = knowledge_base.get_engine()
    return len(knowledge_base.read_docs()), len(eng.meta)


def file_save(req):
    full = _safe_path(req.get("path"))
    content = req.get("content")
    if not full or content is None:
        return 400, {"error": "bad path"}
    os.makedirs(os.path.dirname(full), exist_ok=True)
    with open(full, "w", encoding="utf-8") as f:
        f.write(content)
    files, chunks = _rebuild_index()
    return 200, {"ok": True, "files": files, "chunks": chunks}


def file_upload(req):
    import knowledge_base
    full = _safe_path(req.get("path"))
    if not full:
        return 400, {"error": "bad path"}
    low = full.lower()
    if not low.endswith(knowledge_base.TEXT_EXTS + knowledge_base.BINARY_EXTS):
        return 400, {"error": "unsupported ext"}
    os.makedirs(os.path.dirname(full), exist_ok=True)
    raw = base64.b64decode(req.get("data") or "")
    if low.endswith(knowledge_base.TEXT_EXTS):
        with open(full, "w", encoding="utf-8") as f:
            f.write(raw.decode("utf-8", errors="replace"))
    else:
        with open(full, "wb") as f:
            f.write(raw)
    files, chunks = _rebuild_index()
    return 200, {"ok": True, "files": files, "chunks": chunks}


def file_delete(req):
    import knowledge_base
    full = _safe_path(req.get("path"))
    if not full or not os.path.isfile(full):
        return 404, {"error": "not found"}
    rel = os.path.relpath(full, os.path.abspath(knowledge_base.KNOWLEDGE_DIR))
    bak = os.path.join(BASE_DIR, "knowledge_backup", rel)
    os.makedirs(os.path.dirname(bak), exist_ok=True)
    if not os.path.exists(bak):
        shutil.copy2(full, bak)
    os.remove(full)
    files, chunks = _rebuild_index()
    return 200, {"ok": True, "files": files, "chunks": chunks}


def rebuild():
    files, chunks = _rebuild_index()
    return {"ok": True, "files": files, "chunks": chunks}


# ---------- 对话 ----------

def search(q):
    import knowledge_base
    import embeddings
    hits = knowledge_base.retrieve(q, top_k=6)
    return {"query": q, "mode": embeddings.describe(),
            "hits": [{"source": h["source"], "score": h["score"],
                      "text": h["text"][:400]} for h in hits]}


def chat(q):
    """返回 (steps, reply)，并把本轮写进会话记忆。"""
    import knowledge_base
    import embeddings
    from llm import chat_with_tool_loop_collector
    import memory
    with CHAT_LOCK:
        return _chat_inner(q, knowledge_base, embeddings, chat_with_tool_loop_collector)


def _chat_inner(q, knowledge_base, embeddings, chat_with_tool_loop_collector):
    import memory
    memory.check_idle_summary()
    # 显式"记住"指令：不走模型，直接写入
    m = re.match(r"^(?:请?记住|帮我记住|别忘[了记])[：:，,\s]*(.+)$", q.strip())
    if m:
        memory.add(m.group(1).strip()[:200], "fact")
    steps = []
    hits = knowledge_base.retrieve(q)
    steps.append({"type": "retrieval", "text": f"检索模式: {embeddings.describe()}"})
    for h in hits[:3]:
        steps.append({"type": "retrieval",
                      "text": f"知识库命中: {h['source']}（相关度 {h['score']}）"})
    if not hits:
        steps.append({"type": "retrieval", "text": "知识库未命中（空库或无相关语料）"})
    messages = agent.build_messages(HISTORY, q, verbose=False, hits=hits)
    reply = chat_with_tool_loop_collector(messages, steps)
    HISTORY.append({"role": "user", "content": q})
    HISTORY.append({"role": "assistant", "content": reply})
    save_session()
    # 抽取放后台线程：不阻塞用户看到回复，也和下一轮对话的 LLM 调用错开
    threading.Thread(target=memory.maybe_extract, args=("default",), daemon=True).start()
    return steps, reply


# ---------- 长期记忆管理 ----------

def memory_list():
    import memory
    items = memory._load_all()
    items.sort(key=lambda x: x.get("last_seen_at", ""), reverse=True)
    return {"items": items}


def memory_delete(req):
    import memory
    memory.delete(req.get("id") or "")
    return 200, {"ok": True}


def memory_clear(req=None):
    import memory
    memory.clear()
    return 200, {"ok": True}
