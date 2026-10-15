# -*- coding: utf-8 -*-
"""P2 状态外置层：会话 / 记忆 / 配额 / 账号 / 任务队列 全部走数据库。

后端二选一：
- SQLite（默认，零依赖，WAL 模式，支持多进程共享一个库文件）
- Postgres（生产，设 DATABASE_URL=postgresql://user:pw@host/db，需 pip install "psycopg[binary]"）

设计要点：
- 应用进程不再持有会话/记忆状态，任何实例都能处理任何请求 → HTTP 层无状态可水平扩。
- 对话执行走 jobs 表（DB 队列）：worker 认领任务，产出写 job_events，SSE 端按游标读事件。
- 同一会话 FIFO：只能认领“该会话最早的未完成任务”，不同会话并行。
"""
import json
import os
import sqlite3
import threading
import time

BASE_DIR = os.path.dirname(os.path.abspath(__file__))
DEFAULT_SQLITE = os.path.join(BASE_DIR, "data", "app.db")
IS_PG = False
_local = threading.local()

SCHEMA_SQLITE = """
CREATE TABLE IF NOT EXISTS users (
  username TEXT PRIMARY KEY, uid INTEGER, salt TEXT, hash TEXT, role TEXT, quota INTEGER, created TEXT,
  status TEXT DEFAULT 'active', note TEXT DEFAULT '');
CREATE TABLE IF NOT EXISTS sessions (
  uid INTEGER, user TEXT, sid TEXT, updated TEXT, history TEXT, PRIMARY KEY (user, sid));
CREATE TABLE IF NOT EXISTS memories (
  id TEXT PRIMARY KEY, uid INTEGER, user TEXT, type TEXT, content TEXT, source_session TEXT,
  created_at TEXT, last_seen_at TEXT, confidence REAL, expired INTEGER DEFAULT 0);
CREATE TABLE IF NOT EXISTS usage (
  day TEXT, uid INTEGER, user TEXT, n INTEGER, PRIMARY KEY (day, user));
CREATE TABLE IF NOT EXISTS kv (
  key TEXT PRIMARY KEY, value TEXT);
CREATE TABLE IF NOT EXISTS jobs (
  id INTEGER PRIMARY KEY AUTOINCREMENT, uid INTEGER, user TEXT, sid TEXT, message TEXT,
  status TEXT, reply TEXT, error TEXT, steps TEXT,
  created_at REAL, claimed_at REAL, finished_at REAL);
CREATE TABLE IF NOT EXISTS job_events (
  id INTEGER PRIMARY KEY AUTOINCREMENT, job_id INTEGER, payload TEXT);
CREATE INDEX IF NOT EXISTS idx_jobs_status ON jobs(status, id);
CREATE INDEX IF NOT EXISTS idx_events_job ON job_events(job_id, id);
CREATE TABLE IF NOT EXISTS weak_decks (
  id TEXT PRIMARY KEY, uid INTEGER, user TEXT, name TEXT, created TEXT);
CREATE TABLE IF NOT EXISTS weak_cards (
  id TEXT PRIMARY KEY, uid INTEGER, user TEXT, deck TEXT, point TEXT, type TEXT,
  evidence TEXT, session_ref TEXT, created TEXT, mastered INTEGER DEFAULT 0);
CREATE INDEX IF NOT EXISTS idx_weak_cards_user ON weak_cards(user, deck);
"""

SCHEMA_PG = SCHEMA_SQLITE.replace("INTEGER PRIMARY KEY AUTOINCREMENT", "BIGSERIAL PRIMARY KEY")


def _url():
    return os.environ.get("DATABASE_URL", "").strip()


def backend_name():
    return "postgres" if _url() else "sqlite"


