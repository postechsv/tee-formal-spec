#!/usr/bin/env python3
"""Generate docs/examples.json: Maude-verified examples for rules and equations.

Sources are the search and red commands of tests/api, tests/completion,
tests/gp-conformance and the seed files in docs/example-seeds (--seeds). Each
file is run with its own non-command lines (module declarations, loads, set
commands), in the repository root. Search commands of tests/api and of the seed
files are first completed as tests/run-api-suite.py does (api_query).

Rules: a solution path of each search gives steps (state before, rule label,
state after). The path is taken with "show path N ." where N is the state number
of the solution that the search prints ("Solution 1 (state N)"), so the whole
path is shown, not only its first state. Candidates are checked again with
"search [1] in MOD : before =>1 after ." and "show path labels 1 .", which must
name the same label. Terms are printed by Maude and used as they are printed.
If that re-parse does not confirm a step (for example because the printed
IMP program does not parse back to the same term), the step is kept when the
original search, run again, gives "show path labels N ." with exactly the label
sequence of its path; then the example is taken from that real path and
check_module is the module of that search.

Operators: "set trace eqs on" during the same searches gives equation
applications (equation, instance of its left-hand side, result). The top
operator of the instance selects the op. Candidates are checked with
"red in MOD : input ." and "red in MOD : result .", whose normal forms must
coincide; the output is that normal form. Red commands of the test files also
give candidates (their input, or the sides of "==" and the argument of "not",
with the top operator of each term); for these the output is the normal form
of the input from Maude.

Only verified examples are written (verified is always true in the file). A
term longer than 4000 characters is not used. Rule labels that occur more
than once in src/ are keyed "label@file:line"; a step is attributed to such a
key only when exactly one copy matches the step's left-hand side: first as a
token sequence, then by its words (variables, punctuation, sort names and the
kernel attributes Maude adds are ignored), then by its words being contained in
those of the step.

Output schema (docs/examples.json):
{
  "generated_by": "tools/gen-examples.py",
  "maude_version": "<version from the Maude banner>",
  "rules": {
    "<label or label@file:line>": [
      {"file": "src/x.maude", "line": 62, "module": "MOD",
       "check_module": "MOD2", "before": "<configuration>",
       "after": "<configuration>",
       "source": "tests/api/test-x.maude [BB-ID]", "verified": true}
    ]
  },
  "ops": {
    "<MODULE>/<op name as declared>": [
      {"file": "src/x.maude", "line": 10, "module": "MOD",
       "input": "<red term>", "output": "<normal form>",
       "source": "tests/completion/x.maude:12", "verified": true}
    ]
  },
  "missing": {"rules": ["<key>", ...], "ops": ["<MODULE>/<op>", ...]}
}
"module" is the module in which the rule is declared (src/). "check_module" (rule
entries only) is the module in which the verifying search ran: the module of the
search command in the test or seed file that produced the step. "source" is
"<test file> [<test id>]" when the test has a "--- [id]" comment, and
"<test file>:<line>" otherwise. "missing" lists the rule keys and equation-defined
ops (those with at least one equation in src/) without a verified example.

Usage: python3 tools/gen-examples.py [--maude PATH] [--out docs/examples.json]
                                     [--seeds DIR] [--cache DIR] [--timeout SECONDS]
                                     [--jobs N] [--verbose]
The Maude binary defaults to MAUDE in config.mk. --seeds names the directory of
seed files (default docs/example-seeds; "" disables them). Seed files are
ordinary Maude files whose search commands drive rules that the test files
never reach; each must load and finish quickly. Only the standard library is
used. With --cache, the raw Maude outputs of the test runs are kept in DIR;
they are reused while src/, tests/ and the seed files are unchanged. --verbose
lists the steps that were reached but not verified.
"""

from __future__ import annotations

import argparse
import concurrent.futures
from collections import Counter
import hashlib
import json
import os
from pathlib import Path
import re
import subprocess
import sys


ROOT = Path(__file__).resolve().parent.parent
SRC = ROOT / "src"
TEST_GLOBS = ("tests/api/test-*.maude", "tests/completion/*.maude",
              "tests/gp-conformance/*.maude")
