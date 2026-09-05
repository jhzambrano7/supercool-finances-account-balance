#!/usr/bin/env python3
"""Enforce the commit convention defined in .claude/commands/commit.md.

A convention that lives only in a document decays. This runs as a commit-msg
hook so the rules are checked, not merely documented.

Checked:
  * Conventional Commits header, with a scope drawn from the project layout.
  * A `PRD:` trailer, since this project has no issue tracker and traceability
    to docs/prd.md is the substitute.
  * Absence of AI attribution trailers.
"""

import re
import sys
from pathlib import Path

TYPES = ("feat", "fix", "refactor", "perf", "test", "docs", "build", "ci", "chore", "revert")
SCOPES = (
    "domain",
    "application",
    "api",
    "sql",
    "db",
    "shared",
    "infra",
    "docs",
    "tooling",
    "session",
    "prd",
)

HEADER = re.compile(
    rf"^(?P<type>{'|'.join(TYPES)})"
    r"(?:\((?P<scope>[a-z0-9\-]+)\))?"
    r"(?P<breaking>!)?: "
    r"(?P<subject>.+)$"
)

# Either concrete references (I2, G3, §5.1) or an explicit, justified opt-out.
PRD_REFS = re.compile(r"^PRD: (?:(?:[GI][0-9]+|§[0-9]+(?:\.[0-9]+)?)(?:, )?)+$")
PRD_NA = re.compile(r"^PRD: n/a — .{6,}$")

ATTRIBUTION = re.compile(r"co-authored-by|generated with|🤖", re.IGNORECASE)

# Commits git creates or rewrites on its own must pass through untouched.
EXEMPT_PREFIXES = ("Merge ", "Revert ", "fixup!", "squash!")


def fail(problems: list[str], header: str) -> int:
    print("\nCommit message rejected.\n", file=sys.stderr)
    for problem in problems:
        print(f"  ✗ {problem}", file=sys.stderr)
    print(
        "\nExpected:\n"
        "  <type>(<scope>): <subject>\n\n"
        "  <body: what changed and why>\n\n"
        "  PRD: <I2, G3, §5.1>   or   PRD: n/a — <reason>\n\n"
        f"  types:  {', '.join(TYPES)}\n"
        f"  scopes: {', '.join(SCOPES)}\n\n"
        f"Received header: {header!r}\n"
        "See .claude/commands/commit.md\n",
        file=sys.stderr,
    )
    return 1


def main(argv: list[str]) -> int:
    if len(argv) < 2:
        return 0
    raw = Path(argv[1]).read_text(encoding="utf-8")
    lines = [line for line in raw.splitlines() if not line.startswith("#")]
    while lines and not lines[0].strip():
        lines.pop(0)
    if not lines:
        return 0

    header = lines[0]
    if header.startswith(EXEMPT_PREFIXES):
        return 0

    problems: list[str] = []

    match = HEADER.match(header)
    if not match:
        problems.append("header does not match '<type>(<scope>): <subject>'")
    else:
        scope = match.group("scope")
        subject = match.group("subject")
        if scope and scope not in SCOPES:
            problems.append(f"unknown scope '{scope}'")
        if len(header) > 72:
            problems.append(f"header is {len(header)} chars, keep it under 72")
        if subject[:1].isupper():
            problems.append("subject should start lowercase")
        if subject.endswith("."):
            problems.append("subject should not end with a period")

    trailers = [line for line in lines if line.startswith("PRD:")]
    if not trailers:
        problems.append("missing 'PRD:' trailer (use 'PRD: n/a — <reason>' if truly unrelated)")
    else:
        for trailer in trailers:
            if not (PRD_REFS.match(trailer) or PRD_NA.match(trailer)):
                problems.append(
                    f"malformed trailer {trailer!r}; expected IDs (I2, G3, §5.1) "
                    "or 'n/a — <reason>'"
                )

    if ATTRIBUTION.search(raw):
        problems.append("AI attribution trailers are not allowed")

    return fail(problems, header) if problems else 0


if __name__ == "__main__":
    sys.exit(main(sys.argv))
