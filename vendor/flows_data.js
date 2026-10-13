const $ = (h) => document.getElementById('root').insertAdjacentHTML('beforeend', h);
const SEC = (t, i, inner) => $(`<section class="pt-2"><h2 class="text-lg font-bold mb-4 flex items-center gap-2"><i data-lucide="${i}" class="w-5 h-5 text-amber-400"></i>${t}</h2>${inner}</section>`);
const ARROW = `<div class="flex items-center gap-2 pl-4"><i data-lucide="arrow-down" class="w-4 h-4 text-slate-500"></i></div>`;
const CARD = (icon, title, inner, grid) => `<div class="bg-slate-900 rounded-xl border-2 border-slate-700 p-4"><p class="text-xs text-slate-400 mb-3 flex items-center gap-1"><i data-lucide="${icon}" class="w-4 h-4"></i>${title}</p><div class="${grid}">${inner}</div></div>`;

// ── 1. 全景架构 ──
const pages = [["message-circle","/ 对话","chat.html"],["layout-dashboard","/admin 管理台","admin.html"],["workflow","/rag 原理","rag.html"],["rocket","/star 复盘","star.html"],["git-fork","/threads 线程","threads.html"]];
const layers = [
  ["monitor","① 前端薄壳 frontend/（纯 fetch，零业务逻辑）",
    pages.map(p=>`<div class="bg-slate-950 rounded-lg border border-slate-700 py-3"><i data-lucide="${p[0]}" class="w-4 h-4 text-amber-400 mx-auto mb-1"></i>${p[1]}<br><span class="text-[9px] text-slate-500">${p[2]}</span></div>`).join(''),
    "grid grid-cols-5 gap-3 text-center text-[11px]"],
  ["route","② web_server.py 路由层（waitress 8 线程 · 声明式路由表）",
    `<div class="bg-slate-950 rounded-lg border border-slate-700 p-3 font-mono text-[10px] text-slate-400">GET_ROUTES / POST_ROUTES：路径 → api 函数</div>`, ""],
  ["cpu","③ api.py 业务层 + 支撑模块",
    [["message-circle","对话","chat / search · agent.build_messages<br>llm 工具循环","text-amber-400"],["database","知识库","knowledge_base<br>embeddings / rag · 增删改+自动重建","text-emerald-400"],["brain","记忆","memory.py<br>抽取/合并/过期/检索","text-purple-400"],["server","基础设施","sessions 落盘<br>web_search 免费链 · config / net","text-sky-400"]]
    .map(x=>`<div class="bg-slate-950 rounded-lg border border-slate-700 p-3"><p class="${x[3]} font-semibold text-xs mb-1">${x[1]}</p><p class="text-slate-500">${x[2]}</p></div>`).join(''),
    "grid grid-cols-4 gap-3 text-[11px]"],
  ["hard-drive","④ 持久化层",
    [["book-open","knowledge/ 87 文件"],["database","index/ 检索索引"],["scroll-text","sessions/ 会话"],["brain","memory/ 长期记忆"],["archive","backup/ 自动备份"]]
    .map(x=>`<div class="bg-slate-950 rounded-lg border border-slate-700 py-3"><i data-lucide="${x[0]}" class="w-4 h-4 text-slate-300 mx-auto mb-1"></i>${x[1]}</div>`).join(''),
    "grid grid-cols-5 gap-3 text-center text-[11px]"],
];
let pano = layers.map(l => CARD(l[0], l[1], l[2], l[3])).join(ARROW);
SEC("全景架构：四层结构", "map", `<div class="space-y-3">${pano}</div>`);

