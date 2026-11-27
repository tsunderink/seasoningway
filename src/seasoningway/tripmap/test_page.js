// 地圖頁的瀏覽器煙霧測試，由 test_tripmap.py 呼叫：node test_page.js <index.html>
// 在桌面、手機兩種尺寸打開頁面，切換三個分頁、收合地圖（只有手機有），把看到的文字和 JS 錯誤印成一行 JSON；
// 判斷對不對由 test_tripmap.py 做。Leaflet 用和頁面 CDN 相同的版本，由本機套件提供。
const { pathToFileURL } = require('node:url');
let chromium;
try { ({ chromium } = require('playwright')); } catch (e) { skip('沒有 Node 的 playwright 套件'); }
let leaflet;
try { leaflet = require.resolve('leaflet/dist/leaflet.js'); } catch (e) { skip('沒有 Node 的 Leaflet 套件'); }

function skip(why) { console.log(JSON.stringify({ skip: why })); process.exit(0); }

async function launch() {
  try { return await chromium.launch(); } catch (e) {}
  try { return await chromium.launch({ channel: 'chrome' }); } catch (e) {}  // 沒下載 Playwright 的瀏覽器時用系統的 Chrome
  skip('playwright 找不到可以用的 Chromium 或 Chrome');
}

const SIZES = { desktop: { width: 1280, height: 900 }, phone: { width: 390, height: 844 } };

(async () => {
  const browser = await launch();
  const out = {};
  for (const [name, viewport] of Object.entries(SIZES)) {
    const ctx = await browser.newContext({ viewport });  // 每種尺寸一個新的 localStorage
    await ctx.route('https://cdnjs.cloudflare.com/ajax/libs/leaflet/1.9.4/leaflet.min.js', route =>
      route.fulfill({ path: leaflet, contentType: 'application/javascript' }));
    const page = await ctx.newPage();
    const errors = [];
    page.on('pageerror', e => errors.push(String(e)));
    await page.goto(pathToFileURL(process.argv[2]).href, { waitUntil: 'domcontentloaded' });
    if (await page.evaluate(() => typeof window.L === 'undefined')) throw new Error('Leaflet 載不到');
    const text = sel => page.evaluate(s => (document.querySelector(s) || {}).textContent || '', sel);
    const r = { legend: await text('#legend'), title: await page.title(), views: {} };
    for (const id of ['view-list', 'view-hotels', 'view-days']) {
      await page.click('#' + id);
      r.views[id] = await text('#list');
    }
    r.maptoggle = await page.isVisible('#maptoggle');  // 只有窄螢幕有「收起地圖」
    if (r.maptoggle) { await page.click('#maptoggle'); await page.click('#maptoggle'); }
    r.errors = errors;
    out[name] = r;
    await ctx.close();
  }
  await browser.close();
  console.log(JSON.stringify(out));
})().catch(e => { console.log(JSON.stringify({ error: String(e) })); process.exit(1); });
