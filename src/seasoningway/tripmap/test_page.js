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

async function transferPlan(browser, url) {
  const source = await browser.newContext({ viewport: SIZES.desktop });
  await routeLeaflet(source);
  await source.addInitScript(() => {
    Object.defineProperty(navigator, 'clipboard', { value: { writeText: async () => { throw new Error('Clipboard unavailable'); } } });
    const days = { '2099-01-04': { ids: ['temple', 'garden'], stay: 'inn' }, '2099-01-05': { ids: [], stay: 'inn' } };
    localStorage.setItem('test-plan', JSON.stringify({ days }));
  });
  const original = await source.newPage();
  await original.goto(url, { waitUntil: 'domcontentloaded' });
  const sourceCards = await planCards(original);
  await original.getByRole('button', { name: '匯出行程', exact: true }).click();
  const fallback = original.getByRole('textbox', { name: '行程字串' });
  await fallback.waitFor();
  const code = await fallback.inputValue();
  await source.close();

  const target = await browser.newContext({ viewport: SIZES.phone });
  try {
    await routeLeaflet(target);
    const page = await target.newPage();
    await page.goto(url, { waitUntil: 'domcontentloaded' });
    const before = await planCards(page);
    const open = () => page.getByRole('button', { name: '匯入行程', exact: true }).click();
    const input = page.getByRole('textbox', { name: '貼上行程字串' });
    const submit = async value => {
      await input.fill(value);
      await page.getByRole('button', { name: '覆蓋行程' }).click();
    };
    const replaceCode = change => {
      const payload = JSON.parse(code.slice('SWP1:'.length));
      change(payload);
      return 'SWP1:' + JSON.stringify(payload);
    };
    await open();
    await submit('SWP1:{');
    const malformed = await page.getByRole('alert').innerText();
    await submit(replaceCode(payload => { payload.trip[0] = 'other-trip'; }));
    const otherTrip = await page.getByRole('alert').innerText();
    await submit(replaceCode(payload => { payload.days['2099-01-04'].ids.push('missing-spot'); }));
    const missing = await page.getByRole('alert').innerText();
    await page.getByRole('button', { name: '取消', exact: true }).click();
    const rejected = await planCards(page);
    await open();
    await submit('  ' + code + '\n');
    const imported = await planCards(page);
    await page.getByRole('button', { name: '復原' }).click();
    const undone = await planCards(page);
    await open();
    await submit(code);
    await page.reload({ waitUntil: 'domcontentloaded' });
    return { sourceCards, oneLine: code.startsWith('SWP1:') && !code.includes('\n'), before, malformed,
      otherTrip, missing, rejected, imported, undone, reloaded: await planCards(page) };
  } finally {
    await target.close();
  }
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
    page.on('console', msg => { if (/路網載入失敗|roads\.txt/.test(msg.text())) errors.push(msg.text()); });
    await page.goto(url, { waitUntil: 'domcontentloaded' });
    if (await page.evaluate(() => typeof window.L === 'undefined')) throw new Error('Leaflet 載不到');
    await page.waitForFunction(() => !DATA.roads || NET !== null);
    const text = sel => page.evaluate(s => (document.querySelector(s) || {}).textContent || '', sel);
    const r = { legend: await text('#legend'), title: await page.title(), views: {} };
    r.roadNodes = await page.evaluate(() => NET?.lat.length ?? 0);
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
  out.desktop.transfer = await transferPlan(browser, url);
  await browser.close();
  console.log(JSON.stringify(out));
})().catch(e => { console.log(JSON.stringify({ error: String(e) })); process.exit(1); });