def _conn():
    """每线程一个连接。"""
    url = _url()
    c = getattr(_local, "conn", None)
    if c is not None and not getattr(_local, "closed", False):
        return c
    if url:
        global IS_PG
        IS_PG = True
        try:
            import psycopg
        except Exception as e:
            raise RuntimeError("已设置 DATABASE_URL 但缺少驱动：pip install \"psycopg[binary]\"") from e
        c = psycopg.connect(url, autocommit=True)
    else:
        os.makedirs(os.path.dirname(DEFAULT_SQLITE), exist_ok=True)
        c = sqlite3.connect(DEFAULT_SQLITE, timeout=10, isolation_level=None)
        c.execute("PRAGMA journal_mode=WAL")
        c.execute("PRAGMA busy_timeout=8000")
    _local.conn = c
    return c


def _ph(sql):
    """SQLite 用 ?，Postgres 用 %s。"""
    return sql.replace("?", "%s") if _url() else sql


def _rows(sql, args=()):
    cur = _conn().execute(_ph(sql), args)
    cols = [d[0] for d in cur.description]
    return [dict(zip(cols, r)) for r in cur.fetchall()]


def _exec(sql, args=()):
    return _conn().execute(_ph(sql), args)


def _exec_ret(sql, args=()):
    """执行并返回 RETURNING 结果（两个后端都支持）。"""
    cur = _conn().execute(_ph(sql), args)
    r = cur.fetchone()
    return r


def init_schema():
    conn = _conn()
    if _url():
        for stmt in SCHEMA_PG.split(";"):
            if stmt.strip():
                conn.execute(stmt)
    else:
        conn.executescript(SCHEMA_SQLITE)
    _migrate_uid()


# 老库补列：uid（稳定用户 id，数据隔离的正式依据）
_UID_TABLES = ("users", "sessions", "memories", "usage", "jobs", "weak_decks", "weak_cards")


def _migrate_uid():
    conn = _conn()
    for t in _UID_TABLES:
        try:
            conn.execute(_ph(f"ALTER TABLE {t} ADD COLUMN uid INTEGER"))
        except Exception:
            pass
    # 给还没有 uid 的用户编号（按创建时间稳定递增）
    rows = _rows("SELECT username FROM users WHERE uid IS NULL ORDER BY created, username")
    nxt = 1
    got = _rows("SELECT MAX(uid) AS m FROM users")
    if got and got[0]["m"]:
        nxt = int(got[0]["m"]) + 1
    for r in rows:
        _exec("UPDATE users SET uid=? WHERE username=?", (nxt, r["username"]))
        nxt += 1
    # 历史的会话 / 记忆 / 用量 / 任务 / 卡片按用户名回填 uid
    for t in ("sessions", "memories", "usage", "jobs", "weak_decks", "weak_cards"):
        try:
            _exec(f"UPDATE {t} SET uid=(SELECT uid FROM users u WHERE u.username={t}.user) WHERE uid IS NULL")
        except Exception:
            pass
    # 用户已注销但数据还挂着 → 孤儿行直接清掉
    for t in ("sessions", "memories", "usage", "jobs", "weak_decks", "weak_cards"):
        try:
            _exec(f"DELETE FROM {t} WHERE uid IS NULL")
        except Exception:
            pass
    try:
        _exec("ALTER TABLE weak_cards ADD COLUMN hits INTEGER DEFAULT 1")
    except Exception:
        pass


def uid_of(username):
    """用户名 -> 稳定 id。"""
    rows = _rows("SELECT uid FROM users WHERE username=?", (username,))
    return int(rows[0]["uid"]) if rows and rows[0]["uid"] is not None else None


def _uid(username):
    return uid_of(username)


def _next_uid():
    rows = _rows("SELECT MAX(uid) AS m FROM users")
    return (int(rows[0]["m"]) if rows and rows[0]["m"] else 0) + 1


# ---------- 账号 ----------

def users_all():
    return {r["username"]: r for r in _rows("SELECT * FROM users")}


def user_put(username, salt, pw_hash, role, quota):
    uid = uid_of(username)
    if uid is None:
        uid = _next_uid()
    _exec("DELETE FROM users WHERE username=?", (username,))
    _exec("INSERT INTO users(username,uid,salt,hash,role,quota,created,status,note) VALUES(?,?,?,?,?,?,?,'active','')",
          (username, uid, salt, pw_hash, role, int(quota), time.strftime("%Y-%m-%d %H:%M:%S")))


