#!/usr/bin/env python3
"""Rename code identifiers to the names used in the specification paper.

Usage:
    python3 tools/paper-rename.py [--dry-run] [--root DIR]

Renames are applied to whole identifier tokens only. A token is a maximal run of
[A-Za-z0-9_'-] (with a '.' not allowed to join it either), so `CipherInit` does not
match inside `reqCipherInit`, `finishCipherInit`, `cipher-init-x`, or `TEE_CipherInit`.
Text is rewritten everywhere it occurs (code, comments, `---@dmc` annotations, docs),
because doc comments that mention an old name should follow the rename.

Scope: src/**/*.maude, tests/** (text files), README.md and README (if present),
tools/gen-dmc.py and tools/dmc-lib/** .

Idempotent: a second run finds no old tokens and changes nothing. Before any write,
the script refuses (exit 2) when an old and a new name are both present as code
identifiers (comments excluded), i.e. the rename would merge two identifiers. A new
name present in code with its old name absent is treated as already applied.

Exit status: 0 on success (including no-op), 1 on usage/IO error, 2 on collision.
"""

import argparse
import os
import re
import sys

# (old name in the code base, paper name). Keep this list in sync with the rename table.
RENAMES = [
    ("determineOpClass", "getClass"),
    ("TeeApiCall", "Call"),
    ("CreatePersistentObject", "createPersistent"),
    ("ReadObjectData", "readObjData"),
    ("AllocateOperation", "allocOperation"),
    ("ResetOperation", "resetOperation"),
    ("CipherInit", "cipherInit"),
    ("CipherUpdate", "cipherUpdate"),
    ("CipherDoFinal", "cipherDoFinal"),
    ("CopyObjectAttributes1", "copyObjAttrs1"),
    ("typeRsaKeypair", "rsaKeyPair"),
    ("typeRsaPublicKey", "rsaPubKey"),
    ("typeDsaKeypair", "dsaKeypair"),
    ("typeDsaPublicKey", "dsaPubKey"),
]

IDCHAR = r"A-Za-z0-9_'\-"
SCOPE_FILES = ["README.md", "README"]
SCOPE_DIRS = [("src", (".maude",)), ("tests", None), ("tools/dmc-lib", (".maude",))]
SCOPE_TOOLS = ["tools/gen-dmc.py"]
SKIP_DIRS = {"__pycache__", ".git", "build"}


def token_re(name):
    # Identifier boundary: not preceded by an identifier char or '.', and not followed by an
    # identifier char or by '.' followed by an identifier char.
    return re.compile(
        r"(?<![%s.])%s(?![%s]|\.[%s])" % (IDCHAR, re.escape(name), IDCHAR, IDCHAR)
    )


def strip_comments(text, is_python):
    """Return text with comments blanked, for the collision check only."""
    out = []
    for line in text.split("\n"):
        if is_python:
            # '#' outside string literals is good enough for gen-dmc.py.
            line = re.sub(r"#.*$", "", line)
        else:
            m = re.search(r"---|\*\*\*", line)
            if m:
                line = line[: m.start()]
        out.append(line)
    return "\n".join(out)


def in_scope(root):
    """Yield absolute paths of files to scan."""
    seen = set()

    def add(path):
        if path not in seen and os.path.isfile(path):
            seen.add(path)
            return True
        return False

    for rel in SCOPE_FILES:
        p = os.path.join(root, rel)
        if add(p):
            yield p
    for rel, exts in SCOPE_DIRS:
        base = os.path.join(root, rel)
        for dp, dn, fn in os.walk(base):
            dn[:] = [d for d in dn if d not in SKIP_DIRS]
            for f in sorted(fn):
                if exts is not None and not f.endswith(exts):
                    continue
                p = os.path.join(dp, f)
                if add(p):
                    yield p
    for rel in SCOPE_TOOLS:
        p = os.path.join(root, rel)
        if add(p):
            yield p


def read_text(path):
    try:
        with open(path, "r", encoding="utf-8") as fh:
            return fh.read()
    except UnicodeDecodeError:
        return None  # binary: skip
    except OSError as e:
        sys.stderr.write("error: cannot read %s: %s\n" % (path, e))
        sys.exit(1)


def main():
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--dry-run", action="store_true", help="print per-file counts, write nothing")
    ap.add_argument("--root", default=None, help="repository root (default: parent of tools/)")
    args = ap.parse_args()

    root = os.path.abspath(args.root or os.path.join(os.path.dirname(os.path.abspath(__file__)), ".."))
    self_path = os.path.abspath(__file__)

    files = {}
    for p in in_scope(root):
        if os.path.abspath(p) == self_path:
            continue
        text = read_text(p)
        if text is not None:
            files[p] = text

    # Collision check (code only: comments excluded).
    refuse = []
    plan = []  # (old, new, regex)
    for old, new in RENAMES:
        rold, rnew = token_re(old), token_re(new)
        old_in_code = new_in_code = False
        for p, text in files.items():
            code = strip_comments(text, p.endswith(".py"))
            if rold.search(code):
                old_in_code = True
            if rnew.search(code):
                new_in_code = True
        if old_in_code and new_in_code:
            refuse.append((old, new))
        plan.append((old, new, rold, old_in_code, new_in_code))

    if refuse:
        for old, new in refuse:
            sys.stderr.write("collision: both '%s' and '%s' occur as code identifiers; not renaming\n" % (old, new))
        sys.exit(2)

    total_changes = {}
    per_file = {}
    for old, new, rold, old_in_code, _ in plan:
        # Comments are rewritten too, so an old name that survives only in comments is renamed.
        for p, text in files.items():
            n = len(rold.findall(text))
            if n:
                per_file.setdefault(p, {})[old] = n
                total_changes[(old, new)] = total_changes.get((old, new), 0) + n
                files[p] = rold.sub(new, text)

    print("%-24s %-18s %6s" % ("old", "new", "count"))
    for old, new in RENAMES:
        if (old, new) in total_changes:
            print("%-24s %-18s %6d" % (old, new, total_changes[(old, new)]))

    if not per_file:
        print("no changes")
        return 0

    print("")
    for p in sorted(per_file):
        rel = os.path.relpath(p, root)
        parts = ", ".join("%s:%d" % (o, c) for o, c in sorted(per_file[p].items()))
        print("%-40s %s" % (rel, parts))

    if args.dry_run:
        print("\n(dry run: nothing written)")
        return 0

    for p in per_file:
        with open(p, "w", encoding="utf-8", newline="") as fh:
            fh.write(files[p])
    print("\nwrote %d files" % len(per_file))
    return 0


if __name__ == "__main__":
    sys.exit(main())
