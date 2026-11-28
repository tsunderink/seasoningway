# 提案：資料 repo 不再依賴 agent hooks（2026-11-28，待討論）

狀態：草案，尚未決定是否實作。討論後若採用，把結論改進 [design.md](design.md) 對應條目，再刪掉這份檔案。

## 1. 目標情境

使用者不碰終端機：

1. 在 GitHub 開一個空的私人 repo。
2. 用這個 repo 開 Claude Code cloud session（或 Codex），貼上本 repo 的網址，請 agent「用 seasoningway 規劃旅程」。
3. 之後每次都從同一個 repo 開新 session 接續。

使用者不需要手動執行 `pip`、`init`、設定雲端環境的設定腳本，也不需要調整工具的進階開關。

## 2. 觀察到的問題

| # | 問題 | 依據 |
|---|---|---|
| P1 | agent 拿到網址會先 clone，引擎被放到子目錄。之後讀到的是引擎 repo 的 `AGENTS.md`（寫給開發引擎的 agent），不是資料 repo 的規則 | 使用者實際遇到（Codex） |
| P2 | 在引擎 clone 裡執行 `init`，錯誤訊息是「AGENTS.md：已有自訂檔案，請先備份並把個人規則移到 profile/」，可能誘導 agent 蓋掉引擎的 `AGENTS.md` | 實測 |
| P3 | 資料 repo 裡沒有引擎（design.md 第 8 節）。雲端每個 session 都是新容器，hooks 呼叫的 `seasoningway` 指令不存在 | 實測：`init` 產生的檔案裡沒有任何 `.py` |
| P4 | `AGENTS.md` 寫「開場 hook 未執行時用 `seasoningway status`」「收尾 hook 未執行時用 `seasoningway save --check`」，但 agent 看不出 hook 有沒有跑，這兩個條件無法判斷 | 讀模板 |
| P5 | Codex 的 hooks 是進階開關，hooks 內容一改就會被關掉，而且沒有提示 | 使用者回報，未在本環境驗證 |
| P6 | agent 改 `AGENTS.md`（保留產生標記），`save` 照常保存，`check` 也通過。改過的規則會留到下次 `upgrade` | 實測 |
| P7 | 讀網頁的 subagent 有寫檔工具：Claude 的 `spot-scout` 有 `Edit`，`fact-checker` 有 `Write, Edit`；Codex 的對應 subagent 只關 shell（`shell_tool = false`），仍能寫檔。目前只有 pre-edit hook 限制它們能寫哪些路徑 | 實測產生的 `.claude/agents/`、`.codex/agents/` |

P1、P2 是第一次使用時的入口問題；P3 到 P5 說明 hooks 在目標情境不可靠；P6、P7 是拿掉 hooks 前必須先補的保護。

## 3. 現有保護逐項檢查

hooks 的實作在 `src/seasoningway/hooks.py`。下表是在沒有 Claude 和 Codex hooks 的資料 repo 實測的結果。

| hook | 做什麼 | 沒有 hook 時 |
|---|---|---|
| session-start | 執行 `status`：快轉同步、自動升級 | `AGENTS.md` 要求開場執行 `status` 就能取代 |
| pre-edit | 擋新增的個資、不符 schema 的紀錄、改轉接檔、`out/`、`.git/`；限制 subagent 能寫的路徑 | 個資、schema：`save` 驗不過就不保存（實測個資會被擋）。轉接檔：**會被保存**（P6）。subagent 路徑：**沒人擋**（P7） |
| post-edit | 改完立刻驗那個檔 | `save` 會驗，只是比較晚發現。guide 已寫好沒有 hook 時的做法（有 shell 就執行 `check`，沒有就在回報寫明，交給主 session） |
| stop | `save --check`，沒保存就不讓回合結束 | 本機：下次開場 `status` 會提醒「工作區有未保存的改動」。**雲端：容器回收後改動就消失** |

不受影響的保護：

- `.githooks/` 的 pre-commit（驗證索引快照）和 pre-push（掃個資）。這些是 git 本身的 hooks，不需要工具核准。`status` 和 `save` 都會重新設定 `core.hooksPath`。實測：不經 `save` 直接 `git commit` 帶個資的改動，會被擋下。
- `save` 在 `main` 模式下 push 前會先 pull 再驗證一次。
- 資料 repo 的 CI。

design.md 第 1 條原則本來就規定：工具專屬的功能（hook、skill、subagent）只用來加速，不能是唯一的關卡。P6、P7 違反了這條原則。

## 4. 提案

1. **`AGENTS.md` 開頭改成不帶條件**：「每個 session 第一件事：執行 `seasoningway status`。找不到指令時，先執行 `pip install --upgrade "https://github.com/tsunderink/seasoningway/releases/latest/download/seasoningway.zip"`。」解決 P3、P4。安裝最新版正好配合 design.md 第 7 節的自動升級。
2. **改成每完成一件事就 `save`**，拿掉所有「hook 未執行時」的條件句。這樣雲端忘了保存時，損失最多是最後一步。
3. **不再產生 Claude 和 Codex 的 hooks**：
   - `.claude/settings.json` 只留 `permissions`（`Bash(seasoningway:*)` 和拒絕清單），拿掉 `hooks`。
   - 不再產生 `.codex/hooks.json`，靠 `upgrade` 現有的移除舊轉接檔機制刪掉。
   - `hooks.py` 拿掉 `claude`、`codex` 的事件處理，保留 `hook git`。
   - 解決 P5。
