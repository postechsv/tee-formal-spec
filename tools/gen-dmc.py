#!/usr/bin/env python3
"""gen-dmc: generate the DM-Check-compatible (DMC) build of the TEE spec.

Usage
  Regenerate the build, its derived tests, and run the self-checks:
      python3 tools/gen-dmc.py src build/dmc --tests tests --tests-out build/dmc-tests --check
  Load the build (must report no warnings):
      cd build/dmc && maude        then:  load main.maude
  DM-Check (run with `ulimit -s unlimited`); input file:
      load build/dmc/main.maude
      load dm-check-alpha12.maude
      set module TEE-BEHAVIOR .
  Expected: "Module TEE-BEHAVIOR is now active." and no "cannot reason soundly" list.  The DMC build
  has no built-in ==/=/=, no owise, no membership axioms and no non-equational rule conditions.

Input: the ORIGINAL spec (src/) with `---@dmc <directive>` comment lines placed immediately before the
statement (or view header) they affect.  The annotated spec loads unchanged (the directives are
comments).  Only files reached from main.maude through `load` are transformed; the rest are copied
verbatim (e.g. the not-loaded cryptographic.maude).  The test tree (--tests) is not loaded by the
generator; its derived copy (--tests-out, default <out>/../dmc-tests) has the same layout and the same
numbering as the build, so the test suites run on the DMC build unchanged.  The source and test trees
are never written.

Options
  --only F[,F...]   transform only these files (the others are copied verbatim; pilot scoping)
  --aux PATH        owise-free library, copied as aux.maude and loaded first (default tools/dmc-lib/aux.maude)
  --tests DIR       test tree to derive (its .maude files are rewritten, the other files copied)
  --tests-out DIR   derived test tree (default: <out>/../dmc-tests)
  --check           self-checks: reflection freshness, drift of enumerated constructors, and ground-term
                    comparison of ORIGINAL vs DMC for every enumerated owise group (test commands that
                    the module cannot parse are skipped and counted; DMC results are read back through
                    the closed-sort numbering)
  --dmc-base DIR    DMC-compatible base files for the check build (default: <out-dir>)
  --fresh           ignore the reflection cache
  --cache-dir DIR   bookkeeping directory: reflect.json (reflection cache), ground-*.in and check-build
                    (--check), and manifest.json. Default <out>/.dmc-cache. Only check-build is replaced.
  --maude PATH      Maude binary (default $MAUDE_BIN or the maude-alpha165 path)

Directives (one per line, before the statement)
  ---@dmc close-sort(S=C)       (before `sort S .` or `sorts ... S ... .`)  the sort S gets the constructor
        C : Nat -> S.  Every constant of S (in the scope and in the derived tests) becomes an abbreviation
        `eq c = C(k) .` (its [ctor] attribute is dropped), and the Qid literals of the qid-subsort sort
        become C(k) too.  Numbers are per closed sort: constants (sorted by name), then Qid literals.  The
        table is in the manifest.  Constants are abbreviations because a pattern with a constant never
        matches a normal form, so every equation and rule of the DMC build has its constants and Qid literals
        rewritten (meaning-preserving).  Attribute names (`n :`) and sort names after a colon are left alone.
  ---@dmc qid-subsort           (before `subsort Qid < S .`)  the subsort is dropped in the DMC build.
  ---@dmc restrict(V=S,V'=S)    (before a rule or equation)  the variables of this statement are restricted to
        the sort S (V:S on the left-hand side; the right-hand side is rewritten).  Used for k({ V == V' }) over
        Value? in imp.maude: the K-level comparison is only reached with Value operands (the program path
        requires them), so the restriction is not observable from executions.  Counted in the manifest.
  ---@dmc owise drop
        remove [owise]; the other equations already cover the case.
  ---@dmc owise cond(<C>)
        the owise equation becomes `ceq ... if <C>` (the complement written by the author), e.g.
        `owise cond(not $hasMapping(LM, LB))` or `owise cond(not (FILE in FL))`.
  ---@dmc owise enumerate(VAR)        (or enumerate(argN))
        single-argument enumeration: VAR is a whole argument (its sort's constructors and constants
        are enumerated), or a variable nested inside an argument such as `< S > SL`.  Constructor
        cases that sibling equations cover are skipped; conditional siblings become guards `not (C)`.
  ---@dmc owise enumerate(arg1, arg2, ...)
        cell engine (below) over the listed arguments: each is split into constructor cases on demand.
  ---@dmc owise recurse(sep=<op>, empty=<const>)
        cell engine over the last argument, a collection built with the AC constructor <op> (`__`
        or `_,_`), given as a variable (`FL`) or as `Elem Rest` (`OBJ HS`).  Elements are constructor
        cases; a sibling that names a constant, a constructor shape or an attribute of an element splits
        that element on demand.  The cell right-hand side is the owise value on the rest, with the
        element put back when the owise right-hand side rebuilds the collection.
  ---@dmc owise shapes(sep=<op>, empty=<const>, elem=<sort>)
        cell engine over a list argument with the shapes  <empty> | <one element> | (<element>, <rest>).
  ---@dmc exclude-ctors(c1,c2,...)
        leave these constructors out of enumerate / recurse (siblings for them are then ignored).
  ---@dmc keep-builtin
        keep the statement's `==` / `=/=` as written.  Used for the IMP expression constructor `_==_`
        (Exp Exp -> Exp), which is not Maude's built-in equality.
  ---@dmc replace  ...  ---@dmc end
        generator escape hatch (a block `---(` ... `---)` uncommented into the output).  The
        annotated tree uses no replace block; the count is reported.

Equality of the DMC build (plan)
  * A sort gets `_.=._` (variant equations, Bool results True/False) when its constructors (dmc_ctors:
    every reflected constructor and constant over all modules, plus the closed constructors) are structural:
    numeric arguments (numEq), Bool (boolEq), a Set (mutual subset), Map{Var,Nat} (mapEq), a List{X} or a
    comma list ExpList/ValueList/VarList (listEqX, expListEq), or other structural sorts.  Constants only:
    pairwise.  The `_.=._` declaration goes into the sort's own module.
  * A sort whose constructors are not all visible from its declaring module (`late` sorts, or a constructor
    cycle) gets its equations in equation modules (PRE_MAIN), written to dm-eq.maude and loaded by main.maude
    right after its last load line, before its modules.  An equation
    module imports only the signature it needs: the declaring module of the sort, the declaring modules of
    its constructors (ctor_decl_mods), the subsort chain from each constructor's range to the sort
    (subsort_mods), the sorts of its arguments, and DM-BOOL / DM-INT / the auxiliary modules it calls.
    The equations of other sorts are never imported; the operators they need are declared by SIG modules.
  * Per main or test module (gen.vis = its import closure in the ORIGINAL texts), a late sort gets the
    variant with exactly the constructors whose required modules are inside that closure (late_variants).
    A constructor the module cannot see has no terms there, so dropping its equations changes nothing.
    Modules with the same constructor set share one variant (DM-EQ-S, DM-EQ-S-2, ...); dm_for records which
    variants each module imports.  A module with two or more equation modules in dm_for does not
    import them one by one: it imports an import bundle DM-EQ-FOR-<M>, a module that imports exactly those
    variants with `pr` (bundle_groups; dm_use records the import of each main module).  Bundles of main's
    modules go to dm-eq.maude after the equation modules; a derived test module's bundle is in its own test
    file, right before the first module of the file that uses it, and a ground-check wrapper's bundle is in the
    check input, right before the first wrapper that uses it (dm-eq.maude holds only what main uses).  A bundle's import closure is the union of its members' closures.  The header of a main module
    is `pr <bundle> .` (one line) or the single `pr` line of a module with one variant.
  * DM-EQ-S-SIG (sig_module) declares `_.=._ : S S` and the reflexive `eq DMX .=. DMX = True`; it imports
    only the module declaring S (CONFIGURATION for a prelude sort) and DM-BOOL.  An equation module that
    compares arguments of S imports DM-EQ-S-SIG, never the equations of S.  Variants therefore do not
    declare the reflexive equation or `_.=._` themselves.
  * A prelude sort with an equality (Oid of CONFIGURATION, no module of its own) gets its equations in
    prelude_eq_module variants, with the same per-constructor-set rule.
  * Auxiliary equalities: DM-AUX-EQ-MAP (mapEq, Map{Var,Nat}) and DM-AUX-EQ-EXP (expListEq, ExpList), each
    importing only aux_req of its kind; a variant imports the one it calls.  expListEq compares elements with
    expElemEq, one equation per pair of element kinds (Value, Var, PExp, FExp call, FExp without arguments):
    the same kind compares the parts with their own `.=.`, different kinds are false.  The equations of
    expListEq use only the shapes nil and (E L), since the identity axiom of `,` matches any non-empty list.  The equality of List{X} is the
    function listEqX in LIST-EQ-X (defined from the element equality); an equation module imports LIST-EQ-X
    only when its equations call listEqX.  listEqX has equations for nil and for two or more elements, each
    shape (EX LX); a one-element list is matched by (EX LX) with LX = nil (see list_eq_block).
  * Constants of closed sorts (close-sort) are abbreviations for their constructor terms in the DMC build
    (closed_rewrite_code, const_terms and qid_terms in the manifest), so every pattern in the output is a
    constructor term.  A constant of a sort declared with several ranges is qualified (qualc, QUAL_CONSTS).
  * A constant without equations is a constructor in the DMC build (`[ctor]` added); a reducible constant
    (an alias such as `eq null = TEE-HANDLE-NULL`) is left out of the constructor list.
  * A use of `_.=._` of a sort declared in another module imports that module (pr) at the use site.
  * built-in A == B / A =/= B: numeric sorts -> numEq; Set sorts -> subset; sorts with `.=.` -> `.=.`.
  * Ground-check classification: check-dmc compares ORIGINAL and DMC results on ground terms.  A DMC result
    that is still an unreduced term containing numEq (numEq over an ill-formed ground term) is reported as
    "stuck" and counted separately; it is not a mismatch.

Safety checks (errors; nothing is written)
  * check_import_safety: every main.maude module M that imports an equation module D (a bundle counts as one D,
    through its recorded imports) must not gain a source module through D.  The source modules in D's import
    closure (source imports plus the generated modules' imports) must already be in M's own import closure
    (gen.vis).
  * owise_sibling_errors: an [owise] equation is expanded (or guarded) per module, so every sibling equation
    of the same top operator must be in the owise's module; one in another module is an error.  Owise
    equations with `cond(...)` and `drop` are exempt, and so are owise equations with a replace block.

Automatic rules (no annotation)
  * pr SET{X} / MAP{K,V} / LIST{X} (with optional `* (renames)`) -> SET-DM / MAP-DM / LIST-DM.
    Views used as SET elements or MAP keys are upgraded to TRIV-EQ (`eq? to eqX`); a Nat key
    becomes NatEq (renamed back to Nat); nested Set{X} / List{X} arguments get generated views
    SET-OF-X / LIST-OF-X placed before the enclosing module.  An upgraded view also used as a LIST
    element or a MAP value (TRIV parameters) is passed there as a companion view X-TRIV.  A prelude
    sort given constructors by a spec module (Oid) is handled as a sort of its view's target module.
  * owise equation whose siblings with the same LHS are all conditional -> ceq with the negated
    conditions; owise with two variables that siblings identify -> guard `not (A .=. B)`.
  * owise equations with a single sibling pattern constant are reported as errors unless annotated.
Anything else raises an error at file:line asking for an annotation.

Cell engine (owise enumerate(...)/recurse/shapes).  A cell is a combination of argument shapes.  An
argument is split only when a sibling equation has a constructor, constant, attribute or non-variable
pattern at that position, so cells no sibling constrains stay merged.  A cell covered completely by
a sibling is dropped; a cell covered in part gets the guard `not (<sibling pattern equalities and
condition>)`.  Built-in `==` appears only in guards on sorts marked keep-builtin.  Attribute sets are
split only at the attributes that the siblings name; a sibling written without a rest variable is
approximated by its named attributes (documented limitation).

Reflection: DMC-DUMP (tools/dmc-lib/dump.maude) is run on the ORIGINAL spec through the META-LEVEL
(upModule with imports, getOps, getSubsorts, getEqs).  The result (operators with ranges, constructors,
subsort closure, owise counts) is cached in <cache-dir>/reflect.json keyed by the source hash.
Reflection and the ground checks write only under <cache-dir> (default <out-dir>/.dmc-cache, see
--cache-dir); the generator never removes <cache-dir> itself.

Two passes: pass A is permissive and records what needs equality, which views to upgrade and which
modules need DM-INT; pass B generates the output with the plan computed from pass A.

Output: all files of the source dir are copied to <out-dir>; main.maude gets `load aux` before its
first load line and, when there are equation modules, `load dm-eq` after its last load line; aux.maude
is copied; the equation modules (DM-EQ-*, DM-AUX-EQ-*, LIST-EQ-*, ...) and the import bundles (DM-EQ-FOR-*) of
main's modules are written to dm-eq.maude, which is not written when there are none.  A source dir that already has dm-eq.maude is an error.
<cache-dir>/manifest.json records every generated group,
replace uses, equality kinds, upgraded views, closed sorts and the constant/Qid numbering.
"""

import argparse
import hashlib
try:
    import resource
except ImportError:  # pragma: no cover
    resource = None
import json
import os
import re
import shutil
import subprocess
import sys

TOOLS_DIR = os.path.dirname(os.path.abspath(__file__))
DMC_LIB = os.path.join(TOOLS_DIR, 'dmc-lib')
DEFAULT_AUX = os.path.join(DMC_LIB, 'aux.maude')
DUMP_MAUDE = os.path.join(DMC_LIB, 'dump.maude')
DEFAULT_MAUDE = os.environ.get('MAUDE_BIN', 'maude')
GEN_VERSION = '1'
PRELUDE_MODS = ['SET', 'MAP', 'LIST']
NUM_SORTS = ('Nat', 'NzNat', 'Zero', 'Int')
HEAD_KW = {'fmod', 'mod', 'omod', 'fth', 'th', 'sth', 'view'}
END_KW = {'endfm', 'endm', 'endom', 'endfth', 'endth', 'endsth', 'endv', 'endsm'}
CMD_KW = {'load', 'include', 'set', 'select', 'quit', 'red', 'reduce', 'show', 'in', 'make'}
VAR_RE = re.compile(r"^[A-Za-z][\w\-']*$")
DIRECTIVE_RE = re.compile(r'^\s*---@dmc\s+(.*?)\s*$')


class GenError(Exception):
    pass


# ---------------------------------------------------------------- text helpers

def norm(s):
    """Collapse whitespace outside string literals."""
    out, in_str, prev_space = [], False, False
    for ch in s:
        if ch == '"':
            in_str = not in_str
        if not in_str and ch.isspace():
            if not prev_space:
                out.append(' ')
            prev_space = True
            continue
        prev_space = False
        out.append(ch)
    return ''.join(out).strip()


def mask_strings(s):
    out, in_str = [], False
    for ch in s:
        if ch == '"':
            in_str = not in_str
            out.append('"')
        else:
            out.append('x' if in_str else ch)
    return ''.join(out)


def code_part(line):
    """Code part of a line: comment (--- or ***) removed, string literals kept."""
    in_str = False
    for i, ch in enumerate(line):
        if ch == '"':
            in_str = not in_str
        elif not in_str and line.startswith('---', i) or (not in_str and line.startswith('***', i)):
            return line[:i]
    return line


def split_top(s, sep=',', angles=False):
    """Split at depth-0 occurrences of sep (a single char or a word-separator token).
    With angles=True, object brackets `< ... >` (as in `< H : C | AS >`) are nested too."""
    parts, depth, cur, i = [], 0, [], 0
    while i < len(s):
        ch = s[i]
        if ch in '([{':
            depth += 1
        elif ch in ')]}':
            depth -= 1
        elif angles and ch == '<' and s[i + 1:i + 2] == ' ':
            depth += 1
        elif angles and ch == '>' and s[i - 1:i] == ' ' and s[i + 1:i + 2] in ('', ' '):
            depth -= 1
        if depth == 0 and s.startswith(sep, i):
            parts.append(''.join(cur).strip())
            cur = []
            i += len(sep)
            continue
        cur.append(ch)
        i += 1
    last = ''.join(cur).strip()
    if last or parts:
        parts.append(last)
    return [p for p in parts if p != '']


def top_ws_split(s):
    """Split at depth-0 whitespace (juxtaposition)."""
    parts, depth, cur, in_str = [], 0, [], False
    for ch in s:
        if ch == '"':
            in_str = not in_str
        if not in_str:
            if ch in '([{':
                depth += 1
            elif ch in ')]}':
                depth -= 1
            elif ch.isspace() and depth == 0:
                if cur:
                    parts.append(''.join(cur))
                cur = []
                continue
        cur.append(ch)
    if cur:
        parts.append(''.join(cur))
    return parts


def strip_parens(s):
    s = s.strip()
    if s.startswith('(') and s.endswith(')'):
        depth = 0
        for i, ch in enumerate(s):
            if ch == '(':
                depth += 1
            elif ch == ')':
                depth -= 1
                if depth == 0 and i != len(s) - 1:
                    return s
        return s[1:-1].strip()
    return s


def find_top(s, token):
    """Index of first depth-0 occurrence of token (as a whole word for alphanumeric tokens)."""
    depth = 0
    for i in range(len(s)):
        ch = s[i]
        if ch in '([{':
            depth += 1
        elif ch in ')]}':
            depth -= 1
        elif depth == 0 and s.startswith(token, i):
            before = s[i - 1] if i > 0 else ' '
            after = s[i + len(token)] if i + len(token) < len(s) else ' '
            if token[0].isalnum():
                if (before.isalnum() or before in '_-') or (after.isalnum() or after in '_-'):
                    continue
            return i
    return -1


def rename_var(text, var, repl):
    return re.sub(r'(?<![\w\-\'])' + re.escape(var) + r'(?![\w\-\'])', lambda m: repl, text)


def mentions(text, var):
    return re.search(r'(?<![\w\-\'])' + re.escape(var) + r'(?![\w\-\'])', text) is not None


QUAL_CONSTS = set()       # constant names declared with several ranges (set in main)


def qualc(name, rng):
    """A constant, sort-qualified when its name is shared by several sorts: `size(nil)` is ambiguous
    between two List instances, `size((nil).List{Data})` is not."""
    if rng and name in QUAL_CONSTS:
        return '(%s).%s' % (name, rng)
    return name


def render(name, args, nested=True, rng=None):
    """Render an operator application in Maude syntax, given argument texts."""
    if not args:
        return qualc(name, rng)
    if '_' not in name:
        return '%s(%s)' % (name, ', '.join(args))
    if name == '_,_':
        return '(%s, %s)' % (args[0], args[1])
    if name.endswith('_') and not name.startswith('_') and name.count('_') == 1:
        return '%s %s' % (name[:-1], args[0])
    pieces = name.split('_')
    if len(pieces) != len(args) + 1:
        raise GenError('cannot render operator %s with %d args' % (name, len(args)))
    out = []
    for k, a in enumerate(args):
        if pieces[k]:
            out.append(pieces[k])
        out.append(a)
    if pieces[-1]:
        out.append(pieces[-1])
    txt = ' '.join(out)
    return '(%s)' % txt if nested else txt


# ---------------------------------------------------------------- scanning

class Unit(object):
    def __init__(self, kind, start, end, text, dmc):
        self.kind = kind      # head | end | cmd | stmt | block | dmcend
        self.start = start
        self.end = end        # inclusive line index
        self.text = text      # normalized code text (statements/headers), or raw block lines
        self.dmc = dmc        # directive strings attached (in order)
        self.file = None
        self.line = start + 1


STMT_START_KW = {'eq', 'ceq', 'rl', 'crl', 'op', 'ops', 'var', 'vars', 'sort', 'sorts', 'subsort',
                 'subsorts', 'pr', 'protecting', 'inc', 'including', 'extending', 'mb', 'cmb', 'msg',
                 'msgs', 'class', 'subclass'} | HEAD_KW | END_KW | CMD_KW


def stmt_end_follows(masked, kinds, j, endcol):
    """A standalone '.' ends a statement only when the next code token starts a new unit (Maude
    struct access such as `E1 . E2` also uses a standalone dot)."""
    rest = masked[j][endcol:].split()
    if rest:
        return rest[0] in STMT_START_KW
    for k in range(j + 1, len(masked)):
        if kinds[k] == 'code' and masked[k].strip():
            return masked[k].split()[0] in STMT_START_KW
    return True


def scan_units(lines, fname):
    """Split raw lines into units. Returns (units, line_kinds)."""
    n = len(lines)
    kinds = [None] * n      # 'gap' | 'dmc' | 'code' | 'block'
    codes = [''] * n        # code part (strings kept)
    masked = [''] * n
    i = 0
    while i < n:
        s = lines[i].strip()
        if s.startswith('---('):
            j = i
            while '---)' not in lines[j]:
                j += 1
                if j >= n:
                    raise GenError('%s:%d: unterminated ---( block' % (fname, i + 1))
            for k in range(i, j + 1):
                kinds[k] = 'block'
            i = j + 1
            continue
        m = DIRECTIVE_RE.match(lines[i])
        if m:
            kinds[i] = 'dmc'
            i += 1
            continue
        code = code_part(lines[i])
        codes[i] = code
        masked[i] = mask_strings(code)
        kinds[i] = 'code' if masked[i].strip() else 'gap'
        i += 1

    units, pending = [], []
    i = 0
    while i < n:
        k = kinds[i]
        if k == 'dmc':
            d = DIRECTIVE_RE.match(lines[i]).group(1)
            if d == 'end':
                if pending:
                    raise GenError('%s:%d: directive before ---@dmc end' % (fname, i + 1))
                units.append(Unit('dmcend', i, i, '', []))
            else:
                pending.append(d)
            i += 1
            continue
        if k == 'gap':
            if pending:
                raise GenError('%s:%d: ---@dmc directive must be immediately followed by a statement'
                               % (fname, i + 1))
            i += 1
            continue
        if k == 'block':
            j = i
            while kinds[j] == 'block':
                j += 1
            # block runs: contiguous block lines; a block unit is the whole ---( ... ---) region
            units.append(Unit('block', i, j - 1, '\n'.join(lines[i:j]), pending))
            pending = []
            i = j
            continue
        # code unit
        first = masked[i].split()[0]
        if first in END_KW:
            if pending:
                raise GenError('%s:%d: directive before %s' % (fname, i + 1, first))
            units.append(Unit('end', i, i, first, []))
            i += 1
            continue
        if first in CMD_KW:
            units.append(Unit('cmd', i, i, norm(codes[i]), []))
            pending = []
            i += 1
            continue
        if first in HEAD_KW:
            term = r'(?<![\w\-])is(?![\w\-])'
            kind = 'head'
        else:
            term = r'(?<!\S)\.(?!\S)'
            kind = 'stmt'
        j = i
        found = None
        while j < n:
            if kinds[j] == 'dmc' or kinds[j] == 'gap' or kinds[j] == 'code' or kinds[j] == 'block':
                if kinds[j] == 'dmc' and j > i:
                    raise GenError('%s:%d: ---@dmc directive inside a statement' % (fname, j + 1))
                if kinds[j] == 'block' and j > i:
                    raise GenError('%s:%d: ---( block inside a statement' % (fname, j + 1))
            if kinds[j] == 'code':
                if kind == 'stmt':
                    for m in re.finditer(term, masked[j]):
                        if stmt_end_follows(masked, kinds, j, m.end()):
                            found = (j, m.end())
                            break
                    if found is not None:
                        break
                else:
                    m = re.search(term, masked[j])
                    if m:
                        found = (j, m.end())
                        break
            j += 1
        if found is None:
            raise GenError('%s:%d: unterminated %s' % (fname, i + 1, kind))
        last, endcol = found
        if masked[last][endcol:].strip():
            raise GenError('%s:%d: more than one statement on a line is not supported' % (fname, last + 1))
        pieces = []
        for k in range(i, last + 1):
            if kinds[k] == 'code':
                pieces.append(codes[k][:endcol] if k == last else codes[k])
        text = norm(' '.join(pieces))
        units.append(Unit(kind, i, last, text, pending))
        pending = []
        i = last + 1
    if pending:
        raise GenError('%s: dangling ---@dmc directive at end of file' % fname)
    return units, kinds


