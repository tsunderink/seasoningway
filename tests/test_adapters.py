import json
import os
import shutil
import subprocess
import sys
import tempfile
import tomllib
import unittest
from pathlib import Path

from seasoningway import __version__
from seasoningway.adapters import GENERATED, adapter_files
from seasoningway.config import FORMAT
from seasoningway.guides import TOPICS, guide
from seasoningway.trips import load_trips
from test_cli import run

DEMO = Path(__file__).parent / "fixtures/demo"
SYNTHETIC_PII = "護照號碼" + " AB" + "0" * 8


class DataRepoTest(unittest.TestCase):
    def setUp(self):
        folder = Path(self.enterContext(tempfile.TemporaryDirectory()))
        self.root = folder / "旅行 範例"
        self.root.mkdir()
        self.env = {**os.environ, "PATH": os.pathsep.join((str(Path(sys.executable).parent), os.environ["PATH"])),
                    "GIT_AUTHOR_NAME": "Example Test", "GIT_AUTHOR_EMAIL": "example@example.invalid",
                    "GIT_COMMITTER_NAME": "Example Test", "GIT_COMMITTER_EMAIL": "example@example.invalid"}
        self.env.pop("CLAUDE_PROJECT_DIR", None)

    def process(self, args, *, input=None, cwd=None):
        return subprocess.run(args, cwd=cwd or self.root, input=input, capture_output=True,
                              encoding="utf-8", env=self.env, timeout=30)

    def ok(self, result):
        self.assertEqual(result.returncode, 0, result.stdout + result.stderr)
        return result.stdout

    def cli(self, *args, **kwargs):
        return self.process([sys.executable, "-m", "seasoningway", *args], **kwargs)

    def git(self, *args, cwd=None):
        return self.process(["git", *args], cwd=cwd)

    def write(self, name, text):
        path = self.root / name
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(text, encoding="utf-8", newline="\n")
        return path

    def initialize(self):
        self.ok(self.cli("init"))
        config = self.root / "seasoningway.toml"
        config.write_text(config.read_text(encoding="utf-8").replace('# mode = "main"', 'mode = "local"'),
                          encoding="utf-8")

    def hook(self, tool, event, payload, command=None):
        args = command.split() if command else ("seasoningway", "hook", tool, event)
        executable = shutil.which(args[0], path=self.env["PATH"])
        self.assertIsNotNone(executable)
        return self.process([executable, *args[1:]], input=json.dumps(payload, ensure_ascii=False), cwd=self.root.parent)


