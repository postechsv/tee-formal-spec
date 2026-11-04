#!/usr/bin/env python3
"""gen-maude2lean: generate the maude2lean build of the TEE spec from its DMC build.

Usage
  Build the DMC tree first (tools/gen-dmc.py), then:
      python3 tools/gen-maude2lean.py build/t/.m2l/dmc-src build/t/src --cache-dir build/t/.m2l-cache --check
  Translate to Lean (https://github.com/fadoss/maude2lean):
      cd build/t/src && maude2lean main.maude -o ../lean/TeeSpec.lean
  make NAME=t M2L=1 runs both generators (M2L=1 needs DMC=1); make lean runs maude2lean.

Why the DMC build: maude2lean has no [owise] (the DMC build has none) and no sort test `t :: S`
(the translator skips the Bool operators _::`S that Maude adds per sort, so a statement using one
fails with KeyError).  This pass removes the sort tests from the DMC build; the rest of the tree is
copied unchanged.

Rewrites (every `X :: S` of an equation or rule; X must be a variable, bare or X:K)
  * a whole condition fragment `X :: S` (after `if` or `/\\`, before `/\\` or the end) becomes the
    membership fragment `X : S`, which maude2lean translates to has_sort.
  * `not X :: S` and `not (X :: S)` become `m2l-out-S-K(X)`, where K is the sort of X.
  * any other `X :: S` (inside a Bool term) becomes `(not m2l-out-S-K(X))`.
  m2l-out-S-K : K -> Bool is true exactly on the terms of K outside S.  Its equations are those of
  gen-dmc's dmc-out-S-K (out_pred_eqs: one equation per constructor over the least sort of a term, with
  subsort-overloaded constructors recursing on their arguments), with built-in true/false results instead
  of DM-BOOL's True/False.  This is meaning-preserving because the build has no membership axioms: the
  least sort of a constructor term is fixed by the constructor declarations.

Placement (ctor visibility)
  The equations are over the constructors that a module sees, so a module importing a user may see more.
  Modules are processed in load order; a module gets, before its end line, the lines of every predicate
  that it or a module it imports uses, minus the lines its imports already have (the declaration once).
  The generated equation modules of gen-dmc (dm-eq.maude, aux.maude) are not processed.  Test modules
  are not processed either: a test module that adds constructors of a tested sort is not covered.

Options
  --cache-dir DIR   reflection cache (reflect.json), check inputs and manifest.json
                    (default <out>/../.m2l-cache)
  --maude PATH      Maude binary (default $MAUDE_BIN)
  --fresh           ignore the reflection cache
  --no-eq           remove gen-dmc's `_.=._` (declarations and defining equations); its other uses become
                    the built-in `==` (maude2lean gives `==` the equation L == R = true if L = R only)
  --check           after writing: Maude load warnings of the output, and for each predicate, in each
                    module that uses it and each main.maude module that sees it, ground constructor terms t
                    with  red t :: S  against  red m2l-out-S-K(t)  (one must be the negation of the other).
                    Exit 3 on load warnings, 2 on a mismatch.

The reflection, the statement scanner, the predicate equations and the ground-term builder are gen-dmc's
(imported from tools/gen-dmc.py); this file only adds the sort-test rewrite and the placement.
"""

import argparse
import importlib.util
import json
import os
import re
import shutil
import sys

TOOLS_DIR = os.path.dirname(os.path.abspath(__file__))


def load_gen_dmc():
    spec = importlib.util.spec_from_file_location('gen_dmc', os.path.join(TOOLS_DIR, 'gen-dmc.py'))
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


dmc = load_gen_dmc()
GenError = dmc.GenError

PRED_PREFIX = 'm2l-out-'
# gen-dmc's generated modules: they only compare terms, no sort test is reduced there
SKIP_FILES = ('aux.maude', dmc.DM_EQ_FILE)
CHECK_TERMS = 12        # ground terms per (module, predicate)

SORT_TEST_RE = re.compile(
    r"(?<![\w\-'$#%:.])(?P<neg>not(?:\s+|\s*(?P<open>\(\s*)))?"
    r"(?P<var>[A-Za-z][\w\-']*)(?::(?P<vsort>[\w\-']+(?:\{[^{}\s]*\})?))?"
    r"\s+::\s+(?P<sort>[A-Za-z][\w\-']*(?:\{[^{}\s]*\})?)(?P<close>\s*\))?")
VAR_DECL_RE = re.compile(r'^vars?\s+(.+?)\s*:\s*(\S+)\s*\.$')
BOOL_RE = re.compile(r"(?<![\w\-'$#%:])(True|False)(?![\w\-'])")


