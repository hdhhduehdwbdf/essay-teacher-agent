# -*- coding: utf-8 -*-
"""
知识库检索：BM25（纯Python实现，零依赖）
- 启动时扫描 knowledge/ 目录，切片、分词、建索引（缓存到 index/bm25.pkl）
- 中文用字级 bigram 分词，无需 jieba，检索质量够用
"""
import os, re, math, pickle

from config import KNOWLEDGE_DIR, INDEX_FILE, TOP_K

CHUNK_SIZE = 500      # 每片字符数
CHUNK_OVERLAP = 80    # 片间重叠


def tokenize(text):
    """中文 bigram + 英文单词混合分词"""
    text = re.sub(r"\s+", " ", text.lower())
    tokens = []
    # 英文/数字词
    for w in re.findall(r"[a-z0-9]+", text):
        tokens.append(w)
    # 去掉英文后做中文 bigram
    cn = re.sub(r"[a-z0-9]+", "", text)
    cn = re.sub(r"[^\u4e00-\u9fff]", " ", cn)
    for seg in cn.split():
        if len(seg) == 1:
            tokens.append(seg)
        else:
            for i in range(len(seg) - 1):
                tokens.append(seg[i:i + 2])
    return tokens


def read_docs():
    """读取知识库目录下所有 md/txt 文件"""
    docs = []
    if not os.path.isdir(KNOWLEDGE_DIR):
        return docs
    for fn in sorted(os.listdir(KNOWLEDGE_DIR)):
        if not fn.lower().endswith((".md", ".txt")):
            continue
        path = os.path.join(KNOWLEDGE_DIR, fn)
        try:
            text = open(path, encoding="utf-8", errors="ignore").read()
        except Exception:
            continue
        if text.strip():
            docs.append((fn, text))
    return docs


def chunk_text(text, size=CHUNK_SIZE, overlap=CHUNK_OVERLAP):
    """按段落优先切片"""
    paras = [p.strip() for p in re.split(r"\n\s*\n", text) if p.strip()]
    chunks, buf = [], ""
    for p in paras:
        if len(buf) + len(p) <= size:
            buf += ("\n" + p if buf else p)
        else:
            if buf:
                chunks.append(buf)
            # 单段超长则硬切
            while len(p) > size:
                chunks.append(p[:size])
                p = p[size - overlap:]
            buf = p
    if buf:
        chunks.append(buf)
    return chunks


class BM25:
    def __init__(self, k1=1.5, b=0.75):
        self.k1, self.b = k1, b
        self.corpus_tokens = []
        self.doc_len = []
        self.df = {}
        self.idf = {}
        self.avgdl = 0.0
        self.meta = []

    def build(self, chunks):
        """chunks: list of (source_filename, chunk_text)"""
        self.meta = [(fn, txt) for fn, txt in chunks]
        self.corpus_tokens = [tokenize(txt) for _, txt in chunks]
        self.doc_len = [len(t) for t in self.corpus_tokens]
        self.avgdl = sum(self.doc_len) / max(len(self.doc_len), 1)
        self.df = {}
        for toks in self.corpus_tokens:
            for t in set(toks):
                self.df[t] = self.df.get(t, 0) + 1
        n = len(self.corpus_tokens)
        self.idf = {t: math.log((n - d + 0.5) / (d + 0.5) + 1) for t, d in self.df.items()}

    def search(self, query, top_k=TOP_K):
        q = tokenize(query)
        scores = [0.0] * len(self.corpus_tokens)
        for i, toks in enumerate(self.corpus_tokens):
            tf = {}
            for t in toks:
                tf[t] = tf.get(t, 0) + 1
            s = 0.0
            for t in q:
                if t not in tf:
                    continue
                idf = self.idf.get(t, 0.0)
                denom = tf[t] + self.k1 * (1 - self.b + self.b * self.doc_len[i] / self.avgdl)
                s += idf * tf[t] * (self.k1 + 1) / denom
            scores[i] = s
        ranked = sorted(range(len(scores)), key=lambda i: scores[i], reverse=True)[:top_k]
        return [(self.meta[i][0], self.meta[i][1], scores[i]) for i in ranked if scores[i] > 0]


_engine = None


def get_engine():
    """单例：优先加载缓存索引"""
    global _engine
    if _engine is not None:
        return _engine
    docs = read_docs()
    if not docs:
        _engine = BM25()
        _engine.build([])
        return _engine
    if os.path.exists(INDEX_FILE):
        try:
            cached_docs = None
            with open(INDEX_FILE, "rb") as f:
                obj = pickle.load(f)
            # 缓存校验：文件列表和大小一致才复用
            cached_docs = obj.get("docs_sig")
            sig = [(fn, os.path.getsize(os.path.join(KNOWLEDGE_DIR, fn))) for fn, _ in docs]
            if cached_docs == sig:
                _engine = obj["engine"]
                return _engine
        except Exception:
            pass
    chunks = []
    for fn, text in docs:
        for c in chunk_text(text):
            chunks.append((fn, c))
    eng = BM25()
    eng.build(chunks)
    _engine = eng
    os.makedirs(os.path.dirname(INDEX_FILE), exist_ok=True)
    with open(INDEX_FILE, "wb") as f:
        pickle.dump({"engine": eng,
                     "docs_sig": [(fn, os.path.getsize(os.path.join(KNOWLEDGE_DIR, fn))) for fn, _ in docs]}, f)
    return _engine


def retrieve(query, top_k=TOP_K):
    """返回检索命中的语料片段列表"""
    eng = get_engine()
    if not eng.meta:
        return []
    hits = eng.search(query, top_k)
    return [{"source": fn, "text": txt, "score": round(sc, 3)} for fn, txt, sc in hits]


if __name__ == "__main__":
    # 自测
    eng = get_engine()
    print(f"索引完成：{len(read_docs())} 个文件，{len(eng.meta)} 个片段")
    if eng.meta:
        for h in retrieve("审题立意方法怎么用"):
            print(f"  [{h['score']}] {h['source'][:40]} :: {h['text'][:60]}...")
