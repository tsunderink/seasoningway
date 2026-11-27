"""資料 repo 的驗證與整理提醒。"""
from __future__ import annotations

import argparse
import json
import re
from dataclasses import dataclass, field
from datetime import date, timedelta
from pathlib import Path, PurePosixPath
from urllib.parse import unquote

from .common import CONFIG, ENGINE_DOCS, TRIPS, Problem, real_date
from .config import ASSISTANTS, Config, config_problems
from .gitops import files_to_scan
from .pii import check_pii
from .published import published_states, unpublished
from .records import (STALE_DAYS, missing_dependency, record_kind, record_paths,
                      record_problems, ref_problems, stale_values)
from .trips import Trip, load_trips, section_lines
from .tripmap import build as map_build
from .tripmap.render import MapPage
from .views import VIEW_SOURCES, trip_views


ACCESSED_RE = re.compile(r"查詢於\s*(\d{4}-\d{2}-\d{2})")


def research_problems(root: Path, path: Path) -> list[str]:
    rel = path.relative_to(root).as_posix()
    problems = []
    for n, line in enumerate(path.read_text(encoding="utf-8").splitlines(), 1):
        sources = line.replace(f"]({ENGINE_DOCS}", "](")  # 連到引擎說明的不算來源
        if ("http://" in sources or "https://" in sources) and not ACCESSED_RE.search(line):
            problems.append(f"{rel}:{n}：有來源連結，但同一行沒有「查詢於 YYYY-MM-DD」")
        problems += [f"{rel}:{n}：「查詢於 {d}」不是存在的日期" for d in ACCESSED_RE.findall(line) if not real_date(d)]
    return problems


def check_research(root: Path) -> list[str]:
    return [p for path in sorted((root / TRIPS).glob("*/research/**/*.md")) for p in research_problems(root, path)]


# ---------------------------------------------------------------- 整理債（hygiene）
# repo 只有規劃助理在讀。能用程式判斷的整理規則寫在這裡，不只寫在 AGENTS.md（AGENTS.md 只寫原則，指到這裡）。
# ✗ 擋下：壞的相對連結、合併衝突留下的標記。
# ⚠️ 提醒（SessionStart 會列出，碰到那個檔案的回合順手修）：同一段重複引用同一來源、沒人連到的研究筆記、
#   「已決定」裡的「見下」、README 各段項數、字數預算、「待決定」連到的筆記有過期引用、
#   旅程資料夾裡同層沒有 README、或 README 沒提到的程式（.py、.mjs）、「已決定」開頭沒寫日期、「下一步」開頭沒寫誰做、
#   不是助理（ASSISTANTS）做的「下一步」要訂東西卻沒連到 bookings.json、待訂的項目過了 book_on、artifact 的檔案改了還沒重新發布（published.json）、出發前 60 天內還沒有入境規定的筆記。

LINK_RE = re.compile(r"\]\(([^)\s]+)\)")
RESEARCH_REF_RE = re.compile(r"research/[A-Za-z0-9_./-]+\.md")
CONFLICT_RE = re.compile(rb"^(?:<{7}|>{7})(?: |$)", re.M)
BINARY_SUFFIXES = (".jpg", ".jpeg", ".png", ".webp", ".gif", ".pdf")
CODE_RE = re.compile(r"`[^`]*`")
COMMENT_RE = re.compile(r"<!--.*?-->", re.S)
REPEAT_CITES = 3
CHAR_BUDGET = (  # （路徑樣式, 字數上限）：超過就提醒精簡，第一個符合的算數（PurePath.full_match，** 可以是零層）
    ("AGENTS.md", 5000),  # 兩個助理每個 session 都讀；Claude 另外讀 .claude/CLAUDE.md
    ("CLAUDE.md", 1500),
    (f"{TRIPS}/*/README.md", 7000),
    (f"{TRIPS}/*/research/**/*.md", 9000),
    ("**/*.md", 12000),
)
SECTION_LIMITS = (("已決定", 30), ("待決定", 15), ("下一步", 15))
DECIDED_RE = re.compile(r"\d{4}-\d{2}-\d{2}")  # 「已決定」每項的開頭


