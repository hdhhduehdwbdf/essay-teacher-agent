# -*- coding: utf-8 -*-
"""作文打磨流程引擎：学生码入场 → 七环节提交 → 闸门判定 → 结课双报告。

LLM 调用纪律：每个环节单独一次 llm.chat（不带工具），不占对话主链路。
闸门纪律：LLM 没输出合法闸门 JSON 一律判 retry（宁可误拦，不可放水）。
"""
import json
import re
import secrets
import threading
import time

import coach_rules
import store


def _id(prefix):
    return prefix + secrets.token_hex(4) + time.strftime("%H%M%S")


def _task_active(user):
    tasks = store.essay_tasks_list(user)
    return tasks[0] if tasks else None


def state(user):
    """学生端全量状态：题目列表、当前 run、环节进度、各步记录。"""
    tasks = store.essay_tasks_list(user)
    run = store.essay_run_latest(user)
    steps = store.essay_steps_list(user, run["id"]) if run else []
    task = store.essay_task_get(user, run["task_id"]) if run else None
    return 200, {"tasks": tasks, "run": run, "task": task, "steps": steps,
                 "meta": coach_rules.steps_meta(run),
                 "rules_version": coach_rules.RULES_VERSION}


def enter(user, req):
    """开始一轮新的打磨（绑定第一个可用题目）。学生码即 run.code，用于老师对号。"""
    task = _task_active(user)
    if not task:
        return 400, {"error": "还没有作文题目，先在上面自设一个题目"}
    genre = str(req.get("genre") or "议论文").strip()
    if genre not in ("议论文", "记叙文"):
        genre = "议论文"
    code = "".join(secrets.choice("23456789ABCDEFGHJKMNPQRSTUVWXYZ") for _ in range(8))
    run = store.essay_run_create(user, task["id"], code, genre)
    return 200, {"ok": True, "run": run, "task": task}


def _parse_gate(reply):
    matches = re.findall(r"\{[^{}]*\"gate\"[^{}]*\}", reply or "")
    if matches:
        try:
            g = json.loads(matches[-1]).get("gate")
            if g in ("pass", "retry"):
                return g
        except Exception:
            pass
    return "retry"


def _strip_gate(reply):
    """给学生看的诊断正文：去掉末尾的闸门 JSON 行（内部说明不入正文）。"""
    return re.sub(r"\s*\{[^{}]*\"gate\"[^{}]*\}\s*$", "", (reply or "")).strip()


def _validate_submit(user, req):
    """公共校验：run 存在 / 进行中 / 内容长度。返回 (error_tuple, run, task, step, steps)。"""
    run_id = req.get("run_id", "")
    text = str(req.get("text") or "").strip()
    run = store.essay_run_get(user, run_id)
    if not run:
        return (404, {"error": "打磨会话不存在"}), None, None, None, None
    if run["status"] != "active":
        return (400, {"error": "本轮已结课"}), None, None, None, None
    step = int(run["step"])
    task = store.essay_task_get(user, run["task_id"])
    if not task:
        return (404, {"error": "题目不存在"}), None, None, None, None
    if len(text) < 10:
        return (400, {"error": "提交内容太短，请把完整内容发来"}), None, None, None, None
    steps = store.essay_steps_list(user, run_id)
    return None, run, task, step, steps



def _rejudge_gate(task, run, step, text, first_reply):
    """retry 时的二次复核：独立小调用，两次都说不过才真打回。"""
    try:
        import llm
        msg = llm.chat(coach_rules.rejudge_messages(task, run, step, text),
                       use_tools=False, max_tokens=100, temperature=0.1, timeout=45, retries=1)
        g = _parse_gate((msg.get("content") or "").strip())
        return g if g in ("pass", "retry") else "retry"
    except Exception:
        return "retry"