# ---------------------------------------------------------------- source tree

class Module(object):
    def __init__(self, name, fname, head):
        self.name = name
        self.file = fname
        self.head = head        # head unit
        self.end = None         # end unit
        self.vars = {}          # variable -> sort
        self.stmts = []         # statement units


def read_tree(src):
    """FileInfo (with units) of every file main.maude loads; the others are copied verbatim."""
    loaded = dmc.loaded_files(src)
    fis = {}
    for f in sorted(os.listdir(src)):
        if not f.endswith('.maude'):
            continue
        with open(os.path.join(src, f), encoding='utf-8', newline='') as fh:
            fi = dmc.FileInfo(f, fh.read())
        if f in loaded:
            fi.units, fi.kinds = dmc.scan_units([t for t, _ in fi.lines], f)
        fis[f] = fi
    return fis


def index_modules(fis):
    """Modules of the loaded files (views and parameterized modules left out), in file order."""
    mods = {}
    for f in sorted(fis):
        cur = None
        for u in fis[f].units:
            if u.kind == 'head':
                name = dmc.mod_name_of_head(u.text)
                cur = None
                if u.text.split()[0] != 'view' and name and '{' not in u.text.split(' is')[0]:
                    cur = mods[name] = Module(name, f, u)
            elif cur is None:
                continue
            elif u.kind == 'end':
                cur.end = u
                cur = None
            elif u.kind == 'stmt':
                m = VAR_DECL_RE.match(u.text)
                if m:
                    for v in m.group(1).split():
                        cur.vars[v] = m.group(2)
                cur.stmts.append(u)
    return mods


# ---------------------------------------------------------------- sort-test rewrite

def pred_name(Sp, S):
    return dmc.out_pred(Sp, S).replace('dmc-out-', PRED_PREFIX, 1)


def whole_fragment(text, start, end):
    """The text between start and end is a whole condition fragment."""
    before = text[:start].rstrip()
    after = text[end:].lstrip()
    return (re.search(r'(?:^|\s)(?:if|/\\)$', before) is not None
            and (after.startswith('/\\') or after == '.' or after.startswith('[')))


def rewrite_sort_tests(text, mod, where):
    """Statement text without sort tests, the predicates it uses as (S, K) pairs, and its counts."""
    preds, counts = [], {'membership': 0, 'pred': 0}

    def repl(m):
        var, vsort, S = m.group('var'), m.group('vsort'), m.group('sort')
        K = vsort or mod.vars.get(var)
        if K is None:
            raise GenError('%s: sort test on %s, which is not a variable of %s' % (where, var, mod.name))
        vtext = '%s:%s' % (var, vsort) if vsort else var
        close = m.group('close') or ''
        neg, opened = m.group('neg'), m.group('open') is not None
        if neg is not None and (not opened or close):
            # not X :: S  or  not (X :: S); a close without open belongs to an enclosing group
            preds.append((S, K))
            counts['pred'] += 1
            return '%s(%s)%s' % (pred_name(S, K), vtext, '' if opened else close)
        # X :: S, or not ( X :: S ... ) where the not applies to the whole group
        prefix = neg or ''
        if not prefix and whole_fragment(text, m.start(), m.end() - len(close)):
            counts['membership'] += 1
            return '%s : %s%s' % (vtext, S, close)
        preds.append((S, K))
        counts['pred'] += 1
        return '%s(not %s(%s))%s' % (prefix, pred_name(S, K), vtext, close)

    return SORT_TEST_RE.sub(repl, text), preds, counts


def rewrite_modules(mods):
    """New statement texts (unit -> text), the predicates each module uses, and per-file counts."""
    new, uses, counts = {}, {}, {}
    for M in mods.values():
        for u in M.stmts:
            if ' :: ' not in dmc.mask_strings(u.text) or not re.match(r'^(c?eq|c?rl)\s', u.text):
                continue
            where = '%s:%d' % (M.file, u.line)
            text, preds, c = rewrite_sort_tests(u.text, M, where)
            if ' :: ' in dmc.mask_strings(text):
                raise GenError('%s: a sort test is left after the rewrite: %s' % (where, text))
            new[u] = text
            uses.setdefault(M.name, set()).update(preds)
            d = counts.setdefault(M.file, {})
            for k, n in c.items():
                d[k] = d.get(k, 0) + n
    return new, uses, counts


# ---------------------------------------------------------------- predicates and placement

