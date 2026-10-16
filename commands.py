# -*- coding: utf-8 -*-
"""B3 快捷指令（斜杠命令）

用户在输入框打 `/命令行 正文`，这里把它展开成一段结构化提示词再发给模型。
用户原始输入会记在 history 的 display 字段里，网页上仍显示他打的原话。

新增一条命令：往 COMMANDS 里加一项即可，前端自动出现。
"""
import re

# {body} 会被替换成命令行后面的正文
COMMANDS = [
    {
        "name": "批改",
        "icon": "pen-line",
        "desc": "逐段批改这篇作文",
        "usage": "/批改 正文",
        "template": (
            "请按六步教学法逐段批改下面这篇作文。\n"
            "要求：先给一句整体判断，再一段一段指出问题并给出具体改法；"
            "素材里没有依据的话不要编。\n\n【要批改的作文】\n{body}"
        ),
    },
    {
        "name": "素材",
        "icon": "library",
        "desc": "围绕主题给可用素材",
        "usage": "/素材 主题或题目",
        "template": (
            "围绕主题「{body}」给我 3-5 个可以直接用进作文的素材"
            "（事例 / 名言 / 数据），每条注明：能用在哪一段、怎么切进去。"
            "涉及事实性的例子，先用 web_search 核一遍再写。"
        ),
    },
    {
        "name": "范文",
        "icon": "file-text",
        "desc": "给同题范文骨架",
        "usage": "/范文 题目",
        "template": (
            "围绕题目「{body}」给一篇范文**骨架**，不要整篇代写：\n"
            "1) 立意方向（2 个备选，说明各自的风险）\n"
            "2) 结构提纲（分论点 + 每段要点）\n"
            "3) 挑一个关键段落写示范，并标出可替换的句式。"
        ),
    },
    {
        "name": "重写",
        "icon": "refresh-cw",
        "desc": "只重写指定段落",
        "usage": "/重写 段落原文",
        "template": (
            "重写下面这段文字：保持我的原意和语气，把表达改得更清楚、更有力。\n"
            "输出格式：先给重写后的段落，再逐条说明每处改动为什么更好。\n\n【原文】\n{body}"
        ),
    },
    {
        "name": "评分",
        "icon": "star",
        "desc": "按高考标准打分",
        "usage": "/评分 正文",
        "template": (
            "按高考作文评分标准给下面这篇打分：\n"
            "内容（20）/ 表达（20）/ 发展等级（20）/ 结构（10），分项给分并给总分；\n"
            "然后写清楚扣分点，以及提分最快的两处改动。\n\n【要评分的作文】\n{body}"
        ),
    },
]

_BY_NAME = {c["name"]: c for c in COMMANDS}
# 匹配开头：/批改 或 /批改正文（允许中英文空白、全角空格）
_LEAD = re.compile(r"^\s*/\s*([A-Za-z\u4e00-\u9fff]{1,8})\s*([\s\S]*)$")


def catalog():
    """给前端用的命令清单（不含模板）。"""
    return [{"name": c["name"], "icon": c["icon"], "desc": c["desc"], "usage": c["usage"]}
            for c in COMMANDS]


def parse(text):
    """解析用户输入。

    返回 (command_name|None, body, prompt_to_send)
    - 不是斜杠命令：返回 (None, "", 原文)
    - 是斜杠命令但正文为空：prompt 里会指示模型先索要正文，不要瞎写
    """
    text = text or ""
    m = _LEAD.match(text)
    if not m:
        return None, "", text

    name, body = m.group(1), m.group(2).strip()
    cmd = _BY_NAME.get(name)
    if not cmd:
        return None, "", text

    if body:
        return name, body, cmd["template"].format(body=body)

    # 只打了命令没给正文
    prompt = (
        f"用户想用「{name}」这个动作，但还没把内容发过来。"
        f"请用一句话提醒他把要处理的内容发你（说明你需要什么），"
        f"不要自行编造内容，也不要长篇大论。\n"
        f"（待你拿到内容后，按这个流程处理：{cmd['desc']}）"
    )
    return name, "", prompt
