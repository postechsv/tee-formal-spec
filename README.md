# TEE Formal Specification

This repository contains an executable Maude specification of the GlobalPlatform TEE Internal Core API. Based on the object and handle model presented in the accompanying research paper, it models successful API calls, error returns, panics, storage conditions, and cryptographic operation state transitions.

## Reference Material

The specification and its tests are developed against the following sources:

- The TEE API formal model and class structure presented in the research paper
- GlobalPlatform TEE Internal Core API Specification v1.3.1
- The model and analysis wrapper from the original artifact

`src/main.maude` is the entry point for the active specification.

## Repository Structure

```text
tee-formal-spec/
├── src/                  # Maude specification and executable semantics
├── tests/
│   ├── api/              # API-level black-box regression searches
│   ├── cases/            # Existing test cases grouped by API area
│   ├── completion/       # Completion regressions, one file per API group
│   ├── gp-conformance/   # GlobalPlatform conformance reductions, one file per module group
│   ├── run-api-suite.py  # Runner for tests/api
│   └── run-checks.py     # Runner for completion/ and gp-conformance/
├── mk/features.mk        # Build features (e.g. CRYPTO) toggled by make
├── tools/                # main.maude filter and the DMC generator (gen-dmc.py)
├── Makefile
├── LICENSE
└── README.md
```

### API Behavior Models

| File | Purpose |
| --- | --- |
| `generic.maude` | Generic Object API |
| `transient.maude` | Transient Object API |
| `persistent.maude` | Persistent Object API and storage errors |
| `data-stream.maude` | Persistent data stream access |
| `enumerator.maude` | Persistent object enumeration |
| `cryptographic.maude` | Cryptographic operations and related APIs |

### Common Objects and TEE Infrastructure

| File | Purpose |
| --- | --- |
| `objects.maude` | Common storage object classes and helper functions |
| `handle.maude` | Object, enumerator, operation, and session handles |
| `constants.maude` | GlobalPlatform constants, types, key sizes, and usages |
| `tee-data-type.maude` | Models of GlobalPlatform API data structures |
| `tee-api.maude`, `tee-api-call.maude` | API identifiers, call parameters, and return values |
| `tee-kernel.maude` | TA kernel and API execution state |
| `tee-panic.maude` | Programmer errors and invalid-handle panics (general rules; the persistent and data stream access rules are in `persistent-panic.maude` and `data-stream-panic.maude`) |
| `transient-object.maude`, `persistent-object.maude` | Transient and persistent object classes, shared by the storage APIs |
| `os.maude`, `os-service.maude` | Trusted OS and storage services |
| `app.maude`, `ree-app.maude`, `tee-app.maude`, `tee-client.maude` | REE/TEE applications and client behavior |

### Program Execution Semantics

| File | Purpose |
| --- | --- |
| `k-prelude.maude` | Foundations for the K-style execution semantics |
| `imp.maude` | Syntax, static type system (`wellTyped`), and semantics of model programs |
| `tee-imp.maude` | Translation of program calls into TEE API calls |

The semantics of `imp.maude` is in the style of the C semantics of Ellison and Rosu (POPL 2012), with these choices:

- **Typing.** `mkInitConf` runs a program only if `wellTyped` accepts it, so an ill-typed program has no initial configuration. Declarations are checked in sequence; a by-reference parameter takes an lvalue of its type; a struct is assigned only from a struct of the same type.
- **Scoping.** A function body sees its parameters, its own locals and the globals declared before it. Each `if` and `while` body is a block: the environment is saved on entry (`$restoreEnv`) and restored on exit.
- **Declarations are hoisted (C11 6.2.4p6).** On entry to a function body (after the parameters are bound), to an `if`/`while` body, and to the top level, every `T X ;` directly in that statement sequence (also after a label) is allocated and bound at once; a nested block hoists on its own entry. A declaration reached in the normal flow resets its object to undef and does not allocate again (`T X ::= E ;` is `T X ; X := E ;`). The top level is hoisted too, so a goto past a top-level declaration sees the object. Consequences: a use of a name before its declaration in the same block is rejected by the checker when that name is also a global or outer binding (it would name the outer one in C), because the hoisted local would capture it; a parameter is not a hoisted declaration, and a parameter may shadow a global. Reaching a declaration again (a backward goto) resets it to undef; in C a declaration without an initializer does not change the value, so this follows the chosen model rather than the standard's wording.
- **Goto.** A label is a direct element of the top-level sequence or of a function body. It maps to the labelled statement followed by the rest of its sequence, so a goto continues with that rest, and a function's labels are its own. A goto may leave nested blocks (the environment of the outermost block left is restored), but it cannot enter a block: labels inside `if`/`while` bodies are rejected. A goto to an unknown label is rejected by the checker and is stuck at runtime.
- **Internal items and the library trust boundary.** The computation items of the semantics (`$push`, `$args`, `$bind`, `$exec`, `$alloc`, `$reset`, `$waitSysCall`, `$in`, `$out`, ...) have their own sort `IStmt`, a subsort of `K` but not of `Stmt` or `Program`, so a user program cannot contain them (`IMP-INTERNAL` in `imp.maude`). A TEE API stub is a trusted library function `$libFun(FSIG, K)`, a `Stmt` whose body is internal K. The checker takes `$libFun` only for a TEE API name, and a user function cannot take such a name. Residual risk: a program that is not the syscall prefix can still write `$libFun` for a TEE API name, since the prefix and a program are both `Program`s; the sorts alone cannot tell them apart. The syscall prefix (`syscalls`, `tee-imp.maude`) is library code and a user program must not contain `$libFun`.
- **Evaluation order.** Operands are evaluated left to right, and `&&`/`||` do not evaluate their right operand when the left one decides. A read of an uninitialized variable, division by zero and other undefined operations are stuck.
- **Equations and rules.** Local, deterministic, terminating steps (expressions, assignment, declarations, blocks, sequencing, labels) are equations. Rules are kept for while unrolling (it can run forever), function call entry, argument binding and return (they change the frame stack), and every step that interacts with the TEE API (`tee-app.maude`, `tee-api-call.maude`, `$waitSysCall`, `$in`, `$out`).
- **Configuration.** The configuration is a flat soup of cells; AC matching gives K-style cell locality.

