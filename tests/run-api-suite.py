#!/usr/bin/env python3
"""Run the paper-derived API-level black-box tests."""

from __future__ import annotations

import argparse
import os
from pathlib import Path
import platform
import re
import shutil
import subprocess
import sys


ROOT = Path(__file__).resolve().parent.parent
SPEC = ROOT
MAUDE_RELATIVE = (
    Path("Darwin64/maude.darwin64") if platform.system() == "Darwin"
    else Path("Linux64/maude.linux64")
)
EXPECTED_API_TESTS = 91
SEARCH_START = re.compile(r"^search\s+\[1\]\s+in\s+TEST-AUX\s*:", re.MULTILINE)
TEST_ID = re.compile(r"---\s*\[([^]]+)]")


class TestFailure(RuntimeError):
    pass


def find_maude() -> Path:
    configured = os.environ.get("MAUDE_BIN")
    candidates = [
        Path(configured).expanduser() if configured else None,
        ROOT.parent / "tee-formal-analysis" / "3rd_party" / MAUDE_RELATIVE,
        ROOT.parent / "zenodo" / "tee-formal-analysis" / "3rd_party" / MAUDE_RELATIVE,
    ]
    installed = shutil.which("maude")
    if installed:
        candidates.append(Path(installed))
    for candidate in candidates:
        if candidate is not None and candidate.is_file():
            return candidate.resolve()
    raise TestFailure(
        "Maude executable not found; set MAUDE_BIN or install Maude on PATH"
    )


def split_searches(text: str) -> tuple[str, list[tuple[str, str]]]:
    starts = list(SEARCH_START.finditer(text))
    if not starts:
        return text, []
    header = text[:starts[0].start()]
    tests: list[tuple[str, str]] = []
    for index, start in enumerate(starts):
        end = starts[index + 1].start() if index + 1 < len(starts) else len(text)
        block = text[start.start():end]
        command_end = re.search(r"\n\s*\.\s*(?:\n|$)", block)
        if command_end is None:
            # In these sources the terminating period follows the final term.
            command_end = re.search(r"\s\.\s*(?:\n|$)", block)
        if command_end is None:
            raise TestFailure("cannot find end of search command")
        command = block[:command_end.end()]
        preceding = text[max(0, start.start() - 240):start.start()]
        labels = TEST_ID.findall(preceding)
        test_id = labels[-1] if labels else "unlabelled-{}".format(index + 1)
        tests.append((test_id, command))
    return header, tests


def matching_paren(text: str, opening: int) -> int:
    depth = 0
    for index in range(opening, len(text)):
        if text[index] == "(":
            depth += 1
        elif text[index] == ")":
            depth -= 1
            if depth == 0:
                return index
    raise TestFailure("unbalanced apiReturn expression")


def top_level_arguments(text: str) -> list[str]:
    args, depth, start = [], 0, 0
    for index, char in enumerate(text):
        if char == "(":
            depth += 1
        elif char == ")":
            depth -= 1
        elif char == "," and depth == 0:
            args.append(text[start:index].strip())
            start = index + 1
    args.append(text[start:].strip())
    return args


def canonical_returns(target: str) -> str:
    cursor = 0
    while True:
        found = target.find("apiReturn(", cursor)
        if found < 0:
            return target
        opening = found + len("apiReturn")
        closing = matching_paren(target, opening)
        args = top_level_arguments(target[opening + 1:closing])
        if len(args) == 1:
            args.extend(("noReturnCode", "empty"))
        elif len(args) == 2:
            if args[1].startswith(("TEE-SUCCESS", "TEE-ERROR")):
                args.append("empty")
            else:
                args.insert(1, "noReturnCode")
        replacement = "apiReturn({})".format(" , ".join(args))
        target = target[:found] + replacement + target[closing + 1:]
        cursor = found + len(replacement)


def api_query(command: str, number: int) -> str:
    marker = "=>*"
    if marker not in command:
        raise TestFailure("API test does not use =>*")
    source, target = command.split(marker, 1)
    target = canonical_returns(target)
    # Object patterns list only the attributes relevant to the assertion.  Make
    # the omitted attributes explicit, as required by configuration matching.
    kernel = re.compile(r"(<\s*ta\s*:\s*TaKernel\s*\|.*?)(>\s*\.)", re.DOTALL)
    target, count = kernel.subn(
        r"\1, API-ATTS-{}:AttributeSet \2".format(number), target, count=1
    )
    if count != 1:
        raise TestFailure("API target has no TaKernel assertion")
    target = re.sub(r">\s*\.\s*$", r"> API-CF-{}:Configuration .".format(number), target)
    return source + marker + target


def source_files(suite: str) -> list[Path]:
    directory = SPEC / "tests" / suite
    return sorted(directory.glob("test-*.maude"))


def run_api() -> int:
    maude = find_maude()
    total = passed = 0
    failures: list[str] = []
    for path in source_files("api"):
        header, tests = split_searches(path.read_text(encoding="utf-8"))
        header = header.replace("load ./src/main", "load src/main")
        header = header.replace("load ./tests/api/aux", "load tests/api/aux")
        total += len(tests)
        commands = [api_query(command, index + 1) for index, (_, command) in enumerate(tests)]
        program = header + "\n".join(commands)
        result = subprocess.run(
            [str(maude), "-no-banner", "-no-advise"], cwd=ROOT,
            input=program, text=True, encoding="utf-8",
            stdout=subprocess.PIPE, stderr=subprocess.PIPE,
        )
        outcomes = re.findall(r"^(Solution 1 \(state \d+\)|No solution\.)", result.stdout,
                              re.MULTILINE)
        if result.returncode != 0 or result.stderr.strip():
            raise TestFailure("{}: Maude failed: {}".format(path.name, result.stderr.strip()))
        if len(outcomes) != len(tests):
            raise TestFailure("{}: expected {} outcomes, found {}".format(
                path.name, len(tests), len(outcomes)))
        for (test_id, _), outcome in zip(tests, outcomes):
            if outcome.startswith("Solution 1"):
                passed += 1
            else:
                failures.append(test_id)
    if total != EXPECTED_API_TESTS:
        raise TestFailure(
            "API test count is {}, expected {}".format(total, EXPECTED_API_TESTS)
        )
    print("[test-suite] API-level: {}/{} pass".format(passed, total))
    if failures:
        raise TestFailure("API failures: {}".format(", ".join(failures)))
    return total


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("suite", nargs="?", choices=("api",), default="api")
    args = parser.parse_args()
    try:
        run_api()
        return 0
    except TestFailure as error:
        print("test suite: fail ({})".format(error), file=sys.stderr)
        return 1


if __name__ == "__main__":
    sys.exit(main())
