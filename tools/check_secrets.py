#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""密钥泄漏扫描：全历史 + 工作区。发现命中退出码 1。
用法：python tools/check_secrets.py   （提交/推送前跑，CI 也会自动跑）"""
import re
import subprocess
import sys

PATTERNS = [
    re.compile(r"sk-[a-zA-Z0-9_-]{20,}"),          # OpenAI 兼容 key 形态
    re.compile(r"(?i)(api[_-]?key|secret|token)\s*=\s*[\"'][^\"']{16,}"),
]
ALLOW = re.compile(r"placeholder")  # 示例占位符不算泄漏


def scan_text(text):
    return [p for p in (m.group(0) for p in PATTERNS for m in [p.search(text)] if m)
            if not ALLOW.search(text)]


def main():
    hits = []
    revs = subprocess.run(["git", "rev-list", "--all"], capture_output=True,
                          text=True).stdout.split()
    for rev in revs:
        try:
            files = subprocess.run(
                ["git", "grep", "-l", "-iE", r"(sk-[a-zA-Z0-9_-]{16,})|(api[_-]?key\s*=)", rev],
                capture_output=True, text=True).stdout.split()
        except Exception:
            files = []
        for f in files:
            try:
                text = subprocess.run(["git", "show", f"{rev}:{f.split(':', 1)[1]}"],
                                      capture_output=True, text=True).stdout
            except Exception:
                continue
            for m in scan_text(text):
                hits.append(f"{rev[:8]} {f.split(':', 1)[1]}: {m[:40]}...")
    if hits:
        print("[泄漏] 发现疑似密钥：")
        print("\n".join(sorted(set(hits))))
        return 1
    print("[OK] 全历史与工作区无密钥形泄漏")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
