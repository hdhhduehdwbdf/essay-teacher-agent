# -*- coding: utf-8 -*-
"""薄弱点卡片：AI 每轮抽取候选（只抽不存，前端确认后入库），全部按用户隔离。"""
import json
import re
from difflib import SequenceMatcher

import store


MAX_PER_TURN = 6
_IMP_WEIGHT = {"高": 2, "中": 1, "低": 0}

_EXTRACT_PROMPT = """从下面的师生对话里，提取学生暴露出的薄弱点，供他之后复习。
只输出 JSON 数组，每项形如 {"point": "薄弱点一句话", "type": "知识点|技巧|易错点|素材", "evidence": "对话中的依据（30字内）", "imp": "高|中|低"}。
没有就输出 []。最多 6 条，按重要性从高到低。
学生输入：__USER__
老师回答：__REPLY__"""


def _payload(card):
    return {"id": card["id"], "deck": card["deck"] or "", "point": card["point"],
            "type": card["type"], "evidence": card["evidence"] or "",
            "session_ref": card["session_ref"] or "", "created": card["created"],
            "mastered": bool(card["mastered"])}


def list_all(user, deck=None):
    decks = [dict(d) for d in store.decks_list(user)]
    cards = [_payload(c) for c in store.cards_list(user, deck or None)]
    total = len(cards)
    mastered = sum(1 for c in cards if c["mastered"])
    return {"decks": decks, "cards": cards,
            "stats": {"total": total, "mastered": mastered, "todo": total - mastered}}


def ensure_deck(user, name):
    for d in store.decks_list(user):
        if d["name"] == name:
            return d["id"]
    return store.deck_create(user, name)["id"]


def deck_create(user, name):
    name = str(name or "").strip()[:20]
    if not name:
        return 400, {"error": "分区名不能为空"}
    for d in store.decks_list(user):
        if d["name"] == name:
            return 400, {"error": "分区已存在"}
    return 200, {"ok": True, "deck": store.deck_create(user, name)}


def deck_rename(user, deck_id, name):
    name = str(name or "").strip()[:20]
    if not name or not store.deck_get(user, deck_id):
        return 404, {"error": "分区不存在"}
    store.deck_rename(user, deck_id, name)
    return 200, {"ok": True}


def deck_delete(user, deck_id):
    if not store.deck_get(user, deck_id):
        return 404, {"error": "分区不存在"}
    store.deck_delete(user, deck_id)
    return 200, {"ok": True}


def _norm(s):
    return re.sub(r"[\s，。、；：？！\"'（）()\-—*#]", "", str(s or "")).lower()


def _similar(a, b):
    a, b = _norm(a), _norm(b)
    if not a or not b:
        return False
    if a == b or a in b or b in a:
        return True
    return SequenceMatcher(None, a, b).ratio() >= 0.62


def add(user, point, ctype="知识点", evidence="", deck="", session_ref="", source="manual"):
    point = str(point or "").strip()[:200]
    if not point:
        return 400, {"error": "内容不能为空"}
    ctype = ctype if ctype in ("知识点", "技巧", "易错点", "素材") else "知识点"
    dedupe = source != "manual"
    dup = None
    for c in store.cards_list(user):
        hit = _similar(c["point"], point) if dedupe else _norm(c["point"]) == _norm(point)
        if hit:
            dup = c
            break
    if dup:
        merged_ev = (dup["evidence"] or "")
        if evidence and evidence not in merged_ev:
            merged_ev = (merged_ev + "；" + evidence[:200])[:300]
        card = store.card_update(user, dup["id"], evidence=merged_ev)
        return 200, {"ok": True, "card": _payload(card), "merged": True}
    card = store.card_add(user, point, ctype, evidence[:200], deck, session_ref)
    return 200, {"ok": True, "card": _payload(card), "merged": False}


def add_many(user, items, deck="", session_ref=""):
    added = []
    merged = 0
    for it in items[:MAX_PER_TURN]:
        code, r = add(user, it.get("point", ""), it.get("type", "知识点"),
                      it.get("evidence", ""), it.get("deck") or deck, session_ref, "extract")
        if code == 200:
            if r.get("merged"):
                merged += 1
            else:
                added.append(r["card"])
    return 200, {"ok": True, "cards": added, "added": len(added), "merged": merged}


def update(user, card_id, **kw):
    card = store.card_update(user, card_id, **kw)
    if not card:
        return 404, {"error": "卡片不存在"}
    return 200, {"ok": True, "card": _payload(card)}


def toggle(user, card_id, mastered):
    card = store.card_toggle(user, card_id, mastered)
    if not card:
        return 404, {"error": "卡片不存在"}
    return 200, {"ok": True, "card": _payload(card)}


def delete(user, card_id):
    store.card_delete(user, card_id)
    return 200, {"ok": True}


def clear(user, mastered_only=False, deck=None):
    store.cards_clear(user, mastered_only, deck)
    return 200, {"ok": True}


def suggest(user, message, reply):
    """只抽不存：候选交给前端弹窗，用户选分区确认后再批量入库。"""
    try:
        import llm
        prompt = (_EXTRACT_PROMPT
                  .replace("__USER__", (message or "")[:1500])
                  .replace("__REPLY__", (reply or "")[:2500]))
        raw = llm.chat([{"role": "system", "content": "你是严谨的知识点抽取器，只输出 JSON 数组。"},
                        {"role": "user", "content": prompt}], use_tools=False, max_tokens=600)
        items = json.loads(re.search(r"\[.*\]", raw, re.S).group(0))
        if not isinstance(items, list):
            raise ValueError
    except Exception:
        items = rule_extract(reply)
    items = [i for i in items if (i.get("point") or "").strip()]
    items.sort(key=lambda x: _IMP_WEIGHT.get(x.get("imp"), 1), reverse=True)
    return 200, {"items": items[:MAX_PER_TURN]}


def rule_extract(reply):
    out = []
    marks = [("易错", "高"), ("关键", "高"), ("注意", "中"), ("技巧", "中"), ("记住", "中"), ("误区", "高")]
    for raw in re.split(r"[\n。！？!?；;]", reply or ""):
        s = raw.strip().strip("-*0123456789.、）) ")
        if not (6 <= len(s) <= 80):
            continue
        for word, imp in marks:
            if word in s:
                out.append({"point": s[:120], "type": "易错点" if word in ("易错", "注意") else "技巧",
                            "evidence": "", "imp": imp})
                break
    return out[:MAX_PER_TURN]
