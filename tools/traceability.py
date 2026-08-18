#!/usr/bin/env python3
"""Requirement traceability report (Phase 8).

Cross-checks three things that normally drift apart:

  1. every requirement ID in REQUIREMENTS.md appears in the matrix, and the
     matrix invents no IDs the SRS does not define;
  2. every artefact the matrix cites actually EXISTS — pytest node ids are
     collected from the real suite, Robot test names are located in their
     suites, ctest names in the native project, CI steps in the workflow,
     files on disk;
  3. which requirements still depend on a hardware gate.

A matrix entry pointing at a renamed or deleted test fails the report. That
is the whole point: coverage claims have to stay true to survive.

Usage:
  tools/traceability.py [--json] [--markdown OUT.md]
Exit: 0 clean, 1 broken evidence or missing coverage.
"""

from __future__ import annotations

import argparse
import json
import re
import subprocess
import sys
from dataclasses import dataclass, field
from pathlib import Path

REPO = Path(__file__).resolve().parent.parent
MATRIX = REPO / "tools" / "traceability.yaml"
SRS = REPO / "REQUIREMENTS.md"
WORKFLOW = REPO / ".github" / "workflows" / "ci.yml"
NATIVE_CMAKE = REPO / "tests" / "native" / "CMakeLists.txt"

REQ_ID_RE = re.compile(r"\*\*((?:GUI|MAT|RTE|BLD|CAL|NFR)-\d+)\*\*")
ROBOT_TEST_RE = re.compile(r"^([A-Z][^\s].*?)$", re.MULTILINE)


@dataclass
class Check:
    ok: bool
    detail: str


@dataclass
class RequirementReport:
    req_id: str
    statement: str
    status: str
    checks: list[Check] = field(default_factory=list)
    hardware_gates: list[str] = field(default_factory=list)

    @property
    def broken(self) -> list[Check]:
        return [c for c in self.checks if not c.ok]

    @property
    def verified(self) -> bool:
        return bool(self.checks) and not self.broken


# --- evidence resolvers -----------------------------------------------------

def collect_pytest_ids() -> set[str]:
    """Every test node id the host suite actually defines."""
    result = subprocess.run(
        [sys.executable, "-m", "pytest", "tests", "--collect-only", "-q",
         "--ignore=tests/native", "-p", "no:cacheprovider"],
        cwd=REPO, capture_output=True, text=True,
        env={**_env(), "QT_QPA_PLATFORM": "offscreen"}, check=False)
    ids = set()
    for line in result.stdout.splitlines():
        line = line.strip()
        if "::" in line and not line.startswith(("=", "<", "no tests")):
            ids.add(line.split("[")[0])
    return ids


def _env() -> dict:
    import os

    env = dict(os.environ)
    env["PYTHONPATH"] = str(REPO / "host") + ":" + env.get("PYTHONPATH", "")
    return env


def collect_robot_tests() -> dict[str, set[str]]:
    """Test-case names per Robot suite, parsed from the *** Test Cases ***
    section (names sit at column 0)."""
    suites: dict[str, set[str]] = {}
    for path in sorted((REPO / "sim").glob("*.robot")):
        text = path.read_text(encoding="utf-8")
        section = text.split("*** Test Cases ***")
        names: set[str] = set()
        if len(section) > 1:
            for line in section[1].splitlines():
                if line and not line[0].isspace() and not line.startswith("*"):
                    names.add(line.strip())
        suites[f"sim/{path.name}"] = names
    return suites


def collect_native_tests() -> set[str]:
    if not NATIVE_CMAKE.is_file():
        return set()
    text = NATIVE_CMAKE.read_text(encoding="utf-8")
    names = set(re.findall(r"add_test\(NAME\s+(\w+)", text))
    for match in re.finditer(r"foreach\((\w+)\s+([^)]+)\)", text):
        names.update(match.group(2).split())
    return names


def collect_ci_steps() -> set[str]:
    if not WORKFLOW.is_file():
        return set()
    return set(re.findall(r"^\s*- name:\s*(.+?)\s*$",
                          WORKFLOW.read_text(encoding="utf-8"), re.MULTILINE))


def srs_requirement_ids() -> set[str]:
    return set(REQ_ID_RE.findall(SRS.read_text(encoding="utf-8")))


# --- report -----------------------------------------------------------------

