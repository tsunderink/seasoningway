"""資料 repo 的查找與設定。"""
from __future__ import annotations

import tomllib
from dataclasses import dataclass
from pathlib import Path

from . import __version__
from .common import CONFIG, Problem
from .records import missing_dependency, record_problems


FORMAT = 4  # 資料格式版本：改了資料檔的格式就加 1，並在 MIGRATIONS 加一步
ASSISTANTS = ("Claude", "Codex")  # 規劃助理：只研究和整理，不訂東西
FIXED_ACTORS = ("使用者", *ASSISTANTS)  # 「下一步」開頭一律可以寫的；其他角色代稱寫在 seasoningway.toml 的 people.roles


@dataclass
class Config:
    engine: str = ""
    format: int = 0  # 0＝沒有 seasoningway.toml（還沒初始化）
    git_mode: str = ""  # ""＝還沒設定：預設照 local 保存，check 提醒設定
    default_currency: str = ""
    roles: tuple[str, ...] = ()
    publish: str = "artifact"

    @property
    def actors(self) -> tuple[str, ...]:
        return FIXED_ACTORS + self.roles


def load_config(root: Path, validate: bool = True) -> Config:
    """讀 seasoningway.toml，欄位由 schemas/config.schema.json 定義。format 和這版引擎不同時不驗：schema 是這一版的，
    舊版的設定先 migrate、新版的先更新引擎（check 會提示）。validate=False 給 hooks 用：其他欄位不合 schema 時
    照樣讀得到 git.mode。沒裝 jsonschema 時也照樣讀，check 會另外報缺套件。"""
    path = root / CONFIG
    if not path.is_file():
        return Config()
    text = path.read_text(encoding="utf-8")
    try:
        data = tomllib.loads(text)
    except tomllib.TOMLDecodeError as e:
        raise Problem(f"{CONFIG}：格式錯誤（{e}）") from None
    version = data.get("format", 0)
    version = version if type(version) is int else None  # 不是整數時由 schema 報
    if validate and version in (FORMAT, None) and not missing_dependency():
        errors = record_problems(CONFIG, text)
        if errors:
            raise Problem("\n".join(errors))

    def value(table: str, key: str, default):  # 沒驗過時型別不對就用預設值
        section = data.get(table)
        found = section.get(key, default) if isinstance(section, dict) else default
        return found if isinstance(found, type(default)) else default
    return Config(
        engine=data["engine"] if isinstance(data.get("engine"), str) else "",
        format=version or 0,
        git_mode=value("git", "mode", ""),
        default_currency=value("budget", "default_currency", ""),
        roles=tuple(r for r in value("people", "roles", []) if isinstance(r, str)),
        publish=value("publish", "target", "artifact"),
    )


def config_problems(root: Path) -> tuple[Config, list[str]]:
    """讀設定；有問題時回傳讀得到的值（讀不了就用預設值）和問題清單，讓 check 照常跑完其他項目。"""
    try:
        cfg = load_config(root)
    except Problem as e:
        try:
            cfg = load_config(root, validate=False)
        except Problem:
            cfg = Config(format=FORMAT)
        return cfg, str(e).splitlines()
    if not cfg.format:
        what = f"{CONFIG} 還沒初始化" if (root / CONFIG).is_file() else f"還沒有 {CONFIG}"
        return cfg, [f"{what}：這個目錄需要已建立的資料 repo"]
    if cfg.format < FORMAT:
        return cfg, [f"資料格式是第 {cfg.format} 版，這版引擎要第 {FORMAT} 版：執行 seasoningway migrate"]
    if cfg.format > FORMAT:
        return cfg, [f"{CONFIG} 是第 {cfg.format} 版，比這版引擎（第 {FORMAT} 版）新：更新引擎"]
    return cfg, []


def find_root(start: Path) -> Path | None:
    start = start.resolve()
    return next((folder for folder in (start, *start.parents) if (folder / CONFIG).is_file()), None)


def engine_warning(cfg: Config) -> str | None:
    if cfg.engine and cfg.engine.split(".")[:2] != __version__.split(".")[:2]:
        return f"{CONFIG} 指定引擎 {cfg.engine}，目前安裝的是 {__version__}：請使用指定版本或升級資料 repo"
    return None
