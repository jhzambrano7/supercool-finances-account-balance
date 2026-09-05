#!/usr/bin/env python3
"""Rebuild docs/ai-transcript.md from the Claude Code session logs.

The tech challenge requires *every* prompt and *every* response to be delivered
verbatim. A model-written summary cannot satisfy that: it omits, it compresses,
and it silently drops turns that produced no answer. So this artifact is never
written by the model -- it is derived mechanically.

Storage model (mirrors the ledger design in docs/prd.md 5.1):

    session JSONL  ->  docs/.transcript-store.jsonl  ->  docs/ai-transcript.md
    (external,          (append-only ledger,               (materialized
     ephemeral)          IN the repo, versioned)            projection)

Why the middle layer exists. The session logs live under ~/.claude/projects/,
outside the repository, and Claude Code prunes them on a retention schedule
(`cleanupPeriodDays`, 30 days by default). Rendering the deliverable directly
from them means that once they are pruned, a rebuild silently overwrites a
complete transcript with a shorter one. A full rebuild over an eroding source is
destructive.

So turns are accumulated into an append-only store inside the repo, keyed by the
turn's stable uuid. Each run merges newly observed turns into it and never
removes one. The store is versioned by git, so the transcript stays reproducible
from the repository alone -- no dependency on machine-local session history.

Compaction and /clear are safe by construction: compaction only affects the
model's context window, /clear starts a new session file, and every file in the
project directory is globbed and merged by timestamp.

Run with no arguments; it discovers the session logs for this project.
"""

from __future__ import annotations

import hashlib
import json
import os
import re
import sys
from pathlib import Path

# Only these entry kinds can contribute a turn. Checking for the substring
# before paying for json.loads skips the bulk of a session file (attachments,
# tool results, file snapshots) without parsing it.
_RELEVANT = ('"type":"user"', '"type":"assistant"')

# Blocks injected by hooks/attachments that were never typed by the user.
_NOISE = re.compile(
    r"<(system-reminder|local-command-caveat|local-command-stdout|decision-log)>.*?"
    r"</\1>\s*",
    re.DOTALL,
)
_SLASH = re.compile(
    r"<command-name>\s*(?P<name>[^<]*?)\s*</command-name>.*?"
    r"(?:<command-args>\s*(?P<args>[^<]*?)\s*</command-args>)?",
    re.DOTALL,
)

# Per tool, the input field worth showing in the tool-call summary.
_TOOL_HINT = (
    "file_path", "command", "query", "pattern", "path", "url", "prompt", "skill",
)


def project_slug(project_dir: Path) -> str:
    """Claude Code stores sessions under a dash-flattened absolute path."""
    return str(project_dir.resolve()).replace("/", "-")


def session_files(project_dir: Path) -> list[Path]:
    root = Path.home() / ".claude" / "projects" / project_slug(project_dir)
    if not root.is_dir():
        return []
    return sorted(root.glob("*.jsonl"))


def read_entries(path: Path) -> list[dict]:
    entries = []
    with path.open(encoding="utf-8", errors="replace") as fh:
        for line in fh:
            line = line.strip()
            if not line or not any(marker in line for marker in _RELEVANT):
                continue
            try:
                entries.append(json.loads(line))
            except json.JSONDecodeError:
                continue  # a partially flushed final line; skip it
    return entries


def load_scan_cache(path: Path) -> dict[str, list]:
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return {}


def changed_files(files: list[Path], cache: dict[str, list]) -> list[Path]:
    """Only re-read session files whose size or mtime moved.

    Turns already live in the in-repo store, so skipping an untouched file
    cannot lose anything: in a normal turn exactly one session file has grown.
    """
    pending = []
    for path in files:
        try:
            info = path.stat()
        except OSError:
            continue
        seen = cache.get(str(path))
        if seen != [int(info.st_mtime_ns), info.st_size]:
            pending.append(path)
    return pending


