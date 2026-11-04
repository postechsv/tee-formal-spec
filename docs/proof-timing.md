# Proof time by feature set

How the cost of one inductive invariant proof grows as dmc-gen build features are turned on.

## Setup

- **Property:** the trivial invariant of `trivial.mproof`. The TA kernel's `status` is one of the
  three `AppStatus` constants (`appStatusOk(AS) = true`). The proof is `property invariant ind := Inv from Init`.
- **Builds:** `make NAME=<n> DMC=1 GHOST=ghost/overwrite.maude` plus the feature flags below. Each build's
  `src/` is copied to `/tmp/mp/<dir>/src`, next to `trivial.maude` (module `TRIVIAL-TEE`) and `trivial.mproof`.
- **Engine:** `~/dmc` working tree (2026-10-23), with the three NuITP fixes in `~/dmc/docs/bugs/`
  (`nuitp-getop-no-matching-decl.md`, `nuitp-embsmall-ill-kinded.md`, `nuitp-getop-constant-overload.md`).
- **Commands** (`M = node ~/dmc/lsp/bin/mproof.js`, `MPROOF_ENGINE_DIR=~/dmc`), each timed with bash `time`:
  1. load: `M run trivial.mproof --update-store store`
  2. gengoals: `M property gengoals ind store`
  3. autodischarge: `M autodischarge ind store`
  4. manual: `M goal run` / `M proof run` on the goals that autodischarge leaves open
- **Rules:** the number of `rl [` / `crl [` lines in the build's `src/`.

## Results

