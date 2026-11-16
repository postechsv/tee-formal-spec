#!/usr/bin/env python3
"""strip-modules.py: the spec files of a feature configuration, for the Makefile.

    python3 tools/strip-modules.py --out DIR --main src/main.maude \\
        [--drop-loads "F ..."] [--drop-prs "M ..."] [--without "M ..."] [--cut "M:F ..."] FILE...

Writes the main file and every FILE to DIR, at the same relative path, with LF line endings.

Main file (src/main.maude): only active lines count, a line that starts (after indentation)
with "load X" or "pr M".
  --drop-loads  drop the "load F" lines (the files of disabled features);
  --drop-prs    drop the "pr M ." lines (the modules of disabled features, and WITHOUT);
  --without     the modules of WITHOUT: each must match an active "pr" line, or it is an error.
A comment whose text is itself a load or pr command ("--- pr M .") is removed, so disabled
code is not kept there as a comment; every other comment line is copied unchanged.

Every file, the main file included:
  --cut "M:F"   cut module M of feature F (a features.mk name such as DATA_STREAM).  A file can
                hold modules of several features (src/tee-panic.maude holds the panic module of
                each feature group), so the modules of a disabled feature are cut from the files
                that stay.  A cut module goes with the comment block directly above its header
                and with one blank line after its end keyword.  The block must contain
                "--- @feature f", f = F in lower case with "-" for "_"; a cut module annotated
                with another feature, or with none, is an error, so features.mk and the
                @feature annotations cannot drift apart.
Exit status 1, with the reasons on stderr, on an error; nothing is written then.
"""
import argparse
import os
import re
import sys

COMMENTED_CMD = re.compile(r'^[ \t]*---[ \t]+(pr|load|inc|including|extending|protecting)[ \t]')
LOAD = re.compile(r'^[ \t]*load[ \t]+(\S+)')
PR = re.compile(r'^[ \t]*pr[ \t]+(\S+)')
COMMENT = re.compile(r'^[ \t]*(---|\*\*\*)')
HEADER = re.compile(r'^[ \t]*(fmod|mod|omod|fth|th|oth)[ \t]+(\S+)')
END = re.compile(r'^[ \t]*(endfm|endm|endom|endfth|endth|endoth)([ \t]|$)')
FEATURE = re.compile(r'@feature[ \t]+(\S+)')


def select_main(lines, drop_loads, drop_prs, without, errors):
    out, hit = [], set()
    for line in lines:
        if COMMENTED_CMD.match(line):
            continue
        m = LOAD.match(line)
        if m and m[1] in drop_loads:
            continue
        m = None if m else PR.match(line)
        if m:
            hit.add(m[1])
            if m[1] in drop_prs:
                continue
        out.append(line)
    for name in sorted(without - hit):
        errors.append('WITHOUT name matches no active pr line: %s' % name)
    return out


def cut_modules(path, lines, cut, errors):
    out, pending, cutting, skip_blank = [], [], False, False
    for n, line in enumerate(lines, 1):
        if cutting:
            if END.match(line):
                cutting, skip_blank = False, True
            continue
        if skip_blank:
            skip_blank = False
            if not line.strip():
                continue
        if COMMENT.match(line):
            pending.append(line)
            continue
        m = HEADER.match(line)
        if m and m[2] in cut:
            found = [f[1] for f in map(FEATURE.search, pending) if f]
            found = found[-1] if found else '(none)'
            if found != cut[m[2]]:
                errors.append('%s:%d: module %s is cut with feature %s but annotated @feature %s'
                              % (path, n, m[2], cut[m[2]], found))
            pending, cutting = [], True
            continue
        out += pending + [line]
        pending = []
    if cutting:
        errors.append('%s: no end keyword after a cut module' % path)
    return out + pending


def main():
    ap = argparse.ArgumentParser(description='The spec files of a feature configuration.')
    ap.add_argument('--out', required=True)
    ap.add_argument('--main', required=True)
    for opt in ('--drop-loads', '--drop-prs', '--without', '--cut'):
        ap.add_argument(opt, default='')
    ap.add_argument('files', nargs='*')
    args = ap.parse_args()
    cut = {}
    for item in args.cut.split():
        module, _, feature = item.partition(':')
        cut[module] = feature.lower().replace('_', '-')

    errors, results = [], {}
    for path in [args.main] + args.files:
        with open(path, newline='') as fh:
            lines = [l.rstrip('\n').rstrip('\r') for l in fh]
        if path == args.main:
            lines = select_main(lines, set(args.drop_loads.split()), set(args.drop_prs.split()),
                                set(args.without.split()), errors)
        results[path] = cut_modules(path, lines, cut, errors)
    if errors:
        for e in errors:
            print('strip-modules.py: ' + e, file=sys.stderr)
        sys.exit(1)
    for path, lines in results.items():
        target = os.path.join(args.out, path)
        os.makedirs(os.path.dirname(target), exist_ok=True)
        with open(target, 'w', newline='\n') as fh:
            fh.write(''.join(l + '\n' for l in lines))


if __name__ == '__main__':
    main()
