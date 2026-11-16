#!/usr/bin/env python3
"""Compile a Rust source file to textual MIR, translate it, and evaluate in Maude.

This is a strict frontend for a concrete MIR subset, not the KMIR SMIR JSON
extractor. Unsupported constructs in reachable functions are errors.
"""
from __future__ import annotations

import argparse
from dataclasses import dataclass
import json
import os
from pathlib import Path
import re
import shutil
import subprocess
import sys
import tempfile


ROOT = Path(__file__).resolve().parents[1]
INTEGER = re.compile(r"([ui])(8|16|32|64|128|size)$")
BINOPS = set("Add Sub Mul Div Rem BitXor BitAnd BitOr Shl Shr Eq Lt Le Ne Ge Gt".split())


class FrontendError(Exception):
    pass


def split(text: str, delimiter: str = ",") -> list[str]:
    """Split at top-level delimiters, respecting Rust strings and nesting."""
    result, start, depth, quoted, escaped = [], 0, 0, False, False
    for i, char in enumerate(text):
        if quoted:
            if escaped:
                escaped = False
            elif char == "\\":
                escaped = True
            elif char == '"':
                quoted = False
        elif char == '"':
            quoted = True
        elif char in "([{":
            depth += 1
        elif char in ")]}":
            depth -= 1
        elif char == delimiter and depth == 0:
            result.append(text[start:i].strip())
            start = i + 1
    if depth or quoted:
        raise FrontendError(f"Unbalanced MIR expression: {text}")
    return [part for part in result + [text[start:].strip()] if part]


def quote(text: str) -> str:
    return json.dumps(text, ensure_ascii=True)


def int_info(ty: str) -> tuple[int, bool]:
    match = INTEGER.fullmatch(ty)
    if not match:
        raise FrontendError(f"Expected integer type, got {ty!r}")
    return (64 if match[2] == "size" else int(match[2]), match[1] == "i")


@dataclass
class Function:
    name: str
    params: list[tuple[int, str]]
    result: str
    locals: dict[int, str]
    blocks: dict[int, list[str]]


def read_externs(source: Path) -> set[str]:
    """The foreign functions that the source declares in its extern "C" blocks.  A call of one is
    passed to the model as a call of a foreign function; the model decides what it is (rustApi in
    lang/rust/tee-support.maude, or an extension) and rejects an unknown one."""
    names = set()
    for block in re.finditer(r'extern\s+"C"\s*\{(.*?)\}', source.read_text(), re.S):
        names |= set(re.findall(r"\bfn\s+(\w+)\s*\(", block[1]))
    return names


def impl_types(source: Path) -> dict[int, str]:
    """The type of each inherent impl block of the source, by the line of its impl keyword."""
    types = {}
    for number, line in enumerate(source.read_text().splitlines(), 1):
        block = re.match(r"\s*impl\s+(\w+)\s*\{", line)
        if block:
            types[number] = block[1]
    return types


def read_functions(mir: str, impls: dict[int, str] | None = None) -> dict[str, Function]:
    """The functions of the MIR, by the name a call uses.  rustc prints a method of an inherent
    impl as <impl at FILE:LINE:COL: LINE:COL>::NAME and calls it as TYPE::NAME; impls gives the
    TYPE of the impl block at LINE."""
    functions = {}
    impls = impls or {}

    def method_name(match: re.Match) -> str:
        line = int(match[1])
        if line not in impls:
            raise FrontendError(f"Unsupported impl block at line {line} (only `impl Type {{` is supported)")
        return f"{impls[line]}::{match[2]}"
    # rustc prints top-level functions at column zero and their closing brace
    # at column zero. Nested scopes and blocks are indented.
    for match in re.finditer(r"^fn ([^\n]+) \{\n(.*?)^\}", mir, re.M | re.S):
        header, body = match[1], match[2]
        header = re.sub(r"^<impl at [^>]*?:(\d+):\d+: \d+:\d+>::(\w+)", method_name, header)
        signature = re.fullmatch(r"([\w:]+)\((.*?)\) -> (.+)", header)
        if not signature:
            continue
        params = []
        for arg in split(signature[2]):
            param = re.fullmatch(r"_(\d+): (.+)", arg)
            if not param:
                raise FrontendError(f"Unsupported parameter declaration: {arg}")
            params.append((int(param[1]), param[2]))
        local_types = {0: signature[3], **dict(params)}
        for decl in re.finditer(r"^\s+let (?:mut )?_(\d+): (.+);$", body, re.M):
            local_types[int(decl[1])] = decl[2]
        blocks = {}
        for block in re.finditer(r"^    bb(\d+)(?: \(cleanup\))?: \{\n(.*?)^    \}", body, re.M | re.S):
            blocks[int(block[1])] = split(block[2].strip(), ";")
        name = signature[1]
        if name in functions:
            raise FrontendError(f"Duplicate MIR function: {name}")
        functions[name] = Function(name, params, signature[3], local_types, blocks)
    return functions


