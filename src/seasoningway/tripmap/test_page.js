// 地圖頁的瀏覽器煙霧測試，由 test_tripmap.py 呼叫：node test_page.js <index.html>
// 桌面與手機檢查初始行程、三個分頁和地圖收合，桌面再檢查已存行程與資料庫同步的優先順序。
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
const routeLeaflet = ctx => ctx.route('https://cdnjs.cloudflare.com/ajax/libs/leaflet/1.9.4/leaflet.min.js', route =>
  route.fulfill({ path: leaflet, contentType: 'application/javascript' }));
const planCards = page => page.evaluate(() => {
  const cards = kind => Array.from(document.querySelectorAll(`#list .mini[data-kind="${kind}"]`),
    ({ dataset }) => [dataset.day, dataset.id]);
  return { stops: cards('plan'), stays: cards('stay') };
});

async function savedPlans(browser, url) {
  const runScenario = async (local, saved) => {
    const ctx = await browser.newContext({ viewport: SIZES.desktop });
    try {
      await routeLeaflet(ctx);
      await ctx.addInitScript(({ local, saved }) => {
        if (local !== null) localStorage.setItem('test-plan', JSON.stringify(local));
        window.testPlanWrites = 0;
        window.claude = { use: async () => ({ doc: path => {
          const value = path === 'plan/itinerary' ? saved : null;
          return {
            get: async () => ({ exists: value !== null, data: () => value }),
            onSnapshot: () => {},
            set: async () => { if (path === 'plan/itinerary') window.testPlanWrites++; },
          };
        } }) };
      }, { local, saved });
      const page = await ctx.newPage();
      const errors = [];
      page.on('pageerror', e => errors.push(String(e)));
      await page.goto(url, { waitUntil: 'domcontentloaded' });
      await page.waitForFunction(() => document.querySelector('#plansync')?.textContent.startsWith('已'));
      const cards = await planCards(page);
      const writes = await page.evaluate(() => window.testPlanWrites);
      if (errors.length) throw new Error(errors.join('\n'));
      return { cards, writes };
    } finally {
      await ctx.close();
    }
  };
  const empty = { days: {}, updated: '2099-01-02T00:00:00Z' };
  const remote = { days: { '2099-01-01': { stay: 'inn' } } };
  const { cards: local } = await runScenario(empty, null);
  const { cards: fromDb } = await runScenario(null, remote);
  const { cards: newerLocal } = await runScenario(empty, { ...remote, updated: '2099-01-01T00:00:00Z' });
  const { cards: fallback, writes: fallbackWrites } = await runScenario(null, null);
  return { local, remote: fromDb, newerLocal, fallback, fallbackWrites };
}

(async () => {
  const browser = await launch();
  const url = pathToFileURL(process.argv[2]).href;
  const out = {};
  for (const [name, viewport] of Object.entries(SIZES)) {
    const ctx = await browser.newContext({ viewport });  // 每種尺寸一個新的 localStorage
    await routeLeaflet(ctx);
    const page = await ctx.newPage();
    const errors = [];
    page.on('pageerror', e => errors.push(String(e)));
    await page.goto(url, { waitUntil: 'domcontentloaded' });
    if (await page.evaluate(() => typeof window.L === 'undefined')) throw new Error('Leaflet 載不到');
    const text = sel => page.evaluate(s => (document.querySelector(s) || {}).textContent || '', sel);
    const r = { legend: await text('#legend'), title: await page.title(), views: {} };
    for (const id of ['view-list', 'view-hotels', 'view-days']) {
      await page.click('#' + id);
      r.views[id] = await text('#list');
    }
    r.plan = await planCards(page);
    r.maptoggle = await page.isVisible('#maptoggle');  // 只有窄螢幕有「收起地圖」
    if (r.maptoggle) { await page.click('#maptoggle'); await page.click('#maptoggle'); }
    r.errors = errors;
    out[name] = r;
    await ctx.close();
  }
  out.desktop.saved = await savedPlans(browser, url);
  await browser.close();
  console.log(JSON.stringify(out));
})().catch(e => { console.log(JSON.stringify({ error: String(e) })); process.exit(1); });
