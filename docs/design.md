# 設計

定案於 2026-11-27。改動照 [AGENTS.md](../AGENTS.md)「文件」：改原本那一條並換日期。

## 1. 目標與原則

成品是給使用者看的頁面（地圖、行程、預算）。資料要工整，是為了頁面可靠。

1. **呈現就是關口。** 產生頁面前先驗證資料，驗不過就不產出。離頁面越近的環節越嚴格，回饋也越早。錯誤最好由寫的那個 agent 當場修，因為它還記得資料的來龍去脈。
2. **跨 agent 只靠通用的基本功：檔案、JSON Schema、shell 指令、git。** 各工具專屬的功能（hook、skill、subagent）只用來加速，不能是唯一的關卡。
3. **和呈現無關的流程交給程式。** git 和保存的規則不要 agent 記。給 agent 的規則只剩：用 `render` 看結果，用 `save` 保存。

這是業界的慣例：靜態網站產生器的引擎是依賴、產物不 commit；格式工具的規則寫成設定檔，邏輯做成命令列工具，編輯器外掛只負責即時回饋，CI 才是關卡。

## 2. 兩個 repo

| | 引擎 repo（這裡） | 資料 repo |
|---|---|---|
| 公開性 | 公開 | 私人，每個使用者一個 |
| 內容 | Python 套件 `seasoningway`、資料 CI 用的 GitHub Action（`action.yml`） | 旅程資料、偏好、設定，加上由引擎產生的轉接檔 |
| 怎麼改 | 分支 + PR + CI | 只用 `save`，依 `git.mode` 保存 |
| 版本 | git tag `vX.Y.Z` | `seasoningway.toml` 的 `engine` 寫明用哪一版 |

資料 repo 的目錄：

```
seasoningway.toml      設定：engine 版本、git.mode、people.roles 等
profile/               偏好、心得
trips/<旅程代號>/      README.md、budget.json、bookings.json、itinerary.json、places/、research/、map/config.json、cache/
out/                   render 的產物（.gitignore）
AGENTS.md 等轉接檔     由 init、upgrade 產生（第 6 節）
```

舊引擎的資料都在 `data/` 底下，是因為引擎和資料放在同一個 repo；資料 repo 只有資料，所以不用這一層。

資料格式與欄位的維護方式見 [資料規範](data-spec.md)。套件從 `importlib.resources` 讀取 schema、範本和地圖資源。資料與產物一律用 UTF-8，產生檔統一使用 LF，讓 Windows 和 Linux 的結果可以直接比對。

## 3. 指令

找資料 repo：`--root`，沒給就從目前目錄往上找 `seasoningway.toml`。`init` 預設在目前目錄建立。`guide` 可在任意目錄執行。每個指令在 `cli.COMMANDS` 宣告一次：參數、處理函式和要哪種資料 repo（`Needs`），找根目錄與格式檢查由 `cli.main` 統一處理（2026-11-27）。

地圖的輸入與測試見 [tripmap.md](tripmap.md)。

| 指令 | 做什麼 | 取代舊引擎的 |
|---|---|---|
| `status` | 列出旅程、下一步、check 的問題；main、branch 模式下工作區乾淨時 `pull --ff-only`，不乾淨只提醒；有 `.githooks/` 時啟用 git hooks；2026-11-27 起依第 7 節自動升級引擎版本 | `start`、`list` |
| `render [旅程]` | 驗證（同 `check`），通過才把頁面產生到 `out/<旅程>/`；不通過就不產出，逐項列出問題 | `build`、`index` |
| `check` | 只驗證：schema、跨檔案規則、引用、過期、個資 | `check` |
| `save [-m 說明]` | 見下 | 回合結束前的手動流程、`finish` |
| `save --check` | 不改任何東西，回報沒通過的驗證、沒保存的改動、沒 push 的 commit | `finish` |
| `fetch <旅程> <種類>` | 抓要連網的資料（路線、底圖、地點 ID）寫進 `cache/` | `core/tripmap/` 的各個腳本 |
| `new`、`migrate [--from-template]` | 建立旅程、升級既有資料；2026-11-27 起可從舊模板搬到新目錄 | 同名指令 |
| `budget <旅程>` | 計算並印出預算合計，預算頁由 `render` 產生 | `budget` |
| `published <旅程> <版本>` | 發布後記錄來源和快取雜湊，第一次加 `--slug` | `published` |
| `guide <主題>` | 印出流程說明（規劃、蒐集景點、重查、檢查頁面、定案、檢討） | `.agents/skills/` |
| `init`、`upgrade` | 建立資料 repo；換版本後更新轉接檔並跑 `migrate` | 從模板建立、合併上游 |
| `hook <claude\|codex\|git> <事件>` | 給各工具的 hook 和 git hooks 呼叫 | `core/hooks.py`、`core/githooks/` |

