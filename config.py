# -*- coding: utf-8 -*-
"""
叁月聚粮分身 · 配置文件
填好两个 KEY 就能跑。支持 DeepSeek 或 智谱GLM，二选一。
"""
import os

# ============ ① 大模型 API ============
# 推荐做法：复制 config_local.example.py 为 config_local.py，把 KEY 填在里面（该文件已被 .gitignore 排除，不会提交）
# 也支持环境变量覆盖：CUSTOM_API_KEY / CUSTOM_BASE_URL / CUSTOM_MODEL
CUSTOM_API_KEY = os.environ.get("CUSTOM_API_KEY", "")
CUSTOM_BASE_URL = os.environ.get("CUSTOM_BASE_URL", "")
CUSTOM_MODEL = os.environ.get("CUSTOM_MODEL", "")

# 本地私密配置（可选）：存在则覆盖上面的值，绝不提交
_local = os.path.join(os.path.dirname(os.path.abspath(__file__)), "config_local.py")
if os.path.exists(_local):
    with open(_local, encoding="utf-8") as _f:
        exec(compile(_f.read(), _local, "exec"), globals())

# 备选方案（填了 CUSTOM 的就不用动这些）：
# DeepSeek: https://platform.deepseek.com
DEEPSEEK_API_KEY = ""
# 智谱: https://open.bigmodel.cn
ZHIPU_API_KEY = ""

# 生效模型（按上面自动选择）
MODEL = CUSTOM_MODEL

# ============ ② 搜索 API（可选，实现「先验证再作答」） ============
# 留空即可：默认走免费免注册引擎链（百度 → 360 → Bing RSS，见 web_search.py），零配置可联网
# 想用更干净的结果：博查 API（国内直付，注册送免费额度）
#   申请：https://open.bochaai.com  → API Key管理
BOCHA_API_KEY = ""

# ============ ③ 行为参数 ============
PERSONA_FILE = "persona.md"        # 人格文件
KNOWLEDGE_DIR = "knowledge"           # 知识库语料目录（放 .md/.txt/.json/.csv，可分子目录）
INDEX_FILE = "index/bm25.pkl"         # 关键词检索索引缓存
VECTOR_INDEX_FILE = "index/vectors.pkl"  # 语义向量索引缓存
HISTORY_ROUNDS = 12                   # 带多少轮会话历史
TOP_K = 10                             # 每次检索命中几条语料片段
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
RAG_FUSION = "weighted"               # 融合方式：rrf（倒数排名融合）/ weighted（加权归一）
RAG_WEIGHT_BM25 = 0.5                 # weighted 模式下关键词得分权重（其余给向量）；扫权平台点见 eval/sweep_weights.json
RAG_CANDIDATES = 20                   # 融合前的候选数量
                                      # 2026-10-13：旧 12 条按开关实验删除（净伤 2 题），
                                      # 换为 5 条经逐条验证的新词条（各救回 1 题、零误伤，BM25 路 +5 题）。

# 可选：二次精排（免费额度：siliconflow 的 BAAI/bge-reranker-v2-m3）
RAG_RERANK = False
RERANK_BASE_URL = ""                  # 留空则复用 provider 默认端点
RERANK_API_KEY = ""                   # 留空则复用 EMBEDDING_API_KEY
RERANK_MODEL = ""