class AdapterTest(DataRepoTest):
    def test_all_guides_work_outside_data_repo(self):
        for name in TOPICS:
            with self.subTest(name=name):
                self.assertEqual(self.ok(self.cli("guide", name)), guide(name))
        self.assertIn("蒐集一個地區", guide("scout"))

    def test_init_generates_all_adapters_and_first_save(self):
        self.initialize()
        config = tomllib.loads((self.root / "seasoningway.toml").read_text(encoding="utf-8"))
        self.assertEqual((config["engine"], config["format"]), (__version__, FORMAT))
        self.assertTrue((self.root / "trips").is_dir())
        self.assertEqual(self.ok(self.git("config", "core.hooksPath")).strip(), ".githooks")
        self.assertEqual(self.ok(self.git("branch", "--show-current")).strip(), "main")
        for name in adapter_files(__version__):
            path = self.root / name
            with self.subTest(name=name):
                self.assertIn(GENERATED, path.read_text(encoding="utf-8"))
                self.assertNotIn(b"\r\n", path.read_bytes())
        for folder in (".claude/skills", ".agents/skills"):
            self.assertEqual({path.name for path in (self.root / folder).iterdir()}, set(TOPICS))
            for name in TOPICS:
                self.assertIn(f"seasoningway guide {name}",
                              (self.root / folder / name / "SKILL.md").read_text(encoding="utf-8"))
        for agent, topic in (("spot-scout", "scout"), ("fact-checker", "recheck")):
            claude = (self.root / f".claude/agents/{agent}.md").read_text(encoding="utf-8")
            codex = tomllib.loads((self.root / f".codex/agents/{agent}.toml").read_text(encoding="utf-8"))
            self.assertNotIn("tools: Bash", claude)
            self.assertFalse(codex["features"]["shell_tool"])
            self.assertIn(guide(topic).rstrip(), claude)
            self.assertIn(guide(topic).rstrip(), codex["developer_instructions"])
        self.write("out/ignore.md", "產物\n")
        self.ok(self.cli("save"))
        self.assertIn("save --check 通過", self.ok(self.cli("save", "--check")))
        tracked = self.ok(self.git("ls-files"))
        self.assertIn("wishlist.md", tracked)
        self.assertIn(".githooks/pre-push", tracked)
        self.assertNotIn("out/ignore.md", tracked)
        for entry in self.ok(self.git("ls-files", "--stage", "--", ".githooks")).splitlines():
            self.assertTrue(entry.startswith("100755 "), entry)
        self.ok(self.cli("init"))
        self.assertEqual(self.ok(self.git("status", "--porcelain")), "")

    def test_upgrade_updates_versions_and_migrates_demo_preserving_profile(self):
        self.initialize()
        shutil.copytree(DEMO / "trips", self.root / "trips", dirs_exist_ok=True)
        trip = load_trips(self.root)[0].path
        rel = (trip / "itinerary.json").relative_to(self.root).as_posix()
        itinerary = json.loads((self.root / rel).read_text(encoding="utf-8"))
        itinerary.pop("$schema")
        self.write(rel, json.dumps(itinerary))
        config = (self.root / "seasoningway.toml").read_text(encoding="utf-8")
        old = config.replace(f'engine = "{__version__}"', "'engine' = '0.0.0' # 保留註解")
        self.write("seasoningway.toml", old.replace(f"format = {FORMAT}", "format = 3"))
        workflow = self.root / ".github/workflows/check.yml"
        self.write(".github/workflows/check.yml", workflow.read_text(encoding="utf-8").replace(__version__, "0.0.0"))
        custom = self.write("profile/preferences.md", "# 自訂偏好\n")
        self.assertIn("從第 3 版升級", self.ok(self.cli("upgrade", cwd=trip)))
        current = (self.root / "seasoningway.toml").read_text(encoding="utf-8")
        self.assertIn(f"'engine' = \"{__version__}\" # 保留註解", current)
        self.assertEqual(tomllib.loads(current)["format"], FORMAT)
        self.assertIn(f"uses: tsunderink/seasoningway@v{__version__}", workflow.read_text(encoding="utf-8"))
        self.assertIn("$schema", json.loads((self.root / rel).read_text(encoding="utf-8")))
        self.assertEqual(custom.read_text(encoding="utf-8"), "# 自訂偏好\n")
        self.ok(self.cli("check"))

    def test_refuses_to_overwrite_custom_file_and_avoids_initializing_nested_repo(self):
        self.write("AGENTS.md", "# 自訂規則\n")
        result = self.cli("init")
        self.assertIn("自訂檔案", result.stderr)
        self.assertFalse((self.root / "seasoningway.toml").exists())
        self.assertEqual((self.root / "AGENTS.md").read_text(encoding="utf-8"), "# 自訂規則\n")
        (self.root / "AGENTS.md").unlink()
        self.initialize()
        nested = self.root / "profile"
        result = self.cli("init", cwd=nested)
        self.assertIn("Git 工作區的根目錄", result.stderr)
        self.assertFalse((nested / "seasoningway.toml").exists())


class AgentHooksTest(DataRepoTest):
    def setUp(self):
        super().setUp()
        self.initialize()
        self.payload = {"cwd": str(self.root)}

    def test_generated_agent_hooks_run_using_cwd_from_payload(self):
        for tool, name in (("claude", ".claude/settings.json"), ("codex", ".codex/hooks.json")):
            with self.subTest(tool=tool):
                hooks = json.loads((self.root / name).read_text(encoding="utf-8"))["hooks"]
                self.assertEqual(set(hooks), {"SessionStart", "PreToolUse", "PostToolUse", "Stop"})
                def dispatch(event, payload):
                    command = hooks[event][0]["hooks"][0]["command"]
                    return self.hook(tool, "", {**self.payload, **payload}, command=command)

                started = self.ok(dispatch("SessionStart", {}))
                if tool == "codex":
                    started = json.loads(started)["hookSpecificOutput"]["additionalContext"]
                self.assertIn("[旅程 repo]", started)
                pre = {"tool_name": "Write", "tool_input": {"file_path": "profile/notes.md", "content": SYNTHETIC_PII}}
                if tool == "codex":
                    pre = {"tool_name": "apply_patch", "tool_input": {"command":
                           f"*** Begin Patch\n*** Add File: profile/notes.md\n+{SYNTHETIC_PII}\n*** End Patch"}}
                denial = json.loads(self.ok(dispatch("PreToolUse", pre)))["hookSpecificOutput"]
                self.assertEqual(denial["permissionDecision"], "deny")
                self.assertIn("護照號碼", denial["permissionDecisionReason"])
                self.assertNotIn(SYNTHETIC_PII, denial["permissionDecisionReason"])

                note = "trips/2099-01-example-city/research/test.md"
                self.write(note, "[來源](https://example.invalid/test)\n")
                post = {"tool_name": "Write", "tool_input": {"file_path": str(self.root / note)}}
                if tool == "codex":
                    post = {"tool_name": "apply_patch", "tool_input": {"command": f"*** Add File: {note}\n+x"}}
                result = dispatch("PostToolUse", post)
                reason = json.loads(self.ok(result))["reason"] if tool == "codex" else result.stderr
                self.assertIn("查詢於", reason)

                result = dispatch("Stop", {})
                reason = json.loads(self.ok(result))["reason"] if tool == "codex" else result.stderr
                self.assertIn("未保存", reason)
                self.ok(dispatch("Stop", {"stop_hook_active": True}))

    def test_pre_edit_denies_agent_writing_in_wrong_area_and_new_schema_errors(self):
        def claude(path, content, agent):
            payload = {**self.payload, "agent_type": agent, "tool_name": "Write",
                       "tool_input": {"file_path": path, "content": content}}
            result = self.ok(self.hook("claude", "pre-edit", payload))
            return json.loads(result)["hookSpecificOutput"]["permissionDecisionReason"] if result else ""

        self.assertIn("spot-scout", claude("profile/preferences.md", "偏好", "spot-scout"))
        self.assertIn("紀錄格式", claude("trips/2099-01-example-city/places/test.json", "{}", "spot-scout"))
        self.assertEqual(claude("trips/2099-01-example-city/research/test.md", "研究", "fact-checker"), "")
        self.assertIn("page-checker", claude("profile/notes.md", "文字", "page-checker"))
        self.assertEqual(claude(str(self.root.parent / "screenshot.png"), "圖", "page-checker"), "")
        self.assertIn("產物", claude("out/index.md", "產物", ""))

    def test_codex_pre_edit_blocks_adapter_moves_deletes_and_invalid_add(self):
        def reason(patch):
            payload = {**self.payload, "tool_input": {"command": f"*** Begin Patch\n{patch}\n*** End Patch"}}
            return json.loads(self.ok(self.hook("codex", "pre-edit", payload)))["hookSpecificOutput"]["permissionDecisionReason"]

        self.assertIn("轉接檔", reason("*** Delete File: AGENTS.md"))
        self.assertIn("轉接檔", reason("*** Update File: CLAUDE.md\n*** Move to: profile/notes.md"))
        self.assertIn("轉接檔", reason("*** Update File: profile/notes.md\n*** Move to: .codex/hooks.json"))
        self.assertIn("產物", reason("*** Delete File: out/index.md"))
        self.assertIn("紀錄格式", reason("*** Add File: trips/2099-01-example-city/budget.json\n+{}"))


