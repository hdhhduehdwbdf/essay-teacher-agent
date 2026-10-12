# -*- coding: utf-8 -*-
"""
搜索工具：博查 web-search（Function Calling 的执行端）
"""
import json
import urllib.request

from config import BOCHA_API_KEY, SEARCH_RESULTS


def web_search(query: str) -> str:
    """调用博查搜索API，返回精简结果文本"""
    if not BOCHA_API_KEY:
        return "（搜索工具未配置 BOCHA_API_KEY，无法联网验证。请如实告知用户当前无法搜索，不要编造搜索结果。）"
    try:
        req = urllib.request.Request(
            "https://api.bochaai.com/v1/web-search",
            data=json.dumps({"query": query, "count": SEARCH_RESULTS, "summary": True}).encode("utf-8"),
            headers={
                "Authorization": f"Bearer {BOCHA_API_KEY}",
                "Content-Type": "application/json",
            },
            method="POST",
        )
        with urllib.request.urlopen(req, timeout=20) as resp:
            data = json.loads(resp.read().decode("utf-8"))
        pages = (data.get("data") or {}).get("webPages") or {}
        items = pages.get("value") or []
        if not items:
            return "（搜索无结果）"
        out = []
        for i, it in enumerate(items[:SEARCH_RESULTS], 1):
            name = (it.get("name") or "")[:80]
            snippet = (it.get("summary") or it.get("snippet") or "")[:300]
            url = it.get("url") or ""
            out.append(f"{i}. {name}\n   摘要：{snippet}\n   来源：{url}")
        return "\n".join(out)
    except Exception as e:
        return f"（搜索失败：{e}）"


# Function Calling 工具声明（发给模型的"自我介绍"）
TOOLS_SCHEMA = [
    {
        "type": "function",
        "function": {
            "name": "web_search",
            "description": (
                "搜索互联网获取真实事实信息。规则：凡涉及事实、案例、数据、新闻、"
                "他人评价的表述，必须在写进回答之前先调用本工具验证；"
                "不确定是否属实的素材，先搜再答。不要等到被追问才搜。"
            ),
            "parameters": {
                "type": "object",
                "properties": {
                    "query": {
                        "type": "string",
                        "description": "搜索关键词，简洁明确，如：某某老师 学员评价",
                    }
                },
                "required": ["query"],
            },
        },
    }
]

TOOL_IMPL = {"web_search": web_search}