def pred_lines(gen, M, S, K, where):
    """Declarations and equations of m2l-out-S-K over the constructors visible in M (gen-dmc's out_pred_eqs)."""
    return [BOOL_RE.sub(lambda m: m.group(1).lower(), ln.replace('dmc-out-', PRED_PREFIX))
            for ln in dmc.out_pred_eqs(gen, M, S, K, where)]


def placement(gen, mods, order, closure, uses):
    """Lines to insert into each module: the predicates it or its imports use, minus the lines its
    imports already have.  Returns ({module: lines}, {module: predicates it sees})."""
    lines, sees = {}, {}
    for M in order:
        below = closure[M] - {M}
        avail = set(uses.get(M, ()))
        for N in below:
            avail |= sees.get(N, set())
        if not avail:
            continue
        sees[M] = avail
        have = set()
        for N in below:
            have.update(lines.get(N, ()))
        out = []
        for S, K in sorted(avail):
            for ln in pred_lines(gen, M, S, K, '%s (%s)' % (M, pred_name(S, K))):
                if ln not in have and ln not in out:
                    out.append(ln)
        if out:
            lines[M] = out
    return lines, sees


def render_file(fi, new, inserts):
    """File text with the rewritten statements and the predicate blocks (before the end line)."""
    out = list(fi.lines)
    for u in fi.units:
        if u in new:
            text, eol = fi.lines[u.start]
            indent = re.match(r'\s*', text).group(0)
            out[u.start] = (indent + new[u], eol)
            for k in range(u.start + 1, u.end + 1):
                out[k] = ('', '')
    for u, block in inserts:
        text, eol = fi.lines[u.start]
        eol = eol or fi.eol
        pre = ''.join('  %s%s' % (ln, eol) for ln in
                      ['--- gen-maude2lean: sort-test predicates (%sS-K(X): X of K is outside S)'
                       % PRED_PREFIX] + block)
        out[u.start] = (pre + text, eol)
    return ''.join(t + e for t, e in out)


EQ_DECL_RE = re.compile(r'^\s*op _\.=\._ :')
EQ_STMT_RE = re.compile(r'^\s*c?eq\s')


def drop_eq(text, counts):
    """--no-eq: the text without gen-dmc's `_.=._`.  Its declarations and defining equations (one line
    each, LHS with `.=.` before the first ` = `) are removed; every other `.=.` becomes the built-in `==`."""
    out = []
    for ln in text.splitlines(True):
        code = ln.split('---', 1)[0]
        if EQ_DECL_RE.match(code):
            counts['decl'] = counts.get('decl', 0) + 1
            continue
        if EQ_STMT_RE.match(code) and '.=.' in code.split(' = ', 1)[0]:
            counts['eq'] = counts.get('eq', 0) + 1
            continue
        if '.=.' in code:
            counts['use'] = counts.get('use', 0) + code.count('.=.')
            ln = ln.replace(' .=. ', ' == ')
        out.append(ln)
    return ''.join(out)


# ---------------------------------------------------------------- check

def ground_check(gen, out, cache, uses, sees, main_mods, maude):
    """Compare t :: S and m2l-out-S-K(t) on ground constructor terms.  Returns (tests, mismatches)."""
    cmds, owner = [], []
    for M in sorted(sees):
        if M not in uses and M not in main_mods:
            continue
        ctors, leq = dmc.target_sig(gen, M)
        memo = {}
        for S, K in sorted(sees[M]):
            for t in dmc.target_ground(ctors, leq, K, 3, CHECK_TERMS, memo):
                cmds.append((M, '(%s) :: %s' % (t, S)))
                cmds.append((M, '%s(%s)' % (pred_name(S, K), t)))
                owner.append((M, S, K, t))
    res = dmc.run_build_tests(maude, out, cmds, cache)
    tests, mism = 0, []
    for i, (M, S, K, t) in enumerate(owner):
        a, b = res[2 * i], res[2 * i + 1]
        if a is None or b is None:
            continue
        tests += 1
        if (a, b) not in (('true', 'false'), ('false', 'true')):
            mism.append((M, '%s :: %s' % (t, S), a, b))
    return tests, mism


# ---------------------------------------------------------------- main

