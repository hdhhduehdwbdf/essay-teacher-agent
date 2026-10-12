# -*- coding: utf-8 -*-
"""
模型调用层：支持 DeepSeek（OpenAI兼容接口）和 智谱GLM
纯标准库实现（urllib），不依赖 openai SDK
"""
import json
import urllib.request

import config
import net
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


def chat(messages, use_tools=True):
    """调用模型。返回 assistant 消息对象（可能含 tool_calls）。
    messages 格式与 OpenAI 兼容：
      {"role": "system"|"user"|"assistant"|"tool", "content": str, ...}
    """
    provider, key, url = _pick_provider()
    body = {
        "model": config.MODEL,
        "messages": messages,
        "temperature": 0.7,
        "max_tokens": 1500,
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
    # 部分兼容端点 content 可能为 null（内容在 reasoning 等字段），做归一化
    if not msg.get("content"):
        msg["content"] = msg.get("reasoning") or msg.get("reasoning_content") or ""
    return msg


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
