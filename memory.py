# -*- coding: utf-8 -*-
"""长期记忆层：抽取、合并更新、过期、BM25 检索。零依赖，存 memory/long_term.jsonl"""
import json
import os
import re
import time
import threading

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
    try:
        with open(STATE_FILE, encoding="utf-8") as f:
            return json.load(f)
    except Exception:
        return {"processed": 0, "summarized": 0, "last_activity": ""}


def _save_state(st):
    os.makedirs(os.path.dirname(STATE_FILE), exist_ok=True)
    with open(STATE_FILE, "w", encoding="utf-8") as f:
        json.dump(st, f, ensure_ascii=False)


def _load_all():
    items = []
    if os.path.isfile(MEMORY_FILE):
        with open(MEMORY_FILE, encoding="utf-8") as f:
            for line in f:
                line = line.strip()
                if line:
                    try:
                        items.append(json.loads(line))
                    except Exception:
                        pass
    return items


def _save_all(items):
    os.makedirs(os.path.dirname(MEMORY_FILE), exist_ok=True)
    with open(MEMORY_FILE, "w", encoding="utf-8") as f:
        for it in items:
            f.write(json.dumps(it, ensure_ascii=False) + "\n")


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


def add(content, mtype, source_session="default", confidence=0.9):
    """写入一条记忆；与现有条目语义重复则更新原条目。返回是否新建。"""
    items = _load_all()
    now = _now()
    hit = _find_similar(content, mtype, items)
    if hit:
        hit["content"] = content          # 新说法覆盖旧说法（如年级变化）
        hit["last_seen_at"] = now
        hit["confidence"] = max(hit.get("confidence", 0), confidence)
        created = False
    else:
        items.append({
            "id": f"m{time.strftime('%Y%m%d_%H%M%S')}_{len(items):04d}",
            "type": mtype,
            "content": content,
            "source_session": source_session,
            "created_at": now,
            "last_seen_at": now,
            "confidence": confidence,
            "expired": False,
        })
        created = True
    # 容量上限：超限淘汰最旧且低置信
    for t, cap in CAPS.items():
        group = sorted([x for x in items if x["type"] == t],
                       key=lambda x: (_ts(x.get("last_seen_at", "")), x.get("confidence", 0)))
        while len(group) > cap:
            worst = group.pop(0)
            items = [x for x in items if x["id"] != worst["id"]]
    _save_all(items)
    return created if hit is None else False


def delete(mid):
    items = [x for x in _load_all() if x["id"] != mid]
    _save_all(items)


def clear():
    _save_all([])


def recall(query, limit=3):
    """返回要注入 system prompt 的记忆文本块（profile 全放，其余 BM25 取 TOP3）。"""
    items = [x for x in _load_all() if not _expired(x)]
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


def maybe_extract(session_id="default"):
    """每满 EXTRACT_EVERY 条新消息，让模型抽取一次长期记忆。返回新记的条数。"""
    with EXTRACT_LOCK:
        return _extract_inner(session_id)


def _extract_inner(session_id="default"):
    st = _load_state()
    new_msgs = len(_session_history(session_id)) - st.get("processed", 0)
    if new_msgs < EXTRACT_EVERY:
        return 0
    try:
        from llm import chat as llm_chat
        msgs = [{"role": "system", "content": "你是信息抽取器。只输出 JSON 数组，不要输出其它文字。"
                 "数组元素格式：{\"type\":\"profile|preference|fact\",\"content\":\"...\"}。"
                 "只提取对长期对话有价值的：用户身份/年级/目标/弱项(profile)、偏好(preference)、"
                 "重要事实(fact)。闲聊、一次性问题不要。最多 5 条，没有就输出 []。"},
                {"role": "user", "content": json.dumps(
                    _session_history(session_id)[st.get("processed", 0):],
                    ensure_ascii=False)}]
        resp = llm.chat(msgs, use_tools=False)
        raw = (resp.get("content") or "").strip()
        raw = re.search(r"\[.*\]", raw, re.S)
        arr = json.loads(raw.group(0)) if raw else []
        n = 0
        for it in arr if isinstance(arr, list) else []:
            if isinstance(it, dict) and it.get("content") and it.get("type") in CAPS:
                add(str(it["content"])[:200], it["type"], session_id, 0.7)
                n += 1
        st["processed"] = len(_session_history(session_id))
        _save_state(st)
        return n
    except Exception:
        return 0


def _session_history(session_id):
    try:
        p = os.path.join(os.path.dirname(os.path.abspath(__file__)),
                         "sessions", session_id + ".json")
        with open(p, encoding="utf-8") as f:
            return json.load(f).get("history", [])
    except Exception:
        return []


def touch():
    st = _load_state()
    st["last_activity"] = _now()
    _save_state(st)