SEEDS_DIR = ROOT / "docs" / "example-seeds"
MAX_TERM = 4000
CANDIDATES_PER_KEY = 2       # verified examples kept per rule or op
TRIES_PER_KEY = 6            # candidates checked per rule or op
ARG = object()               # mixfix argument in an operator pattern
OPEN = {"(", "[", "{"}
CLOSE = {")", "]", "}"}
VAR_TOKEN = re.compile(r"[A-Za-z][\w'\-]*:[A-Za-z][\w\-]*")
TOKEN = re.compile(r"[(),\[\]{}]|[^\s(),\[\]{}]+")
WORD = re.compile(r"[A-Za-z][\w\-']*")
MODULE_HEAD = re.compile(r"^\s*(?:o?mod|fmod|o?th|view)\s+(\S+)")
MODULE_END = re.compile(r"^\s*(?:endm|endfm|endom|endth|endv|endfth|endoth)\b")
STATEMENT_START = re.compile(r"^\s*(c?rl|c?eq|ops?|vars?)\b")
RULE_HEAD = re.compile(r"^\s*c?rl\s*\[([^\]]+)\]\s*:\s*")
EQ_HEAD = re.compile(r"^\s*c?eq\s*(?:\[[^\]]*\]\s*:)?\s*")
COMMAND_START = re.compile(r"^(search|red|reduce)\b")
SEARCH_MODULE = re.compile(r"^search\s*(?:\[\d+\]\s*)?in\s+(\S+)\s*:")
RED_MODULE = re.compile(r"^(?:red|reduce)\s+in\s+(\S+)\s*:\s*(.*)\s\.\s*$", re.DOTALL)
LOAD_LINE = re.compile(r"^load\s+(\S+)")
TEST_ID = re.compile(r"---\s*\[([^]]+)]")
SEARCH_MARK = re.compile(r"^(Solution \d+ \(state \d+\)|No solution\.)$", re.MULTILINE)
SOLUTION_STATE = re.compile(r"^Solution \d+ \(state (\d+)\)$")
STATE_HEAD = re.compile(r"^state \d+, (?:Object|Configuration): ", re.MULTILINE)
RULE_HEAD_TRACE = re.compile(r"===\[ (.*?) \]===>", re.DOTALL)
STATE_END = re.compile(r"^(?:={3,}|\*{3,}|states:|rewrites:|Solution|No solution|Bye\.|result |"
                       r"Warning:|Error:)", re.MULTILINE)
BIND_LINE = re.compile(r"^\S+ --> ")


# ---------------------------------------------------------------- source scan

def strip_comment(line: str) -> str:
    match = re.search(r"(?:^|\s)(---|\*\*\*)(?=\s|$)", line)
    return line[:match.start()] if match else line


def top_split(text: str, separator: str) -> int:
    """Index of the first separator outside brackets, or -1."""
    depth = 0
    for index, char in enumerate(text):
        if char in "([{":
            depth += 1
        elif char in ")]}":
            depth -= 1
        elif depth == 0 and text.startswith(separator, index):
            return index
    return -1


def tokens(text: str) -> list[str]:
    return TOKEN.findall(text)


def canonical(toks: list[str], variables: set[str]) -> tuple[str, ...]:
    """Tokens with variables replaced by $V and sort annotations (": Sort") dropped.

    Maude prints a variable as Name:Sort and writes the sort after a spaced colon
    in the source; dropping sorts makes both forms compare equal."""
    out = []
    index = 0
    while index < len(toks):
        token = toks[index]
        if token == ":":
            index += 2
            continue
        out.append("$V" if (token in variables or VAR_TOKEN.fullmatch(token)) else token)
        index += 1
    return tuple(out)


def words(toks: tuple[str, ...]) -> tuple[str, ...]:
    """The sorted word tokens (no variables, no punctuation) of a canonical left-hand side.

    Maude prints a rule's left-hand side with extra attributes of the kernel (for example
    Atts:AttributeSet) that the source does not write; the words still identify the rule."""
    return tuple(sorted(t for t in toks if WORD.fullmatch(t)))


def name_pattern(name: str) -> list:
    pieces = re.findall(r"_|[(),\[\]{}]|[^\s_(),\[\]{}]+", name)
    return [ARG if piece == "_" else piece for piece in pieces]


def balanced(toks, start: int, end: int) -> bool:
    depth = 0
    for index in range(start, end):
        if toks[index] in OPEN:
            depth += 1
        elif toks[index] in CLOSE:
            depth -= 1
            if depth < 0:
                return False
    return depth == 0


