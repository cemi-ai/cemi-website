#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""把字型子集化成這個網站真正用到的字，並且**檢查一個字都沒少**。

只在開發機上跑，不是網站的一部分（實作規格 §11 第 1 項）。

為什麼要自架字型（規格 §8）：現況整頁 18 KiB、0 個字型請求。外連的 CSS
會擋住渲染，對方連不上時頁面要等到逾時才出現。文字已經定稿，子集做一次就好。

🔴 **這一支最重要的不是「產生子集」，是最後那一步「驗涵蓋」。**
   少一個字的時候，瀏覽器會自動拿系統字補上 —— 頁面**不會壞**，只會混進
   另一種字形，而那在截圖上很難一眼看出來。所以：
   子集做完之後，逐字去查輸出字型的 cmap，少一個就 exit 1。

用法：
    python3 tools/fonts/subset_fonts.py                # 用 repo 的 index.html
    python3 tools/fonts/subset_fonts.py --check-only   # 只驗涵蓋，不重做
    python3 tools/fonts/subset_fonts.py --html <檔>

來源字型要先放在 `--src`（預設 `tools/fonts/src/`），見同目錄的 README。
"""
from __future__ import annotations

import argparse
import hashlib
import html as htmlmod
import io
import os
import re
import sys
import unicodedata

ROOT = os.path.abspath(os.path.join(os.path.dirname(__file__), "..", ".."))

# 🔴 **讓輸出可以重現**（程式碼審查提出）。
#   不設的話每次重跑只有 `head.modified` 不同（實測兩次相差 11 秒），
#   輸出的 sha256 就會變 —— 於是「重跑看看有沒有變」量不到東西。
#
#   ⚠ **直接寫 `font["head"].modified = 0` 沒有用**：fontTools 在存檔時會把
#     那一欄覆寫成「現在」。我先那樣做，兩次跑出來還是差 11 秒才發現。
#     正確的做法是 `SOURCE_DATE_EPOCH` —— fontTools（和其他可重現建置的
#     工具）都認這個環境變數。
#   用 2026-11-18 00:00:00 UTC；外面有設就尊重外面的。
os.environ.setdefault("SOURCE_DATE_EPOCH", "1763424000")

#: 拉丁字型要涵蓋的範圍（規格 §8）。這是**固定清單**，不是從頁面推出來的：
#: 版面上的箭頭、引號、破折號有些是 CSS 產生的，`innerText` 裡看不到。
LATIN = (
    [chr(c) for c in range(0x20, 0x7F)] +
    [chr(c) for c in range(0xA0, 0x100)] +
    ["–", "—", "‘", "’", "“", "”",
     "•", "…", "→", " "]
)

#: ⚠ **IBM Plex Mono 沒有 U+2002（EN SPACE）**，Plex Sans 有（實測）。
#: 規格 §3.3 原本讓區塊小標（Mono）的編號後面接一個 em space。
#: 照字面做的話，每一個區塊小標都會為了那一個空白退回系統字。
#: → 改用 CSS 的間距（margin-inline-end）來拉開，不用那個字元。
#:   `::before` 的內容本來就不在 `innerText` 裡，所以 copy-diff 不受影響。
#: 次要連結「→」那一處用的是 Plex **Sans**，U+2002 有，照規格寫。
MONO_SKIP = {"\u2002"}

#: 輸出：(檔名, 來源, 取哪個字重, 要哪一組字)
OUTPUTS = [
    ("ibm-plex-sans-400.woff2", "IBMPlexSans[wdth,wght].ttf", {"wght": 400, "wdth": 100}, "latin"),
    ("ibm-plex-sans-600.woff2", "IBMPlexSans[wdth,wght].ttf", {"wght": 600, "wdth": 100}, "latin"),
    ("ibm-plex-mono-500.woff2", "IBMPlexMono-Medium.ttf", None, "latin"),
    ("noto-sans-tc-400.woff2", "NotoSansTC[wght].ttf", {"wght": 400}, "cjk"),
    ("noto-sans-tc-700.woff2", "NotoSansTC[wght].ttf", {"wght": 700}, "cjk"),
]

BUDGET = 400 * 1024          # 規格 §8：五個檔合計 ≤ 400 KB


def page_chars(html_path):
    """頁面上看得到的字（中英兩種語言都算）。

    兩種語言的文字在 61bef92 的 DOM 裡是兩個並排的 `<span data-lang>`，
    所以**直接讀 HTML 就兩種都拿得到**，不必起瀏覽器切語言。
    ⚠ 反過來說：CSS 產生的內容（`::before` 的編號、箭頭）讀不到，
      那幾個字已經寫死在 `LATIN` 裡。
    """
    raw = io.open(html_path, encoding="utf-8").read()
    raw = re.sub(r"<!--.*?-->", " ", raw, flags=re.S)
    raw = re.sub(r"<(script|style)\b.*?</\1>", " ", raw, flags=re.S | re.I)
    # 屬性裡的字也算：`data-title-en`／`zh`、alt、title 都會顯示給人或報讀軟體
    attrs = " ".join(re.findall(r'(?:data-title-\w+|alt|title|aria-label)="([^"]*)"', raw))
    text = re.sub(r"<[^>]+>", " ", raw) + " " + attrs
    text = htmlmod.unescape(text)
    return set(text) | css_content_chars(io.open(html_path, encoding="utf-8").read())


#: `content: "…"` 裡的字元 —— **CSS 產生的內容不在 innerText 裡**。
CSS_CONTENT = re.compile(r'content\s*:\s*([^;}]+)')
CSS_STRING = re.compile(r'"((?:[^"\\]|\\.)*)"')
CSS_ESCAPE = re.compile(r'\\([0-9A-Fa-f]{1,6})\s?')


def css_content_chars(raw):
    """從 `<style>` 的 `content:` 抽出會被畫出來的字元。

    🔴 **為什麼不能用寫死的清單**（我 2026-11-18 就是這樣錯的）：
    我照規格的散文把「em space」記成 U+2002，實際 CSS 裡寫的是 `\2003`
    （U+2003 EM SPACE；U+2002 是 EN SPACE）。清單少了那個字 → 子集少了它
    → 每一個區塊小標的那個空白都會退回系統字，而**涵蓋檢查看不到**，
    因為 CSS 產生的內容不在 `innerText` 裡。
    改成直接從 CSS 讀，清單就不會和實際用的字漂開。
    """
    out = set()
    style = "\n".join(re.findall(r"<style\b[^>]*>(.*?)</style>", raw, flags=re.S | re.I))
    for val in CSS_CONTENT.findall(style):
        for lit in CSS_STRING.findall(val):
            s2 = CSS_ESCAPE.sub(lambda m: chr(int(m.group(1), 16)), lit)
            out |= set(s2.replace("\\", ""))
    return out


def split_chars(chars):
    """分成「拉丁字型要涵蓋的」和「中文字型要涵蓋的」。"""
    latin, cjk = set(LATIN), set()
    for ch in chars:
        if ch in ("\n", "\r", "\t"):
            continue
        o = ord(ch)
        if o < 0x100 or ch in latin:
            continue
        # 全形標點、CJK、注音、全形英數 → 交給 Noto
        if (0x2E80 <= o <= 0x9FFF or 0x3000 <= o <= 0x303F
                or 0xFF00 <= o <= 0xFFEF or 0x3100 <= o <= 0x312F
                or 0xF900 <= o <= 0xFAFF):
            cjk.add(ch)
        else:
            latin.add(ch)       # 其他的（例如 ±、×）交給拉丁字型
    return latin, cjk


def build(src_dir, out_dir, latin, cjk, verbose=True):
    from fontTools import subset as ftsubset
    from fontTools.ttLib import TTFont
    from fontTools.varLib import instancer

    os.makedirs(out_dir, exist_ok=True)
    made = []
    for name, src, axes, which in OUTPUTS:
        sp = os.path.join(src_dir, src)
        if not os.path.exists(sp):
            print("🔴 找不到來源字型：%s" % sp)
            print("   見 tools/fonts/README.md 的「來源」一節")
            return None
        font = TTFont(sp)
        if axes:
            font = instancer.instantiateVariableFont(font, axes, inplace=False)
        want = latin if which == "latin" else cjk
        opts = ftsubset.Options()
        opts.flavor = "woff2"
        opts.desubroutinize = False
        opts.layout_features = ["kern", "liga", "ccmp", "locl", "mark", "mkmk"]
        opts.name_IDs = [0, 1, 2, 3, 4, 5, 6, 13, 14]   # 留著版權和授權欄位
        opts.notdef_outline = True
        subsetter = ftsubset.Subsetter(options=opts)
        subsetter.populate(text="".join(sorted(want)))
        subsetter.subset(font)
        op = os.path.join(out_dir, name)
        font.flavor = "woff2"
        font.save(op)
        font.close()
        size = os.path.getsize(op)
        made.append((name, size, len(want)))
        if verbose:
            print("  產生 %-26s %6.1f KB（%d 個字）" % (name, size / 1024.0, len(want)))
    return made


def _cmap(path):
    from fontTools.ttLib import TTFont
    f = TTFont(path)
    have = set()
    for t in f["cmap"].tables:
        have |= set(t.cmap.keys())
    f.close()
    return have


def coverage(src_dir, out_dir, latin, cjk):
    """逐字查 cmap，回 (真的掉了, 來源本來就沒有)。

    🔴 **這兩件事要分開**，不可以都算成「少字」：

    * **子集掉了** → 是 bug。來源有、輸出沒有，表示子集化把它丟了。
    * **來源本來就沒有** → 不是 bug，是個要有人決定的事實。
      例如 IBM Plex **Mono** 沒有 U+2002（EN SPACE），而 Plex Sans 有。
      把兩者混在一起報「少 1 個字」，下一個人只會把那個字從清單刪掉 ——
      那就同時把真正的 bug 也關掉了。
    """
    lost, absent = [], []
    for name, src, _axes, which in OUTPUTS:
        fp = os.path.join(out_dir, name)
        if not os.path.exists(fp):
            lost.append((name, "檔案不存在", []))
            continue
        have = _cmap(fp)
        src_have = _cmap(os.path.join(src_dir, src))
        want = latin if which == "latin" else cjk
        if "mono" in name:
            want = want - MONO_SKIP     # 只剩 U+2002：Sans 用得到，Mono 用不到
        miss = [ch for ch in sorted(want) if ord(ch) not in have]
        a = [ch for ch in miss if ord(ch) not in src_have]
        l = [ch for ch in miss if ord(ch) in src_have]
        if l:
            lost.append((name, "子集掉了 %d 個字" % len(l), l[:20]))
        if a:
            absent.append((name, src, a))
    return lost, absent


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--html", default=os.path.join(ROOT, "index.html"))
    ap.add_argument("--src", default=os.path.join(ROOT, "tools", "fonts", "src"))
    ap.add_argument("--out", default=os.path.join(ROOT, "fonts"))
    ap.add_argument("--check-only", action="store_true")
    a = ap.parse_args()

    if not os.path.exists(a.html):
        print("🔴 找不到 %s" % a.html)
        return 1
    chars = page_chars(a.html)
    latin, cjk = split_chars(chars)
    print("# 子集字型（來源：%s）" % os.path.relpath(a.html, ROOT))
    print("  頁面上的字：拉丁 %d（含固定清單）、中文等 %d" % (len(latin), len(cjk)))
    if not cjk:
        print("🔴 一個中文字都沒抽到 —— 抽取壞了，不是「頁面沒有中文」")
        return 1

    if not a.check_only:
        if build(a.src, a.out, latin, cjk) is None:
            return 1

    print()
    print("# 🔴 涵蓋檢查（少一個字，瀏覽器會拿系統字補上，頁面不會壞但會混字形）")
    # 🔴 **把豁免印出來。** `MONO_SKIP` 是一個**沒有被驗證的假設**：它假設沒有任何
    #    Mono 的元素會用到那些字。`.tag::before` 和 `.node::before` 都是 Mono，
    #    哪天有人在那裡放一個 en space，涵蓋檢查會安靜地放行。
    #    印出來不能讓假設變成真的，但至少讓它是**看得見**的，而不是埋在程式裡。
    if MONO_SKIP:
        print("  ⚠ Mono 豁免（假設沒有 Mono 元素用到）：%s"
              % " ".join("U+%04X" % ord(c) for c in sorted(MONO_SKIP)))
    lost, absent = coverage(a.src, a.out, latin, cjk)
    for name, src, chs in absent:
        print("  ⚠ %s：來源字型（%s）**本來就沒有** %d 個字 —— 不是子集的問題"
              % (name, src, len(chs)))
        print("     %s" % " ".join("U+%04X" % ord(c) for c in chs))
        print("     空白類的退回系統字看不出來；有形狀的會混字形，要換字型或換做法。")
    if lost:
        for name, why, sample in lost:
            print("  🔴 %s：%s —— **來源有、輸出沒有，是子集化掉的**" % (name, why))
            if sample:
                print("     例如：%s" % " ".join(sample))
        return 1
    print("  ✅ 來源有的字，五個輸出一個都沒掉")

    total = sum(os.path.getsize(os.path.join(a.out, n)) for n, _, _, _ in OUTPUTS)
    print()
    print("# 大小（規格 §8 的預算：%d KB）" % (BUDGET // 1024))
    for n, _, _, _ in OUTPUTS:
        p = os.path.join(a.out, n)
        print("  %-26s %6.1f KB  sha256 %s…"
              % (n, os.path.getsize(p) / 1024.0,
                 hashlib.sha256(open(p, "rb").read()).hexdigest()[:16]))
    print("  %-26s %6.1f KB" % ("合計", total / 1024.0))
    if total > BUDGET:
        print("  🔴 **超過預算** —— 規格說這時候中文要改回系統字，只自架 Plex")
        return 1
    print("  ✅ 在預算內（還剩 %.1f KB）" % ((BUDGET - total) / 1024.0))
    return 0


if __name__ == "__main__":
    sys.exit(main())