**`save` 的行為：**

1. 驗證，不通過就停下並列出問題。
2. 加入已追蹤檔案的改動、`trips/` 和 `profile/` 底下的新檔，以及 `seasoningway.toml` 和 `wishlist.md`（資料路徑定義在 `common.DATA_DIRS`、`DATA_FILES`）。2026-11-27 起也接受 `init`、`upgrade` 新增且內容符合範本的轉接檔與初始 Git 設定檔。其他新檔會列出來並停止保存，由 agent 移走或加進 `.gitignore`。
3. commit 訊息由程式產生（改了哪趟旅程、哪幾種檔），`-m` 可以附一句說明。
4. 依 `git.mode` 同步：`main` 先 `pull --rebase` 再 push；本地有還沒 push 的 merge 時改用 `--no-rebase`。`branch` 推目前分支的 upstream。`local` 只 commit。
5. 有衝突就讓 rebase 停在衝突處，列出衝突的檔案，結束碼不是 0。agent 整合完再執行一次 `save`，`save` 會接著完成。絕不自動選一邊。
6. 網路錯誤退避重試 4 次。從不 force push。

## 4. 產物與快取

| 種類 | 例子 | 放哪 | commit |
|---|---|---|---|
| 來源 | 紀錄、筆記、設定 | `trips/`、`profile/` | 是 |
| 快取（要連網才拿得到、重抓不一定一樣） | 路線、底圖、地點 ID | `trips/<旅程>/cache/`，由 `fetch` 寫入 | 是，當成資料 |
| 產物（可以從上面兩種重算） | 預算表、行程表、預訂表、總覽、地圖頁 | `out/` | 否 |

產物不 commit。全量 `render` 會再產生 `out/index.md`，單趟產物放 `out/<旅程>/`，地圖與相鄰資源放其中的 `map/`。發布紀錄（`published.json`）記的是該旅程來源和快取的 SHA-256；從第 3 版遷移的紀錄保留 slug、版本，等下次實際發布再建立來源基準。

## 5. 品質關卡

由前到後：

1. **schema**：盡量嚴格（列舉值、格式、必填、不准多的欄位）。每個 JSON 用 `$schema` 指向引擎 repo 公開的對應 schema，任何 agent 都能自己驗證。本機使用當前安裝版本隨附的 schema。
2. **寫入時**（有 hook 的工具）：`hook <工具> pre-edit` 擋下改產物、新出現的個資、不符合 schema 的紀錄；`post-edit` 把問題回給剛寫的 agent。沒有 shell 的 subagent 只能靠這一層得到回饋。
3. **`render`、`save`**：驗不過就不產出、不保存。這一層對任何 agent 都成立。
4. **截圖檢查**：用無頭瀏覽器在手機、平板、桌面尺寸看頁面。
5. **CI**：資料 repo 用 `action.yml` 跑 `check`。這是最後一道防線，不是主要關卡。

`render` 遇到缺值或怪值要明確報錯，不要默默產出一個看起來還可以的頁面。

## 6. 各 agent 的轉接檔

由 `init` 寫入資料 repo、`upgrade` 更新。檔頭標明「由 seasoningway 產生，不要手改」，`hook pre-edit` 擋直接修改。2026-11-27 起，`upgrade` 移除轉接檔路徑內有產生標記且新版不再產生的檔案和留下的空目錄，列出刪除的檔案與保留的自訂檔案。

