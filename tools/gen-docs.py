#!/usr/bin/env python3
"""Build a static documentation site for the Maude TEE specification.

    python3 tools/gen-docs.py --src src --out build/docs [--overview docs/overview.html]
                              [--examples docs/examples.json] [--check]

--examples adds the verified examples of tools/gen-examples.py under rules and ops, collapsed
and hidden by default (inline in the file pages only).

Standard library only. The site opens from file:// and needs no network.
index.html is the hand-written fragment docs/overview.html wrapped in the site chrome, with
[[kind:Name]] and [[kind:Name|text]] cross-reference macros resolved (an unknown target is an
error); the file table is generated and appended.
Output is deterministic: nothing is time-stamped, and every collection is
sorted before it is written.

The Maude parser is a pragmatic token scanner, not a full parser:
  * comments are `---` and `***` line comments;
  * a comment block directly above a module header or an op/ops/sort/sorts/
    class/rl/crl/eq/ceq/msg declaration is that item's doc comment;
  * `--- @tag ...` lines (`---@dmc`, `--- @dmc`, any tag besides @kind/@feature/@related)
    are tool annotations: a small muted tag line above the code they qualify;
  * comment lines that are clearly Maude code (a statement keyword, `:`, `=`, a
    trailing ` .`) are shown dimmed as commented-out code rather than as prose;
  * identifiers are linked to their definition using the import closure of the
    enclosing module; overloaded names link to an index entry that lists every
    candidate;
  * identifiers glued to brackets or a leading qualifier dot (`DATA](72)`,
    `retStatus[^`, `(normal).StorageStatus`) are split into tokens first, and a
    `name[` that heads a declared mixfix op (`invkTa[__]`) is kept as one token;
  * Maude builtins (`true`, `Nat`, `Set`, ...) that src/ does not declare are
    shown with the `builtin` class and reported apart from unresolved names.
"""

import argparse
import collections
import glob
import html
import itertools
import json
import os
import posixpath
import re
import sys
import textwrap

# --------------------------------------------------------------- vocabulary

MOD_KW = {'fmod', 'mod', 'omod', 'fth', 'th', 'view'}
END_KW = {'endfm', 'endm', 'endom', 'endfth', 'endth', 'endv'}
IMPORT_KW = {'pr', 'protecting', 'ex', 'extending', 'inc', 'including'}
SUBSORT_KW = {'subsort', 'subsorts', 'subclass'}
SORT_KW = {'sort', 'sorts'}
OP_KW = {'op', 'ops', 'msg', 'msgs'}
RULE_KW = {'rl', 'crl', 'eq', 'ceq'}
KEYWORDS = (MOD_KW | END_KW | IMPORT_KW | SUBSORT_KW | SORT_KW | OP_KW | RULE_KW | {
    'class', 'var', 'vars', 'load', 'is', 'from', 'to', 'if', 'and', 'or', 'not',
    'then', 'else', 'owise', 'in'})
DOC_KW = MOD_KW | SORT_KW | OP_KW | RULE_KW | {'class'}
PUNCT = {'(', ')', ',', '{', '}', '[', ']'}
HINT_FAMILY = {'sort': {'sort', 'class'}, 'mod': {'module', 'view'}}
SORT_CONTEXT = {':', '->', 'sort', 'sorts', 'subsort', 'subsorts', 'subclass', '<', 'to', 'from'}
ANCHOR_PREFIX = {'module': 'mod', 'view': 'view', 'sort': 'sort', 'class': 'class',
                 'op': 'op', 'attr': 'attr', 'rl': 'rl', 'eq': 'eq'}
KIND_ORDER = ['sort', 'class', 'module', 'view', 'op', 'rl', 'eq', 'attr']

# File categories, following the "Repository layout" table of README.md.
CATEGORIES = [
    ('Entry point', ['main']),
    ('Common objects and data', ['constants', 'data', 'tee-data-type', 'objects', 'handle',
                                 'transient-object', 'persistent-object']),
    ('TEE API interface and TA kernel', ['tee-api', 'tee-api-call', 'tee-msg', 'session',
                                         'tee-kernel']),
    ('Trusted OS and applications', ['os', 'os-service', 'app', 'ree-app', 'tee-app',
                                     'tee-client']),
    ('API behavior models', ['transient', 'persistent', 'generic', 'data-stream', 'enumerator',
                             'cryptographic']),
    ('Panics', ['tee-panic', 'persistent-panic', 'data-stream-panic', 'crypto-panic']),
    ('Program execution', ['k-prelude', 'imp', 'tee-imp']),
]
CATEGORY_OF = {stem: name for name, stems in CATEGORIES for stem in stems}

# Module annotations (docs/annotations.md). Kinds are listed in the order the modules page
# shows them; the explanations are the ones of docs/annotations.md.
KINDS = [
    ('constants', 'Enumerations and constant values: error codes, flags, type identifiers, ids.'),
    ('data', 'Data structures: sorts and functions over them, with no rules.'),
    ('state', 'Class declarations that describe object state.'),
    ('messages', 'Message declarations (msg) and request/return identifiers exchanged between components.'),
    ('behavior', 'Rewrite rules (rl, crl) that implement API behaviour.'),
    ('panic', 'Rules for panic (error-abort) behaviour.'),
    ('program', 'The imperative program language and model programs run by applications.'),
    ('interface', 'API call signatures and parameter plumbing between layers.'),
    ('assembly', 'Modules that only combine other modules, such as src/main.maude.'),
]
KIND_NAMES = [k for k, _ in KINDS]
KIND_TEXT = dict(KINDS)
KIND_TEXT['view'] = 'A view maps a theory onto a module. Views have no kind.'
DATA_KINDS = {'constants', 'data', 'state', 'messages'}
RULE_KINDS = {'behavior', 'panic'}
# @feature groups, in the order of mk/features.mk, then the groups without a feature flag
FEATURES = ['crypto', 'transient', 'persistent', 'generic', 'data-stream', 'enumerator',
            'ta-session', 'client', 'os', 'imp', 'core']
FLAG_FEATURES = {'crypto', 'transient', 'persistent', 'generic', 'data-stream', 'enumerator'}
FEATURE_RANK = {f: i for i, f in enumerate(FEATURES)}
ANNOT_RE = re.compile(r'\s*(?:---|\*\*\*)\s*@(kind|feature|related)\b(.*)$')
RELATED_CAP = 8     # related modules shown per group before the "+N more" expander


def feature_label(f):
    """The name used in mk/features.mk for a feature with a flag, else the group name."""
    return f.upper().replace('-', '_') if f in FLAG_FEATURES else f

TOKEN_RE = re.compile(r'"(?:[^"\\]|\\.)*"|[(),{}]|[^\s(),{}"]+')
OP_RE = re.compile(r'[=<>:|+\-*/&~!@#%^]+')
NUM_RE = re.compile(r'-?\d+(?:\.\d+)?')
ATTR_OPEN_RE = re.compile(r'\[[A-Za-z]')
COMMENT_RE = re.compile(r'(?<!\S)(---|\*\*\*)')
BANNER_RE = re.compile(r'\s*(?:-{4,}|\*{4,})\s*')
CODE_START_RE = re.compile(
    r'(?:rl|crl|eq|ceq|op|ops|msg|sort|sorts|subsort|subsorts|subclass|class|var|vars|pr|'
    r'protecting|ex|extending|inc|including|fmod|mod|omod|fth|th|view|endfm|endm|endom|'
    r'endfth|endth|endv|load)\b')
# ' -> ' is left out: it is common in prose ("Handle.oid -> resource"), and a code line with an
# arrow almost always also has ' : ', ' = ' or a trailing ' .'.
CODE_MARK_RE = re.compile(r' : | => | = |\s\.$|^\.$')
SLUG_KEEP_RE = re.compile(r'[^A-Za-z0-9-]')
MIX_HEAD_RE = re.compile(r'[A-Za-z][A-Za-z0-9\-]*\[')
# Declarations that are never split (their names are defined by the whole token).
DECL_KW = MOD_KW | END_KW | IMPORT_KW | SUBSORT_KW | SORT_KW | OP_KW | {'class', 'var', 'vars', 'load'}
# Maude builtin names. Used only when no declaration in src/ matches.
BUILTINS = {'true', 'false', 'Bool', 'Nat', 'Int', 'String', 'Qid', 'Set', 'List', 'Map',
            'SET', 'LIST', 'MAP', 'NAT', 'INT', 'BOOL', 'QID', 'STRING', 'TRIV', 'Elt',
            'empty', 'insert', 's', 'quo', 'rem', 'abs', 'Configuration', 'Object', 'Msg',
            'NeList', 'CONFIGURATION', 'Oid'}


# ------------------------------------------------------------------ data model

class Tok:
    def __init__(self, text, line, s, e):
        self.text = text
        self.line = line
        self.s = s
        self.e = e
        self.cls = None          # kw, op, num, str, attr, var, file
        self.is_def = None       # Def whose name is this token
        self.target = None       # (page, anchor) of the link target
        self.ambiguous = False
        self.title = None
        self.in_attr = False
        self.stmt = None
        self.vardecl = None      # (sort text, module) for a var declaration name
        self.res_def = None      # Def this token resolved to (unambiguous only)
        self.cands = []         # Defs this token may refer to, primary first
        self.glue = False        # the next character is ( { or [ (a mixfix head like `if(`)


class Stmt:
    def __init__(self, f, kw):
        self.f = f
        self.kw = kw
        self.toks = []
        self.module = None
        self.module_kind = None
        self.defs = []
        self.line0 = None
        self.line1 = None
        self.doc_start = None
        self.doc_lines = None
        self.doc_plain = None


_SEQ = itertools.count()


class Def:
    def __init__(self, kind, name, stem, module, tok, stmt, owner=None):
        self.seq = next(_SEQ)    # creation order: load order, used to pick a primary link
        self.uid = None          # index in the defs.js table
        self.code = ''
        self.kind = kind
        self.name = name
        self.stem = stem
        self.module = module
        self.tok = tok
        self.stmt = stmt
        self.owner = owner
        self.line = tok.line + 1
        self.sig = name
        self.doc = None
        self.anchor = None
        self.page = None
        self.role = None         # @kind of a module (None for views)
        self.feature = None      # @feature of a module
        self.related = []        # @related module names, as written
        self.examples = []       # verified examples (docs/examples.json) of a rule or op


class SrcFile:
    def __init__(self, stem, path, raw):
        self.stem = stem
        self.path = path
        self.raw = raw
        self.codes = []
        self.comments = []
        self.kinds = []
        self.line_toks = [[] for _ in raw]
        self.stmts = []
        self.mod_defs = []
        self.summary = ''


class Site:
    def __init__(self):
        self.files = []
        self.stems = set()
        self.by_name = collections.defaultdict(list)
        self.defs = []
        self.module_defs = {}
        self.imports = collections.defaultdict(list)
        self.view_targets = collections.defaultdict(list)
        self.var_table = {}
        self.var_decl = {}       # (module, name) -> Def of the var declaration
        self.vardefs = []        # var declaration Defs (not in by_name, not indexed)
        self.alldefs = []        # defs + vardefs, in defs.js order
        self.usage = collections.defaultdict(set)
        self.importers = collections.defaultdict(set)
        self.supers = collections.defaultdict(set)
        self.subs = collections.defaultdict(set)
        self.unresolved = collections.Counter()
        self.builtins = collections.Counter()
        self.mix_heads = {}
        self.stats = collections.Counter()
        self._vis = {}
        self.annot_errors = []
        self.roles = {}                          # module -> @kind
        self.related = collections.defaultdict(set)   # undirected @related links
        self.uses = collections.defaultdict(set)      # rule module -> data modules its rules use
        self.used_by = collections.defaultdict(set)   # data module -> rule modules using it

    def visible(self, mod):
        """Module names whose declarations are in scope inside `mod`."""
        if mod is None:
            return set()
        if mod in self._vis:
            return self._vis[mod]
        seen = set()
        stack = [mod]
        while stack:
            m = stack.pop()
            if m in seen:
                continue
            seen.add(m)
            stack.extend(self.imports.get(m, ()))
            stack.extend(self.view_targets.get(m, ()))
        self._vis[mod] = seen
        return seen

    def visible_var(self, mod, name):
        for m in sorted(self.visible(mod)):
            if (m, name) in self.var_table:
                return m, self.var_table[(m, name)]
        return None


# ------------------------------------------------------------------ scanning

def esc(s):
    return html.escape(s, quote=True)


def slug(name):
    return SLUG_KEEP_RE.sub(lambda m: '_%02x' % ord(m.group(0)), name)


def split_comment(line):
    s = line.rstrip()
    st = s.lstrip()
    if st.startswith('---') or st.startswith('***'):
        return '', st
    m = COMMENT_RE.search(s)
    if m:
        return s[:m.start()], s[m.start():]
    return s, None


def tokenize(code, lineno):
    out = []
    for m in TOKEN_RE.finditer(code):
        text = m.group(0)
        s, e = m.start(), m.end()
        if len(text) > 1 and text.endswith('.') and not text.startswith('"'):
            out.append(Tok(text[:-1], lineno, s, e - 1))
            out.append(Tok('.', lineno, e - 1, e))
        else:
            out.append(Tok(text, lineno, s, e))
        out[-1].glue = code[e:e + 1] in ('(', '{', '[')
    return out


