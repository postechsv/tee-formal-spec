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
import shutil
import subprocess
import sys
import threading


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


def run_maude(maude: str, path: Path, verbose: bool) -> tuple[str, str]:
    """Run Maude on the file path (from its directory); return (stdout, stderr).

    With verbose, each stdout line is printed as soon as Maude writes it (stdbuf
    makes the pipe line-buffered where available); the result is the same."""
    # Maude resolves a relative file argument through $PWD, which subprocess
    # does not update when cwd= is given, so PWD is set explicitly.
    env = dict(os.environ, PWD=str(path.parent))
    command = [maude, "-no-banner", "-no-advise", path.name]
    if not verbose:
        result = subprocess.run(
            command, cwd=path.parent, env=env, stdin=subprocess.DEVNULL,
            capture_output=True, text=True, encoding="utf-8",
        )
        return result.stdout, result.stderr
    if shutil.which("stdbuf"):
        command = ["stdbuf", "-oL"] + command
    proc = subprocess.Popen(
        command, cwd=path.parent, env=env, stdin=subprocess.DEVNULL,
        stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True, encoding="utf-8",
    )
    errors: list[str] = []
    # stderr is drained by a thread so Maude cannot block on a full pipe.
    reader = threading.Thread(target=lambda: errors.append(proc.stderr.read()), daemon=True)
    reader.start()
    lines: list[str] = []
    for line in proc.stdout:
        print(line, end="", flush=True)
        lines.append(line)
    proc.wait()
    reader.join()
    return "".join(lines), errors[0] if errors else ""


def run_file(path: Path, maude: str, verbose: bool = False) -> bool:
    text = path.read_text(encoding="utf-8")
    cmds = commands(text.splitlines())
    if verbose:
        print("== {}: {} commands ==".format(path.name, len(cmds)), flush=True)
    try:
        stdout, stderr = run_maude(maude, path, verbose)
    except OSError as error:
        raise CheckError("cannot run Maude '{}': {}".format(maude, error))

    problems = []
    if stderr.strip():
        problems.append("stderr: " + stderr.strip().replace("\n", "\n    "))
    diagnostics = DIAGNOSTIC.findall(stdout)
    if diagnostics:
        problems.append("Maude printed {} Warning/Error line(s)".format(len(diagnostics)))

    # Results appear in command order. Search results and reduce results are
    # matched with the command's own keyword.
    searches = [c for c in cmds if c[1] == "search"]
    reduces = [c for c in cmds if c[1] != "search"]
    search_out = SEARCH_RESULT.findall(stdout)
    reduce_out = REDUCE_RESULT.findall(stdout)
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
    parser.add_argument("-v", "--verbose", action="store_true",
                        help="print the Maude output of each file as it runs")
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
            if not run_file(path, maude, args.verbose):
                status = 1
        except CheckError as error:
            print("[checks] {}: error ({})".format(path.name, error))
            status = 1
    return status


if __name__ == "__main__":
    sys.exit(main())
