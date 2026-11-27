// basemap_tiles.py 呼叫：用無頭 Chromium 打開 basemap_tiles.html，依工作清單一塊一塊截圖。
// node basemap_tiles_render.mjs <伺服器網址> <jobs.json>；jobs.json 每項 {theme, z, lon, lat, size, out}（size 是 CSS px 的正方形邊長，z 是 Leaflet 縮放）
import { createRequire } from "module";
import { readFileSync, existsSync } from "fs";
const { chromium } = createRequire(import.meta.url)("playwright");
const [url, jobsFile] = process.argv.slice(2);
const jobs = JSON.parse(readFileSync(jobsFile, "utf-8")).filter(j => !existsSync(j.out));
const browser = await chromium.launch({ args: ["--use-angle=swiftshader", "--enable-unsafe-swiftshader", "--ignore-gpu-blocklist"] });
const page = await browser.newPage({ viewport: { width: 2048, height: 2048 }, deviceScaleFactor: 2 });
page.on("pageerror", e => console.log("pageerror:", e.message));
await page.goto(url + "/basemap_tiles.html");
let i = 0;
for (const j of jobs) {
  const t0 = Date.now();
  await page.setViewportSize({ width: j.size, height: j.size });
  await page.evaluate(([t, w]) => window.setup(t, w, w), [j.theme, j.size]);
  // Leaflet 的 256px 圖磚在縮放 z 時，和 MapLibre（512px 向量圖磚）的縮放 z − 1 是同一個比例
  await page.evaluate(([a, b, c]) => window.shot(a, b, c), [j.lon, j.lat, j.z - 1]);
  await page.screenshot({ path: j.out, clip: { x: 0, y: 0, width: j.size, height: j.size } });
  console.log(`${++i}/${jobs.length} ${j.theme} z${j.z} ${Math.round((Date.now() - t0) / 1000)}s`);
}
await browser.close();
