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
import auth  # noqa: E402

BASE_DIR = os.path.dirname(os.path.abspath(__file__))
SESSIONS_DIR = os.path.join(BASE_DIR, "sessions")
START_TIME = time.time()

CURRENT = {}  # user -> 当前会话 id
_LOCKS = {}  # (user, sid) -> threading.Lock
_LOCKS_GUARD = threading.Lock()


# ---------- 会话持久化 ----------

def _user_dir(user):
    safe = "".join(c for c in user if c.isalnum() or c in "-_") or "anon"
    return os.path.join(SESSIONS_DIR, safe)


def _session_path(user, sid):
    safe_user = "".join(c for c in user if c.isalnum() or c in "-_") or "anon"
    safe_sid = "".join(c for c in sid if c.isalnum() or c in "-_") or "default"
    return os.path.join(SESSIONS_DIR, safe_user, safe_sid + ".json")


def _session_lock(user, sid):
    key = f"{user}__{sid}"
    with _LOCKS_GUARD:
        if key not in _LOCKS:
            if len(_LOCKS) > 500:
                _LOCKS.clear()
            _LOCKS[key] = threading.Lock()
        return _LOCKS[key]


def save_session(user, sid, history):
    p = _session_path(user, sid)
    os.makedirs(os.path.dirname(p), exist_ok=True)
    tmp = {"id": sid, "user": user, "updated": time.strftime("%Y-%m-%d %H:%M:%S"),
           "history": history}
    with open(p, "w", encoding="utf-8") as f:
        json.dump(tmp, f, ensure_ascii=False)


def load_session(user, sid):
    p = _session_path(user, sid)
    if os.path.isfile(p):
        try:
            with open(p, encoding="utf-8") as f:
                return json.load(f).get("history", [])
        except Exception:
            pass
    return []


def _current_sid(user):
    sid = CURRENT.get(user)
    if sid and os.path.isfile(_session_path(user, sid)):
        return sid
    best = None
    user_dir = _user_dir(user)
    if os.path.isdir(user_dir):
        for fn in os.listdir(user_dir):
            if fn.endswith(".json"):
                mtime = os.path.getmtime(os.path.join(user_dir, fn))
                if best is None or mtime > best[0]:
                    best = (mtime, fn[:-5])
    sid = best[1] if best else "default"
    CURRENT[user] = sid
    return sid


# ---------- 只读接口 ----------

def stats():
    import knowledge_base
    import embeddings
    eng = knowledge_base.get_engine()
    return {"files": len(knowledge_base.read_docs()),
            "chunks": len(eng.meta),
            "mode": embeddings.describe(),
            "history": _total_turns()}


def _total_turns():
    n = 0
    for root, _dirs, files in os.walk(SESSIONS_DIR):
        for fn in files:
            if not fn.endswith(".json"):
                continue
            try:
                with open(os.path.join(root, fn), encoding="utf-8") as f:
                    n += len(json.load(f).get("history", [])) // 2
            except Exception:
                pass
    return n


def system():
    import config
    return {"python": platform.python_version(),
            "uptime_sec": int(time.time() - START_TIME),
            "has_vector_key": bool(getattr(config, "EMBEDDING_API_KEY", "")),
            "vector_provider": getattr(config, "EMBEDDING_PROVIDER", "")}


