import contextlib
import io
import os
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path

from seasoningway import __version__, cli
from seasoningway.guides import TOPICS


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
        for command in cli.COMMANDS:
            self.assertIn(command.name, out)

    def test_guide_lists_available_topics_and_rejects_unknown(self):
        code, out, _ = run("guide", "--help")
        self.assertEqual(code, 0)
        for name in TOPICS:
            self.assertIn(name, out)
        code, _, err = run("guide", "example")
        self.assertEqual(code, 2)
        self.assertIn("example", err)

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

        invoke(root, "init")
        invoke(root, "migrate")
        slug = "2099-02-elsewhere"
        invoke(root, "new", slug, "--title", "範例旅程", "--currency", "TWD", "--travelers", "2")
        trip = root / "trips" / slug
        self.assertIn("範例旅程", (trip / "README.md").read_text(encoding="utf-8"))
        self.assertIn("TWD", invoke(trip / "research", "budget", slug))
        self.assertIn("check 通過", invoke(trip, "check"))
        self.assertIn("check 通過", invoke(trip, "--root", str(root), "check"))
        self.assertFalse((trip / "budget.md").exists())
        self.assertFalse((root / "out").exists())
        invoke(trip, "render")
        self.assertTrue((root / "out" / slug / "budget.md").is_file())
        self.assertTrue((root / "out/index.md").is_file())
        invoke(trip, "published", slug, "v1", "--slug", "example")
        self.assertTrue((trip / "published.json").is_file())
        self.assertEqual((root / "README.md").read_text(encoding="utf-8"), "# 自訂說明\n")
        self.assertFalse((root / "data").exists())

    def test_commands_declare_their_root(self):
        self.assertEqual(len(cli.BY_NAME), len(cli.COMMANDS))
        self.assertEqual(cli.BY_NAME["init"].needs, cli.Needs.HERE)
        self.assertEqual({c.name for c in cli.COMMANDS if c.needs is cli.Needs.NOTHING}, {"guide", "hook"})
        self.assertEqual({c.name for c in cli.COMMANDS if c.needs is cli.Needs.CURRENT},
                         {"new", "budget", "published", "fetch"})

    def test_root_resolution_follows_the_declaration(self):
        empty = Path(self.enterContext(tempfile.TemporaryDirectory()))
        cwd = Path.cwd()
        self.addCleanup(os.chdir, cwd)
        os.chdir(empty)
        code, _, err = run("check")
        self.assertEqual(code, 1)
        self.assertIn("找不到 seasoningway.toml", err)
        code, _, err = run("migrate", "--from-template")
        self.assertEqual(code, 1)
        self.assertIn("data/trips.toml", err)
        code, out, _ = run("guide", "decide")
        self.assertEqual(code, 0)
        self.assertTrue(out)

        (empty / "seasoningway.toml").write_text("format = 3\n", encoding="utf-8")
        nested = empty / "trips"
        nested.mkdir()
        os.chdir(nested)
        code, _, err = run("budget", "2099-01-example")
        self.assertEqual(code, 1)
        self.assertIn("第 3 版：請先執行 seasoningway migrate", err)
        self.assertEqual(cli.resolve_root(cli.BY_NAME["check"], None, None), empty.resolve())
        self.assertEqual(cli.resolve_root(cli.BY_NAME["init"], None, None), nested.resolve())
        self.assertIsNone(cli.resolve_root(cli.BY_NAME["hook"], None, None))


if __name__ == "__main__":
    unittest.main()