def prose(text: str) -> str:
    """去掉 HTML 註解和行內程式碼，裡面的範例連結不算。"""
    return CODE_RE.sub("", COMMENT_RE.sub("", text))


def is_utf8(path: Path) -> bool:
    try:
        path.read_bytes().decode("utf-8")
        return True
    except UnicodeDecodeError:
        return False


def hygiene_files(root: Path) -> list[Path]:
    return [p for p in files_to_scan(root) if p.suffix in (".md", ".json")]


def link_problems(root: Path, path: Path, text: str | None = None) -> list[str]:
    rel = path.relative_to(root).as_posix()
    if path.suffix != ".md":
        return []
    if text is None:
        text = path.read_text(encoding="utf-8")
    return [
        f"{rel}:{n}：連結 {target} 指向不存在的檔案"
        for n, line in enumerate(prose(text).splitlines(), 1)
        for target in LINK_RE.findall(line)
        if not re.match(r"[a-z][a-z0-9+.-]*:|#|//", target, re.I) and not (path.parent / link_path(target)).exists()
    ]


def link_path(target: str) -> str:
    """Markdown 連結的目標去掉 #錨點、解開 %20 這類編碼，得到相對路徑。"""
    return unquote(target.split("#")[0])


def check_links(root: Path, files: list[Path]) -> list[str]:
    return [p for path in files for p in link_problems(root, path)]


def check_conflicts(root: Path) -> list[str]:
    """merge、rebase 解衝突時漏刪的標記：JSON 會解析失敗，Markdown 不會，要另外擋。"""
    problems = []
    for path in files_to_scan(root):
        if path.suffix.lower() in BINARY_SUFFIXES:
            continue
        data = path.read_bytes()
        m = CONFLICT_RE.search(data)
        if m:
            n = data.count(b"\n", 0, m.start()) + 1
            problems.append(f"{path.relative_to(root).as_posix()}:{n}：還有合併衝突的標記（<<<<<<<、>>>>>>>）：整合兩邊，刪掉標記")
    return problems


def linked_notes(root: Path, texts: dict[Path, str]) -> set[Path]:
    """有別的檔案連到或提到的筆記：Markdown 的相對連結（相對於所在的檔案），以及同一趟旅程的檔案裡寫到的
    research/….md（JSON 的連結和來源、README 裡沒加連結的提及，都相對於旅程資料夾）。"""
    linked = set()
    for path, text in texts.items():
        targets = set()
        if path.suffix == ".md":
            targets |= {(path.parent / link_path(t)).resolve() for t in LINK_RE.findall(prose(text)) if not re.match(r"[a-z]+:|#", t)}
        parts = path.relative_to(root).parts
        if parts[:1] == [TRIPS] and len(parts) > 2:
            targets |= {(root.joinpath(*parts[:2]) / r).resolve() for r in RESEARCH_REF_RE.findall(text)}
        linked |= targets - {path.resolve()}
    return linked


def file_problems(root: Path, path: Path) -> list[str]:
    """改了一個檔之後馬上能查的 ✗（供 post-edit 使用）：研究引用沒日期、壞連結；
    紀錄檔的格式和 x-ref（指到的地點、類別、筆記要存在）。"""
    rel = path.relative_to(root).as_posix()
    if not path.is_file():
        return []
    name = record_kind(rel)
    if name:
        text = path.read_text(encoding="utf-8")
        return record_problems(rel, text) or ref_problems(root, rel, name, json.loads(text))
    if path.suffix != ".md":
        return []
    parts = rel.split("/")
    research = rel.startswith(f"{TRIPS}/") and len(parts) > 3 and parts[2] == "research"
    return (research_problems(root, path) if research else []) + link_problems(root, path)