class GitHooksTest(DataRepoTest):
    def setUp(self):
        super().setUp()
        self.initialize()

    def test_pre_commit_checks_staged_snapshot(self):
        self.write("profile/notes.md", SYNTHETIC_PII + "\n")
        self.ok(self.git("add", "--all"))
        self.write("profile/notes.md", "已移除個資\n")
        result = self.git("commit", "-m", "初次保存")
        self.assertNotEqual(result.returncode, 0)
        self.assertIn("護照號碼", result.stdout + result.stderr)
        self.ok(self.git("add", "--all"))
        self.ok(self.git("commit", "-m", "初次保存"))

    def test_pre_push_checks_initial_commit_lines_and_messages(self):
        bare = self.root.parent / "remote.git"
        self.ok(self.git("init", "--bare", str(bare)))
        self.ok(self.git("remote", "add", "origin", str(bare)))
        self.write("profile/notes.md", SYNTHETIC_PII + "\n")
        self.ok(self.git("add", "--all"))
        self.ok(self.git("-c", "core.hooksPath=.git/hooks", "commit", "-m", SYNTHETIC_PII))
        self.write("profile/notes.md", "已移除個資\n")
        self.ok(self.git("add", "--all"))
        self.ok(self.git("commit", "-m", "更新筆記"))
        result = self.git("push", "origin", "main")
        self.assertNotEqual(result.returncode, 0)
        self.assertIn("profile/notes.md:1", result.stderr)
        self.assertIn("commit 訊息第 1 行", result.stderr)
        self.assertNotIn(SYNTHETIC_PII, result.stderr)
        self.ok(self.git("checkout", "--orphan", "clean"))
        self.ok(self.git("add", "--all"))
        self.ok(self.git("commit", "-m", "乾淨的初次保存"))
        self.ok(self.git("push", "origin", "clean"))
        self.ok(self.git("push", "origin", "--delete", "clean"))

    def test_pre_push_checks_lines_added_while_resolving_a_merge(self):
        bare = self.root.parent / "remote.git"
        self.ok(self.git("init", "--bare", str(bare)))
        self.ok(self.git("remote", "add", "origin", str(bare)))
        self.write("profile/notes.md", "筆記\n")
        self.ok(self.cli("save"))
        self.ok(self.git("push", "origin", "main"))
        self.ok(self.git("switch", "-c", "other"))
        self.write("profile/notes.md", "修改甲\n")
        self.ok(self.cli("save"))
        self.ok(self.git("switch", "main"))
        self.write("profile/notes.md", "修改乙\n")
        self.ok(self.cli("save"))
        self.assertNotEqual(self.git("merge", "other").returncode, 0)
        self.write("profile/notes.md", SYNTHETIC_PII + "\n")
        self.ok(self.git("add", "--", "profile/notes.md"))
        self.ok(self.git("-c", "core.hooksPath=.git/hooks", "commit", "-m", "整合筆記"))
        result = self.git("push", "origin", "main")
        self.assertNotEqual(result.returncode, 0)
        self.assertIn("profile/notes.md:1", result.stderr)
