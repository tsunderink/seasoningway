# seasoningway

讓 AI agent 規劃旅程的引擎。名字取 season（依季節走的旅程）和 seasoning（替旅程調味）的雙關。

旅程資料放在你自己的私人 repo（資料 repo），由任何會讀檔、會跑指令的 AI agent（Claude Code、Codex 或其他）規劃。seasoningway 負責驗證資料、產生頁面、保存到 git，讓不同的 agent 交出同樣工整的成果。

> **狀態：骨架。** 指令介面已經定案，實作正從舊引擎搬過來，進度見 [docs/migration.md](docs/migration.md)。

## 安裝

```bash
pip install "git+https://github.com/tsunderink/seasoningway@<版本>"
```

需要 Python 3.13 以上。

## 指令

| 指令 | 做什麼 |
|---|---|
| `seasoningway status` | 開場：列出旅程、下一步和要處理的問題 |
| `seasoningway render` | 驗證資料再產生頁面；驗不過就不產出 |
| `seasoningway save` | 驗證、commit、同步 |
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
