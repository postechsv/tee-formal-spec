# TEE Formal Specification

An executable [Maude](https://maude.cs.illinois.edu/) specification of the GlobalPlatform TEE Internal Core API. It models successful API calls, error returns, panics, storage conditions, and the state transitions of cryptographic operations, together with the trusted application (TA) kernel and the storage and OS services that the API calls depend on. `src/main.maude` is the entry point for the specification.

## Features

- Generic, transient, and persistent object APIs
- Persistent data stream access and persistent object enumeration
- Cryptographic operations (switchable with the `CRYPTO` feature)
- Programmer errors and invalid handles, modeled as panics
- Model programs (`src/imp.maude`) whose calls are translated into TEE API calls (`src/tee-imp.maude`)
- Build variants that drop feature groups or individual modules

## Requirements

- Maude 3
- GNU Make
- Python 3 (for the test runners and the DM-Check generator)

`make` reads the Maude path from `MAUDE` in a `config.mk` file at the repository root. This file is gitignored, so each user creates it:

```make
# config.mk
MAUDE := /path/to/maude
```

Alternatively, set the `MAUDE_BIN` environment variable. The Python test runner looks for Maude in this order: `MAUDE_BIN`, a sibling `tee-formal-analysis/3rd_party` (or `zenodo/tee-formal-analysis/3rd_party`) binary, then `maude` on `PATH`.

## Quick start

Build the default configuration and run both test suites:

```bash
make            # build build/default/src and build/default/tests
make test       # build, then run the API suite and the check suite
make help       # list features, test selection, and current values
```

Build variants use a separate build directory selected with `NAME`:

```bash
make NAME=nocrypto CRYPTO=0 test   # without the cryptographic operation APIs
make NAME=t WITHOUT=MODULE         # drop a module; MODULE must be a pr line in src/main.maude
make DMC=1                         # DM-Check-compatible build (tools/gen-dmc.py)
make V=1 test                      # stream the Maude output of each test file
```

Run one suite or clean up:

```bash
make test-api                # API-level black-box searches (tests/api)
make test-checks             # completion and GlobalPlatform conformance checks
make check-dmc               # regenerate the DM-Check build and run its ground checks
make clean NAME=default      # remove build/default
make distclean               # remove build/
```

Each build directory `build/$(NAME)/` holds a copy of the selected `src/` and `tests/` trees (or the DM-Check output with `DMC=1`) and the settings used for the build. Settings are cached in `build/$(NAME)/config.mk`.

## Feature selection

Features are defined in `mk/features.mk`. Each one is a variable set to `1` (on, the default) or `0` (off), for example `make CRYPTO=0`.

| Feature | Covers | Requires |
| --- | --- | --- |
| `CRYPTO` | Cryptographic operation APIs | `TRANSIENT` |
| `TRANSIENT` | Transient object API | |
| `PERSISTENT` | Persistent object API and storage errors | |
| `GENERIC` | Generic object API | |
| `DATA_STREAM` | Persistent data stream access | `PERSISTENT` |
| `ENUMERATOR` | Persistent object enumeration | `PERSISTENT` |

When a feature is off, its files are dropped from `src/main.maude` and its tests are skipped.

## Repository layout

```text
tee-formal-spec/
├── Makefile              # build driver and test targets
├── mk/
│   └── features.mk       # feature table
├── src/                  # Maude specification
├── tests/
│   ├── api/              # API-level black-box searches (test-*.maude)
│   ├── cases/            # API-specific test cases (not run by make)
│   ├── completion/       # completion regressions, one file per API group
│   ├── gp-conformance/   # GlobalPlatform conformance reductions, one file per module group
│   ├── run-api-suite.py  # runner for tests/api
│   └── run-checks.py     # runner for completion/ and gp-conformance/
├── tools/
│   ├── select-main.awk   # filter that builds src/main.maude for a feature selection
│   ├── gen-dmc.py        # DM-Check generator
│   └── gen-docs.py       # documentation site generator (make docs)
├── LICENSE
└── README.md
```

`build/` is created by `make` and is not tracked.

### API behavior models

| File | Purpose |
| --- | --- |
| `generic.maude` | Generic Object API |
| `transient.maude` | Transient Object API |
| `persistent.maude` | Persistent Object API and storage errors |
| `data-stream.maude` | Persistent data stream access |
| `enumerator.maude` | Persistent object enumeration |
| `cryptographic.maude` | Cryptographic operations and related APIs |

### Common objects and TEE infrastructure

| File | Purpose |
| --- | --- |
| `objects.maude` | Common storage object classes and helper functions |
| `transient-object.maude`, `persistent-object.maude` | Transient and persistent object classes, shared by the storage APIs |
| `handle.maude` | Object, enumerator, operation, and session handles |
| `constants.maude` | GlobalPlatform constants, types, key sizes, and usages |
| `tee-data-type.maude` | Models of GlobalPlatform API data structures |
| `tee-api.maude`, `tee-api-call.maude` | API identifiers, call parameters, and return values |
| `tee-msg.maude` | Message layer of the TEE API models |
| `session.maude` | Session handling |
| `tee-kernel.maude` | TA kernel and API execution state |
| `tee-panic.maude` | General programmer errors and invalid-handle panics |
| `persistent-panic.maude`, `data-stream-panic.maude`, `crypto-panic.maude` | Panics of the storage and cryptographic APIs |
| `os.maude`, `os-service.maude` | Trusted OS and storage services |
| `app.maude`, `ree-app.maude`, `tee-app.maude`, `tee-client.maude` | REE/TEE applications and client behavior |

### Program execution

| File | Purpose |
| --- | --- |
| `k-prelude.maude` | Foundations for the K-style execution semantics |
| `imp.maude` | Syntax and semantics of model programs |
| `tee-imp.maude` | Translation of program calls into TEE API calls |

## Core modeling principles

### Handles

Handles are top-level objects in the configuration rather than nested inside their owners. No object class has a handle-valued attribute, and the handle constructors create top-level objects.

- Each `Handle` has an `oid` that names the object it belongs to.
- A persistent object may have several open handles. A new handle is added only when its sharing flags are compatible with the handles already open on the object.
- Closing a handle removes only that handle. The object and its other handles remain.
- Close-and-delete requires the handle to carry the write-meta access right. On success, all handles of the object are removed, the object is removed together with its embedded data stream, and the storage service is notified to delete the file. Otherwise the TA panics and the object is not deleted.

### Panic semantics

`tee-panic.maude` evaluates invalid handles and other GlobalPlatform programmer errors against the active `TAKernel` call state and top-level handle objects. A panic path converges to `status : panic` on the `TAKernel`, with the API call in a finished state.

## Documentation site

`make docs` builds a static HTML site from `src/` into `build/docs/` (generated by `tools/gen-docs.py`, standard library only):

```bash
make docs                                     # build build/docs and check its links
xdg-open build/docs/index.html                # or open the file in a browser
python3 tools/gen-docs.py --src src --out build/docs --check   # the same, run directly
```

The index page's body is the hand-written fragment `docs/overview.html`, in which `[[op:Name]]`-style macros (`mod`, `sort`, `class`, `op`, `rule`, `attr`, `file`; optional `|text`) become links and unknown targets fail the build; the generator adds the site chrome and a table of the files. The site opens from `file://` and needs no network access. It has one page per `src/*.maude` file, where every identifier links to its definition and doc comments appear as prose; and index pages for modules and views, sorts and classes (with class attributes), operators, and rule and equation labels. `--check` verifies every local link and `#anchor`, and exits nonzero if one is missing.

## Testing

The test groups are:

- `tests/api`: black-box searches that verify external API calls and returns (`make test-api`)
- `tests/completion/<group>.maude`: checks that completed paths converge to final success, error, or panic states (`make test-checks`)
- `tests/gp-conformance/<group>.maude`: focused reductions derived from GlobalPlatform tables and clauses (`make test-checks`)
- `tests/cases`: API-specific test cases; not run by the make targets

`make test` runs the API suite and the check suite. Each runs even if the other fails, and the exit status is nonzero if either fails. Without `V=1`, only summary lines are printed, for example `[test-suite] API-level: ...` and `[checks] FILE: ...`.

## License

See [LICENSE](LICENSE) for the applicable license terms.
