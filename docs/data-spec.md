# 資料規範

所有資料的存放、讀寫、引用、驗證和個資都照這套規則，套件設計見 [design.md](design.md)。

## 1. 原則

照業界在 git 裡管理結構化資料（設定、內容、目錄）的通用做法：資料是文字檔，格式用 schema 定義，寫入前、commit 前、CI 用同一份 schema 驗證，給人看的頁面由資料產生。同類的例子：GitHub Actions 的 workflow 檔（編輯器和 CI 用 JSON Schema 驗證）、Astro 的 content collections（一筆一檔、schema 驗證、用 id 互連）、Backstage 的服務目錄（YAML 實體互相連結）。

標準工具（JSON Schema 2020-12 和 Python 的 `jsonschema`）管型別、必填、列舉、格式、不准多的欄位；自己寫的只剩 schema 管不到的領域規則：跨欄位、跨檔案的一致、過期、個資。

## 2. 四類資料

| 類 | 格式 | 誰改 | 驗證 |
|---|---|---|---|
| 設定 | `seasoningway.toml` | 使用者、規劃助理 | [config.schema.json](../src/seasoningway/schemas/config.schema.json) |
| 紀錄 | JSON | 規劃助理、subagent、工具 | [schemas/](../src/seasoningway/schemas/)＋領域規則 |
| 筆記 | Markdown；旅程 README 開頭的設定也用 schema 驗（`trip.schema.json`） | 規劃助理 | 引用格式、連結；只放理由、比較、結論，事實放紀錄 |
| 視圖 | 由紀錄產生，寫到 `out/` | `seasoningway render` | 先驗證來源再產出 |

repo 以外：artifact 資料庫只存使用者在頁面上的輸入；API 金鑰存在雲端環境的網路密鑰；使用者在對話中給的個資只當下用。

## 3. 紀錄

| 種類 | 位置 | schema | 視圖 |
|---|---|---|---|
| 預算、匯率 | `trips/<旅程>/budget.json`（`items`、`rates`） | `budget` | `budget.md` |
| 預訂 | `trips/<旅程>/bookings.json` | `bookings` | `bookings.md` |
| 行程 | `trips/<旅程>/itinerary.json`，每天一項 | `itinerary` | `itinerary.md` |
| 地點 | `trips/<旅程>/places/<id>.json`，一筆一檔 | `place` | 地圖頁（[地圖引擎](tripmap.md)） |
| 地圖設定 | `trips/<旅程>/map/config.json` | `map` | 地圖頁 |
| 個人資料、想去（之後） | `profile/profile.json`、`wishlist.json` | — | — |

- 每項紀錄最上層有 `$schema`，使用引擎 repo 公開的對應 schema。第 4 版由 `migrate` 補上。
- 物件一律 `additionalProperties: false`，沒定義的欄位不能存。填法寫在欄位的 `description`，檔案裡不放註解。
- 會個別修改、數量多的一筆一檔（地點）：差異和合併以一筆為單位，不會再有超過 100 KB 的檔。數量少、整份一起看的放一個檔（預訂、行程）。
- 寫法：2 格縮排、UTF-8、中文不轉義、結尾換行（`records.dump_json`）。check 只驗內容，不驗寫法。
- schema 管不到的規則包括id、日期、幣別不重複（`RECORD_RULES`）；地點的 id 和檔名一樣、電話只放 `contact`（`place_rules`）；行程依日期排；非基準幣別要有匯率、`show_also` 的幣別要有匯率（`compute_budget`）；待訂的過了 `book_on` 會提醒（`booking_warnings`）。
- 視圖的連結由 check 查（`run_check`），錯誤訊息指向來源的 JSON。

## 4. id 和交叉引用

