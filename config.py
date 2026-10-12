# -*- coding: utf-8 -*-
"""
作文老师 Agent · 配置文件
填好模型 KEY 就能跑。支持任意 OpenAI 兼容端点 / DeepSeek / 智谱GLM。
"""
import os

# ============ ① 大模型 API（三选一，按填写顺序优先） ============

# 方案A：任意 OpenAI 兼容端点（自定义 base_url + key + 模型名）
CUSTOM_API_KEY = ""            # 例如 "sk-xxxx"
CUSTOM_BASE_URL = ""           # 例如 "https://api.example.com/v1/chat/completions"
CUSTOM_MODEL = ""              # 例如 "glm-4-flash" / "deepseek-chat" 等

# 方案B：DeepSeek（https://platform.deepseek.com 注册获取）
DEEPSEEK_API_KEY = ""
DEEPSEEK_BASE_URL = "https://api.deepseek.com/chat/completions"

# 方案C：智谱GLM（https://open.bigmodel.cn 注册获取）
ZHIPU_API_KEY = ""
ZHIPU_BASE_URL = "https://open.bigmodel.cn/api/paas/v4/chat/completions"

# ============ ② 搜索 API（可选，实现「先验证再作答」） ============
# 博查 API（国内直付，注册送免费额度）：https://open.bochaai.com
# 留空也完全可用：默认走免费免注册引擎链（百度 → 360 → Bing RSS，见 web_search.py）
# 填了则优先用博查（结果更干净），失败会自动退回免费引擎
BOCHA_API_KEY = ""

# ============ ③ 行为参数 ============
PERSONA_FILE = "persona.md"           # 人格文件（系统提示词）
KNOWLEDGE_DIR = "knowledge"           # 知识库语料目录（放 .md/.txt/.json/.csv，可分子目录）
INDEX_FILE = "index/bm25.pkl"         # 关键词检索索引缓存
VECTOR_INDEX_FILE = "index/vectors.pkl"  # 语义向量索引缓存
HISTORY_ROUNDS = 12                   # 带多少轮会话历史
TOP_K = 5                             # 每次检索命中几条语料片段
SEARCH_RESULTS = 5                    # 每次搜索取几条结果
MAX_OUTPUT_CHARS = 1200               # 输出长度上限（呼应 persona 的简洁要求）

# ============ ④ 知识库 RAG（免费语义检索，可选） ============
# 不填 KEY 也能跑：自动退回纯 BM25 关键词检索（零依赖、零配置）。
# 想要语义检索，任选一家免费/带免费额度的向量服务，填 provider + key 即可：
#   siliconflow → BAAI/bge-m3（免费）        https://cloud.siliconflow.cn
#   jina        → jina-embeddings-v3（免费额度）https://jina.ai/embeddings
#   zhipu       → embedding-3（有免费额度）   https://open.bigmodel.cn
#   openai      → 任意 OpenAI 兼容端点（自填 base_url + model）
#   local       → 本地哈希向量（完全离线，语义一般，仅作兜底）
RAG_ENABLE = True                     # 关掉则只用 BM25
EMBEDDING_PROVIDER = os.environ.get("EMBEDDING_PROVIDER", "siliconflow")
EMBEDDING_API_KEY = os.environ.get("SILICONFLOW_API_KEY", "") or os.environ.get("EMBEDDING_API_KEY", "")
EMBEDDING_BASE_URL = ""               # provider=openai 时必填，其它可留空
EMBEDDING_MODEL = os.environ.get("EMBEDDING_MODEL", "BAAI/bge-m3")   # 留空则用该 provider 的默认模型
EMBEDDING_BATCH = 16                  # 每次请求向量化的片段数
RAG_FUSION = "rrf"                    # 融合方式：rrf（倒数排名融合）/ weighted（加权归一）
RAG_WEIGHT_BM25 = 0.4                 # weighted 模式下关键词得分权重（其余给向量）
RAG_CANDIDATES = 20                   # 融合前的候选数量

# 可选：二次精排（免费额度：siliconflow 的 BAAI/bge-reranker-v2-m3）
RAG_RERANK = False
RERANK_BASE_URL = ""                  # 留空则复用 provider 默认端点
RERANK_API_KEY = ""                   # 留空则复用 EMBEDDING_API_KEY
RERANK_MODEL = ""
