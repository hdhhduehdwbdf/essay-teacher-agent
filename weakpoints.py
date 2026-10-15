# -*- coding: utf-8 -*-
"""薄弱点卡片：AI 抽取候选（只抽不存）→ 用户确认 → 入库，全部按用户隔离。"""
import json
import re

import store


MAX_PER_TURN = 6
_IMP_WEIGHT = {"高": 3, "中": 2, "低": 1}

_EXTRACT_PROMPT = """你是写作教学的知识点抽取器。从下面这段师生对话里，抽出学生值得复习的内容。

只输出一个 JSON 数组，不要任何解释、不要代码块标记。每个元素格式：
{"type":"知识点|易错点|技巧","point":"一句话结论，不超过30字，要具体可记","evidence":"原对话里对应的短句，不超过40字","imp":"高|中|低"}

规则：
- 数量按内容决定：回答里有多少条真正值得记的，就抽多少条，最多 6 条；只有 1 条就只给 1 条。宁缺勿滥，不要为了凑数硬造。
- 按重要程度从高到低排列：imp="高" 的放最前面，其次 "中"，最后 "低"。
  "高"=易错/常扣分/关键结论；"中"=值得注意的技巧或提醒；"低"=一般性的好建议。
- point 必须是一条可以复习的结论，不要写"学生问了一个问题"这种废话
- evidence 必须能在原对话里找到出处

【学生问】
__USER__

【老师答】
__REPLY__
"""


def _payload(card):
    return {"id": card["id"], "deck": card["deck"] or "", "point": card["point"],
            "type": card["type"], "evidence": card["evidence"] or "",
            "session_ref": card["session_ref"] or "", "created": card["created"],
            "mastered": bool(card["mastered"]), "hits": card.get("hits") or 1}


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
    """判重：长度量级接近且重合度很高才算同一条，防止短串被长卡吞并。"""
    a, b = _norm(a), _norm(b)
    if not a or not b:
        return False
    if a == b:
        return True
    short, long_ = (a, b) if len(a) <= len(b) else (b, a)
    if len(short) / max(1, len(long_)) < 0.6:
        return False
    if short in long_ and len(short) / len(long_) >= 0.85:
        return True
    if len(short) < 6:
        return False
    ga = {a[i:i + 2] for i in range(len(a) - 1)}
    gb = {b[i:i + 2] for i in range(len(b) - 1)}
    return len(ga & gb) / max(1, min(len(ga), len(gb))) > 0.85


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
        if evidence and not merged_ev:
            merged_ev = evidence[:200]
        card = store.card_update(user, dup["id"], evidence=merged_ev)
        store.card_hit(user, dup["id"])
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
    info = list_all(user)
    return 200, {"ok": True, "cards": added, "added": len(added), "merged": merged,
                 "decks": info["decks"], "stats": info["stats"]}


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


def rule_extract(reply):
    """规则兜底：从带标记词的句子里抠出候选卡片。"""
    out = []
    _MARK = re.compile(r"(易错|容易错|常错|注意|记住|切忌|不要|别忘|建议|技巧|口诀|规律|"
                       r"关键|重点是|诀窍|写法是|套路|扣分点|分点|误区|务必|一定)")
    for raw in re.split(r"[\n。！？!?；;]", reply or ""):
        s = raw.strip().replace("**", "").replace("`", "").replace("*", "")
        s = re.sub(r"^[\s#>•·\-—]+", "", s)
        s = re.sub(r"^[^：:]{0,8}[：:]\s*", "", s)
        s = s.strip(" 　·-—。；;.,，")
        if not (8 <= len(s) <= 60):
            continue
        if not _MARK.search(s):
            continue
        if re.search(r"(易错|容易错|常错|切忌|不要|别忘|扣分点)", s):
            ctype, imp = "易错点", "高"
        elif re.search(r"(技巧|诀窍|套路|写法是)", s):
            ctype, imp = "技巧", "中"
        else:
            ctype, imp = "知识点", ("高" if re.search(r"(关键|重点是|务必|一定|必须)", s) else "中")
        out.append({"point": s[:120], "type": ctype, "evidence": s[:60], "imp": imp})
        if len(out) >= 6:
            break
    out.sort(key=lambda x: _IMP_WEIGHT.get(x.get("imp"), 1), reverse=True)
    return out[:MAX_PER_TURN]


def _parse_json_array(text):
    """从模型输出里抠出 JSON 数组，容忍 ```json 包裹和前后废话。"""
    if not text:
        return []
    t = text.strip()
    t = re.sub(r"^```(?:json)?|```$", "", t, flags=re.M).strip()
    start, end = t.find("["), t.rfind("]")
    if start >= 0 and end > start:
        t = t[start:end + 1]
    try:
        data = json.loads(t)
    except Exception:
        return []
    if not isinstance(data, list):
        return []
    out = []
    for it in data:
        if isinstance(it, dict) and it.get("point"):
            out.append({
                "type": str(it.get("type") or "知识点"),
                "point": str(it.get("point")),
                "evidence": str(it.get("evidence") or ""),
                "imp": str(it.get("imp") or ""),
            })
        elif isinstance(it, str) and len(it) >= 3:
            out.append({"type": "知识点", "point": it, "evidence": "", "imp": ""})
    return out


def suggest(user, message, reply):
    """只抽不存：候选交给前端弹窗，用户选分区确认后再批量入库。"""
    try:
        import llm
        prompt = (_EXTRACT_PROMPT
                  .replace("__USER__", (message or "")[:1500])
                  .replace("__REPLY__", (reply or "")[:2500]))
        resp = llm.chat([{"role": "system", "content": "你是严谨的知识点抽取器，只输出 JSON 数组。"},
                         {"role": "user", "content": prompt}], use_tools=False, max_tokens=600)
        raw = (resp.get("content") or "").strip() if isinstance(resp, dict) else str(resp)
        items = _parse_json_array(raw)
    except Exception:
        items = rule_extract(reply)
    items = [i for i in items if (i.get("point") or "").strip()]
    items.sort(key=lambda x: _IMP_WEIGHT.get(x.get("imp"), 1), reverse=True)
    return 200, {"items": items[:MAX_PER_TURN]}
