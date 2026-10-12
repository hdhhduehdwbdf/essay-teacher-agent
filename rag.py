# -*- coding: utf-8 -*-
"""
知识库 RAG：BM25 关键词检索 + 向量语义检索，融合排序（纯标准库，零 pip 依赖）。

设计参考的开源实现（思路，不是直接依赖）：
  - bm25s / rank_bm25   → 关键词召回基线（本项目自带 BM25 实现）
  - RRF（Reciprocal Rank Fusion，Cormack et al.）→ 多路召回融合
  - langchain / llama_index 的 Retriever 抽象 → retrieve(query, top_k) 统一接口

用法：
  python rag.py --ingest <目录>     # 把某个目录里的语料批量导入 knowledge/
  python rag.py --build           # 构建/刷新索引（含向量）
  python rag.py --query "审题立意"  # 命令行检索测试
  python rag.py --stats           # 查看当前检索模式与索引状态
未配置任何向量服务时自动退回纯 BM25，不影响原有流程。
"""
import json
import os
import pickle
import shutil
import sys
import urllib.request

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

import config
import embeddings
import knowledge_base as kb

RRF_K = 60  # RRF 平滑常数


def _docs_sig(docs):
    return [(fn, os.path.getsize(os.path.join(kb.KNOWLEDGE_DIR, fn))) for fn, _ in docs]


def _cfg(name, default=None):
    return getattr(config, name, default)



class HybridRetriever:
    """两路召回（BM25 + 向量）→ 融合 → 可选精排"""

    def __init__(self, bm25_engine, vectors=None):
        self.bm25 = bm25_engine
        self.vectors = vectors or []
        self.has_vectors = bool(self.vectors) and len(self.vectors) == len(self.bm25.meta)

    # ---- 单路召回 ----
    def _bm25_indexed(self, query, n):
        q = kb.tokenize(query)
        scores = []
        for i, toks in enumerate(self.bm25.corpus_tokens):
            tf = {}
            for t in toks:
                tf[t] = tf.get(t, 0) + 1
            s = 0.0
            for t in q:
                if t not in tf:
                    continue
                idf = self.bm25.idf.get(t, 0.0)
                denom = tf[t] + self.bm25.k1 * (1 - self.bm25.b + self.bm25.b * self.bm25.doc_len[i] / self.bm25.avgdl)
                s += idf * tf[t] * (self.bm25.k1 + 1) / denom
            scores.append((i, s))
        scores.sort(key=lambda x: x[1], reverse=True)
        return scores[:n]

    def _vector_ranked(self, query, n):
        if not self.has_vectors:
            return []
        qv = embeddings.embed_query(query)
        sims = [(i, embeddings.cosine(qv, v)) for i, v in enumerate(self.vectors)]
        sims.sort(key=lambda x: x[1], reverse=True)
        return sims[:n]

    # ---- 融合 ----
    def _fuse(self, bm25_ranked, vec_ranked, top_k):
        mode = (_cfg("RAG_FUSION", "rrf") or "rrf").lower()
        if mode == "weighted":
            return self._fuse_weighted(bm25_ranked, vec_ranked, top_k)
        # 倒数排名融合：对两路排名各算 1/(k+rank)，与打的绝对分数量纲无关
        fused = {}
        for rank, (i, _) in enumerate(bm25_ranked, 1):
            fused[i] = fused.get(i, 0.0) + 1.0 / (RRF_K + rank)
        for rank, (i, _) in enumerate(vec_ranked, 1):
            fused[i] = fused.get(i, 0.0) + 1.0 / (RRF_K + rank)
        return sorted(fused.items(), key=lambda x: x[1], reverse=True)[:top_k]

    def _fuse_weighted(self, bm25_ranked, vec_ranked, top_k):
        w = float(_cfg("RAG_WEIGHT_BM25", 0.4))
        bmax = max([s for _, s in bm25_ranked], default=0.0) or 1.0
        vmax = max([s for _, s in vec_ranked], default=0.0) or 1.0
        fused = {}
        for i, s in bm25_ranked:
            fused[i] = fused.get(i, 0.0) + w * (s / bmax)
        for i, s in vec_ranked:
            fused[i] = fused.get(i, 0.0) + (1 - w) * (s / vmax)
        return sorted(fused.items(), key=lambda x: x[1], reverse=True)[:top_k]

    # ---- 对外接口 ----
    def retrieve(self, query, top_k=None):
        top_k = top_k or int(_cfg("TOP_K", 5))
        cand = int(_cfg("RAG_CANDIDATES", 20) or 20)
        cand = max(cand, top_k)
        bm25_ranked = self._bm25_indexed(query, cand)
        vec_ranked = self._vector_ranked(query, cand)
        ranked = self._fuse(bm25_ranked, vec_ranked, cand)
        results = [
            {"source": self.bm25.meta[i][0], "text": self.bm25.meta[i][1], "score": round(sc, 4)}
            for i, sc in ranked
        ]
        if _cfg("RAG_RERANK", False) and results:
            results = _rerank(query, results) or results
        return results[:top_k]

    def stats(self):
        return {
            "chunks": len(self.bm25.meta),
            "vectors": len(self.vectors) if self.has_vectors else 0,
            "mode": embeddings.describe(),
        }


