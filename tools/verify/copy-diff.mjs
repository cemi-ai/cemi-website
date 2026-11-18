// 文案比對：新頁面的每一行字都要在舊頁面找得到，舊頁面的每一行字也都要在新頁面找得到。
// 兩個方向都比，英文、中文各比一次。比的是瀏覽器實際算出來的 innerText，不是原始碼。
//
// 用法：
//   git show 61bef92:index.html > /tmp/old-61bef92.html          # 在這個 repo 裡跑
//   node tools/verify/copy-diff.mjs /tmp/old-61bef92.html index.html --head \
//        --expect tools/verify/expect.json
//
// · 比之前會把兩邊的字「正規化」：NFKC、轉小寫、只留字母和數字。所以標點、空白、
//   大小寫（text-transform）、emoji 都不算差異；斷句處的標點被箭頭取代也不算。
// · CSS 產生的內容（::before 的「01」、箭頭）不在 innerText 裡，不會被當成新增的字。
// · 正規化後 ≤ 2 個字元的行（例如 1、EN、70）**另外用集合比兩個方向**，不走子字串比對
//   （子字串比對對這種長度等於沒有比）。純標點的短行才略過。詳見 §短行 那一段的註解。
// · expect.json 列「預期會消失的行」和「允許新增的行」。清單裡的條目如果實際上沒有用到，
//   也算失敗：死條目會變成洞（memory: guard-tests-must-not-need-updating）。
//   以 re: 開頭的條目是正規式，比對的是「正規化之後」的那一行（例如頁尾年份：
//   re:^\d{4}cemiallrightsreserved$），這樣每年換年份時不用改這份清單。
// · --head：另外比 <head> 的 title、meta、canonical、icon 和 JSON-LD，要完全相同。
// · 全程擋掉 file: 和 data: 以外的請求（地圖、字型都不會載入），只看 DOM 文字。
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

const args = process.argv.slice(2);
const files = args.filter(a => !a.startsWith('--') && !a.endsWith('.json'));
const checkHead = args.includes('--head');
const ei = args.indexOf('--expect');
const expect = ei >= 0 ? JSON.parse(fs.readFileSync(args[ei + 1], 'utf8')) : { removed: {}, added: {} };
if (files.length !== 2) { console.error('用法：node copy-diff.mjs <舊.html> <新.html> [--head] [--expect expect.json]'); process.exit(2); }
const [oldFile, newFile] = files.map(f => path.resolve(f));

const norm = s => s.normalize('NFKC').toLowerCase().replace(/[^\p{L}\p{N}]/gu, '');
const lines = t => [...new Set(t.split(/[\n\t]+/).map(s => s.trim()).filter(Boolean))];

const browser = await chromium.launch({ headless: true, channel: 'chrome' });
const ctx = await browser.newContext({ viewport: { width: 1440, height: 900 } });
await ctx.route('**/*', r => /^(file|data):/.test(r.request().url()) ? r.continue() : r.abort());

async function grab(file, lang) {
  const page = await ctx.newPage();
  await page.goto('file://' + file);
  const ok = await page.evaluate(l => { if (typeof setLang !== 'function') return false; setLang(l); return true; }, lang);
  if (!ok) throw new Error(`${file} 沒有 setLang()，沒辦法切語言`);
  // 分頁（role=tab）藏起來的面板不在 innerText 裡：61bef92 的「製程與微影」13 台設備就是這樣。
  // 所以每個分頁都點一次，把每個狀態的字都收進來（第一次跑時漏了這一步，13 台被當成「新增」）。
  const texts = [await page.evaluate(() => document.body.innerText)];
  const tabs = await page.$$('[role="tab"]');
  for (const t of tabs) { await t.click(); texts.push(await page.evaluate(() => document.body.innerText)); }
  const text = texts.join('\n');
  const head = await page.evaluate(() => ({
    title: document.title,
    meta: [...document.head.querySelectorAll('meta[name], meta[property]')].map(m => `${m.getAttribute('name') || m.getAttribute('property')}=${m.content}`).sort(),
    links: [...document.head.querySelectorAll('link[rel="canonical"], link[rel="icon"]')].map(l => `${l.rel}=${l.getAttribute('href')}`).sort(),
    jsonld: [...document.querySelectorAll('script[type="application/ld+json"]')].map(s => JSON.stringify(JSON.parse(s.textContent))),
  }));
  await page.close();
  return { text, head };
}

