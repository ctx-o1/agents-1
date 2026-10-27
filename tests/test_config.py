"""Configuration checks with isolated environments and temporary stores."""

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

    def test_runtime_settings_use_project_dotenv_with_environment_precedence(self):
        (self.project / ".env").write_text(
            "OPENROUTER_MODEL=dotenv/model\nAGENT_1_MAX_OUTPUT_TOKENS=8192\nAGENT_1_STREAM=false\n",
            encoding="utf-8",
        )
        with chdir(self.launch), patch.dict(os.environ, {"OPENROUTER_MODEL": "environment/model"}, clear=True):
            module = runpy.run_path(str(self.config_path))
            settings = module["load_settings"]()
        self.assertEqual(settings.model, "environment/model")
        self.assertEqual(settings.max_output_tokens, 8192)
        self.assertFalse(settings.stream)

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


class RuntimeSettingsTests(unittest.TestCase):
    def test_defaults_and_explicit_mapping_ignore_unrelated_environment(self):
        with patch.dict(os.environ, {"AGENT_1_MAX_OUTPUT_TOKENS": "invalid"}):
            settings = config.load_settings({})
        self.assertEqual(settings.max_output_tokens, 16384)
        self.assertEqual(settings.max_rounds, 10)
        self.assertTrue(settings.stream)
        self.assertTrue(settings.reasoning_enabled)
        self.assertIsNone(settings.max_task_tokens)
        self.assertEqual(settings.api_key, "")

    def test_overrides_are_parsed_without_exposing_credentials_in_repr(self):
        settings = config.load_settings({
            "OPENROUTER_API_KEY": "private-key", "OPENROUTER_MODEL": "custom/model",
            "OPENROUTER_BASE_URL": "http://localhost:8000/v1",
            "AGENT_1_MAX_OUTPUT_TOKENS": "8192", "AGENT_1_MAX_ROUNDS": "3",
            "AGENT_1_STREAM": " FALSE ", "AGENT_1_REASONING_ENABLED": "0",
            "AGENT_1_MAX_TASK_TOKENS": "50000",
        })
        self.assertEqual(settings.model, "custom/model")
        self.assertEqual(settings.base_url, "http://localhost:8000/v1")
        self.assertEqual(settings.max_output_tokens, 8192)
        self.assertEqual(settings.max_rounds, 3)
        self.assertFalse(settings.stream)
        self.assertFalse(settings.reasoning_enabled)
        self.assertEqual(settings.max_task_tokens, 50000)
        self.assertNotIn("private-key", repr(settings))

    def test_blank_optional_values_use_defaults(self):
        settings = config.load_settings({
            "AGENT_1_MAX_TASK_TOKENS": " ", "AGENT_1_MAX_OUTPUT_TOKENS": "",
            "AGENT_1_MAX_ROUNDS": "", "AGENT_1_STREAM": "", "AGENT_1_REASONING_ENABLED": "",
        })
        self.assertIsNone(settings.max_task_tokens)
        self.assertEqual(settings.max_output_tokens, 16384)
        self.assertTrue(settings.stream)

    def test_invalid_configuration_identifies_the_setting(self):
        for name, values in (
            ("AGENT_1_MAX_OUTPUT_TOKENS", ("0", "-1", "1.5", "many")),
            ("AGENT_1_MAX_ROUNDS", ("0", "-3", "many")),
            ("AGENT_1_MAX_TASK_TOKENS", ("0", "-1", "many")),
            ("AGENT_1_STREAM", ("sometimes",)),
            ("AGENT_1_REASONING_ENABLED", ("maybe",)),
            ("OPENROUTER_MODEL", ("", " ")),
            ("OPENROUTER_BASE_URL", ("", "localhost:8000/v1", "ftp://host/v1", "https://", "https://host:bad/v1")),
        ):
            for value in values:
                with self.subTest(name=name, value=value), self.assertRaisesRegex(ValueError, name):
                    config.load_settings({name: value})


if __name__ == "__main__":
    unittest.main()
