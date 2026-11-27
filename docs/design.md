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

找資料 repo：`--root`，沒給就從目前目錄往上找 `seasoningway.toml`。

第 1 階段用 `migrate` 初始化空目錄。這個階段沿用 `build` 和 `check` 的產物比對，總覽寫到 `trip-index.md`，各旅程的視圖位置見 [資料規範](data-spec.md)。第 2 階段換成 `render`。地圖輸入與測試見 [tripmap.md](tripmap.md)。

| 指令 | 做什麼 | 取代舊引擎的 |
|---|---|---|
| `status` | 列出旅程、下一步、check 的問題；工作區乾淨時 `pull --ff-only`，不乾淨只提醒；啟用 git hooks | `start`、`list` |
| `render [旅程]` | 驗證（同 `check`），通過才把頁面產生到 `out/<旅程>/`；不通過就不產出，逐項列出問題 | `build`、`index` |
| `check` | 只驗證：schema、跨檔案規則、引用、過期、個資 | `check` |
| `save [-m 說明]` | 見下 | 回合結束前的手動流程、`finish` |
| `save --check` | 不改任何東西，回報沒通過的驗證、沒保存的改動、沒 push 的 commit | `finish` |
| `fetch <旅程> <種類>` | 抓要連網的資料（路線、底圖、地點 ID）寫進 `cache/` | `core/tripmap/` 的各個腳本 |
| `new`、`budget`、`published`、`migrate` | 同舊引擎 | 同名指令 |
| `guide <主題>` | 印出流程說明（規劃、蒐集景點、重查、檢查頁面、定案、檢討） | `.agents/skills/` |
| `init`、`upgrade` | 建立資料 repo；換版本後更新轉接檔並跑 `migrate` | 從模板建立、合併上游 |
| `hook <claude\|codex\|git> <事件>` | 給各工具的 hook 和 git hooks 呼叫 | `core/hooks.py`、`core/githooks/` |

**`save` 的行為：**

1. 驗證，不通過就停下並列出問題。
2. 加入已追蹤檔案的改動，以及 `trips/`、`profile/`、`seasoningway.toml` 底下的新檔。其他地方的新檔列出來並停下，由 agent 移走或加進 `.gitignore`。
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

產物不 commit，所以不再需要「產生檔是不是最新的」這項檢查，產物也不會在 rebase 時衝突。發布紀錄（`published.json`）記的是來源和快取的雜湊。

## 5. 品質關卡

由前到後：

1. **schema**：盡量嚴格（列舉值、格式、必填、不准多的欄位）。每個 JSON 用 `$schema` 指向引擎發布的 schema，任何 agent 都能自己驗證。
2. **寫入時**（有 hook 的工具）：`hook <工具> pre-edit` 擋下改產物、新出現的個資、不符合 schema 的紀錄；`post-edit` 把問題回給剛寫的 agent。沒有 shell 的 subagent 只能靠這一層得到回饋。
3. **`render`、`save`**：驗不過就不產出、不保存。這一層對任何 agent 都成立。
4. **截圖檢查**：用無頭瀏覽器在手機、平板、桌面尺寸看頁面。
5. **CI**：資料 repo 用 `action.yml` 跑 `check`。這是最後一道防線，不是主要關卡。

`render` 遇到缺值或怪值要明確報錯，不要默默產出一個看起來還可以的頁面。

## 6. 各 agent 的轉接檔

由 `init` 寫入資料 repo、`upgrade` 更新。檔頭標明「由 seasoningway 產生，不要手改」，`hook pre-edit` 擋直接修改。

| 檔案 | 內容 |
|---|---|
| `AGENTS.md`、`CLAUDE.md` | 硬性規則（個資、只做規劃）、`render` 和 `save` 什麼時候用、`guide` 有哪些主題 |
| `.claude/settings.json`、`.codex/hooks.json` | hook 都只呼叫 `seasoningway hook …` |
| `.claude/skills/`、`.agents/skills/` | 薄入口：什麼時候用，以及執行 `seasoningway guide <主題>` |
| `.claude/agents/`、`.codex/agents/` | subagent 定義。讀網頁的 subagent 沒有 shell，執行不了 `guide`，所以完整的做法要寫進定義檔 |
| `.githooks/` | 呼叫 `seasoningway hook git <事件>`；`status`、`save` 會設定 `core.hooksPath` |
| `.github/workflows/check.yml` | `uses: tsunderink/seasoningway@<版本>` |

使用者自己的規則寫在 `profile/`，不寫進轉接檔。

## 7. 版本

- 引擎用 semver。`0.x` 期間資料格式可以變，但每次變都要有 `migrate`。
- 資料格式另用 `format` 記版本。改紀錄 schema 或旅程 README 的欄位時，更新 `config.FORMAT`、在 `project.MIGRATIONS` 新增從前一版升級的步驟，並用虛構資料測試。`migrate` 支援從第 3 版起升級。
- 資料 repo 的 `seasoningway.toml` 寫 `engine = "X.Y.Z"`。`upgrade` 一起改這個版本、CI 的 `uses:` 版本，並重寫轉接檔。
- 安裝的引擎和 `engine` 的 major.minor 不同時：`status` 提醒，`save` 拒絕保存，避免用錯的格式寫資料。

## 8. 從舊引擎拿掉的

- 引擎和資料分開 commit、引擎 commit 訊息不能寫旅程細節的檢查（`commit-msg`、`commits`）。資料 repo 裡沒有引擎。
- 從模板建立、合併上游（包括 graft 歷史）的流程。改由 `pip install` 指定版本，再 `upgrade`。
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