def match_pattern(pattern: list, toks: tuple[str, ...]) -> bool:
    """True if the whole token sequence is an instance of the operator pattern."""
    if ARG not in pattern:
        name = pattern[0]
        if toks == (name,):
            return True
        return (len(toks) > 2 and toks[0] == name and toks[1] == "("
                and toks[-1] == ")" and balanced(toks, 1, len(toks)))

    def go(pattern_index: int, token_index: int) -> bool:
        if pattern_index == len(pattern):
            return token_index == len(toks)
        piece = pattern[pattern_index]
        if piece is ARG:
            depth = 0
            for end in range(token_index, len(toks)):
                if toks[end] in OPEN:
                    depth += 1
                elif toks[end] in CLOSE:
                    depth -= 1
                    if depth < 0:
                        return False
                if depth == 0 and go(pattern_index + 1, end + 1):
                    return True
            return False
        return (token_index < len(toks) and toks[token_index] == piece
                and go(pattern_index + 1, token_index + 1))

    return go(0, 0)


class OpIndex:
    """Operators declared in src/, matched against terms by their token patterns."""

    def __init__(self) -> None:
        self.entries: list[dict] = []

    def add(self, name: str, module: str, path: str, line: int) -> None:
        self.entries.append({"name": name, "module": module, "file": path, "line": line,
                             "pattern": name_pattern(name),
                             "key": "{}/{}".format(module, name)})

    def top(self, toks: tuple[str, ...], module: str | None) -> dict | None:
        best = None
        best_score = None
        for entry in self.entries:
            pattern = entry["pattern"]
            if not pattern:
                continue
            if pattern[0] is not ARG and (not toks or toks[0] != pattern[0]):
                continue
            if not match_pattern(pattern, toks):
                continue
            literals = [piece for piece in pattern if piece is not ARG]
            # Words (supported, not_) outrank punctuation-only patterns such as
            # _(_), which would also match supported(x).
            words = sum(1 for piece in literals if re.search(r"[A-Za-z0-9]", piece))
            score = (words, len(literals), entry["module"] == module)
            if best_score is None or score > best_score:
                best, best_score = entry, score
        return best


def scan_src(src_dir: Path):
    """Return (rules, equation-defined op keys, operator index, variable names)."""
    variables: set[str] = set()
    raw = []  # (file, line, module, keyword, text)
    for path in sorted(src_dir.glob("*.maude")):
        rel = path.relative_to(ROOT).as_posix()
        module = None
        current = None
        for number, raw_line in enumerate(path.read_text(encoding="utf-8").splitlines(), 1):
            line = strip_comment(raw_line)
            if current is None:
                head = MODULE_HEAD.match(line)
                if head:
                    module = head.group(1)
                    continue
                if MODULE_END.match(line):
                    module = None
                    continue
                if not STATEMENT_START.match(line):
                    continue
                current = [number, [line]]
            else:
                current[1].append(line)
            if line.rstrip().endswith(" .") or line.strip() == ".":
                start, lines = current
                current = None
                text = " ".join(" ".join(lines).split())
                keyword = STATEMENT_START.match(lines[0]).group(1)
                raw.append((rel, start, module or "", keyword, text))
        if current is not None:
            raise SystemExit("unterminated statement in {} at line {}".format(rel, current[0]))

    ops = OpIndex()
    for rel, number, module, keyword, text in raw:
        if keyword.startswith("var"):
            body = re.sub(r"^vars?\s+", "", text.split(" : ", 1)[0])
            variables.update(body.split())
        if keyword in ("op", "ops"):
            head = text.split(" :", 1)[0]
            for name in re.sub(r"^ops?\s+", "", head).split():
                ops.add(name, module, rel, number)

    rules = []
    eq_ops = set()
    for rel, number, module, keyword, text in raw:
        if keyword in ("rl", "crl"):
            head = RULE_HEAD.match(text)
            if head is None:
                continue
            rest = text[head.end():]
            cut = top_split(rest, " => ")
            lhs = rest[:cut] if cut >= 0 else rest
            rules.append({"label": head.group(1), "file": rel, "line": number,
                          "module": module, "canon": canonical(tokens(lhs), variables)})
        elif keyword in ("eq", "ceq"):
            head = EQ_HEAD.match(text)
            rest = text[head.end():] if head else text
            cut = top_split(rest, " = ")
            if cut < 0:
                continue
            entry = ops.top(canonical(tokens(rest[:cut]), variables), module)
            if entry is not None:
                eq_ops.add(entry["key"])
    label_count: dict[str, int] = {}
    for rule in rules:
        label_count[rule["label"]] = label_count.get(rule["label"], 0) + 1
    for rule in rules:
        rule["key"] = (rule["label"] if label_count[rule["label"]] == 1
                       else "{}@{}:{}".format(rule["label"], rule["file"], rule["line"]))
    return rules, eq_ops, ops, variables


