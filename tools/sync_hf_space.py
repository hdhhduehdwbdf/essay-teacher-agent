# -*- coding: utf-8 -*-
"""把仓库根目录的运行文件同步进 hf_space/，传 Space 前跑一次。"""
import os, shutil, sys

sys.stdout.reconfigure(encoding="utf-8", errors="replace")
ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
DEST = os.path.join(ROOT, "hf_space")
CORPUS = None
if "--corpus" in sys.argv:
    CORPUS = sys.argv[sys.argv.index("--corpus") + 1]
FILES = ["agent.py", "config.py", "knowledge_base.py", "embeddings.py", "rag.py",
         "llm.py", "search_tool.py", "web_search.py", "net.py", "persona.md"]
DIRS = [] if CORPUS else ["knowledge"]

for f in FILES:
    src = os.path.join(ROOT, f)
    if os.path.isfile(src):
        shutil.copy2(src, os.path.join(DEST, f))
        print("copy", f)
for d in DIRS:
    src = os.path.join(ROOT, d)
    dst = os.path.join(DEST, d)
    if os.path.isdir(src):
        if os.path.isdir(dst):
            shutil.rmtree(dst)
        shutil.copytree(src, dst, ignore=shutil.ignore_patterns(".git*"))
        n = sum(len(fs) for _, _, fs in os.walk(dst))
        print(f"copy {d}/ ({n} files)")
if CORPUS:
    dst = os.path.join(DEST, "knowledge")
    if os.path.isdir(dst):
        shutil.rmtree(dst)
    shutil.copytree(CORPUS, dst, ignore=shutil.ignore_patterns(".git*"))
    n = sum(len(fs) for _, _, fs in os.walk(dst))
    print(f"copy 私有语料 ({n} files) —— 确认 Space 将设为 Private！")