def mark_attrs(toks):
    opened = False
    for t in toks:
        if opened:
            t.cls = 'attr'
            t.in_attr = True
            if ']' in t.text:
                opened = False
        elif ATTR_OPEN_RE.match(t.text):
            t.cls = 'attr'
            t.in_attr = True
            opened = ']' not in t.text


def comment_body(raw):
    return re.sub(r'^\s*(?:---|\*\*\*)\s?', '', raw, count=1)


TAG_RE = re.compile(r'\s*(?:---|\*\*\*)\s*@([A-Za-z][\w-]*)\s*(.*)$')


def is_tag(raw):
    """A tool annotation line (`--- @dmc ...`, `---@dmc ...`): any @tag but @kind/@feature/@related."""
    return bool(TAG_RE.match(raw)) and not ANNOT_RE.match(raw)


def is_codelike(body):
    """A comment line that is Maude code rather than prose: a statement keyword with a code
    mark or a statement end, or a marked line that ends the statement with ` .`. A keyword alone
    does not make a line code ("class is not clsCipher, ... TK.") and neither does a bare arrow."""
    b = body.strip()
    if not b:
        return False
    if b == '.':
        return True
    if CODE_START_RE.match(b):
        return bool(CODE_MARK_RE.search(b) or b.endswith((' .', ' :', ' is')))
    return bool(CODE_MARK_RE.search(b) and b.endswith(' .'))


def comment_kind(raw, body):
    """One of: annot, tag, banner, blank, code, prose."""
    if ANNOT_RE.match(raw):
        return 'annot'
    if is_tag(raw):
        return 'tag'
    if BANNER_RE.fullmatch(raw):
        return 'banner'
    if not body.strip():
        return 'blank'
    if is_codelike(body):
        return 'code'
    return 'prose'


def is_code_line(b):
    """A comment body that is a Maude statement line: a keyword start with a code tail,
    or a module/closer line (omod ... is, endom, ...)."""
    words = b.split()
    if not words:
        return False
    if words[0] in END_KW or (words[0] in MOD_KW and b.endswith(' is')):
        return True
    return bool(CODE_START_RE.match(b) and (CODE_MARK_RE.search(b) or b.endswith((' .', ' :', ':', ' is'))))


def first_meaningful(f, idxs):
    """The first comment line of a run that is not empty, a nested comment or a dmc line."""
    for i in idxs:
        raw = f.raw[i]
        b = comment_body(raw).strip()
        if b and not is_tag(raw) and not b.startswith(('---', '***')):
            return i
    return None


def run_is_code(f, idxs):
    """True when a run of comment lines is commented-out Maude code (`--- omod ...`, `--- op ...`):
    its first meaningful line is a statement line. Continuation lines of a statement are code too."""
    i = first_meaningful(f, idxs)
    return i is not None and is_code_line(comment_body(f.raw[i]).strip())


def stmt_index(f, idxs):
    """Position in idxs of the first statement line of a run (None when there is none)."""
    for k, i in enumerate(idxs):
        raw = f.raw[i]
        b = comment_body(raw).strip()
        if b and not is_tag(raw) and not b.startswith(('---', '***')) and is_code_line(b):
            return k
    return None


def run_nested_only(f, idxs):
    """True when every non-empty line of a comment run is a nested comment (`--- --- text`)."""
    bodies = [comment_body(f.raw[i]).strip() for i in idxs]
    bodies = [b for b in bodies if b]
    return bool(bodies) and all(b.startswith(('---', '***')) for b in bodies)


def commented_region_end(f, j):
    """End of a block of commented-out code that goes on past empty lines: the following
    comment runs are commented code or nested comments. A module (`--- omod ...` through
    `--- endom`) is one block even though its statements are separated by empty lines."""
    n = len(f.raw)
    end = j
    while True:
        m = end
        while m < n and f.kinds[m] == 'blank':
            m += 1
        if m == end or m >= n or f.kinds[m] != 'comment':
            break
        k = m
        while k < n and f.kinds[k] == 'comment':
            k += 1
        if not (run_is_code(f, range(m, k)) or run_nested_only(f, range(m, k))):
            break
        end = k
    return end


def prose_of_run(f, idxs):
    if run_is_code(f, idxs):
        return False
    return any(comment_kind(f.raw[i], comment_body(f.raw[i])) == 'prose' for i in idxs)


def plain_text(f, idxs):
    paras, cur = [], []
    for i in idxs:
        raw = f.raw[i]
        body = comment_body(raw)
        k = comment_kind(raw, body)
        if k == 'prose':
            cur.append(body.strip())
        elif k == 'blank':
            if cur:
                paras.append(' '.join(cur))
                cur = []
    if cur:
        paras.append(' '.join(cur))
    return '\n\n'.join(paras)


def stmt_text(st):
    t = ' '.join(x.text for x in st.toks if x.text != '.')
    return t if len(t) <= 240 else t[:237] + '...'


def scan_file(site, f):
    toks = []
    for i, code in enumerate(f.codes):
        if code.strip():
            toks.extend(tokenize(code, i))
    mark_attrs(toks)
    for t in toks:
        f.line_toks[t.line].append(t)

    # Statements end at a `.` token, at `is` in a module header, or after the
    # two tokens of a `load` line. Closers (endfm, ...) are one-token statements.
    cur = None
    for t in toks:
        if cur is None:
            cur = Stmt(f, t.text)
            if t.text in END_KW:
                cur.toks.append(t)
                t.stmt = cur
                cur.line0 = cur.line1 = t.line
                f.stmts.append(cur)
                cur = None
                continue
        cur.toks.append(t)
        t.stmt = cur
        done = (t.text == '.' or (cur.kw in MOD_KW and t.text == 'is')
                or (cur.kw == 'load' and len(cur.toks) == 2))
        if done:
            cur.line0 = cur.toks[0].line
            cur.line1 = t.line
            f.stmts.append(cur)
            cur = None
    if cur is not None:
        cur.line0 = cur.toks[0].line
        cur.line1 = cur.toks[-1].line
        f.stmts.append(cur)

    mod, mkind = None, None
    for st in f.stmts:
        if st.kw in END_KW:
            st.module, st.module_kind = mod, mkind
            mod, mkind = None, None
            continue
        if st.kw in MOD_KW and len(st.toks) > 1:
            name = st.toks[1].text
            mod, mkind = name, st.kw
            st.module, st.module_kind = name, st.kw
            md = add_def(site, f, 'view' if st.kw == 'view' else 'module', name, st.toks[1], st,
                         module=name)
            if st.kw != 'view':
                read_annotation(site, f, st, md)
            if st.kw == 'view':
                texts = [t.text for t in st.toks]
                ii = texts.index('is') if 'is' in texts else len(texts)
                fi = texts.index('from') if 'from' in texts else ii
                ti = texts.index('to') if 'to' in texts else ii
                targets = [x for x in texts[fi + 1:ti] + texts[ti + 1:ii]
                           if x not in PUNCT and x != '.']
                site.view_targets[name].extend(targets)
            continue
        st.module, st.module_kind = mod, mkind
        parse_stmt(site, f, st)

    attach_docs(f)
    f.summary = first_summary(f)


def read_annotation(site, f, st, d):
    """Read @kind, @feature and @related from the comment lines above a module header."""
    tags = collections.defaultdict(list)
    j = st.line0 - 1
    while j >= 0 and f.kinds[j] == 'comment':
        m = ANNOT_RE.match(f.raw[j])
        if m:
            tags[m.group(1)].extend(m.group(2).split())
        j -= 1
    where = '%s:%d: module %s' % (f.path, st.line0 + 1, d.name)
    kinds = tags.get('kind', [])
    if len(kinds) != 1 or kinds[0] not in KIND_TEXT or kinds[0] == 'view':
        site.annot_errors.append('%s: needs exactly one @kind from %s' % (where, ' '.join(KIND_NAMES)))
    else:
        d.role = kinds[0]
    feats = tags.get('feature', ['core'])
    if len(feats) != 1 or feats[0] not in FEATURE_RANK:
        site.annot_errors.append('%s: @feature must be one of %s' % (where, ' '.join(FEATURES)))
    else:
        d.feature = feats[0]
    d.related = sorted(set(tags.get('related', [])))


def add_def(site, f, kind, name, tok, st, module=None, owner=None):
    d = Def(kind, name, f.stem, module if module is not None else st.module, tok, st, owner)
    tok.is_def = d
    st.defs.append(d)
    site.defs.append(d)
    site.by_name[name].append(d)
    if kind in ('module', 'view'):
        site.module_defs[name] = d
        f.mod_defs.append(d)
    return d


def parse_stmt(site, f, st):
    toks = st.toks
    kw = st.kw
    texts = [t.text for t in toks]
    inview = st.module_kind == 'view'
    if kw in IMPORT_KW:
        for t in toks[1:]:
            if t.text in ('.', '{', '}', ','):
                continue
            if st.module:
                site.imports[st.module].append(t.text)
        return
    if kw in SUBSORT_KW:
        if inview:
            return
        groups = [[]]
        for t in toks[1:]:
            if t.text == '.':
                break
            if t.text == '<':
                groups.append([])
            else:
                groups[-1].append(t.text)
        for lo, hi in zip(groups, groups[1:]):
            for a in lo:
                for b in hi:
                    site.supers[a].add(b)
                    site.subs[b].add(a)
        return
    if inview:
        return
    if kw in SORT_KW:
        for t in toks[1:]:
            if t.text != '.':
                add_def(site, f, 'sort', t.text, t, st)
    elif kw == 'class':
        cdef = None
        if len(toks) > 1 and toks[1].text != '.':
            cdef = add_def(site, f, 'class', toks[1].text, toks[1], st)
        if cdef is not None and '|' in texts:
            cur = []
            for t in toks[texts.index('|') + 1:]:
                if t.text in (',', '.'):
                    if cur and cur[0].text not in PUNCT:
                        add_def(site, f, 'attr', cur[0].text, cur[0], st, owner=cdef.name)
                    cur = []
                else:
                    cur.append(t)
    elif kw in OP_KW:
        if ':' in texts:
            colon = texts.index(':')
            for t in toks[1:colon]:
                # mixfix pieces such as `_` or `|` of `{_}|{_}` are not names
                if t.text not in PUNCT and t.text != '.' and t.text != '_' and re.search(r'[A-Za-z0-9_]', t.text):
                    add_def(site, f, 'op', t.text, t, st)
    elif kw in ('var', 'vars'):
        if ':' in texts:
            colon = texts.index(':')
            sort_tok = toks[colon + 1] if colon + 1 < len(toks) else None
            sort_text = sort_tok.text if sort_tok is not None and sort_tok.text != '.' else None
            for t in toks[1:colon]:
                if t.text in PUNCT or t.text == '.':
                    continue
                site.var_table[(st.module, t.text)] = sort_text
                t.vardecl = (sort_text, st.module)
                d = Def('var', t.text, f.stem, st.module, t, st)
                t.is_def = d
                site.vardefs.append(d)
                site.var_decl[(st.module, t.text)] = d
    elif kw in RULE_KW:
        if len(toks) > 1 and toks[1].in_attr and toks[1].text.startswith('['):
            label = toks[1].text.strip('[]')
            if label:
                add_def(site, f, 'rl' if kw in ('rl', 'crl') else 'eq', label, toks[1], st)


def attach_docs(f):
    for st in f.stmts:
        if st.kw not in DOC_KW or st.line0 is None or not st.toks:
            continue
        if f.line_toks[st.line0][0] is not st.toks[0]:
            continue
        j = st.line0 - 1
        while j >= 0 and f.kinds[j] == 'comment':
            j -= 1
        start = j + 1
        run = list(range(start, st.line0))
        if run and prose_of_run(f, run):
            st.doc_start = start
            st.doc_lines = run
            st.doc_plain = plain_text(f, run)


def doc_group_end(f, st):
    """Last line of the group that a doc comment documents: the statement, then the code lines
    after it up to a blank line, a comment or the end of the module. A module header is boxed alone."""
    end = st.line1
    if st.kw in MOD_KW:
        return end
    k = end + 1
    while k < len(f.raw) and f.kinds[k] == 'code' and f.line_toks[k]:
        if f.line_toks[k][0].text in END_KW or f.line_toks[k][0].text in MOD_KW:
            break
        end = k
        k += 1
    return end


def first_summary(f):
    i, n = 0, len(f.raw)
    while i < n:
        if f.kinds[i] == 'comment':
            j = i
            while j < n and f.kinds[j] == 'comment':
                j += 1
            if prose_of_run(f, range(i, j)):
                text = plain_text(f, range(i, j))
                return text.split('\n\n')[0]
            i = j
        else:
            i += 1
    return ''


def first_sentence(text, limit=200):
    if not text:
        return ''
    t = ' '.join(text.split())
    m = re.search(r'(?<=[.!?])\s', t)
    s = t[:m.start() + 1] if m else t
    return s if len(s) <= limit else s[:limit - 3].rstrip() + '...'


def stmt_code(st):
    """The source lines of a statement (at most 12, each cut to 160 characters), dedented."""
    lines = [c.rstrip()[:160] for c in st.f.codes[st.line0:st.line1 + 1] if c.strip()]
    if len(lines) > 12:
        lines = lines[:12] + ['...']
    return textwrap.dedent('\n'.join(lines))