## Core Modeling Principles

### Top-Level Handle Ownership

Handles are represented as independent top-level objects in a configuration rather than being nested inside their owners.

- An `OpenableObject` records only the IDs of its handles in `handle-id-set`.
- Each `Handle` refers to its owner through `oid`.
- A handle is never represented as both a nested object and a top-level object.
- A persistent object may own multiple handles.
- A normal close removes only the specified handle.
- Close-and-delete removes all handles and the stream before deleting the owning object.

### Panic Semantics

`tee-panic.maude` evaluates invalid handles and other GlobalPlatform programmer errors against the active `TaKernel` call state and top-level handle objects. A panic path must converge to `app-status: panic` with the API in a finished state.

## Requirements

- A Maude 3 executable
- Python 3 for the API regression runner

The API runner locates Maude in the following order:

1. The `MAUDE_BIN` environment variable
2. A sibling `tee-formal-analysis/3rd_party` artifact
3. A sibling `zenodo/tee-formal-analysis/3rd_party` artifact
4. A `maude` executable on `PATH`

## Running the Tests

Builds and tests are driven by `make` (GNU Make). Put the Maude path in a `config.mk` at the repository root (gitignored), for example `MAUDE := /path/to/maude`, or set `MAUDE_BIN`.

```bash
make test                  # build build/default and run every test suite
make NAME=nocrypto CRYPTO=0 test   # same, without the cryptographic operation APIs
make WITHOUT="M1 M2"       # drop modules (pr lines) from main.maude
make DMC=1 test           # DM-Check-compatible build (gen-dmc output), then the test suites
make V=1 test             # verbose: stream the Maude output of each test file as it runs
make check-dmc             # regenerate the DMC build and run gen-dmc's ground checks
make help                  # features, test selection, targets
```

`V=1` (or `VERBOSE=1`) shows the full Maude output of each API test file and each check file while it runs, and echoes the build commands. Without it, only the summary lines (`[test-suite] API-level: ...`, `[checks] FILE: ...`) are printed. `V` is a display option: it is not cached in `build/$(NAME)/config.mk` and does not trigger a rebuild. The runners also accept `-v` directly, e.g. `python3 tests/run-checks.py -v FILE...`.

Each build lives in `build/$(NAME)/` with `src/` and `tests/` holding the final tree (a copy of the selected sources, or the DM-Check output with `DMC=1`), and its settings are remembered in `build/$(NAME)/config.mk`. With `DMC=1` the generator's bookkeeping (reflection cache and `manifest.json`) is kept in `build/$(NAME)/.dmc-cache/`, outside `src/`, and survives rebuilds until `make clean`. Features and the tests that need them are declared in `mk/features.mk`; tests of disabled features are skipped.

With crypto enabled the suites run 92 API-level searches, 108 completion searches, and 169 conformance reductions.

The test groups serve the following purposes:

- `tests/api`: black-box searches that verify external API calls and returns
- `tests/completion/`: checks that completed paths converge to final success, error, or panic states
- `tests/gp-conformance/`: focused reductions derived from GlobalPlatform tables and clauses
- `tests/cases`: API-specific test cases inherited from the original artifact

## License

See [LICENSE](LICENSE) for the applicable license terms.
