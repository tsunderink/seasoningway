"""更新一趟旅程需要連網或另外取得的快取。"""
import argparse
import subprocess
from datetime import date
from pathlib import Path
from tempfile import TemporaryDirectory

from .common import Problem
from .files import replace_paths
from .tripmap import place_ids, plan_drive, roads
from .trips import load_trip

def configure_fetch(parser: argparse.ArgumentParser) -> None:
    parser.add_argument("slug", help="旅程代號")
    kinds = parser.add_subparsers(dest="kind", required=True, metavar="<種類>")
    drive = kinds.add_parser("drive", help="用 OSRM 試算逐日駕車")
    drive.add_argument("--plan", type=Path, help="地圖頁匯出的行程 JSON，預設用 itinerary.json")
    road = kinds.add_parser("roads", help="用 OSM PBF 產生路網")
    road.add_argument("--pbf", type=Path, required=True, help="道路範圍的 OSM PBF 檔")
    road.add_argument("--date", help="PBF 資料日期（YYYY-MM-DD）")
    base = kinds.add_parser("basemap", help="畫底圖並抓地名")
    base.add_argument("--rail", type=Path, required=True, help="N02 鐵路 GeoJSON")
    base.add_argument("--work", type=Path, required=True, help="已安裝 Node 底圖套件的工作目錄")
    base.add_argument("--date", help="Protomaps 建置日期（YYYYMMDD）")
    place = kinds.add_parser("place-ids", help="查 Google 地圖的 Place ID")
    selection = place.add_mutually_exclusive_group()
    selection.add_argument("--ids", nargs="+", help="只查指定的地點")
    selection.add_argument("--all", action="store_true", help="重查全部")
    selection.add_argument("--missing", action="store_true", help="查還沒有 Place ID 的地點（預設）")
    place.add_argument("--accept", action="append", default=[], metavar="ID", help="接受距離 2–10 公里的候選，可重複指定")


def source_file(path: Path) -> Path:
    path = path.resolve()
    if not path.is_file():
        raise Problem(f"找不到輸入檔 {path}")
    return path


def check_date(value: str | None, *, compact: bool = False) -> str | None:
    if value is None:
        return None
    try:
        parsed = date.fromisoformat(value)
    except ValueError:
        parsed = None
    if parsed is None or value != (parsed.isoformat().replace("-", "") if compact else parsed.isoformat()):
        raise Problem("--date 格式應為 " + ("YYYYMMDD" if compact else "YYYY-MM-DD"))
    return value


def cmd_fetch(args: argparse.Namespace, root: Path, today: date) -> int:
    trip = load_trip(root, args.slug)
    cache = trip.path / "cache"
    if not trip.path.resolve().is_relative_to(root.resolve()) or not cache.resolve().is_relative_to(trip.path.resolve()):
        raise Problem("旅程及快取目錄必須在資料 repo 內")
    if not (trip.path / "map/config.json").is_file():
        raise Problem(f"trips/{trip.slug}/map/config.json：抓取快取前需要地圖設定")

    with TemporaryDirectory(prefix=".fetch-", dir=trip.path) as directory:
        stage = Path(directory)
        try:
            if args.kind == "drive":
                source = source_file(args.plan or trip.path / "itinerary.json")
                name = "drive.txt"
                report = plan_drive.drive_report(trip.path, source, today)
                (stage / name).write_text(report, encoding="utf-8", newline="\n")
                print(report, end="")
                outputs = [name]
            elif args.kind == "roads":
                pbf = source_file(args.pbf)
                osm_date = check_date(args.date)
                code = roads.main([str(trip.path), str(pbf), *([osm_date] if osm_date else [])], output=stage)
                if code:
                    raise Problem(f"路網工具結束碼 {code}")
                outputs = ["roads.txt", "roads.json"]
            elif args.kind == "basemap":
                rail = source_file(args.rail)
                build = check_date(args.date, compact=True)
                work = args.work.resolve()
                if not (work / "node_modules/maplibre-gl").is_dir() or not (work / "node_modules/@protomaps/basemaps").is_dir():
                    raise Problem("底圖工作目錄缺少 MapLibre 或 Protomaps 套件，請見 docs/tripmap.md")
                from .tripmap import basemap_tiles

                output = stage / "base"
                argv = [str(trip.path), str(work), str(rail)] + ([build] if build else [])
                code = basemap_tiles.main(argv, output=output)
                if code:
                    raise Problem(f"底圖工具結束碼 {code}")
                if not all((output / name).is_file() for name in ("tiles.json", "places.json")):
                    raise Problem("底圖工具缺少 tiles.json 或 places.json")
                outputs = ["base"]
            else:
                name = "place_ids.json"
                changed = place_ids.refresh(trip.path, cache / name, stage / name, today,
                                            ids=args.ids, all_places=args.all, accept=set(args.accept))
                if not changed:
                    print("快取已包含要查詢的地點")
                    return 0
                outputs = [name]
            missing = [name for name in outputs if not (stage / name).exists()]
            if missing:
                raise Problem(f"快取工具沒有產生 {'、'.join(missing)}")
            replace_paths(stage, {stage / name: cache / name for name in outputs})
        except ModuleNotFoundError as error:
            raise Problem(f"抓取 {args.kind} 需要 {error.name}，安裝方式見 docs/tripmap.md") from None
        except (OSError, ValueError, KeyError, TypeError, IndexError, subprocess.SubprocessError) as error:
            raise Problem(f"抓取 {args.kind} 失敗（{error}）") from None
    print(f"已更新 trips/{trip.slug}/cache/ 的 {'、'.join(outputs)}")
    return 0
