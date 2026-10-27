"""Data-location checks with isolated environments and temporary stores."""

import json
import os
import runpy
import shutil
import subprocess
import sys
import tempfile
import unittest
from contextlib import chdir
from pathlib import Path
from unittest.mock import patch

from src import config


class DataDirectoryTests(unittest.TestCase):
    def setUp(self):
        temporary = tempfile.TemporaryDirectory()
        self.addCleanup(temporary.cleanup)
        self.root = Path(temporary.name).resolve()
        self.project = self.root / "project"
        self.launch = self.root / "launch"
        self.launch.mkdir()
        source = self.project / "src"
        source.mkdir(parents=True)
        self.config_path = source / "config.py"
        shutil.copyfile(config.__file__, self.config_path)

    def settings(self, environment=None):
        with chdir(self.launch), patch.dict(os.environ, environment or {}, clear=True):
            return runpy.run_path(str(self.config_path))

    def test_default_is_local_to_the_launch_directory_and_created_lazily(self):
        for environment in ({}, {"AGENT_1_DATA_DIR": ""}):
            with self.subTest(environment=environment):
                settings = self.settings(environment)
                self.assertEqual(settings["DATA_DIR"], self.launch / ".agent-1")
                self.assertEqual(settings["PROJECT_ROOT"], self.project)
                self.assertFalse(settings["DATA_DIR"].exists())

    def test_project_dotenv_is_loaded_before_paths_are_selected(self):
        (self.project / ".env").write_text("AGENT_1_DATA_DIR=debug-data\n", encoding="utf-8")
        # A launch-directory .env must not replace the project's configuration.
        (self.launch / ".env").write_text("AGENT_1_DATA_DIR=wrong-store\n", encoding="utf-8")
        self.assertEqual(self.settings()["DATA_DIR"], self.launch / "debug-data")

    def test_environment_overrides_dotenv_and_accepts_absolute_paths(self):
        (self.project / ".env").write_text("AGENT_1_DATA_DIR=ignored\n", encoding="utf-8")
        target = self.root / "custom store"
        settings = self.settings({"AGENT_1_DATA_DIR": str(target)})
        self.assertEqual(settings["DATA_DIR"], target)

    def test_home_directory_override_expands_tilde(self):
        home = self.root / "home"
        settings = self.settings({
            "AGENT_1_DATA_DIR": "~/.agent-1",
            "USERPROFILE": str(home), "HOME": str(home),
        })
        self.assertEqual(settings["DATA_DIR"], home / ".agent-1")
        self.assertFalse(home.exists())

    def test_runtime_stores_memory_history_scratchpad_and_workspace_together(self):
        script = """
import json
from src import config, storage, tools

tools.WORKSPACE.mkdir(parents=True)
session = storage.new_session()
session.messages = [{"role": "user", "content": "Remember the debug store."}]
storage.memory("add", "agent", "Uses a local debug store.", session=session)
storage.memory("add", "user", "Prefers concise answers.", session=session)
storage.scratchpad("write", "Verify the selected store.", session=session)
tools.write_file("note.txt", "A workspace file.")
loaded = storage.load_session(session.id)
assert loaded.scratchpad == session.scratchpad
assert loaded.messages == session.messages
assert loaded.memory_snapshot["agent"][0]["content"] == "Uses a local debug store."
assert storage.session_search("debug store")["matches"][0]["session_id"] == session.id
assert tools.read_file("note.txt") == "A workspace file."
print(json.dumps({"data_dir": str(config.DATA_DIR), "session_id": session.id}))
"""
        environment = {
            **os.environ,
            "AGENT_1_DATA_DIR": "debug-data",
            "PYTHONPATH": str(config.PROJECT_ROOT),
        }
        result = subprocess.run(
            [sys.executable, "-c", script], cwd=self.launch, env=environment,
            capture_output=True, text=True, check=True,
        )
        state = json.loads(result.stdout)
        data = self.launch / "debug-data"
        self.assertEqual(Path(state["data_dir"]), data)
        files = {path.relative_to(data).as_posix() for path in data.rglob("*") if path.is_file()}
        self.assertEqual(files, {
            "MEMORY.md", "USER.md", "workspace/note.txt",
            f"sessions/{state['session_id']}.json",
        })
        saved = json.loads((data / "sessions" / f"{state['session_id']}.json").read_text(encoding="utf-8"))
        self.assertEqual(saved["scratchpad"], "Verify the selected store.")
        self.assertEqual(list(self.launch.iterdir()), [data])


if __name__ == "__main__":
    unittest.main()
