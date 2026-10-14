# -*- coding: utf-8 -*-
"""鉴权与配额：用户文件 + 签名 cookie token + 每日轮次配额。
数据落盘 my_agent/data/（users.json / usage.json / secret.key），均不进 git。
"""
import base64
import hashlib
import hmac
import json
import os
import secrets
import threading
import time

BASE_DIR = os.path.dirname(os.path.abspath(__file__))
DATA_DIR = os.path.join(BASE_DIR, "data")
USERS_FILE = os.path.join(DATA_DIR, "users.json")
USAGE_FILE = os.path.join(DATA_DIR, "usage.json")
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


def _load(path, default):
    try:
        with open(path, encoding="utf-8") as f:
            return json.load(f)
    except Exception:
        return default


def _save(path, obj):
    _ensure_dir()
    tmp = path + ".tmp"
    with open(tmp, "w", encoding="utf-8") as f:
        json.dump(obj, f, ensure_ascii=False)
    os.replace(tmp, path)


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
    users = _load(USERS_FILE, {})
    if users:
        return
    pw = os.environ.get("ADMIN_PASSWORD") or secrets.token_hex(6)
    salt = secrets.token_hex(8)
    users["admin"] = {"salt": salt, "hash": _hash(pw, salt), "role": "admin",
                      "quota": int(os.environ.get("DAILY_QUOTA", "30")), "created": _now()}
    _save(USERS_FILE, users)
    if not os.environ.get("ADMIN_PASSWORD"):
        print(f"[auth] 初始管理员账号已创建 -> 用户名 admin 密码 {pw}（请尽快在管理台修改）")


def _now():
    return time.strftime("%Y-%m-%d %H:%M:%S")


def users():
    with _LOCK:
        return _load(USERS_FILE, {})


def role(username):
    return users().get(username, {}).get("role", "user")


def create_user(username, password, role="user", quota=None):
    username = str(username or "").strip()
    if not (2 <= len(username) <= 20) or not all(c.isalnum() or c in "-_" for c in username):
        return 400, {"error": "用户名需 2-20 位字母数字-_"}
    if len(str(password or "")) < 6:
        return 400, {"error": "密码至少 6 位"}
    with _LOCK:
        users = _load(USERS_FILE, {})
        if username in users:
            return 400, {"error": "用户名已存在"}
        salt = secrets.token_hex(8)
        users[username] = {"salt": salt, "hash": _hash(str(password), salt),
                           "role": role, "quota": int(quota or os.environ.get("DAILY_QUOTA", "30")),
                           "created": _now()}
        _save(USERS_FILE, users)
    return 200, {"ok": True}


def set_password(username, password):
    if len(str(password or "")) < 6:
        return 400, {"error": "密码至少 6 位"}
    with _LOCK:
        users = _load(USERS_FILE, {})
        if username not in users:
            return 404, {"error": "用户不存在"}
        users[username]["hash"] = _hash(str(password), users[username]["salt"])
        _save(USERS_FILE, users)
    return 200, {"ok": True}


def set_quota(username, quota):
    with _LOCK:
        users = _load(USERS_FILE, {})
        if username not in users:
            return 404, {"error": "用户不存在"}
        users[username]["quota"] = int(quota)
        _save(USERS_FILE, users)
    return 200, {"ok": True}


def delete_user(username):
    if username == "admin":
        return 400, {"error": "不能删除管理员"}
    with _LOCK:
        users = _load(USERS_FILE, {})
        if username not in users:
            return 404, {"error": "用户不存在"}
        del users[username]
        _save(USERS_FILE, users)
    return 200, {"ok": True}


def verify(username, password):
    u = users().get(str(username or "").strip())
    if not u:
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
    with _LOCK:
        u = users().get(username)
        quota = u.get("quota", 30) if u else 30
        today = time.strftime("%Y-%m-%d")
        usage = _load(USAGE_FILE, {})
        if usage.get("date") != today:
            usage = {"date": today, "counts": {}}
        used = usage.get("counts", {}).get(username, 0)
        return used < quota, used, quota


def usage_count(username):
    with _LOCK:
        today = time.strftime("%Y-%m-%d")
        usage = _load(USAGE_FILE, {})
        if usage.get("date") != today:
            usage = {"date": today, "counts": {}}
        c = usage.setdefault("counts", {})
        c[username] = c.get(username, 0) + 1
        _save(USAGE_FILE, usage)
        return c[username]


def status(username):
    ok, used, quota = usage_check(username)
    return {"user": username, "role": role(username), "used": used, "quota": quota}
