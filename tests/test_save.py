"""用本機 Git repo 驗證保存與同步。"""
import contextlib
import io
import subprocess
import tempfile
import tomllib
import unittest
from datetime import date
from pathlib import Path
from unittest import mock

from fixture_project import seed_project
from seasoningway import __version__, cli, gitops
from seasoningway.config import FORMAT

TODAY = date(2098, 11, 1)
SLUG = "2099-01-example-city"


class SaveTest(unittest.TestCase):
    def setUp(self):
        self.directory = Path(self.enterContext(tempfile.TemporaryDirectory())).resolve()
        self.root = self.directory / "data-repo"
        self.root.mkdir()
        self.origin = self.directory / "origin.git"
        self.peer = self.directory / "peer"
        seed_project(self.root)
        self.ok("new", SLUG, "--currency", "TWD")
        self.git(self.root, "init", "-b", "main")
        self.identity(self.root)
        self.git(self.root, "add", ".")
        self.git(self.root, "commit", "-m", "初始資料")

    def identity(self, root):
        self.git(root, "config", "user.name", "Test Agent")
        self.git(root, "config", "user.email", "test@example.com")
        self.git(root, "config", "commit.gpgsign", "false")

    def git(self, root, *args):
        result = gitops.run_git(root, *args)
        self.assertEqual(result.returncode, 0, result.stderr + result.stdout)
        return result.stdout.strip()

    def invoke(self, *args):
        out, err = io.StringIO(), io.StringIO()
        with contextlib.redirect_stdout(out), contextlib.redirect_stderr(err):
            code = cli.main(list(args), root=self.root, today=TODAY)
        return code, out.getvalue() + err.getvalue()

    def ok(self, *args):
        status, output = self.invoke(*args)
        self.assertEqual(status, 0, output)
        return output

    def failure(self, *args):
        status, output = self.invoke(*args)
        self.assertEqual(status, 1, output)
        return output

    def write(self, relative, content, root=None):
        path = (root or self.root) / relative
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(content, encoding="utf-8", newline="\n")

    def edit_config(self, original, updated):
        path = self.root / "seasoningway.toml"
        text = path.read_text(encoding="utf-8")
        self.assertIn(original, text)
        self.write("seasoningway.toml", text.replace(original, updated))

    def remote(self, mode):
        self.edit_config('# mode = "main"', f'mode = "{mode}"')
        self.git(self.root, "commit", "-am", "設定保存方式")
        self.git(self.directory, "init", "--bare", "-b", "main", str(self.origin))
        self.git(self.root, "remote", "add", "origin", str(self.origin))
        self.git(self.root, "push", "-u", "origin", "main")
        return self.git(self.root, "rev-parse", "HEAD")

    def clone_peer(self):
        self.git(self.directory, "clone", str(self.origin), str(self.peer))
        self.identity(self.peer)

    def peer_save(self, relative, content):
        self.write(relative, content, root=self.peer)
        self.git(self.peer, "add", ".")
        self.git(self.peer, "commit", "-m", "更新遠端資料")
        self.git(self.peer, "push", "origin", "main")
        return self.git(self.peer, "rev-parse", "HEAD")

    def test_invalid_data_blocks_commit_and_save_check_reports_worktree(self):
        head = self.git(self.root, "rev-parse", "HEAD")
        self.write(f"trips/{SLUG}/budget.json", "{")
        self.assertIn("JSON 格式錯誤", self.failure("save"))
        message = self.failure("save", "--check")
        self.assertIn("JSON 格式錯誤", message)
        self.assertIn("有未保存的改動或新檔案", message)
        self.assertEqual(self.git(self.root, "rev-parse", "HEAD"), head)
        self.assertEqual(self.git(self.root, "diff", "--cached", "--name-only"), "")

    def test_untracked_and_staged_files_outside_data_are_rejected(self):
        self.write("misc.md", "# 額外檔案\n")
        self.write("tmp.md", "# 已暫存的額外檔案\n")
        self.git(self.root, "add", "--", "tmp.md")
        message = self.failure("save")
        self.assertIn("資料路徑外有新檔", message)
        self.assertIn("misc.md", message)
        self.assertIn("tmp.md", message)
        self.assertEqual(self.git(self.root, "diff", "--cached", "--name-only"), "tmp.md")

    def test_tracked_output_is_rejected(self):
        self.write("out/index.md", "# 產物\n")
        self.git(self.root, "add", "-f", "--", "out/index.md")
        self.assertIn("out/ 有已追蹤的產物", self.failure("save"))
        self.assertTrue((self.root / "out/index.md").is_file())

    def test_local_saves_changes_and_ignores_render_outputs(self):
        self.edit_config('# mode = "main"', 'mode = "local"')
        self.write(f"trips/{SLUG}/note.md", "# 範例旅程的筆記\n")
        self.write("wishlist.md", "# 想去的地方\n\n- 範例市\n")
        self.ok("render")
        self.ok("save", "-m", "整理範例資料")
        self.ok("save", "--check")
        subject = self.git(self.root, "log", "-1", "--format=%B")
        self.assertIn(SLUG, subject)
        self.assertIn("旅程檔案", subject)
        self.assertIn("整理範例資料", subject)
        self.assertNotIn("out/", self.git(self.root, "ls-files"))

    def test_main_pulls_and_pushes_new_commit(self):
        initial = self.remote("main")
        self.clone_peer()
        remote = self.peer_save("profile/remote.md", "# 遠端新增\n")
        self.write("profile/local.md", "# 本地新增\n")
        self.ok("save")
        head = self.git(self.root, "rev-parse", "HEAD")
        self.assertNotEqual(initial, head)
        self.assertEqual(self.git(self.root, "rev-parse", "HEAD^"), remote)
        self.assertEqual(self.git(self.origin, "rev-parse", "main"), head)
        self.ok("save", "--check")

    def test_branch_pushes_to_configured_dest_even_with_different_name(self):
        initial = self.remote("branch")
        self.git(self.root, "switch", "-c", "working")
        self.git(self.root, "push", "-u", "origin", "HEAD:refs/heads/review")
        self.write("profile/local.md", "# 分支資料\n")
        self.ok("save")
        self.assertEqual(self.git(self.origin, "rev-parse", "main"), initial)
        self.assertEqual(self.git(self.origin, "rev-parse", "review"), self.git(self.root, "rev-parse", "HEAD"))
        self.ok("save", "--check")

    def test_main_keeps_unpushed_merge(self):
        self.remote("main")
        self.clone_peer()
        self.peer_save("profile/remote.md", "# 遠端新增\n")
        self.git(self.root, "switch", "-c", "side")
        self.write("profile/side.md", "# 支線新增\n")
        self.git(self.root, "add", ".")
        self.git(self.root, "commit", "-m", "支線資料")
        self.git(self.root, "switch", "main")
        self.git(self.root, "merge", "--no-ff", "--no-edit", "side")
        merge = self.git(self.root, "rev-parse", "HEAD")
        self.ok("save")
        self.git(self.origin, "merge-base", "--is-ancestor", merge, "main")
        self.assertEqual(len(self.git(self.origin, "rev-list", "--merges", "main").splitlines()), 2)

    def test_rebase_conflict_can_resume_after_resolution(self):
        self.remote("main")
        self.clone_peer()
        relative = "profile/preferences.md"
        remote = self.peer_save(relative, "# 遠端偏好\n")
        self.write(relative, "# 本地偏好\n")
        message = self.failure("save")
        self.assertIn("rebase 尚未完成", message)
        self.assertIn(relative, message)
        self.assertEqual(self.git(self.origin, "rev-parse", "main"), remote)
        self.assertIn("合併衝突", self.failure("save", "--check"))
        self.write(relative, "# 整合兩邊的偏好\n")
        self.ok("save")
        self.ok("save", "--check")
        self.assertEqual(self.git(self.origin, "show", f"main:{relative}"), "# 整合兩邊的偏好")
        self.assertFalse(gitops.git_path(self.root, "seasoningway-sync").exists())

    def test_push_retries_network_errors_and_leaves_commit_for_next_save(self):
        initial = self.remote("main")
        self.write("profile/local.md", "# 連線中斷時先保存\n")
        real = gitops.run_git

        def failing_push(root, *args):
            if args[0] == "push":
                return subprocess.CompletedProcess(args, 1, "", "fatal: Could not resolve host: example.invalid")
            return real(root, *args)

        with mock.patch.object(gitops, "run_git", side_effect=failing_push) as git, mock.patch.object(gitops, "time") as clock:
            # 只換掉 gitops 的 time：subprocess 等子程序結束時也會呼叫 time.sleep，全域替換會把那些也算進來。
            self.assertIn("Could not resolve host", self.failure("save"))
        self.assertEqual([call.args[0] for call in clock.sleep.call_args_list], [1, 2, 4, 8])
        self.assertEqual(sum(call.args[1] == "push" for call in git.call_args_list), 5)
        self.assertIn("還沒推到 origin/main", self.failure("save", "--check"))
        self.assertEqual(self.git(self.origin, "rev-parse", "main"), initial)
        self.ok("save")
        self.ok("save", "--check")

    def test_version_mismatch_warns_in_status_and_blocks_save(self):
        self.edit_config(f'engine = "{__version__}"', 'engine = "8.1.0"')
        self.assertIn("指定引擎 8.1.0", self.ok("status"))
        self.assertIn("指定引擎 8.1.0", self.failure("save"))
        self.assertIn("指定引擎 8.1.0", self.failure("save", "--check"))
        self.assertEqual(self.git(self.root, "diff", "--cached", "--name-only"), "")

    def test_status_fast_forwards_a_clean_worktree_and_enables_hooks(self):
        initial = self.remote("main")
        self.clone_peer()
        remote = self.peer_save("profile/remote.md", "# 最新資料\n")
        (self.root / ".githooks").mkdir()
        self.write("profile/local.md", "# 未保存的資料\n")
        self.assertIn("工作區有未保存的改動", self.ok("status"))
        self.assertEqual(self.git(self.root, "rev-parse", "HEAD"), initial)
        (self.root / "profile/local.md").unlink()
        output = self.ok("status")
        self.assertIn(SLUG, output)
        self.assertEqual(self.git(self.root, "rev-parse", "HEAD"), remote)
        self.assertEqual(self.git(self.root, "config", "core.hooksPath"), ".githooks")

    def pin_old_engine(self):
        self.edit_config(f'engine = "{__version__}"', 'engine = "0.0.1"')
        self.edit_config(f"format = {FORMAT}", f"format = {FORMAT - 1}")
        self.git(self.root, "commit", "-am", "指定舊引擎")
        self.git(self.root, "push", "origin", "main")

    def test_status_auto_upgrades_to_newer_released_engine_and_saves(self):
        self.remote("main")
        self.pin_old_engine()
        with mock.patch("seasoningway.status.editable_install", return_value=False):
            output = self.ok("status")
        self.assertIn(f"已自動把資料 repo 從引擎 0.0.1 升級到 {__version__} 並保存", output)
        self.assertNotIn("指定引擎", output)
        config = tomllib.loads((self.root / "seasoningway.toml").read_text(encoding="utf-8"))
        self.assertEqual((config["engine"], config["format"]), (__version__, FORMAT))
        self.assertIn(f"uses: tsunderink/seasoningway@v{__version__}",
                      (self.root / ".github/workflows/check.yml").read_text(encoding="utf-8"))
        self.assertEqual(self.git(self.root, "status", "--porcelain"), "")
        self.assertEqual(self.git(self.origin, "rev-parse", "main"), self.git(self.root, "rev-parse", "HEAD"))
        self.assertIn(f"升級引擎到 {__version__}", self.git(self.root, "log", "-1", "--format=%B"))
        with mock.patch("seasoningway.status.editable_install", return_value=False):
            self.assertNotIn("自動", self.ok("status"))  # 已是最新，不再升級

    def test_status_skips_auto_upgrade_for_dev_install_or_dirty_worktree(self):
        self.remote("main")
        self.pin_old_engine()
        head = self.git(self.root, "rev-parse", "HEAD")
        with mock.patch("seasoningway.status.editable_install", return_value=True):
            self.assertIn("指定引擎 0.0.1", self.failure("status"))  # 格式還是舊的
        self.write("profile/local.md", "# 未保存的資料\n")
        with mock.patch("seasoningway.status.editable_install", return_value=False):
            output = self.failure("status")
        self.assertIn(f"還沒自動升級到引擎 {__version__}：工作區有未保存的改動", output)
        self.assertEqual(self.git(self.root, "rev-parse", "HEAD"), head)
        self.assertIn('engine = "0.0.1"', (self.root / "seasoningway.toml").read_text(encoding="utf-8"))

    def test_save_check_keeps_index_and_refs(self):
        self.remote("main")
        self.write("profile/local.md", "# 待保存\n")
        before = self.git(self.root, "rev-parse", "HEAD")
        index = gitops.git_path(self.root, "index")
        contents = index.read_bytes()
        self.failure("save", "--check")
        self.assertEqual(self.git(self.root, "rev-parse", "HEAD"), before)
        self.assertEqual(index.read_bytes(), contents)


if __name__ == "__main__":
    unittest.main()