- 每筆紀錄有固定的 id（小寫英數字，用 `-` 連接；地點也可以用 `_`），改名不改 id；一筆一檔的檔名就是 id。
- 紀錄之間用 id 欄位互連：行程每天的 `places`、`stay_place` 和地圖設定的 `home`、`preset` 指向地點，地點的 `categories` 指向地圖設定的類別，來源的 `url` 可以是研究筆記的相對路徑。schema 用 `x-ref` 註記指向哪一種（`records.REF_KINDS`：`place`、`category`、`file`），check 確認指得到（`ref_problems`）。JSON Schema 本身不檢查資料之間的引用，同類工具都自己做這一步（Astro 的 `reference()`、Backstage 的 relations）。
- `x-` 開頭的註記由 `records.annotated` 沿著 schema 找（跟著 `$ref`、`properties`、`items`、`allOf` 等），驗證器會略過它們。
- 筆記連到紀錄用一般的相對連結，現有的連結檢查會確認它存在。

## 5. 來源和過期

- 來源是 `{url, title, via}`（`common.schema.json` 的 `source`），`via` 註明只讀到搜尋摘要或二手轉述；查詢日期是那一筆的 `as_of`。筆記照 `AGENTS.md`「研究與引用」的行內寫法，意思相同。
- 事實帶自己的來源，不是整筆共用一個日期。
- 過期：查詢日期欄位都引用 `common.schema.json` 的 `as_of`，天數只寫在它的 `x-freshness-days`（30），`records.STALE_DAYS` 讀它。預算的估價和匯率過期時提醒；行程用到的地點（`places`、`stay_place`）有過期的 `as_of` 時提醒（`stale_plan_places`），沒排進行程的不提醒，免得雜訊。入境規定出發前 14 天內重查。

## 6. 個資

- 白名單：schema 裡沒有的欄位就不能存，所以名字、證件號碼、訂位代號、付款資訊、密碼、住家地址都沒有欄位可放。
- 同行者只有角色代稱（`seasoningway.toml` 的 `people.roles`）。
- 自由文字欄位照樣掃個資格式（`pii.PII_REGIONS`）。電話、地址只能放在地點紀錄的 `contact`（公開的店家資料，出自那一筆的來源）；地點的其他欄位出現電話格式，check 會擋。

## 7. 讀寫路徑

- 讀：直接讀檔；跨多筆查用 Grep 或 `jq`；給人看的總表是視圖。
- 直接寫 JSON 檔，執行 `seasoningway check` 驗證 schema、領域規則和 `x-ref`。寫入時的回饋見 [設計的品質關卡](design.md#5-品質關卡)。
- 視圖的位置與快取規則見 [產物與快取](design.md#4-產物與快取)，地圖的來源見 [地圖引擎](tripmap.md)。
- 格式升級見 [設計的版本規則](design.md#7-版本)。

## 8. repo 以外的狀態

- artifact 資料庫是文件型資料庫（集合裡放 JSON 文件，只有讀寫權限規則，沒有型別、沒有外鍵），只能從頁面和 claude.ai 的 Claude 存取，所以只當使用者的工作副本。Claude 讀到新的行程或星號，同一回合寫進 `itinerary.json`，repo 才是事實來源。
- 頁面以 repo 的資料為準：資料庫裡指向不存在 id 的項目，頁面直接略過。所以地點的 id 不能改。

## 9. 為什麼不用資料庫

- 業界的分法：很多人同時寫的應用程式資料放資料庫（例如 Postgres 加 migration 工具）；要版本控制、審閱、由工具和 AI 維護的設定和內容放 git 裡的檔案加 schema。這個 repo 是後者。
- 雲端資料庫：每個使用者都要開帳號、存密鑰、開網路；旅程的個資放到第三方；沒有 git 的歷史和差異；artifact 頁面也連不上。
- 資料庫檔案放進 git：看不出改了什麼，兩邊都改過沒辦法合併。
- artifact 資料庫：沒有型別；git、CI、pre-commit、其他 AI 都碰不到。
- JSONL 存、每次載入記憶體裡的 SQLite 驗證：載入層要自己寫，業界少見，所以用常見的 JSON Schema。

## 10. 已知弱點

- 刪除：git 歷史永久保留，個資 commit 後刪檔也還在，要改寫歷史並 force push（要使用者同意）。所以重點在寫入前擋，不在事後刪。
- 備份：只有 GitHub 一份，雲端容器用完就收。
- 存取控制只有 repo 一層，能讀 repo 就看得到全部；對 GitHub 和讀 repo 的 AI 是明文。敏感資料的原則是不存，不是加密後存。
