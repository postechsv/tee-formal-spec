# Feature table, included by the top-level Makefile.
#
# A feature F is a variable F (value 0 or 1) plus:
#   F_LOADS     "load" lines dropped from src/main.maude when F=0
#   F_MODULES   modules removed when F=0: their "pr" lines in src/main.maude, and their
#               definitions in the src files that stay (tools/strip-modules.py; each
#               must be annotated "--- @feature f", f = F in lower case with "-" for "_")
#   F_REQUIRES  features that must be 1 while F=1 (checked, never auto-enabled). Names
#               API-level dependencies, not load dependencies: the files still load
#               independently
#   F_TESTS     test files dropped from make test when F=0. Paths are relative
#               to tests/ (for example api/test-cryptographic.maude)
#
# Defaults use ?=, so the command line and the build cache override them.
#
# To add a feature FOO, append it to FEATURES and add a block like this one:
#   FEATURES += FOO
#   FOO ?= 1
#   FOO_LOADS := foo-file
#   FOO_MODULES := FOO-BEHAVIOR FOO-TEE-CALL
#   FOO_REQUIRES := CRYPTO
#   FOO_TESTS := api/test-foo.maude foo-checks.maude

FEATURES := CRYPTO TRANSIENT PERSISTENT GENERIC DATA_STREAM ENUMERATOR SHM

# Cryptographic operations. The file is dropped; its panic module is cut from tee-panic.maude.
CRYPTO ?= 1
CRYPTO_LOADS := cryptographic
CRYPTO_MODULES := CRYPTO-OPERATION-BEHAVIOR CRYPTO-OPERATION-TEE-CALL CRYPTO-C-VALUE-TYPE CRYPTO-PANIC
CRYPTO_REQUIRES := TRANSIENT
CRYPTO_TESTS := api/test-cryptographic.maude completion/crypto.maude gp-conformance/crypto.maude completion/paper-fig8.maude

# Storage API groups. transient-object.maude and persistent-object.maude are shared data
# modules (TRANSIENT-OBJECT, PERSISTENT-OBJECT) that the generic, persistent, cryptographic,
# data-stream and enumerator files import. They are not in any _LOADS, so they stay loaded.
# A group's behavior file (transient.maude, persistent.maude, ...) is in the group's _LOADS.
# Its panic module (in tee-panic.maude) is in _MODULES, and is cut from that file when the
# group is off. Its behavior and TEE-CALL modules are removed from main.maude when the group is off.
# A group's TEE-CALL module gives the parameters of its TEE API functions (getFuncParams and
# apiResult, declared in TEE-API-C-PROG-AUX), so TEE-API-C-PROGRAM does not need the group's
# TEE-CALL module and the group can be dropped.
# _REQUIRES names API-level dependencies, not load dependencies: the files still load
# independently. Crypto keys are transient objects (CRYPTO needs TRANSIENT); data streams and
# enumerators act on persistent objects (DATA_STREAM and ENUMERATOR need PERSISTENT).
# GENERIC works on either kind of object, so it has no requirement.
# Tests that use another group's API are listed in F_TESTS of every feature they need.

TRANSIENT ?= 1
TRANSIENT_LOADS := transient
TRANSIENT_MODULES := TRANSIENT-OBJECT-BEHAVIOR TRANSIENT-OBJECT-TEE-CALL
TRANSIENT_REQUIRES :=
TRANSIENT_TESTS := api/test-transient.maude completion/transient.maude gp-conformance/transient.maude

PERSISTENT ?= 1
PERSISTENT_LOADS := persistent
PERSISTENT_MODULES := PERSISTENT-OBJECT-BEHAVIOR PERSISTENT-OBJECT-TEE-CALL PERSISTENT-PANIC
PERSISTENT_REQUIRES :=
PERSISTENT_TESTS := api/test-persistent.maude completion/persistent.maude gp-conformance/persistent.maude

GENERIC ?= 1
GENERIC_LOADS := generic
GENERIC_MODULES := GENERIC-OBJECT-BEHAVIOR GENERIC-OBJECT-TEE-CALL
GENERIC_REQUIRES :=
GENERIC_TESTS := api/test-generic.maude completion/generic.maude

DATA_STREAM ?= 1
DATA_STREAM_LOADS := data-stream
DATA_STREAM_MODULES := DATA-STREAM-OBJECT-BEHAVIOR DATA-STREAM-OBJECT-TEE-CALL DATA-STREAM-PANIC
DATA_STREAM_REQUIRES := PERSISTENT
DATA_STREAM_TESTS := api/test-data-stream.maude completion/data-stream.maude gp-conformance/data-stream.maude

ENUMERATOR ?= 1
ENUMERATOR_LOADS := enumerator
ENUMERATOR_MODULES := ENUMERATOR-OBJECT-BEHAVIOR ENUMERATOR-OBJECT-TEE-CALL
ENUMERATOR_REQUIRES := PERSISTENT
ENUMERATOR_TESTS := api/test-enumerator.maude completion/enumerator.maude

# Shared memory of a client and a TA (src/shared-memory.maude): the C client call
# teecInvokeCommand. The TA runs on the memory that the client passes with the call, and
# passes it back when the call completes (the message rules are in the os and client features).
SHM ?= 1
SHM_LOADS := shared-memory
SHM_MODULES := SHARED-MEMORY-SYNTAX SHARED-MEMORY-BEHAVIOR MAIN-SAMPLE
SHM_REQUIRES :=
SHM_TESTS := completion/shared-memory.maude completion/client-session.maude