def user_set_status(username, status, note=None):
    if note is None:
        _exec("UPDATE users SET status=? WHERE username=?", (status, username))
    else:
        _exec("UPDATE users SET status=?, note=? WHERE username=?", (status, note, username))


def user_delete(username):
    _exec("DELETE FROM users WHERE username=?", (username,))


def user_set_password(username, pw_hash):
    _exec("UPDATE users SET hash=? WHERE username=?", (pw_hash, username))


def user_set_quota(username, quota):
    _exec("UPDATE users SET quota=? WHERE username=?", (int(quota), username))


# ---------- 会话 ----------

def session_save(user, sid, history):
    blob = json.dumps(history, ensure_ascii=False)
    now = time.strftime("%Y-%m-%d %H:%M:%S")
    _exec("DELETE FROM sessions WHERE sid=? AND (uid=? OR user=?)", (sid, _uid(user), user))
    _exec("INSERT INTO sessions(uid,user,sid,updated,history) VALUES(?,?,?,?,?)", (_uid(user), user, sid, now, blob))


def session_load(user, sid):
    rows = _rows("SELECT history FROM sessions WHERE uid=? AND sid=?", (_uid(user), sid))
    if not rows:
        return []
    try:
        return json.loads(rows[0]["history"] or "[]")
    except Exception:
        return []


def session_delete(user, sid):
    _exec("DELETE FROM sessions WHERE uid=? AND sid=?", (_uid(user), sid))


def sessions_purge(user):
    """删除该用户的全部会话（用户注销时使用）。"""
    _exec("DELETE FROM sessions WHERE uid=?", (_uid(user),))


def jobs_purge(user):
    """删除该用户的全部任务（注销时清理，防止被继续执行）。"""
    _exec("DELETE FROM job_events WHERE job_id IN (SELECT id FROM jobs WHERE uid=?)", (_uid(user),))
    _exec("DELETE FROM jobs WHERE uid=?", (_uid(user),))


def kv_del_prefix(prefix):
    """删除指定前缀的键（如用户注销后残留的 cur:{user}）。"""
    rows = _rows("SELECT key FROM kv WHERE key LIKE ?", (prefix + "%",))
    for r in rows:
        _exec("DELETE FROM kv WHERE key=?", (r["key"],))


def sessions_of(user):
    return _rows("SELECT sid AS id, user, uid, updated, history FROM sessions WHERE uid=? ORDER BY updated DESC", (_uid(user),))


def sessions_all():
    return _rows("SELECT uid, user, sid AS id, updated, history FROM sessions ORDER BY updated DESC")


# ---------- 长期记忆 ----------

def memory_all(user=None):
    if user is None:
        return _rows("SELECT * FROM memories ORDER BY last_seen_at DESC")
    return _rows("SELECT * FROM memories WHERE uid=? ORDER BY last_seen_at DESC", (_uid(user),))


def memory_put(item):
    owner = item.get("user", "admin")
    _exec("DELETE FROM memories WHERE id=?", (item["id"],))
    _exec("INSERT INTO memories(id,uid,user,type,content,source_session,created_at,last_seen_at,confidence,expired)"
          " VALUES(?,?,?,?,?,?,?,?,?,?)",
          (item["id"], _uid(owner), owner, item["type"], item["content"],
           item.get("source_session", ""), item.get("created_at", ""), item.get("last_seen_at", ""),
           float(item.get("confidence", 0.9)), 1 if item.get("expired") else 0))


def memory_delete(mid):
    _exec("DELETE FROM memories WHERE id=?", (mid,))


def memory_clear(user=None):
    if user is None:
        _exec("DELETE FROM memories")
    else:
        _exec("DELETE FROM memories WHERE uid=?", (_uid(user),))


# ---------- 配额 ----------

def usage_count(day, user):
    rows = _rows("SELECT n FROM usage WHERE day=? AND uid=?", (day, _uid(user)))
    return rows[0]["n"] if rows else 0


