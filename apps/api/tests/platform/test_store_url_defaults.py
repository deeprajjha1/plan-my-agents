"""Guard tests for the centralised store-URL default in ``_config.py``.

These tests are the regression net for the 2026-05-19 split-brain bug:
``web/app.py`` and ``web/planning.py`` had independently declared
different fallback defaults for ``PLANMYAGENTS_DISCOVERY_STORE_URL``,
so any environment that forgot to set the env var would silently fan
out into two different stores.

The guard walks the AST of every module under ``planmyagents_api`` and
asserts that no file other than ``_config.py`` declares its own non-empty
default for one of the three store env vars. The empty-string default
(``os.getenv("PLANMYAGENTS_DISCOVERY_STORE_URL", "")`` in
``agents/router.py``) is explicitly allowed because it is a "skip if
unset" sentinel, not a fallback DSN.
"""

from __future__ import annotations

import ast
import logging
import os
import unittest
from pathlib import Path

from planmyagents_api import _config

PROJECT_ROOT = Path(__file__).resolve().parents[2]
PACKAGE_ROOT = PROJECT_ROOT / "planmyagents_api"

GUARDED_ENV_VARS = {
    _config.DISCOVERY_STORE_ENV,
    _config.BENCHMARK_STORE_ENV,
    _config.VERIFICATION_STORE_ENV,
}


class _GetenvDefaultVisitor(ast.NodeVisitor):
    """Collect every ``os.getenv("PLANMYAGENTS_*_STORE_URL", <default>)`` call.

    Only flags calls whose first argument is one of the three guarded
    env vars AND whose second argument is a non-empty string literal.
    Calls without a second argument or with the empty string default
    are intentionally ignored — they are either pure reads (returning
    ``None``) or the skip-sentinel pattern used in ``agents/router.py``.
    """

    def __init__(self) -> None:
        self.violations: list[tuple[int, str, str]] = []

    def visit_Call(self, node: ast.Call) -> None:  # noqa: N802 — AST hook name
        if _is_os_getenv(node.func) and len(node.args) >= 2:
            env_arg = node.args[0]
            default_arg = node.args[1]
            env_name = _string_literal(env_arg)
            default_value = _string_literal(default_arg)
            if (
                env_name in GUARDED_ENV_VARS
                and default_value is not None
                and default_value != ""
            ):
                self.violations.append((node.lineno, env_name, default_value))
        self.generic_visit(node)


def _is_os_getenv(func: ast.expr) -> bool:
    if isinstance(func, ast.Attribute) and func.attr == "getenv":
        value = func.value
        return isinstance(value, ast.Name) and value.id == "os"
    return False


def _string_literal(node: ast.expr) -> str | None:
    if isinstance(node, ast.Constant) and isinstance(node.value, str):
        return node.value
    return None


def _iter_python_files() -> list[Path]:
    paths: list[Path] = []
    for path in PACKAGE_ROOT.rglob("*.py"):
        rel = path.relative_to(PACKAGE_ROOT)
        if rel.parts and rel.parts[0] == "__pycache__":
            continue
        paths.append(path)
    return paths


class StoreUrlDefaultGuardTests(unittest.TestCase):
    """Pin the "exactly one source of truth" invariant."""

    def test_only_config_module_declares_store_url_defaults(self) -> None:
        offenders: list[str] = []
        for path in _iter_python_files():
            if path.name == "_config.py":
                continue
            tree = ast.parse(path.read_text(encoding="utf-8"))
            visitor = _GetenvDefaultVisitor()
            visitor.visit(tree)
            for lineno, env_name, default_value in visitor.violations:
                offenders.append(
                    f"{path.relative_to(PROJECT_ROOT)}:{lineno} declares "
                    f'{env_name}={default_value!r} as a default — import from '
                    f"planmyagents_api._config instead."
                )
        self.assertEqual(
            offenders,
            [],
            msg=(
                "Found store-URL default(s) outside _config.py. This is "
                "exactly the split-brain bug the centralised config was "
                "introduced to prevent. Route the read through "
                "discovery_store_url() / benchmark_store_url() / "
                "verification_store_url() in planmyagents_api._config.\n"
                + "\n".join(offenders)
            ),
        )

    def test_config_helpers_fall_back_to_default_postgres_dsn(self) -> None:
        """Helpers return the canonical DSN when the env var is unset."""

        guarded = list(GUARDED_ENV_VARS)
        saved = {name: os.environ.pop(name, None) for name in guarded}
        try:
            self.assertEqual(_config.discovery_store_url(), _config.DEFAULT_POSTGRES_DSN)
            self.assertEqual(_config.benchmark_store_url(), _config.DEFAULT_POSTGRES_DSN)
            self.assertEqual(
                _config.verification_store_url(), _config.DEFAULT_POSTGRES_DSN
            )
        finally:
            for name, value in saved.items():
                if value is not None:
                    os.environ[name] = value

    def test_config_helpers_honour_explicit_env_var(self) -> None:
        """Explicit env value wins over the default."""

        guarded = list(GUARDED_ENV_VARS)
        saved = {name: os.environ.pop(name, None) for name in guarded}
        try:
            os.environ[_config.DISCOVERY_STORE_ENV] = "postgresql://test/discovery"
            os.environ[_config.BENCHMARK_STORE_ENV] = "postgresql://test/benchmark"
            os.environ[_config.VERIFICATION_STORE_ENV] = "postgresql://test/verify"
            self.assertEqual(
                _config.discovery_store_url(), "postgresql://test/discovery"
            )
            self.assertEqual(
                _config.benchmark_store_url(), "postgresql://test/benchmark"
            )
            self.assertEqual(
                _config.verification_store_url(), "postgresql://test/verify"
            )
        finally:
            for name in guarded:
                os.environ.pop(name, None)
            for name, value in saved.items():
                if value is not None:
                    os.environ[name] = value


class LogStoreDefaultsInUseTests(unittest.TestCase):
    """Verify the startup-warning helper fires when (and only when) needed."""

    def test_no_warning_when_all_three_env_vars_are_set(self) -> None:
        guarded = list(GUARDED_ENV_VARS)
        saved = {name: os.environ.pop(name, None) for name in guarded}
        try:
            for name in guarded:
                os.environ[name] = "postgresql://configured/db"
            with self.assertLogs("planmyagents.config", level="WARNING") as captured:
                fallen_back = _config.log_store_defaults_in_use()
                # `assertLogs` requires at least one record; emit a benign
                # one so the context manager doesn't fail when there are
                # genuinely no warnings to capture.
                logging.getLogger("planmyagents.config").warning("guard-noop")
            self.assertEqual(fallen_back, [])
            self.assertEqual(
                [record.message for record in captured.records],
                ["guard-noop"],
            )
        finally:
            for name in guarded:
                os.environ.pop(name, None)
            for name, value in saved.items():
                if value is not None:
                    os.environ[name] = value

    def test_warns_for_each_unset_env_var(self) -> None:
        guarded = list(GUARDED_ENV_VARS)
        saved = {name: os.environ.pop(name, None) for name in guarded}
        try:
            with self.assertLogs("planmyagents.config", level="WARNING") as captured:
                fallen_back = _config.log_store_defaults_in_use()
            self.assertEqual(set(fallen_back), GUARDED_ENV_VARS)
            joined = " ".join(record.getMessage() for record in captured.records)
            for env_var in GUARDED_ENV_VARS:
                self.assertIn(env_var, joined)
        finally:
            for name, value in saved.items():
                if value is not None:
                    os.environ[name] = value


if __name__ == "__main__":
    unittest.main()
