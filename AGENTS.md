# seasoningway 引擎 repo

這是公開的引擎 repo。使用者的旅程資料在各自的私人資料 repo，不在這裡。

## 開工前

1. 讀 [docs/design.md](docs/design.md)（定案的設計）和 [docs/migration.md](docs/migration.md)（從舊引擎搬過來的進度與待辦）。
2. 從 migration.md 第一個沒打勾的項目開始，或做使用者指定的項目。

## 硬性限制

- **任何旅程資料都不進這個 repo**：地名組合、日期、預算、預訂、個人偏好，以及從使用者資料 repo 看到的任何內容。測試只用 `tests/fixtures/` 裡虛構的資料。
- 不寫任何人的名字、電話、證件號碼、付款資訊、訂位代號、住址。
- 不 force push `main`。

## 改動流程

1. 從最新的 `main` 開分支：`<工具>/<主題>`，例如 `codex/port-schemas`、`claude/review-save`。
2. 改程式要附測試。跑 `pip install -e .`、`python3 -m unittest discover -s tests`，全部通過。
3. commit 訊息用 Conventional Commits，說明用繁體中文：`feat(cli): 實作 render`。範圍用模組名稱（`cli`、`render`、`save`、`schemas`、`tripmap`、`adapters`、`docs` 等）。
4. push 分支、開 PR 到 `main`，CI 通過後由使用者或使用者指定的審查者合併。
5. 完成 migration.md 的項目就在同一個 PR 裡打勾。

## 文件

- 改到設計時，改 design.md 原本那一條並換上當天日期，必要時括號註明原本是什麼；不另加一條「見下」。
- 每件事只寫在一個地方，其他地方用連結。
- 文件寫給下一個接手的 agent 看：求短、求準。

## 語言與環境

- 程式、檔名、識別字用英文；文件、訊息、commit 說明用繁體中文。
- Python 3.13 以上。第三方依賴只有 `jsonschema`；要加新的依賴，先在 design.md 寫下理由。