def finalize_defs(site):
    count = collections.Counter(ANCHOR_PREFIX[d.kind] + '-' + slug(d.name) for d in site.defs)
    taken = set()
    for d in site.defs:
        base = ANCHOR_PREFIX[d.kind] + '-' + slug(d.name)
        anchor = base if count[base] == 1 else '%s-%s-%d' % (base, d.stem, d.line)
        n = 1
        while anchor in taken:
            n += 1
            anchor = '%s-%d' % (base if count[base] == 1 else anchor, n)
        taken.add(anchor)
        d.anchor = anchor
        d.page = 'src/%s.html' % d.stem
        st = d.stmt
        if d.kind == 'module':
            d.sig = '%s %s' % (st.kw, d.name)
        elif d.kind == 'view':
            d.sig = 'view %s' % d.name
        elif d.kind == 'sort':
            d.sig = 'sort %s' % d.name
        elif d.kind == 'attr':
            d.sig = 'attribute %s of class %s' % (d.name, d.owner)
        else:
            d.sig = stmt_text(st)
        if st.doc_plain:
            d.doc = st.doc_plain
    for d in site.vardefs:
        d.anchor = 'var-%s-%s-%d' % (slug(d.name), d.stem, d.line)
        d.page = 'src/%s.html' % d.stem
        d.sig = stmt_text(d.stmt)
    for d in site.defs + site.vardefs:
        d.code = stmt_code(d.stmt)
    site.alldefs = site.defs + site.vardefs
    for k, d in enumerate(site.alldefs):
        d.uid = k
    for mod, names in site.imports.items():
        for n in names:
            if n in site.module_defs:
                site.importers[n].add(mod)


# ------------------------------------------------------------- resolution

def pick(site, name, cur_mod, hint, attr_pos=False):
    """Return a Def, a list of candidate Defs (ambiguous), or None.

    Class attributes are candidates only at attribute positions (`| attr :`, `, attr :`).
    """
    cands = [d for d in site.by_name.get(name, ()) if d.kind != 'attr' or attr_pos]
    if not cands:
        return None
    return pick_from(site, cands, cur_mod, hint)


def pick_from(site, cands, cur_mod, hint):
    """Choose among candidate Defs (see pick)."""
    vis = site.visible(cur_mod)
    scope = [d for d in cands if d.kind in ('module', 'view') or d.module in vis]
    pool = scope or cands
    fam = HINT_FAMILY.get(hint)
    if fam:
        sub = [d for d in pool if d.kind in fam]
        if sub:
            pool = sub
    if len(pool) == 1:
        return pool[0]
    local = [d for d in pool if d.module == cur_mod]
    if len(local) == 1:
        return local[0]
    return pool


def order_cands(cands, cur_mod):
    """Candidate Defs with the primary one first: declared in the current module, then load order.
    Links never point at an index page; the primary candidate is the link target."""
    return sorted(cands, key=lambda d: (d.module != cur_mod, d.seq))


def set_def_target(site, t, res, cur_mod, record):
    cands = order_cands(res if isinstance(res, list) else [res], cur_mod)
    d = cands[0]
    t.target = (d.page, d.anchor)
    t.cands = cands
    t.ambiguous = len(cands) > 1
    t.res_def = None if t.ambiguous else d
    if record and not t.ambiguous and d.kind in ('sort', 'class') and cur_mod:
        site.usage[d].add(cur_mod)


def stmt_hint(st, idx):
    if st.kw in IMPORT_KW:
        return 'mod'
    if st.kw == 'view':
        return 'mod' if idx > 1 else None
    if st.kw == 'load':
        return 'file'
    prev = st.toks[idx - 1].text if idx > 0 else None
    if prev in SORT_CONTEXT:
        return 'sort'
    return None


# Maude attribute and metadata words: never names of declarations.
ATTR_WORDS = {'ctor', 'owise', 'otherwise', 'assoc', 'comm', 'id', 'prec', 'gather', 'format',
              'strat', 'memo', 'frozen', 'config', 'object', 'message', 'nonexec', 'label',
              'metadata', 'special', 'iter', 'poly', 'left', 'right', 'print', 'narrowing',
              'concealed'}
IDENT_RE = re.compile(r'[A-Za-z$][A-Za-z0-9_$\-]*')


def resolve_var(site, t, txt, cur_mod, record=True):
    """Link a use of a variable to its declaration (or, failing that, to its sort)."""
    found = site.visible_var(cur_mod, txt) if (cur_mod, txt) not in site.var_table else (
        cur_mod, site.var_table[(cur_mod, txt)])
    if found is None:
        return False
    vmod, sort_text = found
    t.cls = 'var'
    decl = site.var_decl.get((vmod, txt))
    if decl is not None:
        t.target = (decl.page, decl.anchor)
        t.cands = [decl]
    elif sort_text:
        res = pick(site, sort_text, vmod, 'sort')
        if isinstance(res, Def):
            t.target = (res.page, res.anchor)
            t.cands = [res]
    return True


def classify_token(site, t, cur_mod, hint, record, owner=None, attr_pos=False):
    """Assign the class and link target of a non-definition token.

    `owner` is the class named earlier in the same statement (as in a configuration
    `< o : C | attr : v >`); an attribute name then resolves to that class's attribute.
    Names inside attribute brackets (and term brackets such as `[DATA]`) are linked too; the
    attribute words of Maude (ctor, owise, ...) are not.
    """
    if t.is_def is not None:
        return
    txt = t.text
    if t.in_attr or t.cls == 'attr':
        if txt not in ATTR_WORDS and IDENT_RE.fullmatch(txt):
            res = pick(site, txt, cur_mod, None)
            if res is not None:
                set_def_target(site, t, res, cur_mod, record)
            else:
                resolve_var(site, t, txt, cur_mod, record)
        return
    if txt in KEYWORDS and not (t.glue and txt in site.by_name):
        t.cls = 'kw'
        return
    if txt == '.' or OP_RE.fullmatch(txt):
        t.cls = 'op'
        return
    if txt.startswith('"'):
        t.cls = 'str'
        return
    if NUM_RE.fullmatch(txt):
        t.cls = 'num'
        return
    if txt in PUNCT or txt == '_':
        return
    if t.vardecl is not None:
        t.cls = 'var'
        return
    if hint == 'file':
        if txt in site.stems:
            t.cls = 'file'
            t.target = ('src/%s.html' % txt, None)
        return
    if owner is not None:
        for d in site.by_name.get(txt, ()):
            if d.kind == 'attr' and d.owner == owner:
                set_def_target(site, t, d, cur_mod, record)
                return
    heads = site.mix_heads.get(txt)
    if heads:
        res = pick_from(site, heads, cur_mod, hint)
        set_def_target(site, t, res, cur_mod, record)
        return
    if (cur_mod, txt) in site.var_table and resolve_var(site, t, txt, cur_mod, record):
        return
    res = pick(site, txt, cur_mod, hint, attr_pos)
    if res is not None:
        set_def_target(site, t, res, cur_mod, record)
        return
    if resolve_var(site, t, txt, cur_mod, record):
        return
    if txt in BUILTINS:
        t.cls = 'builtin'
        t.title = 'Maude builtin'
        if record:
            site.builtins[txt] += 1
        return
    if record and txt[:1].isalpha():
        site.unresolved[txt] += 1


def classify_file(site, f):
    for st in f.stmts:
        owner = None
        for idx, t in enumerate(st.toks):
            prev = st.toks[idx - 1].text if idx > 0 else None
            nxt = st.toks[idx + 1].text if idx + 1 < len(st.toks) else None
            attr_pos = prev in ('|', ',') and nxt == ':'
            classify_token(site, t, st.module, stmt_hint(st, idx), True,
                           owner if attr_pos else None, attr_pos)
            # a class named earlier in the statement owns the attribute names that follow
            if t.is_def is not None and t.is_def.kind == 'class':
                owner = t.is_def.name
            elif t.res_def is not None and t.res_def.kind == 'class':
                owner = t.res_def.name


def split_glued(t, heads):
    """Spans (start, end) of the token text that become separate tokens.

    A bracket is its own token, as is a leading `.` before a name. `ident[` stays
    whole when `ident[` heads a declared mixfix op (`invkTa[__]`).
    """
    text = t.text
    if len(text) < 2 or text.startswith('"') or '`' in text or t.is_def is not None:
        return [(0, len(text))]
    attr = t.in_attr or t.cls == 'attr'
    cuts = set()
    if not attr and text[0] == '.' and text[1].isalpha():
        cuts.add(1)
    for i, c in enumerate(text):
        if c == '[':
            j = i
            while j > 0 and (text[j - 1].isalnum() or text[j - 1] == '-'):
                j -= 1
            if attr or not (j == 0 and text[:i + 1] in heads):
                cuts.add(i)
            cuts.add(i + 1)
        elif c == ']':
            cuts.add(i)
            cuts.add(i + 1)
    bounds = [0] + sorted(p for p in cuts if 0 < p < len(text)) + [len(text)]
    return [(a, b) for a, b in zip(bounds, bounds[1:]) if b > a]


def split_tok(t, heads):
    """Split token t in place; return the resulting tokens (t itself comes first)."""
    spans = split_glued(t, heads)
    if len(spans) == 1:
        return [t]
    text, s0 = t.text, t.s
    glue = t.glue
    out = []
    for k, (a, b) in enumerate(spans):
        if k == 0:
            t.text, t.e = text[a:b], s0 + b
            t.glue = False
            out.append(t)
        else:
            p = Tok(text[a:b], t.line, s0 + a, s0 + b)
            p.in_attr, p.cls = t.in_attr, t.cls
            p.glue = glue if k == len(spans) - 1 else False
            out.append(p)
    return out


def collect_mix_heads(site):
    for d in site.defs:
        if d.kind == 'op':
            m = MIX_HEAD_RE.match(d.name)
            if m:
                site.mix_heads.setdefault(m.group(0), []).append(d)


def split_file(site, f):
    repl = {}
    for st in f.stmts:
        if st.kw in DECL_KW:
            continue
        new = []
        for t in st.toks:
            pieces = split_tok(t, site.mix_heads)
            if len(pieces) > 1:
                repl[id(t)] = pieces
                for p in pieces:
                    p.stmt = st
            new.extend(pieces)
        st.toks = new
    if repl:
        for i, line in enumerate(f.line_toks):
            f.line_toks[i] = [p for t in line for p in repl.get(id(t), [t])]


def compute_relations(site):
    """Module roles, the undirected @related links, and the data declarations that the rules of
    each behavior or panic module use (a declaration is used when a rule names it)."""
    mods = site.module_defs
    site.roles = {n: d.role for n, d in mods.items() if d.role}
    for n, d in mods.items():
        for r in d.related:
            if r not in mods:
                site.annot_errors.append('module %s: @related names an unknown module %s' % (n, r))
            elif r != n:
                site.related[n].add(r)
                site.related[r].add(n)
    for f in site.files:
        for st in f.stmts:
            m = st.module
            if st.kw not in RULE_KW or m is None or site.roles.get(m) not in RULE_KINDS:
                continue
            for t in st.toks:
                cands = t.cands
                if t.ambiguous:
                    cands = [c for c in cands if c.module in site.visible(m)]
                if len(cands) != 1:
                    continue
                c = cands[0]
                if (c.kind in ('sort', 'class', 'op', 'attr') and c.module != m
                        and site.roles.get(c.module) in DATA_KINDS):
                    site.uses[m].add(c.module)
    for m, used in site.uses.items():
        for x in used:
            site.used_by[x].add(m)


def link_stats(site):
    st = site.stats
    for f in site.files:
        for t in (x for line in f.line_toks for x in line):
            if t.target:
                st['links'] += 1
                if t.ambiguous:
                    st['ambiguous'] += 1
            if t.cls == 'var':
                st['var_uses'] += 1


def href(cur_dir, target):
    page, anchor = target
    rel = posixpath.relpath(page, cur_dir) if cur_dir else page
    return rel + ('#' + anchor if anchor else '')


def link_attrs(cands):
    """data attributes for hover and go-to: the primary definition, then all candidates."""
    if not cands:
        return ''
    s = ' data-d="%d"' % cands[0].uid
    if len(cands) > 1:
        s += ' data-c="%s"' % ','.join(str(d.uid) for d in cands)
    return s


def link_html(t, cur_dir, txt):
    if t.cls == 'var':
        cls = 'var'
    else:
        cls = 'id amb' if t.ambiguous else 'id'
    return '<a class="%s" href="%s"%s>%s</a>' % (cls, esc(href(cur_dir, t.target)), link_attrs(t.cands), txt)


def render_tok(t, cur_dir):
    txt = esc(t.text)
    if t.is_def is not None:
        d = t.is_def
        cls = 'var' if d.kind == 'var' else 'def'
        if t.in_attr:
            pre = '[' if t.text.startswith('[') else ''
            post = ']' if pre and t.text.endswith(']') else ''
            inner = t.text[len(pre):len(t.text) - len(post)]
            return '<span class="attr">%s<span class="%s" id="%s">%s</span>%s</span>' % (
                esc(pre), cls, d.anchor, esc(inner), esc(post))
        return '<span class="%s" id="%s">%s</span>' % (cls, d.anchor, txt)
    if t.in_attr or t.cls == 'attr':
        if t.target:
            return '<span class="attr">%s</span>' % link_html(t, cur_dir, txt)
        return '<span class="attr">%s</span>' % txt
    if t.cls == 'kw':
        return '<span class="kw">%s</span>' % txt
    if t.cls == 'op':
        return '<span class="op">%s</span>' % txt
    if t.cls == 'num':
        return '<span class="num">%s</span>' % txt
    if t.cls == 'str':
        return '<span class="str">%s</span>' % txt
    if t.cls == 'builtin':
        return '<span class="builtin">%s</span>' % txt
    if t.target:
        return link_html(t, cur_dir, txt)
    if t.cls == 'var':
        return '<span class="var">%s</span>' % txt
    return txt


