"""Benchmark case loading and validation."""

from __future__ import annotations

from pathlib import Path
from typing import Any

import yaml

from planmyagents_api.benchmark.models import TestCase


class BenchmarkLoadError(ValueError):
    """Raised when benchmark files are malformed."""


def load_test_cases(benchmarks_dir: Path, capability: str | None = None) -> list[TestCase]:
    """Load benchmark cases from a directory tree.

    Supports two file styles:
    1. A single test case per YAML file.
    2. A YAML file with a top-level `cases` list.
    """

    if not benchmarks_dir.exists():
        raise BenchmarkLoadError(f"Benchmark directory does not exist: {benchmarks_dir}")

    cases: list[TestCase] = []
    files = sorted(
        [*benchmarks_dir.rglob("*.yaml"), *benchmarks_dir.rglob("*.yml")],
        key=lambda path: str(path),
    )

    for file_path in files:
        raw = yaml.safe_load(file_path.read_text()) or {}
        if not isinstance(raw, dict):
            raise BenchmarkLoadError(f"{file_path}: expected mapping at top level")

        entries = raw.get("cases", [raw])
        if not isinstance(entries, list):
            raise BenchmarkLoadError(f"{file_path}: `cases` must be a list")

        for index, entry in enumerate(entries):
            case = _parse_case(entry, file_path, index)
            if capability is None or case.capability == capability:
                cases.append(case)

    return cases


def _parse_case(raw: Any, file_path: Path, index: int) -> TestCase:
    if not isinstance(raw, dict):
        raise BenchmarkLoadError(f"{file_path} case #{index}: expected mapping")

    required = ["id", "capability", "difficulty", "inputs", "expected"]
    missing = [key for key in required if key not in raw]
    if missing:
        raise BenchmarkLoadError(f"{file_path} case #{index}: missing {missing}")

    if not isinstance(raw["inputs"], dict):
        raise BenchmarkLoadError(f"{file_path} case #{index}: `inputs` must be a mapping")
    if not isinstance(raw["expected"], dict):
        raise BenchmarkLoadError(f"{file_path} case #{index}: `expected` must be a mapping")

    return TestCase(
        id=str(raw["id"]),
        capability=str(raw["capability"]),
        difficulty=str(raw["difficulty"]),
        inputs=raw["inputs"],
        expected=raw["expected"],
        notes=str(raw.get("notes", "")),
        created_at=str(raw["created_at"]) if raw.get("created_at") else None,
        created_by=str(raw["created_by"]) if raw.get("created_by") else None,
    )
