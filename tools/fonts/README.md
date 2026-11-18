# `tools/fonts/` —— 字型子集工具

**只在開發機上用，不是網站的一部分。** GitHub Pages 只服務 repo 根目錄的
`index.html` 和它引用的檔案；這個目錄不會被任何人下載到。

依實作規格 §11 第 1 項放進 repo，條件是附這份 README。

## 做什麼

把 OFL 的原始字型切成「這個網站真正用到的字」，並且**逐字檢查一個都沒少**。

```sh
python3 tools/fonts/subset_fonts.py               # 重做子集 ＋ 驗涵蓋
python3 tools/fonts/subset_fonts.py --check-only  # 只驗涵蓋（不重做）
python3 tools/fonts/subset_fonts.py --html 某檔   # 針對別的 HTML
```

🔴 **改了頁面上的任何中文字，就要重跑一次。** 少一個字的時候瀏覽器會拿
系統字補上 —— 頁面不會壞，只會混進另一種字形。所以這支工具最重要的不是
「產生子集」，是最後那一步「驗涵蓋」。

## 需要什麼

| | |
|---|---|
| Python | 3.9＋（B 上用系統的 `python3`） |
| `fonttools` | 子集化（B 上是 4.60.2） |
| `brotli` | WOFF2 的壓縮；**沒有它 fontTools 會在存檔時才失敗** |

```sh
python3 -m pip install --user fonttools brotli
```

## 來源字型（不進 repo）

`NotoSansTC[wght].ttf` 有 11 MB，三個來源加起來 12 MB —— 放進一個
GitHub Pages 的 repo 不划算，所以 `src/` 由 `.gitignore` 擋住。

要重做子集時重新抓，**sha256 對得上才算同一份**（值記在 `fonts/README.md`）：

```sh
mkdir -p tools/fonts/src && cd tools/fonts/src
R=https://raw.githubusercontent.com/google/fonts
# 🔴 **用 -o 指定檔名，不要用 -LO。** `-LO` 會照 URL 存成
#    `IBMPlexSans%5Bwdth%2Cwght%5D.ttf`（百分號編碼沒有被還原），
#    而 subset_fonts.py 找的是 `IBMPlexSans[wdth,wght].ttf` → FileNotFoundError。
#    （審查時實測過。）
# 🔴 **釘 commit，不要用 main。** main 會變，哪天上游更新字型，
#    「同一條指令」就會抓到不同的位元組，而 sha256 的表會突然對不上。
curl -L -o 'IBMPlexSans[wdth,wght].ttf' \
  "$R/8409f033cd7d/ofl/ibmplexsans/IBMPlexSans%5Bwdth,wght%5D.ttf"
curl -L -o 'IBMPlexMono-Medium.ttf' \
  "$R/633f3200539c/ofl/ibmplexmono/IBMPlexMono-Medium.ttf"
curl -L -o 'NotoSansTC[wght].ttf' \
  "$R/b950a7257470/ofl/notosanstc/NotoSansTC%5Bwght%5D.ttf"
shasum -a 256 *.ttf          # 和 fonts/README.md 的表對一次
```

變動軸字型（Plex Sans、Noto TC）會先用 `fontTools.varLib.instancer`
取出需要的那個字重，再子集。

## 它怎麼決定「要哪些字」

1. 讀 `index.html`：去掉註解、`<script>`、`<style>`，再去掉標籤，
   加上 `alt`／`title`／`aria-label`／`data-title-*` 這些**會顯示給人或
   報讀軟體**的屬性。中英兩種語言的文字在 DOM 裡是並排的兩個 `<span>`，
   所以讀 HTML 就兩種都拿得到，不必起瀏覽器。
2. 🔴 **再從 `<style>` 裡把 `content: "…"` 的內容抽出來**（含 `\XXXX` 跳脫）。
   CSS 產生的字元不在 `innerText` 裡 —— 區塊編號後面那個空白、連結後面的
   箭頭都是這樣來的。這一步一開始是一份**手寫清單**，而我把規格散文裡的
   「em space」記成了 U+2002（實際是 U+2003），清單就少了一個字，
   而涵蓋檢查也看不到它。改成直接讀 CSS 之後，清單不會再和實際用的字漂開。
3. 分成「拉丁字型要涵蓋的」和「中文字型要涵蓋的」兩組。

## 它怎麼判斷「少了字」

逐字查輸出字型的 `cmap`，而且**分成兩類**：

- **子集掉了**（來源有、輸出沒有）→ 🔴 是 bug，`exit 1`。
- **來源本來就沒有** → ⚠ 不是 bug，是要有人決定的事實
  （例：IBM Plex Mono 兩個空白 U+2002、U+2003 都沒有；Plex Sans 兩個都有）。

兩者混在一起報「少 N 個字」的話，下一個人只會把那個字從清單刪掉 ——
那會同時把真正的 bug 關掉。

最後再檢查五個檔**合計 ≤ 400 KB**（規格 §8 的預算）。超過的話規格說
中文要改回系統字、只自架 Plex。
