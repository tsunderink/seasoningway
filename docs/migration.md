# 從舊引擎搬過來

舊引擎在使用者資料 repo 的引擎檔案裡：`core/`、`.agents/`、`.claude/`、`.codex/`、`.github/`、`AGENTS.md`。目標設計見 [design.md](design.md)。

照順序做，一個階段一個或多個 PR。每完成一項就在同一個 PR 打勾。

**只搬引擎檔案。** 資料 repo 的 `data/` 和其他任何旅程內容都不能進這個 repo（[AGENTS.md](../AGENTS.md)「硬性限制」）。舊的 `core/examples/demo` 是虛構的範例，可以搬，但搬之前要逐檔確認裡面沒有真實的旅程內容。

## 第 0 階段：骨架（2026-11-27 完成）

- [x] 套件設定、命令列入口、各指令的空殼、測試、CI、`action.yml`
- [x] 設計文件和這份清單

## 第 1 階段：搬程式，改用新的目錄

完成時：`check`、`new`、`budget`、`published`、`migrate` 可以在新目錄的資料 repo 上跑，舊的測試全部搬過來並通過。

- [x] `core/trips.py` 搬進 `src/seasoningway/`，依設定、紀錄、驗證、預算、視圖、個資與 Git 等主題拆開。
- [x] 引擎的檔案用 `importlib.resources` 讀，並寫進 `pyproject.toml` 的 package data。
- [x] 改用新目錄（design.md 第 2 節）：往上找 `seasoningway.toml`，路徑從 `data/trips/` 改成 `trips/`，`data/profile/` 改成 `profile/`，`data/trips.toml` 改成 `seasoningway.toml`。設定 schema 加上 `engine` 欄位。
- [x] `core/schemas/` 搬到 `src/seasoningway/schemas/`。
- [x] `core/templates/` 搬到 `src/seasoningway/templates/`，改用新目錄。
- [x] `core/tripmap/` 搬到 `src/seasoningway/tripmap/`，包括 `template.html`、Leaflet 的 CSS、`test_page.js`。
- [x] `core/examples/demo` 改成新目錄後放到 `tests/fixtures/demo/`。
- [x] `core/test_trips.py`、`core/tripmap/test_tripmap.py` 搬到 `tests/`，移除舊模板的引擎與資料分界、commit 訊息和轉接檔測試。
- [x] CI 加上地圖頁的瀏覽器測試，沿用桌面與手機煙霧測試，測試使用本機的 Leaflet 1.9.4。
- [x] 不搬：`is_engine`、`COMMIT_SCOPES`、`commit-msg`、`commits`（design.md 第 8 節）。
- [x] 資料規範、地圖通用化待辦與地圖介面規格分別移入 `docs/data-spec.md`、`docs/roadmap.md` 和 `docs/tripmap.md`，仍適用的架構規則併入 `design.md`。

## 第 2 階段：新的指令

完成時：design.md 第 3、4 節的 `status`、`render`、`save`、`fetch` 照規格運作，每種失敗情況都有測試。

- [x] `render`：先驗證，通過才產生到 `out/<旅程>/`，不通過就不產出。拿掉第 1 階段的過渡安排，包括 `migrate` 初始化空目錄、`build` 指令、產物比對、`.gitattributes` 的產物標記，以及文件裡標「第 1 階段」的段落。
- [x] `fetch`：把 `plan_drive`、`roads`、`basemap_tiles`、`place_ids` 包成 `fetch <旅程> <種類>`，寫進 `trips/<旅程>/cache/`。
- [x] 舊的 `map/roads.txt`、`map/base/places.json`、`map/base/tiles.json` 改成 `cache/` 裡的資料，`render` 從那裡讀。
- [x] `published.json` 改記來源和快取的雜湊。
- [x] `status`：取代 `start` 和 `list`。
- [x] `save`：照 design.md 第 3 節。測試至少要涵蓋：驗不過、資料路徑外的新檔、三種 `git.mode`、有還沒 push 的 merge、rebase 衝突後再執行一次、push 時網路錯誤、版本不符。
- [x] `save --check`：取代 `finish`。
- [x] 每個 JSON 紀錄加上 `$schema`，指向引擎發布的 schema。`migrate` 負責補上。

