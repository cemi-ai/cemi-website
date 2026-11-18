// 版面量測：在 13 個寬度 × 中英兩種語言，量規格 §9 列的硬性條件。
// 量的是瀏覽器實際排出來的盒子（getBoundingClientRect），不是 CSS 寫的值。
//
// 用法：node measure.mjs <index.html 或 http://127.0.0.1:port/> [輸出資料夾] [--shots]
//   --shots：另外在 1440、768、375 拍整頁截圖（中、英），並輸出加上尺寸標註的線框圖。
// 只放行 file:、data:、127.0.0.1 和 Google Fonts（樣張用；正式版字型自架，應該 0 個外部請求）。
// 🔴 **不要寫死本機的 playwright 路徑。** 原稿是
//   `~/.npm/_npx/<雜湊>/node_modules/playwright/index.mjs` 的絕對路徑 ——
//   那在別人的機器上不存在，而且會把使用者名稱和快取雜湊帶進一個公開的 repo。
//   改成：先看 PLAYWRIGHT_PATH，再試一般的模組解析。
const PW_SPEC = process.env.PLAYWRIGHT_PATH || 'playwright';
let chromium;
try {
  ({ chromium } = await import(PW_SPEC));
} catch (e) {
  console.error('🔴 載入不到 playwright（試了：' + PW_SPEC + '）');
  console.error('   用這一行指到你機器上的那一份：');
  console.error('     PLAYWRIGHT_PATH=/絕對路徑/node_modules/playwright/index.mjs node <這支>');
  console.error('   或在這個 repo 裝一份：npm i -D playwright');
  process.exit(2);
}
import fs from 'fs';
import path from 'path';

const target = process.argv[2];
const outDir = path.resolve(process.argv[3] && !process.argv[3].startsWith('--') ? process.argv[3] : '.');
const shots = process.argv.includes('--shots');
const url = /^https?:/.test(target) ? target : 'file://' + path.resolve(target);
const WIDTHS = [320, 375, 414, 599, 600, 768, 1023, 1024, 1099, 1100, 1280, 1440, 1920];
fs.mkdirSync(outDir, { recursive: true });

const browser = await chromium.launch({ headless: true, channel: 'chrome',
  args: ['--disable-gpu', '--num-raster-threads=1', '--disable-partial-raster', '--disable-features=PaintHolding'] });
