# -*- coding: utf-8 -*-
"""把旧的 JSON 状态一次性导入数据库（P2 迁移）。

导入内容：
- data/users.json     → users 表
- data/usage.json     → usage 表（当天计数）
- sessions/*.json     → sessions 表（sessions/<user>/<sid>.json 与旧的 sessions/default.json）
- memory/long_term.jsonl → memories 表（无 user 字段的旧条目归 admin）
- memory/state.json   → kv.memory_state

用法：python migrate.py            # 幂等，重复执行会覆盖同键记录
"""
import json
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

import store  # noqa: E402

BASE = os.path.dirname(os.path.abspath(__file__))


def _read(path, default=None):
    try:
        with open(path, encoding="utf-8") as f:
            return json.load(f)
    except Exception:
        return default


def migrate_users():
    src = _read(os.path.join(BASE, "data", "users.json"), {}) or {}
    for name, u in src.items():
        store.user_put(name, u.get("salt", ""), u.get("hash", ""),
                       u.get("role", "user"), u.get("quota", 30))
    return len(src)


def migrate_usage():
    src = _read(os.path.join(BASE, "data", "usage.json"), {}) or {}
    day = src.get("date")
    if not day:
        return 0
    n = 0
    for user, c in (src.get("counts") or {}).items():
        store.usage_add(day, user, -store.usage_count(day, user))   # 归零后写入
        store.usage_add(day, user, int(c))
        n += 1
    return n


def migrate_sessions():
    root = os.path.join(BASE, "sessions")
    if not os.path.isdir(root):
        return 0
    n = 0
    for dirpath, _dirs, files in os.walk(root):
        for fn in files:
            if not fn.endswith(".json"):
                continue
            d = _read(os.path.join(dirpath, fn))
            if not isinstance(d, dict):
                continue
            user = d.get("user") or ""
            if not user:
                # 旧结构 sessions/<sid>.json：归 admin
                user = "admin" if os.path.abspath(dirpath) == os.path.abspath(root) else os.path.basename(dirpath)
            store.session_save(user, d.get("id") or fn[:-5], d.get("history", []))
            n += 1
    return n


def migrate_memory():
    src = os.path.join(BASE, "memory", "long_term.jsonl")
    n = 0
    if os.path.isfile(src):
        with open(src, encoding="utf-8") as f:
            for line in f:
                line = line.strip()
                if not line:
                    continue
                try:
                    it = json.loads(line)
                except Exception:
                    continue
                it.setdefault("user", "admin")
                store.memory_put(it)
                n += 1
    st = _read(os.path.join(BASE, "memory", "state.json"))
    if st:
        proc = st.get("processed")
        store.kv_put("memory_state", {"processed": proc if isinstance(proc, dict) else {},
                                      "last_activity": st.get("last_activity", "")})
    return n


def main():
    store.init_schema()
    print(f"后端={store.backend_name()}")
    print(f"账号 {migrate_users()} 个，配额记录 {migrate_usage()} 条，"
          f"会话 {migrate_sessions()} 个，记忆 {migrate_memory()} 条")
    print("迁移完成")


if __name__ == "__main__":
    main()
