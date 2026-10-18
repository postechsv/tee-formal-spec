# Module annotations

Every module in `src/*.maude` carries a machine-readable annotation. The annotation is a
Maude comment, so it does not change the specification. The documentation generator
(`tools/gen-docs.py`) uses it to group modules by role and to link related modules.

## Format

An annotation is placed on its own lines immediately above the `fmod`, `mod`, `omod`,
`fth` or `th` line. Any prose doc comment for the module stays above the annotation. There
is no blank line between the annotation and the module keyword.

```
--- @kind behavior
--- @related TRANSIENT-OBJECT-TEE-CALL
--- @feature transient
omod TRANSIENT-OBJECT-BEHAVIOR is
```

| Tag | Required | Value |
|---|---|---|
| `@kind` | yes | One kind from the closed set below. |
| `@related` | no | Space-separated module names. Only for relations that the imports do not express, such as a behavior module and its panic module. |
| `@feature` | yes | One API feature group (see below). |

`@related` is undirected: the generator treats `A` related to `B` as the same link as `B`
related to `A`. Each link is therefore written on one side only.

## Kinds

| Kind | Meaning |
|---|---|
| `constants` | Enumerations and constant values: error codes, flags, type identifiers, ids. |
| `data` | Data structures: sorts and functions over them, with no rules. An `fmod` with equations is data. |
| `state` | Class declarations that describe object state. |
| `messages` | Message declarations (`msg`) and request/return identifiers exchanged between components. |
| `behavior` | Rewrite rules (`rl`, `crl`) that implement API behaviour. |
| `panic` | Rules for panic (error-abort) behaviour. |
| `program` | The imperative program language and model programs run by applications. |
| `interface` | API call signatures and parameter plumbing between layers. |
| `assembly` | Modules that only combine other modules, such as `src/main.maude`. |

A module that mixes roles gets the dominant kind, in the order rules, classes, messages,
data, constants.

## Feature groups

`@feature` names the API group a module belongs to. Groups follow the feature names in
`mk/features.mk`, written in lower case with hyphens:

| `@feature` | `mk/features.mk` feature |
|---|---|
| `generic` | `GENERIC` |
| `transient` | `TRANSIENT` |
| `persistent` | `PERSISTENT` |
| `data-stream` | `DATA_STREAM` |
| `enumerator` | `ENUMERATOR` |
| `crypto` | `CRYPTO` |
| `ta-session` | TA sessions and the TA kernel (no feature flag) |
| `client` | REE/TEE client API (no feature flag) |
| `os` | Secure monitor, TEE OS and execution environments (no feature flag) |
| `imp` | Model programs and their translation into TEE calls (no feature flag) |
| `core` | Shared modules used by several groups (no feature flag) |

## How gen-docs uses the annotations

- Group the modules of the documentation by `@kind`, in the order listed above.
- Within each kind, group by `@feature`. A module with no `@feature` falls under `core`.
- Draw a link between two modules when one imports the other (from the `pr`, `inc`,
  `ex` lines) or when they appear together in an `@related` tag.
- Show `@kind` and `@feature` on each module page. Feature filters can use the same groups
  as the `make` feature switches.
