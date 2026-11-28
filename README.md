# seasoningway

讓 AI agent 規劃旅程的引擎。名字取 season（依季節走的旅程）和 seasoning（替旅程調味）的雙關。

旅程資料放在你自己的私人 repo（資料 repo），由任何會讀檔、會跑指令的 AI agent（Claude Code、Codex 或其他）規劃。seasoningway 負責驗證資料、產生頁面、保存到 git，讓不同的 agent 交出同樣工整的成果。

## 建立資料 repo

使用者貼出本 repo 的 GitHub 網址並請你規劃旅程時，把使用者為本次 session 選定的 workspace 根目錄當成資料 repo 的根目錄。即使 shell 已切到子目錄，也回到這個根目錄執行以下步驟。

1. 依照[安裝說明](#安裝)安裝 `seasoningway` 套件，確認 agent 能從 `PATH` 執行它。引擎原始碼只在開發引擎時才需要 clone。
2. 如果根目錄已有 `seasoningway.toml`，讀取同一目錄的 `AGENTS.md`，執行 `seasoningway status` 後接續規劃。如果 workspace 是新的空目錄，直接在根目錄執行 `seasoningway init`。
3. 初始化後，在這個 session 讀取根目錄剛產生的 `AGENTS.md`，再執行 `seasoningway guide plan-trip` 並依照指引開始規劃。之後也從同一個 workspace 開新 session，讓 Codex 自動載入這份 `AGENTS.md`。

在 Codex 信任專案後到 `/hooks` 核准 hooks。需要手動執行時，開場用 `seasoningway status`，收尾用 `seasoningway save --check`。

換用新版引擎時，安裝目標版本即可。下次開 session（或執行 `seasoningway status`）時，若安裝的版本比 `seasoningway.toml` 的 `engine` 新，會自動升級並保存（[設計第 7 節](docs/design.md#7-版本)）。也可以手動執行 `seasoningway upgrade` 和 `seasoningway save`。

## 安裝

需要 Python 3.13 以上。安裝或更新到最新的 GitHub 正式發布版：

```bash
pip install --upgrade "https://github.com/tsunderink/seasoningway/releases/latest/download/seasoningway.zip"
```

需要指定版本時，使用完整的 Git 標籤，例如 `v0.4.0`：

```bash
pip install "git+https://github.com/tsunderink/seasoningway@v0.4.0"
```

## 從舊模板遷移

安裝 `v0.1.0` 後，在舊模板的 Git repo 根目錄執行 `seasoningway migrate --from-template`。指令會先在暫存副本遷移並驗證，完成後才搬移資料、移除舊引擎和啟用新 hooks。Git 歷史與遙端設定會保留。舊引擎或索引有未保存的變動時，先把它們保存。

接著執行 `seasoningway render`，檢查 `out/` 頁面，確認後用 `seasoningway save` 保存。

舊模板的遷移與版本發布進度見[遷移清單](docs/migration.md)。

## 指令

| 指令 | 做什麼 |
|---|---|
| `seasoningway status` | 開場：列出旅程、下一步和要處理的問題 |
| `seasoningway render [旅程]` | 驗證資料，再將頁面寫到 `out/` |
| `seasoningway save` | 驗證、commit、同步 |
| `seasoningway save --check` | 只讀檢查驗證、工作區與推送狀態 |
| `seasoningway fetch <旅程> <種類>` | 更新駕車、路網、底圖或地點 ID 快取 |
| `seasoningway guide <主題>` | 印出規劃流程的說明 |
| `seasoningway init` / `upgrade` | 建立資料 repo、換版本後更新 |

完整清單：`seasoningway --help`。設計和理由見 [docs/design.md](docs/design.md)。

## 開發

```bash
pip install -e .
python3 -m unittest discover -s tests
```

規則見 [AGENTS.md](AGENTS.md)。

## 授權

MIT