def dominant_eol(raw):
    crlf = raw.count('\r\n')
    lf = raw.count('\n') - crlf
    return '\r\n' if crlf > lf else '\n'


def split_lines_keep(raw):
    """Lines without EOL, plus their original EOL."""
    out = []
    for ln in raw.splitlines(True):
        if ln.endswith('\r\n'):
            out.append((ln[:-2], '\r\n'))
        elif ln.endswith('\n'):
            out.append((ln[:-1], '\n'))
        else:
            out.append((ln, ''))
    return out


# ---------------------------------------------------------------- statements

def parse_eq(text):
    """Parse eq/ceq statement text. Returns dict or None when not an equation."""
    m = re.match(r'^(c?eq)\s', text)
    if not m or not text.endswith('.'):
        return None
    kw = m.group(1)
    body = text[len(kw):].strip()[:-1].strip()
    attrs = None
    am = re.search(r'\[([^\[\]]*)\]\s*$', body)
    if am:
        attrs = am.group(1).strip()
        body = body[:am.start()].strip()
    eqpos = -1
    depth = 0
    for k, ch in enumerate(body):
        if ch in '([{':
            depth += 1
        elif ch in ')]}':
            depth -= 1
        elif depth == 0 and ch == '=' and (k == 0 or body[k - 1] == ' ') and k + 1 < len(body) \
                and body[k + 1] == ' ':
            eqpos = k
            break
    if eqpos < 0:
        return None
    lhs = body[:eqpos].strip()
    rest = body[eqpos + 1:].strip()
    cond = None
    if kw == 'ceq':
        ip = find_top(rest, 'if')
        if ip < 0:
            raise GenError('ceq without if: %s' % text)
        cond = rest[ip + 2:].strip()
        rhs = rest[:ip].strip()
    else:
        rhs = rest
    fn, args = None, None
    fm = re.match(r'^([^\s()]+)\((.*)\)$', lhs)
    if fm and group_closes_at_end(lhs, len(fm.group(1))):
        fn = fm.group(1)
        args = split_top(fm.group(2), ',', angles=True)
    return {'kw': kw, 'lhs': lhs, 'rhs': rhs, 'cond': cond, 'attrs': attrs,
            'fn': fn, 'args': args, 'owise': attrs is not None and attrs.split() == ['owise']}


def group_closes_at_end(s, i):
    """True when the parenthesis opened at s[i] is closed by the last character."""
    depth = 0
    in_str = False
    for k in range(i, len(s)):
        ch = s[k]
        if ch == '"':
            in_str = not in_str
        if in_str:
            continue
        if ch in '([{':
            depth += 1
        elif ch in ')]}':
            depth -= 1
            if depth == 0:
                return k == len(s) - 1
    return False


def parse_op_decl(text):
    """op/ops NAMES : DOMS -> RANGE ... . Returns list of (name, doms, rng) or []."""
    m = re.match(r'^ops?\s+(.*?)\s*:\s*(.*?)\s*->\s*([^\s\[]+)', text)
    if not m:
        m2 = re.match(r'^ops?\s+(.*?)\s*:\s*(.*?)\s*\[', text)
        if m2 and '->' not in m2.group(2):
            return []
        return []
    names = m.group(1).split()
    doms = m.group(2).split()
    rng = m.group(3)
    return [(nm, doms, rng) for nm in names]


def neg_cond(c):
    c = c.strip()
    depth_ok = find_top(c, ' and ') < 0 and find_top(c, ' or ') < 0 and find_top(c, '/\\') < 0 \
        and find_top(c, 'not ') < 0 and find_top(c, ' = ') < 0
    if depth_ok:
        ops = []
        for tok in ('<=', '>=', '<', '>'):
            pos = 0
            while True:
                k = find_top(c[pos:], ' ' + tok + ' ')
                if k < 0:
                    break
                ops.append((pos + k, tok))
                pos = pos + k + 1
        if len(ops) == 1:
            k, tok = ops[0]
            swap = {'<': '>=', '<=': '>', '>': '<=', '>=': '<'}[tok]
            return c[:k] + ' ' + swap + ' ' + c[k + len(tok) + 2:]
    return 'not (%s)' % c


def parse_directive(d):
    """Return (key, value) for a directive string."""
    if d in ('keep-builtin', 'replace', 'end', 'qid-subsort'):
        return d, None
    m = re.match(r"^restrict\((.*)\)$", d)
    if m:
        pairs = []
        for part in m.group(1).split(','):
            if '=' not in part:
                raise GenError('bad restrict item: %s' % part)
            v, srt = [x.strip() for x in part.split('=', 1)]
            pairs.append((v, srt))
        return 'restrict', pairs
    m = re.match(r"^close-sort\((\w[\w\-']*)=(\w[\w\-']*)\)$", d)
    if m:
        return 'close', (m.group(1), m.group(2))
    m = re.match(r"^qid-literals\((\w[\w\-']*)\)$", d)
    if m:
        return 'qid-literals', m.group(1)
    if d == 'owise drop':
        return 'owise', ('drop', None)
    m = re.match(r'^owise cond\((.*)\)$', d)
    if m:
        return 'owise', ('cond', m.group(1))
    m = re.match(r'^owise enumerate\((arg\d+(?:, ?arg\d+)+)\)$', d)
    if m:
        return 'owise', ('enumerate-multi', [int(x.strip()[3:]) - 1 for x in m.group(1).split(',')])
    m = re.match(r"^owise enumerate\((\w[\w\-']*)\)$", d)
    if m:
        am = re.match(r'^arg(\d+)$', m.group(1))
        return 'owise', ('enumerate', int(am.group(1)) if am else m.group(1))
    m = re.match(r'^owise (recurse|shapes)\((.*)\)$', d)
    if m:
        kv = {}
        for part in m.group(2).split(', '):
            if '=' not in part:
                raise GenError('bad %s option: %s' % (m.group(1), part))
            k, v = part.split('=', 1)
            kv[k.strip()] = v.strip()
        need = ['sep', 'empty'] + (['elem'] if m.group(1) == 'shapes' else [])
        if any(x not in kv for x in need):
            raise GenError('%s needs %s: %s' % (m.group(1), ', '.join(x + '=' for x in need), d))
        return 'owise', (m.group(1), kv)
    m = re.match(r'^exclude-ctors\((.*)\)$', d)
    if m:
        return 'exclude', [x.strip() for x in m.group(1).split(',') if x.strip()]
    raise GenError('unknown directive: ---@dmc %s' % d)


# ---------------------------------------------------------------- reflection

class Reflection(object):
    """Per-module ctor / subsort / owise data from the ORIGINAL spec."""

    def __init__(self, data):
        self.mods = data['modules']

    def has_module(self, m):
        return m in self.mods

    def ctors(self, m):
        return self.mods.get(m, {}).get('ctors', [])

    def ctor(self, m, name):
        for c in self.ctors(m):
            if c['name'] == name:
                return c
        return None

    def supers(self, m):
        sup = {}
        for a, b in self.mods.get(m, {}).get('subs', []):
            sup.setdefault(a, set()).add(b)
        changed = True
        while changed:
            changed = False
            for a in list(sup):
                new = set()
                for b in sup[a]:
                    new |= sup.get(b, set())
                if not new <= sup[a]:
                    sup[a] |= new
                    changed = True
        return sup

    def own_ops(self, m):
        return self.mods.get(m, {}).get('own', {})

    def has_op(self, m, name, doms):
        for f in self.mods.get(m, {}).get('ops', []):
            if f['name'] == name and f['doms'] == doms:
                return True
        return False

    def value_ops(self, m):
        """Constructors plus operators without arguments (the values of a sort) visible in m."""
        res = [dict(c) for c in self.ctors(m)]
        for f in self.mods.get(m, {}).get('ops', []):
            if not f['doms'] and 'ctor' not in f['attrs']:
                res.append({'name': f['name'], 'doms': [], 'rng': f['rng'], 'attrs': f['attrs']})
        return res

    def op_ranges(self, m, name, arity):
        """Ranges of the operators named `name` with `arity` arguments visible in module m."""
        res = set()
        for f in self.mods.get(m, {}).get('ops', []):
            if f['name'] == name and len(f['doms']) == arity:
                res.add(f['rng'])
        for c in self.ctors(m):
            if c['name'] == name and len(c['doms']) == arity:
                res.add(c['rng'])
        return res

    def drop_ops(self, names):
        """Remove the operators named in names (the qid-literals operators, dropped from the DMC build)."""
        for d in self.mods.values():
            d['ctors'] = [c for c in d.get('ctors', []) if c['name'] not in names]
            d['ops'] = [f for f in d.get('ops', []) if f['name'] not in names]
            if 'own' in d:
                d['own'] = dict((k, v) for k, v in d['own'].items() if k not in names)

    def owise_count(self, m):
        return self.mods.get(m, {}).get('owise', 0)


def unlimited_stack():
    """Maude needs an unlimited C stack for the large reflection terms (same as DM-Check runs)."""
    if resource is None:
        return None

    def _set():
        try:
            resource.setrlimit(resource.RLIMIT_STACK, (resource.RLIM_INFINITY, resource.RLIM_INFINITY))
        except (ValueError, OSError):
            pass
    return _set


def maude_run(maude, cwd, infile, timeout=3600):
    with open(infile) as fh:
        # Maude resolves relative loads through $PWD, which cwd= does not change
        proc = subprocess.run([maude, '-no-banner', '-no-advise'], cwd=cwd, stdin=fh,
                              env=dict(os.environ, PWD=cwd),
                              stdout=subprocess.PIPE, stderr=subprocess.STDOUT, universal_newlines=True,
                              timeout=timeout, preexec_fn=unlimited_stack())
    return proc.stdout


def run_reflection(src, cache_dir, mods, maude, fresh):
    """Reflection of every module in `mods` (one Maude process per module, so one failure stays local)."""
    os.makedirs(cache_dir, exist_ok=True)
    h = hashlib.sha256()
    h.update(GEN_VERSION.encode())
    h.update(','.join(sorted(mods)).encode())
    for fn in sorted(os.listdir(src)):
        if fn.endswith('.maude'):
            with open(os.path.join(src, fn), 'rb') as f:
                h.update(fn.encode() + f.read())
    with open(DUMP_MAUDE, 'rb') as f:
        h.update(f.read())
    key = h.hexdigest()
    cache_path = os.path.join(cache_dir, 'reflect.json')
    if not fresh and os.path.exists(cache_path):
        with open(cache_path) as f:
            data = json.load(f)
        if data.get('key') == key:
            return Reflection(data), False
    mods_data = {}
    failed = []
    for m in mods:
        lines = ['load %s' % os.path.join(src, 'main.maude'), 'load %s' % DUMP_MAUDE, 'select DMC-DUMP .',
                 "red dump('%s) ." % m, 'quit']
        path = os.path.join(cache_dir, 'reflect-%s.in' % m)
        with open(path, 'w') as f:
            f.write('\n'.join(lines) + '\n')
        out = maude_run(maude, src, path)
        results = re.findall(r'result String:\s*"(M@@[^"]*)"', out)
        if len(results) != 1:
            failed.append((m, out[-600:]))
            continue
        res = results[0]
        body = res[len('M@@'):]
        name, rest = body.split('@@', 1)
        name = name.replace('`', '')
        entry = {'ctors': [], 'ops': [], 'subs': [], 'owise': 0}
        for rec in rest.split('%%'):
            if not rec:
                continue
            p = rec.split('@@')
            if p[0] == 'C':
                entry['ctors'].append({'name': p[1].replace('`', ''),
                                       'doms': [x.replace('`', '') for x in p[2].split()],
                                       'rng': p[3].replace('`', ''), 'attrs': p[4].split()})
            elif p[0] == 'D':
                entry.setdefault('own', {})[p[1].replace('`', '')] = [x.replace('`', '') for x in p[2].split()]
            elif p[0] == 'F':
                entry['ops'].append({'name': p[1].replace('`', ''),
                                     'doms': [x.replace('`', '') for x in p[2].split()],
                                     'rng': p[3].replace('`', ''), 'attrs': p[4].split()})
            elif p[0] == 'S':
                entry['subs'].append([p[1].replace('`', ''), p[2].replace('`', '')])
            elif p[0] == 'E':
                if 'owise' in p[1].split():
                    entry['owise'] += 1
        mods_data[m] = entry
    if failed:
        raise GenError('reflection failed for modules %s; first Maude output tail:\n%s'
                       % ([f[0] for f in failed], failed[0][1]))
    data = {'key': key, 'modules': mods_data}
    with open(cache_path, 'w') as f:
        json.dump(data, f)
    return Reflection(data), True


# ---------------------------------------------------------------- generator state

class FileInfo(object):
    def __init__(self, name, raw):
        self.name = name
        self.raw = raw
        self.eol = dominant_eol(raw)
        self.lines = split_lines_keep(raw)
        self.units = []
        self.kinds = []


class Collect(object):
    """What pass A (permissive) found: sorts needing equality, views to upgrade, generated views."""

    def __init__(self):
        self.needs = {}
        self.view_needs = set()
        self.nested = {}
        self.numeq = set()
        self.uses = {}          # module -> sorts whose equality it uses (the declaring module is imported)
        self.list_users = {}    # element view -> modules with pr LIST{view}
        self.triv_users = {}    # value view -> modules with pr MAP{key,view}


class Gen(object):
    def __init__(self, reflection):
        self.refl = reflection
        self.views = {}         # name -> dict(from, to, elt, file, line, keep)
        self.mod_eqs = {}       # module -> list of eq dicts (+ unit ref)
        self.mod_ops = {}       # module -> {name: doms} (own operators)
        self.mod_vars = {}      # module -> {var: sort}
        self.mod_sorts = {}     # module -> set of sorts declared
        self.sort_mod = {}      # sort -> first in-scope module declaring it
        self.mod_order = {}     # module -> position in load order (head)
        self.errors = []
        self.warnings = []
        self.manifest = []
        self.counts = {}
        self.replaces = []
        self.coll = None
        self.plan = None
        self.closed = {}        # closed sort -> dict(ctor, mod, file, line)   (close-sort directive)
        self.qid_sort = None    # sort of the Qid literals (qid-subsort directive)
        self.const_terms = {}   # constant name of a closed sort -> ctor term C(k)
        self.qid_terms = {}     # Qid literal name -> ctor term of qid_sort
        self.closed_items = {}  # closed sort -> [(kind, name)] in number order
        self.qid_ops = {}       # operator name -> closed sort (qid-literals directive: op : Qid -> S)
        self.qop_terms = {}     # (operator, Qid literal) -> ctor term of its closed sort
        self.qop_items = {}     # closed sort -> [(operator, Qid literal)] in number order
        self.aux = set()        # auxiliary argument sorts needing DM-AUX-EQ (Map{Var,Nat}, List{X})
        self.aux_extra = []     # equality blocks of prelude sorts, placed in DM-AUX-EQ
        self.make_ctor = set()  # (constant, sort) of non-constructor constants made [ctor] in the DMC build
        self.list_eq = set()    # element views X such that List{X} has an equality (listEqX)
        self.dm_imports = {}    # generated module -> its imports (checked by check_import_safety)
        self.dm_for = {}        # main or test module -> equation modules it imports (its own variants)
        self.dm_use = {}        # main module -> the imports of its header (its bundle, or its single dm_for entry)
        self.main_order = []    # modules of main.maude in file order (names the bundles of main's modules)
        self.graph = {}         # module -> modules it imports (original texts of the loaded files and tests)
        self.vis = {}           # main or test module -> its import closure (source modules and modules of tests)

    def err(self, fi, line, msg):
        self.errors.append('%s:%d: %s' % (fi, line, msg))

    def warn(self, msg):
        self.warnings.append(msg)

    def count(self, fname, key):
        d = self.counts.setdefault(fname, {})
        d[key] = d.get(key, 0) + 1

    def ctors(self, mod):
        return self.refl.ctors(mod)

    def ctor(self, mod, name):
        return self.refl.ctor(mod, name)

    def leq(self, mod, a, b):
        return a == b or b in self.refl.supers(mod).get(a, set())


def owise_sibling_errors(gen, new_text):
    """An [owise] equation is expanded (or guarded) per module, so it sees only the siblings of its own module.
    Any same-op sibling in another module is an error: keep all equations of an op with its owise in one module.
    Owise equations with `cond(...)` (pattern-local) and `drop` are exempt, and so are those with a replace block."""
    errors = []
    for M in sorted(gen.mod_eqs):
        for e in gen.mod_eqs[M]:
            if not e.get('owise') or e.get('unit') is None:
                continue
            dirs = [parse_directive(d) for d in e['unit'].dmc]
            if any(k == 'replace' for k, _ in dirs):
                continue
            if any(k == 'owise' and val[0] in ('cond', 'drop') for k, val in dirs):
                continue
            sibs = [(M2, s_) for M2, ss in gen.mod_eqs.items() if M2 != M
                    for s_ in ss if s_['fn'] == e['fn'] and not s_.get('owise')]
            if sibs:
                errors.append('%s:%d: owise equation for %s in module %s has sibling equations in other modules '
                              '(%s); move them into %s' % (
                                  e['file'], e['line'], e['fn'], M,
                                  ', '.join(sorted(set('%s %s:%d' % (M2, x['file'], x['line']) for M2, x in sibs))), M))
    return errors


def mod_name_of_head(text):
    m = re.match(r'^(?:f|o|s)?(?:mod|th)\s+([^\s{]+)', text)
    return m.group(1) if m else None


def pass1(gen, fname, fi):
    """Collect views, modules, ops, vars, sorts and equations of one file."""
    cur_mod = None
    cur_view = None
    for u in fi.units:
        u.file = fname
        if u.kind == 'head':
            if u.text.split()[0] == 'view':
                m = re.match(r'^view\s+(\S+)\s+from\s+(\S+)\s+to\s+(\S+)\s+is$', u.text)
                if not m:
                    gen.err(fname, u.line, 'unsupported view header: %s' % u.text)
                    continue
                cur_view = m.group(1)
                cur_mod = None
                info = {'from': m.group(2), 'to': m.group(3), 'elt': None, 'file': fname,
                        'line': u.line, 'keep': False}
                for d in u.dmc:
                    key, val = parse_directive(d)
                    if key == 'keep-builtin':
                        info['keep'] = True
                    else:
                        raise GenError('%s:%d: directive %s not allowed on a view' % (fname, u.line, d))
                gen.views[cur_view] = info
            else:
                cur_mod = mod_name_of_head(u.text)
                cur_view = None
                gen.mod_order.setdefault(cur_mod, len(gen.mod_order))
                gen.mod_eqs.setdefault(cur_mod, [])
                gen.mod_ops.setdefault(cur_mod, {})
                gen.mod_vars.setdefault(cur_mod, {})
                gen.mod_sorts.setdefault(cur_mod, set())
        elif u.kind == 'end':
            cur_mod = None
            cur_view = None
        elif u.kind == 'stmt':
            t = u.text
            if cur_view is not None:
                m = re.match(r'^sort Elt to (\S+) \.$', t)
                if m:
                    gen.views[cur_view]['elt'] = m.group(1)
                continue
            if cur_mod is None:
                continue
            if t.startswith('op ') or t.startswith('ops '):
                for nm, doms, rng in parse_op_decl(t):
                    gen.mod_ops[cur_mod][nm] = doms
                for d in u.dmc:
                    key, val = parse_directive(d)
                    if key == 'qid-literals':
                        decl = parse_op_decl(t)
                        if len(decl) != 1 or decl[0][1] != ['Qid'] or decl[0][2] != val:
                            raise GenError('%s:%d: qid-literals(%s) needs one operator declared as Qid -> %s'
                                           % (fname, u.line, val, val))
                        gen.qid_ops[decl[0][0]] = val
            elif re.match(r'^vars?\s', t):
                m = re.match(r'^vars?\s+(.*?)\s*:\s*(\S+)\s*\.$', t)
                if m:
                    for nm in m.group(1).split():
                        gen.mod_vars[cur_mod][nm] = m.group(2)
            elif re.match(r'^subsort\s+Qid\s*<\s*(\S+)\s*\.$', t) and any(k == 'qid-subsort' for k, _ in map(parse_directive, u.dmc)):
                gen.qid_sort = re.match(r'^subsort\s+Qid\s*<\s*(\S+)\s*\.$', t).group(1)
            elif re.match(r'^sorts?\s', t):
                for d in u.dmc:
                    key, val = parse_directive(d)
                    if key == 'close':
                        if val[0] not in t.split()[1:]:
                            raise GenError('%s:%d: close-sort(%s=..) on a statement that does not declare %s'
                                           % (fname, u.line, val[0], val[0]))
                        gen.closed[val[0]] = {'ctor': val[1], 'mod': cur_mod, 'file': fname, 'line': u.line}
                for s in t[t.index(' '):-1].split():
                    gen.mod_sorts[cur_mod].add(s)
                    gen.sort_mod.setdefault(s, cur_mod)
            elif re.match(r'^c?eq\s', t):
                e = parse_eq(t)
                if e is None:
                    continue
                e['file'] = fname
                e['line'] = u.line
                e['unit'] = u
                gen.mod_eqs[cur_mod].append(e)


# ---------------------------------------------------------------- closed sorts (DMC only)
#
# close-sort(S=C) on the declaration of S adds one constructor C : Nat -> S, so that equality on S is
# structural (variant).  Every constant of S, in the scope and in the derived tests, becomes an
# abbreviation of C(k): `eq c = C(k) .` (its declaration loses [ctor]).  The Qid literals of the sort
# named by qid-subsort (`subsort Qid < Var`, which is dropped in the DMC build) become C(k) too.  Numbers
# are per closed sort: constants and Qid literals in sorted order of ('c', name) and ('q', name).
# Every equation and rule of the DMC build has its constants and Qid literals rewritten (constants reduce
# to C(k) anyway, so the rewriting is meaning-preserving; a pattern with a constant would never match).

CONST_DECL_RE = re.compile(r'^ops?\s+(.+?)\s*:\s*->\s*(\S+)\s*(?:\[(.*?)\])?\s*\.$')
CLOSED_TOKEN_RE = re.compile(r"\"[^\"]*\"|(?<![\w\-'$#%:])'([A-Za-z][\w\-]*)|(?<![\w\-'$#%:])([A-Za-z][\w\-']*)")
QID_LIT_RE = re.compile(r"(?<![\w\-'$#%])'([A-Za-z][\w\-]*)")
REWRITE_HEADS = {'eq', 'ceq', 'rl', 'crl', 'search', 'red', 'reduce', 'rewrite', 'frewrite', 'match',
                 'xmatch', 'narrow', 'unify', 'variant', 'ceq'}


