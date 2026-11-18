#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""守門：不該出現在**公開 repo** 的字串。

`cemi-ai/cemi-website` 是 **public**。任何進到 commit 的東西就留在歷史裡了 ——
事後要清只能 force push，而 CLAUDE.md 禁止 force push。所以這一支要在**推之前**跑。

擋的是四類：

1. **人名** —— 公司內部的人名不該出現在對外的原始碼裡。
2. **內部 session／角色名稱** —— 會洩漏內部怎麼分工，對讀者也沒有意義。
   寫「程式碼審查抓到」比寫角色代號清楚。
3. **內部文件編號**（`QEW-…`／`QEF-…`／`QEP-…`）—— 品保文件控制編號。
   曾經有一組寫進 PR 內文（2026-11-18）。
4. **本機路徑**（`/Users/…`、`~/Desktop`、保管庫、`Local-敏感`）和 droplet 的 IP ——
   使用者名稱、資料夾結構、主機位址都不該外流。

🔴 **這是一份要人維護的清單，代價講在前面**：它只能擋**想得到**的字串。
新的人名、新的專案代號、新的主機它都不知道。所以它是**下限不是保證** ——
真正的防線還是「推之前自己看一次 diff」。加字串進來是便宜的，請隨手加。

⚠ **這支檔案本身會被跳過**（它必須包含那些字串才能比對）。代價是：
守門自己檔案裡的違規它看不到。所以這支檔案**只放樣式，不要放例子**。
它會不會動，靠的是下面的正向對照 —— 造一個含違規字串的暫存檔，它必須報紅。

用法：
    python3 tools/verify/check_public_repo_strings.py          # 掃 repo（含正向對照）
    python3 tools/verify/check_public_repo_strings.py --staged # 只掃 git 已暫存的檔