def submit_stream(user, req, emit):
    """流式诊断版提交：诊断正文逐字 emit，结束后统一落库与闸门判定。"""
    err, run, task, step, steps = _validate_submit(user, req)
    if err:
        return err
    text = str(req.get("text") or "").strip()
    messages = coach_rules.step_messages(task, run, steps, step, text[:6000])
    import llm
    from net import Cancelled
    buf = []
    cancel = threading.Event()
    _ACTIVE_RUNS[user] = cancel
    try:
        def on_delta(t):
            if cancel.is_set():
                raise Cancelled("已停止")
            buf.append(t)
            emit({"type": "delta", "text": t})
        msg = llm.chat_stream(messages, use_tools=False, on_delta=on_delta, max_tokens=2200, temperature=0.2, cancel=cancel)
        reply = (msg.get("content") or "").strip()
    except Cancelled:
        emit({"type": "error", "error": "已停止"})
        return 200, {"ok": True, "stopped": True}
    except Exception as e:
        return 502, {"error": "教练暂时离线（LLM 调用失败），请稍后重试：" + type(e).__name__}
    finally:
        _ACTIVE_RUNS.pop(user, None)
    gate = "pass" if step == 1 else _parse_gate(reply)
    if gate == "retry":
        gate = _rejudge_gate(task, run, step, text, reply)
    clean = _strip_gate(reply)
    total = coach_rules.total_steps(run)
    store.essay_step_add(user, run["id"], step, text[:8000], json.dumps(
        {"problems": [], "prescription": ""}, ensure_ascii=False), gate, clean)
    if gate == "pass":
        if step == 1:
            run = store.essay_run_update(user, run["id"], draft=text[:8000], step=2)
        elif step == total:
            run = store.essay_run_update(user, run["id"], step=total, status="done")
            report = _make_report(task, run, steps + [{"step": total, "submit_text": text, "reply": clean, "gate": "pass"}])
            run = store.essay_run_update(user, run["id"], report=report)
        else:
            run = store.essay_run_update(user, run["id"], step=step + 1)
    else:
        run = store.essay_run_update(user, run["id"], step=step)
    result = {"ok": True, "gate": gate, "reply": clean, "run": run,
              "step_info": coach_rules.steps_for(run).get(step, {})}
    emit({"type": "done", "payload": result})
    return 200, result


def submit(user, req):
    run_id = req.get("run_id", "")
    text = str(req.get("text") or "").strip()
    run = store.essay_run_get(user, run_id)
    if not run:
        return 404, {"error": "打磨会话不存在"}
    if run["status"] != "active":
        return 400, {"error": "本轮已结课"}
    step = int(run["step"])
    task = store.essay_task_get(user, run["task_id"])
    if not task:
        return 404, {"error": "题目不存在"}
    if len(text) < 10:
        return 400, {"error": "提交内容太短，请把完整内容发来"}

    steps = store.essay_steps_list(user, run_id)
    messages = coach_rules.step_messages(task, run, steps, step, text[:6000])
    import llm
    try:
        msg = llm.chat(messages, use_tools=False, max_tokens=2200, temperature=0.2)
        reply = (msg.get("content") or "").strip()
    except Exception as e:
        return 502, {"error": "教练暂时离线（LLM 调用失败），请稍后重试：" + type(e).__name__}

    gate = "pass" if step == 1 else _parse_gate(reply)
    if gate == "retry":
        gate = _rejudge_gate(task, run, step, text, reply)
    clean = _strip_gate(reply)
    total = coach_rules.total_steps(run)
    store.essay_step_add(user, run_id, step, text[:8000], json.dumps(
        {"problems": [], "prescription": ""}, ensure_ascii=False), gate, clean)

    if gate == "pass":
        if step == 1:
            run = store.essay_run_update(user, run_id, draft=text[:8000], step=2)
        elif step == total:
            run = store.essay_run_update(user, run_id, step=total, status="done")
            report = _make_report(task, run, steps + [{
                "step": 7, "submit_text": text, "reply": clean, "gate": "pass"}])
            run = store.essay_run_update(user, run_id, report=report)
        else:
            run = store.essay_run_update(user, run_id, step=step + 1)
    else:
        run = store.essay_run_update(user, run_id, step=step)
    return 200, {"ok": True, "gate": gate, "reply": clean, "run": run,
                 "step_info": coach_rules.steps_for(run).get(step, {})}


def _make_report(task, run, steps):
    import llm
    try:
        msg = llm.chat(coach_rules.report_messages(task, run, steps), use_tools=False, max_tokens=3000, timeout=90, retries=1)
        return (msg.get("content") or "").strip()
    except Exception:
        return "（报告生成失败，请稍后在此页重试生成。）"



def ask(user, req):
    """环节进行中的插问：单次 LLM 调用，答疑不判闸门、不推进环节。"""
    run_id = req.get("run_id", "")
    q = str(req.get("question") or "").strip()
    run = store.essay_run_get(user, run_id)
    if not run:
        return 404, {"error": "打磨会话不存在"}
    task = store.essay_task_get(user, run["task_id"])
    if not task:
        return 404, {"error": "题目不存在"}
    if not q:
        return 400, {"error": "问题不能为空"}
    steps = store.essay_steps_list(user, run_id)
    messages = coach_rules.ask_messages(task, run, steps, q[:1000])
    import llm
    try:
        msg = llm.chat(messages, use_tools=False, max_tokens=800, temperature=0.3, timeout=45, retries=1)
        answer = (msg.get("content") or "").strip()
    except Exception as e:
        return 502, {"error": "教练暂时离线：" + type(e).__name__}
    store.essay_step_add(user, run_id, run["step"], q[:2000], "", "ask", answer)
    return 200, {"ok": True, "answer": answer}


_ACTIVE_RUNS = {}


def cancel_run(user, run_id):
    ev = _ACTIVE_RUNS.get(user)
    if ev:
        ev.set()
        return 200, {"ok": True, "stopped": True}
    return 200, {"ok": True, "stopped": False}
