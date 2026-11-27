"""產生一趟旅程的地圖頁 map/index.html：places/*.json ＋ map/config.json ＋ map/ 裡的底圖、照片、路網。

python3 -m seasoningway.tripmap.build trips/<旅程>           # 產生，只在內容有變時寫檔
python3 -m seasoningway.tripmap.build trips/<旅程> --check   # 只比對、不寫檔：不是最新就印出原因、結束碼 1
平常不用自己跑：seasoningway build、check 會對每趟有 map/config.json 的旅程跑。
"""
import sys
from pathlib import Path

from ..common import Problem
from . import render
from .data import MapTrip, page_data


def outputs(trip_dir: Path) -> dict[Path, str]:
    t = MapTrip(trip_dir)
    data = page_data(t)
    return {t.map / "index.html": render.render(t, data)}


def main(argv: list[str]) -> int:
    args = [a for a in argv if not a.startswith("--")]
    if len(args) != 1:
        print(__doc__)
        return 2
    trip_dir = Path(args[0]).resolve()
    try:
        files = outputs(trip_dir)
    except Problem as e:
        print(e)
        return 1
    stale = [p for p, text in files.items() if not p.exists() or p.read_text(encoding="utf-8") != text]
    names = "、".join(p.relative_to(trip_dir.parents[1]).as_posix() for p in stale)
    if "--check" in argv:
        if stale:
            print(f"地圖頁不是最新：{names}。請執行 seasoningway build")
        return 1 if stale else 0
    for p in stale:
        p.write_text(files[p], encoding="utf-8", newline="\n")
    n = len(list((trip_dir / "places").glob("*.json")))
    print(f"{n} 個地點；" + (f"更新了 {names}" if stale else "地圖頁已是最新"))
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