def usage_add(day, user, delta=1):
    n = usage_count(day, user) + delta
    _exec("DELETE FROM usage WHERE day=? AND uid=?", (day, _uid(user)))
    _exec("INSERT INTO usage(day,uid,user,n) VALUES(?,?,?,?)", (day, _uid(user), user, n))
    return n


def usage_purge(user):
    """删除该用户所有日期的用量记录（用户注销时使用）。"""
    _exec("DELETE FROM usage WHERE uid=?", (_uid(user),))


# ---------- 键值（抽取进度等） ----------

def kv_get(key, default=None):
    rows = _rows("SELECT value FROM kv WHERE key=?", (key,))
    if not rows:
        return default
    try:
        return json.loads(rows[0]["value"])
    except Exception:
        return default


def kv_put(key, value):
    _exec("DELETE FROM kv WHERE key=?", (key,))
    _exec("INSERT INTO kv(key,value) VALUES(?,?)", (key, json.dumps(value, ensure_ascii=False)))


def counter_add(key, delta=1):
    """按日计数器（LLM 调用量等）。"""
    cur = kv_get(key, 0)
    kv_put(key, int(cur or 0) + int(delta))
    return int(cur or 0) + int(delta)


def counter_get(key):
    return int(kv_get(key, 0) or 0)


# ---------- 任务队列 ----------

def job_create(user, sid, message):
    row = _exec_ret("INSERT INTO jobs(uid,user,sid,message,status,reply,error,steps,created_at)"
                    " VALUES(?,?,?,?, 'queued','','','',?) RETURNING id",
                    (_uid(user), user, sid, message, time.time()))
    return row[0]


def job_claim():
    """认领“所属会话最早的未完成任务”，实现同会话 FIFO、跨会话并行。"""
    if _url():
        r = _exec_ret(
            "UPDATE jobs SET status='running', claimed_at=? WHERE id = ("
            "  SELECT j.id FROM jobs j WHERE j.status='queued' AND NOT EXISTS ("
            "    SELECT 1 FROM jobs p WHERE p.uid=j.uid AND p.sid=j.sid AND p.id<j.id"
            "      AND p.status IN ('queued','running'))"
            "  ORDER BY j.id LIMIT 1 FOR UPDATE SKIP LOCKED) RETURNING id, user, sid, message",
            (time.time(),))
        if not r:
            return None
        return {"id": r[0], "user": r[1], "sid": r[2], "message": r[3]}
    conn = _conn()
    conn.execute("BEGIN IMMEDIATE")
    try:
        rows = _rows(
            "SELECT j.id, j.uid, j.user, j.sid, j.message FROM jobs j WHERE j.status='queued' AND NOT EXISTS ("
            "  SELECT 1 FROM jobs p WHERE p.uid=j.uid AND p.sid=j.sid AND p.id<j.id"
            "    AND p.status IN ('queued','running')) ORDER BY j.id LIMIT 1")
        if not rows:
            conn.execute("COMMIT")
            return None
        job = rows[0]
        _exec("UPDATE jobs SET status='running', claimed_at=? WHERE id=?", (time.time(), job["id"]))
        conn.execute("COMMIT")
        return job
    except Exception:
        conn.execute("ROLLBACK")
        raise


def job_finish(job_id, status, reply="", error="", steps=None):
    _exec("UPDATE jobs SET status=?, reply=?, error=?, steps=?, finished_at=? WHERE id=?",
          (status, reply or "", error or "", json.dumps(steps or [], ensure_ascii=False), time.time(), job_id))


def job_get(job_id):
    rows = _rows("SELECT * FROM jobs WHERE id=?", (job_id,))
    return rows[0] if rows else None


def job_event_add(job_id, payload):
    _exec("INSERT INTO job_events(job_id,payload) VALUES(?,?)",
          (job_id, json.dumps(payload, ensure_ascii=False)))