def const_decl(gen, t):
    """(names, sort, attrs) for a constant declaration of a closed sort, else None."""
    m = CONST_DECL_RE.match(t)
    if not m or m.group(2) not in gen.closed:
        return None
    return m.group(1).split(), m.group(2), (m.group(3) or '').split()


def const_decl_text(names, rng, attrs):
    keep = [a for a in attrs if a != 'ctor']
    kw = 'ops' if len(names) > 1 else 'op'
    body = '%s %s : -> %s' % (kw, ' '.join(names), rng)
    return body + (' [%s] .' % ' '.join(keep) if keep else ' .')


def qop_call_re(gen):
    """op('lit) for the qid-literals operators: the operator name and the Qid literal."""
    names = '|'.join(re.escape(o) for o in sorted(gen.qid_ops))
    return re.compile(r"(?<![\w\-'$#%:])(" + names + r")\s*\(\s*'([A-Za-z][\w\-]*)\s*\)")


def qop_bare_re(gen):
    """An application of a qid-literals operator (any argument)."""
    names = '|'.join(re.escape(o) for o in sorted(gen.qid_ops))
    return re.compile(r"(?<![\w\-'$#%:])(" + names + r")\s*\(")


def qop_rewrite(gen, code):
    """op('lit) of the qid-literals operators replaced by the ctor term (the rest of code is kept)."""
    if not gen.qop_terms:
        return code
    return qop_call_re(gen).sub(lambda m: gen.qop_terms[(m.group(1), m.group(2))], code)


def closed_rewrite_code(gen, code):
    """Constants and Qid literals of closed sorts replaced by their constructor terms (code only)."""
    if not gen.const_terms and not gen.qid_terms and not gen.qop_terms:
        return code
    code = qop_rewrite(gen, code)

    def sub(m):
        if m.group(1) is not None:
            if m.group(1) not in gen.qid_terms:
                raise GenError("Qid literal '%s has no number" % m.group(1))
            return gen.qid_terms[m.group(1)]
        if m.group(2) is not None:
            name = m.group(2)
            term = gen.const_terms.get(name)
            # an operator application, an attribute name (`name :`) or a sort after a colon is not a constant
            if term is None or re.match(r'\s*(\(|:(?!:))', code[m.end():]):
                return name
            return term
        return m.group(0)
    return CLOSED_TOKEN_RE.sub(sub, code)


def closed_rewrite_line(gen, line):
    code = code_part(line)
    return closed_rewrite_code(gen, code) + line[len(code):]


def closed_numbering(gen, test_texts):
    """Assign numbers to the constants and Qid literals of every closed sort (src and tests)."""
    closed = gen.closed
    consts = dict((s, set()) for s in closed)
    for M in gen.refl.mods:
        for c in gen.refl.value_ops(M):
            if not c['doms'] and c['rng'] in closed:
                consts[c['rng']].add(c['name'])
    for text in test_texts:
        tunits, _ = scan_units(text.splitlines(), 'test')
        for u in tunits:
            if u.kind == 'stmt':
                d = const_decl(gen, u.text)
                if d:
                    consts[d[1]].update(d[0])
    qids, qop_lits = set(), set()
    qop_re = None
    if gen.qid_ops:
        qop_re, bare_re = qop_call_re(gen), qop_bare_re(gen)
    for text in test_texts + gen.src_texts:
        for line in text.splitlines():
            code = mask_strings(code_part(line))
            if qop_re is not None:
                qop_lits.update(qop_re.findall(code))
                code = qop_re.sub(' ', code)
                bad = bare_re.search(code)
                if bad:
                    raise GenError('%s applied to something other than a Qid literal in the DMC scope: %s'
                                   % (bad.group(1), line.strip()))
            qids.update(QID_LIT_RE.findall(code))
    if qids and gen.qid_sort is None:
        raise GenError('Qid literals but no qid-subsort directive: %s' % sorted(qids)[:5])
    refl_names = set()
    for M in gen.refl.mods:
        refl_names.update(f['name'] for f in gen.refl.mods[M].get('ops', []))
        refl_names.update(c['name'] for c in gen.refl.ctors(M))
    for S in closed:
        cn = closed[S]['ctor']
        if cn in refl_names:
            raise GenError('close-sort constructor %s already declared' % cn)
    gen.const_terms, gen.qid_terms, gen.closed_items = {}, {}, {}
    gen.qop_terms, gen.qop_items = {}, {}
    for S in sorted(closed):
        items = [('c', n) for n in sorted(consts[S])]
        if S == gen.qid_sort:
            items += [('q', n) for n in sorted(qids)]
        gen.qop_items[S] = sorted(x for x in qop_lits if gen.qid_ops[x[0]] == S)
        items += [('l', x) for x in gen.qop_items[S]]
        gen.closed_items[S] = items
        for k, (kind, n) in enumerate(items):
            term = '%s(%d)' % (closed[S]['ctor'], k)
            if kind == 'l':
                gen.qop_terms[n] = term
                continue
            table = gen.const_terms if kind == 'c' else gen.qid_terms
            if kind == 'c':
                if n in gen.const_terms:
                    raise GenError('constant %s declared at two closed sorts' % n)
            table[n] = term
    clash = set(gen.const_terms) & set(v for vs in gen.mod_vars.values() for v in vs)
    if clash:
        raise GenError('constants of closed sorts also used as variables: %s' % sorted(clash))


def ctor_decl_mods(gen, c):
    if 'decl' in c:
        return [c['decl']]
    return decl_modules(gen, c)


def eq_lhs_roots(gen):
    cache = gen.__dict__.get('eqroot_cache')
    if cache is None:
        cache = set()
        for M, es in gen.mod_eqs.items():
            for e in es:
                cache.add(e['lhs'].split()[0] if e['lhs'].split() else '')
        gen.eqroot_cache = cache
    return cache


def ac_built_sort(gen, S):
    """Sorts with an associative/commutative operator into them (other than the list sorts that have their
    own equalities in DM-AUX-EQ): their elements are not decomposable by constructors alone."""
    cache = gen.__dict__.setdefault('acsort_cache', {})
    if S not in cache:
        found = False
        for M in gen.refl.mods:
            for c in gen.refl.mods[M].get('ctors', []) + gen.refl.mods[M].get('ops', []):
                if c['doms'] and ('assoc' in c['attrs'] or 'comm' in c['attrs']) and gleq(gen, c['rng'], S):
                    found = True
        cache[S] = found and S not in LIST_EXP
    return cache[S]


def dmc_ctors(gen, S):
    """Constructors of S in the DMC build: the reflected constructors and constants (constants of closed
    sorts are abbreviations, so they are left out), plus the closed constructors of S and its closed
    subsorts.  Over all reflected modules (a constructor may be declared next to its sort)."""
    cache = gen.__dict__.setdefault('dctor_cache', {})
    if S in cache:
        return cache[S]
    out, seen = [], set()
    for M in sorted(gen.refl.mods):
        for c in gen.refl.value_ops(M):
            if c['rng'].startswith('[') or 'assoc' in c['attrs'] or 'comm' in c['attrs']:
                continue
            if not gleq(gen, c['rng'], S):
                continue
            if not c['doms'] and c['rng'] in gen.closed:
                continue
            if not decl_modules(gen, c):
                continue
            if not c['doms'] and 'ctor' not in c['attrs']:
                if c['name'] in eq_lhs_roots(gen):
                    continue                 # a reducible constant (alias of a constructor term, e.g. null)
                # a constant without equations is a constructor in the DMC build (variant analysis)
                gen.make_ctor.add((c['name'], c['rng']))
            key = (c['name'], tuple(c['doms']), c['rng'])
            if key not in seen:
                seen.add(key)
                out.append(c)
    for R in sorted(gen.closed):
        if gleq(gen, R, S):
            out.append({'name': gen.closed[R]['ctor'], 'doms': ['Nat'], 'rng': R, 'attrs': ['ctor'],
                        'decl': gen.closed[R]['mod']})
    out.sort(key=lambda c: (c['name'], c['doms'], c['rng']))
    cache[S] = out
    return out


def is_late(gen, S):
    """Equality of S must follow the modules declaring its constructors (not visible from sort_mod[S]),
    and the auxiliary equalities (DM-AUX-EQ), which come last."""
    M = gen.sort_mod.get(S)
    for c in dmc_ctors(gen, S):
        if M is None or M not in ctor_decl_mods(gen, c):
            return True
        if any(d == MAP_VN or d.startswith('List{') or d in LIST_EXP or d not in gen.sort_mod
               and d not in NUM_SORTS and d != 'Bool' and not d.startswith('Set{') for d in c['doms']):
            return True
    return False


def triv_name(a):
    """Companion view of an upgraded view a for a TRIV parameter (LIST-DM elements, MAP-DM values)."""
    return a + '-TRIV'


def list_eq_mod(a):
    return 'LIST-EQ-' + re.sub(r'\W', '', a)


def list_eq_fn(a):
    """Equality of List{a}: a function of its own name (not `.=.`, whose constants nil are overloaded
    across list sorts); declared in LIST-EQ-a, defined in DM-AUX-EQ."""
    return 'listEq' + re.sub(r'\W', '', a)


def arg_eq(gen, x, y, srt):
    """Equality of two constructor arguments of sort srt (the sort's own equality is structural).
    Map{Var,Nat} and List{X} use the auxiliary equalities of DM-AUX-EQ (generated once)."""
    if srt in NUM_SORTS:
        return 'numEq(%s, %s)' % (x, y)
    if srt == 'Bool':
        return 'boolEq(%s, %s)' % (x, y)
    if srt.startswith('Set{'):
        return '(%s subset %s and %s subset %s)' % (x, y, y, x)
    if srt == MAP_VN:
        gen.aux.add(srt)
        return 'mapEq(%s, %s)' % (x, y)
    if srt in LIST_EXP:
        gen.aux.add('ExpList')
        return 'expListEq(%s, %s)' % (x, y)
    if srt.startswith('List{') and srt.endswith('}'):
        gen.list_eq.add(srt[5:-1])
        return '%s(%s, %s)' % (list_eq_fn(srt[5:-1]), x, y)
    return '(%s .=. %s)' % (x, y)


MAP_VN = 'Map{Var,Nat}'
AUX_MAP = 'DM-AUX-EQ-MAP'
AUX_EXP = 'DM-AUX-EQ-EXP'
LIST_EXP = ('ExpList', 'ValueList', 'VarList')


# Element sorts of an ExpList: Value and Var (LExp) are subsorts of Exp, and PExp (T X) and FExp (F(EL) and
# F()) are constructor sorts.  Each kind has its own equality: the `.=.` of its parts.
EXP_ELEM_SORTS = ('Value', 'Var', 'ImpType', 'FuncId')
EXP_CTOR_MOD = 'IMP-EXP'     # declares the PExp constructor (ImpType Var) and the FExp ones (FuncId ...)
# (kind, left pattern, right pattern) of each element kind: Value, Var, PExp (T X), FExp call F(EL), FExp F()
EXP_KINDS = [
    ('V', 'VA', 'VB'),
    ('X', 'XA', 'XB'),
    ('P', '(TA XA)', '(TB XB)'),
    ('C', 'FA(EA)', 'FB(EB)'),
    ('N', 'FA()', 'FB()'),
]
EXP_SAME_RHS = {'V': 'VA .=. VB', 'X': 'XA .=. XB', 'P': '(TA .=. TB) and (XA .=. XB)',
                'C': '(FA .=. FB) and expListEq(EA, EB)', 'N': 'FA .=. FB'}


def exp_elem_eqs():
    """expElemEq over the kinds of Exp: same kind -> the equality of the parts; different kinds -> false."""
    out = []
    for i, (ka, la, _) in enumerate(EXP_KINDS):
        for j, (kb, _, rb) in enumerate(EXP_KINDS):
            if i == j:
                out.append('  eq expElemEq(%s, %s) = %s .' % (la, rb, EXP_SAME_RHS[ka]))
            else:
                out.append('  eq expElemEq(%s, %s) = false .' % (la, rb))
    return out


def aux_module(gen):
    """DM-AUX-EQ-MAP and DM-AUX-EQ-EXP: owise-free equalities of the auxiliary argument sorts (Map{Var,Nat},
    ExpList).  Each imports only what its own equations use (aux_req), and an equation module imports the
    one it calls (late_variants), so it adds no module that the importing module does not see."""
    out = []
    if MAP_VN in gen.aux:
        imports = ['DM-BOOL', 'DM-INT'] + src_order(gen, aux_req(gen, 'map'))
        gen.dm_imports[AUX_MAP] = imports
        out.extend(['mod %s is' % AUX_MAP] + ['  pr %s .' % m for m in imports] + [
            '  op mapEq : Map{Var,Nat} Map{Var,Nat} -> Bool .',
            '  op mapSub : Map{Var,Nat} Map{Var,Nat} -> Bool .',
            '  op mapHas : Map{Var,Nat} Var Nat -> Bool .',
            '  vars M M\' : Map{Var,Nat} .',
            '  var D D\' : Var .',
            '  vars R R\' : Nat .',
            '  eq mapEq(M, M\') = mapSub(M, M\') and mapSub(M\', M) .',
            '  eq mapSub(empty, M\') = true .',
            '  eq mapSub(((D |-> R) ; M), M\') = mapHas(M\', D, R) and mapSub(M, M\') .',
            '  eq mapHas(empty, D, R) = false .',
            '  eq mapHas(((D\' |-> R\') ; M\'), D, R) = if D .=. D\' then numEq(R, R\') else mapHas(M\', D, R) fi .',
            'endm', ''])
    if 'ExpList' in gen.aux:
        imports = ['DM-BOOL', 'DM-INT'] + src_order(gen, aux_req(gen, 'exp'))
        gen.dm_imports[AUX_EXP] = imports
        out.extend(['mod %s is' % AUX_EXP] + ['  pr %s .' % m for m in imports] + [
            '  op expListEq : ExpList ExpList -> Bool .',
            '  op expElemEq : Exp Exp -> Bool .',
            # `.=.` of the element parts; the same profiles as the sort modules, so the declarations merge
            '  op _.=._ : Value Value -> Bool .',
            '  op _.=._ : Var Var -> Bool .',
            '  op _.=._ : ImpType ImpType -> Bool .',
            '  op _.=._ : FuncId FuncId -> Bool .',
            '  vars VA VB : Value .',
            '  vars XA XB : Var .',
            '  vars TA TB : ImpType .',
            '  vars FA FB : FuncId .',
            '  vars EA EB : ExpList .',
            '  vars E E\' : Exp .',
            '  vars L L\' : ExpList .',
            '  eq expListEq(nil, nil) = true .',
            '  eq expListEq(nil, (E\' , L\')) = false .',
            '  eq expListEq((E , L), nil) = false .',
            '  eq expListEq((E , L), (E\' , L\')) = expElemEq(E, E\') and expListEq(L, L\') .',
        ] + exp_elem_eqs() + ['endm', ''])
    return out


# ---------------------------------------------------------------- transforms

TOK_RE = re.compile(r'"[^"]*"|==|=/=|=>|->|[()\[\]{},]|[^\s()\[\]{},=]+|=|\S')
OPEN = {'(': ')', '[': ']', '{': '}'}
CLOSE = {')': '(', ']': '[', '}': '{'}
STOP = {'and', 'or', 'xor', 'not', 'if', 'then', 'else', '=', '==', '=/=', '=>', '->', ':=', '.',
        '/\\', '\\/', '==>', 'and-then', 'or-else', '|->', ';'}
NUMLIT = 'NUMLIT'


def tokenize(text):
    return [(m.start(), m.end(), m.group(0)) for m in TOK_RE.finditer(text)]


def group_end(toks, j):
    depth = 0
    for k in range(j, len(toks)):
        t = toks[k][2]
        if t in OPEN:
            depth += 1
        elif t in CLOSE:
            depth -= 1
            if depth == 0:
                return k
    raise GenError('unbalanced group in statement')


def group_start(toks, j):
    depth = 0
    for k in range(j, -1, -1):
        t = toks[k][2]
        if t in CLOSE:
            depth += 1
        elif t in OPEN:
            depth -= 1
            if depth == 0:
                return k
    raise GenError('unbalanced group in statement')


def left_bounds(toks, i):
    j, lo = i - 1, None
    while j >= 0:
        t = toks[j][2]
        if t in CLOSE:
            j = group_start(toks, j)
            lo = j
            j -= 1
            continue
        if t in OPEN or t in STOP or t == ',':
            break
        lo = j
        j -= 1
    if lo is None:
        raise GenError('equality without left operand')
    return lo, i - 1


def right_bounds(toks, i):
    k, hi = i + 1, None
    while k < len(toks):
        t = toks[k][2]
        if t in OPEN:
            k = group_end(toks, k)
            hi = k
            k += 1
            continue
        if t in CLOSE or t in STOP or t == ',':
            break
        hi = k
        k += 1
    if hi is None:
        raise GenError('equality without right operand')
    return i + 1, hi


def strip_all(s):
    s = s.strip()
    while True:
        t = strip_parens(s)
        if t == s:
            return s
        s = t


def infer_sort(gen, mod, s):
    s = strip_all(s)
    if re.fullmatch(r'-?\d+', s):
        return NUMLIT
    m = re.fullmatch(r"([A-Za-z][\w\-']*):([^\s()]+)", s)
    if m:
        return m.group(2)
    m = re.fullmatch(r"([A-Za-z][\w\-']*)\((.*)\)", s, re.S)
    if m:
        cands = gen.refl.op_ranges(mod, m.group(1), len(split_top(m.group(2), ',')))
    elif re.fullmatch(r"[A-Za-z][\w\-']*", s):
        if s in gen.mod_vars.get(mod, {}):
            return gen.mod_vars[mod][s]
        cands = gen.refl.op_ranges(mod, s, 0)
    else:
        return None
    if len(cands) > 1:
        sets = [c for c in cands if c.startswith('Set{')]
        if sets:
            return sorted(sets)[0]
    if len(cands) == 1:
        return list(cands)[0]
    return None


class Ctx(object):
    """Per-module state while transforming one file."""

    def __init__(self, gen, fname):
        self.gen = gen
        self.fname = fname
        self.mod = None
        self.view = None
        self.indent = ''


def note_numeq(gen, mod):
    if gen.coll is not None:
        gen.coll.numeq.add(mod)


def kind_of(gen, S, where, mod=None):
    """Equality kind for sort S at a use site: pairwise | structural | builtin."""
    if gen.coll is not None:
        gen.coll.needs.setdefault(S, set()).add(where)
        if mod is not None:
            gen.coll.uses.setdefault(mod, set()).add(S)
        return 'structural'
    return gen.plan.kinds.get(S, 'builtin')


def eq_text(gen, mod, a, b, S, neg, keep, where):
    """Parenthesized equality (or disequality) of a and b at sort S."""
    if S == NUMLIT or S in NUM_SORTS:
        note_numeq(gen, mod)
        core = '(numEq(%s, %s))' % (a, b)
    elif S.startswith(('Set{', 'NeSet{')):
        if b.strip() == 'empty':
            core = '(%s subset empty)' % a
        elif a.strip() == 'empty':
            core = '(%s subset empty)' % b
        else:
            core = '(%s subset %s and %s subset %s)' % (a, b, b, a)
    elif S.startswith('List{') and S.endswith('}') and kind_of(gen, S, where, mod) != 'builtin':
        core = '%s(%s, %s)' % (list_eq_fn(S[5:-1]), a, b)
    else:
        kind = kind_of(gen, S, where, mod)
        if kind in ('pairwise', 'structural'):
            core = '(%s .=. %s)' % (a, b)
        elif keep:
            return '(%s =/= %s)' % (a, b) if neg else '(%s == %s)' % (a, b)
        else:
            raise GenError('%s: no equality on sort %s for built-in comparison; annotate keep-builtin'
                           % (where, S))
    return ('not ' + core) if neg else core


def rewrite_text(ctx, text, where, keep=False):
    """Replace built-in == and =/= by generated equality terms (unless keep)."""
    if keep:
        return text
    gen = ctx.gen
    while True:
        toks = tokenize(text)
        idx = [i for i, t in enumerate(toks) if t[2] in ('==', '=/=')]
        if not idx:
            return text
        i = idx[0]
        ll, lh = left_bounds(toks, i)
        rl, rh = right_bounds(toks, i)
        a = text[toks[ll][0]:toks[lh][1]].strip()
        b = text[toks[rl][0]:toks[rh][1]].strip()
        S = infer_sort(gen, ctx.mod, a)
        if S is None or S == NUMLIT:
            S2 = infer_sort(gen, ctx.mod, b)
            if S2 is not None and S2 != NUMLIT:
                S = S2
            elif S is None:
                S = S2
        if S is None:
            raise GenError('%s: cannot infer the sort of %s or %s for built-in equality'
                           % (where, a, b))
        new = eq_text(gen, ctx.mod, a, b, S, toks[i][2] == '=/=', False, where)
        text = text[:toks[ll][0]] + new + text[toks[rh][1]:]


def guard_conds(gen, mod, text, where):
    """A sibling's equational conditions `t = u` (spaced, top level) as Bool text for a guard.
    Each becomes the sort-aware equality of arg_eq; the sort comes from t or u."""
    while True:
        toks = tokenize(text)
        idx = [i for i, t in enumerate(toks)
               if t[2] == '=' and t[0] > 0 and text[t[0] - 1].isspace()
               and t[1] < len(text) and text[t[1]].isspace()]
        if not idx:
            return text
        i = idx[0]
        ll, lh = left_bounds(toks, i)
        rl, rh = right_bounds(toks, i)
        a = text[toks[ll][0]:toks[lh][1]].strip()
        b = text[toks[rl][0]:toks[rh][1]].strip()
        S = infer_sort(gen, mod, a)
        if S is None or S == NUMLIT:
            S2 = infer_sort(gen, mod, b)
            if S2 is not None and S2 != NUMLIT:
                S = S2
            elif S is None:
                S = S2
        if S is None:
            raise GenError('%s: cannot infer the sort of %s or %s for the equational condition of an '
                           'owise guard' % (where, a, b))
        if S == NUMLIT:
            S = 'Nat'                       # both sides numerals: numEq, as for any Nat
        if S.startswith('List{'):
            raise GenError('%s: equational condition on a list sort (%s) in an owise guard is not '
                           'supported' % (where, S))
        new = arg_eq(gen, a, b, S)
        text = text[:toks[ll][0]] + new + text[toks[rh][1]:]


def split_items(src, sep):
    s = strip_parens(norm(src))
    if sep == '__':
        return top_ws_split(s)
    if sep == '_,_':
        return split_top(s, ',')
    raise GenError('unsupported collection operator %s' % sep)


def classify_item(it):
    it = strip_parens(norm(it))
    k = find_top(it, '|->')
    if k >= 0:
        return '_|->_', [it[:k].strip(), it[k + 3:].strip()]
    m = re.match(r'^([^\s()]+)\((.*)\)$', it)
    if m:
        return m.group(1), split_top(m.group(2), ',')
    if re.match(r'^[^\s()]+$', it):
        return it, []
    return None, [it]


def sep_render(sep, head, rest):
    if sep == '__':
        return '%s %s' % (head, rest)
    if sep == '_,_':
        return '(%s, %s)' % (strip_parens(head), rest)
    raise GenError('unsupported collection operator %s' % sep)