def sessions_list(user=None, is_admin=False):
    items = []
    own_dir = _user_dir(user or "")
    if os.path.isdir(SESSIONS_DIR):
        for root, _dirs, files in os.walk(SESSIONS_DIR):
            if not is_admin and os.path.abspath(root) != os.path.abspath(own_dir):
                continue
            for fn in sorted(files):
                if not fn.endswith(".json"):
                    continue
                try:
                    with open(os.path.join(root, fn), encoding="utf-8") as f:
                        d = json.load(f)
                    rel = os.path.relpath(os.path.join(root, fn), SESSIONS_DIR)
                    key = rel[:-5].replace(os.sep, "__")
                    sid = d.get("id") or fn[:-5]
                    items.append({"id": sid, "key": key, "user": d.get("user", ""),
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


def history(user):
    sid = _current_sid(user)
    return {"history": load_session(user, sid)[-40:]}


def history_clear(user):
    sid = _current_sid(user)
    save_session(user, sid, [])
    return {"ok": True}


def session_current(user):
    return 200, {"id": _current_sid(user)}


def session_new(user, _req=None):
    sid = _current_sid(user)
    with _session_lock(user, sid):
        save_session(user, sid, load_session(user, sid))
    new_sid = "s" + time.strftime("%Y%m%d-%H%M%S")
    CURRENT[user] = new_sid
    save_session(user, new_sid, [])
    return 200, {"id": new_sid}


def session_switch(user, req):
    sid = str(req.get("id") or "default")
    old = _current_sid(user)
    with _session_lock(user, old):
        save_session(user, old, load_session(user, old))
    CURRENT[user] = sid
    load_session(user, sid)
    return 200, {"id": sid}


def session_delete(user, req):
    sid = str(req.get("id") or "")
    if not sid:
        return 400, {"error": "empty"}
    if sid == _current_sid(user):
        CURRENT[user] = "default"
        save_session(user, "default", load_session(user, "default"))
    p = _session_path(user, sid)
    if os.path.isfile(p):
        os.remove(p)
    return 200, {"ok": True, "id": CURRENT.get(user, "default")}


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


def chat(req, user):
    """返回 (steps, reply)，并把本轮写进该用户当前会话。"""
    q = (req.get("message") or "").strip()
    import knowledge_base
    import embeddings
    from llm import chat_with_tool_loop_collector
    sid = _current_sid(user)
    with _session_lock(user, sid):
        steps, reply = _chat_inner(user, sid, q, knowledge_base, embeddings,
                                   chat_with_tool_loop_collector)
    auth.usage_count(user)
    return steps, reply



def chat_stream(req, user, emit):
    """流式对话：通过 emit(dict) 实时推送 steps/delta 事件，返回完整回复。"""
    q = (req.get("message") or "").strip()
    import knowledge_base
    import embeddings
    from llm import chat_with_tool_loop_stream
    sid = _current_sid(user)
    with _session_lock(user, sid):
        reply = _chat_stream_inner(user, sid, q, emit, knowledge_base, embeddings,
                                   chat_with_tool_loop_stream)
    auth.usage_count(user)
    return reply


def _chat_stream_inner(user, sid, q, emit, knowledge_base, embeddings, chat_with_tool_loop_stream):
    import memory
    history = load_session(user, sid)
    memory.touch()
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
    emit({"type": "steps", "steps": steps})
    messages = agent.build_messages(history, q, verbose=False, hits=hits)
    reply_parts = []

    def on_delta(text):
        reply_parts.append(text)
        emit({"type": "delta", "text": text})

    reply = chat_with_tool_loop_stream(messages, steps, on_delta)
    if steps:
        emit({"type": "steps", "steps": steps})
    history.append({"role": "user", "content": q})
    history.append({"role": "assistant", "content": reply})
    save_session(user, sid, history)
    threading.Thread(target=memory.maybe_extract, args=(f"{user}/{sid}",), daemon=True).start()
    emit({"type": "done", "reply": reply})
    return reply

def _chat_inner(user, sid, q, knowledge_base, embeddings, chat_with_tool_loop_collector):
    import memory
    history = load_session(user, sid)
    memory.touch()
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
    messages = agent.build_messages(history, q, verbose=False, hits=hits)
    reply = chat_with_tool_loop_collector(messages, steps)
    history.append({"role": "user", "content": q})
    history.append({"role": "assistant", "content": reply})
    save_session(user, sid, history)
    # 抽取放后台线程：不阻塞用户看到回复，也和下一轮对话的 LLM 调用错开
    threading.Thread(target=memory.maybe_extract, args=(f"{user}/{sid}",), daemon=True).start()
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


# ---------- 鉴权与用户管理 ----------

def auth_login(req):
    username = str(req.get("username") or "").strip()
    blocked, left = auth.login_blocked(username)
    if blocked:
        return 429, {"error": f"登录失败次数过多，请 {left} 秒后再试"}
    if not auth.verify(username, req.get("password")):
        auth.record_login_fail(username)
        return 401, {"error": "用户名或密码错误"}
    auth.clear_login_fails(username)
    return 200, {"ok": True, "token": auth.issue_token(username),
                 "role": auth.role(username), "user": username}


def auth_status(user):
    return {"ok": True, **auth.status(user)}


def admin_users_payload():
    items = []
    for name, u in sorted(auth.users().items()):
        ok, used, quota = auth.usage_check(name)
        items.append({"username": name, "role": u.get("role", "user"),
                      "quota": u.get("quota", 30), "used_today": used,
                      "created": u.get("created", "")})
    return {"users": items}


def admin_users_list(_req=None):
    return 200, admin_users_payload()


def admin_user_create(req):
    return auth.create_user(req.get("username"), req.get("password"),
                            req.get("role") or "user", req.get("quota"))


def admin_user_password(req):
    return auth.set_password(req.get("username"), req.get("password"))


def admin_user_quota(req):
    return auth.set_quota(req.get("username"), req.get("quota"))


def admin_user_delete(req):
    return auth.delete_user(req.get("username"))
