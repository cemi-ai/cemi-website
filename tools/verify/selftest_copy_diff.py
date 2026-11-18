#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""`copy-diff.mjs` 的反事實：把字改掉，它**必須**報紅。

🔴 **為什麼一定要有這一支**（auditor 2026-11-18 的 🟡1）：
`copy-diff.mjs` 原本把正規化後 ≤2 個字元的行整個略過 ——
理由是長行用「子字串比對」，而 1–2 個字的字串幾乎一定找得到。
代價是**這一頁最該守住的事實全部落在盲區**：

    `70+` → 正規化成 `70`（2 字元）→ 跳過
    `×4`  → 正規化成 `4`（1 字元）→ 跳過

實測過：把專利數 70+ 改成 80+、把 Spin Coater 的 ×4 改成 ×5，
**舊版一樣印「結果：通過」、rc 0**。而這一版的改版正好把那些數字
搬進了首屏的規格表和設備表。

所以這一支拿真的頁面做突變，逐條確認它會紅。沒有這一支的話，
「通過」只代表「我沒有去比那幾行」。

用法：
    PLAYWRIGHT_PATH=… python3 tools/verify/selftest_copy_diff.py
"""
from __future__ import annotations

import io
import os
import shutil
import subprocess
import sys
import tempfile

HERE = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.abspath(os.path.join(HERE, "..", ".."))
OLD = "/tmp/old-61bef92.html"
BASE = "61bef92"

#: (名稱, 原字串, 換成什麼, 預期)
CASES = [
    ("沒有突變 → 要通過", None, None, "通過"),
    ("🔴 專利數 70+ → 80+", "70+", "80+", "不通過"),
    ("🔴 Spin Coater ×4 → ×5", '<td class="qty">×4</td>', '<td class="qty">×5</td>', "不通過"),
    ("🔴 Mask Aligner ×3 → ×2", '<td class="qty">×3</td>', '<td class="qty">×2</td>', "不通過"),
    ("🔴 改一個普通的詞（長行，舊版也抓得到）", "precision manufacturing", "precise manufacturing", "不通過"),
]


def ensure_old():
    if os.path.exists(OLD):
        return True
    r = subprocess.run(["git", "-C", ROOT, "show", "%s:index.html" % BASE],
                       capture_output=True)
    if r.returncode != 0:
        print("🔴 取不到 %s:index.html —— 這一支沒量到" % BASE)
        return False
    io.open(OLD, "wb").write(r.stdout)
    return True


def run(html):
    r = subprocess.run(
        ["node", os.path.join(HERE, "copy-diff.mjs"), OLD, html,
         "--head", "--expect", os.path.join(HERE, "expect.json")],
        capture_output=True, text=True)
    out = r.stdout + r.stderr
    if "結果：通過" in out:
        return "通過", out
    if "結果：不通過" in out:
        return "不通過", out
    return "沒跑完", out


def main():
    if not ensure_old():
        return 1
    src = io.open(os.path.join(ROOT, "index.html"), encoding="utf-8").read()
    d = tempfile.mkdtemp(prefix="cdself-")
    good = bad = 0
    for name, old, new, want in CASES:
        html = src
        if old is not None:
            n = src.count(old)
            if n < 1:
                print("  🔴 %s：錨點在 index.html 裡出現 %d 次 —— **這一條沒套用**"
                      % (name, n))
                bad += 1
                continue
            html = src.replace(old, new)
        p = os.path.join(d, "mutated.html")
        io.open(p, "w", encoding="utf-8").write(html)
        got, out = run(p)
        if got == want:
            good += 1
            print("  ✅ %-38s %s" % (name, got))
        else:
            bad += 1
            print("  🔴 %-38s 預期 %s，實際 %s" % (name, want, got))
            print("     " + out.strip().replace("\n", "\n     ")[-500:])
    shutil.rmtree(d, ignore_errors=True)
    print()
    if bad:
        print("🔴 %d 條不符合預期" % bad)
        return 1
    print("✅ %d 條全部符合（短行的盲區確實補起來了）" % good)
    return 0


if __name__ == "__main__":
    sys.exit(main())