// ── 2. 一次提问的完整流程 ──
const FLOW = [
  ["①","idle 检查","worker 同步","sky","超 30 分钟没聊 → 先把上场会话的新消息压成 60 字摘要存入记忆。发生时你要等 1~2 秒：结果必须先有，所以没法后台。"],
  ["②","组装材料","worker · 纯本地","emerald","混合 RAG 检索 TOP5 + 记忆注入（画像全量 + BM25 TOP3 ≤500 字）+ persona + 最近 12 轮历史。毫秒级，不调 LLM。"],
  ["③","模型回答","worker · LLM","amber","工具循环 1~4 次：可直接答（1 次）；要查事实就调 web_search 免费引擎链，查完再想，4 次封顶。"],
  ["④","返回回复","worker 释放","emerald","回复发回浏览器，等待结束，worker 归还线程池接下一个请求。"],
  ["⑤","后台沉淀","daemon 线程","purple","每满 24 条消息触发记忆抽取（独立 LLM 调用 ≤5 条），EXTRACT_LOCK 防重入。用户完全无感。"],
];
const CW = {sky:"text-sky-400", amber:"text-amber-400", emerald:"text-emerald-400", purple:"text-purple-400"};
SEC("一次提问的完整流程（①→⑤）", "timer",
  `<div class="space-y-3">` + FLOW.map(f=>`
    <div class="bg-slate-900 rounded-xl border border-slate-800 p-4 flex gap-4">
      <span class="w-10 h-10 rounded-lg bg-slate-800 flex items-center justify-center font-bold ${CW[f[3]]} shrink-0">${f[0]}</span>
      <div class="flex-1"><div class="flex items-center gap-2 mb-1"><p class="font-semibold text-sm">${f[1]}</p><span class="text-[10px] px-2 py-0.5 rounded-full bg-slate-800 text-slate-400">${f[2]}</span></div>
      <p class="text-xs text-slate-400 leading-relaxed">${f[4]}</p></div>
    </div>`).join('') + `</div>`);

// ── 2. 工具循环 ──
SEC("工具循环：为什么一次提问可能调 1~4 次模型", "repeat",
  `<div class="bg-slate-900 rounded-xl border border-slate-800 p-4 font-mono text-[11px] text-slate-400 leading-loose">
    第1次 llm.chat → 模型直接答？结束(1次) ── 否 → 本地执行 web_search → 结果塞回对话<br>
    第2次 → 答？结束(2次) ── 否 → 再查<br>
    第3次 → 答？结束(3次) ── 否<br>
    第4次 chat(use_tools=False) 强制禁止工具，给最终答案（封顶）
  </div>
  <p class="text-xs text-slate-500 mt-3">普通问题 1 次；涉及事实核查 2 次居多；串行调用，不是并发。</p>`);

// ── 3. RAG 管线 ──
const RAG = [
  ["file-text","1. 语料层","87 个清洗后文件，6 类分目录；增删改自动重建索引"],
  ["scissors","2. 切片","按段落 500 字 + 80 重叠，签名缓存命中则不重建"],
  ["search","3. BM25 路","关键词检索 + 12 组同义词扩展，只扩查询不改语料"],
  ["sparkles","4. 向量路","BAAI/bge-m3 免费语义；没 KEY 自动降级纯 BM25"],
  ["shuffle","5. 融合","RRF 倒数排名融合两路，可选 rerank 精排"],
  ["inbox","6. 注入","TOP5 片段带来源标注拼进 system prompt"],
];
SEC("RAG 检索管线", "search",
  `<div class="grid grid-cols-6 gap-3 text-center text-[11px]">` + RAG.map(r=>`
    <div class="bg-slate-900 rounded-xl border border-slate-800 p-3">
      <i data-lucide="${r[0]}" class="w-4 h-4 text-emerald-400 mx-auto mb-2"></i>
      <p class="font-semibold text-xs mb-1">${r[1]}</p>
      <p class="text-[10px] text-slate-500 leading-relaxed">${r[2]}</p>
    </div>`).join('') + `</div>`);