def elem_list(gen, mod, sort, sep, empty, excl):
    """Constructors of `sort` enumerated by owise enumerate/recurse (reflection based)."""
    res = []
    for c in gen.refl.value_ops(mod):
        if c['name'] in excl or c['rng'].startswith('[') or c['name'] == empty:
            continue
        if sep is not None and c['name'] == sep:
            continue
        if 'assoc' in c['attrs'] or 'comm' in c['attrs']:
            continue
        if not gen.leq(mod, c['rng'], sort):
            continue
        res.append(c)
    res.sort(key=lambda c: c['name'])
    return res


def is_ground(gen, mod, text):
    """No variable of the module occurs in text (constants and constructor terms only)."""
    return not any(t in gen.mod_vars.get(mod, {}) for t in re.findall(r"[A-Za-z][\w\-']*", text))


def neg_text(c):
    return neg_cond(c)


VARCASE = '#VAR'


def match_mixfix(gen, mod, cases, item):
    """Constructor (among cases) whose mixfix pattern matches the item; returns (name, arg texts)."""
    toks = top_ws_split(strip_parens(norm(item)))
    for c in cases:
        name = c['name']
        if '_' not in name or name == VARCASE:
            continue
        pieces = name.split('_')
        nargs = len(pieces) - 1
        template = []
        for k, pc in enumerate(pieces):
            if pc:
                template.append(('lit', pc))
            if k < nargs:
                template.append(('arg', None))
        if len(template) != len(toks):
            continue
        args, ok = [], True
        for (kind_, lit), tok in zip(template, toks):
            if kind_ == 'lit':
                if tok != lit:
                    ok = False
                    break
            else:
                args.append(tok)
        if ok and len(args) == nargs:
            return name, args
    return None


def collection_stmts(ctx, u, e, sibs, action, excl, keep):
    """Constructor cases of an owise equation (enumerate / recurse), with sibling coverage."""
    gen, mod = ctx.gen, ctx.mod
    where = '%s:%d' % (ctx.fname, u.line)
    kind, val = action
    fn, args = e['fn'], e['args']
    doms = gen.mod_ops.get(mod, {}).get(fn)
    if doms is None:
        raise GenError('%s: operator %s not declared in module %s' % (where, fn, mod))
    sep = empty = None
    nested = False
    if kind == 'recurse':
        sep, empty = val['sep'], val['empty']
        if gen.refl.ctor(mod, sep) is None:
            raise GenError('%s: sep %s is not a constructor visible in %s' % (where, sep, mod))
        seps = [c for c in gen.refl.ctors(mod) if c['name'] == sep]
        if not seps:
            raise GenError('%s: sep %s is not a constructor visible in %s' % (where, sep, mod))
        cands = [i for i in range(len(args)) if VAR_RE.match(args[i]) and
                 any(gen.leq(mod, c['rng'], doms[i]) for c in seps)]
        if len(cands) != 1:
            raise GenError('%s: cannot pick collection argument for recurse (candidates %s)'
                           % (where, cands))
        N = cands[0] + 1
        S = doms[N - 1]
    else:
        if isinstance(val, int):
            N = val
        else:
            whole = [i for i, a in enumerate(args) if a == val]
            if whole:
                N = whole[0] + 1
            else:
                inside = [i for i, a in enumerate(args) if mentions(a, val)]
                if len(inside) != 1:
                    raise GenError('%s: variable %s not found in a single owise argument' % (where, val))
                nested = True
                N = inside[0] + 1
        S = doms[N - 1] if not nested else gen.mod_vars.get(mod, {}).get(val)
        if nested and S is None:
            raise GenError('%s: no sort for nested variable %s' % (where, val))
    if not nested and not (1 <= N <= len(args)) or (not nested and not VAR_RE.match(args[N - 1])):
        raise GenError('%s: argument %d of the owise LHS must be a variable' % (where, N))
    rest = args[N - 1] if not nested else val
    elems = elem_list(gen, mod, S, sep, empty, excl)
    if nested:
        return _nested_stmts(ctx, where, fn, args, N - 1, val, S, elems, e, keep)
    shared = set()
    for i, a in enumerate(args):
        if i != N - 1:
            shared.update(re.findall(r"[A-Za-z][\w\-']*", a))
    cases = list(elems)
    if not cases and kind == 'recurse' and sep is not None:
        em = re.match(r'^(Set|NeSet|List|NeList)\{(.*)\}$', S)
        if em:
            if gen.refl.ctors(mod) and elem_list(gen, mod, em.group(2), None, None, excl):
                cases = elem_list(gen, mod, em.group(2), None, None, excl)
            else:
                cases = [{'name': VARCASE, 'doms': [em.group(2)], 'rng': em.group(2), 'attrs': []}]
            elems = cases
    var_covers = []
    empty_guards = []
    empty_covered = False
    cover = {}
    names = set(c['name'] for c in cases)
    for s_ in sibs:
        if len(s_['args']) != len(args):
            continue
        if sep is None and classify_item(s_['args'][N - 1])[0] in excl:
            continue                      # the sibling only covers excluded constructors
        disjoint = False
        for i, a in enumerate(s_['args']):
            if i != N - 1 and norm(a) != norm(args[i]):
                if is_ground(gen, mod, a) and is_ground(gen, mod, args[i]):
                    disjoint = True       # the sibling can never match this owise call
                    break
                h1 = classify_item(a)[0]
                h2 = classify_item(args[i])[0]
                if h1 and h2 and h1 != h2 and gen.refl.ctor(mod, h1) and gen.refl.ctor(mod, h2):
                    disjoint = True       # different constructors at the same position
                    break
                raise GenError('%s: sibling equation differs in a non-collection argument; '
                               'annotate or use replace' % where)
        if disjoint:
            continue
        whole = strip_parens(norm(s_['args'][N - 1]))
        if VAR_RE.match(whole) and not gen.refl.ctor(mod, whole) and whole not in names:
            var_covers.append((whole, s_['cond']))
            continue
        items = split_items(s_['args'][N - 1], sep) if sep else [s_['args'][N - 1]]
        if sep and len(items) > 1 and VAR_RE.match(strip_parens(norm(items[-1]))) and \
                not gen.refl.ctor(mod, strip_parens(norm(items[-1]))):
            items = items[:-1]                  # rest variable of the sibling pattern
        for it in items:
            if empty is not None and strip_parens(norm(it)) == empty:
                if s_['cond'] is None:
                    empty_covered = True
                else:
                    empty_guards.append(s_['cond'])
                continue
            name, sargs = classify_item(it)
            if name in names and name is not None:
                cover.setdefault(name, []).append((sargs, s_['cond']))
                continue
            if VAR_RE.match(strip_parens(norm(it))) and not gen.refl.ctor(mod, strip_parens(norm(it))):
                if VARCASE in names:
                    cover.setdefault(VARCASE, []).append(([strip_parens(norm(it))], s_['cond']))
                    continue
                raise GenError('%s: sibling element variable %s for constructor elements' % (where, it))
            hit = match_mixfix(gen, mod, cases, it)
            if hit is not None:
                cover.setdefault(hit[0], []).append((hit[1], s_['cond']))
                continue
            if name is not None and gen.refl.ctor(mod, name) is not None and not any(
                    c['name'] == name for c in elems):
                continue                # excluded constructor: not enumerated
            raise GenError('%s: sibling item %s is not an enumerated constructor' % (where, it))
    out = []
    used, skipped = [], []
    cnt = [0]
    for c in cases:
        vs = []
        for srt in c['doms']:
            cnt[0] += 1
            vs.append(('DMC%d' % cnt[0], srt))
        vtxt = ['%s:%s' % v for v in vs]
        if c['name'] == VARCASE:
            elem = vtxt[0]
        else:
            elem = render(c['name'], vtxt, nested=True, rng=c['rng'])
        guards = []
        full = False
        for vname, vcond in var_covers:
            if vcond is None:
                full = True
                break
            guards.append(neg_text(rename_var(vcond, vname, elem if c['name'] == VARCASE else
                                              render(c['name'], vtxt, nested=True, rng=c['rng']))))
        if not full:
            for sargs, cond in cover.get(c['name'], []):
                if len(sargs) != len(c['doms']):
                    raise GenError('%s: arity mismatch for %s' % (where, c['name']))
                pieces = []
                for idx, a in enumerate(sargs):
                    if VAR_RE.match(a):
                        if a in shared:
                            pieces.append(eq_text(gen, mod, vtxt[idx], a, c['doms'][idx], False, keep, where))
                        continue
                    unbound = [t for t in re.findall(r"[A-Za-z][\w\-']*", a)
                               if t in gen.mod_vars.get(mod, {}) and t not in shared]
                    if unbound:
                        raise GenError('%s: sibling argument %s has variables %s; annotate or use replace'
                                       % (where, a, unbound))
                    pieces.append(eq_text(gen, mod, vtxt[idx], a, c['doms'][idx], False, keep, where))
                if cond is not None:
                    ctext = cond
                    for idx, a in enumerate(sargs):
                        if VAR_RE.match(a):
                            ctext = rename_var(ctext, a, vtxt[idx])
                    if pieces:
                        guards.append('not (%s and %s)' % (' and '.join(pieces), ctext))
                    else:
                        guards.append(neg_text(ctext))
                elif pieces:
                    guards.append('not (%s)' % ' and '.join(pieces))
                else:
                    full = True
                    break
        if full:
            skipped.append(c['name'])
            continue
        used.append(c['name'])
        if kind == 'enumerate':
            largs = list(args)
            largs[N - 1] = elem if c['name'] == VARCASE else render(c['name'], vtxt, nested=False,
                                                                         rng=c['rng'])
            lhs = '%s(%s)' % (fn, ', '.join(largs))
            rhs = rename_var(e['rhs'], rest, largs[N - 1])
        else:
            lhs_arg = sep_render(sep, elem, rest)
            largs = list(args)
            largs[N - 1] = lhs_arg
            lhs = '%s(%s)' % (fn, ', '.join(largs))
            call = norm(e['lhs'])
            rhs = sep_render(sep, elem, call) if mentions(e['rhs'], rest) else call
        out.append((lhs, rhs, guards))
    if kind == 'recurse' and not empty_covered:
        eargs = list(args)
        eargs[N - 1] = empty
        lhs = '%s(%s)' % (fn, ', '.join(eargs))
        rhs = rename_var(e['rhs'], rest, empty)
        g = ['not (%s)' % c for c in empty_guards]
        out.append((lhs, rhs, g))
    stmts = []
    for lhs, rhs, guards in out:
        if guards:
            stmts.append('ceq %s = %s if %s .' % (lhs, rhs, ' /\\ '.join(guards)))
        else:
            stmts.append('eq %s = %s .' % (lhs, rhs))
    info = {'fn': fn, 'module': mod, 'N': N, 'sort': S, 'doms': doms, 'kind': kind,
            'sep': sep, 'empty': empty, 'excl': sorted(excl), 'elements': [c['name'] for c in elems],
            'used': used, 'skipped': skipped, 'empty_covered': empty_covered}
    # A list argument has one more shape: two or more elements, (E L).  No constructor case covers it, so
    # the owise right-hand side applies to it unless a sibling names that shape.
    if kind == 'enumerate' and S.startswith('List{') and S.endswith('}'):
        juxt_covered = any(' ' in strip_parens(norm(s_['args'][N - 1])) and
                           not re.match(r'^[\w\-]+\(', strip_parens(norm(s_['args'][N - 1])))
                           for s_ in sibs if len(s_['args']) == len(args))
        if juxt_covered:
            raise GenError('%s: a sibling names a multi-element list at argument %d; enumerate cannot cover it'
                           % (where, N))
        shape = '(DMJ1:%s DMJ2:%s)' % (S[5:-1], S)
        stmts.append('eq %s = %s .' % (rename_var(e['lhs'], rest, shape), rename_var(e['rhs'], rest, shape)))
    return stmts, info


def _nested_stmts(ctx, where, fn, args, i, var, S, elems, e, keep):
    """enumerate(VAR) where VAR sits inside argument i of the owise LHS (e.g. < S > SL)."""
    gen, mod = ctx.gen, ctx.mod
    cnt = [0]
    stmts, used = [], []
    for c in elems:
        vs = []
        for srt in c['doms']:
            cnt[0] += 1
            vs.append(('DMC%d' % cnt[0], srt))
        pat = render(c['name'], ['%s:%s' % v for v in vs], nested=True, rng=c['rng'])
        pat0 = render(c['name'], ['%s:%s' % v for v in vs], nested=False, rng=c['rng'])
        largs = list(args)
        largs[i] = rename_var(args[i], var, pat)
        lhs = '%s(%s)' % (fn, ', '.join(largs))
        rhs = rename_var(e['rhs'], var, pat0)
        stmts.append('eq %s = %s .' % (lhs, rhs))
        used.append(c['name'])
    info = {'fn': fn, 'module': mod, 'N': i + 1, 'sort': S, 'doms': gen.mod_ops.get(mod, {}).get(fn),
            'kind': 'enumerate', 'sep': None, 'empty': None, 'excl': [], 'elements': [c['name'] for c in elems],
            'used': used, 'skipped': [], 'empty_covered': False, 'nested': var}
    return stmts, info


def auto_owise(ctx, u, e, sibs):
    gen, mod = ctx.gen, ctx.mod
    where = '%s:%d' % (ctx.fname, u.line)
    lhs_n = norm(e['lhs'])
    same = [s for s in sibs if norm(s['lhs']) == lhs_n]
    if same:
        if any(s['cond'] is None for s in same):
            raise GenError('%s: owise equation is unreachable (unconditional sibling with same LHS)' % where)
        return 'cond', ' /\\ '.join(neg_text(guard_conds(gen, mod, rewrite_text(ctx, s['cond'], where), where))
                                   for s in same)
    doms = gen.mod_ops.get(mod, {}).get(e['fn'], [])
    guards = []
    args = e['args']
    for s in sibs:
        if s['cond'] is not None or len(s['args']) != len(args):
            continue
        for p in range(len(args)):
            for q in range(p + 1, len(args)):
                if not (VAR_RE.match(args[p]) and VAR_RE.match(args[q])) or args[p] == args[q]:
                    continue
                ok = all(norm(s['args'][i]) == norm(args[i]) for i in range(len(args)) if i != q)
                ok = ok and norm(s['args'][q]) == args[p]
                if ok:
                    srt = doms[p] if p < len(doms) else None
                    guards.append((args[p], args[q], srt))
    if guards:
        texts = []
        for a, b, srt in guards:
            if srt is None:
                raise GenError('%s: unknown sort for owise guard' % where)
            texts.append(eq_text(gen, mod, a, b, srt, True, False, where))
        return 'guard', ' /\\ '.join(texts)
    raise GenError('%s: cannot transform owise equation automatically; add ---@dmc owise '
                   'drop | cond(<C>) | enumerate(VAR) | recurse(sep=<op>, empty=<const>)' % where)


def owise_stmts(ctx, u, e, dirs):
    gen, mod = ctx.gen, ctx.mod
    where = '%s:%d' % (ctx.fname, u.line)
    if e['cond'] is not None:
        raise GenError('%s: owise equation with a condition is not supported' % where)
    sibs = [s for s in gen.mod_eqs.get(mod, []) if s['fn'] == e['fn'] and not s['owise'] and s['unit'] is not u]
    for s in sibs:
        if s['cond'] is not None:
            s['cond'] = rewrite_text(ctx, s['cond'], where)
    action, excl, keep = None, [], False
    for key, val in dirs:
        if key == 'owise':
            action = val
        elif key == 'exclude':
            excl += val
        elif key == 'keep-builtin':
            keep = True
    rhs = e['rhs'] if keep else rewrite_text(ctx, e['rhs'], where)
    e = dict(e, rhs=rhs)
    if action is not None and action[0] == 'drop':
        return 'drop', ['eq %s = %s .' % (e['lhs'], e['rhs'])], None
    if action is not None and action[0] == 'cond':
        c = action[1] if keep else rewrite_text(ctx, action[1], where)
        return 'cond', ['ceq %s = %s if %s .' % (e['lhs'], e['rhs'], c)], None
    if action is None or action[0] in ('enumerate-multi', 'recurse', 'shapes', 'enumerate'):
        if e['fn'] is None or e['args'] is None:
            raise GenError('%s: owise LHS must be a prefix operator application' % where)
    if action is not None and action[0] == 'enumerate':
        stmts, info = collection_stmts(ctx, u, e, sibs, action, excl, keep)
        return 'annot', stmts, info
    if action is not None and action[0] in ('enumerate-multi', 'recurse', 'shapes'):
        stmts, info = cell_owise(ctx, u, e, sibs, action, excl, keep)
        return 'annot', stmts, info
    kind, text = auto_owise(ctx, u, e, sibs)
    return kind, ['ceq %s = %s if %s .' % (e['lhs'], e['rhs'], text)], None


# ---------------------------------------------------------------- cell engine
#
# Owise equations with several enumerated arguments, collection arguments whose elements are
# constructor shapes, and list arguments whose shapes are nil / one element / (element, rest).
#
# A cell is a tuple of argument nodes (one per owise LHS argument):
#   ('v', name, sort, fresh)            variable leaf (fresh: generated name, rendered name:sort)
#   ('c', name, kids, sort)             constructor application; kids is a tuple of nodes
#   ('set', attrs, rest, sort)          attribute set; attrs = ((attr, attrsort, node), ...), rest node or None
#   ('cons', elem, rest, sep, sort)     collection cell  elem sep rest
# Sibling argument patterns:
#   ('var', name, sort)  ('lit', text)  ('c', name, kids, rng)  ('attrs', ((attr, dom, pat), ...), restvar)
# A leaf is refined (split into the constructors of its sort, or into the attributes a sibling names)
# only when a sibling equation has a non-variable pattern there.  Leaves that no sibling constrains stay
# variables, so cells merge automatically.  Refinement is allowed at the positions listed in the
# directive (enumerate) or at the collection position (recurse, shapes).

ATTRSET = 'AttributeSet'
NUM_RE = re.compile(r'^\d+$')
TOK3 = re.compile(r'\(|\)|,|[^\s(),]+')
IDENT_RE = re.compile(r"^[A-Za-z][\w\-']*$")


def toks3(s):
    return TOK3.findall(s)


def span_end(toks, i):
    """Index after the balanced group opened at toks[i] ('(' or '<'), or None."""
    op = toks[i]
    cl = ')' if op == '(' else '>'
    depth = 0
    for k in range(i, len(toks)):
        if toks[k] == op:
            depth += 1
        elif toks[k] == cl:
            depth -= 1
            if depth == 0:
                return k + 1
    return None


def unit_end(toks, i):
    if i >= len(toks):
        return None
    t = toks[i]
    if t in ('(', '<'):
        return span_end(toks, i)
    if IDENT_RE.match(t) and i + 1 < len(toks) and toks[i + 1] == '(':
        return span_end(toks, i + 1)
    return i + 1


def split_commas(toks):
    parts, cur, depth = [], [], 0
    for t in toks:
        if t == ',' and depth == 0:
            parts.append(cur)
            cur = []
            continue
        if t in ('(', '<'):
            depth += 1
        elif t in (')', '>'):
            depth -= 1
        cur.append(t)
    parts.append(cur)
    return parts


def mixfix_template(name, nargs):
    pieces = name.split('_')
    if len(pieces) != nargs + 1:
        return None
    tm = []
    for k, pc in enumerate(pieces):
        for t in toks3(pc):
            tm.append(('lit', t))
        if k < nargs:
            tm.append(('slot', None))
    return tm


def template_splits(tm, toks):
    """Token spans of the slots of template tm, for every way the tokens can be split."""
    n = len(toks)

    def rec(ti, pos):
        if ti == len(tm):
            if pos == n:
                yield []
            return
        kind, val = tm[ti]
        if kind == 'lit':
            if pos < n and toks[pos] == val:
                yield from rec(ti + 1, pos + 1)
            return
        nxt = tm[ti + 1] if ti + 1 < len(tm) else None
        if nxt is None:
            if pos < n:
                yield [(pos, n)]
            return
        if nxt[0] == 'slot':
            e = unit_end(toks, pos)
            if e is not None and pos < e <= n:
                for r in rec(ti + 1, e):
                    yield [(pos, e)] + r
            return
        depth = 0
        for e in range(pos, n):
            if e > pos and depth == 0 and toks[e] == nxt[1]:
                for r in rec(ti + 1, e):
                    yield [(pos, e)] + r
            if toks[e] in ('(', '<'):
                depth += 1
            elif toks[e] in (')', '>'):
                depth -= 1
    return list(rec(0, 0))


def gleq(gen, a, b):
    """Subsort test over the union of all reflected modules (a sort may be declared in another module)."""
    sup = gen.__dict__.get('gsup')
    if sup is None:
        sup = {}
        for M in gen.refl.mods:
            for x, y in gen.refl.mods[M].get('subs', []):
                sup.setdefault(x, set()).add(y)
        changed = True
        while changed:
            changed = False
            for x in list(sup):
                new = set()
                for y in sup[x]:
                    new |= sup.get(y, set())
                if not new <= sup[x]:
                    sup[x] |= new
                    changed = True
        gen.gsup = sup
    return a == b or b in sup.get(a, set())


def vops(gen, mod):
    cache = gen.__dict__.setdefault('vops_cache', {})
    if mod not in cache:
        cache[mod] = gen.refl.value_ops(mod)
    return cache[mod]


def attr_dom(gen, mod, attr):
    for c in vops(gen, mod):
        if c['name'] == attr + ':_' and len(c['doms']) == 1:
            return c['doms'][0]
    return None


def parse_attrs(gen, mod, toks):
    parts = split_commas(toks)
    attrs, rest = [], None
    for k, pt in enumerate(parts):
        if not pt:
            return None
        if len(pt) >= 3 and IDENT_RE.match(pt[0]) and pt[1] == ':':
            dom = attr_dom(gen, mod, pt[0])
            if dom is None:
                return None
            p = parse_pat(gen, mod, pt[2:], dom)
            if p is None:
                return None
            attrs.append((pt[0], dom, p))
        elif k == len(parts) - 1 and len(pt) == 1 and IDENT_RE.match(pt[0]):
            rest = ('var', pt[0], ATTRSET)
        else:
            return None
    return ('attrs', tuple(attrs), rest)


def parse_pat(gen, mod, toks, sort):
    """Pattern of the token list toks at sort (None when the form is not understood)."""
    if not toks:
        return None
    if len(toks) == 1:
        t = toks[0]
        if NUM_RE.match(t):
            return ('lit', t)
        for c in vops(gen, mod):
            if c['name'] == t and not c['doms'] and gleq(gen, c['rng'], sort):
                return ('c', t, (), c['rng'])
        if IDENT_RE.match(t):
            return ('var', t, gen.mod_vars.get(mod, {}).get(t))
        return None
    if toks[0] == '(' and span_end(toks, 0) == len(toks):
        return parse_pat(gen, mod, toks[1:-1], sort)
    if sort == ATTRSET:
        return parse_attrs(gen, mod, toks)
    for c in vops(gen, mod):
        if not c['doms'] or c['rng'].startswith('[') or not gleq(gen, c['rng'], sort):
            continue
        name, n = c['name'], len(c['doms'])
        if '_' in name:
            tm = mixfix_template(name, n)
            if tm is None:
                continue
            cands = [[toks[a:b] for a, b in sp] for sp in template_splits(tm, toks)]
        else:
            if toks[0] != name or len(toks) < 3 or toks[1] != '(' or span_end(toks, 1) != len(toks):
                continue
            cands = [split_commas(toks[2:-1])]
        for spans in cands:
            if len(spans) != n:
                continue
            kids = []
            for st, dom in zip(spans, c['doms']):
                sub = parse_pat(gen, mod, st, dom)
                if sub is None:
                    break
                kids.append(sub)
            if len(kids) == n:
                return ('c', name, tuple(kids), c['rng'])
    return None