def main(argv=None):
    ap = argparse.ArgumentParser(description='Generate the maude2lean build from the DMC build.')
    ap.add_argument('src', help='DMC build (gen-dmc output)')
    ap.add_argument('out')
    ap.add_argument('--cache-dir', default=None)
    ap.add_argument('--maude', default=dmc.DEFAULT_MAUDE)
    ap.add_argument('--fresh', action='store_true')
    ap.add_argument('--check', action='store_true')
    ap.add_argument('--no-eq', action='store_true')
    args = ap.parse_args(argv)
    src, out = os.path.abspath(args.src), os.path.abspath(args.out)
    cache = os.path.abspath(args.cache_dir) if args.cache_dir else os.path.join(os.path.dirname(out),
                                                                                '.m2l-cache')
    if src == out:
        sys.stderr.write('gen-maude2lean: the output must not be the source tree\n')
        return 1
    try:
        fis = read_tree(src)
        mods = index_modules(fis)
        new, uses, counts = rewrite_modules(mods)
        graph = dmc.parse_graph([fi.raw for fi in fis.values() if fi.units])
        closure = dict((M, dmc.import_closure(graph, [M]) & set(mods)) for M in mods)
        users = set(uses)
        work = [M for M in mods if mods[M].file not in SKIP_FILES and closure[M] & users]
        pos = dmc.load_positions(src, fis)
        work.sort(key=lambda M: pos.get(M, len(pos)))
        refl, fresh = dmc.run_reflection(src, cache, sorted(work), args.maude, args.fresh)
        names_rng = {}
        for M in work:
            for f in refl.mods.get(M, {}).get('ops', []) + refl.ctors(M):
                if not f['doms']:
                    names_rng.setdefault(f['name'], set()).add(f['rng'])
        dmc.QUAL_CONSTS.update(n for n, rs in names_rng.items() if len(rs) > 1)
        gen = dmc.Gen(refl)
        lines, sees = placement(gen, mods, work, closure, uses)
    except GenError as ex:
        sys.stderr.write('gen-maude2lean: %s; nothing written\n' % ex)
        return 1

    inserts = {}
    for M, block in lines.items():
        if mods[M].end is None:
            sys.stderr.write('gen-maude2lean: module %s has no end line; nothing written\n' % M)
            return 1
        inserts.setdefault(mods[M].file, []).append((mods[M].end, block))
    os.makedirs(out, exist_ok=True)
    for old in os.listdir(out):
        if old.endswith('.maude'):
            os.remove(os.path.join(out, old))
    for f in os.listdir(src):
        p = os.path.join(src, f)
        if os.path.isfile(p) and not f.endswith('.maude'):
            shutil.copy2(p, os.path.join(out, f))
    eq_counts = {}
    for f, fi in fis.items():
        text = render_file(fi, new, inserts.get(f, [])) if fi.units else fi.raw
        if args.no_eq:
            text = drop_eq(text, eq_counts)
        with open(os.path.join(out, f), 'w', encoding='utf-8', newline='') as fh:
            fh.write(text)

    manifest = {'counts': counts,
                'uses': dict((M, sorted(pred_name(S, K) for S, K in ps)) for M, ps in sorted(uses.items())),
                'lines': dict((M, len(b)) for M, b in sorted(lines.items()))}
    with open(os.path.join(cache, 'manifest.json'), 'w') as fh:
        json.dump(manifest, fh, indent=1)
    print('gen-maude2lean: wrote %s%s' % (out, ' (reflection refreshed)' if fresh else ''))
    for f in sorted(counts):
        print('  %-22s %s' % (f, ', '.join('%s=%d' % kv for kv in sorted(counts[f].items()))))
    print('  predicates: %d; lines per module: %s' % (
        len(set(p for ps in uses.values() for p in ps)),
        ', '.join('%s=%d' % (M, len(b)) for M, b in sorted(lines.items()))))
    if args.no_eq:
        print('  --no-eq: removed %d declarations and %d equations of _.=._; %d uses -> ==' % (
            eq_counts.get('decl', 0), eq_counts.get('eq', 0), eq_counts.get('use', 0)))
    if not args.check:
        return 0
    main_mods = set(dmc.mod_names_in(fis['main.maude'])) if 'main.maude' in fis else set()
    try:
        tests, mism = ground_check(gen, out, cache, uses, sees, main_mods, args.maude)
    except GenError as ex:
        sys.stderr.write('gen-maude2lean: check: %s\n' % ex)
        return 2
    print('  ground check: %d terms, %d mismatch(es)' % (tests, len(mism)))
    for M, t, a, b in mism[:20]:
        print('    MISMATCH %s: %s\n      :: %s\n      pred %s' % (M, t, a, b))
    warns = dmc.load_warnings(args.maude, cache, out, None)
    print('  load warnings: %d' % len(warns))
    for f, w in warns:
        print('    LOAD %s: %s' % (f, w))
    if warns:
        return 3
    return 0 if not mism else 2


if __name__ == '__main__':
    sys.exit(main())