def build_report() -> tuple[list[RequirementReport], list[str]]:
    import yaml

    matrix = yaml.safe_load(MATRIX.read_text(encoding="utf-8"))["requirements"]
    pytest_ids = collect_pytest_ids()
    robot_suites = collect_robot_tests()
    native_tests = collect_native_tests()
    ci_steps = collect_ci_steps()

    reports: list[RequirementReport] = []
    for req_id in sorted(matrix):
        entry = matrix[req_id]
        report = RequirementReport(
            req_id=req_id,
            statement=entry.get("statement", ""),
            status=entry.get("status", "verified"),
        )
        for item in entry.get("evidence", []):
            kind = item["kind"]
            if kind == "pytest":
                node = item["id"]
                report.checks.append(Check(
                    node in pytest_ids,
                    f"pytest {node}" + ("" if node in pytest_ids
                                        else "  <- NOT COLLECTED")))
            elif kind == "robot":
                suite, _, name = item["id"].partition("::")
                known = robot_suites.get(suite, set())
                report.checks.append(Check(
                    name in known,
                    f"robot {suite}::{name}" + ("" if name in known
                                                else "  <- NOT FOUND")))
            elif kind == "native":
                name = item["id"]
                report.checks.append(Check(
                    name in native_tests,
                    f"native ctest {name}" + ("" if name in native_tests
                                              else "  <- NOT DEFINED")))
            elif kind == "ci":
                step = item["step"]
                report.checks.append(Check(
                    step in ci_steps,
                    f"ci step {step!r}" + ("" if step in ci_steps
                                           else "  <- NOT IN WORKFLOW")))
            elif kind == "file":
                path = REPO / item["path"]
                note = f" ({item['note']})" if item.get("note") else ""
                report.checks.append(Check(
                    path.is_file(),
                    f"file {item['path']}{note}"
                    + ("" if path.is_file() else "  <- MISSING")))
            elif kind == "hardware":
                report.hardware_gates.append(item["note"])
            else:
                report.checks.append(Check(False, f"unknown evidence kind {kind!r}"))
        reports.append(report)

    # Coverage both ways: no requirement unlisted, no invented IDs.
    problems: list[str] = []
    srs_ids = srs_requirement_ids()
    for missing in sorted(srs_ids - set(matrix)):
        problems.append(f"{missing}: defined in the SRS but absent from the matrix")
    for invented in sorted(set(matrix) - srs_ids):
        problems.append(f"{invented}: in the matrix but not defined in the SRS")
    return reports, problems


def render_markdown(reports: list[RequirementReport],
                    problems: list[str]) -> str:
    verified = [r for r in reports if r.verified and not r.hardware_gates]
    gated = [r for r in reports if r.verified and r.hardware_gates]
    broken = [r for r in reports if r.broken]

    preamble = ("Generated by `tools/traceability.py`, which verifies that "
                "every artefact cited below actually exists — a renamed or "
                "deleted test fails this report rather than silently "
                "weakening coverage.")
    gated_line = (f"- **{len(gated)}** verified as far as emulation allows, "
                  f"with a hardware gate remaining")
    lines = [
        "# Requirement traceability report",
        "",
        preamble,
        "",
        f"- **{len(verified)}** requirements verified by automated evidence",
        gated_line,
        f"- **{len(broken)}** with broken evidence",
        f"- **{len(problems)}** coverage problems",
        "",
        "## Status by requirement",
        "",
        "| ID | Requirement | Automated evidence | Hardware gate |",
        "|---|---|---|---|",
    ]
    for report in reports:
        mark = "broken" if report.broken else (
            "hardware" if report.hardware_gates else "verified")
        gate = "; ".join(report.hardware_gates) or "—"
        lines.append(
            f"| {report.req_id} | {report.statement} | "
            f"{len(report.checks)} artefacts, {mark} | {gate} |")

    if broken:
        lines += ["", "## Broken evidence", ""]
        for report in broken:
            for check in report.broken:
                lines.append(f"- **{report.req_id}**: {check.detail}")
    if problems:
        lines += ["", "## Coverage problems", ""]
        lines += [f"- {p}" for p in problems]

    lines += [
        "",
        "## Release readiness",
        "",
    ]
    if broken or problems:
        lines.append("**Not releasable**: the evidence above does not hold.")
    elif gated:
        lines.append(
            f"**v1.0 blocked on hardware validation.** Every requirement is "
            f"implemented and verified as far as host testing and emulation "
            f"reach, but {len(gated)} carry gates that only a physical "
            f"RP2040 (plus a logic analyzer for the timing budgets) can "
            f"close. Tagging v1.0 before those run would misrepresent the "
            f"state of the system.")
        lines += ["", "Open hardware gates:", ""]
        for report in gated:
            for gate in report.hardware_gates:
                lines.append(f"- **{report.req_id}** — {gate}")
    else:
        lines.append("**All requirements verified.** v1.0 may be tagged.")
    return "\n".join(lines) + "\n"


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--json", action="store_true")
    parser.add_argument("--markdown", type=Path)
    args = parser.parse_args(argv)

    reports, problems = build_report()

    if args.json:
        print(json.dumps({
            "requirements": [
                {"id": r.req_id, "statement": r.statement,
                 "verified": r.verified,
                 "hardware_gates": r.hardware_gates,
                 "broken": [c.detail for c in r.broken]}
                for r in reports],
            "problems": problems,
        }, indent=2))
    else:
        for report in reports:
            mark = ("BROKEN  " if report.broken else
                    "HW-GATE " if report.hardware_gates else "ok      ")
            print(f"{mark}{report.req_id:<8} {len(report.checks)} artefacts")
            for check in report.checks:
                if not check.ok:
                    print(f"          ! {check.detail}")
            for gate in report.hardware_gates:
                print(f"          - hardware: {gate}")
        for problem in problems:
            print(f"COVERAGE  {problem}")

    if args.markdown:
        args.markdown.write_text(render_markdown(reports, problems),
                                 encoding="utf-8")
        print(f"\nwrote {args.markdown}")

    broken = sum(len(r.broken) for r in reports)
    if not args.json:  # keep stdout parseable when JSON was requested
        if broken or problems:
            print(f"\n{broken} broken evidence item(s), {len(problems)} "
                  f"coverage problem(s)")
        else:
            gated = sum(1 for r in reports if r.hardware_gates)
            print(f"\nall evidence verified; {gated} requirement(s) await "
                  f"hardware")
    return 1 if (broken or problems) else 0


if __name__ == "__main__":
    raise SystemExit(main())
