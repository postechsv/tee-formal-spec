# Feature table, included by the top-level Makefile.
#
# A feature F is a variable F (value 0 or 1) plus:
#   F_LOADS     "load" lines dropped from src/main.maude when F=0
#   F_MODULES   "pr" lines dropped from src/main.maude when F=0
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

FEATURES := CRYPTO TRANSIENT PERSISTENT GENERIC DATA_STREAM ENUMERATOR

# Cryptographic operations. The file is dropped with its panic module.
CRYPTO ?= 1
CRYPTO_LOADS := cryptographic crypto-panic
CRYPTO_MODULES := CRYPTO-OPERATION-BEHAVIOR CRYPTO-OPERATION-TEE-CALL CRYPTO-OPERATION-BEHAVIOR-INVISIBLE CRYPTO-PANIC
CRYPTO_REQUIRES := TRANSIENT
CRYPTO_TESTS := api/test-cryptographic.maude completion/crypto.maude gp-conformance/crypto.maude

# Storage API groups. transient-object.maude and persistent-object.maude are shared data
# modules (TRANSIENT-OBJECT, PERSISTENT-OBJECT) that the generic, persistent, cryptographic,
# data-stream and enumerator files import. They are not in any _LOADS, so they stay loaded.
# A group's behavior file (transient.maude, persistent.maude, ...) and its panic file are in
# the group's _LOADS. Its behavior, invisible-reduction and TEE-CALL modules are removed from
# main.maude when the group is off. A group's TEE-CALL library constant (TransientLibs,
# PersistLibs) is declared in TEE-API-IMP-PROG-AUX, so TEE-API-IMP-PROGRAM does not import
# the group's TEE-CALL module and the group can be dropped.
# _REQUIRES names API-level dependencies, not load dependencies: the files still load
# independently. Crypto keys are transient objects (CRYPTO needs TRANSIENT); data streams and
# enumerators act on persistent objects (DATA_STREAM and ENUMERATOR need PERSISTENT).
# GENERIC works on either kind of object, so it has no requirement.
# Tests that use another group's API are listed in F_TESTS of every feature they need.

TRANSIENT ?= 1
TRANSIENT_LOADS := transient
TRANSIENT_MODULES := TRANSIENT-OBJECT-BEHAVIOR TRANSIENT-OBJECT-BEHAVIOR-INVISIBLE TRANSIENT-OBJECT-TEE-CALL
TRANSIENT_REQUIRES :=
TRANSIENT_TESTS := api/test-transient.maude completion/transient.maude gp-conformance/transient.maude

PERSISTENT ?= 1
PERSISTENT_LOADS := persistent persistent-panic
PERSISTENT_MODULES := UNPROTECTED-PERSISTENT-OBJECT-BEHAVIOR UNPROTECTED-PERSISTENT-OBJECT-BEHAVIOR-INVISIBLE PERSISTENT-OBJECT-TEE-CALL PERSISTENT-PANIC
PERSISTENT_REQUIRES :=
PERSISTENT_TESTS := api/test-persistent.maude completion/persistent.maude gp-conformance/persistent.maude

GENERIC ?= 1
GENERIC_LOADS := generic
GENERIC_MODULES := GENERIC-OBJECT-BEHAVIOR GENERIC-OBJECT-BEHAVIOR-INVISIBLE GENERIC-OBJECT-TEE-CALL
GENERIC_REQUIRES :=
GENERIC_TESTS := api/test-generic.maude completion/generic.maude

DATA_STREAM ?= 1
DATA_STREAM_LOADS := data-stream data-stream-panic
DATA_STREAM_MODULES := DATA-STREAM-OBJECT-BEHAVIOR DATA-STREAM-OBJECT-BEHAVIOR-INVISIBLE DATA-STREAM-OBJECT-TEE-CALL DATA-STREAM-PANIC
DATA_STREAM_REQUIRES := PERSISTENT
DATA_STREAM_TESTS := api/test-data-stream.maude completion/data-stream.maude gp-conformance/data-stream.maude

# No INVISIBLE module exists for the enumerator group.
ENUMERATOR ?= 1
ENUMERATOR_LOADS := enumerator
ENUMERATOR_MODULES := ENUMERATOR-OBJECT-BEHAVIOR ENUMERATOR-OBJECT-TEE-CALL
ENUMERATOR_REQUIRES := PERSISTENT
ENUMERATOR_TESTS := api/test-enumerator.maude completion/enumerator.maude
