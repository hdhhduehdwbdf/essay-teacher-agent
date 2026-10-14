# -*- coding: utf-8 -*-
"""
模型调用层：支持 DeepSeek（OpenAI兼容接口）和 智谱GLM
纯标准库实现（urllib），不依赖 openai SDK
"""
import json
import urllib.request
import time

import config
import net
import store

def _count_call():
    """P3 运营看板：按日统计真实 LLM 调用次数（含工具循环与记忆抽取）。"""
    try:
        store.counter_add("llm_calls:" + time.strftime("%Y-%m-%d"), 1)
    except Exception:
        pass
from search_tool import TOOLS_SCHEMA, TOOL_IMPL


def _pick_provider():
    """按 config 里的 KEY 自动选择服务商"""
    if getattr(config, "CUSTOM_API_KEY", "") and getattr(config, "CUSTOM_BASE_URL", ""):
        return "custom", config.CUSTOM_API_KEY, config.CUSTOM_BASE_URL
    if config.DEEPSEEK_API_KEY:
        return "deepseek", config.DEEPSEEK_API_KEY, "https://api.deepseek.com/chat/completions"
    if config.ZHIPU_API_KEY:
        return "zhipu", config.ZHIPU_API_KEY, "https://open.bigmodel.cn/api/paas/v4/chat/completions"
    raise SystemExit(
        "\n[配置缺失] 请在 config.py 里填写 CUSTOM_API_KEY / DEEPSEEK_API_KEY / ZHIPU_API_KEY\n"
    )


def chat(messages, use_tools=True, max_tokens=1500):
    """调用模型。返回 assistant 消息对象（可能含 tool_calls）。
    messages 格式与 OpenAI 兼容：
      {"role": "system"|"user"|"assistant"|"tool", "content": str, ...}
    """
    provider, key, url = _pick_provider()
    body = {
        "model": config.MODEL,
        "messages": messages,
        "temperature": 0.7,
        "max_tokens": max_tokens,
    }
    if use_tools:
        body["tools"] = TOOLS_SCHEMA

    data = net.post_json(
        url,
        body,
        headers={"Authorization": f"Bearer {key}"},
        timeout=120,
        retries=3,
    )
    msg = data["choices"][0]["message"]
    _count_call()
    # 部分兼容端点 content 可能为 null（内容在 reasoning 等字段），做归一化
    if not msg.get("content"):
        msg["content"] = msg.get("reasoning") or msg.get("reasoning_content") or ""
    return msg


def rewrite_query(user_query, timeout=15):
    """LLM 查询改写：把口语/转述型问题改写成检索友好的关键词问句。
    失败或超时返回空串，由调用方回退原查询。"""
    provider, key, url = _pick_provider()
    body = {
        "model": config.MODEL,
        "messages": [
            {"role": "system", "content": "你是检索查询改写器。把用户的口语化问题改写成适合关键词与向量检索的问句。规则：1) 保留专有名词、课程名、数字；2) 把口语转述替换成知识库常用术语（如：骨架→结构框架、下笔→写作方法、救作文→提升作文）；3) 不要扩展成多个问题；4) 只输出改写后的查询本身，不要解释。"},
            {"role": "user", "content": user_query},
        ],
        "temperature": 0.1,
        "max_tokens": 100,
    }
    data = net.post_json(url, body, headers={"Authorization": f"Bearer {key}"}, timeout=timeout, retries=2)
    msg = data["choices"][0]["message"]
    return (msg.get("content") or msg.get("reasoning") or "").strip()


def chat_with_tool_loop(messages, max_loops=3, verbose=True):
    """带 Function Calling 循环的对话：
    模型点名工具 → 本地执行 → 结果回传 → 直到模型给出最终回答
    """
    for _ in range(max_loops):
        msg = chat(messages, use_tools=True)

        if not msg.get("tool_calls"):
            return msg["content"] or ""

        # 模型点名了工具 → 本地执行
        messages.append(msg)
        for tc in msg["tool_calls"]:
            fn_name = tc["function"]["name"]
            try:
                args = json.loads(tc["function"]["arguments"] or "{}")
            except Exception:
                args = {}
            if verbose:
                print(f"  🔍 [分身正在搜索] {args.get('query', fn_name)}")
            result = TOOL_IMPL.get(fn_name, lambda **k: "未知工具")(**args)
            messages.append({
                "role": "tool",
                "tool_call_id": tc["id"],
                "content": result if isinstance(result, str) else str(result),
            })
        # 带着工具结果再问一轮
    # 超过循环上限，强制不带工具要一个最终答案
    return chat(messages, use_tools=False)["content"] or ""


