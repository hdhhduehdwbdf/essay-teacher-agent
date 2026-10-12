# -*- coding: utf-8 -*-
"""
免费联网搜索：不需要注册、不需要 API KEY，纯标准库 urllib 实现。

引擎链（按顺序尝试，命中足够就停）：
  1. Bing RSS     https://www.bing.com/search?q=...&format=rss   结构化 XML，主力
  2. 百度网页     https://www.baidu.com/s?wd=...                 兜底
  3. 360 搜索     https://www.so.com/s?q=...                     二重兜底

说明：DuckDuckGo / Google / Wikipedia / Jina 在国内网络下不可直连（已实测失败），
所以没有纳入默认链路；如果你有代理环境，可用环境变量 WEB_SEARCH_EXTRA 追加自定义引擎。
"""
import gzip
import json
import os
import re
import urllib.parse
import urllib.request
import xml.etree.ElementTree as ET

UA = ("Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
      "(KHTML, like Gecko) Chrome/122.0.0.0 Safari/537.36")
TIMEOUT = 15


def _fetch(url, data=None, headers=None):
    h = {
        "User-Agent": UA,
        "Accept-Language": "zh-CN,zh;q=0.9,en;q=0.8",
        "Accept-Encoding": "identity",
    }
    h.update(headers or {})
    req = urllib.request.Request(url, data=data, headers=h, method="POST" if data else "GET")
    with urllib.request.urlopen(req, timeout=TIMEOUT) as resp:
        raw = resp.read()
        if resp.headers.get("Content-Encoding") == "gzip":
            raw = gzip.decompress(raw)
    for enc in ("utf-8", "gbk", "gb18030"):
        try:
            return raw.decode(enc)
        except UnicodeDecodeError:
            continue
    return raw.decode("utf-8", errors="ignore")


def _strip_tags(s):
    s = re.sub(r"<[^>]+>", "", s or "")
    s = s.replace("&nbsp;", " ").replace("&amp;", "&").replace("&quot;", '"')
    s = s.replace("&lt;", "<").replace("&gt;", ">").replace("&#39;", "'")
    return re.sub(r"\s+", " ", s).strip()


# ---------------- 引擎 1：Bing RSS（结构化，主力） ----------------
def bing_rss(query, count=8):
    url = "https://www.bing.com/search?" + urllib.parse.urlencode({
        "q": query, "format": "rss", "count": count,
    })
    xml_text = _fetch(url)
    root = ET.fromstring(xml_text.encode("utf-8"))
    out = []
    for item in root.iter("item"):
        title = _strip_tags(item.findtext("title") or "")
        link = (item.findtext("link") or "").strip()
        desc = _strip_tags(item.findtext("description") or "")
        if title and link:
            out.append({"title": title, "url": link, "snippet": desc})
        if len(out) >= count:
            break
    return out


# ---------------- 引擎 2：百度（HTML 兜底） ----------------
def baidu(query, count=8):
    url = "https://www.baidu.com/s?" + urllib.parse.urlencode({"wd": query, "rn": count * 2})
    html = _fetch(url)
    out = []
    for block in re.split(r'<div[^>]+class="result[^"]*c-container[^"]*"', html)[1:]:
        m = re.search(r"<h3[^>]*>[\s\S]*?<a[^>]+href=\"([^\"]+)\"[\s\S]*?>([\s\S]*?)</a>", block)
        if not m:
            continue
        link, title = m.group(1), _strip_tags(m.group(2))
        snip = re.search(r'class="[^"]*content-right[^"]*"[^>]*>([\s\S]*?)</(?:div|span)>', block)
        if not title:
            continue
        out.append({"title": title, "url": link, "snippet": _strip_tags(snip.group(1)) if snip else ""})
        if len(out) >= count:
            break
    return out


# ---------------- 引擎 3：360 搜索（HTML 二重兜底） ----------------
def so360(query, count=8):
    url = "https://www.so.com/s?" + urllib.parse.urlencode({"q": query, "rn": count * 2})
    html = _fetch(url)
    out = []
    for block in re.findall(r'<li[^>]+class="res-list"[^>]*>([\s\S]*?)</li>', html):
        m = re.search(r'<a[^>]+href="([^"]+)"[^>]*>([\s\S]*?)</a>', block)
        if not m:
            continue
        link, title = m.group(1), _strip_tags(m.group(2))
        snip = re.search(r'class="[^"]*res-desc[^"]*"[^>]*>([\s\S]*?)</p>', block)
        if not title or title.startswith("http"):
            continue
        out.append({"title": title, "url": link, "snippet": _strip_tags(snip.group(1)) if snip else ""})
        if len(out) >= count:
            break
    return out


ENGINES = [("baidu", baidu), ("so360", so360), ("bing", bing_rss)]


def search(query, count=8):
    """多引擎汇总检索：每个引擎都试一遍，结果去重合并。

    返回 (引擎名串, 结果列表)。全部失败时引擎名为 None，第二项为错误摘要。
    单引擎挂掉（被反爬/超时）不影响整体出结果。
    """
    extra = os.environ.get("WEB_SEARCH_EXTRA", "").strip()
    engines = list(ENGINES)
    if extra:  # 自定义引擎：JSON 数组，如 [["name","https://host/s?q={q}"], ...]
        try:
            for name, tmpl in json.loads(extra):
                engines.append((name, _make_template_engine(tmpl, count)))
        except Exception:
            pass
    merged, used, errors = [], [], []
    for name, fn in engines:
        hits = []
        try:
            hits = fn(query, count)
        except Exception as e:
            errors.append(f"{name}:{type(e).__name__}")
            try:  # 失败就等一下重试一次（百度这类反爬抖动很常见）
                import time
                time.sleep(0.8)
                hits = fn(query, count)
                errors.pop()
            except Exception:
                hits = []
        if hits:
            used.append(name)
            merged.extend(hits)
        if len(merged) >= count * 2:
            break
    if not merged:
        return None, errors
    dedup, seen = [], set()
    for h in merged:
        key = (h.get("url") or "").strip() or re.sub(r"\W+", "", h.get("title", ""))[:40]
        if not key or key in seen:
            continue
        seen.add(key)
        dedup.append(h)
        if len(dedup) >= count:
            break
    return "+".join(used), dedup


def _make_template_engine(tmpl, count):
    def _run(query, n):
        url = tmpl.replace("{q}", urllib.parse.quote(query))
        html = _fetch(url, headers={"Accept": "application/json, text/html"})
        try:
            data = json.loads(html)
            items = data if isinstance(data, list) else (data.get("results") or data.get("items") or [])
            return [{
                "title": (it.get("title") or "")[:120],
                "url": it.get("url") or it.get("link") or "",
                "snippet": _strip_tags(it.get("snippet") or it.get("content") or "")[:300],
            } for it in items][:n]
        except Exception:
            return []
    return _run


def format_results(hits, limit=5):
    lines = []
    for i, h in enumerate(hits[:limit], 1):
        lines.append(f"{i}. {h['title'][:80]}\n   摘要：{h['snippet'][:300]}\n   来源：{h['url']}")
    return "\n".join(lines)


if __name__ == "__main__":
    import sys
    try:  # Windows 控制台默认 GBK，中文/符号会崩
        sys.stdout.reconfigure(encoding="utf-8", errors="replace")
    except Exception:
        pass
    q = " ".join(sys.argv[1:]) or "叁月聚粮 作文"
    engine, hits = search(q, 8)
    print(f"query={q} engine={engine}")
    if isinstance(hits, list):
        print(format_results(hits))
    else:
        print("全部引擎失败：", hits)
