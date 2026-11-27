import contextlib
import io
import subprocess
import sys
import unittest

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


if __name__ == "__main__":
    unittest.main()