def code_row(f, i, cur_dir):
    code = f.codes[i]
    com = f.comments[i]
    parts, pos = [], 0
    for t in f.line_toks[i]:
        if t.s < pos:
            continue
        parts.append(esc(code[pos:t.s]))
        parts.append(render_tok(t, cur_dir))
        pos = t.e
    parts.append(esc(code[pos:]))
    if com:
        parts.append('<span class="cmt">%s</span>' % esc(com))
    n = i + 1
    return ('<div class="ln" id="L%d"><a class="no" href="#L%d">%d</a>'
            '<span class="src">%s</span></div>\n') % (n, n, n, ''.join(parts))


def code_html(site, cur_dir, s, cur_mod):
    """Highlighted and linked HTML for one line of Maude text (no line wrapper)."""
    toks = [t for m in TOKEN_RE.finditer(s) for t in [Tok(m.group(0), 0, m.start(), m.end())]]
    mark_attrs(toks)
    pieces = []
    for t in toks:
        pieces.extend(split_tok(t, site.mix_heads))
    parts, pos = [], 0
    for t in pieces:
        classify_token(site, t, cur_mod, None, False)
        parts.append(esc(s[pos:t.s]))
        parts.append(render_tok(t, cur_dir))
        pos = t.e
    parts.append(esc(s[pos:]))
    return ''.join(parts)


def inline_code(site, cur_dir, s, cur_mod):
    return '<code>%s</code>' % code_html(site, cur_dir, s, cur_mod)


def prose_html(site, cur_dir, text, cur_mod):
    out = []
    pos = 0
    for m in re.finditer(r'`([^`]+)`', text):
        out.append(esc(text[pos:m.start()]))
        out.append(inline_code(site, cur_dir, m.group(1), cur_mod))
        pos = m.end()
    out.append(esc(text[pos:]))
    return ''.join(out)


def trim_blank_rows(f, idxs):
    idxs = list(idxs)
    while idxs and not comment_body(f.raw[idxs[0]]).strip():
        idxs.pop(0)
    while idxs and not comment_body(f.raw[idxs[-1]]).strip():
        idxs.pop()
    return idxs


def cmt_block_html(site, cur_dir, f, idxs, cur_mod):
    """A run of commented-out code: one `--- ` level stripped, highlighted, in a labelled box."""
    rows = []
    for i in trim_blank_rows(f, idxs):
        body = comment_body(f.raw[i]).rstrip()
        if not body.strip():
            inner = ''
        elif body.lstrip().startswith(('---', '***')) or not is_codelike(body):
            inner = '<span class="cmt">%s</span>' % esc(body)
        else:
            inner = code_html(site, cur_dir, body, cur_mod)
        rows.append('<div class="ln"><span class="no">%d</span><span class="src">%s</span></div>\n'
                    % (i + 1, inner))
    return ('<div class="cmtbox"><div class="cmthead">Commented-out code (not loaded)</div>'
            '<div class="cmtbody">%s</div></div>\n') % ''.join(rows)


def render_comment(site, cur_dir, f, idxs, cur_mod, drop_first=False):
    """Prose paragraphs and example code lines of a comment run that is not commented-out code."""
    out, para, ex = [], [], []
    skip = [drop_first]

    def flush_para():
        if para:
            if skip[0]:
                skip[0] = False
            else:
                out.append('<p>%s</p>\n' % prose_html(site, cur_dir, ' '.join(para), cur_mod))
            del para[:]

    def flush_ex():
        if ex:
            out.append('<div class="exlines">%s</div>\n' % ''.join(ex))
            del ex[:]

    for i in idxs:
        raw = f.raw[i]
        body = comment_body(raw)
        k = comment_kind(raw, body)
        if k == 'tag':
            flush_para()
            flush_ex()
            m = TAG_RE.match(raw)
            arg = m.group(2).strip()
            out.append('<div class="dmc"><span class="tagname">%s:</span> %s</div>\n'
                       % (esc(m.group(1)), esc(arg)))
        elif k in ('annot', 'banner', 'blank'):
            flush_para()
            flush_ex()
        elif k == 'code':
            flush_para()
            ex.append('<div class="ln"><span class="src">%s</span></div>'
                      % code_html(site, cur_dir, body.rstrip(), cur_mod))
        else:
            flush_ex()
            para.append(body.strip())
    flush_para()
    flush_ex()
    return ''.join(out)


def comment_html(site, cur_dir, f, idxs, cur_mod, drop_first=False):
    """HTML for a free comment run: prose, or commented-out code."""
    if run_is_code(f, idxs):
        return cmt_block_html(site, cur_dir, f, idxs, cur_mod)
    body = render_comment(site, cur_dir, f, idxs, cur_mod, drop_first)
    return '<div class="free">%s</div>\n' % body if body else ''


def uses_html(site, d, cur_dir):
    mods = sorted(site.usage.get(d, ()))
    items = []
    for m in mods:
        md = site.module_defs.get(m)
        if md is not None:
            items.append('<li><a href="%s">%s</a></li>' % (esc(href(cur_dir, (md.page, md.anchor))), esc(m)))
        else:
            items.append('<li>%s</li>' % esc(m))
    return ('<details class="uses"><summary>%s used in %d module%s</summary><ul>%s</ul></details>\n'
            % (esc(d.name), len(mods), '' if len(mods) == 1 else 's', ''.join(items)))


def module_name_link(site, cur_dir, name):
    md = site.module_defs.get(name)
    if md is None:
        return esc(name)
    return '<a href="%s">%s</a>' % (esc(href(cur_dir, (md.page, md.anchor))), esc(name))


def kind_badge(kind, href_=None):
    """A colored badge for a module kind (views have no kind and get the view badge)."""
    k = kind or 'view'
    title = esc(KIND_TEXT.get(k, ''))
    if href_:
        return '<a class="kbadge k-%s" href="%s" title="%s">%s</a>' % (k, esc(href_), title, esc(k))
    return '<span class="kbadge k-%s" title="%s">%s</span>' % (k, title, esc(k))


def module_tags_html(site, cur_dir, d, kw):
    """Keyword, kind badge (linked to the module's entry on the modules page) and feature tag."""
    parts = ['<span class="kindtag">%s</span>' % esc(kw)]
    if d is not None and d.role:
        parts.append(kind_badge(d.role, href(cur_dir, ('modules.html', d.anchor))))
    if d is not None and d.feature:
        parts.append('<span class="ftag" title="feature group">%s</span>' % esc(feature_label(d.feature)))
    return ' '.join(parts)


def related_groups(site, name):
    """(label, module names) pairs of the Related modules panel of one module; empty groups dropped."""
    d = site.module_defs[name]
    groups = [('Imports', [x for x in dict.fromkeys(site.imports.get(name, []))
                           if x in site.module_defs and x != name]),
              ('Imported by', sorted(site.importers.get(name, ())))]
    if d.role in RULE_KINDS:
        groups.append(('Operates on', sorted(site.uses.get(name, ()))))
    if d.role in DATA_KINDS:
        groups.append(('Used by rules in', sorted(site.used_by.get(name, ()))))
    groups.append(('Related', sorted(site.related.get(name, ()))))
    if d.feature and d.feature != 'core' and d.role:
        groups.append(('Same feature', sorted(
            x for x, e in site.module_defs.items()
            if e.feature == d.feature and e.role and e.role != d.role and x != name)))
    return [(lbl, xs) for lbl, xs in groups if xs]


def related_html(site, cur_dir, name):
    """The collapsible Related modules panel under a module heading."""
    groups = related_groups(site, name)
    total = sum(len(xs) for _, xs in groups)
    if not total:
        return ''
    rows = []
    for lbl, xs in groups:
        items = ['<span class="ri">%s <a href="%s">%s</a></span>' % (
                     kind_badge(site.module_defs[x].role),
                     esc(href(cur_dir, (site.module_defs[x].page, site.module_defs[x].anchor))), esc(x))
                 for x in xs]
        more = ''
        if len(items) > RELATED_CAP:
            more = ('<details class="more"><summary>+%d more</summary><div class="ritems">%s</div></details>'
                    % (len(items) - RELATED_CAP, ''.join(items[RELATED_CAP:])))
            items = items[:RELATED_CAP]
        rows.append('<div class="relgrp"><div class="rlbl">%s</div><div class="ritems">%s</div>%s</div>\n'
                    % (esc(lbl), ''.join(items), more))
    return ('<details class="rel"><summary>Related modules (%d)</summary>\n%s</details>\n'
            % (total, ''.join(rows)))


def module_header_html(site, cur_dir, st):
    name = st.toks[1].text
    d = site.module_defs.get(name)
    parts = ['<section class="module">\n<h2 class="modtitle">%s %s</h2>\n'
             % (esc(name), module_tags_html(site, cur_dir, d, st.kw))]
    if st.kw == 'view':
        vt = site.view_targets.get(name, [])
        parts.append('<p class="meta">maps from %s</p>\n' % ' '.join(module_name_link(site, cur_dir, x) for x in vt[:1]))
        if len(vt) > 1:
            parts.append('<p class="meta">to %s</p>\n' % ' '.join(module_name_link(site, cur_dir, x) for x in vt[1:]))
    parts.append(related_html(site, cur_dir, name))
    return ''.join(parts)


# ------------------------------------------------------------------ examples

EX_MAX = 3                  # examples shown per rule or op: the first open, the rest under "more"
EX_BREAK_WIDTH = 80         # terms up to this length are shown on one line
EX_OBJ_RE = re.compile(r'< [^\s<>|]+ : ')


def load_examples(path):
    """The entries of docs/examples.json (tools/gen-examples.py). A missing file is a warning and
    no examples are shown; a file that is not a JSON object is an error."""
    if not os.path.isfile(path):
        print('[gen-docs] warning: examples file not found: %s (no examples shown)' % path,
              file=sys.stderr)
        return None
    try:
        with open(path, encoding='utf-8') as fh:
            data = json.load(fh)
    except ValueError as e:
        raise DocError('examples file is not valid JSON: %s: %s' % (path, e))
    if not isinstance(data, dict):
        raise DocError('examples file: the top level must be an object: %s' % path)
    return {'rules': data.get('rules') or {}, 'ops': data.get('ops') or {}}


def _stmt_line(d):
    """1-based line of the keyword that starts the statement of a definition."""
    if d.stmt is not None and d.stmt.line0 is not None:
        return d.stmt.line0 + 1
    return d.line


def _stem(path):
    return os.path.splitext(os.path.basename(path or ''))[0]


def _match_rule(site, label, e):
    """The rl/crl definition of an entry: by file and line, else by label when it is unique."""
    cands = [d for d in site.by_name.get(label, ()) if d.kind == 'rl']
    exact = [d for d in cands if d.stem == _stem(e.get('file')) and _stmt_line(d) == e.get('line')]
    if exact:
        return exact[0]
    return cands[0] if len(cands) == 1 else None


def _match_op(site, key, e):
    """The op declaration of an entry: by file and line, else by module and name."""
    module, _, name = key.partition('/')
    # mixfix names such as _() are split into pieces by the scanner, so the statement decides
    stem, line = _stem(e.get('file')), e.get('line')
    exact = [d for d in site.defs if d.kind == 'op' and d.stem == stem and
             (_stmt_line(d) == line or d.line == line)]
    for d in exact:
        if d.name == name:
            return d
    if exact:
        return exact[0]
    same = [d for d in site.by_name.get(name, ()) if d.kind == 'op' and d.module == module]
    return same[0] if len(same) == 1 else None


def attach_examples(site, examples):
    """Attach each entry of docs/examples.json to its definition (Def.examples). Entries that
    match no definition are kept in site.ex_missing; counts are in site.ex_stats."""
    site.ex_stats = collections.Counter()
    site.ex_missing = []
    if examples is None:
        return
    for key, entries in sorted(examples['rules'].items()):
        label = key.partition('@')[0]
        for e in entries:
            if e.get('verified') is False:
                continue
            site.ex_stats['rules_n'] += 1
            d = _match_rule(site, label, e)
            if d is None:
                site.ex_missing.append('rule %s (%s:%s)' % (key, e.get('file'), e.get('line')))
                continue
            d.examples.append(e)
            site.ex_stats['rules_ok'] += 1
    for key, entries in sorted(examples['ops'].items()):
        for e in entries:
            if e.get('verified') is False:
                continue
            site.ex_stats['ops_n'] += 1
            d = _match_op(site, key, e)
            if d is None:
                site.ex_missing.append('op %s (%s:%s)' % (key, e.get('file'), e.get('line')))
                continue
            d.examples.append(e)
            site.ex_stats['ops_ok'] += 1


def report_examples(site):
    s = site.ex_stats
    print('[gen-docs] examples attached: rules %d of %d entries, ops %d of %d entries' % (
        s['rules_ok'], s['rules_n'], s['ops_ok'], s['ops_n']))
    if site.ex_missing:
        print('[gen-docs] warning: %d example entries matched no declaration (first: %s)' % (
            len(site.ex_missing), '; '.join(site.ex_missing[:10])), file=sys.stderr)


