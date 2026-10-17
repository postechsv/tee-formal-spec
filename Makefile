# Build driver for the TEE formal spec.
#
#   make                    build build/default/src and build/default/tests
#   make NAME=t             build build/t/src and build/t/tests
#   make NAME=t CRYPTO=0    build without the crypto modules
#   make NAME=t WITHOUT=M   build without module M (must be a pr line in main.maude)
#   make NAME=t DMC=1       build the DM-Check version (tools/gen-dmc.py) instead of a plain copy
#   make NAME=t test        build, then run test-api and test-checks in build/t/tests
#   make test-api           API-level black-box tests (tests/api/test-*.maude)
#   make test-checks        search/red checks (completion/*.maude, gp-conformance/*.maude)
#   make dmc                same as make DMC=1 (the setting is cached like the features)
#   make check-dmc          regenerate the DMC build and run gen-dmc's ground checks (exit 2 on mismatch)
#   make help               list features, test selection and current values
#   make clean NAME=t       remove build/t
#
# build/$(NAME)/ holds:
#   src/, tests/   the final tree: a copy of the selected sources (DMC=0) or the gen-dmc
#                  output (DMC=1). Nothing else is written there by the DMC build.
#   .dmc-cache/    DMC=1 bookkeeping (reflection cache, manifest.json, check-build). Kept across
#                  builds; only make clean removes it.
#   config.mk, config.want, .inputs (the selected input list), .stamp (build marker)
#   .stage/        intermediate, removed after each successful build. A failed build leaves
#                  the stamp untouched, so the next run rebuilds.
#
# Setting precedence: command line > build/$(NAME)/config.mk (cache) > mk/features.mk.
# Personal settings such as the Maude path go in config.mk at the repo root (gitignored).

# Ignore a NAME inherited from the shell environment; only make arguments or config.mk set it.
ifneq ($(filter environment%,$(origin NAME)),)
NAME := default
endif
NAME ?= default
OUT := build/$(NAME)
STAGE := $(OUT)/.stage
FINAL_STAMP := $(OUT)/.stamp

# Personal settings (gitignored). Read before MAUDE so it can set MAUDE.
-include config.mk

# Maude binary. Set MAUDE in config.mk, or MAUDE_BIN in the environment.
MAUDE ?= $(or $(MAUDE_BIN),maude)
export MAUDE_BIN := $(MAUDE)

# Settings used by the previous build in this directory.
-include $(OUT)/config.mk

# Feature table (defaults with ?=).
include mk/features.mk

# DM-Check transform (tools/gen-dmc.py). Not a spec feature: it does not drop any files.
DMC ?= 0

.PHONY: all src test test-api test-checks dmc check-dmc help config clean distclean FORCE

all: src

# Stop early on invalid values or unmet requirements.
$(if $(filter-out 0 1,$(DMC)),$(error DMC must be 0 or 1, got '$(DMC)'))
$(foreach F,$(FEATURES),\
  $(if $(filter-out 0 1,$($(F))),$(error $(F) must be 0 or 1, got '$($(F))'))\
  $(if $(filter 1,$($(F))),\
    $(foreach G,$($(F)_REQUIRES),$(if $(filter 0,$($(G))),$(error $(F) requires $(G))))))

# Arguments for tools/select-main.awk. Disabled features contribute their lists.
DROP_LOADS := $(foreach F,$(FEATURES),$(if $(filter 0,$($(F))),$($(F)_LOADS)))
DROP_MODULES := $(foreach F,$(FEATURES),$(if $(filter 0,$($(F))),$($(F)_MODULES))) $(WITHOUT)