# ---------------------------------------------------------------- test files

def term_candidates(term: str) -> list[str]:
    """A red term and the terms it contains that are worth checking alone."""
    found = [term]
    text = term.strip()
    if text.startswith("not "):
        found += term_candidates(text[4:])
    cut = top_split(text, " == ")
    if cut >= 0:
        found += term_candidates(text[:cut]) + term_candidates(text[cut + 4:])
    return found


def read_test_file(path: Path):
    """Return (prelude lines, commands) of a test file.

    A command starts at the beginning of a line with search, red or reduce and
    ends at the first line that ends with " ."; all other lines are prelude.
    """
    raw = path.read_text(encoding="utf-8").splitlines()
    prelude: list[str] = []
    commands = []
    index = 0
    while index < len(raw):
        if not COMMAND_START.match(raw[index]):
            prelude.append(raw[index])
            index += 1
            continue
        start = index
        body = []
        while index < len(raw):
            body.append(strip_comment(raw[index]))
            ended = raw[index].rstrip().endswith(" .") or raw[index].strip() == "."
            index += 1
            if ended:
                break
        text = " ".join(" ".join(body).split())
        before = "\n".join(raw[max(0, start - 4):start])
        ids = TEST_ID.findall(before)
        commands.append({"kind": COMMAND_START.match(raw[start]).group(1), "text": text,
                         "line": start + 1, "id": ids[-1] if ids else None})
    return prelude, commands


def resolve_prelude(path: Path, prelude: list[str]) -> str:
    """Make load paths relative to the repository root, as Maude is run from there."""
    out = []
    for line in prelude:
        match = LOAD_LINE.match(line)
        if match:
            name = match.group(1)
            for base in (path.parent, ROOT):
                candidate = Path(str(base / name) + ("" if name.endswith(".maude") else ".maude"))
                if candidate.is_file():
                    out.append("load " + os.path.relpath(candidate.resolve(), ROOT)
                               .replace(".maude", ""))
                    break
            else:
                out.append(line)
        else:
            out.append(line)
    return "\n".join(out) + "\n"


def api_query(command: str, number: int) -> str:
    """Make the omitted attributes of the target explicit, as tests/run-api-suite.py does."""
    marker = "=>*"
    source, target = command.split(marker, 1)
    kernel = re.compile(r"(<\s*ta\s*:\s*TAKernel\s*\|.*?)(>\s*\.)", re.DOTALL)
    target, count = kernel.subn(r"\1, API-ATTS-{}:AttributeSet \2".format(number), target, count=1)
    if count != 1:
        raise SystemExit("API target has no TAKernel assertion")
    target = re.sub(r">\s*\.\s*$", r"> API-CF-{}:Configuration .".format(number), target)
    return source + marker + target


# ---------------------------------------------------------------- Maude runs

def maude_version(maude: str) -> str:
    result = subprocess.run([maude, "-no-advise"], input="", text=True,
                            capture_output=True, cwd=ROOT, timeout=120)
    match = re.search(r"Maude\s+([\w.]+(?:\s+alpha\d+)?)", result.stdout + result.stderr)
    return match.group(1).strip() if match else "unknown"


def run_maude(maude: str, program: str, timeout: float) -> str:
    env = dict(os.environ, PWD=str(ROOT))
    result = subprocess.run([maude, "-no-banner", "-no-advise"], input=program, text=True,
                            encoding="utf-8", capture_output=True, cwd=ROOT,
                            env=env, timeout=timeout)
    return result.stdout


def split_results(stdout: str) -> list[str]:
    """Text after each search result line (the path of that search)."""
    marks = list(SEARCH_MARK.finditer(stdout))
    return [stdout[mark.end():(marks[i + 1].start() if i + 1 < len(marks) else len(stdout))]
            for i, mark in enumerate(marks)]