# ---------------- 可选二次精排（免费额度：siliconflow BAAI/bge-reranker-v2-m3） ----------------
def _rerank(query, results):
    model = _cfg("RERANK_MODEL", "").strip()
    if not model:
        return None
    p = embeddings.provider()
    url = _cfg("RERANK_BASE_URL", "").strip()
    if not url:
        if p == "siliconflow":
            url = "https://api.siliconflow.cn/v1/rerank"
        elif p == "jina":
            url = "https://api.jina.ai/v1/rerank"
        else:
            return None
    key = _cfg("RERANK_API_KEY", "").strip() or _cfg("EMBEDDING_API_KEY")
    docs = [r["text"] for r in results]
    try:
        req = urllib.request.Request(
            url,
            data=json.dumps({"model": model, "query": query, "documents": docs, "top_n": len(docs)}).encode("utf-8"),
            headers={"Authorization": f"Bearer {key}", "Content-Type": "application/json"},
            method="POST",
        )
        with urllib.request.urlopen(req, timeout=30) as resp:
            data = json.loads(resp.read().decode("utf-8"))
        out = []
        for it in data.get("results", []):
            src = results[it["index"]]
            src["score"] = round(float(it.get("relevance_score", 0.0)), 4)
            src["reranked"] = True
            out.append(src)
        return out or None
    except Exception:
        return None


# ---------------- 索引构建与缓存 ----------------
_retriever = None


def build_vectors(docs, chunks, force=False):
    """向量索引缓存：语料签名 + 模型都一致才复用"""
    path = _cfg("VECTOR_INDEX_FILE", "index/vectors.pkl")
    sig = _docs_sig(docs)
    model = embeddings.model_name()
    if not force and os.path.exists(path):
        try:
            with open(path, "rb") as f:
                obj = pickle.load(f)
            if obj.get("sig") == sig and obj.get("model") == model and len(obj.get("vectors", [])) == len(chunks):
                return obj["vectors"]
        except Exception:
            pass
    texts = [c[1] for c in chunks]
    vectors = embeddings.embed_texts(texts)
    os.makedirs(os.path.dirname(path), exist_ok=True)
    with open(path, "wb") as f:
        pickle.dump({"model": model, "sig": sig, "vectors": vectors}, f)
    return vectors