def pretty_break(term):
    """The term with line breaks before each top-level object and message, and after each
    top-level comma of an object's attribute list (continuation lines indented). Brackets are
    tracked, so commas inside an attribute value stay on their line."""
    if len(term) <= EX_BREAK_WIDTH:
        return term
    stack = []        # 'p' for an open bracket, 'o' for an open object
    cuts = []         # (position, indent) in increasing position order
    i, n = 0, len(term)
    while i < n:
        c = term[i]
        if c == '"':
            j = i + 1
            while j < n and term[j] != '"':
                j += 2 if term[j] == '\\' else 1
            i = j + 1
            continue
        if c in '([{':
            stack.append('p')
        elif c in ')]}':
            if stack and stack[-1] == 'p':
                stack.pop()
        elif c == '<' and 'p' not in stack and EX_OBJ_RE.match(term, i):
            if i > 0:
                cuts.append((i, 0))
            stack.append('o')
        elif (c == '>' and stack and stack[-1] == 'o' and term[i - 1] == ' '
              and (i + 1 == n or term[i + 1] == ' ')):
            stack.pop()
        elif c == ',' and stack and stack[-1] == 'o':
            cuts.append((i + 1, 4))
        elif c == 'm' and not stack and term.startswith('msg ', i) and i > 0 and term[i - 1] == ' ':
            cuts.append((i, 0))
        i += 1
    if not cuts:
        return term
    out, pos = [], 0
    for at, indent in cuts:
        out.append(term[pos:at].rstrip(' '))
        out.append('\n' + ' ' * indent)
        pos = at
        while pos < n and term[pos] == ' ':
            pos += 1
    out.append(term[pos:])
    return ''.join(out)


def term_block(site, text, module, prefix=''):
    """A highlighted, linked term in a scrolling block, one div per line. prefix is HTML put
    before the first line."""
    html_text = code_html(site, 'src', pretty_break(text or ''), module)
    lines = html_text.split('\n')
    lines[0] = prefix + lines[0]
    return '<div class="exterm">%s</div>' % ''.join('<div class="exl">%s</div>' % ln for ln in lines)


def example_title(d):
    """The one-line summary of a definition's example block: the op or rule it belongs to."""
    return 'Example: [%s]' % d.name if d.kind == 'rl' else 'Example: %s' % d.name


def example_html(site, d):
    """The collapsed example block of a rule or op definition."""
    items = []
    for e in d.examples[:EX_MAX]:
        if d.kind == 'rl':
            body = ('<div class="exlab">Before</div>' + term_block(site, e.get('before'), e.get('module'))
                    + '<div class="exarr">&rarr; [%s]</div>' % esc(d.name)
                    + '<div class="exlab">After</div>' + term_block(site, e.get('after'), e.get('module')))
        else:
            body = (term_block(site, e.get('input'), e.get('module'),
                               prefix='<span class="kw">red</span> ')
                    + '<div class="exarr">&rarr;</div>' + term_block(site, e.get('output'), e.get('module')))
        body += '<div class="exsrc">%s</div>' % esc(e.get('source') or '')
        items.append('<div class="exitem">%s</div>' % body)
    html_out = '<details class="example"><summary>%s</summary>%s' % (esc(example_title(d)), items[0])
    if len(items) > 1:
        html_out += '<details class="exmore"><summary>more (%d)</summary>%s</details>' % (
            len(items) - 1, ''.join(items[1:]))
    return html_out + '</details>\n'


def ex_mark(d):
    if not d.examples:
        return ''
    return ' <span class="exmark" title="has a verified example on its source page">ex</span>'


def example_line(f, st, d):
    """The line after which the examples of definition d (statement st) are shown. A rule's
    examples follow its statement. An op's examples follow the whole group it belongs to (see
    doc_group_end), so they never split the op from its equations."""
    if d.kind == 'rl':
        return st.line1
    return doc_group_end(f, st)


def render_file(site, f):
    cur_dir = 'src'
    out = []
    starts = {}
    ends = {}
    uses_at = collections.defaultdict(list)
    doc_at = {}
    close_at = collections.Counter()    # line -> number of doc boxes to close after it
    summary_run = first_prose_run(f)
    for st in f.stmts:
        if st.kw in MOD_KW and st.line0 not in starts and st.toks[0] is f.line_toks[st.line0][0]:
            starts[st.line0] = st
        if st.kw in END_KW:
            ends[st.line0] = st
        if st.kw in SORT_KW and st.module_kind != 'view' and st.defs:
            uses_at[st.line1].append(st)
        if st.doc_start is not None:
            doc_at.setdefault(st.line0, st)
    ex_at = collections.defaultdict(list)     # line after which example blocks are shown
    n_examples = 0
    for st in f.stmts:
        for d in st.defs:
            if d.examples and st.line1 is not None:
                ex_at[example_line(f, st, d)].append(example_html(site, d))
                n_examples += 1
    cur_mod = None
    header_done = set()
    i, n = 0, len(f.raw)
    while i < n:
        kind = f.kinds[i]
        if kind == 'comment':
            j = i
            while j < n and f.kinds[j] == 'comment':
                j += 1
            if run_is_code(f, range(i, j)):
                end = commented_region_end(f, j)
                out.append(cmt_block_html(site, cur_dir, f, range(i, end), cur_mod))
                i = end
                continue
            st = doc_at.get(j)
            attached = st is not None and st.doc_start == i
            k = None if attached else stmt_index(f, range(i, j))
            if k is not None:
                # prose lines, then commented-out code from the first statement line on
                if k > 0:
                    out.append(comment_html(site, cur_dir, f, range(i, i + k), cur_mod))
                out.append(cmt_block_html(site, cur_dir, f, range(i + k, j), cur_mod))
                i = j
                continue
            ctx = cur_mod
            # the first paragraph is already shown as the page's lead, so it is not repeated
            drop = (i == summary_run)
            if attached:
                # a doc comment boxes the statement below it and the lines that follow it
                ctx = st.module
                if st.kw in MOD_KW and st.line0 in starts:
                    out.append(module_header_html(site, cur_dir, st))
                    cur_mod = st.toks[1].text
                    ctx = cur_mod
                    header_done.add(st.line0)
                body = render_comment(site, cur_dir, f, range(i, j), ctx, drop_first=drop)
                if body:
                    out.append('<div class="unit"><div class="unit-doc">%s</div>' % body)
                    close_at[doc_group_end(f, st)] += 1
            else:
                out.append(comment_html(site, cur_dir, f, range(i, j), ctx, drop_first=drop))
            i = j
            continue
        if kind == 'code':
            if i in starts and i not in header_done:
                out.append(module_header_html(site, cur_dir, starts[i]))
                cur_mod = starts[i].toks[1].text
            out.append(code_row(f, i, cur_dir))
            out.extend(ex_at.get(i, ()))
            if i in ends:
                out.append('</section>\n')
                cur_mod = None
            for st in uses_at.get(i, []):
                for d in st.defs:
                    if d.kind == 'sort':
                        out.append(uses_html(site, d, cur_dir))
            for _ in range(close_at.pop(i, 0)):
                out.append('</div>\n')
        i += 1

    sidebar = ['<h3>Modules in this file</h3>\n<ul>']
    for d in f.mod_defs:
        sidebar.append('<li><a href="#%s">%s</a> %s</li>'
                       % (esc(d.anchor), esc(d.name), kind_badge(d.role)))
    sidebar.append('</ul>\n<h3>Files</h3>\n<ul>')
    for g in site.files:
        cls = ' class="here"' if g.stem == f.stem else ''
        sidebar.append('<li><a%s href="%s.html">%s</a></li>' % (cls, esc(g.stem), esc(g.stem)))
    sidebar.append('</ul>\n<h3>Index</h3>\n<ul>')
    for name, label in INDEX_LINKS:
        sidebar.append('<li><a href="../%s">%s</a></li>' % (name, label))
    sidebar.append('</ul>')
    head = '<h1>%s.maude</h1>\n' % esc(f.stem)
    if f.summary:
        head += '<p class="lead">%s</p>\n' % esc(f.summary)
    if n_examples:
        head += ('<div class="exctl"><button type="button" class="exbtn">Show all examples</button>'
                 '</div>\n')
    body = head + '<div class="code">\n' + ''.join(out) + '</div>\n'
    return page(site, cur_dir, '%s.maude' % f.stem, body, ''.join(sidebar))


INDEX_LINKS = [('index.html', 'Overview'), ('modules.html', 'Modules and views'),
               ('sorts.html', 'Sorts, classes and attributes'), ('ops.html', 'Operators'),
               ('rules.html', 'Rule and equation labels')]


def page(site, cur_dir, title, body, side=''):
    """Site chrome around body. side is the inner HTML of the left contents column; it is
    shown as a sticky column on wide screens and as a <details> above the body on narrow ones."""
    root = '' if not cur_dir else '../'
    nav = ''.join('<a href="%s%s">%s</a>' % (root, n, esc(lbl)) for n, lbl in INDEX_LINKS[:5])
    if side:
        side_html = ('<nav class="side" aria-label="Contents">%s</nav>\n'
                     '<details class="sidem"><summary>Contents</summary>'
                     '<div class="side sidein">%s</div></details>\n') % (side, side)
        cls = 'wrap'
    else:
        side_html = ''
        cls = 'wrap nosidebar'
    return ('<!doctype html>\n<html lang="en">\n<head>\n<meta charset="utf-8">\n'
            '<meta name="viewport" content="width=device-width, initial-scale=1">\n'
            '<title>%s - TEE Formal Spec</title>\n'
            '<link rel="stylesheet" href="%sstyle.css">\n'
            '<script src="%ssearch-index.js" defer></script>\n'
            '<script src="%sdefs.js" defer></script>\n'
            '<script src="%sapp.js" defer></script>\n</head>\n'
            '<body data-root="%s">\n'
            '<header class="top"><a class="brand" href="%sindex.html">TEE Formal Spec</a>'
            '<nav class="links">%s</nav>'
            '<form class="search" action="%sindex.html" method="get">'
            '<input id="q" name="q" type="search" placeholder="Search names" aria-label="Search names">'
            '</form></header>\n'
            '<div class="%s">%s<main>\n%s</main></div>\n</body>\n</html>\n') % (
        esc(title), root, root, root, root, root, root, nav, root, cls, side_html, body)


def table(headers, rows):
    head = ''.join('<th>%s</th>' % esc(x) for x in headers)
    body = []
    for row in rows:
        rid, cells = row
        idattr = ' id="%s"' % rid if rid else ''
        body.append('<tr%s>%s</tr>' % (idattr, ''.join('<td>%s</td>' % c for c in cells)))
    return '<table class="idx"><thead><tr>%s</tr></thead><tbody>\n%s\n</tbody></table>\n' % (
        head, '\n'.join(body))


def group_rows(defs, key_fn):
    """Rows where the first row of each name carries the nm-/nma- group anchor."""
    seen = set()
    rows = []
    for d in defs:
        gid = key_fn(d)
        rid = None
        if d.name not in seen:
            seen.add(d.name)
            rid = gid
        rows.append((rid, d))
    return rows


def def_link(site, cur_dir, d):
    return '<a href="%s"%s>%s</a>' % (esc(href(cur_dir, (d.page, d.anchor))), link_attrs([d]), esc(d.name))


def file_link(cur_dir, stem):
    return '<a href="%s">%s</a>' % (esc(href(cur_dir, ('src/%s.html' % stem, None))), esc(stem))


def sort_key(d):
    return (d.name.lower(), d.name, d.stem, d.line)


def index_pages(site):
    pages = {}
    root = ''
    pages['modules.html'] = modules_page(site)

    # sorts.html
    sorts = sorted([d for d in site.defs if d.kind in ('sort', 'class')], key=sort_key)
    rows = []
    for rid, d in group_rows(sorts, lambda d: 'nm-' + slug(d.name)):
        sup = ', '.join(sorted(module_sort_link(site, root, d.module, x) for x in site.supers.get(d.name, ())))
        sub = ', '.join(sorted(module_sort_link(site, root, d.module, x) for x in site.subs.get(d.name, ())))
        used = len(set(site.usage.get(d, ())))
        rows.append((rid, [def_link(site, root, d), esc(d.kind), file_link(root, d.stem),
                           esc(first_sentence(d.doc)), sup, sub, str(used)]))
    attrs = sorted([d for d in site.defs if d.kind == 'attr'], key=lambda d: (d.name.lower(), d.name, d.owner or ''))
    arows = []
    for rid, d in group_rows(attrs, lambda d: 'nma-' + slug(d.name)):
        cls = site.by_name.get(d.owner or '', [])
        cls_link = ''.join(def_link(site, root, c) for c in cls[:1]) or esc(d.owner or '')
        arows.append((rid, [def_link(site, root, d), cls_link, file_link(root, d.stem),
                            esc(first_sentence(d.doc))]))
    html_body = (
        '<h1>Sorts, classes and attributes</h1>\n<p class="lead">Sorts and classes with their '
        'supersorts and subsorts (from <code>subsort</code>, <code>subsorts</code>, '
        '<code>subclass</code>), and class attributes. "Used in" counts the modules whose code '
        'mentions the sort.</p>\n<h2>Sorts and classes</h2>\n'
        + table(['Name', 'Kind', 'File', 'Summary', 'Supersorts', 'Subsorts', 'Used in'], rows)
        + '<h2>Class attributes</h2>\n'
        + table(['Attribute', 'Class', 'File', 'Summary'], arows))
    pages['sorts.html'] = ('Sorts, classes and attributes', html_body)

    # ops.html
    ops = sorted([d for d in site.defs if d.kind == 'op'], key=sort_key)
    rows = []
    for rid, d in group_rows(ops, lambda d: 'nm-' + slug(d.name)):
        rows.append((rid, [def_link(site, root, d) + ex_mark(d), '<code>%s</code>' % esc(d.sig),
                           file_link(root, d.stem), module_name_link(site, root, d.module or ''),
                           esc(first_sentence(d.doc))]))
    pages['ops.html'] = (
        'Operators',
        '<h1>Operators</h1>\n<p class="lead">Every <code>op</code>, <code>ops</code> and '
        '<code>msg</code> declared in src/, sorted by name. Overloaded names share one group.</p>\n'
        + table(['Name', 'Declaration', 'File', 'Module', 'Summary'], rows))

    # rules.html
    rules = sorted([d for d in site.defs if d.kind in ('rl', 'eq')], key=sort_key)
    rows = []
    for rid, d in group_rows(rules, lambda d: 'nm-' + slug(d.name)):
        rows.append((rid, [def_link(site, root, d) + ex_mark(d), esc(d.kind), '<code>%s</code>' % esc(d.sig[:200]),
                           file_link(root, d.stem), module_name_link(site, root, d.module or '')]))
    pages['rules.html'] = (
        'Rule and equation labels',
        '<h1>Rule and equation labels</h1>\n<p class="lead">Labeled <code>rl</code>, <code>crl</code>, '
        '<code>eq</code> and <code>ceq</code> statements. Unlabeled equations are not listed.</p>\n'
        + table(['Label', 'Kind', 'Statement', 'File', 'Module'], rows))
    return pages