def read_constants(mir: str) -> dict[str, str | None]:
    """The constant items of the MIR, by name: the operand of a constant whose value rustc prints
    (const NAME: T = const V;), or None for a constant that rustc prints as a body (a computed
    value, such as 10 * 1000 or A + 1), whose value the frontend does not evaluate."""
    constants: dict[str, str | None] = {}
    for item in re.finditer(r"^const ([\w:]+): [^=\n]+ = (.*)$", mir, re.M):
        value = item[2].rstrip(";")
        constants[item[1]] = value if item[2].endswith(";") and value.startswith("const ") else None
    return constants


def read_structs(source: Path) -> dict:
    structs = {}
    for structure in re.finditer(r"\bstruct\s+(\w+)\s*\{([^{}]*)\}", source.read_text()):
        fields = [tuple(re.sub(r"^pub(?:\([^)]*\))?\s+", "", part).split(":", 1)) for part in split(structure[2])]
        structs[structure[1]] = [(name.strip(), ty.strip()) for name, ty in fields]
    return structs


class Translator:
    def __init__(self, functions: dict[str, Function], structs=None, constants=None, externs=None):
        self.functions = functions
        self.externs = externs or set()
        self.constants = constants or {}
        self.types: dict[str, int] = {}
        self.function_ids: dict[str, int] = {}
        self.bodies: dict[str, str] = {}
        self.externals: dict[str, str] = {}
        self.structs = structs or {}

    @staticmethod
    def pointee(name: str) -> str | None:
        match = re.fullmatch(r"(?:&(?:mut )?|\*(?:const|mut) )(.+)", name)
        return match[1] if match else None

    def ty(self, name: str) -> str:
        name = name.strip()
        if name not in self.types:
            if name not in ("bool", "()") and not INTEGER.fullmatch(name):
                pointer = self.pointee(name)
                array = re.fullmatch(r"\[(.+); (\d+)\]", name)
                if pointer:
                    self.ty(pointer)
                elif array:
                    self.ty(array[1])
                    self.ty("usize")
                elif name in self.structs:
                    for _, field in self.structs[name]:
                        self.ty(field)
                elif name.startswith("(") and name.endswith(")"):
                    for field in split(name[1:-1]):
                        self.ty(field)
                else:
                    raise FrontendError(f"Unsupported MIR type: {name}")
            self.types[name] = len(self.types) + 1
        return f"ty({self.types[name]})"

    def type_info(self, name: str) -> str:
        pointer = self.pointee(name)
        if pointer:
            kind = "typeInfoRefType" if name.startswith("&") else "typeInfoPtrType"
            return f"{kind}({self.ty(pointer)})"
        array = re.fullmatch(r"\[(.+); (\d+)\]", name)
        if array:
            raw = " ".join(f"byte({byte})" for byte in int(array[2]).to_bytes(8, "little"))
            length = (f"someTyConst(tyConst(tyConstKindValue({self.ty('usize')}, "
                      f"allocation(bytes({raw}), provenanceMap(.ProvenanceMapEntries), align(8), mutabilityNot)), tyConstId(0)))")
            return f"typeInfoArrayType({self.ty(array[1])}, {length})"
        if name in self.structs:
            fields = " ".join(self.ty(ty) for _, ty in self.structs[name]) or ".Tys"
            return f"typeInfoTupleType({fields}, noLayoutShape)"
        if name == "bool":
            return "typeInfoPrimitiveType(primTypeBool)"
        if name.startswith("("):
            fields = " ".join(self.ty(field) for field in split(name[1:-1])) or ".Tys"
            return f"typeInfoTupleType({fields}, noLayoutShape)"
        _, signed = int_info(name)
        suffix = name[1:]
        primitive = f"primTypeInt(intTyI{suffix})" if signed else f"primTypeUint(uintTyU{suffix})"
        return f"typeInfoPrimitiveType({primitive})"

    def place(self, text: str) -> str:
        text = text.strip()
        if re.fullmatch(r"_\d+", text):
            return f"place(local({text[1:]}), .ProjectionElems)"
        deref = re.fullmatch(r"\(\*_(\d+)\)", text)
        if deref:
            return f"place(local({deref[1]}), projectionElemDeref)"
        index = re.fullmatch(r"_(\d+)\[(\d+|_\d+)\]", text)
        if index:
            projection = f"projectionElemIndex(local({index[2][1:]}))" if index[2].startswith("_") else f"projectionElemConstantIndex({index[2]}, {int(index[2]) + 1}, false)"
            return f"place(local({index[1]}), {projection})"
        field = re.fullmatch(r"\(_(\d+)\.(\d+): (.+)\)", text)
        if field:
            return f"place(local({field[1]}), projectionElemField(fieldIdx({field[2]}), {self.ty(field[3])}))"
        raise FrontendError(f"Unsupported MIR place: {text}")

    def constant(self, value: str, ty: str) -> str:
        if ty == "bool":
            if value not in ("true", "false"):
                raise FrontendError(f"Invalid bool: {value}")
            data = [int(value == "true")]
        else:
            width, signed = int_info(ty)
            number = int(value.replace("_", ""), 0) if value.lower().lstrip("-").startswith(("0x", "0b", "0o")) else int(value.replace("_", ""))
            if not (-(1 << (width - 1)) if signed else 0) <= number <= (1 << (width - int(signed))) - 1:
                raise FrontendError(f"Constant {value} outside {ty}")
            data = (number % (1 << width)).to_bytes(width // 8, "little")
        raw = " ".join(f"byte({byte})" for byte in data)
        return ("operandConstant(constOperand(span(0), noUserTypeAnnotationIndex, "
                f"mirConst(constantKindAllocated(allocation(bytes({raw}), "
                "provenanceMap(.ProvenanceMapEntries), align(1), mutabilityNot)), "
                f"{self.ty(ty)}, mirConstId(0))))")

    def operand(self, text: str) -> str:
        text = text.strip()
        for kind in ("copy", "move"):
            if text.startswith(kind + " "):
                return f"operand{kind.title()}({self.place(text[len(kind) + 1:])})"
        if text in ("const true", "const false"):
            return self.constant(text[6:], "bool")
        const = re.fullmatch(r"const (-?[\w]+)_([ui](?:8|16|32|64|128|size))", text)
        if const:
            return self.constant(const[1], const[2])
        if text == "const ()":
            return self.fn_constant(self.ty("()"))
        named = re.fullmatch(r"const ([\w:]+)", text)
        if named and named[1] in self.constants:
            value = self.constants[named[1]]
            if value is None:
                raise FrontendError(f"Constant {named[1]} is computed (rustc prints it as a body); give it a literal value")
            return self.operand(value)
        raise FrontendError(f"Unsupported MIR operand: {text}")

    @staticmethod
    def fn_constant(ty: str) -> str:
        return ("operandConstant(constOperand(span(0), noUserTypeAnnotationIndex, "
                f"mirConst(constantKindZeroSized, {ty}, mirConstId(0))))")

    def rvalue(self, text: str) -> str:
        if text.startswith("&raw mut ") or text.startswith("&raw const "):
            mutable = text.startswith("&raw mut ")
            return f"rvalueAddressOf({'mutabilityMut' if mutable else 'mutabilityNot'}, {self.place(text.split(' ', 2)[2])})"
        if text.startswith("&"):
            mutable = text.startswith("&mut ")
            place = text[5:] if mutable else text[1:]
            borrow = "borrowKindMut(mutBorrowKindDefault)" if mutable else "borrowKindShared"
            return f'rvalueRef(region(opaque("")), {borrow}, {self.place(place)})'
        array = re.fullmatch(r"\[(.*)\]", text)
        if array:
            repeated = split(array[1], ";")
            if len(repeated) == 2:
                count = int(repeated[1])
                operands = [self.operand(repeated[0])] * count
            else:
                operands = [self.operand(arg) for arg in split(array[1])]
            # The array element type is determined by the destination local;
            # aggregateKindArray only tags the value as a Range.
            return f"rvalueAggregate(aggregateKindArray({self.ty('u8')}), ({', '.join(operands) or '.Operands'}))"
        structure = re.fullmatch(r"(\w+) \{ (.*) \}", text)
        if structure and structure[1] in self.structs:
            fields = dict(part.split(": ", 1) for part in split(structure[2]))
            operands = [self.operand(fields[name]) for name, _ in self.structs[structure[1]]]
            return f"rvalueAggregate(aggregateKindTuple, ({', '.join(operands) or '.Operands'}))"
        operation = re.fullmatch(r"(\w+)\((.*)\)", text)
        if operation:
            name, args = operation[1], split(operation[2])
            checked = name.endswith("WithOverflow")
            base = name.removesuffix("WithOverflow")
            if base in BINOPS and len(args) == 2:
                kind = "rvalueCheckedBinaryOp" if checked else "rvalueBinaryOp"
                return f"{kind}(binOp{base}, {self.operand(args[0])}, {self.operand(args[1])})"
            if name in ("Not", "Neg") and len(args) == 1:
                return f"rvalueUnaryOp(unOp{name}, {self.operand(args[0])})"
        if text.startswith("(") and text.endswith(")"):
            args = split(text[1:-1])
            return f"rvalueAggregate(aggregateKindTuple, ({', '.join(self.operand(arg) for arg in args) or '.Operands'}))"
        cast = re.fullmatch(r"(.+) as ([ui](?:8|16|32|64|128|size)) \(IntToInt\)", text)
        if cast:
            return f"rvalueCast(castKindIntToInt, {self.operand(cast[1])}, {self.ty(cast[2])})"
        pointer_cast = re.fullmatch(r"(.+) as (.+) \((PtrToPtr|Transmute|PointerWithExposedProvenance)\)", text)
        if pointer_cast:
            kind = {"PtrToPtr": "castKindPtrToPtr", "Transmute": "castKindTransmute", "PointerWithExposedProvenance": "castKindPointerWithExposedProvenance"}[pointer_cast[3]]
            return f"rvalueCast({kind}, {self.operand(pointer_cast[1])}, {self.ty(pointer_cast[2])})"
        return f"rvalueUse({self.operand(text)})"

    @staticmethod
    def unwind(text: str) -> str:
        if text == "unwind continue":
            return "unwindActionContinue"
        if text in ("unwind unreachable", "unwind terminate(abi)"):
            return "unwindActionUnreachable" if text.endswith("unreachable") else "unwindActionTerminate"
        raise FrontendError(f"Unsupported unwind action: {text}")

    def terminator(self, text: str) -> str:
        if text in ("return", "unreachable", "resume"):
            return "terminatorKind" + text.title()
        goto = re.fullmatch(r"goto -> bb(\d+)", text)
        if goto:
            return f"terminatorKindGoto(basicBlockIdx({goto[1]}))"
        switch = re.fullmatch(r"switchInt\((.*)\) -> \[(.*)\]", text)
        if switch:
            branches, otherwise = [], None
            for case in split(switch[2]):
                target = re.fullmatch(r"(-?\d+|otherwise): bb(\d+)", case)
                if not target:
                    raise FrontendError(f"Unsupported switch branch: {case}")
                if target[1] == "otherwise":
                    otherwise = f"basicBlockIdx({target[2]})"
                else:
                    branches.append(f"branch({target[1]}, basicBlockIdx({target[2]}))")
            if otherwise is None:
                raise FrontendError("switchInt has no otherwise branch")
            return f"terminatorKindSwitchInt({self.operand(switch[1])}, switchTargets({' '.join(branches) or '.Branches'}, {otherwise}))"
        assertion = re.fullmatch(r"assert\((.*)\) -> \[success: bb(\d+), (.*)\]", text)
        if assertion:
            args = split(assertion[1])
            condition = args[0]
            expected = "false" if condition.startswith("!") else "true"
            if expected == "false":
                condition = condition[1:]
            # Preserve the compiler's message without trying to interpret its
            # formatting arguments; the condition drives assertion behavior.
            message = f"assertMessageOther(opaque({quote(', '.join(args[1:]))}))"
            return f"assert({self.operand(condition)}, {expected}, {message}, basicBlockIdx({assertion[2]}), {self.unwind(assertion[3])})"
        call = re.fullmatch(r"(.+?) = ([\w:]+)\((.*)\) -> \[return: bb(\d+), (.*)\]", text)
        if call:
            self.function(call[2])
            call_args = split(call[3])
            for arg in call_args:
                if re.match(r"(?:copy|move) ", arg) and not re.fullmatch(r"(?:copy|move) _\d+", arg):
                    raise FrontendError(f"Projected call argument is not supported by the current semantics: {arg}; assign it to a local first")
            operands = ", ".join(self.operand(arg) for arg in call_args) or ".Operands"
            return (f"terminatorKindCall({self.fn_constant(f'ty({self.function_ids[call[2]]})')}, "
                    f"({operands}), {self.place(call[1])}, someBasicBlockIdx(basicBlockIdx({call[4]})), {self.unwind(call[5])})")
        raise FrontendError(f"Unsupported MIR terminator: {text}")

    def function(self, name: str) -> None:
        if name in self.function_ids:
            return
        if name not in self.functions and name.split("::")[-1] in self.externs:
            self.function_ids[name] = 100000 + len(self.function_ids)
            self.externals[name] = name.split("::")[-1]
            return
        if name not in self.functions:
            raise FrontendError(f"Called function {name!r} has no MIR body (external/generic calls are unsupported)")
        fn = self.functions[name]
        self.function_ids[name] = 100000 + len(self.function_ids)
        if not fn.blocks or sorted(fn.blocks) != list(range(len(fn.blocks))):
            raise FrontendError(f"{name}: expected contiguous basic blocks starting at bb0")
        if sorted(fn.locals) != list(range(len(fn.locals))):
            raise FrontendError(f"{name}: local declarations are not contiguous")
        for ty in fn.locals.values():
            self.ty(ty)
        blocks = []
        for index, lines in sorted(fn.blocks.items()):
            try:
                statements = []
                for line in lines[:-1]:
                    assignment = re.fullmatch(r"(.+?) = (.+)", line)
                    storage = re.fullmatch(r"Storage(Live|Dead)\(_(\d+)\)", line)
                    if assignment:
                        kind = f"statementKindAssign({self.place(assignment[1])}, {self.rvalue(assignment[2])})"
                    elif storage:
                        kind = f"statementKindStorage{storage[1]}(local({storage[2]}))"
                    elif line == "nop":
                        kind = "statementKindNop"
                    else:
                        raise FrontendError(f"Unsupported MIR statement: {line}")
                    statements.append(f"statement({kind}, span(0))")
                terminal = self.terminator(lines[-1])
                blocks.append(f"basicBlock({' '.join(statements) or '.Statements'}, terminator({terminal}, span(0)))")
            except (FrontendError, IndexError) as error:
                raise FrontendError(f"{name}, bb{index}: {error}") from error
        decls = " ".join(f"localDecl({self.ty(ty)}, span(0), mutabilityMut)" for _, ty in sorted(fn.locals.items()))
        self.bodies[name] = (f"monoItem(symbol({quote(name)}), monoItemFn(symbol({quote(name)}), defId({self.function_ids[name]}), "
                             f"someBody(body({' | '.join(blocks)}, {decls}, {len(fn.params)}, opaque(\"\"), noLocal, span(0)))))")

    def program(self, entry: str) -> str:
        self.function(entry)
        functions = " ".join(f"functionName(ty({index}), functionNormalSym(symbol({quote(self.externals.get(name, name))})))" for name, index in self.function_ids.items())
        mappings = " ".join(f"TypeMapping(ty({index}), {self.type_info(name)})" for name, index in list(self.types.items()))
        mappings += " " + " ".join(f"TypeMapping(ty({index}), typeInfoFunType({quote(name)}))" for name, index in self.function_ids.items())
        return f"pgm(symbol(\"rustc-mir\"), .GlobalAllocs, {functions}, {' '.join(self.bodies.values())}, {mappings}, machineInfo(opaque(\"x86_64\")))"


def argument(value: str, ty: str) -> str:
    if ty == "bool":
        if value not in ("true", "false"):
            raise FrontendError(f"Boolean argument must be true or false, got {value!r}")
        return f"BoolVal({value})"
    width, signed = int_info(ty)
    number = int(value, 0)
    if not (-(1 << (width - 1)) if signed else 0) <= number <= (1 << (width - int(signed))) - 1:
        raise FrontendError(f"Argument {value} outside {ty}")
    return f"Integer({number}, {width}, {str(signed).lower()})"


def load_extensions(paths: list[Path]) -> tuple[list[Path], list[str]]:
    """Read the extensions: Maude files loaded after the model.  The program module imports every
    module that an extension defines.  An extension adds its foreign functions with rustApi/rustSig
    equations and its objects of the initial configuration with an equation for rustExtObjects
    (lang/rust/tee-support.maude).  Returns the files and the modules."""
    loads, imports = [], []
    for path in paths:
        path = path.resolve(strict=True)
        code = "\n".join(re.split(r"---|\*\*\*", line, maxsplit=1)[0] for line in path.read_text().splitlines())
        modules = re.findall(r"^\s*(?:fmod|mod|omod)\s+([A-Za-z0-9-]+)\s+is\b", code, re.M)
        if not modules:
            raise FrontendError(f"{path}: defines no module")
        loads.append(path)
        imports += modules
    return loads, imports

def run(command: list[str], timeout: float) -> subprocess.CompletedProcess:
    # No stdin: Maude reads commands from stdin after the file, and would wait on an open one.
    return subprocess.run(command, capture_output=True, text=True, timeout=timeout, stdin=subprocess.DEVNULL)


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("source", type=Path)
    parser.add_argument("--function", "-f", help="Explicit function-level evaluation instead of the default main entry")
    parser.add_argument("--arg", action="append", default=[], help="Integer/bool argument; repeat for multiple parameters")
    parser.add_argument("--output-dir", type=Path, help="Keep MIR, Maude module, compiler version and execution log here")
    parser.add_argument("--emit-only", action="store_true", help="Write the runnable program.maude without launching Maude")
    parser.add_argument("--ta", action="append", default=[], metavar="ID[=FILE.rs]", help="Register cmd_N services from this source or another Rust file (repeatable)")
    parser.add_argument("--role", choices=("ta", "ra"), default="ta", help="Run TEE FFI as a trusted app or a REE client (default: ta)")
    parser.add_argument("--ext", action="append", default=[], type=Path, metavar="FILE.maude",
                        help="Model extension loaded after the model; the program imports its modules.  It defines its foreign functions with rustApi/rustSig equations and its initial objects with an equation for rustExtObjects (repeatable)")
    parser.add_argument("--rustc", default=os.environ.get("RUSTC", "rustc"))
    parser.add_argument("--maude", default=os.environ.get("MAUDE_BIN"), help="Maude executable (also reads MAUDE in config.mk)")
    parser.add_argument("--timeout", type=float, default=60, help="Timeout in seconds for each compiler/evaluator invocation")
    args = parser.parse_args()
    try:
        ext_loads, ext_imports = load_extensions(args.ext)
        source = args.source.resolve(strict=True)
        if args.timeout <= 0:
            raise FrontendError("--timeout must be positive")
        maude = args.maude
        if not maude and (ROOT / "config.mk").exists():
            setting = re.search(r"^MAUDE\s*:?=\s*(.+)$", (ROOT / "config.mk").read_text(), re.M)
            if setting:
                maude = setting[1].strip()
        maude = maude or shutil.which("maude")
        if not maude and not args.emit_only:
            raise FrontendError("Maude not found; use --maude PATH or set MAUDE_BIN")
        work = args.output_dir.resolve() if args.output_dir else ROOT / "build" / "rust2maude" / source.stem
        work.mkdir(parents=True, exist_ok=True)
        # Each invocation uses a fresh directory; a failed run cannot appear to
        # succeed by reading output left by an earlier invocation.
        with tempfile.TemporaryDirectory(prefix=".run-", dir=work) as temporary:
            temp = Path(temporary)
            mir_path = temp / "program.mir"
            version = run([args.rustc, "--version"], args.timeout)
            if version.returncode:
                raise FrontendError(version.stderr.strip())
            (work / "rustc-version.txt").write_text(version.stdout)
            target = run([args.rustc, "--print", "cfg"], args.timeout)
            if target.returncode or 'target_pointer_width="64"' not in target.stdout or 'target_endian="little"' not in target.stdout:
                raise FrontendError("The current Maude frontend requires a 64-bit little-endian Rust target")
            compiled = run([args.rustc, str(source), "--crate-name", "maude_input", "--edition=2021", "--crate-type=lib", "--emit=mir",
                            "-C", "opt-level=0", "-C", "overflow-checks=yes", "-o", str(mir_path)], args.timeout)
            if compiled.returncode:
                raise FrontendError("rustc failed:\n" + compiled.stderr.strip())
            shutil.copyfile(mir_path, work / "program.mir")
            mir_text = mir_path.read_text()
            functions = read_functions(mir_text, impl_types(source))
            entry = args.function or "main"
            if entry not in functions:
                raise FrontendError(f"Function {entry!r} not found. Available: {', '.join(functions) or '(none)'}")
            fn = functions[entry]
            if len(args.arg) != len(fn.params):
                raise FrontendError(f"{entry} expects {len(fn.params)} argument(s); received {len(args.arg)}. Use --arg VALUE for each parameter")
            # Named structs use source field order. The Rust compiler validates
            # their declarations; MIR supplies all actual local/projection types.
            translator = Translator(functions, read_structs(source), read_constants(mir_text), read_externs(source))
            registrations = {}
            target_definitions = []
            if any("=" not in registration for registration in args.ta):
                for name in functions:
                    if re.fullmatch(r"cmd_\d+", name):
                        translator.function(name)
            pgm = translator.program(entry)
            for registration in args.ta:
                parts = registration.split("=", 1)
                identity = int(parts[0])
                if identity < 0 or identity in registrations:
                    raise FrontendError(f"Invalid or duplicate TA ID: {identity}")
                if len(parts) == 1:
                    registrations[identity] = "sourceProgram"
                    continue
                target_source = Path(parts[1]).resolve(strict=True)
                target_mir = temp / f"ta-{identity}.mir"
                target_compile = run([args.rustc, str(target_source), "--crate-name", f"ta_{identity}", "--edition=2021", "--crate-type=lib", "--emit=mir",
                                      "-C", "opt-level=0", "-C", "overflow-checks=yes", "-o", str(target_mir)], args.timeout)
                if target_compile.returncode:
                    raise FrontendError(f"TA {identity} compilation failed:\n{target_compile.stderr}")
                shutil.copyfile(target_mir, work / target_mir.name)
                target_text = target_mir.read_text()
                target_functions = read_functions(target_text, impl_types(target_source))
                commands = [name for name in target_functions if re.fullmatch(r"cmd_\d+", name)]
                if not commands:
                    raise FrontendError(f"TA {identity}: no cmd_N service found in {target_source}")
                target_translator = Translator(target_functions, read_structs(target_source), read_constants(target_text), read_externs(target_source))
                for command in commands:
                    target_translator.function(command)
                target_pgm = target_translator.program(commands[0])
                program_name = f"targetProgram{identity}"
                registrations[identity] = program_name
                target_definitions.append(f"op {program_name} : -> Pgm . eq {program_name} = {target_pgm} .")
            locals_ = [f"ListItem(newLocal({translator.ty(fn.result)}, mutabilityMut))"]
            for value, (_, ty) in zip(args.arg, fn.params):
                locals_.append(f"ListItem(typedValue({argument(value, ty)}, {translator.ty(ty)}, mutabilityNot))")
            imports = f'''load {quote(str(ROOT / 'src/k-prelude.maude'))}
load {quote(str(ROOT / 'src/tee-api.maude'))}
load {quote(str(ROOT / 'src/lang.maude'))}
load {quote(str(ROOT / 'lang/rust/semantics.maude'))}'''
            runtime_imports, runtime_equations = "", ""
            initial = f"#callConfig(sourceProgram, ty({translator.function_ids[entry]}), {' ; '.join(locals_)})"
            if translator.externals:
                built = run(["make", "--no-print-directory", "-C", str(ROOT), "NAME=rust-pipeline", "src",
                             "CRYPTO=1", "TRANSIENT=1", "PERSISTENT=1", "GENERIC=1", "DATA_STREAM=1", "ENUMERATOR=1", "SHM=1", "WITHOUT="], args.timeout)
                if built.returncode:
                    raise FrontendError("TEE model build failed:\n" + built.stdout + built.stderr)
                imports = "\n".join(f"load {quote(str(path))}" for path in [ROOT / "build/rust-pipeline/src/main.maude", *ext_loads])
                runtime_imports = " ".join(f"pr {name} ." for name in ["TEE-BEHAVIOR", "TEE-RUST-BEHAVIOR", *ext_imports])
                runtime_equations = '''
  op runnerTa : -> Oid [ctor] . op runnerOs : -> Oid [ctor] .
  op evaluate : Configuration -> EvaluationResult .
  op teePanicked : -> EvaluationResult [ctor] .
  var C : Configuration . var AS : AttributeSet .
  eq evaluate(< runnerTa : TrustedApp | proc : (k(.K) retVal(return(V)) KS), status : (normal).AppStatus, api-call : noCall, AS:AttributeSet > C)
    = evaluated(V) .
  eq evaluate(< runnerTa : TrustedApp | proc : (k(.K) retVal(noReturn) KS), status : (normal).AppStatus, api-call : noCall, AS:AttributeSet > C)
    = completed .
  eq evaluate(< runnerTa : TrustedApp | status : panic, AS:AttributeSet > C) = teePanicked .
  eq evaluate(< runnerTa : TrustedApp | proc : (k(#ProgramError(E) ~> K) KS), AS:AttributeSet > C) = failed(E) .
  eq evaluate(< runnerTa : RichApp | proc : (k(.K) retVal(return(V)) KS), AS:AttributeSet > C) = evaluated(V) .
  eq evaluate(< runnerTa : RichApp | proc : (k(.K) retVal(noReturn) KS), AS:AttributeSet > C) = completed .
  eq evaluate(< runnerTa : RichApp | proc : (k(#ProgramError(E) ~> K) KS), AS:AttributeSet > C) = failed(E) .
'''
                if args.role == "ra":
                    initial = f"< runnerTa : RichApp | proc : ({initial}), prog : sourceProgram >"
                else:
                    initial = (f"< runnerTa : TrustedApp | proc : ({initial}), prog : sourceProgram, client-service : none, "
                           "status : (normal).AppStatus, api-call : noCall, counter : 1, storage : oid(0, runnerTa), os-kernel : runnerOs > "
                           "< oid(0, runnerTa) : Storage | status : (normal).StorageStatus, files : empty, counter : 100 >")
                registry = ", ".join(f"rustApp({identity}) |-> taConfig({program}, true, true)" for identity, program in registrations.items()) or "empty"
                initial += (" < runnerOs : TrustedOS | service-in : nil, service-out : nil, service-request-ta-inst : noOid, "
                            f"ta-config : ({registry}), ta-instances : empty, ta-storages : empty, session-counter : 0 >")
                initial += " rustExtObjects"
            elif args.ext:
                raise FrontendError("--ext needs the TEE model: the program calls no TEE API function")
            # Only the language-independent prelude and MIR semantics are needed.
            # Do not import the TA adapter, which consumes #EndProgram.
            module = f'''--- Generated from {source.name}; compiler: {version.stdout.strip()}
{imports}
mod RUST-SOURCE-RUN is
  pr RUST-SEMANTICS .
  {runtime_imports}
  op sourceProgram : -> Pgm .
  eq sourceProgram = {pgm} .
  {' '.join(target_definitions)}
  sort EvaluationResult .
  op evaluate : KConfig -> EvaluationResult .
  op evaluated : Value -> EvaluationResult [ctor] .
  op completed : -> EvaluationResult [ctor] .
  op failed : MIRError -> EvaluationResult [ctor] .
  var V : Value . var KS : KConfig . var K : K . var E : MIRError .
  eq evaluate(k(#EndProgram) retVal(return(V)) KS) = evaluated(V) .
  eq evaluate(k(#EndProgram) retVal(noReturn) KS) = completed .
  eq evaluate(k(#ProgramError(E) ~> K) KS) = failed(E) .
  {runtime_equations}
endm
rew in RUST-SOURCE-RUN : evaluate({initial}) .
'''
            module_path = work / "program.maude"
            module_path.write_text(module)
            print(f"rustc: {version.stdout.strip()}", flush=True)
            print(f"Artifacts: {work}", flush=True)
            print(f"Maude file: {module_path}", flush=True)
            if args.emit_only:
                print("Generated runnable Maude file (evaluation skipped)")
                return 0
            print(f"Evaluating {entry}({', '.join(args.arg)}) in Maude ({len(translator.bodies)} MIR function(s))", flush=True)
            result = run([maude, "-no-banner", "-no-advise", "-batch", str(module_path)], args.timeout)
            (work / "maude.log").write_text(result.stdout + result.stderr)
            if result.returncode or result.stderr.strip() or re.search(r"^(Warning|Error):", result.stdout, re.M):
                raise FrontendError(f"Maude load/evaluation failed; see {work / 'maude.log'}\n{result.stderr.strip()}")
            output = re.search(r"^result EvaluationResult: (.*?)(?=\nBye\.|\Z)", result.stdout, re.M | re.S)
            normalized = re.sub(r"\s+", " ", output[1]).strip() if output else ""
            normalized = re.sub(r"\(\s+", "(", normalized)
            normalized = re.sub(r"\s+\)", ")", normalized)
            if normalized.startswith("evaluated(") or normalized == "completed":
                print(normalized)
                return 0
            if normalized.startswith("failed("):
                raise FrontendError(f"MIR execution failed: {normalized}")
            if normalized == "teePanicked":
                raise FrontendError("TEE execution panicked")
            raise FrontendError(f"MIR execution is stuck or incomplete; see {work / 'maude.log'}")
    except (FrontendError, OSError, ValueError, subprocess.TimeoutExpired) as error:
        print(f"error: {error}", file=sys.stderr)
        return 1


if __name__ == "__main__":
    sys.exit(main())