def note_warnings(rel: str, text: str, linked: set[Path], path: Path) -> list[str]:
    """一份 Markdown 的字數預算；研究筆記另查同段重複引用和有沒有被連到。"""
    warnings = []
    limit = next(n for pattern, n in CHAR_BUDGET if PurePosixPath(rel).full_match(pattern))
    if len(text) > limit:
        warnings.append(f"{rel}：{len(text):,} 字，超過 {limit:,} 字的預算，精簡或拆檔")
    if "/research/" not in rel:
        return warnings
    section, counts = "", {}
    for line in text.splitlines() + ["#"]:
        if line.startswith("#"):
            if any(c >= REPEAT_CITES for c in counts.values()):
                warnings.append(f"{rel}「{section}」：同一來源引用了 {max(counts.values())} 次，改成段首一行「來源：…」")
            section, counts = line.lstrip("# ").strip(), {}
        elif ACCESSED_RE.search(line) and not line.startswith("來源："):
            for u in set(re.findall(r"\]\((https?://[^)\s]+)\)", line)):
                counts[u] = counts.get(u, 0) + 1
    if path.resolve() not in linked:
        warnings.append(f"{rel}：沒有任何檔案連到它；還有用就從 README 或相關筆記連過來，沒用就刪")
    return warnings


def script_warnings(root: Path) -> list[str]:
    """旅程資料夾裡的程式要列在同層 README。"""
    warnings = []
    for script in sorted(p for ext in ("*.py", "*.mjs") for p in (root / TRIPS).glob(f"*/**/{ext}")):
        readme, rel = script.parent / "README.md", script.relative_to(root).as_posix()
        if "__pycache__" in script.parts:
            continue
        if not readme.is_file():
            warnings.append(f"{rel}：同資料夾沒有 README.md；還有用就寫一份列出每支程式，沒用就刪")
        elif script.name not in readme.read_text(encoding="utf-8"):
            warnings.append(f"{rel}：同資料夾的 README.md 沒提到它；還有用就寫進檔案表，沒用就刪")
    return warnings


def readme_warnings(trip: Trip, cfg: Config) -> list[str]:
    """旅程 README「已決定」「待決定」「下一步」的寫法（AGENTS.md「事實來源」）。"""
    readme, where = trip.path / "README.md", f"{TRIPS}/{trip.slug}/README.md"
    sections = {heading: section_lines(readme, heading) for heading in ("已決定", "待決定", "下一步")}
    warnings = []
    for heading, limit in SECTION_LIMITS:
        lines = [l for l in sections[heading] if l.startswith("- ")]
        if len(lines) > limit:
            warnings.append(f"{where}「{heading}」有 {len(lines)} 項（上限 {limit}）：合併、刪掉做完或被取代的")
    for line in sections["已決定"]:
        if line.startswith("- ") and not DECIDED_RE.match(line[2:]):
            warnings.append(f"{where}「已決定」要以決定的日期開頭（YYYY-MM-DD：）：{line[2:22]}…")
    for line in sections["下一步"]:
        if line.startswith("- ") and not line[2:].startswith(cfg.actors):
            warnings.append(f"{where}「下一步」要先寫誰做（{'、'.join(cfg.actors)}；"
                            f"角色代稱寫在 {CONFIG} 的 people.roles）：{line[2:22]}…")
        elif not line[2:].startswith(ASSISTANTS) and re.search(r"(?<![補已])訂(?!單)", line) and "bookings.json" not in line:
            warnings.append(f"{where}「下一步」：要訂的東西寫進 bookings.json（附建議預訂時間、連結），再連到 bookings.json：{line[2:22]}…")
    if any("見下" in l for l in sections["已決定"]):
        warnings.append(f"{where}「已決定」有「見下」：決定改了就改原來那一條")
    return warnings


def hygiene_warnings(root: Path, files: list[Path], today: date, cfg: Config) -> list[str]:
    texts = {p: p.read_text(encoding="utf-8") for p in files}
    linked = linked_notes(root, texts)
    warnings = [w for path, text in texts.items() if path.suffix == ".md"
                for w in note_warnings(path.relative_to(root).as_posix(), text, linked, path)]
    warnings += script_warnings(root)
    for trip in load_trips(root):
        warnings += readme_warnings(trip, cfg)
        if trip.active:
            warnings += stale_for_decisions(root, trip, today)
            warnings += entry_missing(trip, today)
    return warnings


ENTRY_DAYS = 60  # 出發前幾天開始提醒要有入境規定的筆記
ENTRY_RECHECK_DAYS = 14  # 入境規定要在出發前幾天內重查（AGENTS.md「研究與引用」）


