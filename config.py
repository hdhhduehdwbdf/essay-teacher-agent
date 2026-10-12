# -*- coding: utf-8 -*-
"""
作文老师 Agent · 配置文件
填好模型 KEY 就能跑。支持任意 OpenAI 兼容端点 / DeepSeek / 智谱GLM。
"""

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
# 不填也能跑：涉及事实的问题，分身会如实说无法联网，不会编造
BOCHA_API_KEY = ""

# ============ ③ 行为参数 ============
PERSONA_FILE = "persona.md"           # 人格文件（系统提示词）
KNOWLEDGE_DIR = "knowledge"           # 知识库语料目录（放 .md/.txt 文件）
INDEX_FILE = "index/bm25.pkl"         # 检索索引缓存
HISTORY_ROUNDS = 12                   # 带多少轮会话历史
TOP_K = 5                             # 每次检索命中几条语料片段
SEARCH_RESULTS = 5                    # 每次搜索取几条结果
MAX_OUTPUT_CHARS = 1200               # 输出长度上限（呼应 persona 的简洁要求）