def save_scan_cache(path: Path, files: list[Path]) -> None:
    snapshot = {}
    for item in files:
        try:
            info = item.stat()
        except OSError:
            continue
        snapshot[str(item)] = [int(info.st_mtime_ns), info.st_size]
    try:
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(json.dumps(snapshot), encoding="utf-8")
    except OSError:
        pass  # a stale cache only costs a re-read, never correctness


def clean_prompt(text: str) -> str:
    return _NOISE.sub("", text).strip()


def as_slash_command(text: str) -> str | None:
    match = _SLASH.search(text)
    if not match or "<command-name>" not in text:
        return None
    name = (match.group("name") or "").strip()
    args = (match.group("args") or "").strip()
    return f"{name} {args}".strip() or None


def tool_summary(block: dict) -> str:
    name = block.get("name", "tool")
    payload = block.get("input") or {}
    if isinstance(payload, dict):
        for key in _TOOL_HINT:
            value = payload.get(key)
            if isinstance(value, str) and value.strip():
                flat = " ".join(value.split())
                if len(flat) > 140:
                    flat = flat[:140] + "…"
                return f"`{name}` — {flat}"
    return f"`{name}`"


def collect_turns(entries: list[dict]) -> list[dict]:
    """Pair each real user prompt with the assistant output that followed it."""
    turns: list[dict] = []
    current: dict | None = None

    for entry in entries:
        if entry.get("isSidechain"):
            continue  # subagent traffic, not a user/assistant exchange
        kind = entry.get("type")
        message = entry.get("message") or {}

        if kind == "user":
            # Tool results and meta entries are user-role but are not prompts.
            if entry.get("toolUseResult") is not None or entry.get("isMeta"):
                continue
            content = message.get("content")
            if not isinstance(content, str):
                continue
            command = as_slash_command(content)
            body = command if command else clean_prompt(content)
            if not body:
                continue
            # Identity must be stable ACROSS PROCESSES: the store is keyed by it.
            # Python's builtin hash() is seed-randomized per interpreter run and
            # would mint a new key every time, duplicating turns forever.
            fallback = hashlib.sha1(
                f"{entry.get('timestamp', '')}:{body}".encode("utf-8")
            ).hexdigest()
            current = {
                "uuid": entry.get("uuid") or fallback,
                "timestamp": entry.get("timestamp", ""),
                "prompt": body,
                "is_command": bool(command),
                "response": [],
                "tools": [],
            }
            turns.append(current)

        elif kind == "assistant" and current is not None:
            for block in message.get("content") or []:
                if not isinstance(block, dict):
                    continue
                # "thinking" blocks are internal reasoning, not the delivered
                # response; excluded on purpose and disclosed in the header.
                if block.get("type") == "text":
                    text = (block.get("text") or "").strip()
                    if text:
                        current["response"].append(text)
                elif block.get("type") == "tool_use":
                    current["tools"].append(tool_summary(block))

    return turns


def load_store(path: Path) -> dict[str, dict]:
    """Read the in-repo append-only turn store, keyed by turn uuid."""
    stored: dict[str, dict] = {}
    if not path.is_file():
        return stored
    with path.open(encoding="utf-8", errors="replace") as fh:
        for line in fh:
            line = line.strip()
            if not line:
                continue
            try:
                turn = json.loads(line)
            except json.JSONDecodeError:
                continue
            key = turn.get("uuid")
            if key:
                stored[key] = turn
    return stored


def merge_into_store(stored: dict[str, dict], observed: list[dict]) -> list[dict]:
    """Union stored turns with freshly observed ones. Never drops a stored turn.

    A turn already in the store is refreshed only when the new observation is at
    least as complete, so a truncated or partially flushed read can never erase
    a response that was already captured.
    """
    for turn in observed:
        key = turn["uuid"]
        previous = stored.get(key)
        if previous is None:
            stored[key] = turn
            continue
        richer = (
            len(turn.get("response") or []) >= len(previous.get("response") or [])
            and len(turn.get("tools") or []) >= len(previous.get("tools") or [])
        )
        if richer:
            stored[key] = turn

    return sorted(stored.values(), key=lambda item: (item.get("timestamp", ""),
                                                     item.get("uuid", "")))


