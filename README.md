# TEE Formal Specification

An executable [Maude](https://maude.cs.illinois.edu/) specification of the GlobalPlatform TEE Internal Core API. It models successful API calls, error returns, panics, storage conditions, and the state transitions of cryptographic operations, together with the trusted application (TA) kernel and the storage and OS services that the API calls depend on.

This repository holds the specification, its build (feature selection), its test suites and its documentation site. Transforming it (DM-Check, maude2lean, ghost composition) and analyzing applications on top of it is done in [tee-formal-analysis](https://github.com/postechsv/tee-formal-analysis), which fetches a revision of this repository and builds it with `make`.

## Paper

Geunyeol Yu, Seunghyun Chae, Kyungmin Bae, Sungkun Moon.
[Formal Specification of Trusted Execution Environment APIs](https://doi.org/10.1007/978-3-031-57259-3_5).
FASE 2024, LNCS, Springer.

## Features

- Generic, transient, and persistent object APIs
- Persistent data stream access and persistent object enumeration
- Shared memory of a client and a TA: the TA runs on the memory that the client passes with the call
- Cryptographic operations
- Programmer errors and invalid handles, modeled as panics
- Model programs (`lang/c/semantics.maude`) whose calls are translated into TEE API calls (`lang/c/tee-support.maude`)

## Using the specification

The entry point is `src/main.maude`, with every feature on; a build of a feature configuration filters it into `build/<NAME>/src/main.maude` (GNU Make, Python 3). Its comments explain the specification and end with sample `rew` and `search` commands on a small scenario (module `MAIN-SAMPLE`: an RA opens a session with a TA, invokes a command that calls a TEE API, and closes it), to paste at the Maude prompt:

```bash
maude src/main.maude    # load the specification, then paste a sample command
```

## Building

`make` builds a feature configuration of the specification into `build/<NAME>/` (GNU Make, Python 3):

```bash
make                         # build/default/src and build/default/tests
make NAME=t CRYPTO=0         # a build without the cryptographic operation API
make NAME=t CONFIG=x.toml    # the features set by x.toml (see features.toml)
make NAME=t test             # build, then run the API suite and the check suite on it
make docs                    # documentation site in build/docs
make help                    # features, settings and targets
```

Set the Maude binary in `config.mk` (gitignored) for the tests:

```make
MAUDE := /path/to/maude
```

The API groups below are feature groups (`features.mk`): a build without some of them (`make CRYPTO=0`) drops their files from `build/<NAME>/src/main.maude` and their tests.

A build configuration file selects features: `make CONFIG=x.toml` reads its `F = V` lines
(`features.toml` sets every feature on). The file is TOML with top-level keys only, which is also
make syntax, so the Makefile includes it; a key that is not a feature is an error. A feature it does
not set keeps its default (`features.mk`), and the command line overrides it
(`make CONFIG=x.toml CRYPTO=1`). Like the other settings, CONFIG is cached in `build/<NAME>/config.mk`.

```toml
CRYPTO = 0
SHM = 0
```

| Feature | Covers | Requires |
| --- | --- | --- |
| `CRYPTO` | Cryptographic operation APIs | `TRANSIENT` |
| `TRANSIENT` | Transient object API | |
| `PERSISTENT` | Persistent object API and storage errors | |
| `GENERIC` | Generic object API | |
| `DATA_STREAM` | Persistent data stream access | `PERSISTENT` |
| `ENUMERATOR` | Persistent object enumeration | `PERSISTENT` |
| `SHM` | Shared memory of a client and a TA (the C client call `teecInvokeCommand`) | |

## Repository layout

```text
tee-formal-spec/
├── src/                  # Maude specification (main.maude: the entry point)
├── lang/c/               # the TA programming language: semantics.maude (syntax and K-style
│                         # semantics), typing.maude (type checker), tee-support.maude (program calls to TEE API calls); loaded by src/main.maude
├── tests/
│   ├── api/              # API-level black-box searches (test-*.maude)
│   ├── cases/            # API-specific test cases
│   ├── completion/       # completion regressions, one file per API group
│   ├── gp-conformance/   # GlobalPlatform conformance reductions, one file per module group
│   └── main.maude        # shared test modules
├── docs/annotations.md   # the machine-readable module annotations of src/
├── tools/
│   ├── strip-modules.py  # main.maude and the spec files of a feature configuration
│   ├── test/             # the test runners (run-api-suite.py, run-checks.py)
│   └── docs/             # the documentation site: gen-docs.py, gen-examples.py and their inputs
├── .github/workflows/docs.yml   # builds the site and deploys it to GitHub Pages
├── Makefile              # build, tests, docs
├── features.mk           # feature groups a build can drop
├── LICENSE
└── README.md
```

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
| `tee-api.maude`, `tee-api-call.maude` | API identifiers, call parameters, return values, and the values `# X` of the API data |
| `tee-msg.maude` | Message layer of the TEE API models |
| `session.maude` | Sessions of an RA or a TA client: opening a session (TEEC_OpenSession, TEE_OpenTASession) with the TA instance it creates, and invoking a command in it |
| `tee-kernel.maude` | TA kernel and API execution state |
| `tee-panic.maude` | Panics: GENERAL-PANIC (general programmer errors) and the panic module of each feature group (PERSISTENT-PANIC, DATA-STREAM-PANIC, CRYPTO-PANIC), cut by the build when the group is off |
| `os.maude`, `os-service.maude` | Trusted OS and storage services |
| `app.maude`, `ree-app.maude`, `tee-app.maude` | REE/TEE applications and client behavior |
| `shared-memory.maude` | Shared memory of a client and a TA (language-independent part; the C client call `teecInvokeCommand` is in `../lang/c/tee-support.maude`) |

### Program execution

`src/` does not depend on the language of the programs: `lang.maude` declares what the
model needs from it, and a language under `lang/` gives it. `main.maude` loads the language files
after every file of `src/`.

| File | Purpose |
| --- | --- |
| `k-prelude.maude` | Foundations for the K-style execution semantics |
| `lang.maude` | What the model shares with the program language: values `# X`, argument lists `List{Arg}`, function identifiers, programs, parameter lists, the tasks of an API call (`$in`, `$wait`, `$out`), running a TA service (`mkConf`, `getVals`), shared objects of a client TA |
| `../lang/c/semantics.maude` | Syntax and semantics of model programs (a C subset) |
| `../lang/c/typing.maude` | Static type checking of model programs (`wellTyped`) |
| `../lang/c/tee-support.maude` | The C side of the model: TEE API calls of programs (`getFuncParams` of each API function), client calls, running a TA service, shared memory |

## Core modeling principles

### Handles

Handles are top-level objects in the configuration rather than nested inside their owners. No object class has a handle-valued attribute, and the handle constructors create top-level objects.

- Each `Handle` has an `oid` that names the object it belongs to.
- A persistent object may have several open handles. A new handle is added only when its sharing flags are compatible with the handles already open on the object.
- Closing a handle removes only that handle. The object and its other handles remain.
- Close-and-delete requires the handle to carry the write-meta access right. On success, all handles of the object are removed, the object is removed together with its embedded data stream, and the storage service is notified to delete the file. Otherwise the TA panics and the object is not deleted.

### Panic semantics

`tee-panic.maude` evaluates invalid handles and other GlobalPlatform programmer errors against the active `TAKernel` call state and top-level handle objects. A panic path converges to `status : panic` on the `TAKernel`, with the API call in a finished state.

## Testing

The test files are plain Maude:

- `tests/api`: black-box searches that verify external API calls and returns
- `tests/completion/<group>.maude`: checks that completed paths converge to final success, error, or panic states
- `tests/gp-conformance/<group>.maude`: focused reductions derived from GlobalPlatform tables and clauses
- `tests/cases`: API-specific test cases

`make test` builds the specification and runs the API suite and the check suite on it (`make test-api`, `make test-checks`).

## License

See [LICENSE](LICENSE) for the applicable license terms.