4. **`check` 比對轉接檔**：用 `adapter_updates()` 算出應有的內容，跟 repo 裡的不同就報問題，於是 `save` 拒絕保存，提示執行 `seasoningway upgrade`。解決 P6。被改的檔沒有產生標記的情況，沿用 `upgrade` 現有的處理（要求使用者先備份）。
5. **讀網頁的 subagent 不再直接寫 repo**：拿掉 `Write`、`Edit`，把結果交回主 session，由主 session 寫入、執行 `check` 和 `save`。解決 P7。取捨見第 5 節第 2 點。
6. **入口文件**（P1、P2）：
   - README「建立資料 repo」第 1 步寫明「不要 clone 本 repo」，並說明已經 clone 到子目錄時該怎麼辦。
   - 一併處理有檔案但沒有 `seasoningway.toml` 的資料夾：舊模板改走「從舊模板遷移」，其他情況直接 `init`。
   - 措辭不只針對 Codex。
   - 引擎 repo 的 `AGENTS.md` 開頭加一行：「本檔寫給開發引擎的 agent；要用 seasoningway 規劃旅程，請照 README『建立資料 repo』做。」
   - `init` 在引擎 repo 裡執行時，直接說明這是引擎 repo，請到 workspace 根目錄執行，不要出現 P2 的錯誤訊息。
7. **文件與版本**：
   - 改寫 design.md 第 5 節第 2 點（寫入時那一層）、第 6 節的轉接檔表、第 7 節「`status`（也就是 SessionStart hook）」和「雲端環境的設定腳本」的說法。
   - 改寫 guide 裡「有 hook 時……」的段落。
   - 轉接檔改變、資料格式不變，所以不需要 `migrate`。版本升 minor。

## 5. 取捨與風險

1. **開場不再強制執行**：agent 沒照 `AGENTS.md` 開場執行 `status`，就不會同步和自動升級。`save` 在 `main` 模式下 push 前會 pull，所以資料不會衝突，只是升級會延後。
2. **subagent 不寫檔會占用主 session 的上下文**：現在 guide 的設計是「主 session 只看得到回報，所以查到的內容都寫進檔案，回報只放摘要」。改成交回主 session 寫，完整內容都會進主 session，派多個 subagent 時更明顯。替代做法：
   - (a) subagent 保留寫檔權限，但只靠第 4 點保護轉接檔；`profile/` 和旅程檔被惡意網頁內容改掉的風險，由使用者看 commit 發現。
   - (b) subagent 把內容寫到 repo 外的暫存目錄，主 session 驗證後搬進來。
3. **Codex 能不能拿掉 subagent 的寫檔工具不確定**：目前產生的 `.codex/agents/*.toml` 只關 shell。若 Codex 做不到，第 5 點在 Codex 上只能靠指示。
4. **雲端忘了保存仍會丟資料**：第 2 點只能把損失降到最後一步，無法完全避免。
5. **網路限制**：第 1 點的安裝要能連到 GitHub 的 Release 下載。在 Claude Code cloud 預設網路下已實測可以安裝，網路設得更嚴的環境沒有驗證。

## 6. 考慮過但不採用

| 做法 | 不採用的原因 |
|---|---|
| 在引擎 `AGENTS.md` 依使用者目的分流 | 把開發者文件和使用者文件混在一起；只加一行說明這份檔案寫給誰（第 4 節第 6 點） |
| 讓 hook 找不到指令時自己安裝引擎 | 還是依賴 hooks，P5 沒解決 |
| 把引擎放一份進資料 repo | 違反 design.md 第 8 節，每個 repo 都要各自更新程式碼 |
| 做成全域 skill 或 plugin | 要先安裝才能用，目標情境的第一個 session 什麼都還沒裝；skill 由 agent 判斷是否載入，不保證每個 session 都讀到；skill 和 `AGENTS.md` 一樣只是指示，擋不了東西 |
| 要使用者手動 `pip install` 和 `init`，或設定雲端環境的設定腳本 | 違反目標情境 |
| 保留 hooks，另外讓 `status` 偵測 hooks 沒在跑並提示 | 維持兩條路徑，偵測只能用時間推測，可能誤報 |

## 7. 待討論

1. P5：Codex 的 hooks 一改就被關掉，是否確定？Codex 有沒有不靠 hooks 的替代機制？
2. 第 5 節第 2 點選哪個：subagent 不寫檔、(a) 或 (b)？
3. 第 5 節第 3 點：Codex 的 subagent 能不能拿掉寫檔工具？
4. 第 4 節第 4 點的比對要多嚴格：完全相同，還是忽略行尾空白等差異？
5. 拿掉 hooks 後，`hook claude|codex` 指令要立即移除，還是先保留一版，讓還沒 `upgrade` 的資料 repo 的 hooks 不會報錯？

## 8. 驗證方式（實作時）

- 單元測試：`check` 發現改過的轉接檔；`upgrade` 刪除 `.codex/hooks.json`；新產生的 `.claude/settings.json` 不含 `hooks`；`init` 在引擎 repo 裡回報正確的錯誤。
- 情境測試：在空目錄模擬「沒裝引擎 → 照 `AGENTS.md` 安裝 → `status` → 修改 → `save`」整個流程。
- 實際用一次 Claude Code cloud 和 Codex，從空 repo 貼網址跑完第 1 節的流程。