def write_store(path: Path, turns: list[dict]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    payload = "\n".join(
        json.dumps(turn, ensure_ascii=False, sort_keys=True) for turn in turns
    )
    path.write_text(payload + "\n", encoding="utf-8")


def render(turns: list[dict]) -> str:
    out: list[str] = [
        "# AI Transcript",
        "",
        "Complete, verbatim record of every prompt given to the AI assistant and every",
        "response it returned, in chronological order — as required by the challenge",
        "instructions (*\"you must provide each and every prompt you used along with",
        "every response\"*).",
        "",
        "This file is **generated**, never hand-written. It is rebuilt by",
        "`.claude/hooks/ai-transcript-build.py` from `.transcript-store.jsonl`, an",
        "append-only store kept in this repository and versioned by git. Session logs",
        "only ever *add* turns to that store — nothing is ever removed — so the",
        "transcript is reproducible from the repository alone, and neither context",
        "compaction, nor `/clear`, nor the expiry of machine-local session history can",
        "shorten it. Do not edit by hand — edits are overwritten on the next run.",
        "",
        "Notes on what is included:",
        "",
        "- Prompts and responses are reproduced **verbatim**, untouched.",
        "- Tool calls the assistant made are listed per turn, to show the actual work.",
        "- Internal reasoning (*thinking*) blocks are **excluded**: they are not part of",
        "  the delivered response. The reasoning behind each decision is documented in",
        "  [decision-log.md](./decision-log.md) and [prd.md](./prd.md).",
        "- Text injected automatically by tooling (system reminders, hook output) is",
        "  stripped, since it was not written by the user.",
        "",
        f"**Turns recorded:** {len(turns)}",
        "",
        "---",
        "",
    ]

    for index, turn in enumerate(turns, start=1):
        stamp = turn["timestamp"].replace("T", " ").replace("Z", " UTC")
        label = "Slash command" if turn["is_command"] else "Prompt"
        out.append(f"## Turn {index} — {stamp}")
        out.append("")
        out.append(f"### {label}")
        out.append("")
        out.append("```text")
        out.append(turn["prompt"])
        out.append("```")
        out.append("")

        if turn["tools"]:
            out.append("<details><summary>Tool calls "
                       f"({len(turn['tools'])})</summary>")
            out.append("")
            for line in turn["tools"]:
                out.append(f"- {line}")
            out.append("")
            out.append("</details>")
            out.append("")

        out.append("### Response")
        out.append("")
        if turn["response"]:
            out.append("\n\n".join(turn["response"]))
        else:
            out.append("*(No response was produced for this prompt — the turn was "
                       "interrupted or the prompt was re-submitted.)*")
        out.append("")
        out.append("---")
        out.append("")

    return "\n".join(out).rstrip() + "\n"


def main() -> int:
    project_dir = Path(os.environ.get("CLAUDE_PROJECT_DIR", Path.cwd()))
    store_path = project_dir / "docs" / ".transcript-store.jsonl"
    cache_path = project_dir / ".claude" / ".transcript-scan-cache.json"
    target = project_dir / "docs" / "ai-transcript.md"

    # The in-repo store is the authority. Session logs only ever add to it.
    stored = load_store(store_path)

    files = session_files(project_dir)
    cache = load_scan_cache(cache_path)
    pending = changed_files(files, cache)

    # Nothing moved on disk and the projection exists: no work to do.
    if not pending and target.is_file() and stored:
        return 0

    entries: list[dict] = []
    for path in pending:
        entries.extend(read_entries(path))
    entries.sort(key=lambda item: item.get("timestamp", ""))  # across sessions

    turns = merge_into_store(stored, collect_turns(entries))
    if not turns:
        return 0

    write_store(store_path, turns)
    save_scan_cache(cache_path, files)

    target.parent.mkdir(parents=True, exist_ok=True)
    target.write_text(render(turns), encoding="utf-8")
    return 0


if __name__ == "__main__":
    try:
        sys.exit(main())
    except Exception as error:  # never let this break the session
        print(f"ai-transcript-build: {error}", file=sys.stderr)
        sys.exit(0)