def node_sort(N):
    if N[0] == 'v':
        return N[2]
    if N[0] == 'cons':
        return N[4]
    return N[3]


def render_node(N):
    k = N[0]
    if k == 'v':
        return '%s:%s' % (N[1], N[2]) if N[3] else N[1]
    if k == 'c':
        if not N[2]:
            return qualc(N[1], N[3])
        return render(N[1], [render_node(c) for c in N[2]], nested=True)
    if k == 'set':
        parts = ['%s : %s' % (a, render_node(c)) for a, _, c in N[1]]
        if N[2] is not None:
            parts.append(render_node(N[2]))
        return ', '.join(parts)
    if k == 'cons':
        return sep_render(N[3], render_node(N[1]), render_node(N[2]))
    raise GenError('bad node %r' % (N,))


def child_of(N, key):
    if N[0] == 'c':
        return N[2][key]
    if N[0] == 'cons':
        return N[1] if key == 'elem' else N[2]
    if N[0] == 'set':
        if key == 'rest':
            return N[2]
        for a, _, c in N[1]:
            if ('attr', a) == key:
                return c
    raise GenError('bad node path %r at %r' % (key, N[0]))


def set_child(N, key, new):
    if N[0] == 'c':
        kids = list(N[2])
        kids[key] = new
        return ('c', N[1], tuple(kids), N[3])
    if N[0] == 'cons':
        if key == 'elem':
            return ('cons', new, N[2], N[3], N[4])
        return ('cons', N[1], new, N[3], N[4])
    if N[0] == 'set':
        if key == 'rest':
            return ('set', N[1], new, N[3])
        return ('set', tuple((a, s_, new if ('attr', a) == key else c) for a, s_, c in N[1]), N[2], N[3])
    raise GenError('bad node path %r at %r' % (key, N[0]))


def set_path(N, path, new):
    if not path:
        return new
    return set_child(N, path[0], set_path(child_of(N, path[0]), path[1:], new))


def combine(sts):
    for st in sts:
        if st[0] == 'no':
            return st
    for st in sts:
        if st[0] == 'maybe':
            return st
    pieces, binds = [], []
    for st in sts:
        pieces.extend(st[1])
        binds.extend(st[2])
    return ('yes', pieces, binds)


def under(key, st):
    if st[0] == 'maybe':
        return ('maybe', (key,) + st[1], st[2], st[3])
    return st


class CellCtx(object):
    def __init__(self):
        self.count = 0
        self.sibs = []        # (patterns, cond)
        self.shared = set()
        self.sort_of = {}
        self.coll = None
        self.refine = set()
        self.keep = False
        self.where = ''

    def fresh(self, sort):
        self.count += 1
        return ('v', 'DMC%d' % self.count, sort, True)


def m_match(gen, mod, cx, P, N):
    """Status of a non-collection pattern against a node: no | maybe | yes(pieces, binds)."""
    k = P[0]
    if k == 'var':
        name = P[1]
        if name in cx.shared:
            if N[0] == 'v' and N[1] == name and not N[3]:
                return ('yes', [], [])
            return ('yes', [(N, name, cx.sort_of[name])], [])
        ns = node_sort(N)
        if P[2] is not None and not (gleq(gen, ns, P[2]) or gleq(gen, P[2], ns)):
            return ('no',)                  # sorts unrelated: the variable can never match this node
        return ('yes', [], [(name, N)])
    if k == 'lit':
        if N[0] == 'v' and N[2] in NUM_SORTS:
            return ('yes', [(N, P[1], N[2])], [])
        if N[0] == 'c' and N[1] == P[1]:
            return ('yes', [], [])
        return ('no',)
    if k == 'c':
        name, kids, rng = P[1], P[2], P[3]
        if N[0] == 'c':
            if N[1] != name or len(N[2]) != len(kids):
                return ('no',)
            return combine([under(i, m_match(gen, mod, cx, kids[i], N[2][i])) for i in range(len(kids))])
        if N[0] == 'v':
            if not gleq(gen, rng, N[2]):
                return ('no',)
            return ('maybe', (), P, 'refine')
        return ('no',)
    if k == 'attrs':
        items, rest = P[1], P[2]
        if N[0] == 'v':
            return ('maybe', (), P, 'refine')
        if N[0] != 'set':
            return ('no',)
        sts = []
        for attr, dom, p in items:
            child = None
            for a, _, c in N[1]:
                if a == attr:
                    child = c
            if child is None:
                if N[2] is None:
                    return ('no',)
                sts.append(('maybe', (), P, ('attr', attr)))
            else:
                sts.append(under(('attr', attr), m_match(gen, mod, cx, p, child)))
        if rest is not None:
            if N[2] is None:
                raise GenError('%s: attribute pattern with a rest variable against a closed attribute set'
                               % cx.where)
            sts.append(('yes', [], [(rest[1], N[2])]))
        return combine(sts)
    return ('no',)


def m_coll(gen, mod, cx, P, N):
    """Status of a collection pattern against a collection node (cons, empty or variable)."""
    if P[0] == 'var':
        if N[0] == 'v' and N[1] == P[1] and not N[3]:
            return ('yes', [], [])
        return ('yes', [], [(P[1], N)])
    if N[0] == 'cons':
        if P[0] == 'c' and P[1] == N[3]:
            if len(P[2]) != 2:
                raise GenError('%s: unsupported collection pattern' % cx.where)
            return combine([under('elem', m_match(gen, mod, cx, P[2][0], N[1])),
                            under('rest', m_coll(gen, mod, cx, P[2][1], N[2]))])
        return ('no',)
    if N[0] == 'c':
        return ('yes', [], []) if (P[0] == 'c' and P[1] == N[1] and not P[2]) else ('no',)
    if N[0] == 'v':
        if P[0] == 'c' and gleq(gen, P[3], N[2]):
            return ('maybe', (), P, 'refine')
        return ('no',)
    return ('no',)


def args_status(gen, mod, cx, pats, nodes):
    sts = []
    for i, P in enumerate(pats):
        if i == cx.coll:
            st = m_coll(gen, mod, cx, P, nodes[i])
        else:
            st = m_match(gen, mod, cx, P, nodes[i])
        if st[0] == 'maybe':
            st = ('maybe', (i,) + st[1], st[2], st[3])
        sts.append(st)
    return combine(sts)


def sort_elem_of(sort):
    """Element sort of a comma list sort (ExpList -> Exp, ValueList -> Value, VarList -> Var)."""
    return {'ExpList': 'Exp', 'ValueList': 'Value', 'VarList': 'Var'}[sort]


def refine_options(gen, mod, cx, N, P, reason):
    """Replacement nodes that split N so that the pattern P can be decided."""
    if isinstance(reason, tuple) and reason[0] == 'attr':
        if N[0] != 'set':
            raise GenError('%s: internal: attribute refinement of %s' % (cx.where, N[0]))
        dom = [d for a, d, _ in P[1] if a == reason[1]][0]
        child = cx.fresh(dom)
        return [('set', N[1] + ((reason[1], dom, child),), N[2], N[3])]
    if N[0] == 'v' and N[2] == ATTRSET:
        if P[0] != 'attrs':
            raise GenError('%s: cannot refine an attribute set for this pattern' % cx.where)
        attrs = tuple((a, d, cx.fresh(d)) for a, d, _ in P[1])
        return [('set', attrs, cx.fresh(ATTRSET), ATTRSET)]
    if N[0] == 'v':
        sort = N[2]
        opts = []
        for c in sorted(vops(gen, mod), key=lambda c: c['name']):
            if c['rng'].startswith('[') or 'assoc' in c['attrs'] or 'comm' in c['attrs']:
                continue
            if not gleq(gen, c['rng'], sort):
                continue
            kids = tuple(cx.fresh(d) for d in c['doms'])
            opts.append(('c', c['name'], kids, c['rng']))
        # a list variable also splits into its multi-element shape (E REST), which no constructor covers
        lm = re.match(r'^(List\{(.*)\})$', sort)
        if lm and sort.endswith('}'):
            opts.append(('cons', cx.fresh(lm.group(2)), cx.fresh(sort), '__', sort))
        elif sort in LIST_EXP:
            opts.append(('cons', cx.fresh(sort_elem_of(sort)), cx.fresh(sort), '_,_', sort))
        if not opts:
            raise GenError('%s: no constructors to split a variable of sort %s' % (cx.where, sort))
        return opts
    raise GenError('%s: cannot refine a %s node' % (cx.where, N[0]))


def expand(gen, mod, cx, nodes):
    """All leaf cells below nodes, each with the sibling statuses (no maybe left)."""
    first = None
    sts = []
    for pats, cond in cx.sibs:
        st = args_status(gen, mod, cx, pats, nodes)
        sts.append(st)
        if st[0] == 'maybe' and first is None:
            first = st
    if first is None:
        yield nodes, sts
        return
    root = first[1][0]
    path = first[1][1:]
    if root not in cx.refine:
        raise GenError('%s: a sibling equation needs argument %d split; annotate the owise equation '
                       'with enumerate(...) for that argument' % (cx.where, root + 1))
    N = child_of_path(nodes[root], path)
    for opt in refine_options(gen, mod, cx, N, first[2], first[3]):
        new_root = set_path(nodes[root], path, opt)
        yield from expand(gen, mod, cx, nodes[:root] + (new_root,) + nodes[root + 1:])


def child_of_path(N, path):
    for key in path:
        N = child_of(N, key)
    return N


def cell_guards(gen, mod, cx, nodes, sts):
    """Guards of one leaf cell (None when a sibling covers it completely)."""
    guards = []
    for (pats, cond), st in zip(cx.sibs, sts):
        if st[0] == 'no':
            continue
        pieces, binds = st[1], st[2]
        first_bind = {}
        extra = []
        for name, N in binds:
            if name in first_bind:
                extra.append((N, render_node(first_bind[name]), node_sort(N)))
            else:
                first_bind[name] = N
        ctext = guard_conds(gen, mod, cond, cx.where) if cond is not None else None
        if ctext is not None:
            for name, N in first_bind.items():
                ctext = rename_var(ctext, name, render_node(N))
        parts = []
        for N, other, srt in pieces + extra:
            parts.append(eq_text(gen, mod, render_node(N), other, srt, False, cx.keep, cx.where))
        if ctext is not None:
            parts.append(ctext)
        if not parts:
            return None
        if ctext is not None and not pieces and not extra:
            guards.append(neg_cond(ctext))
        else:
            guards.append('not (%s)' % ' and '.join(parts))
    return guards


def pat_node(cx, p, sort):
    """Node of an owise LHS argument: its variables stay unrefined, its constructors are fixed."""
    if p[0] == 'var':
        return ('v', p[1], sort if sort is not None else p[2], False)
    if p[0] == 'c':
        doms = [c['doms'] for c in vops(cx.gen, cx.mod) if c['name'] == p[1] and len(c['doms']) == len(p[2])
                and gen_leq_ok(cx.gen, cx.mod, c['rng'], p[3])]
        ds = doms[0] if doms else [None] * len(p[2])
        return ('c', p[1], tuple(pat_node(cx, k, d) for k, d in zip(p[2], ds)), p[3])
    raise GenError('%s: unsupported owise LHS argument' % cx.where)


def gen_leq_ok(gen, mod, a, b):
    return a == b or gleq(gen, a, b)


def cell_owise(ctx, u, e, sibs, action, excl, keep):
    """Equations for an owise LHS with enumerated / collection arguments (see the cell engine)."""
    gen, mod = ctx.gen, ctx.mod
    where = '%s:%d' % (ctx.fname, u.line)
    kind, val = action
    fn, args = e['fn'], e['args']
    doms = gen.mod_ops.get(mod, {}).get(fn)
    if doms is None or len(doms) != len(args):
        raise GenError('%s: owise operator %s has no declaration matching its LHS' % (where, fn))
    n = len(args)
    mvars = gen.mod_vars.get(mod, {})
    cx = CellCtx()
    cx.where = where
    cx.keep = keep
    cx.gen, cx.mod = gen, mod
    juxt = False
    elemvar = restvar = None
    if kind == 'enumerate-multi':
        mode = 'enum'
        coll = None
        cx.refine = set(val)
    else:
        mode = 'recurse' if kind == 'recurse' else 'shapes'
        coll = n - 1
        cx.coll = coll
        cx.refine = {coll}
        cur = toks3(norm(args[coll]))
        if len(cur) == 1 and IDENT_RE.match(cur[0]):
            restvar = cur[0]
        elif mode == 'recurse' and len(cur) == 2 and all(IDENT_RE.match(t) for t in cur):
            elemvar, restvar = cur
            juxt = True
        else:
            raise GenError('%s: unsupported collection argument %s' % (where, args[coll]))
    kv = val if isinstance(val, dict) else {}
    sep, empty = kv.get('sep'), kv.get('empty')
    collsort = doms[coll] if coll is not None else None
    owise_nodes = []
    for i in range(n):
        if i == coll:
            owise_nodes.append(None)
            continue
        p = parse_pat(gen, mod, toks3(norm(args[i])), doms[i])
        if p is None:
            raise GenError('%s: cannot read owise argument %s' % (where, args[i]))
        owise_nodes.append(pat_node(cx, p, doms[i]))
    for i in range(n):
        if i != coll and IDENT_RE.match(args[i]):
            cx.sort_of[args[i]] = doms[i]
    if coll is not None:
        cx.sort_of[restvar] = collsort
    if juxt:
        elemsort = mvars.get(elemvar)
        if elemsort is None:
            raise GenError('%s: no sort for element variable %s' % (where, elemvar))
        cx.sort_of[elemvar] = elemsort
    elif mode == 'shapes':
        elemsort = kv.get('elem')
        if elemsort is None:
            raise GenError('%s: shapes(...) needs elem=<sort>' % where)
    else:
        elemsort = collsort
    # variables of the arguments that stay whole: a sibling's variable at a refined position is bound
    # to the cell's node instead of being compared with the owise variable of the same name
    cx.shared = set(x for i in range(n) if i != coll and i not in cx.refine
                    for x in re.findall(r"[A-Za-z][\w\-']*", args[i]))
    cx.shared |= set(x for x in (elemvar, restvar) if x)
    for s in sibs:
        if len(s['args']) != n:
            continue
        pats = []
        for i in range(n):
            p = parse_pat(gen, mod, toks3(norm(s['args'][i])), doms[i])
            if p is None:
                raise GenError('%s: cannot read sibling argument %s' % (where, s['args'][i]))
            pats.append(p)
        cx.sibs.append((pats, s['cond']))
    base = owise_nodes
    options = []
    if mode == 'enum':
        options = [None]
    elif mode == 'recurse':
        if not juxt:
            options.append(('c', empty, (), collsort))
        cands = elem_list(gen, mod, elemsort, sep, empty, excl)
        for c in cands:
            kids = tuple(cx.fresh(d) for d in c['doms'])
            elem = ('c', c['name'], kids, c['rng'])
            options.append(('cons', elem, ('v', restvar, collsort, False), sep, collsort))
        if not cands:
            # element sort without constructors (FileName in Set{FileName}): one variable element
            inner = elemsort
            em = None if juxt else re.match(r'^(Set|NeSet|List|NeList)\{(.*)\}$', collsort)
            if em:
                inner = em.group(2)
            options.append(('cons', cx.fresh(inner), ('v', restvar, collsort, False), sep, collsort))
    else:
        options.append(('c', empty, (), collsort))
        single = cx.fresh(elemsort)
        options.append(single)
        options.append(('cons', cx.fresh(elemsort), ('v', restvar, collsort, False), sep, collsort))
    stmts, leaves = [], 0
    for opt in options:
        nodes = list(base)
        if coll is not None:
            nodes[coll] = ('v', restvar, collsort, False) if opt is None else opt
        for leaf, sts in expand(gen, mod, cx, tuple(nodes)):
            guards = cell_guards(gen, mod, cx, leaf, sts)
            if guards is None:
                continue
            leaves += 1
            lhs = '%s(%s)' % (fn, ', '.join(render_node(x) for x in leaf))
            renames = {}
            for i in range(n):
                if i != coll and IDENT_RE.match(args[i]) and not (leaf[i][0] == 'v' and leaf[i][1] == args[i]):
                    renames[args[i]] = render_node(leaf[i])
            cn = leaf[coll] if coll is not None else None
            if coll is not None:
                if cn[0] == 'cons':
                    if juxt:
                        renames[elemvar] = render_node(cn[1])
                    renames[restvar] = render_node(cn[2])
                elif cn[0] == 'c':
                    renames[restvar] = render_node(cn)
                else:
                    renames[restvar] = render_node(cn)
            rhs = e['rhs']
            if mode == 'recurse' and cn is not None and cn[0] == 'cons' and not (
                    elemvar and mentions(rhs, elemvar)):
                # the cell value for (elem REST): the owise value on REST, with elem put back when
                # the owise right-hand side rebuilds the collection (writeAt), or not at all when the
                # right-hand side is the recursive call itself (persistentHandlesCompatible)
                call = [render_node(x) for x in leaf]
                call[coll] = render_node(cn[2])   # the rest as this cell has it (may be refined)
                calltext = '%s(%s)' % (fn, ', '.join(call))
                if norm(rhs) == norm(calltext) or not mentions(rhs, restvar):
                    rhs = calltext
                else:
                    rhs = sep_render(sep, render_node(cn[1]), calltext)
            else:
                for name, text in renames.items():
                    rhs = rename_var(rhs, name, text)
            if guards:
                stmts.append('ceq %s = %s if %s .' % (lhs, rhs, ' /\\ '.join(guards)))
            else:
                stmts.append('eq %s = %s .' % (lhs, rhs))
    elems = [c['name'] for c in elem_list(gen, mod, elemsort, sep, empty, excl)] if mode == 'recurse' else []
    info = {'fn': fn, 'module': mod, 'N': coll + 1 if coll is not None else None, 'sort': collsort,
            'doms': doms, 'kind': 'recurse' if mode == 'recurse' else 'enumerate', 'sep': sep,
            'empty': empty, 'excl': sorted(excl), 'elements': elems, 'used': [], 'skipped': [],
            'empty_covered': False, 'cells': leaves,
            'nodrift': juxt or mode != 'recurse'}
    return stmts, info



def eqname_of(sort):
    return 'eq' + re.sub(r'\W', '', sort)


def eq_block(gen, S, kind, M, ctors=None):
    """Lines of the equality operator for sort S declared in module M (inserted after M's header).
    A constructor written by juxtaposition (`__`) is ambiguous inside `.=.` (the juxtaposition operators
    of the spec overlap), so its equations are those of an auxiliary function on S, with `.=.` defined
    from it; the argument sort of the function then selects the constructor's parse."""
    all_ctors = dmc_ctors(gen, S)
    if ctors is None:
        ctors = all_ctors
    op = '_.=._'
    juxt = any('__' in c['name'] for c in all_ctors) and kind != 'pairwise'
    if juxt:
        op = 'dm' + re.sub(r'\W', '', S) + 'Eq'
    out = ['  op _.=._ : %s %s -> Bool .' % (S, S)]
    if juxt:
        out.append('  op %s : %s %s -> Bool .' % (op, S, S))
        z1, z2 = 'DMZ%s1' % re.sub(r'\W', '', S), 'DMZ%s2' % re.sub(r'\W', '', S)
        out.append('  vars %s %s : %s .' % (z1, z2, S))
        out.append('  eq %s .=. %s = %s(%s, %s) [variant] .' % (z1, z2, op, z1, z2))

    def eq_lhs(a, b):
        return '%s(%s, %s)' % (op, a, b) if juxt else '%s .=. %s' % (a, b)
    if kind == 'pairwise':
        out.append('  eq DMX:%s .=. DMX:%s = True [variant] .' % (S, S))
        consts = [c for c in ctors if not c['doms']]
        for a in consts:
            for b in consts:
                if a is not b:
                    out.append('  eq %s = False [variant] .' % eq_lhs(a['name'], b['name']))
        return out
    for c in ctors:
        n = len(c['doms'])
        va = ['DMA%d:%s' % (k + 1, s) for k, s in enumerate(c['doms'])]
        vb = ['DMB%d:%s' % (k + 1, s) for k, s in enumerate(c['doms'])]
        lhs = render(c['name'], va, nested=True, rng=c['rng'])
        rhs_ = render(c['name'], vb, nested=True, rng=c['rng'])
        if n == 0:
            out.append('  eq %s = True [variant] .' % eq_lhs(lhs, rhs_))
        else:
            parts = [arg_eq(gen, x, y, srt) for x, y, srt in zip(va, vb, c['doms'])]
            out.append('  eq %s = %s [variant] .' % (eq_lhs(lhs, rhs_), ' and '.join(parts)))
        for d in ctors:
            if d is c:
                continue
            vd = ['DMC%d:%s' % (k + 1, s) for k, s in enumerate(d['doms'])]
            out.append('  eq %s = False [variant] .' % eq_lhs(lhs, render(d['name'], vd, nested=True)))
    return out


def wrapper_lines(S, kind):
    f = eqname_of(S)
    if kind == 'builtin':
        body = 'DMA:%s == DMB:%s' % (S, S)
    else:
        body = 'DMA:%s .=. DMB:%s' % (S, S)
    return ['  op %s : %s %s -> Bool .' % (f, S, S),
            '  eq %s(DMA:%s, DMB:%s) = %s .' % (f, S, S, body)]


def ctor_doms(gen, X):
    """Argument sorts of the constructors (not AC) that can build a term of sort X, over all modules
    (a constructor of a subsort of X builds an X term, e.g. struct_ : ImpType -> SType builds an ImpType)."""
    edges = getattr(gen, 'dep_edges', None)
    if edges is None:
        edges = {}
        sup_cache = {}
        for M in gen.refl.mods:
            sup_cache[M] = gen.refl.supers(M)
            for c in gen.refl.value_ops(M):
                if not c['doms'] or c['rng'].startswith('[') or 'assoc' in c['attrs'] or 'comm' in c['attrs']:
                    continue
                for Y in {c['rng']} | sup_cache[M].get(c['rng'], set()):
                    edges.setdefault(Y, set()).update(c['doms'])
        gen.dep_edges = edges
    return sorted(edges.get(X, set()))


def reach_sorts(gen, X):
    seen, todo = set(), [X]
    while todo:
        y = todo.pop()
        for d in ctor_doms(gen, y):
            if d in gen.sort_mod and d not in seen:
                seen.add(d)
                todo.append(d)
    return seen


def eq_rep(gen, S, cyc):
    """Sort whose `.=.` serves S: the top sort of the constructor cycle through S, or the cycle sort
    above S (a subsort of a cyclic sort such as SType < ImpType), or S itself."""
    if S in cyc:
        rs = reach_sorts(gen, S)
        members = [X for X in cyc if X == S or (X in rs and S in reach_sorts(gen, X))]
        for X in sorted(members):
            M = gen.sort_mod[X]
            if all(gen.leq(M, Y, X) for Y in members):
                return X
        return S
    M = gen.sort_mod.get(S)
    for R in sorted(cyc):
        if R != S and M is not None and R in visible_sorts(gen, M) and gen.leq(M, S, R):
            return R
    return S


