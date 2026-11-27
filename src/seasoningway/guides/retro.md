# 旅後檢討

1. 和使用者一起寫旅程的 `retro.md`，記下整體感想、做得好的地方和下次想改的事。預算落差用 `seasoningway budget` 計算，回顧寫下原因。
2. 預算每類記一筆「實付」概算進 `budget.json`，再執行 `seasoningway budget <旅程代號>`。
3. 從「要帶到下一趟的候選」提議要寫進 `profile/lessons.md` 的心得。使用者確認後才寫，並註明出處：`（出自 [旅程代號](../trips/旅程代號/retro.md)，YYYY-MM-DD）`。
4. 「repo 和流程」由規劃助理檢討，不用問使用者。對照這趟的 git log 和 check 的 ⚠️ 找出：
   - 重複出現的錯誤整理成引擎的驗證需求和測試案例。
   - 沒用上或被繞過的規則：刪掉或改寫。
   - 沒人用的檔案或步驟：刪掉。

   引擎改進只整理通用問題，交到 seasoningway 引擎 repo 處理。旅程回顧留在資料 repo。
5. 旅程 README 的 `status` 改成「已結束」。

完成條件：`retro.md` 每段都有內容或寫明「沒有」，使用者確認過的心得已寫入 `profile/lessons.md`。改完執行 `seasoningway save`。
