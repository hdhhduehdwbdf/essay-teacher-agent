# 作文老师 Agent

一个**纯 Python 标准库**实现的作文老师 AI 分身：人格提示词 + 知识库混合 RAG（BM25 + 向量语义检索 + RRF 融合）+ 联网搜索（Function Calling）+ 网页聊天界面，**零第三方依赖**，不用 pip install 任何东西。

## 功能特性

- **人格系统**：`persona.md` 内置一套可定制的「六步教学法」思辨写作框架（理解闸门 → 审题 → 搭框架 → 先破后立 → 素材匹配 → 决策框架），可直接替换成你自己的老师人设
- **知识库 RAG**：把老师语料（.md/.txt/.json/.csv，可分子目录）放进 `knowledge/` 目录，自动建「BM25 关键词 + 向量语义」双路索引并融合排序；回答优先基于检索到的真实语料
- **免费可用的语义检索**：内置 5 种向量方案（SiliconFlow / Jina / 智谱 / 任意 OpenAI 兼容端点 / 离线兜底），只需填一个免费 KEY，无需安装任何库；不填则自动退回纯 BM25
- **联网验证**：通过 Function Calling 接入搜索，涉及事实的表述先搜再答，**默认零配置**——走免费免注册引擎链（百度 → 360 → Bing RSS），填了博查 KEY 则优先用博查、失败自动回退
- **网页界面**：自带聊天网页，实时展示「知识库命中 → 联网搜索 → 生成回答」全过程
- **多模型支持**：任意 OpenAI 兼容端点 / DeepSeek / 智谱 GLM

## 目录结构

```
├── config.py           # 配置：填 API KEY（唯一要改的文件）
├── agent.py            # 命令行入口：交互对话 / 单问单答 / 自检
├── web_server.py       # 网页聊天服务（python web_server.py 后开 http://127.0.0.1:8778）
├── web_chat.html       # 网页前端
├── llm.py              # 模型调用 + Function Calling 循环
├── search_tool.py      # 博查搜索工具（先验证再作答的执行端）
├── web_search.py       # 免费免注册多引擎搜索（百度/360/Bing RSS，零配置可用）
├── knowledge_base.py   # 语料装载 + BM25 索引（纯 Python 实现）
├── embeddings.py       # 向量化层（免费向量服务 / 离线兜底，纯 urllib）
├── rag.py              # 混合检索：双路召回 + RRF 融合 + 可选精排（自带 CLI）
├── persona.md          # 人格提示词模板（可定制）
├── knowledge/          # 知识库语料目录（放 .md/.txt/.json/.csv）
└── index/              # 检索索引缓存（bm25.pkl / vectors.pkl，自动生成）
```

## 快速开始（3步）

**1. 填 KEY**（打开 `config.py`，三选一）：
- 任意 OpenAI 兼容端点：填 `CUSTOM_API_KEY` + `CUSTOM_BASE_URL` + `CUSTOM_MODEL`
- DeepSeek：https://platform.deepseek.com 注册获取
- 智谱 GLM：https://open.bigmodel.cn 注册获取（glm-4-flash 免费）

**2. 自检**：
```bash
python agent.py --test
```

**3. 开聊**：
```bash
python agent.py                # 命令行交互模式
python agent.py "怎么审题"      # 单问模式
python web_server.py           # 网页模式 → 浏览器打开 http://127.0.0.1:8778
```
命令行模式里 `/new` 清空会话记忆，`exit` 退出。

## 充实知识库

把老师的真实语料（课程文案、文章、问答记录等）整理成 `.md` 或 `.txt` 文件放进 `knowledge/` 目录，重启后自动重建索引。

**语料质量决定回答质感**：素材里有的，分身会引用；素材里没有的，它会如实说"这个我没怎么碰过"——这正是人格真实感的来源。

也可以手动构建 / 自测检索效果：
```bash
python rag.py --ingest D:\某目录    # 批量导入语料（保留子目录结构，含 pdf/docx）
python rag.py --build            # 重建索引（含向量）
python rag.py --query "审题立意"  # 命令行看命中结果
python rag.py --stats            # 查看当前检索模式与索引状态
```

## 启用语义检索（免费 RAG）

默认配置已经指向 **SiliconFlow 的 `BAAI/bge-m3`**（中文效果好、免费），你只需要填一个 KEY，**不需要 pip install**。KEY 两种填法，二选一：

- 环境变量：`setx SILICONFLOW_API_KEY "sk-xxx"`（重开终端生效）
- 写进 `config.py`：`EMBEDDING_API_KEY = "sk-xxx"`

没填 KEY 不会报错，自动退回纯 BM25。想换其它免费方案：

| provider | 模型 | 说明 | 申请地址 |
| --- | --- | --- | --- |
| `siliconflow` | `BAAI/bge-m3` | 中文效果好，免费，推荐首选 | https://cloud.siliconflow.cn |
| `jina` | `jina-embeddings-v3` | 有免费额度 | https://jina.ai/embeddings |
| `zhipu` | `embedding-3` | 有免费额度 | https://open.bigmodel.cn |
| `openai` | 自填模型名 | 任意 OpenAI 兼容 `/v1/embeddings` 端点 | — |
| `local` | 本地哈希向量 | 完全离线，语义一般，仅作兜底 | — |

填完 KEY 执行 `python rag.py --build` 生成向量索引（全量向量化一次，之后按「语料签名 + 模型」缓存复用）。此后检索链路为：
BM25 关键词召回 + 向量语义召回 → RRF 融合排序 → 可选 `BAAI/bge-reranker-v2-m3` 精排。
当前生效的模式可以在 `python agent.py --test` 或网页版的「检索模式」提示里看到。
想临时切换而不改代码，可用环境变量覆盖：`EMBEDDING_PROVIDER`、`EMBEDDING_API_KEY`、`EMBEDDING_MODEL` 等。

## 工作原理

每次提问的处理链路：

```
用户提问
   ↓
① 混合 RAG 检索 knowledge/（BM25 + 向量 → RRF 融合）→ 命中 TOP5 语料片段
   ↓
② 组装 system prompt（persona + 检索片段 + 使用规则）+ 最近12轮历史
   ↓
③ 发给大模型 → 涉及事实时模型点名 web_search 工具
   ↓
④ 本地代码真调博查 API → 结果回传模型
   ↓
⑤ 模型基于 persona + 语料 + 搜索结果生成最终回答
```

## 定制自己的老师分身

1. 编辑 `persona.md`：替换「老师X」的背景、价值观、口头禅、教学法（结构已给全，填内容即可）
2. `knowledge/` 放入该老师的真实语料
3. `config.py` 填上你的 API KEY

## 常见问题

- **答非所问/不像老师** → `knowledge/` 目录还是空的。放语料后删掉 `index/bm25.pkl`、`index/vectors.pkl` 重建
- **检索模式还是 BM25** → `EMBEDDING_PROVIDER` / `EMBEDDING_API_KEY` 没填对；向量服务调用失败时也会自动退回 BM25（日志会给出原因）
- **不联网搜索** → 多为问题不涉事实（模型自行判断是否调用）；免费引擎链可用性可在 `python agent.py --test` 里看到连通性测试结果
- **想换模型** → 改 `config.py` 的对应字段
- **超长回答** → 已有 `MAX_OUTPUT_CHARS` 提示机制；想硬限制就在 `llm.py` 里截断

## 免责声明

本项目仅为教学演示用途的通用框架。`persona.md` 中的教学法为通用思辨写作方法，请勿将模板用于冒充任何真实人物；构建真实人物的分身请确保获得本人授权。