const ctx = await browser.newContext({ viewport: { width: 1440, height: 900 }, deviceScaleFactor: 1 });
const external = new Set();
await ctx.route('**/*', r => {
  const u = r.request().url();
  if (/^(file|data):/.test(u) || /^https?:\/\/127\.0\.0\.1[:/]/.test(u)) return r.continue();
  if (/^https:\/\/fonts\.(googleapis|gstatic)\.com\//.test(u)) { external.add(new URL(u).host); return r.continue(); }
  external.add(new URL(u).host + ' (擋掉)'); return r.abort();
});
const page = await ctx.newPage();

async function measure() {
  return page.evaluate(() => {
    const r = el => el.getBoundingClientRect();
    const vis = el => { const b = r(el); const cs = getComputedStyle(el); return b.width > 0 && b.height > 0 && cs.visibility !== 'hidden' && cs.display !== 'none'; };
    const W = window.innerWidth;
    const out = { overflow: Math.max(document.documentElement.scrollWidth, document.body.scrollWidth) - W };
    // logo
    const img = document.querySelector('.site-header .brand img, nav .nav-logo img');
    const header = img.closest('header, nav');
    const hb = r(header), ib = r(img);
    const src = img.currentSrc || img.src;
    const lockup = /淺底|深底|logo-light|logo-dark/.test(decodeURIComponent(src));
    const H = lockup ? ib.height : ib.height;            // 兩種圖的 H 都是圖高（六邊形高＝圖高）
    out.logo = { kind: lockup ? '全名' : '標記', h: +ib.height.toFixed(3), w: +ib.width.toFixed(2),
      top: +(ib.top - hb.top).toFixed(2), bottom: +(hb.bottom - ib.bottom).toFixed(2), quarterH: +(H / 4).toFixed(3), halfH: +(H / 2).toFixed(3) };
    // 到頁首裡最近的可見控制項（水平距離）
    const ctrls = [...header.querySelectorAll('a, button')].filter(el => !el.closest('.brand, .nav-logo') && vis(el) && r(el).top < hb.bottom && r(el).bottom > hb.top);
    out.logo.nearest = ctrls.length ? +Math.min(...ctrls.map(el => r(el).left - ib.right)).toFixed(2) : null;
    out.logo.leftEdge = +ib.left.toFixed(2);
    // 最小字級（有直接文字子節點、看得到的元素）
    let min = 99, minEl = '';
    for (const el of document.querySelectorAll('body *')) {
      if (!vis(el)) continue;
      const hasText = [...el.childNodes].some(n => n.nodeType === 3 && n.textContent.trim());
      if (!hasText) continue;
      const fs = parseFloat(getComputedStyle(el).fontSize);
      if (fs < min) { min = fs; minEl = el.tagName.toLowerCase() + (el.className ? '.' + String(el.className).split(' ')[0] : ''); }
    }
    out.minFont = min; out.minFontEl = minEl;
    // 點擊目標（頁首的連結與按鈕，WCAG 2.2 的 24×24）
    const tgt = [...header.querySelectorAll('a, button')].filter(vis).map(el => r(el));
    out.minTarget = tgt.length ? +Math.min(...tgt.map(b => Math.min(b.width, b.height))).toFixed(1) : null;
    out.pageH = document.documentElement.scrollHeight;
    return out;
  });
}

const rows = [];
for (const lang of ['en', 'zh']) {
  for (const w of WIDTHS) {
    await page.setViewportSize({ width: w, height: 900 });
    await page.goto(url, { waitUntil: 'networkidle' });
    await page.evaluate(l => setLang(l), lang);
    await page.evaluate(() => document.fonts.ready);
    const m = await measure();
    rows.push({ lang, w, ...m });
  }
}

// 判定
const fail = [];
for (const x of rows) {
  const L = x.logo, id = `${x.lang} ${x.w}`;
  if (x.overflow > 0) fail.push(`${id}：橫向溢出 ${x.overflow}px`);
  const wantKind = x.w >= 600 ? '全名' : '標記';
  const wantH = x.w >= 1024 ? 40 : x.w >= 600 ? 36.5 : 32;
  if (L.kind !== wantKind) fail.push(`${id}：logo 應為${wantKind}，實際${L.kind}`);
  if (Math.abs(L.h - wantH) > 0.01) fail.push(`${id}：logo 高 ${L.h}，應為 ${wantH}`);
  if (L.kind === '全名' && L.h < 36.4) fail.push(`${id}：全名 ${L.h} < 36.4`);
  if (L.top < L.quarterH || L.bottom < L.quarterH) fail.push(`${id}：logo 上下 ${L.top}/${L.bottom} < ¼H ${L.quarterH}`);
  if (L.leftEdge < L.quarterH) fail.push(`${id}：logo 到視窗左緣 ${L.leftEdge} < ¼H`);
  if (L.nearest !== null && L.nearest < L.halfH) fail.push(`${id}：logo 到最近控制項 ${L.nearest} < ½H ${L.halfH}`);
  if (x.minFont < 12) fail.push(`${id}：最小字 ${x.minFont}px（${x.minFontEl}）< 12`);
  if (x.minTarget !== null && x.minTarget < 24) fail.push(`${id}：頁首點擊目標 ${x.minTarget}px < 24`);
}
fs.writeFileSync(path.join(outDir, 'measure.json'), JSON.stringify({ url, external: [...external], rows }, null, 1));
console.log('寬度  語言 logo        高      上/下         ¼H     到控制項 ½H      溢出 最小字 頁首目標 頁高');
for (const x of rows) {
  const L = x.logo;
  console.log(`${String(x.w).padStart(5)} ${x.lang}   ${L.kind} ${String(L.h).padEnd(7)} ${String(L.top).padStart(6)}/${String(L.bottom).padEnd(6)} ${String(L.quarterH).padEnd(6)} ${String(L.nearest).padStart(7)} ${String(L.halfH).padEnd(6)} ${String(x.overflow).padStart(4)} ${String(x.minFont).padStart(5)} ${String(x.minTarget).padStart(6)} ${x.pageH}`);
}
console.log('外部請求：', [...external].join(', ') || '（無）');
console.log(fail.length ? `\n不通過 ${fail.length} 項：\n  ` + fail.join('\n  ') : '\n全部通過');

if (shots) {
  const BLOCKS = [
    ['.site-header .bar', '頁首'], ['.brand img', 'logo'], ['.nav-links', '導覽'], ['.lang', '語言'], ['.menu-btn', '選單鈕'],
    ['.hero-grid > div:first-child', '主視覺文字'], ['.sheet', '規格表'], ['.certs', '證書小圖'],
    ['#about .about > div:first-child', '關於：標題'], ['.about-body', '關於：內文'], ['.founders', '創辦人'],
    ['#products .sh', '區塊標題'], ['.prods', '產品 6 項'], ['.svc .card:first-child', '委託代工流程'], ['.svc .card:last-child', '研究與開發'],
    ['#equipment .sh', '區塊標題'], ['.eq-grid table:first-child', '分析與檢驗 10'], ['.eq-grid table:last-child', '製程與微影 13'],
    ['#contact .sh', '區塊標題'], ['.contact-list', '聯絡資料'], ['.map', '地圖'], ['.site-footer .wrap', '頁尾'],
  ];
  for (const w of [1440, 768, 375]) for (const lang of ['zh', 'en']) {
    await page.setViewportSize({ width: w, height: 900 });
    await page.goto(url, { waitUntil: 'networkidle' });
    await page.evaluate(l => setLang(l), lang);
    await page.evaluate(() => document.fonts.ready);
    const tag = `${String(w).padStart(4, '0')}-${lang}`;
    await page.screenshot({ path: path.join(outDir, `樣張-${tag}.png`), fullPage: true });
    if (lang !== 'zh') continue;
    await page.evaluate(blocks => {
      const layer = document.createElement('div');
      layer.style.cssText = `position:absolute;left:0;top:0;width:100%;height:${document.documentElement.scrollHeight}px;overflow:hidden;pointer-events:none;z-index:9999`;
      document.body.appendChild(layer);
      document.querySelector('.site-header').style.position = 'relative';
      for (const [sel, name] of blocks) {
        for (const el of document.querySelectorAll(sel)) {
          const b = el.getBoundingClientRect(); if (!b.width || !b.height) continue;
          const box = document.createElement('div');
          box.style.cssText = `position:absolute;left:${b.left + scrollX}px;top:${b.top + scrollY}px;width:${b.width}px;height:${b.height}px;outline:1.5px dashed #C2185B;outline-offset:-1px`;
          const lab = document.createElement('div');
          lab.textContent = `${name} ${Math.round(b.width)}×${Math.round(b.height)}`;
          lab.style.cssText = 'position:absolute;left:0;top:0;transform:translateY(-100%);background:#C2185B;color:#fff;font:600 11px/1.3 -apple-system,sans-serif;padding:1px 5px;white-space:nowrap';
          // 頁首裡的框：標籤放在框的下緣外側，不蓋住 logo 和按鈕；頁首本身的標籤靠右
          if (el.closest('.site-header')) { lab.style.top = '100%'; lab.style.transform = 'none'; if (el.matches('.bar')) { lab.style.left = 'auto'; lab.style.right = '0'; } }
          else if (b.top + scrollY < 16) lab.style.transform = 'none';
          box.appendChild(lab); layer.appendChild(box);
        }
      }
    }, BLOCKS);
    await page.screenshot({ path: path.join(outDir, `線框-${tag}.png`), fullPage: true });
  }
  console.log('截圖：', fs.readdirSync(outDir).filter(f => f.endsWith('.png')).join(', '));
}
await browser.close();
process.exit(fail.length ? 1 : 0);