def cycle_sorts(gen, S, cyc):
    """Members of the constructor cycle through S (empty unless S is on a cycle)."""
    if S not in cyc:
        return []
    rs = reach_sorts(gen, S)
    return [X for X in cyc if X == S or (X in rs and S in reach_sorts(gen, X))]


def decl_modules(gen, c):
    """Modules declaring the operator c and its result sort (a constructor declared next to its sort)."""
    out = []
    for m, e in gen.refl.mods.items():
        if e.get('own', {}).get(c['name']) == c['doms']:
            out.append(m)
    return out


def visible_sorts(gen, M):
    cache = gen.__dict__.setdefault('vsort_cache', {})
    if M not in cache:
        e = gen.refl.mods.get(M, {})
        out = set()
        for c in e.get('ctors', []) + e.get('ops', []):
            out.add(c['rng'])
            out.update(c['doms'])
        for a, b in e.get('subs', []):
            out.add(a)
            out.add(b)
        cache[M] = out
    return cache[M]


def global_ctors(gen, S):
    """Constructors of S (or a subsort) declared with their result sort, over all reflected modules."""
    cache = gen.__dict__.setdefault('gctor_cache', {})
    if S in cache:
        return cache[S]
    out, seen = [], set()
    for M in gen.refl.mods:
        for c in gen.refl.value_ops(M):
            if c['rng'].startswith('[') or 'assoc' in c['attrs'] or 'comm' in c['attrs']:
                continue
            if not gen.leq(M, c['rng'], S) or not decl_modules(gen, c):
                continue
            key = (c['name'], tuple(c['doms']), c['rng'])
            if key not in seen:
                seen.add(key)
                out.append(c)
    out.sort(key=lambda c: (c['name'], c['doms'], c['rng']))
    cache[S] = out
    return out


def sort_ctors(gen, S, M):
    """Constructors used for the equality of S (DMC build, over all reflected modules)."""
    return dmc_ctors(gen, S)


PRE_MAIN = '<pre-main>'     # anchor of the equation modules: written to DM_EQ_FILE (see main_with_plan)
DM_EQ_FILE = 'dm-eq.maude'  # generated equation modules; main.maude loads them after its last load line
DM_EQ_LOAD = 'dm-eq'        # the load name of DM_EQ_FILE
DM_EQ_HEADER = '*** Generated by tools/gen-dmc.py: equation modules of the DMC build, loaded by main.maude.'
HEAD_RE = re.compile(r'^(fmod|mod|omod)\s+(\S+?)[\s(\[]')   # first line of a module of main.maude
LOAD_RE = re.compile(r'^load\s+\S')


def anchor_module(gen, S, cyc=None):
    """Equation modules go to DM_EQ_FILE, loaded by main.maude before its modules (see main_with_plan): all the
    constructors are loaded by then, and main's modules import the equation modules."""
    return PRE_MAIN


def src_order(gen, names):
    """The source modules among names (not main's, not generated), in load order: a declaration
    precedes its users, so the imports of an equation module are loaded before it."""
    keep = set(m for m in names if m in gen.mod_order and m not in gen.main_mods)
    return sorted(keep, key=lambda m: gen.mod_order[m])


def global_subs(gen):
    """Subsort declarations over all source modules: first[(a, b)] is the first module (load order) that
    sees a < b, which is the module declaring it; sup is the transitive closure of the declarations."""
    cache = gen.__dict__.get('gsubs_cache')
    if cache is None:
        first = {}
        for m in sorted(gen.mod_order, key=lambda m: gen.mod_order[m]):
            for a, b in gen.refl.mods.get(m, {}).get('subs', []):
                first.setdefault((a, b), m)
        sup = {}
        for a, b in first:
            sup.setdefault(a, set()).add(b)
        changed = True
        while changed:
            changed = False
            for a in list(sup):
                new = set()
                for b in sup[a]:
                    new |= sup.get(b, set())
                if not new <= sup[a]:
                    sup[a] |= new
                    changed = True
        cache = gen.gsubs_cache = (first, sup)
    return cache


def subsort_mods(gen, R, S):
    """Modules declaring the subsort chain from R up to S (R <= S), with the sorts of the chain."""
    if R == S:
        return set()
    first, sup = global_subs(gen)

    def le(x, y):
        return x == y or y in sup.get(x, ())
    out = set()
    for (a, b), m in first.items():
        if le(R, a) and le(b, S):
            out.add(m)
            out.update(x for x in (gen.sort_mod.get(a), gen.sort_mod.get(b)) if x)
    return out


def visible_decl(gen, srt):
    """The first source module (load order) that sees an operator of result sort srt: the module that
    imports the library instantiation declaring the sort (e.g. Map{Var,Nat} is imported by imp)."""
    for m in sorted(gen.mod_order, key=lambda m: gen.mod_order[m]):
        if m in gen.main_mods:
            continue
        e = gen.refl.mods.get(m, {})
        if any(c['rng'] == srt for c in e.get('ops', []) + e.get('ctors', [])):
            return m
    return None


def dm_module(gen, name, imports, lines):
    """Records the imports of a generated module (for the import-safety check) and returns its lines."""
    gen.dm_imports[name] = list(imports)
    out = ['mod %s is' % name]
    out.extend('  pr %s .' % m for m in imports)
    out.extend(lines)
    out.extend(['endm', ''])
    return out


def req_ctor(gen, S, c):
    """Source modules that the equations of constructor c of S need to see: its declaring modules, the
    subsorts from its range up to S, the sorts of its arguments with their equality (eq_rep), and the
    auxiliary modules of Map{Var,Nat} and ExpList arguments."""
    cyc = getattr(gen, 'cyc', set())
    out = set(ctor_decl_mods(gen, c))
    out.update(subsort_mods(gen, c['rng'], S))
    for d in c['doms']:
        if d in gen.sort_mod:
            out.add(gen.sort_mod[d])
            r = eq_rep(gen, d, cyc)
            if r != d and r in gen.sort_mod:
                out.add(gen.sort_mod[r])
                out.update(subsort_mods(gen, d, r))
        if d.startswith('List{') and d.endswith('}') and d[5:-1] in gen.sort_mod:
            out.add(gen.sort_mod[d[5:-1]])
        if d == MAP_VN:
            out.update(aux_req(gen, 'map'))
        if d in LIST_EXP:
            out.update(aux_req(gen, 'exp'))
    return set(m for m in out if m in gen.mod_order and m not in gen.main_mods)


def aux_req(gen, kind):
    """Source modules that the auxiliary equality of kind 'map' (Map{Var,Nat}) or 'exp' (ExpList) needs."""
    if kind == 'map':
        names = [visible_decl(gen, MAP_VN), gen.sort_mod.get('Var')]
    else:
        names = [gen.sort_mod.get(x) for x in EXP_ELEM_SORTS] + [gen.sort_mod.get('Exp'), gen.sort_mod.get('ExpList'),
                                                                 EXP_CTOR_MOD]
    return set(m for m in names if m and m in gen.mod_order and m not in gen.main_mods)


def sig_name(S):
    return 'DM-EQ-' + re.sub(r'\W', '', S).upper() + '-SIG'


def sig_module(gen, S, after):
    """DM-EQ-S-SIG: the declaration of `.=.` on S and the reflexive equation, shared by every equation module
    that compares S-terms.  It imports only the module declaring S (CONFIGURATION for a prelude sort) and
    DM-BOOL, so its closure is small; the equations of S stay in its variants."""
    cache = gen.__dict__.setdefault('sig_cache', {})
    if S not in cache:
        home = 'CONFIGURATION' if S in gen.prelude_need else gen.sort_mod[S]
        imports = ['DM-BOOL', home]
        body = ['  op _.=._ : %s %s -> Bool .' % (S, S), '  var DMX : %s .' % S,
                '  eq DMX .=. DMX = True [variant] .']
        name = sig_name(S)
        after.setdefault(anchor_module(gen, S), []).extend(dm_module(gen, name, imports, body))
        cache[S] = name
    return cache[S]


def eq_variant_body(gen, S, k, ctors):
    """Equations of S over the constructors ctors: the `.=.` declaration and the reflexive equation are in
    DM-EQ-S-SIG, so they are left out here."""
    lines = eq_block(gen, S, k, None, ctors=ctors)
    return [ln for ln in lines if not ln.startswith('  op _.=._ :') and not ln.startswith('  eq DMX:')]


def sig_deps(gen, S, ctors, after):
    """SIG modules of the argument sorts of ctors that have an equality of their own (late or prelude sorts)."""
    out = []
    for c in ctors:
        for d in c['doms']:
            if d in gen.late_sorts or d in gen.prelude_need:
                out.append(sig_module(gen, d, after))
    return list(dict.fromkeys(out))


def prelude_eq_module(gen, d, js, after):
    """Equations of the prelude sort d (Oid of CONFIGURATION) over the constructors js (indices into
    dmc_ctors(d)), in a module of their own, shared by the variants that use the same constructors."""
    cache = gen.__dict__.setdefault('prelude_cache', {})
    key = (d, js)
    if key not in cache:
        base = 'DM-EQ-' + re.sub(r'\W', '', d).upper()
        n = sum(1 for k2 in cache if k2[0] == d)
        name = base if n == 0 else '%s-%d' % (base, n + 1)
        sub = [dmc_ctors(gen, d)[j] for j in js]
        deps = set()
        for c in sub:
            deps |= req_ctor(gen, d, c)
        body = eq_variant_body(gen, d, gen.prelude_need[d], sub)
        text = '\n'.join(body)
        imports = [sig_module(gen, d, after)] + src_order(gen, deps) + sig_deps(gen, d, sub, after)
        if 'mapEq(' in text:
            imports.append(AUX_MAP)
        if 'expListEq(' in text:
            imports.append(AUX_EXP)
        imports = ['DM-BOOL', 'DM-INT'] + list(dict.fromkeys(imports))
        after.setdefault(anchor_module(gen, d), []).extend(dm_module(gen, name, imports, body))
        cache[key] = name
    return cache[key]


def late_variants(gen, S, k, after):
    """Equation modules of the late sort S.  A module (main or test, see gen.vis) imports the variant that has
    exactly the constructors whose required modules are in its own import closure: a constructor it cannot
    see has no terms in it, so the variant adds nothing to the module.  Modules with the same constructors
    share a variant.  Each variant is a module DM-EQ-S (-n) in dm-eq.maude, loaded before the modules of main.maude."""
    own = gen.sort_mod[S]
    ctors = dmc_ctors(gen, S)
    reqs = [req_ctor(gen, S, c) for c in ctors]
    pre = sorted(set(d for c in ctors for d in c['doms'] if d in gen.prelude_need))
    groups = {}
    for M in sorted(gen.vis):
        V = gen.vis[M]
        if own not in V:
            continue
        allowed = tuple(i for i in range(len(ctors)) if reqs[i] <= V)
        pre_allowed = tuple((d, tuple(j for j, c in enumerate(dmc_ctors(gen, d))
                                      if req_ctor(gen, d, c) <= V)) for d in pre)
        groups.setdefault((allowed, pre_allowed), []).append(M)
    base = 'DM-EQ-' + re.sub(r'\W', '', S).upper()
    for n, key in enumerate(sorted(groups)):
        allowed, pre_allowed = key
        sub = [ctors[i] for i in allowed]
        name = base if n == 0 else '%s-%d' % (base, n + 1)
        body = eq_variant_body(gen, S, k, sub)
        deps = set([own])
        for i in allowed:
            deps |= reqs[i]
        text = '\n'.join(body)
        imports = [sig_module(gen, S, after)] + src_order(gen, deps) + sig_deps(gen, S, sub, after)
        imports += [prelude_eq_module(gen, d, js, after) for d, js in pre_allowed if js]
        if 'mapEq(' in text:
            imports.append(AUX_MAP)
        if 'expListEq(' in text:
            imports.append(AUX_EXP)
        imports = ['DM-BOOL', 'DM-INT'] + list(dict.fromkeys(imports))
        after.setdefault(anchor_module(gen, S), []).extend(dm_module(gen, name, imports, body))
        for M in groups[key]:
            gen.dm_for.setdefault(M, []).append(name)
            if pre_allowed and any(js for _, js in pre_allowed):
                gen.dm_for[M].append(sig_name(S))


BUNDLE_PREFIX = 'DM-EQ-FOR-'


def bundle_groups(gen, mods, tag='', dm=None):
    """Import bundles of the modules mods (in order).  A module whose dm (default dm_for) has two or more equation
    modules imports one bundle module DM-EQ-FOR-<first of mods that uses it><tag>, which imports exactly those
    equation modules with `pr`; modules with the same set share it.  A module with one equation module keeps
    that single import.  Returns (groups, use): groups maps each bundle name to its imports, use maps each
    module to the names its header imports.  tag distinguishes the bundles of a test file or of the
    ground-check input (their module names are not unique across those files)."""
    dm = gen.dm_for if dm is None else dm
    groups, use, by_key = {}, {}, {}
    for M in mods:
        names = dm.get(M, [])
        if len(names) < 2:
            use[M] = list(names)
            continue
        key = tuple(sorted(names))
        if key not in by_key:
            name = BUNDLE_PREFIX + M + tag
            if name in gen.vis or name in gen.mod_order or name in gen.main_mods:
                raise GenError('bundle module %s clashes with a module of the spec' % name)
            by_key[key] = name
            groups[name] = list(names)
        use[M] = [by_key[key]]
    return groups, use


def build_plan(gen):
    """Equality kinds, header insertions and view upgrades from the collected needs."""
    coll = gen.coll
    forced = set()
    need = set(coll.needs)
    view_sorts = {}
    for vname in coll.view_needs:
        v = gen.views.get(vname)
        if v is None or v['elt'] is None:
            raise GenError('view %s is used as a TRIV-EQ parameter but has no sort Elt' % vname)
        if v['from'] != 'TRIV':
            raise GenError('view %s is used as a TRIV-EQ parameter but is from %s' % (vname, v['from']))
        view_sorts.setdefault(v['elt'], []).append(vname)
        if v['elt'] not in gen.sort_mod and v['to'] in gen.mod_sorts:
            # a prelude sort (Oid of CONFIGURATION) given constructors by a spec module: its equality is
            # declared in the view's target, like a sort of the spec
            gen.sort_mod[v['elt']] = v['to']
        if v['keep']:
            forced.add(v['elt'])
        need.add(v['elt'])
    cyc = set(X for X in gen.sort_mod if X in reach_sorts(gen, X))
    gen.cyc = cyc
    memo = {}

    def skind(S, stack):
        if S in memo:
            return memo[S]
        if S.startswith('List{') and S.endswith('}') and S not in forced:
            inner = S[5:-1]
            ok = inner in gen.sort_mod and skind(inner, stack | {S}) != 'builtin'
            memo[S] = 'structural' if ok else 'builtin'
            if ok:
                gen.list_eq.add(inner)
            return memo[S]
        if S not in forced and S not in stack and ac_built_sort(gen, S):
            memo[S] = 'builtin'          # an AC constructor into S (a soup or multiset): no ctor-based equality
            return memo[S]
        if S in forced:
            return 'builtin'
        if S not in gen.sort_mod and not dmc_ctors(gen, S):
            return 'builtin'                 # a prelude sort (e.g. Oid of CONFIGURATION) without constructors
        if S in stack:
            return 'structural'         # constructor cycle: judged by the cycle's own members
        R = eq_rep(gen, S, cyc)
        if R != S:
            memo[S] = skind(R, stack)
            return memo[S]
        M = gen.sort_mod.get(S)
        ctors = dmc_ctors(gen, S)
        if not ctors:
            memo[S] = 'builtin'
        elif all(not c['doms'] for c in ctors):
            memo[S] = 'pairwise'
        else:
            ok = True
            for c in ctors:
                for d in c['doms']:
                    if d in NUM_SORTS or d == S or d == 'Bool' or d.startswith('Set{') or d == MAP_VN or d in LIST_EXP:
                        continue
                    if skind(d, stack | {S}) == 'builtin':
                        ok = False
            memo[S] = 'structural' if ok else 'builtin'
        return memo[S]
    kinds = {}
    for S in sorted(need):
        kinds[S] = skind(S, set())
    for S in list(memo):
        kinds[S] = memo[S]
    header, numeq, boolM = {}, set(coll.numeq), set()
    after = {}
    # prelude sorts with an equality (Oid of CONFIGURATION): their equations are emitted with the equation
    # modules of the sorts that use them (prelude_eq_module), so they follow the same import rules
    gen.prelude_need = dict((S, k) for S, k in kinds.items()
                            if S not in gen.sort_mod and k != 'builtin' and S not in view_sorts
                            and not S.startswith('List{'))
    gen.late_sorts = set(S for S, k in kinds.items()
                         if S in gen.sort_mod and k != 'builtin' and not S.startswith('List{')
                         and eq_rep(gen, S, cyc) == S and (cycle_sorts(gen, S, cyc) or is_late(gen, S)))
    for S, k in sorted(kinds.items()):
        if S.startswith('List{'):
            continue                          # equality is listEqX (see list_plan)
        if k == 'builtin' and S not in view_sorts:
            continue
        if S not in gen.sort_mod:
            continue
        M = gen.sort_mod[S]
        lines = header.setdefault(M, [])
        if k != 'builtin' and eq_rep(gen, S, cyc) == S:
            boolM.add(M)
            block = eq_block(gen, S, k, M)
            if cycle_sorts(gen, S, cyc) or is_late(gen, S):
                # equations need every constructor visible: equation modules before main's modules
                lines.append(block[0])
                late_variants(gen, S, k, after)
            else:
                lines.extend(block)
                if any(d in NUM_SORTS for c in dmc_ctors(gen, S) for d in c['doms']):
                    numeq.add(M)
        if S in view_sorts:
            lines.extend(wrapper_lines(S, k))
    for M in list(header):
        if M in boolM:
            header[M].insert(0, '  pr DM-BOOL .')
    for M in numeq:
        header.setdefault(M, []).insert(0, '  pr DM-INT .')
    for M, Ss in sorted(coll.uses.items()):
        for S in sorted(Ss):
            if S.startswith('List{') and S[5:-1] in gen.list_eq:
                line = '  pr %s .' % list_eq_mod(S[5:-1])
                if line not in header.setdefault(M, []):
                    header[M].insert(0, line)
                continue
            D = gen.sort_mod.get(S)
            if D is None or D == M or kinds.get(S, 'builtin') == 'builtin' or S in view_sorts:
                continue
            line = '  pr %s .' % D
            if line not in header.setdefault(M, []):
                header[M].insert(0, line)
    aux = aux_module(gen)
    after.setdefault(anchor_module(gen, None), [])[0:0] = aux
    upgraded = set(v for vs in view_sorts.values() for v in vs)
    list_plan(gen, coll, upgraded, after)
    plan = Plan(kinds, header, coll.nested, numeq, {v: eqname_of(gen.views[v]['elt'])
                                                    for vs in view_sorts.values() for v in vs})
    plan.after = after
    # equation modules (dm-eq.maude) loaded before main's modules: every module of main.maude and every test
    # module imports them, so their equalities apply where the spec is executed
    # an equation module imports LIST-EQ-X only when its equations call listEqX (declared there)
    lines = after.get(PRE_MAIN, [])
    res, k = [], 0
    while k < len(lines):
        mm = re.match(r'^mod (DM-EQ-\S+|DM-AUX-EQ-MAP|DM-AUX-EQ-EXP) is$', lines[k])
        if not mm:
            res.append(lines[k])
            k += 1
            continue
        j = k
        while lines[j] != 'endm':
            j += 1
        body = ' '.join(lines[k:j])
        extra = [list_eq_mod(a) for a in sorted(gen.list_eq) if list_eq_fn(a) + '(' in body]
        gen.dm_imports[mm.group(1)] = gen.dm_imports.get(mm.group(1), []) + extra
        res.append(lines[k])
        res.extend('  pr %s .' % x for x in extra)
        res.extend(lines[k + 1:j + 1])
        k = j + 1
    # import bundles of main's modules, in dm-eq.maude after the equation modules they import.  A test module's
    # bundle goes in its own test file (derive_test_text), a ground-check wrapper's in the check input (ground_check)
    groups, gen.dm_use = bundle_groups(gen, list(gen.main_order))
    for name, names in groups.items():
        res.extend(dm_module(gen, name, names, []))
    after[PRE_MAIN] = res
    plan.late = [mm.group(1) for ln in res for mm in [re.match(r'^mod (\S+) is$', ln)] if mm]
    return plan


def list_plan(gen, coll, upgraded, after):
    """LIST-DM instantiations and the list equalities (LIST-EQ-X) placed before the first module that
    uses them (the anchor's end when only constructors use them).  An upgraded element view (TRIV-EQ) is
    not a TRIV parameter of LIST-DM, so LIST{X} then instantiates a companion view X-TRIV (TRIV) and
    renames the sort back to List{X}.  The value parameter of MAP-DM is TRIV too: MAP{K,X} uses X-TRIV
    the same way (the companion is placed before the first module of either kind)."""
    order = lambda m: gen.mod_order.get(m, 0)
    for a in sorted(set(coll.list_users) | set(coll.triv_users) | gen.list_eq):
        S = 'List{%s}' % a
        users = set(coll.list_users.get(a, ())) | set(M for M, Ss in coll.uses.items() if S in Ss)
        tusers = set(coll.triv_users.get(a, ()))
        if not users and not tusers and a not in gen.list_eq:
            continue
        comp = []
        if a in upgraded:
            v = gen.views[a]
            comp = ['view %s from TRIV to %s is' % (triv_name(a), v['to']),
                    '  sort Elt to %s .' % v['elt'], 'endv', '']
        block = list_eq_block(gen, a, upgraded, users) if a in gen.list_eq else []
        if tusers:
            if comp:
                coll.nested.setdefault(min(users | tusers, key=order), []).append(('triv-%s' % a, comp))
            comp = []
        block = comp + block
        if not block:
            continue
        if users:
            coll.nested.setdefault(min(users, key=order), []).append(('list-%s' % a, block))
        else:
            after.setdefault(anchor_module(gen, None), [])[0:0] = block


def list_eq_block(gen, a, upgraded, users):
    """LIST-EQ-a: the structural equality of List{a}, defined from the equality of the elements
    (`.=.` of the element sort, declared in its module D, which must be loaded before the first user)."""
    S = 'List{%s}' % a
    f = list_eq_fn(a)
    D = gen.sort_mod.get(a)
    if users:
        first = min(users, key=lambda m: gen.mod_order.get(m, 0))
        if D is not None and gen.mod_order.get(D, 0) >= gen.mod_order.get(first, 0):
            raise GenError('LIST-EQ-%s: element module %s is not loaded before %s' % (a, D, first))
    imports = ['LIST-DM', 'DM-BOOL', 'DM-INT'] + ([D] if D is not None else [])
    gen.dm_imports[list_eq_mod(a)] = imports
    lines = ['fmod %s is' % list_eq_mod(a), '  pr %s .' % list_inst(a, upgraded),
             '  pr DM-BOOL .', '  pr DM-INT .']
    if D is not None:
        lines.append('  pr %s .' % D)
    lines.append('  op %s : %s %s -> Bool .' % (f, S, S))
    # The identity axiom of the juxtaposition makes (EX LX) match every non-empty list (and a one-element
    # list, with LX = nil), so only the shapes nil and (EX LX) are written.  An equation with a bare
    # element pattern or with a (nil LX) pattern would overlap with these and give conflicting results.
    lines += ['  vars EX EY : %s .' % a, '  vars LX LY : %s .' % S,
              '  eq %s(nil, nil) = true .' % f,
              '  eq %s(nil, (EY LY)) = false .' % f,
              '  eq %s((EX LX), nil) = false .' % f,
              '  eq %s((EX LX), (EY LY)) = %s and %s(LX, LY) .' % (f, arg_eq(gen, 'EX', 'EY', a), f),
              'endfm', '']
    return lines


