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
import store  # noqa: E402

BASE_DIR = os.path.dirname(os.path.abspath(__file__))
SESSIONS_DIR = os.path.join(BASE_DIR, "sessions")
START_TIME = time.time()

CURRENT = {}  # user -> 当前会话 id（进程内缓存，权威数据在库里）


# ---------- 会话持久化 ----------

def save_session(user, sid, history):
    store.session_save(user, sid, history)


def load_session(user, sid):
    return store.session_load(user, sid)


def _current_sid(user):
    sid = CURRENT.get(user)
    if sid:
        return sid
    rows = store.sessions_of(user)
    sid = rows[0]["id"] if rows else "default"
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
    for r in store.sessions_all():
        try:
            n += len(json.loads(r["history"] or "[]")) // 2
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
    rows = store.sessions_all() if is_admin else store.sessions_of(user or "")
    items = []
    for r in rows:
        try:
            turns = len(json.loads(r["history"] or "[]")) // 2
        except Exception:
            turns = 0
        items.append({"id": r["id"], "key": f"{r.get('user', user)}__{r['id']}",
                      "user": r.get("user", user or ""),
                      "updated": r.get("updated", ""), "turns": turns})
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
    new_sid = "s" + time.strftime("%Y%m%d-%H%M%S")
    CURRENT[user] = new_sid
    save_session(user, new_sid, [])
    return 200, {"id": new_sid}


def session_switch(user, req):
    sid = str(req.get("id") or "default")
    CURRENT[user] = sid
    if all(r["id"] != sid for r in store.sessions_of(user)):
        save_session(user, sid, [])
    return 200, {"id": sid}


def session_delete(user, req):
    sid = str(req.get("id") or "")
    if not sid:
        return 400, {"error": "empty"}
    if sid == _current_sid(user):
        CURRENT[user] = "default"
    store.session_delete(user, sid)
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
    """入队执行并等待结果。返回 (code, payload)。"""
    q = (req.get("message") or "").strip()
    sid = _current_sid(user)
    job_id = store.job_create(user, sid, q)
    timeout = float(os.environ.get("CHAT_TIMEOUT", "180"))
    deadline = time.time() + timeout
    while time.time() < deadline:
        job = store.job_get(job_id)
        if job and job["status"] in ("done", "failed"):
            break
        time.sleep(0.2)
    job = store.job_get(job_id)
    if not job or job["status"] not in ("done", "failed"):
        return 504, {"error": "回答超时，请重试"}
    if job["status"] != "done":
        return 500, {"error": job.get("error") or "执行失败"}
    auth.usage_count(user)
    return 200, {"reply": job["reply"], "steps": json.loads(job.get("steps") or "[]")}


def chat_stream(req, user, emit):
    """入队执行，按游标读 job_events 实时推送；任何实例都能服务该连接。"""
    q = (req.get("message") or "").strip()
    sid = _current_sid(user)
    job_id = store.job_create(user, sid, q)
    cursor = 0
    timeout = float(os.environ.get("CHAT_TIMEOUT", "180"))
    deadline = time.time() + timeout
    while True:
        for e in store.job_events(job_id, cursor):
            cursor = e["id"]
            try:
                emit(json.loads(e["payload"]))
            except Exception:
                pass
        job = store.job_get(job_id)
        done = job and job["status"] in ("done", "failed")
        if done and not store.job_events(job_id, cursor):
            break
        if time.time() > deadline:
            emit({"type": "error", "error": "回答超时，请重试"})
            return ""
        time.sleep(float(os.environ.get("SSE_POLL", "0.15")))
    job = store.job_get(job_id)
    if job["status"] == "done":
        auth.usage_count(user)
        emit({"type": "done", "reply": job["reply"]})
        return job["reply"]
    emit({"type": "error", "error": job.get("error") or "执行失败"})
    return ""


# ---------- 队列执行（worker 调用） ----------

def run_job(job):
    """执行一个已认领的任务：检索 → LLM → 落会话 → 写事件。"""
    import memory
    import knowledge_base
    import embeddings
    from llm import chat_with_tool_loop_stream
    user, sid, q = job["user"], job["sid"], job["message"]
    job_id = job["id"]

    def emit(ev):
        store.job_event_add(job_id, ev)

    try:
        history = load_session(user, sid)
        memory.touch()
        m = re.match(r"^(?:请?记住|帮我记住|别忘[了记])[：:，,\s]*(.+)$", q.strip())
        if m:
            memory.add(m.group(1).strip()[:200], "fact", user)
        steps = []
        hits = knowledge_base.retrieve(q)
        steps.append({"type": "retrieval", "text": f"检索模式: {embeddings.describe()}"})
        for h in hits[:3]:
            steps.append({"type": "retrieval",
                          "text": f"知识库命中: {h['source']}（相关度 {h['score']}）"})
        if not hits:
            steps.append({"type": "retrieval", "text": "知识库未命中（空库或无相关语料）"})
        emit({"type": "steps", "steps": steps})
        messages = agent.build_messages(history, q, verbose=False, hits=hits, user=user)

        def on_delta(text):
            emit({"type": "delta", "text": text})

        reply = chat_with_tool_loop_stream(messages, steps, on_delta)
        if steps:
            emit({"type": "steps", "steps": steps})
        history.append({"role": "user", "content": q})
        history.append({"role": "assistant", "content": reply})
        store.session_save(user, sid, history)
        threading.Thread(target=memory.maybe_extract, args=(user, sid), daemon=True).start()
        store.job_finish(job_id, "done", reply, steps=steps)
        return reply
    except Exception as e:
        store.job_finish(job_id, "failed", error=f"{type(e).__name__}: {e}")
        return ""


# ---------- 长期记忆管理 ----------

def memory_list():
    import memory
    items = memory._load_all()
    items.sort(key=lambda x: x.get("last_seen_at", ""), reverse=True)
    return {"items": items}


def memory_delete(req, user=None, is_admin=False):
    import memory
    memory.delete(req.get("id") or "", None if is_admin else user)
    return 200, {"ok": True}


def memory_clear(req=None, user=None, is_admin=False):
    import memory
    memory.clear(None if is_admin else user)
    return 200, {"ok": True}


def memory_list_payload(user=None, is_admin=True):
    """管理台：按用户过滤（管理员看全部）。"""
    import memory
    return memory._load_all(None if is_admin else user)


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
