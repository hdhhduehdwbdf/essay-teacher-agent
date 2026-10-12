# -*- coding: utf-8 -*-
"""语料清洗：剥微信导出元数据头，丢纯图片占位行。幂等，可 --dry-run。"""
import os, re, sys, json, shutil

sys.stdout.reconfigure(encoding="utf-8", errors="replace")

ROOT = os.path.abspath(os.path.join(os.path.dirname(__file__), "..", "knowledge"))
BACKUP = os.path.abspath(os.path.join(os.path.dirname(__file__), "..", "knowledge_backup"))

LINK_LINE = re.compile(r"^\[[^\]]*info\.json[^\]]*\]\(https?://\)\s*$", re.I)
LINK_LINE_ANY = re.compile(r"^\[[^\]]+\]\(\s*https?://[^)]*\)\s*$")
IMAGE_LINE = re.compile(r"^!\[[^\]]*\]\(\s*https?://[^)]*\)\s*$")


def strip_json_block(text):
    """找到紧随链接行的 { ... } 块并整体删除，保留 title 单独成行。"""
    lines = text.splitlines()
    out, i, removed = [], 0, 0
    while i < len(lines):
        line = lines[i]
        if LINK_LINE_ANY.match(line) and "info.json" in line:
            # 向后找第一个 { 开始的行，括号配平找结尾
            j = i + 1
            while j < len(lines) and not lines[j].lstrip().startswith("{"):
                j += 1
            if j < len(lines):
                depth, k, buf = 0, j, []
                while k < len(lines):
                    buf.append(lines[k])
                    depth += lines[k].count("{") - lines[k].count("}")
                    if depth <= 0 and k > j:
                        break
                    k += 1
                try:
                    meta = json.loads("\n".join(buf))
                    title = (meta.get("title") or "").strip()
                    author = meta.get("author")
                    pub = meta.get("publish_time")
                    head = title
                    if author or pub:
                        head = title + f"（{pub or ''}）" if pub else title
                    if head:
                        out.append(head)
                    removed += 1
                    i = k + 1
                    continue
                except Exception:
                    pass
        out.append(line)
        i += 1
    return "\n".join(out), removed


def strip_image_lines(text):
    kept, n = [], 0
    for line in text.splitlines():
        if IMAGE_LINE.match(line.strip()):
            n += 1
            continue
        kept.append(line)
    return "\n".join(kept), n


def clean_text(text):
    text, a = strip_json_block(text)
    text, b = strip_image_lines(text)
    # 折叠多余空行
    text = re.sub(r"\n{3,}", "\n\n", text).strip() + "\n"
    return text, a, b


def main():
    dry = "--dry-run" in sys.argv
    changed = json_blocks = img_lines = 0
    for root, _dirs, files in os.walk(ROOT):
        for fn in sorted(files):
            if not fn.lower().endswith((".md", ".txt")) or fn.startswith("."):
                continue
            path = os.path.join(root, fn)
            original = open(path, encoding="utf-8", errors="ignore").read()
            cleaned, a, b = clean_text(original)
            if cleaned != original:
                changed += 1
                json_blocks += a
                img_lines += b
                rel = os.path.relpath(path, ROOT)
                print(f"  清洗 {rel}：元数据块 {a}，图片行 {b}")
                if not dry:
                    bak = os.path.join(BACKUP, rel)
                    os.makedirs(os.path.dirname(bak), exist_ok=True)
                    if not os.path.exists(bak):
                        shutil.copy2(path, bak)
                    with open(path, "w", encoding="utf-8") as f:
                        f.write(cleaned)
    mode = "（dry-run 预览）" if dry else ""
    print(f"完成{mode}：改动 {changed} 个文件，剥除元数据块 {json_blocks} 个、图片行 {img_lines} 行。")


if __name__ == "__main__":
    main()