let failed = false;
for (const lang of ['en', 'zh']) {
  const A = await grab(oldFile, lang), B = await grab(newFile, lang);
  const nA = norm(A.text), nB = norm(B.text);
  const mk = list => list.map(e => e.startsWith('re:') ? { key: e, re: new RegExp(e.slice(3)) } : { key: e, n: norm(e) });
  const expRemoved = mk([...(expect.removed?.[lang] || []), ...(expect.removed?.both || [])]);
  const expAdded = mk([...(expect.added?.[lang] || []), ...(expect.added?.both || [])]);
  const hit = (list, n) => list.find(e => e.re ? e.re.test(n) : e.n === n);
  const usedR = new Set(), usedA = new Set();
  const missing = [], extra = [];
  // 🔴🔴 **短行不可以整個略過**（auditor 2026-11-18 的 🟡1）。
  //   正規化之後 ≤2 字元的行，舊版是數一數就跳過 —— 因為長行用的是
  //   「子字串比對」，而 1–2 個字的字串幾乎一定找得到，會一直假綠。
  //   但那等於**把這一頁最該守住的事實全部排除在外**：
  //     `70+` → `70`、`×4` → `4`、`×3` → `3`。
  //   實測：把 70+ 改成 80+、×4 改成 ×5，舊版**照樣 rc 0 通過**。
  //   而這一版的改版正好把那些數字搬進了表格。
  //
  //   改法：短行另外收起來，用**集合**各比一次方向（仍然走 expect）。
  //   ⚠ 為什麼是集合不是多重集合（auditor 原話是多重集合）：
  //     `lines()` 上游就 `new Set(...)` 去重過了 —— 那是為了把舊頁
  //     「每個分頁點一次」收集到的重複內容收斂掉。所以在這裡多重集合
  //     等於集合。要真的數次數就得拿掉上游的去重，而那會讓舊頁的
  //     分頁狀態產生大量假差異。**次數的變化這一支量不到**，寫在這裡。
  const shortsA = new Set(), shortsB = new Set();
  for (const l of lines(A.text)) {
    const n = norm(l); if (!n.length) continue;            // 純標點，沒有文字
    if (n.length <= 2) { shortsA.add(n); continue; }
    if (!nB.includes(n)) { const e = hit(expRemoved, n); if (e) usedR.add(e.key); else missing.push(l); }
  }
  for (const l of lines(B.text)) {
    const n = norm(l); if (!n.length) continue;            // 同上
    if (n.length <= 2) { shortsB.add(n); continue; }
    if (!nA.includes(n)) { const e = hit(expAdded, n); if (e) usedA.add(e.key); else extra.push(l); }
  }
  for (const n of shortsA) {
    if (shortsB.has(n)) continue;
    const e = hit(expRemoved, n); if (e) usedR.add(e.key); else missing.push('（短）' + n);
  }
  for (const n of shortsB) {
    if (shortsA.has(n)) continue;
    const e = hit(expAdded, n); if (e) usedA.add(e.key); else extra.push('（短）' + n);
  }
  const shortA = shortsA.size, shortB = shortsB.size;
  const deadR = expRemoved.filter(e => !usedR.has(e.key)).map(e => e.key);
  const deadA = expAdded.filter(e => !usedA.has(e.key)).map(e => e.key);
  console.log(`\n=== ${lang.toUpperCase()}：舊 ${lines(A.text).length} 行（短行 ${shortA} 種）／新 ${lines(B.text).length} 行（短行 ${shortB} 種）`);
  console.log(`  舊的不見了（未列在 expect）：${missing.length}`); missing.forEach(l => console.log('    − ' + l));
  console.log(`  新多出來的（未列在 expect）：${extra.length}`); extra.forEach(l => console.log('    + ' + l));
  console.log(`  預期消失、實際消失：${usedR.size}；預期新增、實際新增：${usedA.size}`);
  if (deadR.length || deadA.length) console.log(`  ✗ expect 裡沒用到的條目：${[...deadR, ...deadA].join('、')}`);
  if (missing.length || extra.length || deadR.length || deadA.length) failed = true;
  if (checkHead && lang === 'en') {
    const h = JSON.stringify(A.head) === JSON.stringify(B.head);
    console.log(`  <head>（title、meta、canonical、icon、JSON-LD）：${h ? '相同' : '不同'}`);
    if (!h) { failed = true; console.log('    舊：' + JSON.stringify(A.head, null, 1)); console.log('    新：' + JSON.stringify(B.head, null, 1)); }
  }
}
await browser.close();
console.log(failed ? '\n結果：不通過' : '\n結果：通過');
process.exit(failed ? 1 : 0);
