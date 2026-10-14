# -*- coding: utf-8 -*-
"""长期记忆层：抽取、合并更新、过期、BM25 检索。零依赖，存 memory/long_term.jsonl"""
import json
import os
import re
import time
import threading

import store

BASE_DIR = os.path.dirname(os.path.abspath(__file__))
MEMORY_FILE = os.path.join(BASE_DIR, "memory", "long_term.jsonl")
STATE_FILE = os.path.join(BASE_DIR, "memory", "state.json")

EXPIRE_DAYS = 180          # fact 类型半年没被"再见到"就过期
EXTRACT_EVERY = 24         # 每 24 条消息（12 轮）触发一次模型抽取
CAPS = {"profile": 20, "preference": 20, "fact": 50}
EXTRACT_LOCK = threading.Lock()


def _now():
    return time.strftime("%Y-%m-%d %H:%M:%S")


def _ts(s):
    try:
        return time.mktime(time.strptime(s, "%Y-%m-%d %H:%M:%S"))
    except Exception:
        return 0


def _load_state():
    return store.kv_get("memory_state", {"processed": {}, "last_activity": ""})


def _save_state(st):
    store.kv_put("memory_state", st)


def _owner(it):
    """记忆归属用户；历史遗留（无 user 字段）条目归 admin。"""
    return it.get("user") or "admin"


def _load_all(user=None):
    items = store.memory_all(user)
    for it in items:
        it["expired"] = bool(it.get("expired"))
    return items


def _save_all(items):
    """整表覆盖（迁移/测试用）。正常写入走 memory_put 单条 upsert。"""
    store.memory_clear(None)
    for it in items:
        store.memory_put(it)


def _expired(it):
    if it.get("expired"):
        return True
    if it["type"] == "fact" and it.get("last_seen_at"):
        age = time.time() - _ts(it["last_seen_at"])
        if age > EXPIRE_DAYS * 86400:
            return True
    return False


def _tokens(text):
    text = re.sub(r"\s+", " ", text.lower())
    cn = re.sub(r"[^\u4e00-\u9fff]", " ", text)
    toks = set(re.findall(r"[a-z0-9]+", text))
    for seg in cn.split():
        for i in range(len(seg) - 1):
            toks.add(seg[i:i + 2])
    return toks


def _find_similar(content, mtype, items):
    """同类型且词面重合 >= 0.5 视为同一条，更新而不是追加。"""
    q = _tokens(content)
    if not q:
        return None
    best, best_sim = None, 0.0
    for it in items:
        if it["type"] != mtype or it.get("expired"):
            continue
        overlap = len(q & _tokens(it["content"]))
        sim = overlap / max(len(q), 1)
        if sim > best_sim:
            best, best_sim = it, sim
    return best if best_sim >= 0.5 else None


def add(content, mtype, user="admin", source_session="default", confidence=0.9):
    """写入一条记忆（归属 user）；与同用户现有条目语义重复则更新原条目。"""
    items = _load_all()
    now = _now()
    mine = [x for x in items if _owner(x) == user]
    hit = _find_similar(content, mtype, mine)
    if hit:
        hit["content"] = content          # 新说法覆盖旧说法（如年级变化）
        hit["last_seen_at"] = now
        hit["confidence"] = max(hit.get("confidence", 0), confidence)
        hit["user"] = user
        created = False
    else:
        hit = {
            "id": f"m{time.strftime('%Y%m%d_%H%M%S')}_{len(items):04d}",
            "type": mtype,
            "content": content,
            "user": user,
            "source_session": source_session,
            "created_at": now,
            "last_seen_at": now,
            "confidence": confidence,
            "expired": False,
        }
        items.append(hit)
        created = True
    # 容量上限：按用户分桶，超限淘汰最旧且低置信
    dropped = []
    for t, cap in CAPS.items():
        group = sorted([x for x in items if x["type"] == t and _owner(x) == user],
                       key=lambda x: (_ts(x.get("last_seen_at", "")), x.get("confidence", 0)))
        while len(group) > cap:
            worst = group.pop(0)
            items = [x for x in items if x["id"] != worst["id"]]
            dropped.append(worst["id"])
    store.memory_put(hit)
    for mid in dropped:
        store.memory_delete(mid)
    return created if hit is None else False


def delete(mid, user=None):
    """user 非空时只允许删该用户自己的条目；None 表示管理员全权。"""
    if user is not None:
        rows = [x for x in _load_all() if x["id"] == mid]
        if not rows or _owner(rows[0]) != user:
            return
    store.memory_delete(mid)


def clear(user=None):
    store.memory_clear(user)


def recall(query, user="admin", limit=3):
    """返回该用户要注入 system prompt 的记忆文本块（profile 全放，其余 BM25 取 TOP3）。"""
    items = [x for x in _load_all(user) if not _expired(x)]
    if not items:
        return ""
    profiles = [x["content"] for x in items if x["type"] == "profile"]
    others = [x for x in items if x["type"] != "profile"]
    picked = []
    if query:
        q = _tokens(query)
        scored = sorted(others, key=lambda x: -len(q & _tokens(x["content"])))
        picked = [x["content"] for x in scored[:limit] if q & _tokens(x["content"])]
    lines = [f"- {c}" for c in profiles + picked]
    return "\n".join(lines)


def maybe_extract(user="admin", sid="default"):
    """每满 EXTRACT_EVERY 条新消息，让模型抽取一次长期记忆。返回新记的条数。"""
    with EXTRACT_LOCK:
        return _extract_inner(user, sid)


def _extract_inner(user="admin", sid="default"):
    session_id = f"{user}/{sid}"
    st = _load_state()
    processed = st.get("processed", {})
    if not isinstance(processed, dict):     # 兼容旧的全局 int
        processed = {}
    done = processed.get(session_id, 0)
    new_msgs = len(_session_history(session_id)) - done
    if new_msgs < EXTRACT_EVERY:
        return 0
    try:
        from llm import chat as llm_chat
        msgs = [{"role": "system", "content": "你是信息抽取器。只输出 JSON 数组，不要输出其它文字。"
                 "数组元素格式：{\"type\":\"profile|preference|fact\",\"content\":\"...\"}。"
                 "只提取对长期对话有价值的：用户身份/年级/目标/弱项(profile)、偏好(preference)、"
                 "重要事实(fact)。闲聊、一次性问题不要。最多 5 条，没有就输出 []。"},
                {"role": "user", "content": json.dumps(
                    _session_history(session_id)[done:],
                    ensure_ascii=False)}]
        resp = llm.chat(msgs, use_tools=False)
        raw = (resp.get("content") or "").strip()
        raw = re.search(r"\[.*\]", raw, re.S)
        arr = json.loads(raw.group(0)) if raw else []
        n = 0
        for it in arr if isinstance(arr, list) else []:
            if isinstance(it, dict) and it.get("content") and it.get("type") in CAPS:
                add(str(it["content"])[:200], it["type"], user, session_id, 0.7)
                n += 1
        processed[session_id] = len(_session_history(session_id))
        st["processed"] = processed
        _save_state(st)
        return n
    except Exception:
        return 0


def _session_history(session_id):
    """session_id 形如 user/sid，直接读库。"""
    if "/" not in session_id:
        return []
    user, sid = session_id.split("/", 1)
    return store.session_load(user, sid)


def touch():
    st = _load_state()
    st["last_activity"] = _now()
    _save_state(st)
