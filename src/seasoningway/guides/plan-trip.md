# 規劃旅程

## 初始化

1. 在資料 repo 根目錄執行 `seasoningway init`。
2. 問使用者要用哪種 Git 保存方式和預算基準幣別，依 `seasoningway.toml` 的註解設定。同行者用角色代稱。
3. 問查簽證所需的國籍與護照種類、出發城市、常用機場、看得懂的語言，記進 `profile/preferences.md` 並附日期。
4. 執行 `seasoningway save`。

## 開新旅程

- 沒有大概月份的點子記在 `wishlist.md`。有了月份就執行 `seasoningway new YYYY-MM-英文地名 --title 中文標題 --travelers 人數`，並從 wishlist 移除。
- 日期變動：用 `git mv` 改資料夾名稱，再 `seasoningway render`。
- 進度寫在旅程 README 開頭的 `status`：構想 → 規劃中 → 已訂妥 → 旅行中 → 已結束。
- 要做地圖頁時照 [地圖規格](https://github.com/tsunderink/seasoningway/blob/main/docs/tripmap.md)：先寫 `map/config.json`、`map/regions.json`，地點用 `scout` 蒐集；還綁著日本或自駕的地方見 [地圖待辦](https://github.com/tsunderink/seasoningway/blob/main/docs/roadmap.md)。

## 規劃步驟

每一步的結果寫進旅程 README 的「已決定」「待決定」「下一步」或 `research/`。

1. 讀 `profile/`，先問「還沒問的偏好」裡和這趟有關的（同行者、節奏、住宿、預算風格、每天交通上限），答案記進 `profile/preferences.md`。
2. 定硬條件：日期、航班、人數、預算（README 開頭的 `budget_target`）、必去項目、每天移動上限、住宿條件。另外查：
   - 入境規定（簽證、停留天數、入境手續）：寫進 `research/`。`check` 在出發前 60 天還沒有這份筆記、出發前 14 天內還沒重查時會提醒。
   - 當地行事曆：假日、大型考試等會訂滿旅館的日子。
3. 找候選景點，附來源和查詢日期。來源要涵蓋三種觀點：外國旅客、當地口碑、美食評分（日本用 japan-guide、じゃらん、食べログ）。季節營業和路況另外查。候選多的時候執行 `seasoningway guide scout` 並分區蒐集。
4. 依旅行月份給每個點 1–5 級推薦度，每一級的意思固定，並寫理由。推薦度和整年口碑分開寫清楚。
5. 先問使用者要自己排行程（有地圖頁的話可以在地圖頁排），還是助理先排草案。
6. 排好後助理檢查：
   - 每天的移動時數、入住時間、公休、排隊名店的開門時間。
   - 住宿條件，例如溫泉旅館排平日，避開週五、週六晚。
   - 自駕日的喝酒安排。
   - 要調整的天先試算替代方案，再寫進 `itinerary.json`（一天一項）。
7. `seasoningway budget <旅程代號>` 算預算；要預訂的寫進 `bookings.json`，附建議預訂時間和連結。
8. 用 `seasoningway render` 產生預覽，改完執行 `seasoningway save`。若發布私人 artifact，完成後執行 `seasoningway published` 記錄版本。

完成條件：硬條件都在「已決定」，沒定的都在「待決定」，每個要訂的項目都在 `bookings.json`。