def solution_states(stdout: str) -> list[str | None]:
    """For each search result line, the state number of its solution (None if none)."""
    states = []
    for mark in SEARCH_MARK.finditer(stdout):
        found = SOLUTION_STATE.match(mark.group(1))
        states.append(found.group(1) if found else None)
    return states


def split_before(stdout: str) -> list[str]:
    """Text before each search result line (the trace of that search)."""
    bodies = []
    start = 0
    for mark in SEARCH_MARK.finditer(stdout):
        bodies.append(stdout[start:mark.start()])
        start = mark.end()
    return bodies


def parse_path(body: str) -> list[tuple[str, str, str]]:
    """Return (before, rule text, after) for each step of a show path body."""
    items = []
    for match in STATE_HEAD.finditer(body):
        items.append((match.start(), match.end(), "state", None))
    for match in RULE_HEAD_TRACE.finditer(body):
        items.append((match.start(), match.end(), "rule", match.group(1)))
    items.sort(key=lambda item: item[0])
    states, rules, kinds = [], [], []
    for index, (start, end, kind, rule) in enumerate(items):
        if kind == "state":
            stop = items[index + 1][0] if index + 1 < len(items) else len(body)
            chunk = body[end:stop]
            ended = STATE_END.search(chunk)
            if ended:
                chunk = chunk[:ended.start()]
            states.append(" ".join(chunk.split()))
            kinds.append("state")
        else:
            rules.append(" ".join(rule.split()))
            kinds.append("rule")
    if kinds != ["state"] + ["rule", "state"] * len(rules):
        return []
    return [(states[i], rules[i], states[i + 1]) for i in range(len(rules))]


def parse_eq_traces(body: str) -> list[tuple[str, str, str]]:
    """Return (equation, instance, result) for each equation application in a trace."""
    lines = body.splitlines()
    found = []
    index = 0
    while index < len(lines):
        if not lines[index].startswith("*********** equation"):
            index += 1
            continue
        end = index + 1
        while end < len(lines) and not lines[end].startswith("***"):
            end += 1
        block = lines[index + 1:end]
        index = end
        if not block or not block[0].startswith(("eq ", "ceq ")):
            continue
        decl_end = next((i for i, line in enumerate(block) if line.rstrip().endswith(" .")), None)
        if decl_end is None:
            continue
        decl = " ".join(block[:decl_end + 1]).strip()
        rest = block[decl_end + 1:]
        arrow = next((i for i, line in enumerate(rest) if line.strip() == "--->"), None)
        if arrow is None:
            continue
        before = [line for line in rest[:arrow] if line.strip() != "empty substitution"]
        after_lines = []
        for line in rest[arrow + 1:]:
            if line.startswith(("rewrites:", "states:", "result ")):
                break
            after_lines.append(line)
        binds = [i for i, line in enumerate(before) if BIND_LINE.match(line)]
        instance_lines = before[binds[-1] + 1:] if binds else before
        instance = " ".join(" ".join(instance_lines).split())
        result = " ".join(" ".join(after_lines).split())
        if instance and result:
            found.append((decl, instance, result))
    return found


def parse_results(stdout: str) -> list[str]:
    """The term of each red result line (sort prefix removed)."""
    out = []
    lines = stdout.splitlines()
    for index, line in enumerate(lines):
        if not line.startswith("result "):
            continue
        parts = [line.split(": ", 1)[1] if ": " in line else ""]
        for follow in lines[index + 1:]:
            if not follow.startswith(" ") or not follow.strip():
                break
            parts.append(follow)
        out.append(" ".join(" ".join(parts).split()))
    return out


def verify_rule(maude: str, prelude: str, module: str, before: str, label: str,
                after: str, timeout: float) -> bool:
    program = prelude + "search [1] in {} : {} =>1 {} .\nshow path labels 1 .\n".format(
        module, before, after)
    stdout = run_maude(maude, program, timeout)
    if "Solution 1" not in stdout:
        return False
    return any(line.strip() == label for line in stdout.splitlines())