def chat_with_tool_loop_collector(messages, steps, max_loops=3):
    """与 chat_with_tool_loop 相同逻辑，但把每步动作（搜索/兜底）收集到 steps 列表供网页展示"""
    for _ in range(max_loops):
        msg = chat(messages, use_tools=True)

        if not msg.get("tool_calls"):
            return msg["content"] or ""

        messages.append(msg)
        for tc in msg["tool_calls"]:
            fn_name = tc["function"]["name"]
            try:
                args = json.loads(tc["function"]["arguments"] or "{}")
            except Exception:
                args = {}
            steps.append({"type": "tool", "text": f"正在联网搜索: {args.get('query', fn_name)}"})
            result = TOOL_IMPL.get(fn_name, lambda **k: "未知工具")(**args)
            # 搜索结果摘要给前端看
            brief = str(result)[:160]
            steps.append({"type": "tool_result", "text": f"搜索结果: {brief}..." if len(str(result)) > 160 else f"搜索结果: {brief}"})
            messages.append({
                "role": "tool",
                "tool_call_id": tc["id"],
                "content": result if isinstance(result, str) else str(result),
            })
    return chat(messages, use_tools=False)["content"] or ""



def _sse_iter(resp):
    """逐行读取 OpenAI 兼容 SSE 流，产出 data 载荷字符串"""
    for raw in resp:
        line = raw.decode("utf-8", errors="replace").strip()
        if not line.startswith("data:"):
            continue
        data = line[5:].strip()
        if data == "[DONE]":
            return
        yield data


def chat_stream(messages, use_tools=True, on_delta=None, max_tokens=1500):
    """流式调用模型：content 增量通过 on_delta 实时回调，返回完整 assistant 消息。
    模型点名工具时，tool_calls 以增量累积后一次性返回（调用方执行工具后可再次流式调用）。"""
    provider, key, url = _pick_provider()
    body = {
        "model": config.MODEL,
        "messages": messages,
        "temperature": 0.7,
        "max_tokens": max_tokens,
        "stream": True,
    }
    if use_tools:
        body["tools"] = TOOLS_SCHEMA
    req = urllib.request.Request(
        url,
        data=json.dumps(body).encode("utf-8"),
        headers={"Authorization": f"Bearer {key}", "Content-Type": "application/json",
                 "Accept": "text/event-stream"},
        method="POST",
    )
    content_parts = []
    tool_calls = {}
    _count_call()
    with urllib.request.urlopen(req, timeout=120) as resp:
        for data in _sse_iter(resp):
            try:
                chunk = json.loads(data)
            except ValueError:
                continue
            choices = chunk.get("choices") or []
            if not choices:
                continue
            delta = choices[0].get("delta") or {}
            if delta.get("content"):
                content_parts.append(delta["content"])
                if on_delta:
                    on_delta(delta["content"])
            for tc in delta.get("tool_calls") or []:
                idx = tc.get("index", 0)
                slot = tool_calls.setdefault(idx, {
                    "id": tc.get("id") or "", "type": "function",
                    "function": {"name": "", "arguments": ""}})
                if tc.get("id"):
                    slot["id"] = tc["id"]
                fn = tc.get("function") or {}
                slot["function"]["name"] += fn.get("name") or ""
                slot["function"]["arguments"] += fn.get("arguments") or ""
    if tool_calls:
        return {"role": "assistant",
                "tool_calls": [tool_calls[i] for i in sorted(tool_calls)]}
    return {"role": "assistant", "content": "".join(content_parts)}


def chat_with_tool_loop_stream(messages, steps, on_delta, max_loops=3):
    """chat_with_tool_loop_collector 的流式版：最终回答通过 on_delta 逐字吐出，
    工具轮步骤照常收集到 steps。"""
    for _ in range(max_loops):
        msg = chat_stream(messages, use_tools=True, on_delta=on_delta)
        if not msg.get("tool_calls"):
            return msg.get("content") or ""
        messages.append(msg)
        for tc in msg["tool_calls"]:
            fn_name = tc["function"]["name"]
            try:
                args = json.loads(tc["function"]["arguments"] or "{}")
            except Exception:
                args = {}
            steps.append({"type": "tool", "text": f"正在联网搜索: {args.get('query', fn_name)}"})
            result = TOOL_IMPL.get(fn_name, lambda **k: "未知工具")(**args)
            brief = str(result)[:160]
            steps.append({"type": "tool_result", "text": f"搜索结果: {brief}..." if len(str(result)) > 160 else f"搜索结果: {brief}"})
            messages.append({
                "role": "tool",
                "tool_call_id": tc["id"],
                "content": result if isinstance(result, str) else str(result),
            })
    return chat_stream(messages, use_tools=False, on_delta=on_delta, max_tokens=1500).get("content") or ""
