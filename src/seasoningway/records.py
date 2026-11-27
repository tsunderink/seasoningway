"""讀寫紀錄、JSON Schema 與跨檔案參照。"""
from __future__ import annotations

import functools
import json
import re
import tomllib
from datetime import date
from decimal import Decimal
from importlib.resources import files
from pathlib import Path

from .common import CONFIG, DATE_RE, TRIPS, Problem, real_date

SCHEMAS = files("seasoningway").joinpath("schemas")


RECORDS = {"budget.json": "budget", "bookings.json": "bookings", "itinerary.json": "itinerary"}  # 旅程資料夾裡的紀錄檔 → schema
NESTED_RECORDS = {("places", None): "place", ("map", "config.json"): "map"}  # 旅程子資料夾裡的紀錄（None＝資料夾裡所有 .json）
JSONSCHEMA_MISSING = "缺少 jsonschema 套件：執行 pip install seasoningway"


def missing_dependency() -> str | None:
    try:
        import jsonschema  # noqa: F401
        import referencing  # noqa: F401
    except ImportError:
        return JSONSCHEMA_MISSING
    return None


@functools.cache
def schema_doc(file: str) -> dict:
    return json.loads((SCHEMAS / file).read_text(encoding="utf-8"))


# 價格、營業時間、時刻表、匯率多久要重查：schemas/common.schema.json 的 $defs/as_of
STALE_DAYS = schema_doc("common.schema.json")["$defs"]["as_of"]["x-freshness-days"]


def schema_url(name: str) -> str:
    return f"https://raw.githubusercontent.com/tsunderink/seasoningway/main/src/seasoningway/schemas/{name}.schema.json"


def record_kind(rel: str) -> str | None:
    """repo 裡的相對路徑 → 它的 schema 名稱；不是旅程紀錄檔就回傳 None。"""
    parts = rel.split("/")
    if len(parts) >= 3 and parts[0] == TRIPS and parts[-1] == "published.json":
        return "published"
    if len(parts) == 3 and parts[0] == TRIPS:
        return RECORDS.get(parts[2])
    if len(parts) == 4 and parts[0] == "trips" and parts[3].endswith(".json"):
        return NESTED_RECORDS.get((parts[2], parts[3])) or NESTED_RECORDS.get((parts[2], None))
    return None


@functools.cache
def validator(name: str):
    if missing_dependency():
        raise Problem(JSONSCHEMA_MISSING)
    import jsonschema
    import referencing
    from referencing.jsonschema import DRAFT202012

    registry = referencing.Registry().with_resources(
        (p.name, referencing.Resource.from_contents(schema_doc(p.name), default_specification=DRAFT202012))
        for p in SCHEMAS.iterdir() if p.name.endswith(".schema.json")
    )
    return jsonschema.Draft202012Validator(schema_doc(f"{name}.schema.json"), registry=registry,
                                           format_checker=jsonschema.FormatChecker())


def json_path(parts) -> str:
    out = ""
    for p in parts:
        out += f"[{p}]" if isinstance(p, int) else (f".{p}" if out else str(p))
    return out or "（整份）"


DECIMAL_REPR_RE = re.compile(r"Decimal[(]'([^']*)'[)]")


def schema_errors(name: str, data, where: str) -> list[str]:
    """用 schemas/<name>.schema.json 驗 data，回傳「where：欄位：原因」。陣列裡的項目附上它的 id 或名稱。"""
    def key(e) -> list[str]:
        return [f"{p:08d}" if isinstance(p, int) else p for p in e.absolute_path]

    def label(parts: list) -> str:
        if len(parts) >= 2 and isinstance(parts[1], int) and isinstance(data, dict):
            node = data.get(parts[0])
            if isinstance(node, list) and parts[1] < len(node) and isinstance(node[parts[1]], dict):
                name = next((str(node[parts[1]][k]) for k in ("id", "item", "date", "currency") if k in node[parts[1]]), "")
                return f"（{name[:20]}）" if name else ""
        return ""

    def message(e) -> str:
        return DECIMAL_REPR_RE.sub(r"\1", e.message)  # 訊息裡的 Decimal('1.5') 印成 1.5

    return [f"{where}：{json_path(e.absolute_path)}{label(list(e.absolute_path))}：{message(e)}"
            for e in sorted(validator(name).iter_errors(data), key=key)]


def parse_json(text: str, where: str):
    try:
        return json.loads(text, parse_float=Decimal)
    except json.JSONDecodeError as e:
        raise Problem(f"{where}:{e.lineno}：JSON 格式錯誤（{e.msg}）") from None