def modules_page(site):
    """modules.html: the module map (feature rows, kind columns), then the modules by kind and feature."""
    root = ''
    mods = sorted([d for d in site.defs if d.kind == 'module' and d.role], key=sort_key)
    views = sorted([d for d in site.defs if d.kind == 'view'], key=sort_key)
    by = collections.defaultdict(lambda: collections.defaultdict(list))
    for d in mods:
        by[d.role][d.feature].append(d)

    def feature_head(f):
        note = '' if f in FLAG_FEATURES else ' <span class="meta">no feature flag</span>'
        return '%s%s' % (esc(feature_label(f)), note)

    def mod_row(d):
        imps = ', '.join(module_name_link(site, root, x) for x in dict.fromkeys(site.imports.get(d.name, [])))
        imp_by = ', '.join(module_name_link(site, root, m) for m in sorted(site.importers.get(d.name, ())))
        return (d.anchor, [def_link(site, root, d), file_link(root, d.stem),
                           esc(first_sentence(d.doc)), imps, imp_by])

    head = ''.join('<th>%s</th>' % kind_badge(k, '#kind-' + k) for k in KIND_NAMES)
    matrix_rows = []
    for f in FEATURES:
        if not any(by[k].get(f) for k in KIND_NAMES):
            continue
        cells = ''.join('<td>%s</td>' % '<br>'.join(def_link(site, root, d) for d in by[k].get(f, []))
                        for k in KIND_NAMES)
        matrix_rows.append('<tr><th scope="row">%s</th>%s</tr>' % (feature_head(f), cells))
    body = [
        '<h1>Modules and views</h1>\n<p class="lead">Every <code>fmod</code>, <code>mod</code> and '
        '<code>omod</code> in src/, grouped by kind and then by feature, and the views. Each module '
        'lists the modules it imports, the modules that import it, and its related modules.</p>\n',
        '<h2 id="map">Module map</h2>\n<p class="meta">Rows are feature groups, columns are module kinds. '
        'Each cell lists the modules of that feature and kind.</p>\n'
        '<div class="mapwrap"><table class="map"><thead><tr><th>Feature</th>%s</tr></thead>'
        '<tbody>%s</tbody></table></div>\n' % (head, ''.join(matrix_rows)),
    ]
    for k in KIND_NAMES:
        if not by[k]:
            continue
        body.append('<h2 id="kind-%s">%s</h2>\n<p class="lead">%s</p>\n'
                    % (k, kind_badge(k), esc(KIND_TEXT[k])))
        for f in FEATURES:
            if by[k].get(f):
                body.append('<h3>%s</h3>\n' % feature_head(f))
                body.append(table(['Name', 'File', 'Summary', 'Imports', 'Imported by'],
                                  [mod_row(d) for d in by[k][f]]))
    if views:
        vrows = []
        for d in views:
            tg = ' '.join(module_name_link(site, root, x) for x in dict.fromkeys(site.view_targets.get(d.name, [])))
            vrows.append((d.anchor, [def_link(site, root, d), file_link(root, d.stem),
                                     esc(first_sentence(d.doc)), tg]))
        body.append('<h2 id="views">Views</h2>\n<p class="lead">%s</p>\n' % esc(KIND_TEXT['view']))
        body.append(table(['Name', 'File', 'Summary', 'Maps to'], vrows))
    return ('Modules and views', ''.join(body))


def module_sort_link(site, cur_dir, module, name):
    res = pick(site, name, module, 'sort')
    if res is None:
        return esc(name)
    cands = order_cands(res if isinstance(res, list) else [res], module)
    return '<a href="%s"%s>%s</a>' % (esc(href(cur_dir, (cands[0].page, cands[0].anchor))),
                                      link_attrs(cands), esc(name))


MACRO_RE = re.compile(r'\[\[([a-z]+):([^\[\]|]+?)(?:\|([^\[\]]*))?\]\]')

# macro kind -> (definition kinds, index page, group anchor prefix)
MACRO_KINDS = {'mod': ({'module', 'view'}, 'modules.html', 'nm'),
               'sort': ({'sort'}, 'sorts.html', 'nm'),
               'class': ({'class'}, 'sorts.html', 'nm'),
               'op': ({'op'}, 'ops.html', 'nm'),
               'rule': ({'rl', 'eq'}, 'rules.html', 'nm'),
               'attr': ({'attr'}, 'sorts.html', 'nma')}


class DocError(Exception):
    pass


def macro_html(site, kind, name, shown):
    """Link HTML for one cross-reference macro, or None when the target is unknown.

    A name links to its primary definition; hover data lists every candidate, as code links do.
    """
    if kind == 'file':
        if name not in site.stems:
            return None
        target = 'src/%s.html' % name
        attrs = ''
    else:
        kinds, _, _ = MACRO_KINDS[kind]
        cands = [d for d in site.by_name.get(name, ()) if d.kind in kinds]
        if not cands:
            return None
        cands = order_cands(cands, None)
        target = '%s#%s' % (cands[0].page, cands[0].anchor)
        attrs = link_attrs(cands)
    if shown is None:
        return '<a class="id" href="%s"%s>%s</a>' % (esc(target), attrs, esc(name))
    return '<a class="xref" href="%s"%s>%s</a>' % (esc(target), attrs, shown)


def expand_macros(site, text, path):
    """Resolve [[kind:Name]] and [[kind:Name|text]] in the overview fragment.

    Any unknown or malformed macro is an error, reported with its line; nothing is written."""
    errors = []
    out = []
    for lineno, line in enumerate(text.split('\n'), 1):
        def sub(m):
            kind, name, shown = m.group(1), m.group(2).strip(), m.group(3)
            if kind != 'file' and kind not in MACRO_KINDS:
                errors.append('%s:%d: unknown macro kind in %s' % (path, lineno, m.group(0)))
                return m.group(0)
            html_ = macro_html(site, kind, name, shown)
            if html_ is None:
                errors.append('%s:%d: unknown %s target in %s' % (path, lineno, kind, m.group(0)))
                return m.group(0)
            return html_
        new = MACRO_RE.sub(sub, line)
        if '[[' in new:
            errors.append('%s:%d: malformed macro (expected [[kind:Name]] or [[kind:Name|text]])'
                          % (path, lineno))
        out.append(new)
    if errors:
        raise DocError('\n'.join(errors))
    return '\n'.join(out)


def files_table(site):
    cats = collections.OrderedDict((name, []) for name, _ in CATEGORIES)
    cats['Other'] = []
    for f in site.files:
        cats.setdefault(CATEGORY_OF.get(f.stem, 'Other'), []).append(f)
    parts = []
    for cat, files in cats.items():
        if not files:
            continue
        items = ''.join('<tr><td>%s</td><td>%s</td></tr>' % (
            file_link('', f.stem), esc(first_sentence(f.summary, 220))) for f in files)
        parts.append('<h3>%s</h3>\n<table class="idx"><thead><tr><th>File</th><th>Description</th>'
                     '</tr></thead><tbody>%s</tbody></table>\n' % (esc(cat), items))
    return ''.join(parts)


def home_page(site, overview_html):
    body = (
        '<section class="overview">\n%s\n</section>\n'
        '<section><h2>Search</h2>\n<p class="meta">Filter names (modules, sorts, classes, attributes, '
        'operators, labels). Use the box at the top of any page.</p>\n'
        '<input id="index-q" type="search" placeholder="Search names" aria-label="Search names">\n'
        '<ul id="results" class="results"></ul></section>\n'
        '<section><h2>Indexes</h2><ul>%s</ul></section>\n'
        '<section><h2>All files</h2>\n%s</section>\n') % (
        overview_html,
        ''.join('<li><a href="%s">%s</a></li>' % (n, esc(lbl)) for n, lbl in INDEX_LINKS[1:]),
        files_table(site))
    return page(site, '', 'Overview', body)


def first_prose_run(f):
    """Start line of the first comment run that holds prose (the one first_summary reads)."""
    i, n = 0, len(f.raw)
    while i < n:
        if f.kinds[i] == 'comment':
            j = i
            while j < n and f.kinds[j] == 'comment':
                j += 1
            if prose_of_run(f, range(i, j)):
                return i
            i = j
        else:
            i += 1
    return None


