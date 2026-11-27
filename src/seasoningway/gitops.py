"""讀取 Git 追蹤範圍、產物標記與新增的行。"""
import re
import subprocess
from pathlib import Path


def generated_files(root: Path, names: list[str]) -> set[str]:
    """.gitattributes 標了 linguist-generated 的檔案。它們由來源檔產生、來源檔都另外掃過，所以不重掃。"""
    try:
        out = subprocess.run(
            ["git", "check-attr", "-z", "--stdin", "linguist-generated"],
            cwd=root, input="\0".join(names).encode("utf-8"), capture_output=True, check=True,
        ).stdout.decode("utf-8").split("\0")
    except (OSError, subprocess.CalledProcessError):
        return set()
    return {out[i] for i in range(0, len(out) - 2, 3) if out[i + 2] == "set"}


def files_to_scan(root: Path) -> list[Path]:
    try:
        out = subprocess.run(
            ["git", "ls-files", "-z", "--cached", "--others", "--exclude-standard"],
            cwd=root, capture_output=True, check=True,
        ).stdout.decode("utf-8")
        names = [n for n in out.split("\0") if n]
    except (OSError, subprocess.CalledProcessError):
        names = [p.relative_to(root).as_posix() for p in root.rglob("*") if p.is_file() and ".git" not in p.parts]
    names = sorted(set(names))
    skip = generated_files(root, names)
    return [root / n for n in names if n not in skip and (root / n).is_file()]


HUNK_RE = re.compile(r"^@@ -\d+(?:,\d+)? \+(\d+)")
GIT_ESCAPES = {"a": 7, "b": 8, "t": 9, "n": 10, "v": 11, "f": 12, "r": 13, '"': 34, "\\": 92}


def diff_path(header: str) -> str | None:
    """diff 的「+++ b/路徑」標頭裡的路徑。含空白的路徑後面多一個 tab；含引號、反斜線、控制字元的
    整個加上引號、用 C 的跳脫寫法。刪掉的檔（+++ /dev/null）回傳 None。"""
    name = header[4:].removesuffix("\t")
    if name.startswith('"') and name.endswith('"'):
        out, body, i = bytearray(), name[1:-1], 0
        while i < len(body):
            if body[i] == "\\" and body[i + 1:i + 2] in GIT_ESCAPES:
                out.append(GIT_ESCAPES[body[i + 1]])
                i += 2
            elif body[i] == "\\":  # 八進位的位元組，例如 \303
                out.append(int(body[i + 1:i + 4], 8))
                i += 4
            else:
                out += body[i].encode("utf-8")
                i += 1
        name = out.decode("utf-8", "replace")
    return name[2:] if name.startswith("b/") else None


def added_lines(root: Path, sha: str) -> dict[str, list[tuple[int, str]]]:
    """commit 新增的文字，按路徑分組，附上行號。"""
    diff = subprocess.run(["git", "-c", "core.quotePath=false", "diff-tree", "-r", "-p", "-U0", "--no-renames",
                           "--no-commit-id", "--no-color", sha], cwd=root, capture_output=True).stdout.decode("utf-8", "replace")
    added: dict[str, list[tuple[int, str]]] = {}
    path, header, n = None, False, 0
    for line in diff.split("\n"):
        if line.startswith("diff --git "):
            path, header = None, True
        elif header and line.startswith("+++ "):
            path = diff_path(line)
        elif m := HUNK_RE.match(line):
            header, n = False, int(m[1])
        elif not header and path and line.startswith("+"):
            added.setdefault(path, []).append((n, line[1:]))
            n += 1
    return added
