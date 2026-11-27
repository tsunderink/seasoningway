---
title: 範例市冬遊
status: 規劃中
start: 2099-01-15
end: 2099-01-19
travelers: 2
currency: TWD
show_also:
budget_target: 60000
---
# 範例市冬遊

<!-- status：構想／規劃中／已訂妥／旅行中／已結束；日期 YYYY-MM-DD，未定留白；currency 是預算基準幣別；show_also 是 budget.md 另外並列的幣別（要在 budget.json 的 rates 有匯率）；budget_target 是全體合計的預算目標（基準幣別，未定留白）。改完執行 seasoningway render。建立於 2026-11-24。 -->

## 概要

- 目的地：範例市（虛構的城市，這趟旅程是引擎的範例資料）
- 同行者：我、伴侶
- 主題：雪景、老街燈會

## 已決定

- 2098-11-10：住範例市車站附近，兩天各搭一次巴士到老街和河堤步道（見本川の遊歩道）

<!-- 一條一行並附日期，例如：- 2026-11-17：住車站附近 -->

## 待決定

- 第三晚住哪間（[住宿候選](research/lodging.md)）

## 下一步

- 使用者 2098-11-20 前訂第一、二晚的住宿（[bookings.json](bookings.json)）
- Claude 重查住宿候選的房價再比較

## 檔案

- [逐日行程](itinerary.json)
- [研究筆記](research/)
- [地點](places/)（一個點一個檔，行程每天的 `places` 用 id 引用）
- [預算摘要](budget.json)
- [預訂清單](bookings.json)
- [行李清單](packing.md)
- [旅後心得](retro.md)