def dump_json(data) -> str:
    """紀錄檔的固定寫法：2 格縮排、中文不轉義、結尾換行（MIGRATIONS 的轉換寫紀錄時用）。"""
    def number(value):
        if isinstance(value, Decimal):
            return int(value) if value == value.to_integral_value() else float(value)
        raise TypeError(type(value))
    return json.dumps(data, ensure_ascii=False, indent=2, default=number) + "\n"


def unique_problems(items: list[dict], key: str, where: str, field_name: str) -> list[str]:
    seen, out = set(), []
    for i, item in enumerate(items):
        if item[key] in seen:
            out.append(f"{where}：{field_name}[{i}]：{key} {item[key]} 重複")
        seen.add(item[key])
    return out


PHONE_RE = re.compile(r"(?<![\d-])\+?\d{2,4}-\d{2,4}-\d{3,4}(?![\d-])")  # 03-1234-5678、+81-3-…；日期、郵遞區號、番地不算


def place_rules(d: dict, where: str) -> list[str]:
    """地點：id 和檔名一樣；電話只放 contact.phone（公開的店家資料，一個地方）。"""
    out = [] if d["id"] == Path(where).stem else [f"{where}：id {d['id']} 要和檔名（{Path(where).stem}）一樣"]
    rest = json.dumps({k: v for k, v in d.items() if k != "contact"}, ensure_ascii=False, default=str)
    out += [f"{where}：電話 {m} 要放在 contact.phone，其他欄位不寫" for m in PHONE_RE.findall(rest)]
    return out


RECORD_RULES = {  # schema 管不到的規則
    "place": place_rules,
    "map": lambda d, w: unique_problems(d["categories"], "id", w, "categories")
                       + unique_problems(d.get("ratings", []), "site", w, "ratings"),
    "budget": lambda d, w: unique_problems(d["rates"], "currency", w, "rates"),
    "bookings": lambda d, w: unique_problems(d["items"], "id", w, "items"),
    "itinerary": lambda d, w: unique_problems(d["days"], "date", w, "days") + (
        [] if [x["date"] for x in d["days"]] == sorted(x["date"] for x in d["days"]) else [f"{w}：days 要依日期排"]),
}


def record_problems(rel: str, text: str) -> list[str]:
    """驗 repo 裡的一個檔（rel 是相對路徑）：旅程的紀錄檔、seasoningway.toml、旅程 README 開頭的設定。
    寫入前的 hook 傳將要寫進去的內容，check 傳磁碟上的內容。不歸 schema 管的檔回傳 []。"""
    parts = rel.split("/")
    try:
        if rel == CONFIG:
            try:
                data = tomllib.loads(text)
            except tomllib.TOMLDecodeError as e:
                return [f"{CONFIG}：格式錯誤（{e}）"]
            return schema_errors("config", data, CONFIG)
        if len(parts) == 3 and parts[0] == "trips" and parts[2] == "README.md":
            meta = parse_front_matter(text)
            return schema_errors("trip", {k: v for k, v in meta.items() if v}, f"{rel} 開頭") if meta else []
        name = record_kind(rel)
        if name is None:
            return []
        data = parse_json(text, rel)
        errors = schema_errors(name, data, rel)
        return errors or (RECORD_RULES[name](data, rel) if name in RECORD_RULES else [])
    except Problem as e:
        return str(e).splitlines()


# schema 裡的自訂標註（JSON Schema 不認得 x- 開頭的關鍵字，驗證時略過，這裡另外查）：
# - x-ref：值是同一趟旅程裡另一筆資料的 id 或路徑，REF_KINDS 說要去哪裡找
# - x-freshness-days：查詢日期，超過這麼多天拿來做決定前要重查