def get_retriever(force_vector=False):
    """单例：BM25 索引 + 向量索引"""
    global _retriever
    if _retriever is not None and not force_vector:
        return _retriever
    docs = kb.read_docs()
    # 复用 knowledge_base 的 BM25 单例（含 pickle 缓存），避免重复切片
    eng = kb.get_engine()
    chunks = eng.meta
    vectors = None
    if _cfg("RAG_ENABLE", True) and embeddings.available() and chunks:
        try:
            vectors = build_vectors(docs, chunks, force=force_vector)
        except Exception as e:
            print(f"[RAG] 向量化失败，退回 BM25：{e}")
            vectors = None
    _retriever = HybridRetriever(eng, vectors)
    return _retriever


def retrieve(query, top_k=None):
    return get_retriever().retrieve(query, top_k)


def ingest(src, dest=None):
    """把任意目录里的语料批量导入 knowledge/（保留子目录结构）"""
    dest = dest or kb.KNOWLEDGE_DIR
    dest_abs = os.path.abspath(dest)
    n = 0
    for root, _dirs, files in os.walk(src):
        for fn in sorted(files):
            low = fn.lower()
            if fn.startswith(".") or not low.endswith(kb.TEXT_EXTS + kb.BINARY_EXTS):
                continue
            path = os.path.join(root, fn)
            rel = os.path.relpath(path, src)
            out = os.path.join(dest_abs, rel)
            if os.path.abspath(out) == os.path.abspath(path):
                continue
            os.makedirs(os.path.dirname(out), exist_ok=True)
            shutil.copy2(path, out)
            n += 1
    return n


def _key_hint():
    if embeddings.provider() in ("siliconflow", "jina", "zhipu", "openai") and not embeddings.available():
        return (f"\n[提示] 未检测到向量服务 KEY，当前只用 BM25。\n"
                f"       申请 siliconflow 免费 KEY（https://cloud.siliconflow.cn），然后两种填法二选一：\n"
                f"       ① 设环境变量 SILICONFLOW_API_KEY=sk-xxx\n"
                f"       ② 在 config.py 里改 EMBEDDING_API_KEY = \"sk-xxx\"")
    return ""


def main():
    argv = sys.argv[1:]
    if "--stats" in argv or not argv:
        docs = kb.read_docs()
        r = get_retriever()
        s = r.stats()
        print("=" * 56)
        print(f"知识库文件：{len(docs)} 个")
        print(f"语料片段：{s['chunks']} 片")
        print(f"向量索引：{s['vectors']} 条")
        print(f"检索模式：{s['mode']}")
        print("=" * 56)
        print(_key_hint())
        return
    if "--ingest" in argv:
        i = argv.index("--ingest")
        src = argv[i + 1] if len(argv) > i + 1 else ""
        if not src or not os.path.isdir(src):
            print("[错误] 用法：python rag.py --ingest <语料所在目录>")
            return
        n = ingest(src)
        print(f"[导入] 从 {src} 复制 {n} 个语料文件到 {os.path.abspath(kb.KNOWLEDGE_DIR)}/")
        force = "--no-vector" not in argv
        if force and embeddings.available():
            r = get_retriever(force_vector=True)
            print(f"[完成] {r.stats()['chunks']} 个片段，模式：{r.stats()['mode']}")
        else:
            print(f"[完成] 已导入，执行 python rag.py --build 可重建索引。{_key_hint()}")
        return
    if "--build" in argv:
        docs = kb.read_docs()
        if not docs:
            print(f"[提示] {kb.KNOWLEDGE_DIR}/ 下还没有语料，把 .md/.txt/.json/.csv 放进去再构建。")
            print(_key_hint())
            return
        r = get_retriever(force_vector=True)
        print(f"[完成] {len(docs)} 个文件 / {r.stats()['chunks']} 个片段，模式：{r.stats()['mode']}")
        print(_key_hint())
        return
    if "--query" in argv:
        i = argv.index("--query")
        q = argv[i + 1] if len(argv) > i + 1 else ""
        for h in retrieve(q):
            print(f"[{h['score']}] {h['source']} :: {h['text'][:80]}...")
        return
    print(__doc__)


if __name__ == "__main__":
    main()
