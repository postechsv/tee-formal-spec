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
│   ├── completion.maude  # Visible/invisible completion regressions
│   └── gp-conformance.maude
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
| `tee-panic.maude` | Programmer errors and invalid-handle panics |
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

Run the API regression suite from the repository root:

```bash
python3 tests/run-api-suite.py
```

The current suite runs 92 API-level searches.

Run the completion and GlobalPlatform conformance checks from the `tests` directory. The following example uses the Linux binary supplied with the analysis artifact:

```bash
cd tests
../../tee-formal-analysis/3rd_party/Linux64/maude.linux64 completion.maude
../../tee-formal-analysis/3rd_party/Linux64/maude.linux64 gp-conformance.maude
```

On macOS, use `Darwin64/maude.darwin64`, or set `MAUDE_BIN` to the appropriate executable path.

The test groups serve the following purposes:

- `tests/api`: black-box searches that verify external API calls and returns
- `tests/completion.maude`: checks that completed paths converge to final success, error, or panic states
- `tests/gp-conformance.maude`: focused reductions derived from GlobalPlatform tables and clauses
- `tests/cases`: API-specific test cases inherited from the original artifact

## License

See [LICENSE](LICENSE) for the applicable license terms.