def verify_path(maude: str, prelude: str, search: str, state: str, labels: list[str],
                timeout: float) -> bool:
    """True if the search shows the path to state with exactly these labels.

    "show path labels" is the last command, so its label lines are the last lines of the
    output (before the final "Bye.")."""
    if not labels:
        return False
    stdout = run_maude(maude, prelude + search + "\nshow path labels {} .\n".format(state),
                       timeout)
    lines = [line.strip() for line in stdout.splitlines() if line.strip()]
    if lines and lines[-1] == "Bye.":
        lines = lines[:-1]
    return lines[-len(labels):] == labels


def verify_op(maude: str, prelude: str, module: str, instance: str, result: str | None,
              timeout: float) -> str | None:
    """Return the normal form of instance, or None if Maude does not confirm it."""
    program = prelude + "red in {} : {} .\n".format(module, instance)
    if result is not None:
        program += "red in {} : {} .\n".format(module, result)
    results = parse_results(run_maude(maude, program, timeout))
    if result is None:
        return results[0] if len(results) == 1 else None
    return results[0] if len(results) == 2 and results[0] == results[1] else None


# ---------------------------------------------------------------- collection

def run_test_file(maude: str, path: Path, cache: Path | None, digest: str, timeout: float,
                  api: bool):
    """Run the search, path and trace programs of one file; return its data.

    The search program gives the state number of each solution; the path program then
    shows the path to each of those states. api: complete the searches as
    tests/run-api-suite.py does."""
    prelude_lines, commands = read_test_file(path)
    prelude = resolve_prelude(path, prelude_lines)
    searches = [c for c in commands if c["kind"] == "search"]
    texts = [api_query(c["text"], index + 1) if api else c["text"]
             for index, c in enumerate(searches)]
    search_text = "".join(t + "\n" for t in texts)

    def run_cached(mode: str, program: str) -> str:
        name = "{}-{}-{}.txt".format(path.relative_to(ROOT).as_posix().replace("/", "_"),
                                     mode, digest)
        cached = cache / name if cache else None
        if cached is not None and cached.is_file():
            return cached.read_text(encoding="utf-8")
        output = run_maude(maude, program, timeout)
        if cached is not None:
            cache.mkdir(parents=True, exist_ok=True)
            cached.write_text(output, encoding="utf-8")
        return output

    outputs = {"search": run_cached("search", prelude + search_text)}
    for line in outputs["search"].splitlines():
        if line.startswith(("Warning:", "Error:")):
            print("warning: {}: {}".format(path.relative_to(ROOT).as_posix(), line),
                  file=sys.stderr)
            break
    states = solution_states(outputs["search"])
    if len(states) != len(texts):
        states = [None] * len(texts)
    path_program = prelude + "".join(
        t + "\n" + ("show path {} .\n".format(state) if state else "")
        for t, state in zip(texts, states))
    outputs["path"] = run_cached("paths", path_program)
    outputs["trace"] = run_cached(
        "trace", prelude + "set trace on .\nset trace rules off .\nset trace eqs on .\n"
        "set trace whole off .\n" + search_text)
    return {"path": path, "prelude": prelude, "searches": searches, "commands": commands,
            "texts": texts, "states": states, "outputs": outputs}


def source_of(rel: str, command: dict) -> str:
    return "{} [{}]".format(rel, command["id"]) if command["id"] else "{}:{}".format(
        rel, command["line"])


def inputs_digest(maude: str, seeds: Path | None) -> str:
    digest = hashlib.sha256(maude.encode())
    seed_files = sorted(seeds.glob("*.maude")) if seeds and seeds.is_dir() else []
    for path in sorted(list(SRC.glob("*.maude")) + list((ROOT / "tests").rglob("*.maude"))
                       + seed_files):
        digest.update(path.relative_to(ROOT).as_posix().encode())
        digest.update(path.read_bytes())
    return digest.hexdigest()[:16]