CSS = """
:root{--bg:#fbfbfa;--fg:#1d1f21;--muted:#6b7077;--line:#d9dbde;--panel:#f1f2f3;
--kw:#8a2a8f;--op:#7a5600;--num:#0a6b57;--str:#a0401c;--attr:#1f5fa8;--def:#0b3d91;
--var:#0a6b57;--cmt:#8b9096;--link:#1a56b8;--hl:#fff3b0;--arrow:#555;--node:#eef3fb}
@media (prefers-color-scheme: dark){:root:not([data-theme="light"]){--bg:#15171a;--fg:#e4e6e8;
--muted:#9aa0a6;--line:#33373d;--panel:#1d2024;--kw:#d78be0;--op:#e0b34f;--num:#6fd3b3;
--str:#f09a72;--attr:#7fb3f0;--def:#a9c7ff;--var:#6fd3b3;--cmt:#6f757c;--link:#8fbaff;
--hl:#4a4116;--arrow:#aab;--node:#1f2a3c}}
:root[data-theme="dark"]{--bg:#15171a;--fg:#e4e6e8;--muted:#9aa0a6;--line:#33373d;--panel:#1d2024;
--kw:#d78be0;--op:#e0b34f;--num:#6fd3b3;--str:#f09a72;--attr:#7fb3f0;--def:#a9c7ff;
--var:#6fd3b3;--cmt:#6f757c;--link:#8fbaff;--hl:#4a4116;--arrow:#aab;--node:#1f2a3c}
/* Module kind colors (badges): one text color per kind; the tint is derived from it. */
:root{--hdr:52px;--k-constants:#7a5c00;--k-data:#0a6b57;--k-state:#7b3fa0;--k-messages:#a0401c;--k-behavior:#1a56b8;--k-panic:#b3213b;--k-program:#3d6d14;--k-interface:#0b6e85;--k-assembly:#555b63;--k-view:#6b7077}
@media (prefers-color-scheme: dark){:root:not([data-theme="light"]){--k-constants:#e6c462;--k-data:#6fd3b3;--k-state:#d9a8f2;--k-messages:#f2a07c;--k-behavior:#8fbaff;--k-panic:#ff8ea2;--k-program:#a9de7e;--k-interface:#72d4ea;--k-assembly:#b8bec5;--k-view:#9aa0a6}}
:root[data-theme="dark"]{--k-constants:#e6c462;--k-data:#6fd3b3;--k-state:#d9a8f2;--k-messages:#f2a07c;--k-behavior:#8fbaff;--k-panic:#ff8ea2;--k-program:#a9de7e;--k-interface:#72d4ea;--k-assembly:#b8bec5;--k-view:#9aa0a6}
.kbadge{display:inline-block;font:11px/1.5 ui-monospace,SFMono-Regular,Menlo,Consolas,monospace;
padding:0 6px;border-radius:9px;border:1px solid color-mix(in srgb,var(--kc) 55%,transparent);
background:color-mix(in srgb,var(--kc) 14%,transparent);color:var(--kc);vertical-align:middle;
white-space:nowrap;text-decoration:none}
a.kbadge:hover{text-decoration:none;border-color:var(--kc)}
.k-constants{--kc:var(--k-constants)}
.k-data{--kc:var(--k-data)}
.k-state{--kc:var(--k-state)}
.k-messages{--kc:var(--k-messages)}
.k-behavior{--kc:var(--k-behavior)}
.k-panic{--kc:var(--k-panic)}
.k-program{--kc:var(--k-program)}
.k-interface{--kc:var(--k-interface)}
.k-assembly{--kc:var(--k-assembly)}
.k-view{--kc:var(--k-view)}
.ftag{display:inline-block;font:11px/1.5 ui-monospace,SFMono-Regular,Menlo,Consolas,monospace;
color:var(--muted);border:1px solid var(--line);border-radius:4px;padding:0 5px;vertical-align:middle}
body{background:var(--bg);color:var(--fg);margin:0;font:16px/1.55 system-ui,-apple-system,"Segoe UI",
Roboto,"Helvetica Neue",Arial,sans-serif}
a{color:var(--link);text-decoration:none}a:hover{text-decoration:underline}
code,pre,.ln,a.id,.idx code{font-family:ui-monospace,SFMono-Regular,Menlo,Consolas,
"Liberation Mono","DejaVu Sans Mono",monospace}
.top{display:flex;gap:16px;align-items:center;flex-wrap:wrap;padding:10px 16px;border-bottom:1px solid var(--line);
background:var(--panel);position:sticky;top:0;z-index:2;min-height:var(--hdr);box-sizing:border-box}
.top .brand{font-weight:700;color:var(--fg)}
.top .links{display:flex;gap:12px;flex-wrap:wrap;font-size:14px}
.top .search{margin-left:auto}
.top input{font-size:14px;font-family:inherit;padding:4px 8px;border:1px solid var(--line);border-radius:4px;
background:var(--bg);color:var(--fg);min-width:200px}
.wrap{display:grid;grid-template-columns:220px minmax(0,1fr);max-width:1400px;margin:0 auto}
.wrap.nosidebar{grid-template-columns:minmax(0,1fr)}
main{padding:12px 24px 48px;min-width:0}
.side{border-right:1px solid var(--line);padding:10px 12px;font-size:13px;line-height:1.4;position:sticky;
top:var(--hdr);align-self:start;max-height:calc(100vh - var(--hdr));overflow-y:auto;min-width:0}
.sidem{display:none}
.side h3{font-size:12px;text-transform:uppercase;letter-spacing:.06em;color:var(--muted);margin:14px 0 6px}
.side ul{list-style:none;padding:0;margin:0}.side li{margin:2px 0}
.side a.here{font-weight:700;color:var(--fg)}
h1{font-size:26px;margin:8px 0 6px}h2{font-size:20px;margin:28px 0 8px;border-bottom:1px solid var(--line);padding-bottom:4px}
h3{font-size:16px;margin:20px 0 6px}
.lead{color:var(--muted);margin:4px 0 14px}
.meta{color:var(--muted);font-size:14px;margin:4px 0}
.kindtag{font-size:12px;color:var(--muted);font-family:ui-monospace,monospace}
.modtitle{font-family:ui-monospace,SFMono-Regular,Menlo,Consolas,monospace;font-size:18px;margin:26px 0 4px;
border-bottom:2px solid var(--line)}
section.module{margin-top:10px}
/* A doc comment and the statement it documents share one box (.unit); free comments are prose. */
.unit{margin:14px 0 12px;border:1px solid var(--line);border-left:4px solid var(--link);border-radius:6px;padding:0 0 4px}
.unit-doc{background:var(--panel);border-bottom:1px solid var(--line);padding:8px 14px;font-size:15px;
line-height:1.5;color:var(--fg);border-radius:5px 5px 0 0}
.unit-doc p{margin:4px 0}
.free{margin:10px 0;font-size:15px;line-height:1.5;color:var(--fg)}
.free p{margin:6px 0}
.exlines{margin:6px 0;font-size:13px}
.dmc{font:12px ui-monospace,monospace;color:var(--muted);margin:4px 0 2px}
.dmc .tagname{display:inline-block;padding:0 5px;margin-right:4px;border:1px solid var(--line);border-radius:4px;font-size:11px}
/* Commented-out code: a dashed, dimmed box with a label; it is not part of the loaded spec. */
.cmtbox{margin:12px 0;border:1px dashed var(--line);border-radius:6px;background:var(--bg)}
.cmthead{font-size:12px;font-weight:600;letter-spacing:.05em;text-transform:uppercase;color:var(--muted);
padding:5px 12px;border-bottom:1px dashed var(--line)}
.cmtbody{overflow-x:auto;padding:4px 0;font-size:13px;opacity:.78}
.cmtbody .ln{min-height:1.4em}
[id]{scroll-margin-top:calc(var(--hdr) + 16px)}
/* The whole block scrolls horizontally as one unit: lines never wrap and never scroll alone. */
.code{font-size:14px;line-height:1.5;margin-top:4px;overflow-x:auto;padding-bottom:2px}
.ln{display:flex;white-space:pre;min-height:1.5em;width:max-content;min-width:100%}
.ln:target,.ln.target,.ln:has(:target){background:var(--hl)}
.ln .no{flex:0 0 3.5em;text-align:right;padding-right:12px;color:var(--cmt);user-select:none}
.ln .src{flex:0 0 auto}
a.xref{color:var(--link)}
.kw{color:var(--kw);font-weight:600}.op{color:var(--op)}.num{color:var(--num)}.str{color:var(--str)}
.attr{color:var(--attr)}.cmt{color:var(--cmt);font-style:italic}
.def{color:var(--def);font-weight:600}
a.id{color:var(--fg);text-decoration:none;border-bottom:1px dotted var(--muted)}
a.id:hover,a.id:focus{color:var(--link);border-bottom-color:var(--link);text-decoration:none}
a.id.amb{border-bottom-style:dashed}
a.var{color:var(--var);text-decoration:none;border-bottom:1px dotted var(--var)}
span.var{color:var(--var)}
span.builtin{color:var(--muted);font-style:italic}
span.var[id]{font-weight:600}
details.uses{margin:4px 0 10px 3.5em;font-size:13px;color:var(--muted)}
details.uses summary{cursor:pointer}
details.uses ul{columns:3;list-style:none;padding-left:0}
table.idx{border-collapse:collapse;width:100%;font-size:14px;margin:8px 0 18px}
table.idx th,table.idx td{border-bottom:1px solid var(--line);padding:5px 8px;text-align:left;vertical-align:top}
table.idx th{color:var(--muted);font-weight:600}
table.idx tr:target td{background:var(--hl)}
.results{list-style:none;padding:0;font-size:14px}
.results li{padding:3px 0;border-bottom:1px solid var(--line)}
.results .k{color:var(--muted);font-size:12px;margin-left:8px}
#index-q{font-size:15px;font-family:inherit;padding:6px 10px;border:1px solid var(--line);border-radius:4px;
background:var(--bg);color:var(--fg);width:min(480px,100%)}
/* Hover popup for a linked name (filled by app.js from defs.js). */
.tip{position:fixed;z-index:10;box-sizing:border-box;max-width:min(640px,calc(100vw - 16px));
max-height:calc(100vh - 16px);overflow:auto;background:var(--panel);color:var(--fg);
border:1px solid var(--line);border-radius:6px;box-shadow:0 6px 24px rgba(0,0,0,.2);
padding:8px 12px;font-size:13px;line-height:1.4}
.tip[hidden]{display:none}
.tip-item+.tip-item{border-top:1px solid var(--line);margin-top:8px;padding-top:8px}
.tip-note{color:var(--muted);font-size:12px;margin-bottom:6px}
.tip-head{font-family:ui-monospace,SFMono-Regular,Menlo,Consolas,monospace}
.tip-kind{color:var(--muted);font-size:11px;text-transform:uppercase;letter-spacing:.04em}
.tip-name{font-weight:700}
.tip-where{color:var(--muted);font-size:12px}
.tip pre{margin:6px 0;padding:6px 8px;font:12px/1.4 ui-monospace,SFMono-Regular,Menlo,Consolas,monospace;
white-space:pre-wrap;word-break:break-word;background:var(--bg);border-left:3px solid var(--def);border-radius:3px}
.tip p{margin:4px 0}
.tip-rel{color:var(--muted);font-size:12px}
.tip-go{font-size:12px}

/* Narrow screens: the contents column becomes a <details> above the page body. */
@media (max-width:900px){.wrap{grid-template-columns:minmax(0,1fr)}
.side:not(.sidein){display:none}
.sidem{display:block;border-bottom:1px solid var(--line);padding:6px 16px;font-size:14px}
.sidem summary{cursor:pointer;font-weight:600}
.sidein{position:static;max-height:none;border:0;padding:4px 0;font-size:14px}
main{padding:12px 16px 40px}.top .search{margin-left:0}details.uses ul{columns:1}}
/* Related modules panel under a module heading: collapsed, one line per relation. */
details.rel{margin:4px 0 12px;font-size:13px;border:1px solid var(--line);border-radius:5px;padding:0 10px}
details.rel>summary{cursor:pointer;color:var(--muted);padding:5px 0}
.relgrp{display:grid;grid-template-columns:11em minmax(0,1fr);gap:2px 10px;padding:4px 0;border-top:1px dotted var(--line)}
.relgrp details.more{grid-column:2;font-size:12px}
.relgrp details.more>summary{cursor:pointer;color:var(--muted);padding:2px 0}
.rlbl{color:var(--muted);font-size:12px;padding-top:2px}
.ritems{display:flex;flex-wrap:wrap;gap:3px 14px}
.ri{white-space:nowrap}
.ri .kbadge{font-size:10px;padding:0 4px;margin-right:3px}
/* The module map: feature rows, kind columns. */
.mapwrap{overflow-x:auto;margin:8px 0 16px}
table.map{border-collapse:collapse;font-size:12px;width:100%%;min-width:720px}
table.map th,table.map td{border:1px solid var(--line);padding:5px 6px;text-align:left;vertical-align:top}
table.map thead th{font-weight:600}
table.map tbody th{font-weight:600;white-space:nowrap;background:var(--panel)}
table.map td{font-family:ui-monospace,SFMono-Regular,Menlo,Consolas,monospace;line-height:1.5}
table.map td a{font-size:12px}
table.map .meta{font-size:11px;display:block;font-weight:400}
.side .kbadge{font-size:10px;padding:0 4px;margin-top:1px}
.side li{line-height:1.6}
.tip-role{margin-left:4px}
/* Verified examples under rules and ops (docs/examples.json): collapsed until opened. */
details.example{margin:1px 0 4px 3.5em;font-size:12px;line-height:1.4}
details.example>summary{cursor:pointer;color:var(--muted);padding:1px 0;white-space:nowrap;list-style-position:inside}
details.example>summary:hover{color:var(--link)}
details.example[open]{margin:4px 0 10px 3.5em;border:1px solid var(--line);border-radius:5px;padding:0 10px 6px;
background:var(--bg)}
details.example[open]>summary{margin-bottom:2px}
.exitem+.exitem{border-top:1px dotted var(--line);margin-top:6px;padding-top:4px}
.exlab{color:var(--muted);font-size:11px;text-transform:uppercase;letter-spacing:.04em;margin-top:6px}
.exterm{font:12.5px/1.45 ui-monospace,SFMono-Regular,Menlo,Consolas,monospace;white-space:pre;
overflow:auto;max-height:14em;background:var(--panel);border-radius:4px;padding:4px 8px;margin:2px 0}
.exl{min-height:1.45em}
.exarr{font:12.5px ui-monospace,SFMono-Regular,Menlo,Consolas,monospace;color:var(--muted);margin:4px 0}
.exsrc{color:var(--muted);font-size:11px;margin:4px 0 6px}
details.exmore{margin:4px 0 6px}
details.exmore>summary{cursor:pointer;color:var(--muted);font-size:12px;padding:2px 0}
.exctl{margin:6px 0 10px}
.exbtn{font:13px system-ui,-apple-system,sans-serif;color:var(--link);background:var(--bg);
border:1px solid var(--line);border-radius:4px;padding:2px 10px;cursor:pointer}
.exmark{display:inline-block;font:10px/1.5 ui-monospace,SFMono-Regular,Menlo,Consolas,monospace;
color:var(--muted);border:1px solid var(--line);border-radius:3px;padding:0 3px;vertical-align:middle}
"""