def entry_missing(trip: Trip, today: date) -> list[str]:
    """出發前 ENTRY_DAYS 天內，research/ 沒有一份寫到「入境」的筆記就提醒；
    出發前 ENTRY_RECHECK_DAYS 天內，這些筆記最新的「查詢於」早於那天也提醒。"""
    try:
        start = date.fromisoformat(trip.meta.get("start", ""))
    except ValueError:
        return []
    days = (start - today).days
    if not 0 <= days <= ENTRY_DAYS:
        return []
    notes = [t for t in (p.read_text(encoding="utf-8") for p in (trip.path / "research").rglob("*.md")) if "入境" in t]
    if not notes:
        return [f"{TRIPS}/{trip.slug}：再 {days} 天出發，research/ 還沒有入境規定（簽證、停留天數、入境手續）的筆記；"
                f"出發前 {ENTRY_RECHECK_DAYS} 天內要再重查"]
    due = start - timedelta(days=ENTRY_RECHECK_DAYS)
    latest = max((d for t in notes for d in ACCESSED_RE.findall(t)), default="")
    if days <= ENTRY_RECHECK_DAYS and latest < due.isoformat():
        return [f"{TRIPS}/{trip.slug}：再 {days} 天出發，入境規定的筆記最後查詢於 {latest or '（沒有日期）'}，"
                f"{due.isoformat()} 以後要重查（簽證、入境規定出發前 {ENTRY_RECHECK_DAYS} 天內重查）"]
    return []


def stale_for_decisions(root: Path, trip: Trip, today: date) -> list[str]:
    """「待決定」連到的研究筆記有超過 STALE_DAYS 天的引用：做決定前要重查。其他筆記不提醒，免得雜訊蓋過重點。"""
    targets = []
    for line in section_lines(trip.path / "README.md", "待決定"):
        for target in LINK_RE.findall(prose(line)):
            path = trip.path / link_path(target)
            if "/research/" in path.as_posix() and path.suffix == ".md" and path.is_file() and path not in targets:
                targets.append(path)
    warnings = []
    for path in targets:
        old = [d for d in ACCESSED_RE.findall(path.read_text(encoding="utf-8"))
               if (when := real_date(d)) and (today - when).days > STALE_DAYS]  # 不存在的日期由 research_problems 擋
        if old:
            warnings.append(f"{path.relative_to(root).as_posix()}：「待決定」用到它，有 {len(old)} 處引用超過 {STALE_DAYS} 天"
                            f"（最舊 {min(old)}），做決定前重查（照 recheck skill）")
    return warnings


def check_records(root: Path, trips: list[Trip]) -> tuple[list[str], set[Path]]:
    """子資料夾的紀錄（地點、地圖設定）用 schema 驗；所有紀錄的 x-ref 要指得到東西。
    回傳（問題, 紀錄有問題的旅程資料夾）。旅程資料夾直下的紀錄檔由 trip_views 驗，這裡不重複報。"""
    problems, bad = [], set()
    for t in trips:
        for path in record_paths(t.path):
            rel = path.relative_to(root).as_posix()
            text = path.read_text(encoding="utf-8")
            invalid = record_problems(rel, text)
            if invalid:
                bad.add(t.path)
                problems += invalid if path.parent != t.path else []
                continue
            refs = ref_problems(root, rel, record_kind(rel), json.loads(text))
            problems += refs
            bad |= {t.path} if refs and path.parent != t.path else set()
    return problems, bad


