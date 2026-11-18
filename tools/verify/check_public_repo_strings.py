#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""守門：不該出現在**公開 repo** 的字串。

這個 repo 是 public。任何進到 commit 的東西就留在歷史裡 ——
事後要清只能 force push，而那是禁止的。所以這一支要在**推之前**跑。

🔴🔴 **這支檔案自己也是公開的，而第一版正好犯了它要防的錯。**
第一版把真的文件編號、真的主機位址、真的帳號當成「樣本」和「樣式」寫在裡面，
再讓守門**跳過自己**，於是它看不到自己。
而且那比單獨洩漏那些值更糟：一份**替每一項標好類別**的清單就是一張架構圖。

所以這一版分成兩半：

* **公開的這一半（本檔）只有「形狀」** —— 通用的 IPv4、子網域的樣式、
  編號的格式、`/Users/<名字>` 這一類。樣本一律用造出來的假值
  （RFC 5737 的文件用位址、RFC 2606 的 example.*）。
* **真值那一半放在 repo 外**：`~/.config/cemi-website/public-denylist`
  （目錄 0700、檔案 0600，和放憑證同一個慣例）。這個檔**不進 repo、不進備份、
  不進任何訊息或 log**。守門啟動時讀它，連正向對照都用它的內容**現場造**。

另外兩條硬規則：

* **守門不跳過自己。** 它和別的檔走同一條路。能通過的唯一方式是檔案裡只出現
  `FAKE_OK` 那幾個刻意造的假值 —— 真值一旦被貼進來，它自己就會紅。
* **沒有 denylist 檔的時候要大聲說**，不可以默默當成全部通過：那種情況下
  只有形狀檢查跑過，人名和主機名**完全沒有被檢查**。

代價講明：denylist 是一份**要人維護的清單**，只擋想得到的東西。
它是**下限不是保證** —— 真正的防線還是推之前自己看一次 diff。

用法：
    python3 tools/verify/check_public_repo_strings.py             # 掃整個 repo
    python3 tools/verify/check_public_repo_strings.py --staged    # 只掃已暫存的
    python3 tools/verify/check_public_repo_strings.py --file X    # 掃一個外部檔（PR 內文）
    python3 tools/verify/check_public_repo_strings.py --require-denylist   # 沒清單就失敗

denylist 檔的格式（一行一條，`類別=字`，`#` 開頭是註解）：

    人名=某個名字
    內部角色=某個角色名
    內部主機=某台主機的完整名稱