## 第 3 階段：各 agent 的轉接

完成時：在空目錄執行 `init` 會產生一個可以用的資料 repo，Claude Code 和 Codex 開在裡面時 hook 都會跑。

- [x] `guide <主題>`：舊的 `.agents/skills/*/` 改寫成 `src/seasoningway/guides/`，主題名稱沿用 skill 的名稱。
- [x] `hook claude|codex <事件>`：搬 `core/hooks.py`，事件有 `session-start`（呼叫 `status`）、`pre-edit`、`post-edit`、`stop`（呼叫 `save --check`）。`AGENT_WRITES` 改用新路徑，並擋直接修改轉接檔。
- [x] `hook git pre-commit|pre-push`：pre-commit 跑 `check`；pre-push 掃新增的行和 commit 訊息裡的個資。
- [x] 轉接檔的範本（design.md 第 6 節）。資料 repo 的 `AGENTS.md` 從舊的 `AGENTS.md` 取資料 repo 也適用的部分：個資、只做規劃、事實來源、研究與引用、金額與預算、偏好與心得、語言與命名。git 的手動步驟改成「改完執行 `seasoningway save`」。
- [x] subagent 定義：搬 `.claude/agents/`、`.codex/agents/`，沒有 shell 的 subagent 要把 guide 的完整內容寫進定義檔。
- [x] `init`、`upgrade`：寫入或更新轉接檔，`upgrade` 另外改 `engine` 版本和 CI 的 `uses:` 版本，再跑 `migrate`。
- [x] `action.yml` 能用：在 `tests/fixtures/demo` 上跑通。

## 第 4 階段：從舊的模板 repo 搬過來

完成時：舊的模板 repo 可以一個指令變成新的資料 repo，而且頁面和舊引擎產生的一樣。

- [x] `migrate --from-template`：`data/*` 搬到根目錄、`data/trips.toml` 改成 `seasoningway.toml` 並加上 `engine`、地圖快取搬進 `cache/`、刪掉舊的引擎檔案、執行 `init`。用虛構的舊目錄 fixture 驗證。
- [x] 對照測試：同一份 demo 以舊引擎 `build` 和新版 `render` 產生頁面，差異見下表。測試使用舊引擎的 Markdown 產物和地圖 SHA-256；有舊引擎時可設定 `SEASONINGWAY_LEGACY_CORE` 指向舊 `core/`，跑 `python3 -m unittest discover -s tests -p test_legacy.py` 當場重建舊頁面。
- [x] 發布 [`v0.1.0`](https://github.com/tsunderink/seasoningway/releases/tag/v0.1.0)。

| 產物 | 差異 |
|---|---|
| 旅程總覽 | 檔頭改用 `seasoningway render`；旅程、偏好、心得及清單的連結改從 `out/` 指回來源。 |
| 預算、預訂 | 檔頭改用 `seasoningway render`。表格、金額與預訂資訊逐行一致。 |
| 逐日行程 | 檔頭改用 `seasoningway render`；住宿研究連結改從 `out/<旅程>/` 指回來源。 |
| 地圖 HTML、道路快取 | 逐位元組一致。 |

## 第 5 階段：切換使用者的資料 repo（不由 Codex 做，2026-11-27 完成）

資料 repo 的改動由使用者授權的 Claude session 在資料 repo 裡做：安裝 `v0.1.0`、執行 `migrate --from-template`、確認 `render` 的頁面和切換前相同、`save`。Codex 只負責引擎 repo。

切換時在資料 repo 確認：地圖頁、道路快取、底圖圖磚和照片逐位元組相同，Markdown 頁面只差連結目標。之後的改動照一般流程開 PR，發版照 [AGENTS.md](../AGENTS.md)「改動流程」。