APP_JS = """(function () {
  'use strict';
  var root = document.body.getAttribute('data-root') || '';

  // The sticky header height is published as --hdr, which sets the scroll margin of anchors.
  function setHdr() {
    var h = document.querySelector('header.top');
    if (h) document.documentElement.style.setProperty('--hdr', h.offsetHeight + 'px');
  }
  setHdr();
  window.addEventListener('resize', setHdr);

  function markLine() {
    var h = location.hash;
    if (!/^#L\\d+$/.test(h)) return;
    document.querySelectorAll('.ln.target').forEach(function (e) { e.classList.remove('target'); });
    var el = document.getElementById(h.slice(1));
    if (el) el.classList.add('target');
  }
  window.addEventListener('hashchange', markLine);
  markLine();

  // Name search on the overview page.
  (function () {
    var box = document.getElementById('index-q');
    var list = document.getElementById('results');
    if (!box || !list || !window.TEE_DOCS_INDEX) return;
    function show(q) {
      list.innerHTML = '';
      q = q.trim().toLowerCase();
      if (!q) return;
      var n = 0;
      for (var i = 0; i < window.TEE_DOCS_INDEX.length && n < 200; i++) {
        var e = window.TEE_DOCS_INDEX[i];
        if (e[0].toLowerCase().indexOf(q) < 0) continue;
        n++;
        var li = document.createElement('li');
        var a = document.createElement('a');
        a.href = root + e[2];
        a.textContent = e[0];
        li.appendChild(a);
        var k = document.createElement('span');
        k.className = 'k';
        k.textContent = e[1] + ' ' + e[3];
        li.appendChild(k);
        list.appendChild(li);
      }
    }
    box.addEventListener('input', function () { show(box.value); });
    var params = new URLSearchParams(location.search);
    if (params.get('q')) { box.value = params.get('q'); show(box.value); }
  })();

  // Hover popup for linked names: the definition(s) from defs.js, shown at once.
  (function () {
    var defs = window.TEE_DEFS || [];
    var tip = document.createElement('div');
    tip.className = 'tip';
    tip.id = 'tip';
    tip.setAttribute('role', 'tooltip');
    tip.hidden = true;
    document.body.appendChild(tip);
    var cur = null, showT = 0, hideT = 0;

    function clear() { clearTimeout(showT); clearTimeout(hideT); }
    function hide() { clear(); cur = null; tip.hidden = true; }
    function hideSoon() { clearTimeout(showT); clearTimeout(hideT); hideT = setTimeout(hide, 180); }
    function el(tag, cls, text) {
      var e = document.createElement(tag);
      if (cls) e.className = cls;
      if (text != null) e.textContent = text;
      return e;
    }
    function role(r) {
      return el('span', 'kbadge k-' + r, r);
    }
    function linkOf(t) { return t && t.closest ? t.closest('a[data-d]') : null; }

    function fill(a) {
      var ids = (a.getAttribute('data-c') || a.getAttribute('data-d') || '').split(',');
      tip.textContent = '';
      var n = 0;
      ids.forEach(function (s) {
        var d = defs[+s];
        if (!d) return;
        n++;
        var item = el('div', 'tip-item');
        var head = el('div', 'tip-head');
        head.appendChild(el('span', 'tip-kind', d.k + '  '));
        head.appendChild(el('span', 'tip-name', d.n));
        var isMod = d.k === 'module' || d.k === 'view';
        if (isMod && d.r) { head.appendChild(document.createTextNode(' ')); head.appendChild(role(d.r)); }
        var where = el('span', 'tip-where', !isMod && d.m ? ' in ' + d.m + ' ' : ' ');
        if (!isMod && d.m && d.r) where.appendChild(role(d.r));
        where.appendChild(document.createTextNode('  ' + d.f + '.maude'));
        head.appendChild(where);
        item.appendChild(head);
        if (d.c) item.appendChild(el('pre', null, d.c));
        if (d.d) item.appendChild(el('p', null, d.d));
        if (d.sup) item.appendChild(el('p', 'tip-rel', 'supersorts: ' + d.sup.join(', ')));
        if (d.sub) item.appendChild(el('p', 'tip-rel', 'subsorts: ' + d.sub.join(', ')));
        var go = el('a', 'tip-go', 'Go to definition');
        go.href = root + 'src/' + d.f + '.html#' + d.a;
        item.appendChild(go);
        tip.appendChild(item);
      });
      if (n > 1) {
        tip.insertBefore(el('div', 'tip-note', n + ' declarations of this name (overloaded). '
          + 'The link goes to the first; each is listed here.'), tip.firstChild);
      }
      return n;
    }

    function place(a) {
      var r = a.getBoundingClientRect();
      var vw = document.documentElement.clientWidth, vh = window.innerHeight;
      var w = tip.offsetWidth, h = tip.offsetHeight;
      var left = Math.max(8, Math.min(r.left, vw - w - 8));
      var top = r.bottom + 4;
      if (top + h > vh - 8) {
        var up = r.top - h - 4;
        top = up >= 8 ? up : Math.max(8, vh - h - 8);
      }
      tip.style.left = left + 'px';
      tip.style.top = top + 'px';
    }

    function show(a) {
      clear();
      cur = a;
      if (!fill(a)) { hide(); return; }
      tip.hidden = false;
      place(a);
    }

    document.addEventListener('mouseover', function (e) {
      var a = linkOf(e.target);
      if (!a) return;
      clear();
      if (a === cur) return;
      showT = setTimeout(function () { show(a); }, 30);
    });
    document.addEventListener('mouseout', function (e) {
      var a = linkOf(e.target);
      if (!a) return;
      var to = e.relatedTarget;
      if (to && (a.contains(to) || tip.contains(to))) return;
      hideSoon();
    });
    tip.addEventListener('mouseover', clear);
    tip.addEventListener('mouseleave', hideSoon);
    document.addEventListener('focusin', function (e) {
      var a = linkOf(e.target);
      if (a) show(a);
    });
    document.addEventListener('focusout', function (e) {
      if (linkOf(e.target)) hideSoon();
    });
    document.addEventListener('keydown', function (e) { if (e.key === 'Escape') hide(); });
    window.addEventListener('scroll', hide, { passive: true });
  })();

  // Examples: the page's button opens every details.example, or closes them all when all are open.
  function exLabel(b, anyClosed) {
    b.textContent = anyClosed ? 'Show all examples' : 'Hide all examples';
  }
  function exClosed() {
    var els = document.querySelectorAll('details.example');
    for (var i = 0; i < els.length; i++) { if (!els[i].open) return true; }
    return false;
  }
  var exBtn = document.querySelector('button.exbtn');
  if (exBtn) {
    exBtn.addEventListener('click', function () {
      var open = exClosed();
      document.querySelectorAll('details.example').forEach(function (e) { e.open = open; });
      exLabel(exBtn, !open);
    });
    document.addEventListener('toggle', function (e) {
      if (e.target.matches && e.target.matches('details.example')) exLabel(exBtn, exClosed());
    }, true);
  }
})();
"""


def write(path, text):
    os.makedirs(os.path.dirname(path) or '.', exist_ok=True)
    with open(path, 'w', encoding='utf-8', newline='\n') as fh:
        fh.write(text)


def build(src, out, overview, examples=None):
    site = Site()
    main_path = os.path.join(src, 'main.maude')
    main_text = open(main_path, encoding='utf-8').read() if os.path.isfile(main_path) else ''
    load_order = re.findall(r'^\s*load\s+(\S+)', main_text, re.M)
    paths = {os.path.splitext(os.path.basename(p))[0]: p
             for p in glob.glob(os.path.join(src, '*.maude'))}
    stems = [s for s in load_order if s in paths]
    stems += sorted(s for s in paths if s not in stems)
    site.stems = set(paths)
    for stem in stems:
        raw = open(paths[stem], encoding='utf-8').read().split('\n')
        if raw and raw[-1] == '':
            raw.pop()
        raw = [ln.rstrip('\r') for ln in raw]
        f = SrcFile(stem, paths[stem], raw)
        for i, line in enumerate(raw):
            code, com = split_comment(line)
            f.codes.append(code)
            f.comments.append(com)
            if code.strip():
                f.kinds.append('code')
            elif com is not None:
                f.kinds.append('comment')
            else:
                f.kinds.append('blank')
        site.files.append(f)
    for f in site.files:
        scan_file(site, f)
    collect_mix_heads(site)
    for f in site.files:
        split_file(site, f)
    finalize_defs(site)
    for f in site.files:
        classify_file(site, f)
    compute_relations(site)
    if site.annot_errors:
        raise DocError('\n'.join(site.annot_errors))
    link_stats(site)
    attach_examples(site, examples)
    site.stats['defs'] = len(site.defs)
    site.stats['files'] = len(site.files)

    pages = {}
    if not os.path.isfile(overview):
        raise DocError('overview fragment not found: %s' % overview)
    frag = open(overview, encoding='utf-8').read()
    pages['index.html'] = home_page(site, expand_macros(site, frag, overview))
    for name, (title, body) in index_pages(site).items():
        pages[name] = page(site, '', title, body)
    for f in site.files:
        pages['src/%s.html' % f.stem] = render_file(site, f)
    write_out = {}
    for rel, text in pages.items():
        write_out[rel] = text
    # search index: names with their kind and root-relative href
    entries = []
    for d in site.defs:
        entries.append([d.name, d.kind, '%s#%s' % (d.page, d.anchor), d.stem])
    entries.sort(key=lambda e: (e[0].lower(), e[0], e[1], e[2]))
    write_out['search-index.js'] = 'window.TEE_DOCS_INDEX = %s;\n' % json.dumps(
        entries, separators=(',', ':'), ensure_ascii=True)
    write_out['style.css'] = CSS.lstrip()
    write_out['defs.js'] = defs_js(site)
    write_out['app.js'] = APP_JS
    for rel, text in write_out.items():
        write(os.path.join(out, rel), text)
    return site


def defs_js(site):
    """defs.js: the definitions table that hover popups read (index = data-d / data-c value)."""
    rows = []
    for d in site.alldefs:
        # every definition lives in src/<stem>.html, so the page is not stored
        assert d.page == 'src/%s.html' % d.stem
        e = {'n': d.name, 'k': d.kind, 'a': d.anchor, 'f': d.stem, 'c': d.code}
        if site.roles.get(d.module):
            e['r'] = site.roles[d.module]
        if d.module:
            e['m'] = d.module
        if d.doc:
            e['d'] = d.doc if len(d.doc) <= 500 else d.doc[:497].rstrip() + '...'
        if d.kind in ('sort', 'class'):
            sup = sorted(site.supers.get(d.name, ()))
            sub = sorted(site.subs.get(d.name, ()))
            if sup:
                e['sup'] = sup[:20]
            if sub:
                e['sub'] = sub[:20]
        rows.append(e)
    return 'window.TEE_DEFS = %s;\n' % json.dumps(rows, separators=(',', ':'), ensure_ascii=True)


def unresolved_summary(site):
    names = site.unresolved
    total = sum(names.values())
    looks = {k: v for k, v in names.items()
             if re.match(r'[A-Z][A-Za-z0-9]*$', k) or re.search(r'[a-z]-[a-z]', k)}
    return total, len(names), sum(looks.values()), len(looks), sorted(
        names.items(), key=lambda kv: (-kv[1], kv[0]))[:25]


# ----------------------------------------------------------------- checking

def check_site(out):
    pages = []
    for root, _, files in os.walk(out):
        for fn in files:
            if fn.endswith('.html'):
                pages.append(os.path.normpath(os.path.join(root, fn)))
    pages.sort()
    ids = {}
    errors = []
    for p in pages:
        text = open(p, encoding='utf-8').read()
        found = re.findall(r'\sid="([^"]+)"', text)
        seen = set()
        for x in found:
            if x in seen:
                errors.append('%s: duplicate id %s' % (os.path.relpath(p, out), x))
            seen.add(x)
        ids[p] = seen
    links = 0
    for p in pages:
        text = open(p, encoding='utf-8').read()
        for h in re.findall(r'\shref="([^"]+)"', text):
            if h.startswith(('http://', 'https://', 'mailto:')):
                continue
            links += 1
            target, _, anchor = h.partition('#')
            tpath = os.path.normpath(os.path.join(os.path.dirname(p), target)) if target else p
            rel = os.path.relpath(p, out)
            if target and not os.path.exists(tpath):
                errors.append('%s: missing target %s' % (rel, h))
                continue
            if anchor and tpath in ids and anchor not in ids[tpath]:
                errors.append('%s: missing anchor %s' % (rel, h))
    return len(pages), links, errors


def main(argv):
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument('--src', default='src', help='directory of .maude files (default: src)')
    ap.add_argument('--out', default='build/docs', help='output directory (default: build/docs)')
    ap.add_argument('--overview', default=None,
                    help='hand-written overview fragment for index.html '
                         '(default: docs/overview.html next to --src)')
    ap.add_argument('--examples', default=None, metavar='JSON',
                    help='verified examples (docs/examples.json from tools/gen-examples.py), '
                         'shown collapsed under rules and ops (default: none)')
    ap.add_argument('--check', action='store_true', help='verify every local href and #anchor')
    args = ap.parse_args(argv)
    src = args.src
    overview = args.overview or os.path.join(os.path.dirname(os.path.abspath(src)), 'docs', 'overview.html')
    try:
        examples = load_examples(args.examples) if args.examples else None
        site = build(src, args.out, overview, examples)
    except DocError as e:
        print('[gen-docs] error:\n%s' % e, file=sys.stderr)
        return 2
    st = site.stats
    total, distinct, looks, looks_n, top = unresolved_summary(site)
    print('[gen-docs] files: %d  definitions: %d  modules/views: %d/%d  sorts/classes: %d/%d  '
          'ops: %d  labels: %d' % (
              st['files'], st['defs'],
              sum(1 for d in site.defs if d.kind == 'module'), sum(1 for d in site.defs if d.kind == 'view'),
              sum(1 for d in site.defs if d.kind == 'sort'), sum(1 for d in site.defs if d.kind == 'class'),
              sum(1 for d in site.defs if d.kind == 'op'),
              sum(1 for d in site.defs if d.kind in ('rl', 'eq'))))
    print('[gen-docs] links: %d (of which overloaded, linked to the primary of several: %d)' % (
        st['links'], st['ambiguous']))
    print('[gen-docs] unresolved identifier uses: %d over %d names; of these, %d uses over %d names '
          'look like defined names (capitalized or hyphenated)' % (total, distinct, looks, looks_n))
    print('[gen-docs] most frequent unresolved names: %s' % ', '.join('%s(%d)' % kv for kv in top))
    btop = sorted(site.builtins.items(), key=lambda kv: (-kv[1], kv[0]))
    print('[gen-docs] builtin identifier uses: %d over %d names (Maude builtins, not unresolved): %s' % (
        sum(site.builtins.values()), len(site.builtins), ', '.join('%s(%d)' % kv for kv in btop)))
    if args.examples:
        report_examples(site)
    if args.check:
        npages, nlinks, errors = check_site(args.out)
        print('[gen-docs] check: %d pages, %d local links, %d problems' % (npages, nlinks, len(errors)))
        for e in errors[:50]:
            print('  ' + e)
        if errors:
            return 1
    print('[gen-docs] wrote %s' % os.path.abspath(args.out))
    return 0


if __name__ == '__main__':
    sys.exit(main(sys.argv[1:]))