"""
from __future__ import annotations

import io
import os
import re
import subprocess
import sys

HERE = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.abspath(os.path.join(HERE, "..", ".."))

DENYLIST_PATH = os.environ.get(
    "CEMI_PUBLIC_DENYLIST",
    os.path.expanduser("~/.config/cemi-website/public-denylist"))

# 🔴 刻意造出來的假值。**只有這些**可以當樣本出現在這個檔案裡。
#   IP 用 RFC 5737（TEST-NET），網域用 RFC 2606 的 example.*。
FAKE_OK = {
    "203.0.113.7", "192.0.2.1", "198.51.100.1",
    "127.0.0.1", "0.0.0.0", "255.255.255.255",
    "example.com", "example.org",
    "ABC-X99-Z-00-Q",
}

# ════════════════════════════════════════════════════════════════════
#  形狀（公開）—— 通用樣式，不列舉任何真的主機／前綴／帳號
# ════════════════════════════════════════════════════════════════════
RULES = [
    ("IP 位址",      r"\b(?:\d{1,3}\.){3}\d{1,3}\b"),
    # 公司網域的**子網域**。主網域本身是公開官網（canonical 會用到），不算。
    ("內部主機",     r"\b(?!www\.)[a-z0-9-]+\.cemi-tw\.com\b"),
    # 文件編號的**形狀**（不列真的前綴）：字母-字母數字-…
    ("文件編號",     r"\b[A-Z]{2,4}-[A-Z]\d{1,3}(?:-[A-Z0-9]{1,4}){1,4}\b"),
    # 🔴 要求後面真的有個使用者名稱 —— 這樣這一行樣式本身才不會配到自己。
    ("本機路徑",     r"/Users/[A-Za-z0-9._-]+"),
    ("本機路徑",     r"~/(?:Desktop|Projects)"),
    ("內部資料夾",   r"\b\d{2}-(?:Inbox|Notes|Documents|Scripts|Logs|Projects|Archive)\b"),
    ("內部筆記參照",  r"(?i)\bmemory[:：]\s*[a-z0-9-]{4,}"),
]
COMPILED = [(tag, re.compile(p)) for tag, p in RULES]

#: 🔴 **HTML 另外加嚴**：`index.html` 整份都會送給每一個訪客，view-source 就看得到。
#:   原則：線上頁面的註解只說「這段程式做什麼」，**不說「誰在什麼時候決定的」**。
HTML_RULES = [
    ("HTML 註解裡的日期", r"\b20\d\d-\d\d-\d\d\b"),
    ("HTML 註解裡的決策", r"裁示|決議|核准|approved by|sign-?off"),
]
HTML_COMPILED = [(tag, re.compile(p)) for tag, p in HTML_RULES]

# `<!-- … -->` 和 `/* … */` —— HTML 檔裡這兩種註解都會送到瀏覽器。
COMMENT_RX = re.compile(r"<!--.*?-->|/\*.*?\*/", re.S)

EXTS = {".html", ".md", ".py", ".mjs", ".js", ".json", ".css", ".txt", ".yml", ".yaml"}
SKIP_DIRS = {".git", "node_modules", "src"}

ROLE_CATEGORY = "內部角色"


def load_denylist(path=DENYLIST_PATH):
    """回 [(類別, 已編譯樣式)]；檔案不在就回 None（**不是空清單**）。

    🔴 `None` 和 `[]` 要分得開：前者是「沒有清單」（這些類別根本沒檢查），
       後者是「清單是空的」。兩者混在一起的話，缺檔會看起來像全部通過。
    """
    if not os.path.exists(path):
        return None
    out = []
    for raw in io.open(path, encoding="utf-8"):
        line = raw.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        cat, _, word = line.partition("=")
        cat, word = cat.strip(), word.strip()
        if not word:
            continue
        rx = re.compile(r"(?<![A-Za-z0-9])" + re.escape(word) + r"(?![A-Za-z0-9])",
                        re.IGNORECASE)
        out.append((cat, rx))
    return out


def comment_spans(text):
    return [(m.start(), m.end()) for m in COMMENT_RX.finditer(text)]


def scan_text(text, html=False, deny=None):
    """回 [(行號, 標籤, 命中的字)]。"""
    hits = []
    spans = comment_spans(text) if html else []

    def in_comment(pos):
        return any(a <= pos < b for a, b in spans)

    for tag, rx in COMPILED + (HTML_COMPILED if html else []):
        for m in rx.finditer(text):
            s = m.group(0)
            if s in FAKE_OK:
                continue
            hits.append((text.count("\n", 0, m.start()) + 1, tag, s))

    for cat, rx in (deny or []):
        for m in rx.finditer(text):
            # 🔴 角色名在 HTML 裡：**註解裡一律算違規**，不需要「接日期或冒號」
            #    那種寬條件。註解外面不算 —— 那些字也可能是 CSS class
            #    （這一頁就有一個），只會出現在選擇器和屬性裡。
            if html and cat == ROLE_CATEGORY and not in_comment(m.start()):
                continue
            hits.append((text.count("\n", 0, m.start()) + 1, cat, m.group(0)))
    return sorted(set(hits))


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
        if not os.path.isfile(p) or os.path.splitext(rel)[1].lower() not in EXTS:
            continue
        if any(part in SKIP_DIRS for part in rel.split(os.sep)):
            continue
        out.append((rel, p))
    return out


def scan_files(files, deny):
    bad = []
    for rel, p in files:
        # 🔴 **不跳過自己**（見檔頭）。
        try:
            text = io.open(p, encoding="utf-8").read()
        except (UnicodeDecodeError, OSError):
            continue
        for ln, tag, s in scan_text(text, rel.lower().endswith(".html"), deny):
            bad.append((rel, ln, tag, s))
    return bad


def positive_control(deny):
    """證明每一條路都會動。

    🔴 形狀那幾條的樣本是**組出來的**，不是寫死的字面值 ——
       因為寫死的話，那個值就會出現在這個公開檔案裡，而它（故意）不在
       `FAKE_OK` 裡，守門掃自己的時候就會紅。組出來的話檔案裡沒有完整的字串。
    🔴 真值那幾條的樣本**來自 denylist**，所以這個檔案裡一樣沒有它們。
       失敗時只印類別和第幾條，**不印字**。
    """
    probe_ip = "203.0.113." + str(7 + 1)
    probe_host = "secret-host" + ".cemi-tw.com"
    # ⚠ 切的位置要讓**部分比對**也配不到。把字串切在最後一碼之前的話，
    #   前半段自己就是一個合法的編號形狀，而它不在 FAKE_OK 裡 → 守門會抓到自己。
    #   （所以這一段註解也不可以把那個形狀寫出來。）
    probe_doc = "ABC" + "-X99-Z-00-R"
    probe_home = "/Users/" + "someone/x"

    probes = [
        ("IP 位址",       "連到 %s 看看" % probe_ip, False),
        ("內部主機",      "部署到 %s" % probe_host, False),
        ("文件編號",      "依 %s 修正" % probe_doc, False),
        ("本機路徑",      "放在 %s" % probe_home, False),
        ("本機路徑",      "放在 ~/Desk" + "top 底下", False),
        ("內部資料夾",    "報告在 05-Pro" + "jects/abc", False),
        ("內部筆記參照",   "見 memo" + "ry: some-topic-name", False),
        ("HTML 註解裡的日期", "<!-- 2026-11-18 改的 -->", True),
        ("HTML 註解裡的決策", "<!-- 經裁示改成這樣 -->", True),
    ]
    missed = []
    for want, line, as_html in probes:
        tags = {t for _, t, _ in scan_text(line, html=as_html, deny=deny)}
        if want not in tags:
            missed.append((want, sorted(tags)))

    # denylist 的每一條都要抓得到（訊息不印原字）
    for i, (cat, rx) in enumerate(deny or []):
        word = rx.pattern  # 已 escape，但只用來造輸入，不印出來
        sample = re.sub(r"\\(.)", r"\1",
                        word.replace(r"(?<![A-Za-z0-9])", "").replace(r"(?![A-Za-z0-9])", ""))
        tags = {t for _, t, _ in scan_text("前後有字 %s 後面" % sample, deny=deny)}
        if cat not in tags:
            missed.append(("denylist 第 %d 條（%s）" % (i + 1, cat), sorted(tags)))

    # 反過來：對的時候要通過。
    clean = []
    clean += scan_text("假值：203.0.113.7、example.com、ABC-X99-Z-00-Q。", deny=deny)
    clean += scan_text("日期 2026-11-18 在 .md 裡合法", html=False, deny=deny)
    # ⚠ 同樣要組出來：這個 class 名稱本身就在 denylist 的角色名裡，
    #   寫成字面值的話守門掃到自己這一行就會紅。
    css_class_probe = '<p class="le' + 'ad">x</p>'
    clean += [x for x in scan_text(css_class_probe, html=True, deny=deny)
              if x[1] == ROLE_CATEGORY]
    return missed, clean


def scan_one_file(path, deny):
    text = io.open(path, encoding="utf-8").read()
    hits = scan_text(text, path.lower().endswith(".html"), deny)
    if hits:
        print("🔴 %s 有 %d 處不該公開的字串：" % (path, len(hits)))
        for ln, tag, s in hits:
            print("   第 %d 行  [%s] %s" % (ln, tag, s))
        return 1
    print("✅ %s：沒有發現不該公開的字串" % path)
    return 0


def main():
    deny = load_denylist()
    if deny is None:
        print("🔴🔴 **找不到真值清單** —— 只做了形狀檢查。")
        print("     人名、帳號、主機名、內部資料夾名這幾類**完全沒有被檢查**。")
        print("     這不等於乾淨。清單要放在：%s（目錄 0700、檔案 0600）" % DENYLIST_PATH)
        if "--require-denylist" in sys.argv:
            return 2
        deny = []
    else:
        print("✅ 真值清單：%d 條（內容不印出來）" % len(deny))

    missed, false_pos = positive_control(deny)
    if missed:
        print("🔴 正向對照失敗 —— **掃描器本身壞了，下面的結果不算數**：")
        for tag, got in missed:
            print("   %s 應該被抓到，實際判成 %s" % (tag, got or "沒有命中"))
        return 2
    if false_pos:
        print("🔴 誤判：乾淨的輸入被判成違規 %s" % false_pos)
        return 2
    print("✅ 正向對照：每一類都抓得到，假值和 CSS class 不誤判")

    if "--file" in sys.argv:
        return scan_one_file(sys.argv[sys.argv.index("--file") + 1], deny)

    files = tracked_files("--staged" in sys.argv)
    if not files:
        print("🔴 一個檔都沒掃到 —— 這不是「乾淨」，是**沒有量到**")
        return 2
    bad = scan_files(files, deny)
    print("   掃了 %d 個檔（**包含這支守門自己**）" % len(files))
    if bad:
        print("\n🔴 發現 %d 處不該進公開 repo 的字串：" % len(bad))
        for rel, ln, tag, s in bad:
            print("   %s:%d  [%s] %s" % (rel, ln, tag, s))
        return 1
    print("✅ 沒有發現不該進公開 repo 的字串")
    return 0


if __name__ == "__main__":
    sys.exit(main())