def annotated(name: str, data) -> list[tuple[tuple, dict, object]]:
    """沿著 schemas/<name>.schema.json 走 data，回傳有 x- 標註的值：[(路徑, {標註}, 值)]。
    會跟著 $ref（同檔、跨檔）、properties、patternProperties、additionalProperties、items、prefixItems、allOf/anyOf/oneOf、then/else。"""
    found: dict[tuple, tuple[dict, object]] = {}

    def resolve(ref: str, base: str) -> tuple[dict, str]:
        file, _, pointer = ref.partition("#")
        file = file or base
        node = schema_doc(file)
        for part in filter(None, pointer.split("/")):
            node = node[part.replace("~1", "/").replace("~0", "~")]
        return node, file

    def walk(s, base: str, value, path: tuple, notes: dict) -> None:
        if not isinstance(s, dict):
            return
        notes = {**notes, **{k: v for k, v in s.items() if k.startswith("x-")}}
        if "$ref" in s:
            walk(*resolve(s["$ref"], base), value, path, notes)
        elif notes and not isinstance(value, (dict, list)):
            found[path] = ({**found.get(path, ({}, None))[0], **notes}, value)
        for key in ("allOf", "anyOf", "oneOf"):
            for sub in s.get(key, []):
                walk(sub, base, value, path, notes)
        for key in ("then", "else"):
            walk(s.get(key), base, value, path, notes)
        if isinstance(value, dict):
            for k, v in value.items():
                subs = [s["properties"][k]] if k in s.get("properties", {}) else \
                    [sub for pattern, sub in s.get("patternProperties", {}).items() if re.search(pattern, k)]
                if not subs and isinstance(s.get("additionalProperties"), dict):
                    subs = [s["additionalProperties"]]
                for sub in subs:
                    walk(sub, base, v, path + (k,), {})
        elif isinstance(value, list):
            prefix = s.get("prefixItems", [])
            for i, v in enumerate(value):
                walk(prefix[i] if i < len(prefix) else s.get("items"), base, v, path + (i,), {})

    walk(schema_doc(f"{name}.schema.json"), f"{name}.schema.json", data, (), {})
    return [(path, notes, value) for path, (notes, value) in found.items()]


def config_categories(trip_dir: Path) -> set[str] | None:
    path = trip_dir / "map" / "config.json"
    try:
        return {c["id"] for c in json.loads(path.read_text(encoding="utf-8"))["categories"]}
    except (OSError, ValueError, KeyError, TypeError):
        return None  # 沒有地圖設定（或還有格式問題，另外報）就不查


REF_KINDS = {  # x-ref 的種類 → (同一趟旅程裡有哪些合法的值；None＝這趟沒有這種資料、不查, 找不到時的說明)
    "place": (lambda d: {p.stem for p in (d / "places").glob("*.json")}, "地點 {v} 不存在（places/{v}.json）"),
    "category": (config_categories, "類別 {v} 沒有定義在 map/config.json 的 categories"),
    "file": (None, "連到的 {v} 不存在"),  # 相對路徑（網址不查）
}


def ref_problems(root: Path, rel: str, name: str, data) -> list[str]:
    """紀錄檔裡 x-ref 標的值要指得到東西（地點、類別、研究筆記）。"""
    trip_dir = root / TRIPS / rel.split("/")[1]
    known: dict[str, set[str] | None] = {}
    out = []
    for path, notes, value in annotated(name, data):
        kind = notes.get("x-ref")
        if kind not in REF_KINDS or not isinstance(value, str):
            continue
        find, message = REF_KINDS[kind]
        if find is None:
            ok = re.match(r"https?://", value) or (trip_dir / value.split("#")[0]).is_file()
        else:
            if kind not in known:
                known[kind] = find(trip_dir)
            ok = known[kind] is None or value in known[kind]
        if not ok:
            out.append(f"{rel}：{json_path(path)}：" + message.format(v=value))
    return out


def stale_values(name: str, data, today: date) -> list[tuple[tuple, str]]:
    """x-freshness-days 標的日期超過期限的：[(路徑, 日期)]。"""
    out = []
    for path, notes, value in annotated(name, data):
        days = notes.get("x-freshness-days")
        when = real_date(value) if isinstance(value, str) and DATE_RE.match(value) else None
        if days and when and (today - when).days > days:  # 不存在的日期由 schema 驗證擋
            out.append((path, value))
    return out


def read_record(root: Path, path: Path) -> dict:
    rel = path.relative_to(root).as_posix()
    if not path.is_file():
        raise Problem(f"找不到 {rel}（舊格式的旅程先執行 seasoningway migrate）")
    text = path.read_text(encoding="utf-8")
    problems = record_problems(rel, text)
    if problems:
        raise Problem("\n".join(problems))
    return parse_json(text, rel)


def parse_front_matter(text: str) -> dict[str, str]:
    lines = text.splitlines()
    if not lines or lines[0].strip() != "---":
        return {}
    meta: dict[str, str] = {}
    for line in lines[1:]:
        if line.strip() == "---":
            return meta
        key, sep, value = line.partition(":")
        if sep and key.strip():
            meta[key.strip()] = value.strip()
    return {}


def record_paths(trip_dir: Path) -> list[Path]:
    """一趟旅程所有的紀錄檔（RECORDS 和 NESTED_RECORDS），存在的才列。"""
    paths = [trip_dir / name for name in RECORDS]
    for (folder, name) in NESTED_RECORDS:
        paths += sorted((trip_dir / folder).glob("*.json")) if name is None else [trip_dir / folder / name]
    return [p for p in paths if p.is_file()]
