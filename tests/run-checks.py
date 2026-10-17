#!/usr/bin/env python3
"""Run ground checks (search / red / reduce commands) in Maude test files.

Usage: python3 run-checks.py FILE...

Each FILE is run by Maude from its own directory, so its relative loads
(for example "load ../src/main") resolve as they do in the source tree.
Paths may be relative to the current directory or absolute.

A command starts at the beginning of a line with "search", "red" or "reduce"
(indentation is not allowed, so comment lines never match). Its result is
expected to be:
  search         "Solution N (state M)"  (a "No solution." result fails)
  red, reduce    "result Bool: true"     (any other result fails)

A file fails when Maude writes to stderr, prints "Warning:" or "Error:", or
the number of results differs from the number of commands. A failing file
lists the line numbers of the commands whose result is wrong.

Output: one "[checks] FILE: passed/total" line per file. Exit status is 0 only
if every file passes. The Maude binary is taken from MAUDE_BIN (default: maude
on PATH).
"""

from __future__ import annotations

import argparse
import os
from pathlib import Path
import re
import subprocess
import sys


COMMAND = re.compile(r"^(search|red|reduce)\b")
SEARCH_RESULT = re.compile(r"^(Solution \d+ \(state \d+\)|No solution\.)$", re.MULTILINE)
REDUCE_RESULT = re.compile(r"^result .*$", re.MULTILINE)
DIAGNOSTIC = re.compile(r"^(Warning|Error):", re.MULTILINE)


class CheckError(RuntimeError):
    pass


def commands(lines: list[str]) -> list[tuple[int, str, str]]:
    """Return (line number, keyword, first line) for each command."""
    found = []
    for number, line in enumerate(lines, start=1):
        match = COMMAND.match(line)
        if match:
            found.append((number, match.group(1), line.rstrip()))
    return found


def run_file(path: Path, maude: str) -> bool:
    text = path.read_text(encoding="utf-8")
    cmds = commands(text.splitlines())
    try:
        # Maude resolves a relative file argument through $PWD, which subprocess
        # does not update when cwd= is given, so PWD is set explicitly.
        env = dict(os.environ, PWD=str(path.parent))
        result = subprocess.run(
            [maude, "-no-banner", "-no-advise", path.name],
            cwd=path.parent, env=env, stdin=subprocess.DEVNULL,
            capture_output=True, text=True, encoding="utf-8",
        )
    except OSError as error:
        raise CheckError("cannot run Maude '{}': {}".format(maude, error))

    problems = []
    if result.stderr.strip():
        problems.append("stderr: " + result.stderr.strip().replace("\n", "\n    "))
    diagnostics = DIAGNOSTIC.findall(result.stdout)
    if diagnostics:
        problems.append("Maude printed {} Warning/Error line(s)".format(len(diagnostics)))

    # Results appear in command order. Search results and reduce results are
    # matched with the command's own keyword.
    searches = [c for c in cmds if c[1] == "search"]
    reduces = [c for c in cmds if c[1] != "search"]
    search_out = SEARCH_RESULT.findall(result.stdout)
    reduce_out = REDUCE_RESULT.findall(result.stdout)
    if len(search_out) != len(searches) or len(reduce_out) != len(reduces):
        problems.append(
            "result count mismatch: {} search results for {} search commands, "
            "{} red/reduce results for {} red/reduce commands".format(
                len(search_out), len(searches), len(reduce_out), len(reduces))
        )
        results = None
    else:
        results = {}
        for (number, _, line), out in zip(searches, search_out):
            results[number] = (line, out.startswith("Solution"))
        for (number, _, line), out in zip(reduces, reduce_out):
            results[number] = (line, out == "result Bool: true")

    if results is not None:
        failing = [number for number, (_, ok) in sorted(results.items()) if not ok]
        for number in failing:
            problems.append("failing command at line {}: {}".format(
                number, results[number][0].strip()))

    total = len(cmds)
    passed = total - (len(failing) if results is not None else total)
    print("[checks] {}: {}/{}".format(path.name, passed, total))
    for problem in problems:
        print("    " + problem)
    return not problems


def main() -> int:
    parser = argparse.ArgumentParser(
        description="Run search/red/reduce ground checks in Maude files.",
        epilog=(
            "Each FILE runs from its own directory. Commands must start at column 0. "
            "Exit status 0 means every file passed; otherwise 1."
        ),
    )
    parser.add_argument("files", nargs="+", metavar="FILE", help="Maude check files")
    args = parser.parse_args()

    maude = os.environ.get("MAUDE_BIN") or "maude"
    status = 0
    for name in args.files:
        path = Path(name).resolve()
        if not path.is_file():
            print("[checks] {}: not a file".format(name))
            status = 1
            continue
        try:
            if not run_file(path, maude):
                status = 1
        except CheckError as error:
            print("[checks] {}: error ({})".format(path.name, error))
            status = 1
    return status


if __name__ == "__main__":
    sys.exit(main())