def list_inst(a, upgraded):
    """The LIST-DM instantiation of element view a (the same text as pr_auto emits)."""
    if a in upgraded:
        return 'LIST-DM{%s} * (sort List{%s} to List{%s})' % (triv_name(a), triv_name(a), a)
    return 'LIST-DM{%s}' % a


class Plan(object):
    def __init__(self, kinds, header, nested, numeq, upgrade):
        self.kinds = kinds
        self.header = header
        self.nested = nested
        self.numeq = numeq
        self.upgrade = upgrade
        self.after = {}


def pr_auto(ctx, u, t):
    """pr SET{..} / pr MAP{..} / pr LIST{..}, with optional `* (renames)`."""
    gen, mod = ctx.gen, ctx.mod
    where = '%s:%d' % (ctx.fname, u.line)
    m = re.match(r'^(pr|protecting|inc|including)\s+(.*?)\s*\.$', t)
    if not m:
        return None
    kw, body = m.group(1), m.group(2)
    renames = []
    star = body.find(' * (')
    if star >= 0:
        rest = body[star + 4:].rstrip()
        if not rest.endswith(')'):
            raise GenError('%s: unsupported renaming in %s' % (where, t))
        renames = [rest[:-1].strip()]          # kept verbatim (operator names may contain commas)
        body = body[:star].strip()
    hm = re.match(r'^(SET|MAP|LIST)\{(.*)\}$', body)
    if not hm:
        return None
    base = hm.group(1)
    args = split_top(hm.group(2), ',')
    new_args, sort_renames = [], []
    for k, a in enumerate(args):
        a = a.strip()
        nested = re.match(r'^(Set|List)\{(.*)\}$', a)
        if nested:
            inner = nested.group(2).strip()
            if inner not in gen.views:
                raise GenError('%s: nested %s{%s} needs a view for %s' % (where, nested.group(1), inner, inner))
            nv = ('SET-OF-' if nested.group(1) == 'Set' else 'LIST-OF-') + inner
            _nested_view(gen, mod, nv, nested.group(1), inner)
            new_args.append(nv)
            sort_renames.append(a)
            continue
        if a == 'Nat' and base == 'MAP' and k == 0:
            new_args.append('NatEq')
            sort_renames.append('Nat')
            continue
        if a == 'Nat':
            new_args.append('Nat')
            sort_renames.append('Nat')
            continue
        if a not in gen.views:
            raise GenError('%s: view %s not found for %s{...}' % (where, a, base))
        if (base == 'SET' or (base == 'MAP' and k == 0)) and gen.coll is not None:
            gen.coll.view_needs.add(a)
        if base == 'LIST' and gen.coll is not None:
            gen.coll.list_users.setdefault(a, set()).add(mod)
        if base == 'MAP' and k == 1 and gen.coll is not None:
            gen.coll.triv_users.setdefault(a, set()).add(mod)
        if (base == 'LIST' or (base == 'MAP' and k == 1)) and gen.plan is not None and a in gen.plan.upgrade:
            a_new = triv_name(a)
            new_args.append(a_new)
            sort_renames.append(a)
            continue
        new_args.append(a)
        sort_renames.append(a)
    newbody = '%s-DM{%s}' % (base, ','.join(new_args))
    if new_args != sort_renames:
        sname = {'SET': 'Set', 'MAP': 'Map', 'LIST': 'List'}[base]
        srt_old = '%s{%s}' % (sname, ','.join(sort_renames))
        srt_new = '%s{%s}' % (sname, ','.join(new_args))
        renames = ['sort %s to %s' % (srt_new, srt_old)] + renames
    if renames:
        newbody += ' * (%s)' % ', '.join(renames)
    return '%s %s .' % (kw, newbody)


def _nested_view(gen, mod, name, coll_kind, inner):
    """Generated view instantiating SET-DM / LIST-DM with a sort built on another view."""
    if gen.coll is None:
        return
    lst = gen.coll.nested.setdefault(mod, [])
    if any(n == name for n, _ in lst):
        return
    if coll_kind == 'Set':
        lines = ['view %s from TRIV to SET-DM{%s} is' % (name, inner),
                 '  sort Elt to Set{%s} .' % inner, 'endv', '']
        gen.coll.view_needs.add(inner)
    else:
        lines = ['view %s from TRIV to LIST-DM{%s} is' % (name, inner),
                 '  sort Elt to List{%s} .' % inner, 'endv', '']
    lst.append((name, lines))


def owise_recurse_args(args):
    return args


def transform_unit(ctx, u, raw):
    """Replacement lines (without EOL) for a unit, or None to keep it verbatim."""
    gen = ctx.gen
    dirs = [parse_directive(d) for d in u.dmc]
    where = '%s:%d' % (ctx.fname, u.line)
    if u.kind == 'head':
        if u.text.split()[0] == 'view':
            if dirs and [k for k, v in dirs] != ['keep-builtin']:
                raise GenError('%s: directive not allowed on a view header' % where)
            m = re.match(r'^view\s+(\S+)\s+from\s+(\S+)\s+to\s+(\S+)\s+is$', u.text)
            ctx.view = m.group(1)
            ctx.mod = None
            if gen.plan is not None and ctx.view in gen.plan.upgrade:
                return ['view %s from TRIV-EQ to %s is' % (m.group(1), m.group(3))]
            return None
        if dirs:
            raise GenError('%s: directive on a module header is not supported' % where)
        ctx.mod = mod_name_of_head(u.text)
        ctx.view = None
        if gen.plan is None:
            return None
        pre = [ln for _, lines in gen.plan.nested.get(ctx.mod, []) for ln in lines]
        post = gen.plan.header.get(ctx.mod, [])
        if not pre and not post:
            return None
        return pre + [raw] + post
    if u.kind == 'end':
        ended = ctx.mod
        ctx.mod = None
        ctx.view = None
        if dirs:
            raise GenError('%s: directive before %s' % (where, u.text))
        if gen.plan is not None and ended in gen.plan.after:
            return [raw] + gen.plan.after[ended]
        return None
    if u.kind != 'stmt':
        if dirs and u.kind != 'cmd':
            raise GenError('%s: directive on a %s unit' % (where, u.kind))
        return None
    if ctx.view is not None:
        if dirs:
            raise GenError('%s: directive inside a view body is not supported' % where)
        v = gen.views.get(ctx.view)
        if gen.plan is not None and ctx.view in gen.plan.upgrade and u.text.startswith('sort Elt to '):
            return [raw, '  op eq? to %s .' % gen.plan.upgrade[ctx.view]]
        return None
    if ctx.mod is None:
        if dirs:
            raise GenError('%s: directive outside a module' % where)
        return None
    t = u.text
    kinds = [k for k, v in dirs]
    if 'qid-subsort' in kinds or 'qid-literals' in kinds:
        return []
    if 'close' in kinds:
        sname, cname = [v for k, v in dirs if k == 'close'][0]
        return [raw, ctx.indent + 'pr NAT .', ctx.indent + 'op %s : Nat -> %s [ctor] .' % (cname, sname)]
    m = CONST_DECL_RE.match(t)
    if m and not dirs and len(m.group(1).split()) == 1 and (m.group(1), m.group(2)) in gen.make_ctor \
            and 'ctor' not in (m.group(3) or ''):
        attrs = (m.group(3) or '').split() + ['ctor']
        return [ctx.indent + 'op %s : -> %s [%s] .' % (m.group(1), m.group(2), ' '.join(attrs))]
    cd = const_decl(gen, t)
    if cd is not None:
        if dirs:
            raise GenError('%s: directive on a constant of a closed sort' % where)
        names, rng, attrs = cd
        return [ctx.indent + const_decl_text(names, rng, attrs)] + [
            ctx.indent + 'eq %s = %s .' % (n, gen.const_terms[n]) for n in names]
    if t.startswith(('pr ', 'protecting ', 'including ', 'inc ')):
        if dirs:
            raise GenError('%s: directive on a import' % where)
        new = pr_auto(ctx, u, t)
        if new is not None:
            gen.count(ctx.fname, 'auto-pr')
            return [ctx.indent + new]
        return None
    rs = [v for k, v in dirs if k == 'restrict']
    if rs:
        for v, srt in rs[0]:
            t = re.sub(r"(?<![\w\-'$#%%:])%s(?![\w\-'$#%%:])" % re.escape(v), '%s:%s' % (v, srt), t)
        gen.count(ctx.fname, 'restrict')
        sep = ' => ' if t.startswith(('rl ', 'crl ')) else ' = '
        if sep in t:
            # the left-hand side keeps the IMP constructor (V:Value == V':Value); the right side is rewritten
            i = t.index(sep) + len(sep)
            keep = any(k == 'keep-builtin' for k, v in dirs)
            return [ctx.indent + t[:i] + rewrite_text(ctx, t[i:], where, keep)]
    if t.startswith(('rl ', 'crl ')):
        keep = any(k == 'keep-builtin' for k, v in dirs)
        if '==' in t or '=/=' in t:
            gen.count(ctx.fname, 'auto-builtin-rule')
            return [ctx.indent + rewrite_text(ctx, t, where, keep)] if not keep else None
        return None
    e = parse_eq(t)
    if e is not None:
        if e['owise']:
            kind, stmts, info = owise_stmts(ctx, u, e, dirs)
            if info is not None:
                gen.manifest.append(dict(info, file=ctx.fname, line=u.line))
                gen.count(ctx.fname, 'annot-owise-' + info['kind'])
            else:
                gen.count(ctx.fname, 'owise-' + kind)
            return [ctx.indent + x for x in stmts]
        keep = any(k == 'keep-builtin' for k, v in dirs)
        if dirs and [k for k, v in dirs] != ['keep-builtin']:
            raise GenError('%s: directive on a non-owise equation: %s' % (where, [v for k, v in dirs]))
        if '==' in t or '=/=' in t:
            if keep:
                gen.count(ctx.fname, 'keep-builtin-eq')
                return None
            gen.count(ctx.fname, 'auto-builtin')
            return [ctx.indent + rewrite_text(ctx, t, where)]
        return None
    if dirs:
        raise GenError('%s: directive on an unsupported statement' % where)
    return None


class State(object):
    pass


def transform_file(gen, fname, fi):
    ctx = Ctx(gen, fname)
    out = []
    units = fi.units
    k = 0
    i = 0
    n = len(fi.lines)
    eol = fi.eol

    def raw_of(u):
        return [fi.lines[j][0] for j in range(u.start, u.end + 1)]
    while i < n:
        if k < len(units) and units[k].start == i:
            u = units[k]
            if u.kind == 'block' and any(parse_directive(d)[0] == 'replace' for d in u.dmc):
                e = k + 1
                while e < len(units) and units[e].kind != 'dmcend':
                    e += 1
                if e >= len(units):
                    raise GenError('%s:%d: replace without ---@dmc end' % (fname, u.line))
                inner = [re.sub(r'^\s*---', '', fi.lines[j][0], count=1) for j in range(u.start + 1, u.end)]
                for ln in inner:
                    out.append(ln + eol)
                gen.replaces.append('%s:%d' % (fname, u.line))
                gen.count(fname, 'replace')
                i = units[e].end + 1
                k = e + 1
                continue
            if u.kind == 'dmcend':
                i = u.end + 1
                k += 1
                continue
            if u.kind == 'stmt':
                ctx.indent = re.match(r'^(\s*)', fi.lines[u.start][0]).group(1)
            raw = '\n'.join(raw_of(u))
            try:
                res = transform_unit(ctx, u, raw)
            except GenError as ex:
                if gen.coll is None:
                    gen.errors.append(str(ex))
                res = None
            rule_unit = u.kind == 'stmt' and u.text.split()[0] in ('eq', 'ceq', 'rl', 'crl')
            if res is None:
                for ln in raw_of(u):
                    out.append((closed_rewrite_line(gen, ln) if rule_unit else ln) + eol)
            else:
                for ln in res:
                    for piece in ln.split('\n'):
                        if rule_unit:
                            piece = closed_rewrite_line(gen, piece)
                        out.append(piece + eol)
            i = u.end + 1
            k += 1
            continue
        if fi.kinds[i] == 'dmc':
            i += 1
            continue
        out.append(fi.lines[i][0] + fi.lines[i][1])
        i += 1
    return ''.join(out)


def derive_test_text(gen, rel, text):
    """Derived copy of a test file: constants of closed sorts become their constructor terms, and the
    test modules import the equation modules of the DMC build (see main_with_plan).  A test module whose
    equation modules are two or more imports one bundle, defined in this file right before the first
    module of the file that uses it (dm-eq.maude has no bundle of a test module)."""
    lines = split_lines_keep(text)
    units, kinds = scan_units([t for t, _ in lines], rel)
    tmods = [mod_name_of_head(u.text) for u in units
             if u.kind == 'head' and u.text.split()[0] in ('mod', 'omod')]
    tag = '-IN-' + re.sub(r'[^A-Za-z0-9]+', '-', rel[:-len('.maude')]).strip('-').upper()
    bundles, use = bundle_groups(gen, tmods, tag) if gen.plan is not None else ({}, {})
    out, k, i = [], 0, 0
    while i < len(lines):
        if k < len(units) and units[k].start == i:
            u = units[k]
            eol = lines[u.start][1]
            head = u.text.split()[0] if u.kind in ('stmt', 'cmd') and u.text else ''
            if u.kind == 'head' and u.text.split()[0] in ('mod', 'omod') and gen.plan is not None:
                name = mod_name_of_head(u.text)
                for b in use.get(name, []):
                    if b in bundles:
                        out.extend(x + eol for x in dm_module(gen, b, bundles.pop(b), []))
                for j in range(u.start, u.end + 1):
                    out.append(lines[j][0] + lines[j][1])
                out.extend('  pr %s .%s' % (m, eol) for m in use.get(name, []))
            elif u.kind == 'stmt' and const_decl(gen, u.text) is not None:
                names, rng, attrs = const_decl(gen, u.text)
                out.append(const_decl_text(names, rng, attrs) + eol)
                out.extend('eq %s = %s .%s' % (n, gen.const_terms[n], eol) for n in names)
            elif head in REWRITE_HEADS:
                for j in range(u.start, u.end + 1):
                    out.append(closed_rewrite_line(gen, lines[j][0]) + lines[j][1])
            else:
                for j in range(u.start, u.end + 1):
                    out.append(lines[j][0] + lines[j][1])
            i = u.end + 1
            k += 1
            continue
        out.append(lines[i][0] + lines[i][1])
        i += 1
    return ''.join(out)


def derive_tests(gen, tsrc, tout):
    """Write the derived test tree (same layout as tsrc) to tout; .maude files are rewritten."""
    if os.path.exists(tout):
        shutil.rmtree(tout)
    n = 0
    for root, dirs, files in os.walk(tsrc):
        for fn in files:
            p = os.path.join(root, fn)
            rel = os.path.relpath(p, tsrc)
            dst = os.path.join(tout, rel)
            os.makedirs(os.path.dirname(dst), exist_ok=True)
            if fn.endswith('.maude'):
                with open(p, encoding='utf-8', newline='') as fh:
                    text = fh.read()
                with open(dst, 'w', encoding='utf-8', newline='') as fh:
                    fh.write(derive_test_text(gen, rel, text))
                n += 1
            else:
                shutil.copy2(p, dst)
    return n


def load_positions(src, fis):
    """Module positions in Maude load order (a loaded file's own loads come before its modules)."""
    order, seen = [], set()

    def visit(f):
        if f in seen:
            return
        seen.add(f)
        path = os.path.join(src, f)
        if not os.path.exists(path):
            return
        with open(path, encoding='utf-8') as fh:
            for ln in fh:
                mm = re.match(r'^load\s+(\S+?)(?:\.maude)?\s*$', ln)
                if mm:
                    visit(mm.group(1) + '.maude')
        if f in fis:
            order.extend(mod_names_in(fis[f]))
    visit('main.maude')
    pos = {}
    for i, m in enumerate(order):
        if m:
            pos.setdefault(m, i)
    return pos


def mod_names_in(fi):
    out = []
    for u in fi.units:
        if u.kind == 'head' and u.text.split()[0] != 'view':
            out.append(mod_name_of_head(u.text))
    return out


def main_with_aux(text, eol):
    if re.search(r'^load aux\s*$', text, re.M):
        return text
    lines = text.splitlines(True)
    for idx, ln in enumerate(lines):
        if ln.startswith('load '):
            lines.insert(idx, 'load aux' + eol)
            return ''.join(lines)
    raise GenError('main.maude has no load line to insert `load aux` before')


def dm_eq_load_line(gen, text, eol):
    """The plan's equation modules (anchor PRE_MAIN) live in DM_EQ_FILE, which main.maude loads right after
    its last load line.  That is where they were before (after all loads, before main's first module), so
    the loaded order is the same.  A load line after main's first module would change that order: an error."""
    lines = text.splitlines(True)
    first = next((i for i, ln in enumerate(lines) if HEAD_RE.match(ln.rstrip('\r\n'))), len(lines))
    loads = [i for i, ln in enumerate(lines) if LOAD_RE.match(ln)]
    if any(i > first for i in loads):
        raise GenError('main.maude has a load line after its first module; %s cannot be placed before '
                       'the modules that follow it' % DM_EQ_FILE)
    if not loads:
        raise GenError('main.maude has no load line to insert `load %s` after' % DM_EQ_LOAD)
    lines.insert(loads[-1] + 1, 'load %s%s' % (DM_EQ_LOAD, eol))
    return ''.join(lines)


def main_with_plan(gen, text, eol):
    """main.maude declares modules too.  Its header and nested blocks get the plan's additions, and every
    module of main.maude imports the equation modules of DM_EQ_FILE (loaded by dm_eq_load_line), through
    its import bundle (dm_use), so that their equations apply to the top-level modules."""
    if gen.plan is None:
        return text
    if gen.plan.after.get(PRE_MAIN):
        text = dm_eq_load_line(gen, text, eol)
    out, cur = [], None
    for ln in text.splitlines(True):
        raw = ln.rstrip('\r\n')
        head = HEAD_RE.match(raw)
        if head:
            cur = head.group(2)
            pre = [x for _, lines in gen.plan.nested.get(cur, []) for x in lines]
            post = gen.plan.header.get(cur, []) + ['  pr %s .' % m for m in gen.dm_use.get(cur, [])]
            out.extend(x + eol for x in pre)
            out.append(ln)
            if post:
                out.extend(x + eol for x in post)
        else:
            out.append(ln)
    return ''.join(out)


# ---------------------------------------------------------------- self-checks

def list_juxt_terms(gen, mod, sort, groups_by_sort, depth):
    """Two-element juxtapositions (x y) of a List{X} sort: the shape no constructor case covers (the
    assoc constructor is not enumerated), so an owise equation over a list is only tested on these."""
    if not (sort.startswith('List{') and sort.endswith('}')) or depth < 1:
        return []
    el = ground_terms(gen, mod, sort[5:-1], groups_by_sort, limit=3, depth=depth - 1)
    el = [x for x in el if x]
    return ['(%s %s)' % (a, b) for a in el[:2] for b in el[:2]]


def ground_terms(gen, mod, sort, groups_by_sort, limit=6, depth=2):
    key = (mod, sort)
    if depth < 0:
        return []
    terms = []
    terms.extend("%s('%s)" % x for x in gen.qop_items.get(sort, []))
    if sort in groups_by_sort:
        g = groups_by_sort[sort]
        elems = []
        for name in g['elements']:
            c = gen.refl.ctor(mod, name)
            if c is None:
                continue
            ets = []
            for srt in c['doms']:
                ets.append(ground_terms(gen, mod, srt, groups_by_sort, limit=3, depth=depth - 1))
            if any(len(x) == 0 for x in ets):
                continue
            from itertools import product
            for combo in list(product(*ets))[:4]:
                elems.append((name, render(name, list(combo), nested=False), list(combo)))
        singles = [e[1] for e in elems]
        terms.append(g['empty'])
        terms.extend(singles)
        seen = set()
        for a in range(len(elems)):
            for b in range(len(elems)):
                if a == b:
                    continue
                ka = elems[a][2][0] if elems[a][2] else elems[a][1]
                kb = elems[b][2][0] if elems[b][2] else elems[b][1]
                if ka == kb:
                    continue
                pair = (min(a, b), max(a, b))
                if pair in seen:
                    continue
                seen.add(pair)
                t = render(g['sep'], [elems[a][1], elems[b][1]], nested=False)
                terms.append(t)
        terms.extend(list_juxt_terms(gen, mod, sort, groups_by_sort, depth))
        return terms[:limit * 4]
    for c in gen.refl.ctors(mod):
        if not gen.leq(mod, c['rng'], sort) or 'assoc' in c['attrs'] or 'comm' in c['attrs']:
            continue
        if c['rng'].startswith('['):
            continue
        ets = []
        for srt in c['doms']:
            ets.append(ground_terms(gen, mod, srt, groups_by_sort, limit=2, depth=depth - 1))
        if any(len(x) == 0 for x in ets):
            continue
        from itertools import product
        for combo in list(product(*ets))[:limit]:
            terms.append(render(c['name'], list(combo), nested=False))
    terms.extend(list_juxt_terms(gen, mod, sort, groups_by_sort, depth))
    out, seen2 = [], set()
    for t in terms:
        if t not in seen2:
            seen2.add(t)
            out.append(t)
    return out[:limit]


def run_build_tests(maude, build_dir, cmds, cache, late=None, bundles=None):
    """Results of `red` commands, in command order.  A command whose `select` or `red` line gets a
    parse warning from Maude (a ground term that the module does not know) yields None.  With `late`
    (the imports of each module in the DMC build: a bundle, or its equation modules), each module is selected
    through a wrapper that imports it and them.  A bundle in `bundles` (name -> imports) is written into this
    input right before the first wrapper that uses it."""
    lines = ['load %s' % os.path.join(build_dir, 'main.maude')]
    cmd_lines = []
    wrapped = {}
    if late is not None:
        pending = dict(bundles or {})
        for mod, _ in cmds:
            if mod not in wrapped:
                for b in late.get(mod, []):
                    if b in pending:
                        lines.append('mod %s is' % b)
                        lines.extend('  pr %s .' % x for x in pending.pop(b))
                        lines.append('endm')
                wrapped[mod] = 'GC-' + mod
                lines.append('mod GC-%s is' % mod)
                lines.extend('  pr %s .' % m for m in [mod] + list(late.get(mod, [])))
                lines.append('endm')
    for mod, cmd in cmds:
        lines.append('select %s .' % wrapped.get(mod, mod))
        lines.append('red %s .' % cmd)
        cmd_lines.append((len(lines) - 1, len(lines)))      # (select line, red line), 1-based
    lines.append('quit')
    os.makedirs(cache, exist_ok=True)
    path = os.path.join(cache, 'ground-%s.in' % os.path.basename(build_dir.rstrip(os.sep)))
    with open(path, 'w') as f:
        f.write('\n'.join(lines) + '\n')
    out = maude_run(maude, build_dir, path)
    bad_lines = set(int(m) for m in re.findall(r'^Warning: <standard input>, line (\d+)', out, re.M))
    keep = [i for i, (sl, rl) in enumerate(cmd_lines) if sl not in bad_lines and rl not in bad_lines]
    res_blocks = []
    for block in re.split(r'^=+\s*$', out, flags=re.M):
        if not re.search(r'^reduce in ', block, re.M):
            continue
        rm = re.search(r'^result [^:]+: (.*)$', block, re.M | re.S)
        # the last block also holds Maude's closing 'Bye.' line (quit in the input)
        res_blocks.append(re.sub(r'\s*Bye\.$', '', norm(rm.group(1))) if rm else 'NO-RESULT')
    if len(res_blocks) != len(keep):
        raise GenError('ground check: %d results for %d usable commands in %s'
                       % (len(res_blocks), len(keep), build_dir))
    res = [None] * len(cmds)
    for i, r in zip(keep, res_blocks):
        # a result that Maude reports with a warning (a term the module cannot parse) is not comparable
        res[i] = None if 'Warning' in r else r
    return res