def job_cancel(job_id):
    """请求取消：排队中置 cancelled（worker 不再领取），运行中由 worker 轮询后中止。"""
    _exec("UPDATE jobs SET status='cancelled' WHERE id=? AND status IN ('queued','running')", (job_id,))


# ---------- 薄弱点卡片（按用户隔离） ----------

def decks_list(user):
    return _rows("SELECT id, name, created FROM weak_decks WHERE uid=? ORDER BY created", (_uid(user),))


def deck_get(user, deck_id):
    rows = _rows("SELECT id, name FROM weak_decks WHERE uid=? AND id=?", (_uid(user), deck_id))
    return rows[0] if rows else None


def deck_create(user, name):
    deck_id = "d" + secrets_hex8() + time.strftime("%H%M%S")
    _exec("INSERT INTO weak_decks(id,uid,user,name,created) VALUES(?,?,?,?,?)",
          (deck_id, _uid(user), user, name, time.strftime("%Y-%m-%d %H:%M:%S")))
    return deck_get(user, deck_id)


def deck_rename(user, deck_id, name):
    _exec("UPDATE weak_decks SET name=? WHERE uid=? AND id=?", (name, _uid(user), deck_id))


def deck_delete(user, deck_id):
    _exec("DELETE FROM weak_cards WHERE uid=? AND deck=?", (_uid(user), deck_id))
    _exec("DELETE FROM weak_decks WHERE uid=? AND id=?", (_uid(user), deck_id))


def cards_list(user, deck=None):
    if deck:
        return _rows("SELECT * FROM weak_cards WHERE uid=? AND deck=? ORDER BY created DESC", (_uid(user), deck))
    return _rows("SELECT * FROM weak_cards WHERE uid=? ORDER BY created DESC", (_uid(user),))


def card_get(user, card_id):
    rows = _rows("SELECT * FROM weak_cards WHERE uid=? AND id=?", (_uid(user), card_id))
    return rows[0] if rows else None


def card_add(user, point, ctype, evidence="", deck="", session_ref=""):
    card_id = "c" + secrets_hex8() + time.strftime("%H%M%S")
    _exec("INSERT INTO weak_cards(id,uid,user,deck,point,type,evidence,session_ref,created,mastered,hits) VALUES(?,?,?,?,?,?,?,?,?,0,1)",
          (card_id, _uid(user), user, deck or "", point, ctype, evidence, session_ref,
           time.strftime("%Y-%m-%d %H:%M:%S")))
    return card_get(user, card_id)


def card_update(user, card_id, point=None, ctype=None, evidence=None, deck=None):
    row = card_get(user, card_id)
    if not row:
        return None
    _exec("UPDATE weak_cards SET point=?, type=?, evidence=?, deck=? WHERE uid=? AND id=?",
          (point if point is not None else row["point"],
           ctype if ctype is not None else row["type"],
           evidence if evidence is not None else row["evidence"],
           deck if deck is not None else row["deck"], user, card_id))
    return card_get(user, card_id)


def card_toggle(user, card_id, mastered):
    _exec("UPDATE weak_cards SET mastered=? WHERE uid=? AND id=?", (1 if mastered else 0, _uid(user), card_id))
    return card_get(user, card_id)


def card_hit(user, card_id):
    _exec("UPDATE weak_cards SET hits=COALESCE(hits,1)+1, mastered=0 WHERE uid=? AND id=?", (_uid(user), card_id))


def card_delete(user, card_id):
    _exec("DELETE FROM weak_cards WHERE uid=? AND id=?", (_uid(user), card_id))


def cards_clear(user, mastered_only=False, deck=None):
    sql = "DELETE FROM weak_cards WHERE uid=?"
    args = [_uid(user)]
    if mastered_only:
        sql += " AND mastered=1"
    if deck:
        sql += " AND deck=?"
        args.append(deck)
    _exec(sql, tuple(args))


def secrets_hex8():
    import secrets as _s
    return _s.token_hex(4)

def job_events(job_id, after_id=0):
    return _rows("SELECT id, payload FROM job_events WHERE job_id=? AND id>? ORDER BY id", (job_id, after_id))
