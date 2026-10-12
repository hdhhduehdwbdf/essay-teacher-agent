# -*- coding: utf-8 -*-
"""
高可用 HTTP 网络层（纯标准库）
- 连接池复用：同一主机的 HTTPS 连接线程安全复用，省去每次 TLS 握手
- 指数退避重试：429 / 5xx / 超时 / 连接失败 自动重试，支持 Retry-After
- 统一超时与失败兜底
"""
import http.client
import json
import random
import threading
import time
from urllib.parse import urlsplit


_POOL = {}          # netloc -> {"conns": [...], "lock": Lock}
_POOL_LOCK = threading.Lock()
DEFAULT_RETRIES = 3
DEFAULT_BACKOFF = 1.0   # 首次退避秒数


def _get_slot(netloc, secure):
    with _POOL_LOCK:
        if netloc not in _POOL:
            _POOL[netloc] = {"conns": [], "lock": threading.Lock()}
        return _POOL[netloc]


def _new_conn(netloc, secure, timeout):
    if secure:
        return http.client.HTTPSConnection(netloc, timeout=timeout)
    return http.client.HTTPConnection(netloc, timeout=timeout)


def _acquire(slot, netloc, secure, timeout):
    with slot["lock"]:
        while slot["conns"]:
            conn = slot["conns"].pop()
            try:
                # 复用前轻量探测：send 无 body 的 HEAD 不行，直接丢弃可疑连接
                if conn.sock is not None:
                    return conn
            except Exception:
                try:
                    conn.close()
                except Exception:
                    pass
        return _new_conn(netloc, secure, timeout)


def _release(slot, conn):
    with slot["lock"]:
        if len(slot["conns"]) < 4:
            slot["conns"].append(conn)
        else:
            try:
                conn.close()
            except Exception:
                pass


def post_json(url, payload, headers=None, timeout=60, retries=DEFAULT_RETRIES,
              backoff=DEFAULT_BACKOFF):
    """POST JSON，带重试与连接复用。成功返回 dict，最终失败抛最后异常。"""
    sp = urlsplit(url)
    netloc = sp.netloc
    secure = sp.scheme == "https"
    path = sp.path or "/"
    if sp.query:
        path += "?" + sp.query
    body = json.dumps(payload, ensure_ascii=False).encode("utf-8")
    headers = dict(headers or {})
    headers.setdefault("Content-Type", "application/json")
    headers["Content-Length"] = str(len(body))

    slot = _get_slot(netloc, secure)
    last_exc = None
    for attempt in range(retries + 1):
        conn = _acquire(slot, netloc, secure, timeout)
        try:
            conn.request("POST", path, body=body, headers=headers)
            resp = conn.getresponse()
            raw = resp.read()
            if resp.status < 400:
                _release(slot, conn)
                return json.loads(raw.decode("utf-8"))
            if resp.status in (429, 500, 502, 503, 504) and attempt < retries:
                wait = float(resp.getheader("Retry-After") or 0)
                if wait <= 0:
                    wait = backoff * (2 ** attempt) + random.uniform(0, 0.5)
                try:
                    conn.close()
                except Exception:
                    pass
                time.sleep(wait)
                continue
            _release(slot, conn)
            raise RuntimeError(f"HTTP {resp.status}: {raw[:300].decode('utf-8', 'replace')}")
        except RuntimeError:
            raise
        except Exception as e:
            last_exc = e
            try:
                conn.close()
            except Exception:
                pass
            if attempt < retries:
                time.sleep(backoff * (2 ** attempt) + random.uniform(0, 0.5))
                continue
    raise last_exc if last_exc else RuntimeError("request failed")
