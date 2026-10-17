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
import threading


ROOT = Path(__file__).resolve().parent.parent
SPEC = ROOT
MAUDE_RELATIVE = (
    Path("Darwin64/maude.darwin64") if platform.system() == "Darwin"
    else Path("Linux64/maude.linux64")
)
EXPECTED_API_TESTS = 92
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


def run_maude(maude: Path, program: str, cwd: Path, env: dict, verbose: bool):
    """Run Maude with program on stdin; return (returncode, stdout, stderr).

    With verbose, each stdout line is printed as soon as Maude writes it (stdbuf
    makes the pipe line-buffered where available); the result is the same."""
    command = [str(maude), "-no-banner", "-no-advise"]
    if not verbose:
        result = subprocess.run(
            command, cwd=cwd, env=env, input=program, text=True, encoding="utf-8",
            stdout=subprocess.PIPE, stderr=subprocess.PIPE,
        )
        return result.returncode, result.stdout, result.stderr
    if shutil.which("stdbuf"):
        command = ["stdbuf", "-oL"] + command
    proc = subprocess.Popen(
        command, cwd=cwd, env=env, text=True, encoding="utf-8",
        stdin=subprocess.PIPE, stdout=subprocess.PIPE, stderr=subprocess.PIPE,
    )
    errors: list[str] = []
    # Pipes are drained by threads so neither Maude nor the program write can block.
    threads = [
        threading.Thread(target=lambda: errors.append(proc.stderr.read()), daemon=True),
        threading.Thread(target=lambda: (proc.stdin.write(program), proc.stdin.close()),
                         daemon=True),
    ]
    for thread in threads:
        thread.start()
    lines: list[str] = []
    for line in proc.stdout:
        print(line, end="", flush=True)
        lines.append(line)
    proc.wait()
    for thread in threads:
        thread.join()
    return proc.returncode, "".join(lines), errors[0] if errors else ""


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
            if args[1].startswith(("success", "error")):
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
    kernel = re.compile(r"(<\s*ta\s*:\s*TAKernel\s*\|.*?)(>\s*\.)", re.DOTALL)
    target, count = kernel.subn(
        r"\1, API-ATTS-{}:AttributeSet \2".format(number), target, count=1
    )
    if count != 1:
        raise TestFailure("API target has no TAKernel assertion")
    target = re.sub(r">\s*\.\s*$", r"> API-CF-{}:Configuration .".format(number), target)
    return source + marker + target


def default_api_files() -> list[Path]:
    return sorted((SPEC / "tests" / "api").glob("test-*.maude"))


def run_api(files: list[Path], check_count: bool, verbose: bool = False) -> int:
    maude = find_maude()
    total = passed = 0
    failures: list[str] = []
    for path in files:
        header, tests = split_searches(path.read_text(encoding="utf-8"))
        header = header.replace("load ./src/main", "load src/main")
        header = header.replace("load ./tests/api/aux", "load tests/api/aux")
        total += len(tests)
        commands = [api_query(command, index + 1) for index, (_, command) in enumerate(tests)]
        program = header + "\n".join(commands)
        if verbose:
            print("== {}: {} searches ==".format(path.name, len(tests)), flush=True)
        returncode, stdout, stderr = run_maude(
            maude, program, ROOT,
            # Maude resolves relative loads through $PWD, which cwd= does not update.
            dict(os.environ, PWD=str(ROOT)), verbose,
        )
        outcomes = re.findall(r"^(Solution 1 \(state \d+\)|No solution\.)", stdout,
                              re.MULTILINE)
        if returncode != 0 or stderr.strip():
            raise TestFailure("{}: Maude failed: {}".format(path.name, stderr.strip()))
        if len(outcomes) != len(tests):
            raise TestFailure("{}: expected {} outcomes, found {}".format(
                path.name, len(tests), len(outcomes)))
        for (test_id, _), outcome in zip(tests, outcomes):
            if outcome.startswith("Solution 1"):
                passed += 1
            else:
                failures.append(test_id)
    if check_count and total != EXPECTED_API_TESTS:
        raise TestFailure(
            "API test count is {}, expected {}".format(total, EXPECTED_API_TESTS)
        )
    print("[test-suite] API-level: {}/{} pass".format(passed, total))
    if failures:
        raise TestFailure("API failures: {}".format(", ".join(failures)))
    return total


def main() -> int:
    parser = argparse.ArgumentParser(
        description="Run the API-level black-box tests (tests/api/test-*.maude).",
        epilog=(
            "FILE arguments: paths to test-*.maude files, relative to the current "
            "directory or absolute. Each file must be a test file of this tree "
            "(its header loads src/main from the tree root). With no FILE, all "
            "of tests/api is run and the total is checked against 92. With FILE, "
            "only the listed files run and the total check is skipped. "
            "The runner uses MAUDE_BIN for the Maude binary."
        ),
    )
    parser.add_argument("-v", "--verbose", action="store_true",
                        help="print the Maude output of each file as it runs")
    parser.add_argument("files", nargs="*", metavar="FILE",
                        help="API test files to run (default: all of tests/api)")
    args = parser.parse_args()
    try:
        if args.files:
            files = [Path(name).resolve() for name in args.files]
            for path in files:
                if not path.is_file():
                    raise TestFailure("not a file: {}".format(path))
            run_api(files, check_count=False, verbose=args.verbose)
        else:
            run_api(default_api_files(), check_count=True, verbose=args.verbose)
        return 0
    except TestFailure as error:
        print("test suite: fail ({})".format(error), file=sys.stderr)
        return 1


if __name__ == "__main__":
    sys.exit(main())