| Build | Features on | Rules | Goals | Build | Load | gengoals | autodischarge | Left open | Manual tactics |
|---|---|---|---|---|---|---|---|---|---|
| trivial43 | ow-core minus `persistent.maude` and `persistent-panic.maude` | 43 | 31 | – | – | – | – | 3 (init, handle-invoke-ta, handle-session-req) | about 13 min in all, before the NuITP fixes |
| ow-core | PERSISTENT | 90 | 113 | – | – | 15 s | 47 s (1 min 32 s before the NuITP fixes) | 2 (handle-invoke-ta, handle-session-req) | about 40 s |
| ow-gen | PERSISTENT, GENERIC | 126 | 177 | – | 2 s | 29 s | 2 min 23 s | 2 (handle-invoke-ta, handle-session-req with the TA's `proc none` case) | same tactics as ow-core: `in ok with ...` 4.4 s + 4.6 s, then `auto .` on the side goals 1 min 25 s + 1 min 31 s wall (27 s + 14 s in the engine) |
| ow-enum | PERSISTENT, GENERIC, ENUMERATOR | 151 | 220 | 28 s | 1.6 s (build cache reused) | 42 s | 5 min 1 s wall (262 s in the engine) | 2 (handle-invoke-ta, handle-session-req with the TA's `proc none` case) | same tactics as ow-core: `in ok with ...` 2.9 s + 2.8 s, then `auto .` 24 s + 16 s wall (20 s + 12 s in the engine) |
| ow-ds | PERSISTENT, GENERIC, ENUMERATOR, DATA_STREAM | 176 | 265 | 26 s | 1.4 s (build cache reused) | 57 s | 8 min 30 s wall (432 s in the engine) | 3 (handle-invoke-ta, handle-session-req with `proc none`, read-object-data-storage-status-check) | the first two as in ow-core (4.1 s + 24 s, 3.2 s + 16 s). read-object-data-storage-status-check: `partitioned S5 as ...` into 4 cases, then per case `in ok with ...` (all clause variables bound) and `auto .`, about 11 s per case |
| ow-tr | PERSISTENT, GENERIC, ENUMERATOR, DATA_STREAM, TRANSIENT | 209 | 320 | 25 s | 1.5 s | 1 min 23 s | 15 min 8 s wall | 3 (the same three as ow-ds) | the same tactics as ow-ds. handle-invoke-ta: `in ok with ...` 1.3 s, `auto .` 19 s in the engine. read-object-data-storage-status-check: `auto .` about 4 s per case |
| ow (full) | all: CRYPTO, TRANSIENT, PERSISTENT, GENERIC, DATA_STREAM, ENUMERATOR | 379 | 591 | 38 s | 1.7 s | 1 min 2 s | 2 min 12 s (11 min 52 s before the store fixes) | 3 (the same three as ow-ds) | the same tactics as ow-ds, 1 min 22 s in all (3 min 50 s before the store fixes). `in ok with ...` and `partitioned` about 1.5 s each. `auto .` 20 s wall (18 s in the engine) on handle-invoke-ta and handle-session-req, about 8 s wall (5.5 s) per read-object-data-storage-status-check case |

The ow-tr and earlier rows were measured before the autodischarge and store fixes (see Notes); the full row after them.
The full store is 44 MB after autodischarge and 91 MB with every goal proved (1.5 GB and 1.8 GB before the store fixes).

## Notes

- **trivial43** was measured as one run of about 13 minutes. Machine time and tactic time were not
  separated. It ran before the NuITP fixes, so `auto` failed on every goal with a hypothesis.
- **ow-core:** both open goals closed with
  `in ok with TK |-> TK, AS |-> AS, SI |-> SI, OK |-> OK, N |-> N, CS |-> CS .` and then `auto .` on the
  side goal. The first ow-core session took about 63 minutes of wall time (03:34 to 04:37 on 2026-10-23).
  Most of it went to finding and fixing the NuITP bugs. The numbers in the table come from a fresh store
  after the fixes.
- **ow (full), first attempt (before the autodischarge fixes):** the gengoals progress line
  (`[DMC narrow] depth N`) went from 0 to 679 in about one minute. It then stayed at 679 for more than
  2 minutes 30 seconds, so the run was stopped. Killing `mproof.js` left its `mproof-engine` child
  running, and it had to be killed separately. With the fixes, the same source passed depth 679 within
  15 s and gengoals finished in 1 min 2 s. The stall was probably the Maude `glUnquote` over all goal
  names, which the fixes replaced; this was not checked separately.
- **ow (full), checkpoint saving and store size** (fixed 2026-10-23). Three parts of the store grew with
  the square of the goal count:
  - **Collision warnings.** DM-CHECK warned `goal-label-collision` for every goal name with a `#`, but
    the spec's own syntax (`[# ...]`) puts `#` in most names, and each step repeated the warning for
    every goal. The full autodischarge record held 228,717 such warnings (250 MB; 36 MB at 220 goals,
    73 MB at 320), and every checkpoint save serialized and hashed that record again, about 1 s per
    goal. None of them was a real collision. Now only the `#<n>` suffix that `glFreshGoalName` adds
    counts, and a step warns only for its new goals (`glNameWarning`, `glRecStep` in dm-check-alpha12.maude).
  - **State dumps.** Each goal kept its own dump of the whole engine state (625 KB compressed), two per
    goal: 740 MB. The dumps saved after one command differed only in the dump command's own file
    path, which the engine keeps in the state. The dump path is now fixed, and a goal's state file is
    a hard link to one store-wide file named by its content (`.mproof/state-blobs`); unlinked files
    are removed with the unused records (`goal-checkpoints.js`).
  - **Child cache entries.** Each autodischarge child's cache entry kept the composed state of every
    open goal (about 360 KB each, 215 MB), which no restore reads. It now keeps the proof only
    (`controller.js`).

  | full, fresh store | before | warnings | + shared dumps | + child entries |
  |---|---|---|---|---|
  | autodischarge | 11 min 52 s | – | 2 min 36 s | 2 min 12 s |
  | store after autodischarge | 1.5 GB | – | 246 MB | 44 MB |
  | store, all goals proved | 1.8 GB | – | 293 MB | 91 MB |

  The warnings fix alone was measured on ow-enum: autodischarge 73 s to 30 s, records 119 MB to 38 MB.
  On ow-enum with all three fixes the store is 19 MB after autodischarge (182 MB before the dump and cache
  fixes) and 63 MB with every goal proved (308 MB). Goal status was the same in every run. A second
  autodischarge on the same store restored all 220 children from the cache. The LSP tests give the same
  4 failures as before the change, and `tests/run_all.sh` gives the same verdicts as the engine without it.

  autodischarge per goal is still higher in full (2 min 12 s for 591, about 0.22 s) than in ow-enum
  (28 s for 220, about 0.13 s). Not examined yet.
- The load time does not include the build (`make`), which is listed on its own.
- **ow-gen:** the autodischarge time is the engine's own report (`autodischarge . -> ok (143421 ms)`).
  Each `auto .` run spends about one minute outside the engine, restoring the goal's checkpoint.
- **Store size:** each goal's `record.json` is about 30 MB in ow-gen, mostly checkpoint data. The store
  is 4.0 GB for ow-gen and 1.4 GB for ow-core. These stores filled the Windows C: drive, which holds the
  WSL disk image, and that is the likely cause of the WSL restarts (see ow-gen and ow-enum below).
  After the store changes in `~/dmc/lsp` (2026-10-23), the ow-enum store is 258 MB after autodischarge
  and 308 MB with every goal proved:
  - Checkpoints name their records by content hash and keep each record once per store, instead of
    copying the shared prefix (load, gengoals, autodischarge) into every goal.
  - Debug logs and traces stay only in the checkpoint that made them.
  - `.dag` state files are gzip-compressed.
  - Records are written once to the shared store, not again per goal.
  - Record files that no checkpoint names any more are removed after undo, reset or an engine rebuild.
- **ow-enum:** the load time is short because the build cache under `~/.cache/mproof` from the first
  attempt was reused. The first attempt's load is not in the table.
- **Why autodischarge grows faster than the rule count** (measured on ow-enum, 2026-10-23):
  - The time of one goal depends on how many goals are still open, not on the goal: 2.7 s for the
    first goals and 0.14 s for the last. Goal-specific cost (match and NuITP) is tens of ms per goal;
    `ind.init` and the panic goals take up to 1.6 s more than their neighbours.
  - The engine (mproof-engine) uses about 99% of the CPU, and the LSP about 1%. Meta-level calls are
    1 to 3% of a child command.
  - A Maude profile of 10 children: about 70% of the rewrites are `glUnquote` (Qid to display string,
    one character per step, copying the rest each time) for every open goal's name, reported again for
    each child. Then come `rcHasItem` (`rcChanged`, all goals pairwise, 6.6%) and `rcIn` (`rcMinus`
    on the open goal lists, 4.7%).
  - So one child costs about (open goals) × (name length)² + (open goals)² + G², and the whole
    autodischarge about G²·L² + G³.
  - After `glUnquote`, the next cost was `jesc` (JSON escaping, NuITP.maude's equations): each child's
    event escapes its state delta, which lists every open goal, and `jesc` finds each special
    character from scratch, so it is quadratic in the number of open goals.
  - Changes: `glUnquote` and `jesc` run in OCaml for DM-CHECK-UI (mproof-engine hooks;
    `DMC_UNQUOTE=0` and `DMC_JESC=0` turn them off), and `rcMinus` and `rcChanged` look names and
    items up in ACU sets instead of walking lists (dm-check-alpha12.maude). The goals and their
    status are the same as before.

    | ow-enum, fresh store | before | `glUnquote` | + sets | + `jesc` |
    |---|---|---|---|---|
    | gengoals | 39.5 s | 34.1 s | 38.9 s | 16.4 s |
    | autodischarge | 288 s | 159 s | 143 s | 73 s |
    | sum of the children | 244 s | 105 s | 99 s | 19 s |
    | mean per child, goals 1-44 / 89-132 / 177-220 | 2.49 / 0.87 / 0.15 s | 1.19 / 0.35 / 0.08 s | 0.98 / 0.35 / 0.06 s | 0.25 / 0.05 / 0.04 s |
    | after the last child (checkpoints saved) | 39 s | 50 s | 40 s | 51 s |

    The children now take about the same time whatever their position. Most of what is left is
    the checkpoint saving after the last child.

## Session time, including bug fixing

The time of one proof session is the wall time from start to "all goals proved", including the
time spent finding and fixing tool bugs that blocked the proof. The spans below come from the
conversation timestamps. Times are KST on 2026-10-23.

### trivial43: 03:03:43 to 03:22:57

| Span | Time | What |
|---|---|---|
| 03:03:43 – 03:04:23 | 40 s | known tactics on init and handle-invoke-ta |
| 03:04:23 – 03:12:14 | 8 min | LSP bug: goal labels with `[` or `#` were dropped, so `in ok` reported "not an open goal". Found, fixed in the LSP, store rebuilt. A second LSP bookkeeping issue (closed goals still shown open) was being traced |
| 03:12:14 – 03:19:30 | 7 min | usage limit pause (not counted) |
| 03:19:30 – 03:21:44 | 2 min | duplicate goal names: two rules with the same label in the spec source gave goals of the same name. Fixed with unique labels (`glUniqLabels`) |
| 03:21:44 – 03:22:57 | 1 min | clean store rebuild, autodischarge, last tactics (`destruct AS ; auto .`) |

Active time 12 min: about 10 min of bug fixing and 2 min of proving.

### ow-core: 03:33:58 to 04:37:21

| Span | Time | What |
|---|---|---|
| 03:33:58 – 03:36:22 | 2 min 30 s | gengoals (15 s) and autodischarge (1 min 32 s, before the NuITP fixes) |
| 03:36:22 – 03:37:12 | 1 min | known tactics on the three goal families seen in trivial43 |
| 03:37:12 – 03:53:45 | 16 min 30 s | NuITP: `auto` could not close `appStatusOk(panic) = true` in the close-and-delete-persistent-panic side goal. Traced through NuITP debug events and a baseline comparison. Stopped by the user, not fixed |
| 03:54:26 – 04:16:15 | 22 min | workaround with a lemma (`lemma add`). Three LSP bugs found and fixed: the lemma went after the property block, goal copies did not get new declarations, checkpoint keys ignored proved lemmas. Includes about 1 min of moving the work to `/tmp/mp`. The workaround still did not work |
| 04:17:16 – 04:22:01 | 4 min | the RPO hypothesis: toy modules with and without `rpo` attributes. Missing `rpo` attributes were ruled out |
| 04:22:31 – 04:33:37 | 11 min | NuITP: found where `removeU` stops and fixed the three upstream bugs (`getOp'''`, `embSmall'`, `getOp'`) |
| 04:33:58 – 04:37:21 | 3 min 30 s | the two remaining goals closed with `in ok with ...` and `auto .`, after a checkpoint rebuild |

Total 63 min: about 53 min of bug fixing (NuITP 27.5 min, LSP 22 min, RPO check 4 min) and about
8 min of proving. Gaps while waiting for the user are not listed. A fresh store after the fixes takes
15 s of gengoals, 47 s of autodischarge and about 40 s of tactics.

### ow-gen: 04:46:54 to 05:02:24

| Span | Time | What |
|---|---|---|
| 04:46:54 – 04:47:59 | 1 min | build, load, gengoals (29 s) |
| 04:48:21 – 04:50:45 | 2 min 23 s | autodischarge |
| 04:52:29 | – | WSL restarted (cause not known). Files written in the last minute before it were not on disk: two `record.json` files and five checkpoint `.dag` files of six autodischarged goals were zero-filled |
| 04:53:46 – 04:54:02 | 16 s | `in ok with ...` on both goals |
| 04:54:09 – 04:59:14 | 5 min | recovery: `auto .` failed with "Bad control character ... at position 16777216", because `resolveProof` reads every goal's record. Found the zero-filled files, deleted the six goal directories (which made `resolveProof` fail with ENOENT), then wrote empty records for them from `store.json`. Those six goals stay proved in `store.json` but their records no longer hold the proof steps |
| 04:59:14 – 05:02:24 | 3 min | `auto .` on both side goals |

Total 15.5 min: about 5 min of recovery and about 10.5 min of building and proving. No tool bug blocked
the proof itself.

### ow-enum

| Span | Time | What |
|---|---|---|
| 05:07 | 28 s | build |
| first attempt | – | WSL died during autodischarge at about goal 53 of 220. The C: drive (WSL disk image) was full, filled by the ow-gen and earlier stores |
| – | – | not counted: disk cleanup and the store size changes in `~/dmc/lsp` (see Notes) |
| 05:43:55 – 05:49:40 | 5 min 45 s | fresh store: load 1.6 s, gengoals 42 s, autodischarge 5 min 1 s. 218 of 220 goals proved |
| after 05:49:40 | about 46 s | `in ok with ...` and `auto .` on handle-invoke-ta and handle-session-req. All 222 goals proved |

The proof itself took about 6.5 min. No proof tactic beyond the ow-core ones was needed.

### ow-ds: 05:52:44 to about 06:25

| Span | Time | What |
|---|---|---|
| 05:52:44 – 06:02:59 | 10 min 15 s | build (26 s), load, gengoals (57 s, narrowing depth 302), autodischarge (8 min 30 s). 262 of 265 goals proved |
| after 06:02:59 | about 1 min | handle-invoke-ta and handle-session-req with the ow-core tactics |
| about 06:05 – 06:22 | about 17 min | read-object-data-storage-status-check. Its rule is the only one with `if ... fi` on the right-hand side, and the TA kernel object is inside the `if`, so `in ok` cannot match the state. `partitioned S5` substitutes the constant but does not reduce the state. Normalizing the children in the engine was tried and reverted: a later `abstracted M + 0 as K` in the dmc tests then applied to the constraint only. Fix without an engine change: `in ok with ...` binding every clause variable (`C` and `CS` to the case's terms). With `with`, the engine does not match; it puts `instance = state` into the side goal, and NuITP `auto .` reduces the `if` |
| 06:22:46 – about 06:25 | about 2 min | the four cases: `in ok with ...` about 3 s and `auto .` about 8 s each. All 275 goals proved |

About 30 min in all: about 11 min of machine time and about 17 min of finding the tactic for the new goal.
The tactic for the normal case is
`in ok with TK |-> TK, AS |-> normal, SI |-> SI, OK |-> OK, N |-> N, CS |-> final(mkTeeApi(54)[# O2 V3 A4]), C |-> C1 msg read[] from TK to O2 .`
and for the other three cases `CS |-> return(mkTeeApi(54)[# storageErrCode(<status>)]), C |-> C1`.

### ow-tr: about 06:26 to 06:47

| Span | Time | What |
|---|---|---|
| about 06:26 | 25 s | build. No new rule has `if ... fi` on the right-hand side |
| 06:27:14 – 06:43:47 | 16 min 33 s | load (1.5 s), gengoals (1 min 23 s), autodischarge (15 min 8 s). 317 of 320 goals proved. The store is 486 MB after autodischarge |
| after the user's go-ahead – 06:47 | a few min | the three open goals with the ow-ds tactics, unchanged. All 330 goals proved. The store is 596 MB |

No new proof tactic was needed. Against ow-ds, the rules went up by 19 % (176 to 209) and
autodischarge went from 8 min 30 s to 15 min 8 s (about 1.8 times).

### ow (full): 07:33 to 07:50

First attempt stopped in gengoals (see Notes). Second run after the autodischarge fixes, in `/tmp/mp/full2`:

| Span | Time | What |
|---|---|---|
| – | 38 s | build, from the first attempt (the build was up to date) |
| 07:33:11 – 07:34:15 | 1 min 4 s | load (2.0 s), gengoals (1 min 2 s): 591 goals |
| 07:34:15 – 07:46:08 | 11 min 52 s | autodischarge: goals 1 to 591 done by about 07:36, then checkpoint saving. 588 of 591 goals proved. The store is 1.5 GB |
| 07:46:35 – 07:50:25 | 3 min 50 s | the three open goals with the ow-ds tactics, unchanged. All 601 goals proved. The store is 1.8 GB |

About 17 min from load to the last goal. No new proof tactic was needed.

After the store fixes (see Notes), the same run in `/tmp/mp/full4`, 08:07:44 to 08:12:22:

| Span | Time | What |
|---|---|---|
| 08:07:44 – 08:08:47 | 1 min 3 s | load (1.7 s), gengoals (1 min 2 s) |
| 08:08:47 – 08:11:00 | 2 min 12 s | autodischarge. 588 of 591 goals proved. The store is 44 MB |
| 08:11:00 – 08:12:22 | 1 min 22 s | the three open goals with the same tactics. All 601 goals proved. The store is 91 MB |

About 4 min 40 s from load to the last goal.
