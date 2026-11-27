import contextlib
import io
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path

from seasoningway import __version__, cli


def run(*argv: str) -> tuple[int, str, str]:
    out, err = io.StringIO(), io.StringIO()
    with contextlib.redirect_stdout(out), contextlib.redirect_stderr(err):
        try:
            code = cli.main(list(argv))
        except SystemExit as e:
            code = e.code
    return code, out.getvalue(), err.getvalue()


class CliTest(unittest.TestCase):
    def test_version(self):
        code, out, _ = run("--version")
        self.assertEqual(code, 0)
        self.assertIn(__version__, out)

    def test_help_lists_every_command(self):
        code, out, _ = run()
        self.assertEqual(code, 0)
        for name, _, _ in cli.COMMANDS:
            self.assertIn(name, out)

    def test_unimplemented_command_points_to_migration_stage(self):
        code, _, err = run("render", "some-trip")
        self.assertEqual(code, 2)
        self.assertIn("docs/migration.md 第 2 階段", err)

    def test_every_command_has_a_stage(self):
        for name, _, stage in cli.COMMANDS:
            self.assertIn(stage, (1, 2, 3), name)

    def test_module_entry_point(self):
        r = subprocess.run([sys.executable, "-m", "seasoningway", "--version"], capture_output=True, text=True)
        self.assertEqual(r.returncode, 0)
        self.assertIn(__version__, r.stdout)

    def test_commands_on_a_new_data_repo(self):
        root = Path(self.enterContext(tempfile.TemporaryDirectory()))
        (root / "README.md").write_text("# 自訂說明\n", encoding="utf-8")

        def invoke(cwd: Path, *args: str) -> str:
            result = subprocess.run([sys.executable, "-m", "seasoningway", *args], cwd=cwd,
                                    capture_output=True, encoding="utf-8", timeout=30)
            self.assertEqual(result.returncode, 0, result.stdout + result.stderr)
            return result.stdout

        invoke(root, "migrate")
        slug = "2099-02-elsewhere"
        invoke(root, "new", slug, "--title", "範例旅程", "--currency", "TWD", "--travelers", "2")
        trip = root / "trips" / slug
        self.assertIn("範例旅程", (trip / "README.md").read_text(encoding="utf-8"))
        self.assertIn("TWD", invoke(trip / "research", "budget", slug))
        self.assertIn("check 通過", invoke(trip, "check"))
        self.assertIn("check 通過", invoke(trip, "--root", str(root), "check"))
        invoke(trip, "published", f"trips/{slug}", "v1", "--slug", "example", "--include", "budget.md")
        self.assertTrue((trip / "published.json").is_file())
        self.assertEqual((root / "README.md").read_text(encoding="utf-8"), "# 自訂說明\n")
        self.assertFalse((root / "data").exists())


if __name__ == "__main__":
    unittest.main()
