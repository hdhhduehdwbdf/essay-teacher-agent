# -*- coding: utf-8 -*-
"""
向量化层（embedding）：纯标准库 urllib 实现，无需 pip 安装任何东西。

免费可用方案（在 config.py 里填 provider + key）：
  siliconflow  BAAI/bge-m3                免费，中文效果好，推荐首选
  jina         jina-embeddings-v3         有免费额度
  zhipu        embedding-3                有免费额度
  openai       任意 OpenAI 兼容 /v1/embeddings 端点
  local        本地哈希向量（离线兜底，无语义模型，仅保证流程可跑）

未配置 key 时 available() 返回 False，检索自动退回 BM25。
"""
import json
import math
import os
import re
import urllib.error
import urllib.request

import config

PROVIDERS = {
    "siliconflow": {
        "url": "https://api.siliconflow.cn/v1/embeddings",
        "model": "BAAI/bge-m3",
    },
    "jina": {
        "url": "https://api.jina.ai/v1/embeddings",
        "model": "jina-embeddings-v3",
    },
    "zhipu": {
        "url": "https://open.bigmodel.cn/api/paas/v4/embeddings",
        "model": "embedding-3",
    },
}


def _cfg(name, default=""):
    """优先读环境变量（便于部署时切换），否则读 config.py"""
    env = os.environ.get(name)
    if env is not None and env != "":
        if env.strip().lower() in ("0", "false", "no", "off"):
            return False
        if env.strip().lower() in ("1", "true", "yes", "on"):
            return True
        return env
    return getattr(config, name, default)


def provider():
    return (_cfg("EMBEDDING_PROVIDER") or "").strip().lower()


def model_name():
    p = provider()
    if not p:
        return ""
    if _cfg("EMBEDDING_MODEL").strip():
        return _cfg("EMBEDDING_MODEL").strip()
    if p == "openai":
        return _cfg("EMBEDDING_MODEL").strip()
    if p == "local":
        return "local-hash512"
    return PROVIDERS.get(p, {}).get("model", "")


def available():
    """是否具备语义检索能力"""
    p = provider()
    if not p or p not in ("siliconflow", "jina", "zhipu", "openai", "local"):
        return False
    if p == "local":
        return True
    if not _cfg("EMBEDDING_API_KEY"):
        return False
    if p == "openai" and not _cfg("EMBEDDING_BASE_URL"):
        return False
    return bool(model_name())


def describe():
    """给日志/前端展示用的模式说明"""
    if not _cfg("RAG_ENABLE", True):
        return "BM25（RAG 已关闭）"
    p = provider()
    if not available():
        hint = f"EMBEDDING_PROVIDER={p or '未配置'}"
        return f"BM25（未启用语义检索：{hint}）"
    return f"BM25 + {p}:{model_name()}"


def _endpoint():
    p = provider()
    base = _cfg("EMBEDDING_BASE_URL").strip()
    if p == "openai":
        if base.endswith("/embeddings"):
            return base
        return base.rstrip("/") + "/embeddings"
    if base:
        return base if base.endswith("/embeddings") else base.rstrip("/") + "/embeddings"
    return PROVIDERS.get(p, {}).get("url", "")


def _post(url, payload, key, timeout=60):
    req = urllib.request.Request(
        url,
        data=json.dumps(payload).encode("utf-8"),
        headers={"Authorization": f"Bearer {key}", "Content-Type": "application/json"},
        method="POST",
    )
    with urllib.request.urlopen(req, timeout=timeout) as resp:
        return json.loads(resp.read().decode("utf-8"))


def _parse_vectors(data):
    items = data.get("data")
    if not isinstance(items, list):
        raise RuntimeError(f"返回格式异常：{str(data)[:200]}")
    items = sorted(items, key=lambda x: x.get("index", 0))
    return [list(map(float, it["embedding"])) for it in items]


def embed_texts(texts):
    """批量向量化。失败时抛异常，由上层降级回 BM25。"""
    if not texts:
        return []
    if provider() == "local":
        return [_local_vector(t) for t in texts]

    url = _endpoint()
    key = _cfg("EMBEDDING_API_KEY")
    model = model_name()
    batch = max(1, int(_cfg("EMBEDDING_BATCH", 16) or 16))
    out = []
    for i in range(0, len(texts), batch):
        part = texts[i:i + batch]
        payload = {"model": model, "input": part}
        if provider() in ("jina", "openai"):
            payload["encoding_format"] = "float"
        data = _post(url, payload, key)
        vecs = _parse_vectors(data)
        if len(vecs) != len(part):
            raise RuntimeError(f"向量条数不匹配：期望 {len(part)} 实得 {len(vecs)}")
        out.extend(vecs)
    return out


def embed_query(text):
    return embed_texts([text])[0]


# ---------------- 本地兜底向量（离线可用，不依赖模型） ----------------
_DIM = 512


def _local_vector(text):
    """哈希 + 字/词特征 + L2 归一化：维度固定，能跑通语义相似度的近似版本。
    质量远不如真模型，但保证离线也能验证混合检索链路。"""
    vec = [0.0] * _DIM
    text = (text or "").lower()
    grams = []
    for w in re.findall(r"[a-z0-9]+", text):
        grams.append(w)
    for seg in re.findall(r"[\u4e00-\u9fff]+", text):
        grams.append(seg)
        grams.extend(seg[i:i + 2] for i in range(len(seg) - 1))
        grams.extend(seg[i:i + 3] for i in range(len(seg) - 2))
    for g in grams:
        h = _stable_hash(g)
        idx = h % _DIM
        vec[idx] += 1.0
    norm = math.sqrt(sum(v * v for v in vec)) or 1.0
    return [v / norm for v in vec]


def _stable_hash(s):
    """固定哈希（不随进程盐值变化），保证索引和查询一致"""
    h = 2166136261
    for ch in s:
        h ^= ord(ch)
        h = (h * 16777619) & 0xFFFFFFFF
    return h


def cosine(a, b):
    if not a or not b:
        return 0.0
    n = min(len(a), len(b))
    dot = sum(a[i] * b[i] for i in range(n))
    na = math.sqrt(sum(x * x for x in a))
    nb = math.sqrt(sum(x * x for x in b))
    if not na or not nb:
        return 0.0
    return dot / (na * nb)


if __name__ == "__main__":
    print("可用性：", available())
    print("当前模式：", describe())
    if available():
        vs = embed_texts(["审题立意怎么练", "作文开头怎么写"])
        print("维度：", len(vs[0]), "相似度：", round(cosine(vs[0], vs[1]), 4))
