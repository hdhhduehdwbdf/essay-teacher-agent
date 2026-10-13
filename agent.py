# -*- coding: utf-8 -*-
"""
叁月聚粮分身 · 命令行入口
组装顺序：persona(system) → 知识库检索片段 → 会话历史 → 用户输入
用法：
  python agent.py                # 交互对话
  python agent.py "问题"          # 单问单答
  python agent.py --test         # 自检（不调API，测索引和配置）
"""
import sys, os

try:  # Windows 控制台默认 GBK，打印 emoji/中文会崩，这里统一切到 UTF-8
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")
except Exception:
    pass

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

import config
import knowledge_base
from llm import chat_with_tool_loop

SYSTEM_TMPL = """{persona}

==================== 长期记忆（关于当前用户的既有了解） ====================
{memory}
====================================================================================
使用规则：
- 记忆块为空则忽略本节；有内容时自然融入回答（如称呼、年级、弱项针对性），
  不要逐条复述记忆，不要说"根据记忆"这类机械表述。

==================== 知识库检索结果（与本问题相关的老师语料片段） ====================
{retrieved}
====================================================================================
使用规则：
- 上方检索结果是老师真实语料，回答优先基于它；素材里没有的，说"这个我没怎么碰过"。
- 涉及事实/案例/数据的表述，先用 web_search 工具验证，再写进回答。
- 保持 persona 全部规则：理解在前拆解在后、先验证再作答、不编造细节。
"""


def build_messages(history, user_input, verbose=True, hits=None):
    memory_block = ""
    try:
        import memory
        memory_block = memory.recall(user_input)
    except Exception:
        pass
    # ① 知识库检索
    if hits is None:
        hits = knowledge_base.retrieve(user_input)
    if verbose:
        print(f"  📚 [知识库命中] {len(hits)} 片段: " + ", ".join(h['source'][:24] for h in hits[:3]) + ("..." if len(hits) > 3 else ""))

    if hits:
        retrieved = "\n\n".join(f"【来源: {h['source']}】\n{h['text']}" for h in hits)
    else:
        retrieved = "（知识库暂无语料或未命中。此时更要用 web_search 验证事实，素材里没有的如实说没碰过。）"

    persona = open(os.path.join(os.path.dirname(os.path.abspath(__file__)), config.PERSONA_FILE),
                   encoding="utf-8").read()
    system = SYSTEM_TMPL.format(persona=persona, memory=memory_block or "（暂无长期记忆）", retrieved=retrieved)

    # ② 组装：system → 历史 → 本轮输入
    messages = [{"role": "system", "content": system}]
    kept = history[-(config.HISTORY_ROUNDS * 2):]
    messages.extend(kept)
    messages.append({"role": "user", "content": user_input})
    return messages


def answer(user_input, history, verbose=True):
    messages = build_messages(history, user_input, verbose)
    reply = chat_with_tool_loop(messages, verbose=verbose)
    # 简洁纪律：超长截断提醒（不硬切，交由下一轮 system 收敛）
    history.append({"role": "user", "content": user_input})
    history.append({"role": "assistant", "content": reply})
    return reply


def selftest():
    print("=" * 50)
    print("自检模式（不调 API）")
    print("=" * 50)
    ok = True
    # persona 文件
    p = os.path.join(os.path.dirname(os.path.abspath(__file__)), config.PERSONA_FILE)
    if os.path.exists(p):
        n = len(open(p, encoding="utf-8").read())
        print(f"[OK] persona: {p} ({n} 字)")
    else:
        print(f"[缺失] persona 文件不存在: {p}")
        ok = False
    # 配置
    if getattr(config, "CUSTOM_API_KEY", "") or config.DEEPSEEK_API_KEY or config.ZHIPU_API_KEY:
        prov = "自定义端点" if getattr(config, "CUSTOM_API_KEY", "") else ("DeepSeek" if config.DEEPSEEK_API_KEY else "智谱GLM")
        print(f"[OK] 模型API: {prov} / {config.MODEL}")
    else:
        print("[缺失] config.py 未填模型 API KEY")
        ok = False
    if config.BOCHA_API_KEY:
        print("[OK] 搜索API: 博查")
    else:
        try:
            import web_search
            print("[OK] 搜索API: 免费免注册引擎链（百度 → 360 → Bing RSS）")
            eng, hits = web_search.search("测试 搜索连通性", 3)
            print(f"       连通性测试: {'通过（' + eng + '，命中 ' + str(len(hits)) + ' 条）' if eng else '失败：' + str(hits)}")
        except Exception as e:
            print(f"[警告] 免费搜索不可用（{type(e).__name__}: {e}），答事实类问题会如实说无法搜索")
    # 知识库
    eng = knowledge_base.get_engine()
    docs = knowledge_base.read_docs()
    print(f"[{'OK' if docs else '警告'}] 知识库: {len(docs)} 个文件 → {len(eng.meta)} 个检索片段")
    try:
        import embeddings
        print(f"       检索模式: {embeddings.describe()}")
    except Exception as e:
        print(f"       检索模式: BM25（{e}）")
    if not docs:
        print("       （把老师语料 .md/.txt/.json/.csv 放进 knowledge/ 目录即可）")
    elif eng.meta:
        demo = knowledge_base.retrieve("审题立意方法")
        for h in demo[:2]:
            print(f"       检索测试 [score={h['score']}] {h['source'][:36]}")
    print("=" * 50)
    print("自检" + ("通过，可以运行 python agent.py 开始对话" if ok else "未通过，请先补齐上述 [缺失] 项"))
    return ok


def main():
    args = [a for a in sys.argv[1:]]
    if "--test" in args:
        args.remove("--test")
        selftest()
        return

    history = []
    # 单问模式
    if args:
        q = " ".join(args)
        print(answer(q, history))
        return

    # 交互模式
    print("叁月聚粮分身（自建版）· 输入问题开始对话，exit 退出，/new 清空会话记忆")
    print("-" * 60)
    while True:
        try:
            q = input("\n你：").strip()
        except (EOFError, KeyboardInterrupt):
            print("\n再见。")
            break
        if not q:
            continue
        if q.lower() in ("exit", "quit", "q"):
            print("再见。")
            break
        if q == "/new":
            history.clear()
            print("（会话记忆已清空）")
            continue
        print("分身：")
        try:
            print(answer(q, history))
        except SystemExit as e:
            print(e)
            break
        except Exception as e:
            print(f"[调用出错] {e}\n请检查网络与 API KEY 配置。")


if __name__ == "__main__":
    main()