MAIN_SRC := src/main.maude
# Spec files in the stage: every src/*.maude except main.maude and the files of disabled features.
DROP_SRC := $(addprefix src/,$(addsuffix .maude,$(DROP_LOADS)))
SRC_FILES := $(filter-out $(MAIN_SRC) $(DROP_SRC),$(wildcard src/*.maude))

# Test selection. Names are relative to tests/. Files listed in F_TESTS of a
# disabled feature are dropped.
ALL_API_TESTS := $(patsubst tests/%,%,$(wildcard tests/api/test-*.maude))
ALL_CHECK_TESTS := $(patsubst tests/%,%,$(wildcard tests/completion/*.maude tests/gp-conformance/*.maude))
DROP_TESTS := $(foreach F,$(FEATURES),$(if $(filter 0,$($(F))),$($(F)_TESTS)))
API_TESTS := $(filter-out $(DROP_TESTS),$(ALL_API_TESTS))
CHECK_TESTS := $(filter-out $(DROP_TESTS),$(ALL_CHECK_TESTS))
# The test runners run in $(OUT)/tests. A full copy of tests/ (all files, including the
# runners and the README) is made, so relative loads such as ../src/main resolve inside $(OUT).
# With every API test selected, run the runner without arguments so it checks the total.
API_FULL := $(if $(filter-out $(API_TESTS),$(ALL_API_TESTS)),,1)
API_ARGS = $(if $(API_FULL),,$(addprefix $(OUT)/tests/,$(API_TESTS)))
CHECK_ARGS = $(addprefix $(OUT)/tests/,$(CHECK_TESTS))
TEST_SRCS := $(shell find tests -type f)

# gen-dmc reads the stage and writes $(OUT)/src; derived tests go to $(OUT)/tests. Its
# bookkeeping lives in $(OUT)/.dmc-cache, outside src/ (the DMC=0 path replaces src/ wholesale).
# DMC_CHECK=1 (set by check-dmc) adds --check.
GEN_DMC = python3 tools/gen-dmc.py $(STAGE)/src $(OUT)/src --tests $(STAGE)/tests --tests-out $(OUT)/tests --cache-dir $(OUT)/.dmc-cache --maude $(MAUDE) $(if $(DMC_CHECK),--check)

# Everything that decides the final tree. config.want changes with every setting; .inputs
# with the selected file list, so deleting a file (which no longer appears in the lists) rebuilds.
FINAL_INPUTS := $(MAIN_SRC) $(SRC_FILES) $(TEST_SRCS) tools/select-main.awk mk/features.mk $(OUT)/config.want $(OUT)/.inputs
FINAL_INPUTS += $(if $(filter 1,$(DMC)),tools/gen-dmc.py $(wildcard tools/dmc-lib/*))

# The cache is updated last, so a failed build never leaves bad settings behind.
src: $(FINAL_STAMP)
	@echo "config: NAME=$(NAME) $(foreach F,$(FEATURES),$(F)=$($(F))) WITHOUT='$(WITHOUT)' DMC=$(DMC)"
	@cmp -s $(OUT)/config.want $(OUT)/config.mk || cp $(OUT)/config.want $(OUT)/config.mk

# Stage, then the final tree, then the stage is removed. A failure before the touch
# leaves the stamp old, so the next run rebuilds (and starts by removing the stage).
$(FINAL_STAMP): $(FINAL_INPUTS) | $(OUT)
	@rm -rf $(STAGE) $(OUT)/tests
	@mkdir -p $(STAGE)/src $(STAGE)/tests
	@awk -v skip_loads='$(DROP_LOADS)' -v skip_mods='$(DROP_MODULES)' -v without='$(WITHOUT)' \
		-f tools/select-main.awk $(MAIN_SRC) > $(STAGE)/src/main.maude
	@$(if $(SRC_FILES),cp $(SRC_FILES) $(STAGE)/src/)
	@cp -R tests/. $(STAGE)/tests/
	@if [ '$(DMC)' = 1 ]; then \
		rm -rf $(OUT)/src && $(GEN_DMC); \
	else \
		rm -rf $(OUT)/src && cp -R $(STAGE)/src $(OUT)/src && cp -R $(STAGE)/tests $(OUT)/tests; \
	fi
	@rm -rf $(STAGE)
	@touch $@

# Run both suites in $(OUT)/tests. Each runs even if the other fails; the exit status
# is nonzero if either fails.
test: src
	@status=0; \
	$(MAKE) --no-print-directory test-api || status=1; \
	$(MAKE) --no-print-directory test-checks || status=1; \
	exit $$status

test-api: src
	@if [ -z '$(API_TESTS)' ]; then echo '[test-suite] no API tests selected'; else python3 $(OUT)/tests/run-api-suite.py $(API_ARGS); fi

test-checks: src
	@if [ -z '$(CHECK_TESTS)' ]; then echo '[checks] no check files selected'; else python3 $(OUT)/tests/run-checks.py $(CHECK_ARGS); fi

# The DMC build is a cached setting, like the features: DMC=1 stays until DMC=0 is given.
dmc:
	@$(MAKE) --no-print-directory DMC=1 src

# Always regenerates (-B), then runs the ground checks. gen-dmc exits 2 on a mismatch.
check-dmc:
	@$(MAKE) --no-print-directory -B DMC=1 DMC_CHECK=1 src

help config:
	@echo "NAME=$(NAME)  OUT=$(OUT)  MAUDE=$(MAUDE)"
	@echo "features:"
	@$(foreach F,$(FEATURES),echo '  $(F)=$($(F))';) echo '  WITHOUT=$(WITHOUT)'
	@echo "  DMC=$(DMC)"
	@echo "tests (relative to tests/):"
	@echo "  api:    $(API_TESTS)"
	@echo "  checks: $(CHECK_TESTS)"
	@echo "targets:"
	@echo "  make [src]     build $(OUT)/src and $(OUT)/tests (DMC=1: gen-dmc output)"
	@echo "  make test      test-api and test-checks"
	@echo "  make test-api  API-level tests (run-api-suite.py)"
	@echo "  make test-checks  search/red checks (run-checks.py)"
	@echo "  make dmc       same as DMC=1 (cached)"
	@echo "  make check-dmc regenerate the DMC build and run its ground checks"
	@echo "  make clean     remove $(OUT)"
	@echo "  make distclean remove build/"

clean:
	rm -rf $(OUT)

distclean:
	rm -rf build

$(OUT):
	@mkdir -p $@

# Desired settings, rewritten only when they change. The final tree depends on it.
# Copied to the cache config.mk (read by the next run) after a successful build.
# Gated so help/clean never create build directories.
ifeq (,$(filter help config clean distclean,$(MAKECMDGOALS)))
$(OUT)/config.want: FORCE | $(OUT)
	@printf '%s\n' '# Generated by Makefile. Do not edit.' $(foreach F,$(FEATURES),'$(F) := $($(F))') 'WITHOUT := $(WITHOUT)' 'DMC := $(DMC)' > $@.tmp
	@if cmp -s $@.tmp $@; then rm -f $@.tmp; else mv $@.tmp $@; fi

# Selected input files (sorted), rewritten only when the list changes. A deleted or added file
# changes the list, so the stamp becomes stale.
$(OUT)/.inputs: FORCE | $(OUT)
	@printf '%s\n' $(sort $(SRC_FILES) $(TEST_SRCS)) > $@.tmp
	@if cmp -s $@.tmp $@; then rm -f $@.tmp; else mv $@.tmp $@; fi
endif

FORCE:
