# -*- coding: utf-8 -*-
"""
高可用 HTTP 网络层（纯标准库）
- 连接池复用：同一主机的 HTTPS 连接线程安全复用，省去每次 TLS 握手
- 指数退避重试：429 / 5xx / 超时 / 连接失败 自动重试，支持 Retry-After
- 可取消：传入 cancel(threading.Event) 后走「手动读」通道 —— socket 用短超时
  轮询，每轮都看一眼取消标志；一旦被取消就立刻断开连接（服务端会察觉并
  停止生成），抛出 Cancelled。

为什么取消不用 conn.close()/shutdown()：
    Windows 上从另一个线程 close/shutdown 一个正阻塞在 recv 的 socket，
    并不会把 recv 叫醒（实测 Python 3.7.9 无效）。所以这里自己读 socket，
    用 0.25 秒的小超时轮询，保证取消是「立刻」生效的。
"""
import gzip
import http.client
import json
import random
import socket
import ssl
import threading
import time
from urllib.parse import urlsplit


class Cancelled(Exception):
    """请求被主动取消。这不是网络故障，不要走重试。"""


_POOL = {}          # netloc -> {"conns": [...], "lock": Lock}
_POOL_LOCK = threading.Lock()
DEFAULT_RETRIES = 3
DEFAULT_BACKOFF = 1.0   # 首次退避秒数
POLL = 0.25             # 可取消通道的轮询间隔（秒）


# ==================== 连接池（不可取消时的快通道） ====================

def _get_slot(netloc):
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


def _kill(conn):
    try:
        conn.close()
    except Exception:
        pass


# ==================== 可取消通道 ====================

def _dechunk(data):
    """解 Transfer-Encoding: chunked。"""
    out = bytearray()
    i = 0
    while True:
        j = data.find(b"\r\n", i)
        if j < 0:
            break
        try:
            size = int(data[i:j].split(b";")[0].strip() or b"0", 16)
        except Exception:
            break
        if size <= 0:
            break
        out += data[j + 2:j + 2 + size]
        i = j + 2 + size + 2
    return bytes(out)


def _raw_post(sp, body, headers, timeout, cancel):
    """自己走 socket：发请求 + 轮询式收响应，随时可被打断。

    连接头写 Connection: close，所以「收到 EOF」就等于响应结束，
    不必再纠结 Content-Length。
    """
    host = sp.hostname
    port = sp.port or (443 if sp.scheme == "https" else 80)
    path = sp.path or "/"
    if sp.query:
        path += "?" + sp.query

    sock = socket.create_connection((host, port), timeout=min(timeout, 30))
    try:
        if sp.scheme == "https":
            sock = ssl.create_default_context().wrap_socket(sock, server_hostname=host)
        head = ["POST %s HTTP/1.1" % path,
                "Host: %s" % sp.netloc,
                "Connection: close"]
        for k, v in headers.items():
            head.append("%s: %s" % (k, v))
        sock.sendall(("\r\n".join(head) + "\r\n\r\n").encode("utf-8") + body)

        sock.settimeout(POLL)
        buf = bytearray()
        deadline = time.time() + timeout
        while True:
            if cancel is not None and cancel.is_set():
                raise Cancelled("已停止")
            if time.time() > deadline:
                raise socket.timeout("响应超时")
            try:
                chunk = sock.recv(65536)
            except socket.timeout:
                continue
            except OSError:
                if cancel is not None and cancel.is_set():
                    raise Cancelled("已停止")
                raise
            if not chunk:
                break
            buf += chunk
    finally:
        try:
            sock.close()
        except Exception:
            pass

    # ---- 拆响应 ----
    idx = buf.find(b"\r\n\r\n")
    if idx < 0:
        raise RuntimeError("响应不完整")
    head_raw = bytes(buf[:idx]).decode("iso-8859-1", "replace")
    payload = bytes(buf[idx + 4:])
    lines = head_raw.split("\r\n")
    try:
        status = int(lines[0].split(" ")[1])
    except Exception:
        raise RuntimeError("状态行异常: %s" % lines[0][:120])
    hdrs = {}
    for ln in lines[1:]:
        if ":" in ln:
            k, v = ln.split(":", 1)
            hdrs[k.strip().lower()] = v.strip()
    if hdrs.get("transfer-encoding", "").lower().find("chunked") >= 0:
        payload = _dechunk(payload)
    if hdrs.get("content-encoding", "").lower().find("gzip") >= 0:
        try:
            payload = gzip.decompress(payload)
        except Exception:
            pass
    return status, hdrs, payload


def _post_cancellable(sp, body, headers, timeout, retries, backoff, cancel):
    last_exc = None
    for attempt in range(retries + 1):
        if cancel.is_set():
            raise Cancelled("已停止")
        try:
            status, hdrs, raw = _raw_post(sp, body, headers, timeout, cancel)
        except Cancelled:
            raise
        except Exception as e:
            if cancel.is_set():
                raise Cancelled("已停止")
            last_exc = e
            if attempt < retries:
                wait = backoff * (2 ** attempt) + random.uniform(0, 0.5)
                if cancel.wait(wait):
                    raise Cancelled("已停止")
                continue
            raise
        if status < 400:
            return json.loads(raw.decode("utf-8"))
        if status in (429, 500, 502, 503, 504) and attempt < retries:
            wait = float(hdrs.get("retry-after") or 0)
            if wait <= 0:
                wait = backoff * (2 ** attempt) + random.uniform(0, 0.5)
            if cancel.wait(wait):
                raise Cancelled("已停止")
            continue
        raise RuntimeError("HTTP %d: %s" % (status, raw[:300].decode("utf-8", "replace")))
    raise last_exc if last_exc else RuntimeError("request failed")


# ==================== 统一入口 ====================

def post_json(url, payload, headers=None, timeout=60, retries=DEFAULT_RETRIES,
              backoff=DEFAULT_BACKOFF, cancel=None):
    """POST JSON。成功返回 dict，最终失败抛最后异常。

    cancel 传入 threading.Event 时走可取消通道：随时能中断，并真正断开连接。
    """
    sp = urlsplit(url)
    body = json.dumps(payload, ensure_ascii=False).encode("utf-8")
    headers = dict(headers or {})
    headers.setdefault("Content-Type", "application/json")
    headers["Content-Length"] = str(len(body))

    if cancel is not None:
        return _post_cancellable(sp, body, headers, timeout, retries, backoff, cancel)

    netloc = sp.netloc
    secure = sp.scheme == "https"
    path = sp.path or "/"
    if sp.query:
        path += "?" + sp.query

    slot = _get_slot(netloc)
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
                _kill(conn)
                time.sleep(wait)
                continue
            _release(slot, conn)
            raise RuntimeError("HTTP %d: %s" % (resp.status, raw[:300].decode("utf-8", "replace")))
        except RuntimeError:
            raise
        except Exception as e:
            last_exc = e
            _kill(conn)
            if attempt < retries:
                time.sleep(backoff * (2 ** attempt) + random.uniform(0, 0.5))
                continue
    raise last_exc if last_exc else RuntimeError("request failed")
