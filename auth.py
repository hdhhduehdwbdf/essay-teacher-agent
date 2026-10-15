# -*- coding: utf-8 -*-
"""鉴权与配额：用户文件 + 签名 cookie token + 每日轮次配额。
数据落盘 my_agent/data/（users.json / usage.json / secret.key），均不进 git。
"""
import base64
import hashlib
import hmac
import os
import secrets
import threading
import time
import json

import store

BASE_DIR = os.path.dirname(os.path.abspath(__file__))
DATA_DIR = os.path.join(BASE_DIR, "data")
SECRET_FILE = os.path.join(DATA_DIR, "secret.key")
TOKEN_TTL = 7 * 86400
THROTTLE_INTERVAL = 2

_LOCK = threading.RLock()
_THROTTLE = {}
LOGIN_MAX_FAILS = 5
LOGIN_LOCK_SECONDS = 300
_FAILS = {}


def _ensure_dir():
    os.makedirs(DATA_DIR, exist_ok=True)


def _secret():
    _ensure_dir()
    if os.path.isfile(SECRET_FILE):
        with open(SECRET_FILE) as f:
            s = f.read().strip()
        if s:
            return s
    s = secrets.token_hex(32)
    with open(SECRET_FILE, "w") as f:
        f.write(s)
    return s


def _hash(pw, salt):
    return hashlib.sha256((salt + pw).encode("utf-8")).hexdigest()


def init_admin():
    """首次启动时创建管理员账号。密码优先取环境变量 ADMIN_PASSWORD。"""
    store.init_schema()
    if store.users_all():
        return
    pw = os.environ.get("ADMIN_PASSWORD") or secrets.token_hex(6)
    salt = secrets.token_hex(8)
    store.user_put("admin", salt, _hash(pw, salt), "admin", int(os.environ.get("DAILY_QUOTA", "30")))
    if not os.environ.get("ADMIN_PASSWORD"):
        print(f"[auth] 初始管理员账号已创建 -> 用户名 admin 密码 {pw}（请尽快在管理台修改）")


def _now():
    return time.strftime("%Y-%m-%d %H:%M:%S")


def users():
    return store.users_all()


def role(username):
    return users().get(username, {}).get("role", "user")


def create_user(username, password, role="user", quota=None):
    username = str(username or "").strip()
    if not (2 <= len(username) <= 20) or not all(c.isalnum() or c in "-_" for c in username):
        return 400, {"error": "用户名需 2-20 位字母数字-_"}
    if len(str(password or "")) < 6:
        return 400, {"error": "密码至少 6 位"}
    with _LOCK:
        if username in store.users_all():
            return 400, {"error": "用户名已存在"}
        salt = secrets.token_hex(8)
        store.user_put(username, salt, _hash(str(password), salt), role,
                       int(quota or os.environ.get("DAILY_QUOTA", "30")))
    return 200, {"ok": True}


def register(username, password, note=""):
    """访客自助申请：账号为 pending 状态，管理员同意后才能登录。"""
    code, payload = create_user(username, password, role="user")
    if code != 200:
        return code, payload
    store.user_set_status(str(username).strip(), "pending", str(note or "")[:100])
    _notify_applications(f"新注册申请：{username}（{str(note or '')[:50]}）")
    return 200, {"ok": True, "pending": True}


def _notify_applications(text):
    print(f"[register] {text}", flush=True)
    url = os.environ.get("ALERT_WEBHOOK", "")
    if not url:
        return
    try:
        import urllib.request
        req = urllib.request.Request(url, data=json.dumps({"text": text}).encode(),
                                     headers={"Content-Type": "application/json"}, method="POST")
        urllib.request.urlopen(req, timeout=5)
    except Exception as e:
        print(f"[register] 告警发送失败：{type(e).__name__}: {e}", flush=True)


def approve_user(username):
    """同意申请：账号转 active。"""
    if username not in store.users_all():
        return 404, {"error": "申请不存在"}
    store.user_set_status(username, "active")
    return 200, {"ok": True}


def reject_user(username):
    """拒绝申请：直接删除账号记录。"""
    if username not in store.users_all():
        return 404, {"error": "申请不存在"}
    _purge_user_data(username)
    return 200, {"ok": True}


