"""資料 repo 的共用流程，由指令與各工具的轉接檔共用。"""
from importlib.resources import files

TOPICS = {
    "plan-trip": "初始化資料 repo、開新旅程與規劃行程。剛建立資料 repo、使用者提出新旅程或要開始規劃、旅程日期變動時用。",
    "scout": "蒐集景點、餐廳並整理成地點檔。要替旅程找候選點，或補充某個地區的點時用。",
    "recheck": "重查事實並更新查詢日期。檢查提醒資訊、估價或匯率過期，要依待確認的資訊做決定，或出發前 14 天內要重查入境規定時用。",
    "check-page": "在手機、平板與桌面尺寸操作網頁並截圖。改動地圖等產生頁面，或使用者要確認頁面的實際效果時用。",
    "decide": "當回合更新決定、預訂與下一步。使用者對旅程的待決定事項選定方案時用。",
    "retro": "記錄旅後回顧及經使用者確認的心得。旅程狀態改為已結束，或使用者要回顧旅程時用。",
}


def guide(topic: str) -> str:
    folder = files("seasoningway").joinpath("guides")
    text = folder.joinpath(f"{topic}.md").read_text(encoding="utf-8")
    if topic == "scout":
        text += "\n" + folder.joinpath("scout-region.md").read_text(encoding="utf-8")
    return text


def cmd_guide(args, root, today) -> int:
    print(guide(args.topic), end="")
    return 0