| 檔案 | 內容 |
|---|---|
| `AGENTS.md`、`CLAUDE.md` | 硬性規則（個資、只做規劃）、`render` 和 `save` 什麼時候用、`guide` 有哪些主題 |
| `.claude/settings.json`、`.codex/hooks.json` | hook 都只呼叫 `seasoningway hook …`。2026-11-27 起，Claude 預先允許執行 `seasoningway` 指令。 |
| `.claude/skills/`、`.agents/skills/` | 薄入口：什麼時候用，以及執行 `seasoningway guide <主題>` |
| `.claude/agents/`、`.codex/agents/` | subagent 定義。讀網頁的 subagent 沒有 shell，執行不了 `guide`，所以完整的做法要寫進定義檔 |
| `.githooks/` | 呼叫 `seasoningway hook git <事件>`；`init`、`upgrade`、`status`、`save` 會設定 `core.hooksPath` |
| `.github/workflows/check.yml` | `uses: tsunderink/seasoningway@<版本>` |

使用者自己的規則寫在 `profile/`。若同名轉接檔已有自訂內容，先備份並移出產生路徑，再執行 `init` 或 `upgrade`。工具設定和 hooks 的啟用方式見 [建立資料 repo](../README.md#建立資料-repo)。

## 7. 版本

- 引擎用 semver。`0.x` 期間資料格式可以變，但每次變都要有 `migrate`。2026-11-28 起，發版流程從通過 main CI 的 commit 建立安裝包，隨 GitHub Release 發布，供[安裝指令](../README.md#安裝)取得最新正式版。
- 資料格式另用 `format` 記版本。改紀錄 schema 或旅程 README 的欄位時，更新 `config.FORMAT`、在 `project.MIGRATIONS` 新增從前一版升級的步驟，並用虛構資料測試。`migrate` 支援從第 3 版起升級。
- 資料 repo 的 `seasoningway.toml` 寫 `engine = "X.Y.Z"`。`upgrade` 一起改這個版本、CI 的 `uses:` 版本，並重寫轉接檔。
- 安裝的引擎和 `engine` 的 major.minor 不同時：`status` 提醒，`save` 拒絕保存，避免用錯的格式寫資料。
- 自動升級（2026-11-27）：安裝的引擎是已發布的版本（不是可編輯安裝）且比 `engine` 新時，`status`（也就是 SessionStart hook）在快轉同步後執行 `upgrade` 和 `save`。工作區不乾淨或設定有格式以外的問題就不升級，只提醒。安裝的引擎比較舊時照上一條處理。要跟上新版，只要讓環境安裝最新發布版，例如雲端環境的設定腳本。

## 8. 從舊引擎拿掉的

- 引擎和資料分開 commit、引擎 commit 訊息不能寫旅程細節的檢查（`commit-msg`、`commits`）。資料 repo 裡沒有引擎。
- 從模板建立、合併上游（包括 graft 歷史）的流程。改用[套件安裝流程](../README.md#安裝)和第 7 節的升級流程。
- Codex 專用的引擎分支流程。引擎 repo 改用一般的 PR 流程。
- 產物 commit 進 repo，以及 `.gitattributes` 的產物清單和「產物是不是最新的」檢查。
- 規則檔裡「改了檔案的回合結束前」的手動步驟。改用 `save`。

保留：個資掃描（寫入時、`check`、git pre-push 掃新增的行和 commit 訊息）。

## 9. 不採用的

| 方案 | 為什麼不用 |
|---|---|
| MCP 伺服器 | 每家工具的設定格式不同，又要多跑一個伺服器；agent 還是能繞過它直接寫檔。等真的需要支援沒有 shell 的 agent 再評估，而且只包 `check` 和 `save` |
| 把整個引擎做成 skill | 不保證會載入，版本也難管。skill 只當入口 |
| 各工具的 plugin | 只限單一工具 |
| 模板 + 合併上游、submodule | 前者就是第 8 節拿掉的流程；後者 `.claude/` 必須在根目錄，放不進 submodule |
| 把資料放在服務後面 | 太重，也失去「檔案就是事實來源」的好處 |
