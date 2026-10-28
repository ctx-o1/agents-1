"""Workspace-tool checks using temporary files only."""

import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from src import tools


class DirectoryListingTests(unittest.TestCase):
    def setUp(self):
        temporary = self.enterContext(tempfile.TemporaryDirectory())
        self.workspace = Path(temporary).resolve()
        self.enterContext(patch.object(tools, "WORKSPACE", self.workspace))

    def test_empty_and_complete_listings_are_distinct_from_truncated_listings(self):
        self.assertEqual(tools.list_files(), "(empty directory)")
        for index in range(200):
            (self.workspace / f"{index:03d}.txt").touch()
        complete = tools.list_files()
        self.assertEqual(len(complete.splitlines()), 200)
        self.assertNotIn("truncated", complete)
        (self.workspace / "200.txt").touch()
        partial = tools.list_files()
        self.assertEqual(partial.splitlines()[:200], complete.splitlines())
        self.assertIn("[Results truncated", partial.splitlines()[-1])

    def test_long_names_also_obey_the_shared_character_budget(self):
        for index in range(170):
            (self.workspace / (f"{index:03d}-" + "x" * 140 + ".txt")).touch()
        result = tools.list_files()
        lines = result.splitlines()
        self.assertIn("truncated", lines[-1])
        self.assertLess(len(lines), 171)
        self.assertLessEqual(len("\n".join(lines[:-1])), tools.MAX_TOOL_OUTPUT)
        self.assertLess(len(result), tools.MAX_TOOL_OUTPUT + 100)
