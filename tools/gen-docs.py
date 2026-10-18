#!/usr/bin/env python3
"""Build a static documentation site for the Maude TEE specification.

    python3 tools/gen-docs.py --src src --out build/docs [--overview docs/overview.html] [--check]

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
  * `---@dmc` lines are tool annotations (dimmed), and comment lines that look
    like Maude code are shown dimmed rather than as prose;
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
import json
import os
import posixpath
import re
import sys

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
CODE_MARK_RE = re.compile(r' : | => | = | -> |\s\.$|^\.$')
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


class Def:
    def __init__(self, kind, name, stem, module, tok, stmt, owner=None):
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
        self.usage = collections.defaultdict(set)
        self.importers = collections.defaultdict(set)
        self.supers = collections.defaultdict(set)
        self.subs = collections.defaultdict(set)
        self.unresolved = collections.Counter()
        self.builtins = collections.Counter()
        self.mix_heads = {}
        self.stats = collections.Counter()
        self._vis = {}

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


def is_dmc(raw):
    return raw.lstrip().startswith('---@dmc')


def is_codelike(body):
    b = body.strip()
    if not b:
        return False
    return bool(CODE_START_RE.match(b) or CODE_MARK_RE.search(b))


def comment_kind(raw, body):
    """One of: dmc, banner, blank, code, prose."""
    if is_dmc(raw):
        return 'dmc'
    if BANNER_RE.fullmatch(raw):
        return 'banner'
    if not body.strip():
        return 'blank'
    if is_codelike(body):
        return 'code'
    return 'prose'


def prose_of_run(f, idxs):
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
            add_def(site, f, 'view' if st.kw == 'view' else 'module', name, st.toks[1], st,
                    module=name)
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
                if t.text not in PUNCT and t.text != '.':
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
    for m in site.importers:
        pass
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


def ambiguous_target(name, cands):
    kinds = {d.kind for d in cands}
    first = next(k for k in KIND_ORDER if k in kinds)
    if first in ('sort', 'class'):
        return 'sorts.html', 'nm-' + slug(name)
    if first in ('module', 'view'):
        return 'modules.html', 'nm-' + slug(name)
    if first == 'op':
        return 'ops.html', 'nm-' + slug(name)
    if first in ('rl', 'eq'):
        return 'rules.html', 'nm-' + slug(name)
    return 'sorts.html', 'nma-' + slug(name)


def set_def_target(site, t, res, name, cur_mod, record):
    if isinstance(res, Def):
        t.target = (res.page, res.anchor)
        t.res_def = res
        t.title = res.sig
        if record and res.kind in ('sort', 'class') and cur_mod:
            site.usage[res].add(cur_mod)
    else:
        t.target = ambiguous_target(name, res)
        t.ambiguous = True
        t.title = 'ambiguous: %d definitions' % len(res)


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


def classify_token(site, t, cur_mod, hint, record, owner=None, attr_pos=False):
    """Assign the class and link target of a non-definition token.

    `owner` is the class named earlier in the same statement (as in a configuration
    `< o : C | attr : v >`); an attribute name then resolves to that class's attribute.
    """
    if t.is_def is not None or t.in_attr or t.cls == 'attr':
        return
    txt = t.text
    if txt in KEYWORDS:
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
    if txt in PUNCT:
        return
    if t.vardecl is not None:
        sort_text, vmod = t.vardecl
        t.cls = 'var'
        t.title = '%s : %s' % (txt, sort_text or '?')
        if sort_text:
            res = pick(site, sort_text, vmod, 'sort')
            if isinstance(res, Def):
                t.target = (res.page, res.anchor)
        return
    if hint == 'file':
        if txt in site.stems:
            t.cls = 'file'
            t.target = ('src/%s.html' % txt, None)
        return
    if owner is not None:
        for d in site.by_name.get(txt, ()):
            if d.kind == 'attr' and d.owner == owner:
                set_def_target(site, t, d, txt, cur_mod, record)
                return
    heads = site.mix_heads.get(txt)
    if heads:
        res = pick_from(site, heads, cur_mod, hint)
        set_def_target(site, t, res, heads[0].name, cur_mod, record)
        return
    local_var = site.var_table.get((cur_mod, txt))
    if local_var is not None or (cur_mod, txt) in site.var_table:
        found = (cur_mod, local_var)
    else:
        res = pick(site, txt, cur_mod, hint, attr_pos)
        if res is not None:
            set_def_target(site, t, res, txt, cur_mod, record)
            return
        found = site.visible_var(cur_mod, txt)
    if found is not None:
        vmod, sort_text = found
        t.cls = 'var'
        t.title = '%s : %s' % (txt, sort_text or '?')
        if sort_text:
            res = pick(site, sort_text, vmod, 'sort')
            if isinstance(res, Def):
                t.target = (res.page, res.anchor)
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
    if len(text) < 2 or text.startswith('"') or '`' in text or t.in_attr or t.cls == 'attr':
        return [(0, len(text))]
    cuts = set()
    if text[0] == '.' and text[1].isalpha():
        cuts.add(1)
    for i, c in enumerate(text):
        if c == '[':
            j = i
            while j > 0 and (text[j - 1].isalnum() or text[j - 1] == '-'):
                j -= 1
            if not (j == 0 and text[:i + 1] in heads):
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
    out = []
    for k, (a, b) in enumerate(spans):
        if k == 0:
            t.text, t.e = text[a:b], s0 + b
            out.append(t)
        else:
            out.append(Tok(text[a:b], t.line, s0 + a, s0 + b))
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


def render_tok(t, cur_dir):
    txt = esc(t.text)
    if t.is_def is not None:
        d = t.is_def
        if t.in_attr:
            pre = '[' if t.text.startswith('[') else ''
            post = ']' if pre and t.text.endswith(']') else ''
            inner = t.text[len(pre):len(t.text) - len(post)]
            return '<span class="attr">%s<span class="def" id="%s" title="%s">%s</span>%s</span>' % (
                esc(pre), d.anchor, esc(d.sig), esc(inner), esc(post))
        return '<span class="def" id="%s" title="%s">%s</span>' % (d.anchor, esc(d.sig), txt)
    if t.in_attr or t.cls == 'attr':
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
        return '<span class="builtin" title="Maude builtin">%s</span>' % txt
    title = ' title="%s"' % esc(t.title) if t.title else ''
    if t.target:
        h = esc(href(cur_dir, t.target))
        if t.cls == 'var':
            return '<a class="var" href="%s"%s>%s</a>' % (h, title, txt)
        cls = 'id amb' if t.ambiguous else 'id'
        return '<a class="%s" href="%s"%s>%s</a>' % (cls, h, title, txt)
    if t.cls == 'var':
        return '<span class="var"%s>%s</span>' % (title, txt)
    if t.cls == 'file':
        return txt
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


def inline_code(site, cur_dir, s, cur_mod):
    parts, pos = [], 0
    for m in TOKEN_RE.finditer(s):
        for t in split_tok(Tok(m.group(0), 0, m.start(), m.end()), site.mix_heads):
            classify_token(site, t, cur_mod, None, False)
            parts.append(esc(s[pos:t.s]))
            parts.append(render_tok(t, cur_dir))
            pos = t.e
    parts.append(esc(s[pos:]))
    return '<code>%s</code>' % ''.join(parts)


def prose_html(site, cur_dir, text, cur_mod):
    out = []
    pos = 0
    for m in re.finditer(r'`([^`]+)`', text):
        out.append(esc(text[pos:m.start()]))
        out.append(inline_code(site, cur_dir, m.group(1), cur_mod))
        pos = m.end()
    out.append(esc(text[pos:]))
    return ''.join(out)


def render_comment(site, cur_dir, f, idxs, cur_mod, attached, drop_first=False):
    out, para, pre = [], [], []
    skip = [drop_first]

    def flush_para():
        if para:
            if skip[0]:
                skip[0] = False
            else:
                out.append('<p>%s</p>\n' % prose_html(site, cur_dir, ' '.join(para), cur_mod))
            del para[:]

    def flush_pre():
        if pre:
            out.append('<pre class="cmtcode">%s</pre>\n' % '\n'.join(pre))
            del pre[:]

    for i in idxs:
        raw = f.raw[i]
        body = comment_body(raw)
        k = comment_kind(raw, body)
        if k == 'dmc':
            flush_para()
            flush_pre()
            out.append('<div class="dmc">%s</div>\n' % esc(body.strip()))
        elif k == 'banner':
            flush_para()
            flush_pre()
        elif k == 'blank':
            flush_para()
            flush_pre()
        elif k == 'code':
            flush_para()
            pre.append(esc(body.rstrip()))
        else:
            flush_pre()
            para.append(body.strip())
    flush_para()
    flush_pre()
    if not out:
        return ''
    return '<div class="%s">\n%s</div>\n' % ('doc' if attached else 'free', ''.join(out))


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


def module_header_html(site, cur_dir, st):
    name = st.toks[1].text
    d = site.module_defs.get(name)
    parts = ['<section class="module">\n<h2 class="modtitle">%s <span class="kindtag">%s</span></h2>\n'
             % (esc(name), esc(st.kw))]
    if st.kw == 'view':
        vt = site.view_targets.get(name, [])
        parts.append('<p class="meta">maps from %s</p>\n' % ' '.join(module_name_link(site, cur_dir, x) for x in vt[:1]))
        if len(vt) > 1:
            parts.append('<p class="meta">to %s</p>\n' % ' '.join(module_name_link(site, cur_dir, x) for x in vt[1:]))
    else:
        imps = []
        for x in dict.fromkeys(site.imports.get(name, [])):
            imps.append(module_name_link(site, cur_dir, x))
        if imps:
            parts.append('<p class="meta">imports %s</p>\n' % ', '.join(imps))
    by = sorted(site.importers.get(name, ()))
    if by:
        items = ''.join('<li>%s</li>' % module_name_link(site, cur_dir, m) for m in by)
        parts.append('<details class="uses"><summary>imported by %d module%s</summary><ul>%s</ul></details>\n'
                     % (len(by), '' if len(by) == 1 else 's', items))
    return ''.join(parts)


def render_file(site, f):
    cur_dir = 'src'
    out = []
    starts = {}
    ends = {}
    uses_at = collections.defaultdict(list)
    doc_at = {}
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
    cur_mod = None
    i, n = 0, len(f.raw)
    while i < n:
        kind = f.kinds[i]
        if kind == 'comment':
            j = i
            while j < n and f.kinds[j] == 'comment':
                j += 1
            st = doc_at.get(j)
            attached = st is not None and st.doc_start == i
            ctx = st.module if attached else cur_mod
            # the first paragraph is already shown as the page's lead, so it is not repeated
            out.append(render_comment(site, cur_dir, f, range(i, j), ctx, attached,
                                      drop_first=(i == summary_run)))
            i = j
            continue
        if kind == 'code':
            if i in starts:
                out.append(module_header_html(site, cur_dir, starts[i]))
                cur_mod = starts[i].toks[1].text
            out.append(code_row(f, i, cur_dir))
            if i in ends:
                out.append('</section>\n')
                cur_mod = None
            for st in uses_at.get(i, []):
                for d in st.defs:
                    if d.kind == 'sort':
                        out.append(uses_html(site, d, cur_dir))
        i += 1

    sidebar = ['<h3>Modules in this file</h3>\n<ul>']
    for d in f.mod_defs:
        sidebar.append('<li><a href="#%s">%s</a> <span class="kindtag">%s</span></li>'
                       % (esc(d.anchor), esc(d.name), esc(d.stmt.kw)))
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
            '<script src="%sapp.js" defer></script>\n</head>\n'
            '<body data-root="%s">\n'
            '<header class="top"><a class="brand" href="%sindex.html">TEE Formal Spec</a>'
            '<nav class="links">%s</nav>'
            '<form class="search" action="%sindex.html" method="get">'
            '<input id="q" name="q" type="search" placeholder="Search names" aria-label="Search names">'
            '</form></header>\n'
            '<div class="%s">%s<main>\n%s</main></div>\n</body>\n</html>\n') % (
        esc(title), root, root, root, root, root, nav, root, cls, side_html, body)


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
    return '<a href="%s">%s</a>' % (esc(href(cur_dir, (d.page, d.anchor))), esc(d.name))


def file_link(cur_dir, stem):
    return '<a href="%s">%s</a>' % (esc(href(cur_dir, ('src/%s.html' % stem, None))), esc(stem))


def sort_key(d):
    return (d.name.lower(), d.name, d.stem, d.line)


def index_pages(site):
    pages = {}
    root = ''
    # modules.html
    mods = sorted([d for d in site.defs if d.kind in ('module', 'view')], key=sort_key)
    rows = []
    for rid, d in group_rows(mods, lambda d: 'nm-' + slug(d.name)):
        imps = ', '.join(module_name_link(site, root, x) for x in dict.fromkeys(site.imports.get(d.name, [])))
        by = sorted(site.importers.get(d.name, ()))
        rows.append((rid, [def_link(site, root, d), esc(d.kind), file_link(root, d.stem),
                           esc(first_sentence(d.doc)), imps,
                           ', '.join(module_name_link(site, root, m) for m in by)]))
    pages['modules.html'] = (
        'Modules and views',
        '<h1>Modules and views</h1>\n<p class="lead">Every <code>fmod</code>, <code>mod</code>, '
        '<code>omod</code>, <code>fth</code>, <code>th</code> and <code>view</code> in src/, '
        'with its file, its summary, and the modules it imports and that import it.</p>\n'
        + table(['Name', 'Kind', 'File', 'Summary', 'Imports', 'Imported by'], rows))

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
        rows.append((rid, [def_link(site, root, d), '<code>%s</code>' % esc(d.sig),
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
        rows.append((rid, [def_link(site, root, d), esc(d.kind), '<code>%s</code>' % esc(d.sig[:200]),
                           file_link(root, d.stem), module_name_link(site, root, d.module or '')]))
    pages['rules.html'] = (
        'Rule and equation labels',
        '<h1>Rule and equation labels</h1>\n<p class="lead">Labeled <code>rl</code>, <code>crl</code>, '
        '<code>eq</code> and <code>ceq</code> statements. Unlabeled equations are not listed.</p>\n'
        + table(['Label', 'Kind', 'Statement', 'File', 'Module'], rows))
    return pages


def module_sort_link(site, cur_dir, module, name):
    res = pick(site, name, module, 'sort')
    if isinstance(res, Def):
        return '<a href="%s">%s</a>' % (esc(href(cur_dir, (res.page, res.anchor))), esc(name))
    return esc(name)


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

    A name with one definition links to it; an ambiguous name links to its group on the
    index page, as code links do."""
    if kind == 'file':
        if name not in site.stems:
            return None
        target = 'src/%s.html' % name
    else:
        kinds, index_page, prefix = MACRO_KINDS[kind]
        cands = [d for d in site.by_name.get(name, ()) if d.kind in kinds]
        if not cands:
            return None
        if len(cands) == 1:
            target = '%s#%s' % (cands[0].page, cands[0].anchor)
        else:
            target = '%s#%s-%s' % (index_page, prefix, slug(name))
    if shown is None:
        return '<a class="id" href="%s">%s</a>' % (esc(target), esc(name))
    return '<a class="xref" href="%s">%s</a>' % (esc(target), shown)


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
:root{--hdr:52px}
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
.doc{margin:14px 0 6px;padding:6px 12px;border-left:3px solid var(--line)}
.doc p{margin:6px 0}
.free{margin:10px 0;color:var(--muted);font-size:14px}
.free p{margin:4px 0}
.dmc{font:12px ui-monospace,monospace;color:var(--cmt);margin:2px 0}
pre.cmtcode{font:13px/1.35 ui-monospace,SFMono-Regular,Menlo,Consolas,monospace;color:var(--cmt);
margin:4px 0;white-space:pre-wrap}
/* The whole block scrolls horizontally as one unit: lines never wrap and never scroll alone. */
.code{font-size:14px;line-height:1.5;margin-top:4px;overflow-x:auto;padding-bottom:2px}
.ln{display:flex;white-space:pre;min-height:1.5em;width:max-content;min-width:100%}
.ln:target,.ln.target{background:var(--hl)}
.ln .no{flex:0 0 3.5em;text-align:right;padding-right:12px;color:var(--cmt);user-select:none}
.ln .src{flex:0 0 auto}
a.xref{color:var(--link)}
.kw{color:var(--kw);font-weight:600}.op{color:var(--op)}.num{color:var(--num)}.str{color:var(--str)}
.attr{color:var(--attr)}.cmt{color:var(--cmt);font-style:italic}
.def{color:var(--def);font-weight:600}
a.id{color:var(--fg);border-bottom:1px dotted var(--line)}
a.id:hover{color:var(--link);border-bottom-color:var(--link);text-decoration:none}
a.id.amb{border-bottom-style:dashed}
a.var{color:var(--var);border-bottom:1px dotted var(--var)}
span.var{color:var(--var)}
span.builtin{color:var(--muted);font-style:italic}
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
/* Narrow screens: the contents column becomes a <details> above the page body. */
@media (max-width:900px){.wrap{grid-template-columns:minmax(0,1fr)}
.side:not(.sidein){display:none}
.sidem{display:block;border-bottom:1px solid var(--line);padding:6px 16px;font-size:14px}
.sidem summary{cursor:pointer;font-weight:600}
.sidein{position:static;max-height:none;border:0;padding:4px 0;font-size:14px}
main{padding:12px 16px 40px}.top .search{margin-left:0}details.uses ul{columns:1}}
"""

APP_JS = """(function () {
  'use strict';
  var root = document.body.getAttribute('data-root') || '';
  function markLine() {
    var h = location.hash;
    if (!/^#L\\d+$/.test(h)) return;
    document.querySelectorAll('.ln.target').forEach(function (e) { e.classList.remove('target'); });
    var el = document.getElementById(h.slice(1));
    if (el) el.classList.add('target');
  }
  window.addEventListener('hashchange', markLine);
  markLine();
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
"""


def write(path, text):
    os.makedirs(os.path.dirname(path) or '.', exist_ok=True)
    with open(path, 'w', encoding='utf-8', newline='\n') as fh:
        fh.write(text)


def build(src, out, overview):
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
    link_stats(site)
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
    write_out['app.js'] = APP_JS
    for rel, text in write_out.items():
        write(os.path.join(out, rel), text)
    return site


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
    ap.add_argument('--check', action='store_true', help='verify every local href and #anchor')
    args = ap.parse_args(argv)
    src = args.src
    overview = args.overview or os.path.join(os.path.dirname(os.path.abspath(src)), 'docs', 'overview.html')
    try:
        site = build(src, args.out, overview)
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
    print('[gen-docs] links: %d (of which ambiguous, linked to an index group: %d)' % (
        st['links'], st['ambiguous']))
    print('[gen-docs] unresolved identifier uses: %d over %d names; of these, %d uses over %d names '
          'look like defined names (capitalized or hyphenated)' % (total, distinct, looks, looks_n))
    print('[gen-docs] most frequent unresolved names: %s' % ', '.join('%s(%d)' % kv for kv in top))
    btop = sorted(site.builtins.items(), key=lambda kv: (-kv[1], kv[0]))
    print('[gen-docs] builtin identifier uses: %d over %d names (Maude builtins, not unresolved): %s' % (
        sum(site.builtins.values()), len(site.builtins), ', '.join('%s(%d)' % kv for kv in btop)))
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
