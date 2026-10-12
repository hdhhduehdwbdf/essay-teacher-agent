# -*- coding: utf-8 -*-
"""
搜索工具（Function Calling 的执行端）

优先级：
  1. 博查 web-search（config 里填了 BOCHA_API_KEY 才走，质量最好）
  2. 免费免注册引擎链：百度 → 360 → Bing RSS（见 web_search.py，零配置可用）
"""
import json
import urllib.request

from config import BOCHA_API_KEY, SEARCH_RESULTS
import net
from web_search import search as free_search, format_results


def web_search(query: str) -> str:
    """联网搜索，返回精简结果文本"""
    if not BOCHA_API_KEY:
        return free_web_search(query)
    try:
        data = net.post_json(
            "https://api.bochaai.com/v1/web-search",
            {"query": query, "count": SEARCH_RESULTS, "summary": True},
            headers={"Authorization": f"Bearer {BOCHA_API_KEY}"},
            timeout=20,
            retries=2,
        )
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
        # 博查挂了也别让分身失去联网能力，退回免费引擎
        fallback = free_web_search(query)
        return fallback if "（联网搜索失败" not in fallback else f"（搜索失败：{e}）"


def free_web_search(query: str) -> str:
    """免费免注册多引擎搜索"""
    engine, hits = free_search(query, max(SEARCH_RESULTS, 5))
    if not hits:
        return ("（联网搜索失败：所有免费引擎均不可用"
                f"{'，' + str(hits) if hits else ''}。请如实告知用户当前无法联网验证，不要编造结果。）")
    return f"[引擎: {engine}]\n" + format_results(hits, SEARCH_RESULTS)


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
