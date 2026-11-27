# seasoningway

讓 AI agent 規劃旅程的引擎。名字取 season（依季節走的旅程）和 seasoning（替旅程調味）的雙關。

旅程資料放在你自己的私人 repo（資料 repo），由任何會讀檔、會跑指令的 AI agent（Claude Code、Codex 或其他）規劃。seasoningway 負責驗證資料、產生頁面、保存到 git，讓不同的 agent 交出同樣工整的成果。

舊模板的遷移與版本發布進度見 [遷移清單](docs/migration.md)。

## 安裝

```bash
pip install "git+https://github.com/tsunderink/seasoningway@<版本>"
```

需要 Python 3.13 以上。

## 建立資料 repo

在新的空目錄執行 `seasoningway init`，再用 `seasoningway guide plan-trip` 完成設定。啟動 Claude Code 或 Codex 前，請確認該工具能從 `PATH` 執行 `seasoningway`。在 Codex 信任專案後到 `/hooks` 核准 hooks；需要手動執行時，開場用 `seasoningway status`，收尾用 `seasoningway save --check`。

換用新版引擎時，安裝目標版本即可：下次開 session（或執行 `seasoningway status`）時，若安裝的版本比 `seasoningway.toml` 的 `engine` 新，會自動升級並保存（[設計第 7 節](docs/design.md#7-版本)）。也可以手動執行 `seasoningway upgrade` 和 `seasoningway save`。

## 從舊模板遷移

安裝 `v0.1.0` 後，在舊模板的 Git repo 根目錄執行 `seasoningway migrate --from-template`。指令會先在暫存副本遷移並驗證，完成後才搬移資料、移除舊引擎和啟用新 hooks。Git 歷史與遙端設定會保留。舊引擎或索引有未保存的變動時，先把它們保存。

接著執行 `seasoningway render`，檢查 `out/` 頁面，確認後用 `seasoningway save` 保存。

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
