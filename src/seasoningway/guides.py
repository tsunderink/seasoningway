"""資料 repo 的共用流程，由指令與各工具的轉接檔共用。"""
from importlib.resources import files

TOPICS = {
    "plan-trip": "初始化資料 repo、開新旅程、規劃行程或調整旅程日期。",
    "scout": "按地區蒐集候選景點、餐廳，並整理成地點檔。",
    "recheck": "重查過期或待確認的旅遊資訊、估價、匯率與入境規定。",
    "check-page": "在手機、平板與桌面尺寸檢查產生的網頁並截圖。",
    "decide": "使用者做了決定後，當回合整理旅程的待決定、預訂與下一步。",
    "retro": "旅程結束後整理回顧及經使用者確認的心得。",
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