def unclose_map(gen):
    inv = {}
    for S, items in gen.closed_items.items():
        for k, (kind, n) in enumerate(items):
            term = '%s(%d)' % (gen.closed[S]['ctor'], k)
            if kind == 'c':
                inv[term] = n
            elif kind == 'q':
                inv[term] = "'" + n
            else:
                inv[term] = "%s('%s)" % n
    return inv


def unqualify(s):
    """Sort-qualified constants, (nil).List{Data} (printed by Maude or written by gen-dmc for
    ambiguous names), read as the plain constant."""
    return re.sub(r"\(([A-Za-z][\w\-']*)\)\.[A-Z][\w\-]*(?:\{[^{}]*\})?",
                  lambda m: m.group(1) if m.group(1) in QUAL_CONSTS else m.group(0), s)


def unclose(gen, s, inv):
    s = unqualify(s)
    if not inv:
        return s
    names = sorted(set(gen.closed[S]['ctor'] for S in gen.closed), key=len, reverse=True)
    pat = re.compile(r'\b(?:%s)\(\d+\)' % '|'.join(re.escape(n) for n in names))
    return pat.sub(lambda m: inv.get(m.group(0), m.group(0)), s)


# Entry files of the derived tests: the files that make test runs (api tests from the tree root, the
# others from their own directory).  The fragments they load (cases/, api/aux.maude) are loaded by them.
# tests/main.maude is not an entry: no runner loads it, and its case files already give parse warnings
# in the plain build (tests/cases/test-*.maude: `;` in the terms), so it cannot be warning-free.
TEST_ENTRY_RE = re.compile(r'^(api/test-[^/]*|completion/[^/]*|gp-conformance/[^/]*)\.maude$')


def load_warnings(maude, cache, out_dir, tests_out):
    """Maude 'Warning:' lines when the DMC build loads (out_dir/main.maude), and when each derived test entry
    file loads on top of it.  Advisories are not warnings and are not reported.  Returns (file, line) pairs."""
    tree = os.path.dirname(out_dir)            # the tree root: build/<NAME>, holding src/ and tests/
    jobs = [(out_dir, os.path.join(out_dir, 'main.maude'), False)]
    if tests_out:
        for root, _, fns in os.walk(tests_out):
            for fn in sorted(fns):
                path = os.path.join(root, fn)
                rel = os.path.relpath(path, tests_out).replace(os.sep, '/')
                if TEST_ENTRY_RE.match(rel):
                    # api tests load ./src/main and ./tests/api/aux from the tree root (run-api-suite.py
                    # rewrites its header the same way); the other entries load ../src/main from their directory
                    jobs.append((tree, path, True) if rel.startswith('api/') else (root, path, False))
    found = []
    os.makedirs(cache, exist_ok=True)
    infile = os.path.join(cache, 'load-warnings.in')
    for cwd, path, api in jobs:
        load = path
        if api:
            text = open(path, encoding='utf-8').read()
            text = text.replace('load ./src/main', 'load %s' % os.path.join(out_dir, 'main'))
            text = text.replace('load ./tests/api/aux', 'load %s' % os.path.join(tree, 'tests', 'api', 'aux'))
            load = os.path.join(cache, 'entry-' + os.path.basename(path))
            with open(load, 'w', encoding='utf-8') as fh:
                fh.write(text)
        with open(infile, 'w') as fh:
            fh.write('load %s\nquit\n' % load)
        for line in maude_run(maude, cwd, infile).splitlines():
            if line.startswith('Warning:'):
                found.append((os.path.relpath(path, tree), line))
    return found


# Hand-written ground checks of the ExpList equality (DM-AUX-EQ-EXP): the expected Bool is what `==` gives
# on the same ExpLists in the plain build.  They run in TEE-BEHAVIOR, which imports the equation variants
# that the element equalities need.  The elements cover every kind of Exp (Value, Var, PExp, FExp).
AUX_LIST_CHECK_MODULE = 'TEE-BEHAVIOR'
AUX_LIST_CHECKS = [
    ('nil', 'nil', 'true'),
    ('nil', '(# 1, # 2)', 'false'),
    ('(# 1, # 2)', 'nil', 'false'),
    ('(# 1, # 2)', '(# 1, # 2)', 'true'),
    ('(# 1, # 2)', '(# 1, # 3)', 'false'),
    ('(# 1, # 2)', '# 1', 'false'),
    ('# 1', '# 1', 'true'),
    ('(int mkVar(1))', '(int mkVar(1))', 'true'),
    ('(int mkVar(1))', '(int mkVar(2))', 'false'),
    ('(int mkVar(1))', '(TEE-Handle * mkVar(1))', 'false'),
    ('(int mkVar(1), int mkVar(2))', '(int mkVar(1), int mkVar(2))', 'true'),
    ('(int mkVar(1), int mkVar(2))', '(int mkVar(1), int mkVar(3))', 'false'),
    ('(mkFuncId(0)(# 1))', '(mkFuncId(0)(# 1))', 'true'),
    ('(mkFuncId(0)(# 1))', '(mkFuncId(0)(# 2))', 'false'),
    ('(mkFuncId(0)())', '(mkFuncId(0)())', 'true'),
    ('(mkFuncId(0)())', '(mkFuncId(1)())', 'false'),
    ('(mkFuncId(0)())', '(int mkVar(1))', 'false'),
    ('(# 1, mkFuncId(0)())', '(# 1, mkFuncId(0)())', 'true'),
]


def aux_list_checks(gen, check_build, maude):
    """Run AUX_LIST_CHECKS in the DMC check build.  Returns (count, mismatches, skipped) in the shape of
    ground_check; a mismatch is ((module, command), expected, got)."""
    if AUX_LIST_CHECK_MODULE not in gen.mod_order:
        return 0, [], ['expListEq checks skipped (no %s module)' % AUX_LIST_CHECK_MODULE]
    cmds = [(AUX_LIST_CHECK_MODULE, 'expListEq(%s, %s)' % (x, y)) for x, y, _ in AUX_LIST_CHECKS]
    got = run_build_tests(maude, check_build, cmds, gen.cache_dir)
    mism = []
    for (x, y, want), (mod, cmd), g in zip(AUX_LIST_CHECKS, cmds, got):
        if g != want:
            mism.append(((mod, cmd), want, 'NO-RESULT' if g is None else g))
    return len(cmds), mism, []


def ground_check(gen, orig_dir, out_dir, maude):
    groups = {}
    tests = []
    skipped = []
    for g in gen.manifest:
        mod = g['module']
        doms = g['doms']
        if g['kind'] == 'recurse':
            groups.setdefault(g['sort'], dict(g, elements=g['elements'], empty=g['empty'], sep=g['sep']))
    for g in gen.manifest:
        mod = g['module']
        argsets = []
        for srt in g['doms']:
            gt = ground_terms(gen, mod, srt, groups)
            argsets.append(gt)
        if any(len(a) == 0 for a in argsets):
            skipped.append(g['fn'])
            continue
        from itertools import product
        for combo in list(product(*[a[:4] for a in argsets]))[:40]:
            cmd = '%s(%s)' % (g['fn'], ', '.join(combo))
            tests.append((mod, cmd))
    # remove duplicates
    seen, uniq = set(), []
    for t in tests:
        if t not in seen:
            seen.add(t)
            uniq.append(t)
    cache = gen.cache_dir
    a = [None if x is None else unqualify(x) for x in run_build_tests(maude, orig_dir, uniq, cache)]
    inv = unclose_map(gen)
    wmods = list(dict.fromkeys(m for m, _ in uniq))
    wbund, wuse = bundle_groups(gen, wmods, '-GC')
    dmc_cmds = [(m, qop_rewrite(gen, c)) for m, c in uniq]
    b = [None if x is None else unclose(gen, x, inv)
         for x in run_build_tests(maude, out_dir, dmc_cmds, cache, late=wuse, bundles=wbund)]
    mism = []
    unparsed = 0
    stuck = []
    for idx, t in enumerate(uniq):
        if a[idx] is None or b[idx] is None:
            unparsed += 1
            continue
        if a[idx] != b[idx]:
            # numEq (order based) left unreduced on a non-numeral argument (an ill-formed ground term whose
            # Nat subterm is stuck): the built-in == of the original compares normal forms, so it is
            # reported apart, not as a value mismatch
            if a[idx] in ('true', 'false') and '<=' in b[idx]:
                stuck.append((t, a[idx], b[idx]))
                continue
            mism.append((t, a[idx], b[idx]))
    aux_total, aux_mism, aux_skip = aux_list_checks(gen, out_dir, maude)
    mism += aux_mism
    skipped += aux_skip
    if unparsed:
        skipped.append('%d unparsable test commands skipped' % unparsed)
    if stuck:
        skipped.append('%d stuck (unreduced numEq on ill-formed ground terms): %s'
                       % (len(stuck), '; '.join(sorted(set('%s::%s' % t[0] for t in stuck)))))
    return len(uniq) + aux_total, mism, skipped


IMPORT_RE = re.compile(r'^(?:pr|protecting|ex|extending|inc|including)\s+([^\s{*.]+)')


def parse_graph(texts):
    """Import edges of the modules in texts (pr/protecting/ex/extending/inc/including lines, read per module;
    a parameterized import such as SET{X} contributes its base name, which is not a source module)."""
    graph = {}
    for raw_text in texts:
        cur = None
        for raw in raw_text.splitlines():
            t = raw.strip()
            if not t or t.startswith('--'):
                continue
            h = mod_name_of_head(t)
            if h:
                cur = h
                graph.setdefault(cur, set())
                continue
            if re.match(r'^(endm|endfm|endth|endv)\b', t) or t.startswith('view '):
                cur = None
                continue
            m = IMPORT_RE.match(t)
            if m and cur:
                graph[cur].add(m.group(1))
    return graph


def import_closure(graph, roots):
    seen, todo = set(), list(roots)
    while todo:
        m = todo.pop()
        if m in seen:
            continue
        seen.add(m)
        todo.extend(graph.get(m, ()))
    return seen


def check_import_safety(gen):
    """Every main.maude module M gets only equation modules D whose import closure adds no source module
    to M's own import closure (gen.vis): the equations of D must not change which behavior M sees."""
    graph = dict((m, set(es)) for m, es in gen.graph.items())
    graph.update((m, set(es)) for m, es in gen.dm_imports.items())
    errors = []
    for M in sorted(gen.main_mods):
        for D in gen.dm_use.get(M, []):
            reach = import_closure(graph, [D])
            extra = sorted(m for m in reach - gen.vis[M]
                           if m in gen.mod_order and m not in gen.main_mods)
            if extra:
                errors.append('main module %s imports equation module %s, whose imports add %s '
                              '(not imported by %s)' % (M, D, ', '.join(extra), M))
    return errors


def loaded_files(src):
    """Files reached from main.maude through uncommented `load` lines (transitively)."""
    seen, todo = set(), ['main']
    while todo:
        m = todo.pop()
        if m in seen:
            continue
        seen.add(m)
        path = os.path.join(src, m + '.maude')
        if not os.path.exists(path):
            continue
        with open(path, encoding='utf-8') as fh:
            for ln in fh:
                mm = re.match(r'^load\s+(\S+?)(?:\.maude)?\s*$', ln)
                if mm:
                    todo.append(mm.group(1))
    return set(x + '.maude' for x in seen if x != 'main') | {'main.maude'}


def in_scope(f, only, loaded):
    """A file is transformed when it is loaded by main.maude and (with --only) listed there."""
    return f in loaded and (only is None or f in only)


def main(argv=None):
    ap = argparse.ArgumentParser(description='Generate the DM-Check-compatible build from annotated sources.')
    ap.add_argument('src')
    ap.add_argument('out')
    ap.add_argument('--only', default=None)
    ap.add_argument('--aux', default=DEFAULT_AUX)
    ap.add_argument('--maude', default=DEFAULT_MAUDE)
    ap.add_argument('--check', action='store_true')
    ap.add_argument('--dmc-base', default=None)
    ap.add_argument('--fresh', action='store_true')
    ap.add_argument('--tests', default=None, help='test tree to derive (same numbering), e.g. tests')
    ap.add_argument('--tests-out', default=None, help='derived test tree (default: <out>/../dmc-tests)')
    ap.add_argument('--cache-dir', default=None,
                    help='reflection cache, ground-check inputs and manifest.json (default: <out>/.dmc-cache)')
    args = ap.parse_args(argv)
    src = os.path.abspath(args.src)
    out = os.path.abspath(args.out)
    cache_dir = os.path.abspath(args.cache_dir) if args.cache_dir else os.path.join(out, '.dmc-cache')
    only = set(x.strip() for x in args.only.split(',')) if args.only else None
    os.makedirs(out, exist_ok=True)
    prev_manifest = None
    mpath = os.path.join(cache_dir, 'manifest.json')
    if os.path.exists(mpath):
        with open(mpath) as f:
            prev_manifest = json.load(f)

    files = sorted(f for f in os.listdir(src) if f.endswith('.maude'))
    loaded = loaded_files(src)
    fis = {}
    gen = Gen(None)
    if DM_EQ_FILE in files:
        gen.errors.append('%s/%s already exists: the name is reserved for the generated equation modules'
                          % (src, DM_EQ_FILE))
    for f in files:
        with open(os.path.join(src, f), encoding='utf-8', newline='') as fh:
            raw = fh.read()
        fi = FileInfo(f, raw)
        if in_scope(f, only, loaded):
            try:
                fi.units, fi.kinds = scan_units([t for t, _ in fi.lines], f)
            except GenError as ex:
                gen.errors.append(str(ex))
                fi.units, fi.kinds = [], []
        else:
            fi.units, fi.kinds = [], []
        fis[f] = fi
    scoped = [f for f in files if in_scope(f, only, loaded) and fis[f].kinds]
    for f in scoped:
        try:
            pass1(gen, f, fis[f])
        except GenError as ex:
            gen.errors.append(str(ex))
    mods = set(PRELUDE_MODS)
    for f in scoped:
        mods.update(m for m in mod_names_in(fis[f]) if m)
    mods = sorted(mods)
    refl, _ = run_reflection(src, cache_dir, mods, args.maude, args.fresh or args.check)
    refl.drop_ops(set(gen.qid_ops))
    gen.refl = refl
    gen.main_order = mod_names_in(fis['main.maude']) if 'main.maude' in fis else []
    gen.main_mods = set(gen.main_order)
    names_rng = {}
    for m in mods:
        for f in refl.mods.get(m, {}).get('ops', []) + refl.ctors(m):
            if not f['doms']:
                names_rng.setdefault(f['name'], set()).add(f['rng'])
    QUAL_CONSTS.update(n for n, rs in names_rng.items() if len(rs) > 1)
    for m in mods:
        gen.mod_ops[m] = dict(refl.own_ops(m))
    gen.cache_dir = cache_dir
    gen.mod_order = load_positions(src, fis)
    gen.src_texts = [fis[f].raw for f in scoped]
    test_texts, tests_src = [], None
    if args.tests:
        tests_src = os.path.abspath(args.tests)
        for root, _, fns in os.walk(tests_src):
            for fn in sorted(fns):
                if fn.endswith('.maude'):
                    with open(os.path.join(root, fn), encoding='utf-8') as fh:
                        test_texts.append(fh.read())
    # import closures of the main and test modules (the equation modules each of them may import)
    gen.graph = parse_graph([fis[f].raw for f in scoped] + test_texts)
    test_mods = set(parse_graph(test_texts)) - set(gen.mod_order)
    # every source module too: the ground checks (run_build_tests) select the variants of any module they test
    gen.vis = dict((M, import_closure(gen.graph, [M]))
                   for M in set(gen.main_mods) | test_mods | set(gen.mod_order))
    try:
        closed_numbering(gen, test_texts)
    except GenError as ex:
        gen.errors.append('closed sorts: %s' % ex)
    if gen.errors:
        sys.stderr.write('gen-dmc: %d error(s); nothing written\n' % len(gen.errors))
        for e in gen.errors:
            sys.stderr.write('  ERROR %s\n' % e)
        return 1

    # pass A: permissive, collects what needs equality, views and generated modules
    gen.coll = Collect()
    for f in scoped:
        if f != 'main.maude':
            transform_file(gen, f, fis[f])
    try:
        gen.plan = build_plan(gen)
    except GenError as ex:
        sys.stderr.write('gen-dmc: plan error: %s\n' % ex)
        return 1
    gen.coll = None
    gen.manifest, gen.counts, gen.replaces = [], {}, []

    # pass B: strict transformation
    new_text = {}
    for f in scoped:
        if f == 'main.maude':
            continue
        try:
            new_text[f] = transform_file(gen, f, fis[f])
        except GenError as ex:
            gen.errors.append(str(ex))
    main_text = None
    if 'main.maude' in fis:
        raw = fis['main.maude'].raw
        try:
            main_text = main_with_plan(gen, main_with_aux(raw, dominant_eol(raw)), dominant_eol(raw))
        except GenError as ex:
            gen.errors.append(str(ex))
    dm_eq_text = None
    if gen.plan is not None and gen.plan.after.get(PRE_MAIN):
        if 'main.maude' not in fis:
            gen.errors.append('equation modules have no main.maude to load them from')
        else:
            eol = dominant_eol(fis['main.maude'].raw)
            dm_eq_text = DM_EQ_HEADER + eol + ''.join(x + eol for x in gen.plan.after[PRE_MAIN])
    if gen.plan is not None and 'main.maude' in fis:
        gen.errors.extend(check_import_safety(gen))
    gen.errors.extend(owise_sibling_errors(gen, new_text))
    if gen.errors:
        sys.stderr.write('gen-dmc: %d error(s); nothing written\n' % len(gen.errors))
        for e in gen.errors:
            sys.stderr.write('  ERROR %s\n' % e)
        return 1

    drift = []
    if args.check and prev_manifest:
        for g in prev_manifest.get('groups', []):
            if g.get('nodrift'):
                continue
            cur = [c['name'] for c in elem_list(gen, g['module'], g['sort'], g['sep'], g['empty'],
                                                 set(g['excl']))]
            if sorted(cur) != sorted(e for e in g['elements'] if e != VARCASE):
                drift.append('%s:%d %s: enumerated ctors %s, reflection now %s'
                             % (g['file'], g['line'], g['fn'], g['elements'], cur))

    for old in os.listdir(out):
        if old.endswith('.maude'):
            os.remove(os.path.join(out, old))
    for f in os.listdir(src):
        p = os.path.join(src, f)
        if os.path.isfile(p) and not f.endswith('.maude'):
            shutil.copy2(p, os.path.join(out, f))
    for f in files:
        if f == 'main.maude':
            text = main_text
        elif f in new_text:
            text = new_text[f]
        else:
            text = fis[f].raw
        with open(os.path.join(out, f), 'w', encoding='utf-8', newline='') as fh:
            fh.write(text)
    if dm_eq_text is not None:
        with open(os.path.join(out, DM_EQ_FILE), 'w', encoding='utf-8', newline='') as fh:
            fh.write(dm_eq_text)
    shutil.copy2(args.aux, os.path.join(out, 'aux.maude'))
    tests_out = None
    if tests_src:
        tests_out = os.path.abspath(args.tests_out) if args.tests_out else os.path.join(
            os.path.dirname(out), 'dmc-tests')
        ntests = derive_tests(gen, tests_src, tests_out)
        print('  derived test files: %d -> %s' % (ntests, tests_out))
    manifest = {'version': GEN_VERSION, 'groups': gen.manifest, 'replaces': gen.replaces,
                'counts': gen.counts, 'warnings': gen.warnings,
                'views_upgraded': sorted(gen.plan.upgrade), 'kinds': gen.plan.kinds,
                'closed': dict((S, {'ctor': gen.closed[S]['ctor'], 'items': gen.closed_items[S]})
                               for S in sorted(gen.closed)),
                'qid_sort': gen.qid_sort, 'qid_ops': gen.qid_ops,
                'const_terms': gen.const_terms, 'qid_terms': gen.qid_terms}
    with open(mpath, 'w') as fh:
        json.dump(manifest, fh, indent=1)

    print('gen-dmc: wrote %s' % out)
    for f in sorted(gen.counts):
        print('  %-22s %s' % (f, ', '.join('%s=%d' % kv for kv in sorted(gen.counts[f].items()))))
    print('  replace uses: %d %s' % (len(gen.replaces), gen.replaces))
    print('  equality kinds: %s' % ', '.join('%s=%s' % (k, v) for k, v in sorted(gen.plan.kinds.items())))
    print('  views upgraded to TRIV-EQ: %s' % ', '.join(sorted(gen.plan.upgrade)))
    for S in sorted(gen.closed):
        print('  closed sort %s: ctor %s, numbered items %d' % (
            S, gen.closed[S]['ctor'], len(gen.closed_items[S])))
    for w in gen.warnings:
        print('  WARNING %s' % w)
    for d in drift:
        print('  DRIFT %s' % d)
    if args.check:
        check_build = os.path.join(cache_dir, 'check-build')
        if os.path.exists(check_build):
            shutil.rmtree(check_build)
        os.makedirs(check_build)
        base = os.path.abspath(args.dmc_base) if args.dmc_base else out
        for f in os.listdir(base):
            if os.path.isfile(os.path.join(base, f)):
                shutil.copy2(os.path.join(base, f), os.path.join(check_build, f))
        for f in os.listdir(out):
            if os.path.isfile(os.path.join(out, f)) and f.endswith('.maude'):
                shutil.copy2(os.path.join(out, f), os.path.join(check_build, f))
        total, mism, skipped = ground_check(gen, src, check_build, args.maude)
        print('  ground check: %d tests, %d mismatch(es); functions skipped (no ground terms): %s'
              % (total, len(mism), skipped))
        for t, a, b in mism[:20]:
            print('    MISMATCH %s::%s\n      ORIG %s\n      DMC  %s' % (t[0], t[1], a, b))
        warns = load_warnings(args.maude, cache_dir, out, tests_out if tests_src else None)
        print('  load warnings: %d' % len(warns))
        for f, w in warns:
            print('    LOAD %s: %s' % (f, w))
        if warns:
            return 3
        return 0 if not mism else 2
    return 0


if __name__ == '__main__':
    sys.exit(main())
