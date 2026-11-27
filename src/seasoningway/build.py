"""第 1 階段沿用的視圖產生和比對。"""
from __future__ import annotations

import argparse
import os
import subprocess
import sys
from datetime import date
from pathlib import Path

from .common import INDEX, TRIPS, Problem
from .trips import load_trips
from .views import write_index, write_views


# 子程序的輸出一律當 UTF-8 讀，子程序的 Python 也用 UTF-8 寫：Windows 的預設編碼（例如 cp950）讀不了 ⚠️ 這類字元
UTF8 = {"encoding": "utf-8", "errors": "replace"}


def child_env() -> dict[str, str]:
    return {**os.environ, "PYTHONIOENCODING": "utf-8"}


def generators(root: Path) -> list[tuple[str, list[str], Path, Path]]:
    """紀錄檔以外產生檔案的程式：[(名稱, 指令, 工作目錄, 旅程資料夾)]。
    - 有 map/config.json 的旅程：引擎的地圖頁 seasoningway.tripmap.build
    - 旅程子資料夾裡自己的 build.py
    約定：不帶參數時產生檔案；帶 --check 時只比對、不寫檔，不是最新就印出原因並以非 0 結束。"""
    out = []
    for config in sorted((root / TRIPS).glob("*/map/config.json")):
        trip_dir = config.parent.parent
        out.append((f"seasoningway.tripmap.build {trip_dir.relative_to(root).as_posix()}",
                    [sys.executable, "-m", "seasoningway.tripmap.build", str(trip_dir)], root, trip_dir))
    for script in sorted((root / TRIPS).glob("*/*/build.py")):
        out.append((script.relative_to(root).as_posix(), [sys.executable, str(script)], script.parent, script.parent.parent))
    return out


def check_generated(root: Path, skip: set[Path] = frozenset()) -> list[str]:
    """用 --check 確認 generators 產生的檔案是最新的。skip 是紀錄已經有問題的旅程（問題另外報，不重複）。"""
    problems = []
    for name, argv, cwd, trip_dir in generators(root):
        if trip_dir in skip:
            continue
        try:
            r = subprocess.run([*argv, "--check"], cwd=cwd, capture_output=True, timeout=120, env=child_env(), **UTF8)
        except (OSError, subprocess.TimeoutExpired) as e:
            problems.append(f"{name} --check 無法執行（{e}）")
            continue
        if r.returncode:
            problems.append(f"{name} --check 沒通過：" + ((r.stdout + r.stderr).strip() or f"結束碼 {r.returncode}"))
    return problems


def cmd_build(args: argparse.Namespace, root: Path, today: date) -> int:
    failed = 0
    for t in load_trips(root):
        try:
            print(f"已更新 {TRIPS}/{t.slug}/ 的 {'、'.join(write_views(t, root, today))}")
        except Problem as e:
            failed += 1
            print(f"錯誤：{e}", file=sys.stderr)
    write_index(root)
    print(f"已更新 {INDEX}")
    for name, argv, cwd, _ in generators(root):
        r = subprocess.run(argv, cwd=cwd, capture_output=True, env=child_env(), **UTF8)
        out = (r.stdout + r.stderr).strip()
        if r.returncode:
            failed += 1
            print(f"錯誤：{name} 失敗：{out}", file=sys.stderr)
        else:
            print(f"已執行 {name}" + (f"：{out}" if out else ""))
    return 1 if failed else 0
