# 定案收尾

在使用者做決定的同一回合做完：

1. 寫進旅程 README「已決定」，開頭寫今天的日期。決定改了就改原來那一條、日期換成改的那天（必要時括號註明原本是什麼），不另加一條。
2. 刪掉那條「待決定」，以及做完的「下一步」。
3. 研究筆記裡被排除的選項，細節縮成一行結論（舊版留在 git 歷史）。
4. 地圖頁上被排除的點：在 `places/<id>.json` 把 `season.tier` 降到 2 以下，`season.why` 開頭寫原因（不刪檔，使用者的行程和星號用 id 記）。住宿點（有 `lodging`）沒有推薦度：不住的旅館刪掉地點檔，`itinerary.json`、`map/config.json` 用到的先改掉（check 會擋找不到的 id；地圖頁存的行程會略過不存在的 id）。
5. 有固定時間的決定（入住、預約時段）寫進地圖設定 `map/config.json` 的 `notes`（那天的固定事項）。
6. 要使用者去訂的，寫進 `bookings.json`，附建議預訂時間和連結，「下一步」連到 `bookings.json`。
7. `seasoningway render` 產生頁面。若重新發布 artifact，發布完成後以 `seasoningway published <旅程> <版本號>` 記錄版本。

完成條件：`check` 沒有 ✗，也沒有和這次決定有關的 ⚠️。

改完執行 `seasoningway save`。