def stale_plan_places(root: Path, trip: Trip, today: date) -> list[str]:
    """行程（itinerary.json）用到的地點，查詢日期過期了：做決定前要重查。沒排進行程的點不提醒，免得雜訊。"""
    path = trip.path / "itinerary.json"
    try:  # 壞掉、型別不對的紀錄由 check_records 擋，這裡跳過
        days = json.loads(path.read_text(encoding="utf-8"))["days"]
        ids = list(dict.fromkeys(i for d in days for i in [*d.get("places", []), d.get("stay_place")] if isinstance(i, str) and i))
    except (OSError, ValueError, KeyError, TypeError, AttributeError):
        return []
    stale = {}
    for i in ids:
        place = trip.path / "places" / f"{i}.json"
        try:
            old = stale_values("place", json.loads(place.read_text(encoding="utf-8")), today) if place.is_file() else []
        except (OSError, ValueError, TypeError, AttributeError):
            continue
        if old:
            stale[i] = min(d for _, d in old)
    if not stale:
        return []
    more = f" 等 {len(stale)} 個" if len(stale) > 5 else ""
    return [f"{TRIPS}/{trip.slug}/itinerary.json 用到的地點查詢超過 {STALE_DAYS} 天（最舊 {min(stale.values())}）："
            f"{'、'.join(list(stale)[:5])}{more}，做決定前重查 places/ 裡的這幾筆（照 recheck skill）"]


@dataclass
class CheckReport:
    problems: list[str]
    warnings: list[str] = field(default_factory=list)
    pages: dict[str, dict[str, str]] = field(default_factory=dict)
    maps: dict[str, MapPage] = field(default_factory=dict)


def inspect_data(root: Path, today: date) -> CheckReport:
    cfg, problems = config_problems(root)
    report = CheckReport(problems)
    warnings = report.warnings
    if not cfg.format:  # 還沒初始化，其他的問題都是它的後果
        return report
    if not cfg.git_mode:
        warnings.append(f"{CONFIG} 還沒設定 git.mode：問使用者回合結束怎麼保存（main：直接 push 到 main；branch：push 到目前分支；"
                        "local：只 commit），寫進 [git] mode。設定前照 local 做，雲端 session 沒 push 的改動會隨容器消失")
    trips_dir = root / TRIPS
    if trips_dir.is_dir():
        problems += [
            f"{TRIPS}/{d.name}：缺少 README.md（新旅程請用 seasoningway new 建立）"
            for d in sorted(trips_dir.iterdir())
            if d.is_dir() and not (d / "README.md").is_file()
        ]
    missing = missing_dependency()
    if missing:
        problems.append(missing)
        return report
    not_utf8 = [f"{p.relative_to(root).as_posix()}：不是 UTF-8，用 UTF-8 重新存檔" for p in hygiene_files(root) if not is_utf8(p)]
    if not_utf8:  # 後面的檢查都當 UTF-8 讀
        problems.extend(not_utf8)
        return report
    trips = load_trips(root)
    record_issues, bad = check_records(root, trips)
    problems += record_issues
    for t in trips:
        views, trip_problems, trip_warnings = trip_views(t, root, today)
        problems += trip_problems
        warnings += trip_warnings
        for name, text in views.items():
            source = VIEW_SOURCES[name][0] if name in VIEW_SOURCES else "budget.json"
            problems += [f"{p}（改 {source}）" for p in link_problems(root, t.path / name, text)]
        if not trip_problems and t.path not in bad and (t.path / "map/config.json").exists():
            try:
                report.maps[t.slug] = map_build.load_page(t.path)
            except Problem as error:
                problems.extend(str(error).splitlines())
        report.pages[t.slug] = views
    warnings += [w for t in trips for w in stale_plan_places(root, t, today)]
    problems += check_research(root)
    problems += check_pii(root)
    problems += check_conflicts(root)
    states, publication_problems = published_states(root)
    problems += publication_problems
    if cfg.publish == "artifact":
        warnings += unpublished(root, states)
    files = hygiene_files(root)
    problems += check_links(root, files)
    warnings += hygiene_warnings(root, files, today, cfg)
    report.problems = list(dict.fromkeys(problems))
    return report


def run_check(root: Path, today: date) -> tuple[list[str], list[str]]:
    report = inspect_data(root, today)
    return report.problems, report.warnings


def print_issues(problems: list[str], warnings: list[str]) -> None:
    for w in warnings:
        print(f"⚠️ {w}")
    for p in problems:
        print(f"✗ {p}")


def cmd_check(args: argparse.Namespace, root: Path, today: date) -> int:
    problems, warnings = run_check(root, today)
    print_issues(problems, warnings)
    print("check 通過" if not problems else f"check 沒通過：{len(problems)} 個問題")
    return 1 if problems else 0
