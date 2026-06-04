"""Baseline-firewall audit (sprint-6 / B).

The customer execution path (planner -> router -> protocol adapter ->
external API) is FORBIDDEN from importing or referencing any module
under ``planmyagents_api.benchmark.baselines.*``. The baselines exist
purely as scoring references for the benchmark runner; if one ever
appears in a customer routing decision, PlanMyAgents has accidentally
become an executor of the form we explicitly do not want to be.

The Python-level guard for this lives in
``apps/api/tests/test_benchmark_baseline_firewall.py`` (4 tests, all
green) — it imports each routing module and confirms no baseline
submodule ends up in ``sys.modules`` as a side effect. That's the
strong invariant.

This script is the **shallow** companion guard: it greps the source
tree for any string of the form ``planmyagents_api.benchmark.baselines``
under the routing/planner/web/workflows modules. It is faster, cheaper,
and catches the most obvious regression — someone typing
``from planmyagents_api.benchmark.baselines.razorpay import RazorpayPaymentAuthorization``
into a router file — even when the file isn't part of the standard
import chain (e.g. a typo in a helper called only from one branch).

CI uses this on every push. The Python test still runs the deep
invariant on every PR; this script is the cheap belt the deep test's
braces hang from.

Exit codes:
    0 - clean, no baseline references in routing modules
    1 - one or more baseline references found; CI fails
"""

from __future__ import annotations

import argparse
import ast
import json
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent.parent
DEFAULT_API_SRC = ROOT / "apps" / "api" / "planmyagents_api"

# Directories under apps/api/planmyagents_api/ that participate in the
# customer execution / routing / planning surface. Mirrors the
# ``ROUTING_MODULES`` constant in
# apps/api/tests/test_benchmark_baseline_firewall.py — keep both in
# sync. If a new top-level package joins the routing chain, add it
# here AND in the Python test.
ROUTING_DIRS = (
    "agents",
    "planner",
    "workflows",
    "web",
)

# Modules under those directories that are intentionally allowed to
# reference baselines. Today there is exactly one — the benchmark
# scheduler script lives under scripts/, not under any routing dir,
# so this list is empty by design. If you find yourself wanting to
# add an entry, ask why a routing module needs to know about a
# baseline at all; the answer is almost always "it doesn't".
ALLOWLISTED_FILES: tuple[Path, ...] = ()

BASELINE_PREFIX = "planmyagents_api.benchmark.baselines"


def _python_files_under(directory: Path) -> list[Path]:
    return [p for p in directory.rglob("*.py") if p.is_file()]


def _is_baseline_ref(name: str | None) -> bool:
    if not name:
        return False
    return name == BASELINE_PREFIX or name.startswith(BASELINE_PREFIX + ".")


def _scan_file(path: Path) -> list[tuple[int, str]]:
    """Return ``(line_number, summary)`` tuples for every AST node in
    ``path`` that imports or attribute-accesses something rooted at
    ``planmyagents_api.benchmark.baselines``.

    Pure AST scan so docstrings and comments that mention the firewall
    for documentation purposes (e.g. the architectural notes in
    ``router.py`` and ``baselines/__init__.py``) don't generate false
    positives. The firewall is about CODE references, not text.
    """

    hits: list[tuple[int, str]] = []
    try:
        text = path.read_text(encoding="utf-8")
    except (UnicodeDecodeError, FileNotFoundError):
        return hits
    try:
        tree = ast.parse(text, filename=str(path))
    except SyntaxError:
        return hits

    for node in ast.walk(tree):
        if isinstance(node, ast.ImportFrom):
            # `from planmyagents_api.benchmark.baselines[...] import X`
            module = node.module or ""
            if _is_baseline_ref(module):
                names = ", ".join(alias.name for alias in node.names)
                hits.append((node.lineno, f"from {module} import {names}"))
        elif isinstance(node, ast.Import):
            for alias in node.names:
                if _is_baseline_ref(alias.name):
                    suffix = (
                        f" as {alias.asname}"
                        if alias.asname
                        else ""
                    )
                    hits.append((node.lineno, f"import {alias.name}{suffix}"))
        elif isinstance(node, ast.Attribute):
            # Walk attribute chains like
            # planmyagents_api.benchmark.baselines.razorpay.RazorpayX
            # back to their root name and reconstruct the dotted form.
            chain: list[str] = []
            current: ast.AST = node
            while isinstance(current, ast.Attribute):
                chain.append(current.attr)
                current = current.value
            if isinstance(current, ast.Name):
                chain.append(current.id)
                dotted = ".".join(reversed(chain))
                if _is_baseline_ref(dotted):
                    hits.append((node.lineno, dotted))
    return hits


def audit(api_src: Path, *, allowlist: tuple[Path, ...] = ALLOWLISTED_FILES) -> dict:
    """Walk every routing module and collect every baseline reference.

    Returns a JSON-serialisable summary so callers (CI, dev scripts)
    can pretty-print or post the result to a chat channel without
    re-parsing stdout.
    """

    violations: list[dict[str, object]] = []
    scanned = 0
    for directory_name in ROUTING_DIRS:
        directory = api_src / directory_name
        if not directory.is_dir():
            continue
        for path in _python_files_under(directory):
            scanned += 1
            if path in allowlist:
                continue
            for lineno, line in _scan_file(path):
                try:
                    rel = str(path.relative_to(ROOT))
                except ValueError:
                    # Audit called on a synthetic fixture tree (tests).
                    rel = str(path)
                violations.append({
                    "file": rel,
                    "line": lineno,
                    "text": line,
                })
    return {
        "ok": not violations,
        "scanned_files": scanned,
        "routing_dirs": list(ROUTING_DIRS),
        "violations": violations,
    }


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--api-src",
        default=str(DEFAULT_API_SRC),
        help="Path to apps/api/planmyagents_api (auto-detected by default).",
    )
    parser.add_argument(
        "--json",
        action="store_true",
        help="Emit the full JSON report to stdout (useful for CI annotations).",
    )
    args = parser.parse_args()

    api_src = Path(args.api_src).resolve()
    if not api_src.is_dir():
        print(
            f"audit-baseline-firewall: api source not found at {api_src}",
            file=sys.stderr,
        )
        return 2

    report = audit(api_src)

    if args.json:
        print(json.dumps(report, indent=2))
    else:
        print(
            f"audit-baseline-firewall: scanned {report['scanned_files']} files "
            f"across {', '.join(report['routing_dirs'])}"
        )
        if report["ok"]:
            print("audit-baseline-firewall: CLEAN — no baseline imports in routing modules")
        else:
            print(
                f"audit-baseline-firewall: {len(report['violations'])} VIOLATION(S):"
            )
            for v in report["violations"]:
                print(f"  {v['file']}:{v['line']}  {v['text']}")
            print(
                "audit-baseline-firewall: failing build. See "
                "apps/api/planmyagents_api/benchmark/baselines/__init__.py "
                "for the architectural reason this is forbidden."
            )

    return 0 if report["ok"] else 1


if __name__ == "__main__":
    raise SystemExit(main())
