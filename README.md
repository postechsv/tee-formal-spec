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
│   ├── completion/       # Visible/invisible completion regressions, one file per API group
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
| `imp.maude` | Syntax and semantics of model programs |
| `tee-imp.maude` | Translation of program calls into TEE API calls |

## Core Modeling Principles

### Top-Level Handle Ownership

Handles are represented as independent top-level objects in a configuration rather than being nested inside their owners.

- An `OpenableObject` records only the IDs of its handles in `handle-id-set`.
- Each `Handle` refers to its owner through `oid`.
- A handle is never represented as both a nested object and a top-level object.
- A persistent object may own multiple handles.
- A normal close removes only the specified handle.
- Close-and-delete removes all handles and the stream before deleting the owning object.

### Visible and Invisible Reduction

- Regular behavior modules expose API execution through messages and rewrite rules.
- `*-INVISIBLE` modules provide equation-based reductions with the same observable results.
- Both paths must agree on successful returns, error codes, panics, and state transitions.

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