def _purge_user_data(username):
    """注销账号：连同会话、长期记忆、用量一并清理，避免同名重建后继承旧数据。"""
    with _LOCK:
        store.sessions_purge(username)
        store.memory_clear(username)
        store.usage_purge(username)
        store.jobs_purge(username)
        store.cards_clear(username)
        store.kv_del_prefix(f"cur:{username}")
        store.user_delete(username)


def pending_users():
    return [u for u in store.users_all().values() if u.get("status") == "pending"]


def set_password(username, password):
    if len(str(password or "")) < 6:
        return 400, {"error": "密码至少 6 位"}
    with _LOCK:
        if username not in store.users_all():
            return 404, {"error": "用户不存在"}
        salt = store.users_all()[username]["salt"]
        store.user_set_password(username, _hash(str(password), salt))
    return 200, {"ok": True}


def set_quota(username, quota):
    with _LOCK:
        if username not in store.users_all():
            return 404, {"error": "用户不存在"}
        store.user_set_quota(username, quota)
    return 200, {"ok": True}


def delete_user(username):
    if username == "admin":
        return 400, {"error": "不能删除管理员"}
    with _LOCK:
        if username not in store.users_all():
            return 404, {"error": "用户不存在"}
    _purge_user_data(username)
    return 200, {"ok": True}


def verify(username, password):
    u = users().get(str(username or "").strip())
    if not u:
        return False
    if u.get("status") == "pending":
        return False
    return hmac.compare_digest(u["hash"], _hash(str(password), u["salt"]))


def login_blocked(username):
    """返回 (blocked, 剩余秒数)，连续失败过多时锁定。"""
    with _LOCK:
        n, ts = _FAILS.get(username, (0, 0))
        if n < LOGIN_MAX_FAILS:
            return False, 0
        left = int(LOGIN_LOCK_SECONDS - (time.time() - ts))
        if left <= 0:
            _FAILS.pop(username, None)
            return False, 0
        return True, left


def record_login_fail(username):
    with _LOCK:
        n, _ts = _FAILS.get(username, (0, 0))
        _FAILS[username] = (n + 1, time.time())


def clear_login_fails(username):
    with _LOCK:
        _FAILS.pop(username, None)


def _fingerprint(username):
    return users().get(username, {}).get("hash", "")[:16]


def issue_token(username):
    exp = int(time.time()) + TOKEN_TTL
    payload = f"{username}|{exp}|{_fingerprint(username)}"
    sig = hmac.new(_secret().encode(), payload.encode(), hashlib.sha256).hexdigest()[:32]
    return base64.urlsafe_b64encode(f"{payload}|{sig}".encode()).decode()


def parse_token(token):
    try:
        raw = base64.urlsafe_b64decode(token.encode()).decode()
        username, exp, fp, sig = raw.split("|")
        if int(exp) < time.time():
            return None
        if fp != _fingerprint(username):
            return None
        payload = f"{username}|{exp}|{fp}"
        want = hmac.new(_secret().encode(), payload.encode(), hashlib.sha256).hexdigest()[:32]
        if not hmac.compare_digest(sig, want):
            return None
        return username
    except Exception:
        return None


def from_headers(headers):
    cookie = ""
    if hasattr(headers, "get"):
        cookie = headers.get("cookie") or ""
    for part in cookie.split(";"):
        part = part.strip()
        if part.startswith("auth="):
            return parse_token(part[5:])
    return None


def set_cookie(token):
    return f"auth={token}; Path=/; HttpOnly; Max-Age={TOKEN_TTL}; SameSite=Lax"


def clear_cookie():
    return "auth=; Path=/; HttpOnly; Max-Age=0; SameSite=Lax"


def throttled(username):
    """同一用户相邻请求间隔过短则 True。"""
    with _LOCK:
        now = time.time()
        last = _THROTTLE.get(username, 0)
        _THROTTLE[username] = now
        return (now - last) < THROTTLE_INTERVAL


def usage_check(username):
    """返回 (ok, used, quota)。ok=False 表示已超限。调用方需在对话成功后调 usage_count。"""
    u = store.users_all().get(username)
    quota = u.get("quota", 30) if u else 30
    used = store.usage_count(time.strftime("%Y-%m-%d"), username)
    return used < quota, used, quota


def usage_count(username):
    return store.usage_add(time.strftime("%Y-%m-%d"), username)


def status(username):
    ok, used, quota = usage_check(username)
    return {"user": username, "role": role(username), "used": used, "quota": quota}
