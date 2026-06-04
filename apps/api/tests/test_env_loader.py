"""Tests for the zero-dependency `.env` loader."""

from __future__ import annotations

import os
import sys
import tempfile
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[3]
sys.path.insert(0, str(ROOT / "apps" / "api"))

from planmyagents_api import _env  # noqa: E402


class LoadDotenvOnceTests(unittest.TestCase):
    def setUp(self) -> None:
        # Each test gets a fresh `_LOADED` flag and a clean env slate for the
        # keys we touch, so tests don't pollute each other.
        _env._LOADED = False
        self._original_env: dict[str, str | None] = {}
        for key in [
            "PLANMYAGENTS_TEST_KEY_A",
            "PLANMYAGENTS_TEST_KEY_B",
            "PLANMYAGENTS_TEST_KEY_QUOTED",
            "PLANMYAGENTS_TEST_KEY_PREEXISTING",
        ]:
            self._original_env[key] = os.environ.pop(key, None)

    def tearDown(self) -> None:
        _env._LOADED = False
        for key, value in self._original_env.items():
            if value is None:
                os.environ.pop(key, None)
            else:
                os.environ[key] = value

    def test_loads_simple_key_value_pairs(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            env_path = Path(tmp) / ".env"
            env_path.write_text(
                "PLANMYAGENTS_TEST_KEY_A=alpha\n"
                "PLANMYAGENTS_TEST_KEY_B=beta gamma\n"
            )
            loaded = _env.load_dotenv_once(env_path)
            self.assertEqual(loaded, env_path)
            self.assertEqual(os.environ["PLANMYAGENTS_TEST_KEY_A"], "alpha")
            self.assertEqual(os.environ["PLANMYAGENTS_TEST_KEY_B"], "beta gamma")

    def test_strips_surrounding_quotes(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            env_path = Path(tmp) / ".env"
            env_path.write_text('PLANMYAGENTS_TEST_KEY_QUOTED="hello world"\n')
            _env.load_dotenv_once(env_path)
            self.assertEqual(
                os.environ["PLANMYAGENTS_TEST_KEY_QUOTED"], "hello world"
            )

    def test_skips_blank_lines_and_comments(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            env_path = Path(tmp) / ".env"
            env_path.write_text(
                "\n"
                "# this is a comment\n"
                "   # indented comment\n"
                "PLANMYAGENTS_TEST_KEY_A=ok\n"
            )
            _env.load_dotenv_once(env_path)
            self.assertEqual(os.environ["PLANMYAGENTS_TEST_KEY_A"], "ok")

    def test_preserves_shell_exported_values(self) -> None:
        os.environ["PLANMYAGENTS_TEST_KEY_PREEXISTING"] = "shell-wins"
        with tempfile.TemporaryDirectory() as tmp:
            env_path = Path(tmp) / ".env"
            env_path.write_text(
                "PLANMYAGENTS_TEST_KEY_PREEXISTING=file-loses\n"
            )
            _env.load_dotenv_once(env_path)
            self.assertEqual(
                os.environ["PLANMYAGENTS_TEST_KEY_PREEXISTING"], "shell-wins",
                "Values already in os.environ must not be overwritten by .env",
            )

    def test_idempotent_second_call_is_noop(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            env_path = Path(tmp) / ".env"
            env_path.write_text("PLANMYAGENTS_TEST_KEY_A=first\n")
            _env.load_dotenv_once(env_path)

            env_path.write_text("PLANMYAGENTS_TEST_KEY_A=second\n")
            second = _env.load_dotenv_once(env_path)

            self.assertIsNone(second, "Second call must return None (already loaded)")
            self.assertEqual(
                os.environ["PLANMYAGENTS_TEST_KEY_A"], "first",
                "Second call must not re-read the file",
            )

    def test_missing_file_is_noop(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            env_path = Path(tmp) / "does-not-exist.env"
            loaded = _env.load_dotenv_once(env_path)
            self.assertIsNone(loaded)


if __name__ == "__main__":
    unittest.main()