def main() -> int:
    parser = argparse.ArgumentParser(description="Generate docs/examples.json (verified examples).")
    parser.add_argument("--maude", help="Maude binary (default: MAUDE in config.mk)")
    parser.add_argument("--out", default=str(ROOT / "docs" / "examples.json"))
    parser.add_argument("--seeds", default=str(SEEDS_DIR),
                        help="directory of seed files (default: docs/example-seeds; "
                             "empty string for none)")
    parser.add_argument("--cache", help="directory for raw Maude outputs of the test runs")
    parser.add_argument("--timeout", type=float, default=1800.0,
                        help="seconds allowed per Maude run (default: 1800)")
    parser.add_argument("--jobs", type=int, default=os.cpu_count() or 2)
    parser.add_argument("--verbose", action="store_true",
                        help="list the rule steps that were reached but not verified")
    args = parser.parse_args()

    maude = args.maude
    if maude is None:
        config = (ROOT / "config.mk").read_text(encoding="utf-8")
        match = re.search(r"^MAUDE\s*:=\s*(\S+)", config, re.MULTILINE)
        if match is None:
            print("no MAUDE in config.mk; use --maude", file=sys.stderr)
            return 2
        maude = match.group(1)
    cache = Path(args.cache) if args.cache else None
    seeds = Path(args.seeds) if args.seeds else None
    digest = inputs_digest(maude, seeds)
    version = maude_version(maude)

    rules, eq_ops, ops, variables = scan_src(SRC)
    op_entries = {entry["key"]: entry for entry in ops.entries}

    # Test files in tests/api and all seed files are API-level: their searches are completed
    # by api_query. The other test files are used as they are written.
    jobs = [(path, path.parent.name == "api" and path.parent.parent.name == "tests")
            for path in sorted({path for pattern in TEST_GLOBS for path in ROOT.glob(pattern)})]
    if seeds is not None and seeds.is_dir():
        jobs += [(path, True) for path in sorted(seeds.glob("*.maude"))]
    with concurrent.futures.ThreadPoolExecutor(max_workers=max(1, args.jobs)) as pool:
        runs = list(pool.map(lambda job: run_test_file(maude, job[0], cache, digest, args.timeout,
                                                       job[1]), jobs))

    rules_by_label: dict[str, list[dict]] = {}
    for rule in rules:
        rules_by_label.setdefault(rule["label"], []).append(rule)
    rule_steps: dict[str, list[dict]] = {}
    op_candidates: dict[str, list[dict]] = {}

    for run in runs:
        rel = run["path"].relative_to(ROOT).as_posix()
        prelude = run["prelude"]
        searches = run["searches"]
        path_bodies = split_results(run["outputs"]["path"])
        path_outcomes = [m.group(1) for m in SEARCH_MARK.finditer(run["outputs"]["path"])]
        trace_bodies = split_before(run["outputs"]["trace"])
        if len(path_outcomes) != len(searches) or len(trace_bodies) != len(searches):
            print("warning: {} has {} searches but {} results".format(
                rel, len(searches), len(path_outcomes)), file=sys.stderr)
            continue
        for index, command in enumerate(searches):
            if not path_outcomes[index].startswith("Solution"):
                continue
            module = SEARCH_MODULE.match(command["text"]).group(1)
            source = source_of(rel, command)
            steps = parse_path(path_bodies[index])
            step_labels = [RULE_HEAD.match(rule_text).group(1) if RULE_HEAD.match(rule_text)
                           else None for _, rule_text, _ in steps]
            for before, rule_text, after in steps:
                head = RULE_HEAD.match(rule_text)
                if head is None:
                    continue
                label = head.group(1)
                rest = rule_text[head.end():]
                cut = top_split(rest, " => ")
                canon = canonical(tokens(rest[:cut] if cut >= 0 else rest), variables)
                same_label = rules_by_label.get(label, [])
                if len(before) > MAX_TERM or len(after) > MAX_TERM:
                    continue
                if len(same_label) == 1:
                    rule = same_label[0]
                else:
                    # Duplicate label: attribute the step only when exactly one copy
                    # has the same left-hand side (as a multiset of tokens).
                    same_sig = [r for r in same_label if sorted(r["canon"]) == sorted(canon)]
                    if len(same_sig) != 1:
                        same_sig = [r for r in same_label if words(r["canon"]) == words(canon)]
                    if len(same_sig) != 1:
                        # The step's printed left-hand side has more words (sort names, kernel
                        # attributes): the copy whose words all occur in it, if only one.
                        step_words = Counter(words(canon))
                        same_sig = [r for r in same_label
                                    if not Counter(words(r["canon"])) - step_words]
                    if len(same_sig) != 1:
                        continue
                    rule = same_sig[0]
                rule_steps.setdefault(rule["key"], []).append(
                    {"size": len(before) + len(after), "before": before, "after": after,
                     "label": label, "source": source, "module": module, "prelude": prelude,
                     "search": run["texts"][index], "state": run["states"][index],
                     "labels": step_labels})
            for decl, instance, result in parse_eq_traces(trace_bodies[index]):
                head = EQ_HEAD.match(decl)
                rest = decl[head.end():] if head else decl
                cut = top_split(rest, " = ")
                if cut < 0:
                    continue
                entry = ops.top(canonical(tokens(rest[:cut]), variables), module)
                if (entry is None or entry["key"] not in eq_ops
                        or len(instance) > MAX_TERM or len(result) > MAX_TERM):
                    continue
                op_candidates.setdefault(entry["key"], []).append(
                    {"size": len(instance) + len(result), "input": instance,
                     "result": result, "source": source, "module": module,
                     "prelude": prelude})
        for command in run["commands"]:
            if command["kind"] == "search":
                continue
            found = RED_MODULE.match(command["text"])
            if found is None:
                continue
            module, term = found.group(1), found.group(2).strip()
            source = source_of(rel, command)
            for candidate in term_candidates(term):
                if len(candidate) > MAX_TERM:
                    continue
                entry = ops.top(canonical(tokens(candidate), variables), module)
                if entry is None or entry["key"] not in eq_ops:
                    continue
                op_candidates.setdefault(entry["key"], []).append(
                    {"size": len(candidate), "input": candidate, "result": None,
                     "source": source, "module": module, "prelude": prelude})

    rules_out = {}
    missing_rules = []
    for rule in rules:
        key = rule["key"]
        candidates = sorted(rule_steps.get(key, []), key=lambda c: c["size"])
        examples, seen = [], set()
        for cand in candidates[:TRIES_PER_KEY]:
            if len(examples) >= CANDIDATES_PER_KEY:
                break
            if (cand["before"], cand["after"]) in seen:
                continue
            seen.add((cand["before"], cand["after"]))
            if (verify_rule(maude, cand["prelude"], cand["module"], cand["before"],
                            cand["label"], cand["after"], args.timeout)
                    or (cand["state"] is not None and None not in cand["labels"]
                        and verify_path(maude, cand["prelude"], cand["search"], cand["state"],
                                        cand["labels"], args.timeout))):
                examples.append({"file": rule["file"], "line": rule["line"],
                                 "module": rule["module"], "check_module": cand["module"],
                                 "before": cand["before"], "after": cand["after"],
                                 "source": cand["source"], "verified": True})
        if examples:
            rules_out[key] = examples
        else:
            missing_rules.append(key)
            if candidates and args.verbose:
                print("reached, not verified: {} ({} steps)".format(key, len(candidates)),
                      file=sys.stderr)

    ops_out = {}
    missing_ops = []
    for key in sorted(eq_ops):
        entry = op_entries[key]
        candidates = sorted(op_candidates.get(key, []), key=lambda c: c["size"])
        examples, seen = [], set()
        for cand in candidates[:TRIES_PER_KEY]:
            if len(examples) >= CANDIDATES_PER_KEY:
                break
            if cand["input"] in seen:
                continue
            seen.add(cand["input"])
            output = verify_op(maude, cand["prelude"], cand["module"], cand["input"],
                               cand["result"], args.timeout)
            if output is not None and len(output) <= MAX_TERM:
                examples.append({"file": entry["file"], "line": entry["line"],
                                 "module": entry["module"], "input": cand["input"],
                                 "output": output, "source": cand["source"],
                                 "verified": True})
        if examples:
            ops_out[key] = examples
        else:
            missing_ops.append(key)

    document = {
        "generated_by": "tools/gen-examples.py",
        "maude_version": version,
        "rules": dict(sorted(rules_out.items())),
        "ops": dict(sorted(ops_out.items())),
        "missing": {"rules": sorted(missing_rules), "ops": sorted(missing_ops)},
    }
    out = Path(args.out)
    out.parent.mkdir(parents=True, exist_ok=True)
    out.write_text(json.dumps(document, indent=1, ensure_ascii=False) + "\n", encoding="utf-8")
    print("rules with examples: {}/{}".format(len(rules_out), len(rules)), file=sys.stderr)
    print("equation-defined ops with examples: {}/{}".format(len(ops_out), len(eq_ops)),
          file=sys.stderr)
    print("wrote {}".format(out), file=sys.stderr)
    return 0


if __name__ == "__main__":
    sys.exit(main())