// ── 4. 记忆管线 ──
const MEM = [
  ["user","画像 profile","身份/年级/目标/弱项<br>全量注入 · 上限 20","text-emerald-400"],
  ["sliders","偏好 preference","讲解深度、要不要例子<br>BM25 按需 · 上限 20","text-sky-400"],
  ["bookmark","事实 fact","带时间戳 · 6 个月未确认过期<br>上限 50","text-amber-400"],
  ["scroll-text","摘要 summary","压缩 60 字 · 只存新消息<br>上限 100","text-slate-400"],
];
const MECH = ["git-merge|合并更新：相似条目改写原条目，不追加矛盾","search|BM25 小索引：按当前问题取 TOP3 相关记忆","scissors|容量淘汰：超上限按最旧+低置信删除"];
SEC("长期记忆管线", "brain",
  `<div class="grid grid-cols-4 gap-3 text-center text-[11px] mb-3">` + MEM.map(m=>`
    <div class="bg-slate-900 rounded-xl border border-slate-800 p-3">
      <i data-lucide="${m[0]}" class="w-4 h-4 ${m[3]} mx-auto mb-2"></i>
      <p class="font-semibold text-xs mb-1">${m[1]}</p>
      <p class="text-[10px] text-slate-500">${m[2]}</p></div>`).join('') + `</div>
    <div class="grid grid-cols-3 gap-3 text-[11px]">` + MECH.map(m=>{const [i,d]=m.split('|');return `<div class="bg-slate-900 rounded-lg border border-slate-800 p-2.5 flex items-center gap-2"><i data-lucide="${i}" class="w-4 h-4 text-purple-400 shrink-0"></i>${d}</div>`;}).join('') + `</div>`);

// ── 5. 线程与锁 ──
const LOCKS = [
  ["lock","CHAT_LOCK","/api/chat 同时只有 1 个，防止两请求交叉写 HISTORY 串上下文"],
  ["brain","EXTRACT_LOCK","抽取/摘要互斥，daemon 线程不会重复记同一批消息"],
  ["hard-drive","SESSION_LOCK","sessions/*.json 写入互斥，落盘不写坏"],
  ["mouse-pointer-click","前端 loading 锁","发送中禁用按钮，前端最多 1 个在途请求"],
];
SEC("线程模型与四道锁", "git-fork",
  `<p class="text-xs text-slate-400 mb-3">waitress 8 worker 线程并行接客；聊天串行（锁内），管理台/搜索/静态页与聊天真并行。三类 LLM 调用：<b class="text-amber-400">对话本体</b>在 worker（用户要等）、<b class="text-purple-400">记忆抽取</b>在 daemon 后台（无感）、<b class="text-sky-400">会话摘要</b>在 worker 开头（秒级）。</p>
  <div class="grid grid-cols-4 gap-3">` + LOCKS.map(l=>`
    <div class="bg-slate-900 rounded-xl border border-slate-800 p-4">
      <i data-lucide="${l[0]}" class="w-5 h-5 text-emerald-400 mb-2"></i>
      <p class="font-semibold text-sm mb-1.5">${l[1]}</p>
      <p class="text-xs text-slate-400 leading-relaxed">${l[2]}</p>
    </div>`).join('') + `</div>`);

// ── 6. 存储分布 ──
const STORE = [
  ["book-open","knowledge/","emerald","清洗后的老师语料，6 类目录；上传/编辑/删除自动维护"],
  ["database","index/","emerald","bm25.pkl 缓存（+vectors.pkl 填 KEY 后生成）"],
  ["scroll-text","sessions/","sky","每轮对话落盘 default.json，重启不丢"],
  ["brain","memory/","purple","long_term.jsonl 长期记忆 + state.json 抽取进度"],
  ["archive","knowledge_backup/","slate","删除/清洗前自动备份，可回滚"],
];
SEC("存储分布与生命周期", "database",
  `<div class="grid grid-cols-5 gap-3 text-center text-[11px]">` + STORE.map(s=>`
    <div class="bg-slate-900 rounded-xl border border-slate-800 p-3">
      <i data-lucide="${s[0]}" class="w-5 h-5 ${s[2]==='slate'?'text-slate-400':'text-'+s[2]+'-400'} mx-auto mb-2"></i>
      <p class="font-mono text-xs mb-1">${s[1]}</p>
      <p class="text-[10px] text-slate-500 leading-relaxed">${s[3]}</p>
    </div>`).join('') + `</div>`);
lucide.createIcons();
