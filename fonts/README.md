# 自架字型

這五個 `.woff2` 是**子集**，只含這個網站真正用到的字。由
`tools/fonts/subset_fonts.py` 產生，不要手改。

**改了頁面上的任何中文字就要重跑**，不然新字會退回系統字 ——
頁面不會壞，只會混進另一種字形，而那在截圖上很難一眼看出來。

```sh
python3 tools/fonts/subset_fonts.py              # 重做子集 ＋ 驗涵蓋
python3 tools/fonts/subset_fonts.py --check-only # 只驗涵蓋
```

## 為什麼自架（規格 §8）

- 現況整頁 18 KiB、**0 個字型請求**。外連的 CSS 會擋住渲染，對方連不上時
  頁面要等到逾時才出現。
- 文字已經定稿，子集只要做一次。
- `measure.mjs` 會檢查外部請求：不可以出現 `fonts.googleapis.com`。

## 來源（OFL 1.1）

從 `github.com/google/fonts` 取得，2026-11-18。

| 來源檔 | sha256 | 取自 |
|---|---|---|
| `IBMPlexSans[wdth,wght].ttf` | `3b031aa4216174205bd8471f88a49b91f093169e9e87bd5262242bc5967fe2e3` | `ofl/ibmplexsans/` |
| `IBMPlexMono-Medium.ttf` | `a9b4c49bb299e05b5f6c481e7fb5e78943d2793249a0c8874ab574a2d1ea6755` | `ofl/ibmplexmono/` |
| `NotoSansTC[wght].ttf` | `864727d210d54f2537bbe23b3a839436c3992af72de9322af5270897246bd44f` | `ofl/notosanstc/` |

來源字型**不進 repo**（Noto 那一個就 11 MB），放在 `tools/fonts/src/`，
由 `.gitignore` 擋住。要重做子集時照 `tools/fonts/README.md` 重新下載，
sha256 對得上才算同一份。

- 授權全文：`OFL-IBM-Plex.txt`（Sans 和 Mono 共用，實測兩份逐位元組相同）、
  `OFL-Noto-Sans-TC.txt`。兩份都是從上面同一個 repo 的同一個目錄取的 ——
  **授權要和實際出貨的那個檔案對應**，不要從別處找一份看起來一樣的。

## 產出（2026-11-18）

| 檔 | 大小 | sha256 |
|---|---|---|
| `ibm-plex-sans-400.woff2` | 16.4 KB | `999b76af0b010125…` |
| `ibm-plex-sans-600.woff2` | 17.2 KB | `7ab8cd4f16969c68…` |
| `ibm-plex-mono-500.woff2` | 11.8 KB | `16a8aec04224b42c…` |
| `noto-sans-tc-400.woff2` | 66.6 KB | `1caa42e4d9bc23b9…` |
| `noto-sans-tc-700.woff2` | 68.0 KB | `47365fbefbcfd811…` |
| **合計** | **180.2 KB** | 預算 400 KB（規格 §8），還剩 219.8 KB |

重跑一次會得到不同的 sha256（子集工具不保證位元組重現），**涵蓋和大小才是驗收標準**。

## 這個子集涵蓋什麼

- **拉丁**（Plex Sans 400/600、Plex Mono 500）：ASCII、Latin-1，加上頁面和
  CSS 真正用到的標點（`–` `—` 引號 `•` `…` `→` 和 EN SPACE）。
- **中文等**（Noto Sans TC 400/700）：從 `index.html` 抽出來的 402 個
  CJK／全形字元。
- 🔴 **CSS `content:` 產生的字元也算**。那些字不在 `innerText` 裡，所以
  工具會另外去 `<style>` 裡把 `content: "…"` 的內容（含 `\XXXX` 跳脫）抽出來。
  一開始我按規格的散文把「em space」記成 U+2002，實際 CSS 寫的是 `\2003`
  —— 清單少一個字、涵蓋檢查也看不到。改成直接從 CSS 讀之後就不會漂。

## 已知：來源字型本來就沒有的字

`subset_fonts.py` 會把「**子集掉了**」和「**來源本來就沒有**」分開報 ——
前者是 bug，後者是要有人決定的事實。混在一起的話，下一個人只會把那個字
從清單刪掉，順手把真正的 bug 也關掉。

- **IBM Plex Mono 沒有 U+2003（EM SPACE）**，Plex Sans 有。
  區塊小標（`.tag::before`，用 Mono）本來照規格 §3.3 在編號後面接一個
  em space；照字面做的話每一個小標都會為了那一個空白退回系統字。
  → 改成用 CSS 的 `margin-inline-end: 1em`。`::before` 的內容不在
  `innerText` 裡，所以和 `copy-diff` 無關。