"""
from __future__ import annotations

import io
import os
import re
import subprocess
import sys
import tempfile

HERE = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.abspath(os.path.join(HERE, "..", ".."))
SELF = os.path.abspath(__file__)

#: (標籤, 正規式)。大小寫不敏感的用 (?i)。六類，順序照檔頭。
RULES = [
    ("人名",       r"(?i)\bnick\b"),
    ("人名(帳號)",  r"(?i)\bnickhsu\b"),
    ("內部角色",    r"(?i)\b(?:auditor|researcher|reviewer)\b"),
    # ⚠ `lead`／`builder`／`docs` 也是普通英文字 —— 這一頁就有 `<p class="lead">`。
    #   所以這三個**不能**無條件比對，只在「像角色名」的上下文才算：
    #   後面接日期或冒號，或和另一個角色名並列（`lead／auditor`、`lead 和 builder`）。
    #   代價講明：單獨出現的 `lead` 抓不到。這是刻意的取捨，不是漏掉。
    ("內部角色",    r"(?i)\b(?:builder|lead|docs)\s*(?=20\d\d-|：|:)"),
    ("內部角色",    r"(?i)\b(?:builder|lead|docs|auditor|researcher|reviewer)\b"
                    r"\s*[／/、,和]\s*"
                    r"(?:builder|lead|docs|auditor|researcher|reviewer)\b"),
    ("品保文件編號", r"\bQE[WFP]-[A-Z0-9]+(?:-[A-Z0-9]+)*"),
    ("本機路徑",    r"/Users/"),
    ("本機路徑",    r"~/(?:Desktop|Projects|Local-)"),
    ("本機路徑",    r"Local-敏感"),
    ("保管庫",      r"Claude's Brain"),
    ("保管庫資料夾", r"\b0[0-5]-(?:Inbox|Notes|Documents|Scripts|Logs|Projects)\b"),
    ("保管庫資料夾", r"\b99-Archive\b"),
    ("內部文件",    r"\bCLAUDE\.md\b"),
    ("memory 檔名",  r"(?i)\bmemory[:：]\s*[a-z0-9-]{4,}"),
    ("內部主機",    r"(?i)\b(?:admin|quality|spc|hr)\.cemi-tw\.com\b"),
    ("droplet IP",  r"\b159\.223\.33\.59\b"),
]
COMPILED = [(tag, re.compile(p)) for tag, p in RULES]

#: 🔴 **HTML 另外加嚴**：`index.html` 整份都會送給每一個訪客，view-source 就看得到
#:   —— 曝光比 repo 還大（repo 要有人特地去看，網頁是自動送的）。
#:   原則：線上頁面的註解只說「這段程式做什麼」，**不說「誰在什麼時候決定的」**。
#:   所以在 .html 裡，日期和裁示字眼本身就算違規，不必等人名出現。
HTML_RULES = [
    ("HTML 註解裡的日期",   r"\b20\d\d-\d\d-\d\d\b"),
    ("HTML 註解裡的決策",   r"裁示|決議|核准|approved by|sign-?off"),
]
HTML_COMPILED = [(tag, re.compile(p)) for tag, p in HTML_RULES]

#: 只掃會進 repo 的純文字檔。
EXTS = {".html", ".md", ".py", ".mjs", ".js", ".json", ".css", ".txt", ".yml", ".yaml"}
SKIP_DIRS = {".git", "node_modules", "src"}          # tools/fonts/src 是 gitignore 的來源字型


def tracked_files(staged=False):
    cmd = ["git", "-C", ROOT, "diff", "--cached", "--name-only", "--diff-filter=ACM"] \
        if staged else ["git", "-C", ROOT, "ls-files"]
    r = subprocess.run(cmd, capture_output=True, text=True)
    if r.returncode != 0:
        print("🔴 git 失敗，**這一支沒有量到東西**：%s" % r.stderr.strip())
        sys.exit(2)
    out = []
    for rel in r.stdout.splitlines():
        if not rel.strip():
            continue
        p = os.path.join(ROOT, rel)
        if not os.path.isfile(p):
            continue
        if any(part in SKIP_DIRS for part in rel.split(os.sep)):
            continue
        if os.path.splitext(rel)[1].lower() not in EXTS:
            continue
        out.append((rel, p))
    return out


def scan_text(text, html=False):
    """回 [(行號, 標籤, 命中的字)]。`html=True` 時多套 HTML_RULES。"""
    rules = COMPILED + (HTML_COMPILED if html else [])
    hits = []
    for i, line in enumerate(text.splitlines(), 1):
        for tag, rx in rules:
            for m in rx.finditer(line):
                hits.append((i, tag, m.group(0)))
    return hits


def scan_files(files):
    bad = []
    for rel, p in files:
        if os.path.abspath(p) == SELF:
            continue                      # 見檔頭：守門自己跳過
        try:
            text = io.open(p, encoding="utf-8").read()
        except (UnicodeDecodeError, OSError):
            continue
        is_html = rel.lower().endswith(".html")
        for ln, tag, s in scan_text(text, html=is_html):
            bad.append((rel, ln, tag, s))
    return bad


def positive_control():
    """造一個含違規字串的暫存檔，掃描器**必須**每一條都抓到。

    沒有這一步的話，「0 處」有兩種成因分不開：真的乾淨，或掃描器根本沒在動
    （樣式打錯、檔案清單是空的、副檔名過濾把全部濾掉）。
    """
    probes = [
        ("人名",            "決定是 Nick 下的", False),
        ("內部角色",         "auditor 抓到的", False),
        ("內部角色",         "builder 2026-11-18 做的", False),
        ("內部角色",         "和 lead／auditor 講過的", False),
        ("品保文件編號",      "依 QEW-P18-I-12-C 修正", False),
        ("本機路徑",         "放在 /Users/someone/x", False),
        ("本機路徑",         "放在 ~/Desktop 底下", False),
        ("保管庫",           "寫到 Claude's Brain 裡", False),
        ("保管庫資料夾",      "報告在 05-Projects/abc", False),
        ("內部文件",         "規則寫在 CLAUDE.md", False),
        ("memory 檔名",      "見 memory: some-topic-name", False),
        ("內部主機",         "部署到 admin.cemi-tw.com", False),
        ("droplet IP",       "連到 159.223.33.59", False),
        # HTML 專用的兩條：只有 html=True 才該命中
        ("HTML 註解裡的日期", "<!-- 2026-11-18 改的 -->", True),
        ("HTML 註解裡的決策", "<!-- 經裁示改成這樣 -->", True),
    ]
    missed = []
    for want_tag, line, as_html in probes:
        tags = {t for _, t, _ in scan_text(line, html=as_html)}
        if want_tag not in tags:
            missed.append((want_tag, line, sorted(tags)))
    # 🔴 反過來也要問：對的時候會通過嗎。乾淨的句子不可以被誤判 ——
    #    而且 HTML 專用的兩條**不可以**在非 HTML 的檔案上命中（不然 tools/ 的
    #    文件寫個日期就紅了，守門會被當成雜訊關掉）。
    false_pos = scan_text(
        "這一行沒有違規字串：lead time、leading、a builder pattern 都不算。")
    html_leak = scan_text("2026-11-18 這個日期在 .md 裡是合法的", html=False)
    return missed, false_pos + html_leak


def scan_one_file(path):
    """掃一個**不在 repo 裡**的檔，例如存成檔案的 PR 內文。

    PR 內文和 repo 一樣是公開的，而且改它會留下編輯歷史 ——
    所以新的那一版要先掃乾淨再存上去。
    """
    text = io.open(path, encoding="utf-8").read()
    hits = scan_text(text, html=path.lower().endswith(".html"))
    if hits:
        print("🔴 %s 有 %d 處不該公開的字串：" % (path, len(hits)))
        for ln, tag, s in hits:
            print("   第 %d 行  [%s] %s" % (ln, tag, s))
        return 1
    print("✅ %s：沒有發現不該公開的字串" % path)
    return 0


def main():
    staged = "--staged" in sys.argv
    missed, false_pos = positive_control()
    if missed:
        print("🔴 正向對照失敗 —— **掃描器本身壞了，下面的結果不算數**：")
        for tag, line, got in missed:
            print("   「%s」應該被判成 %s，實際判成 %s" % (line, tag, got or "沒有命中"))
        return 2
    if false_pos:
        print("🔴 誤判：乾淨的句子被判成違規 %s" % false_pos)
        return 2
    print("✅ 正向對照：%d 條樣式都抓得到，乾淨的句子不誤判"
          % (len(COMPILED) + len(HTML_COMPILED)))

    if "--file" in sys.argv:
        return scan_one_file(sys.argv[sys.argv.index("--file") + 1])

    files = tracked_files(staged)
    if not files:
        print("🔴 一個檔都沒掃到（%s）—— 這不是「乾淨」，是**沒有量到**"
              % ("已暫存的檔" if staged else "git ls-files"))
        return 2
    bad = scan_files(files)
    print("   掃了 %d 個檔%s" % (len(files), "（已暫存）" if staged else ""))
    if bad:
        print("\n🔴 發現 %d 處不該進公開 repo 的字串：" % len(bad))
        for rel, ln, tag, s in bad:
            print("   %s:%d  [%s] %s" % (rel, ln, tag, s))
        print("\n改成中性的寫法（寫決定本身，不寫是誰說的）再推。")
        return 1
    print("✅ 沒有發現不該進公開 repo 的字串")
    return 0


if __name__ == "__main__":
    sys.exit(main())
