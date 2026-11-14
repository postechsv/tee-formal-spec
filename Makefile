# Build driver of the TEE formal specification: feature selection, test suites, documentation.
#
#   make                    build build/default/src and build/default/tests
#   make NAME=t             build build/t/src and build/t/tests
#   make NAME=t CRYPTO=0    build without the crypto modules (features.mk lists the features)
#   make NAME=t WITHOUT=M   build without module M (must be a pr line in main.maude)
#   make NAME=t test        build, then run test-api and test-checks in build/t/tests
#   make test-api           API-level black-box tests (tests/api/test-*.maude)
#   make test-checks        search/red checks (completion/*.maude, gp-conformance/*.maude)
#   make V=1 test           verbose: stream the Maude output of each test file / check file
#   make docs               build the documentation site: build/docs/index.html
#   make help               list features, test selection and current values
#   make clean NAME=t       remove build/t
#
# build/$(NAME)/ holds:
#   src/, tests/   the selected sources (src/main.maude loads them) and a copy of tests/ with the
#                  test runners (tools/test/), so relative loads such as ../src/main resolve
#   tests.txt      the selected test files, relative to tests/: lines "api FILE" and "check FILE",
#                  and "api-full" when every API test is selected (the runner then checks the total)
#   config.mk, config.want, .inputs (the selected input list), .stamp (build marker)
#   .stage/        intermediate, removed after each successful build. A failed build leaves
#                  the stamp untouched, so the next run rebuilds.
#
# Setting precedence: command line > build/$(NAME)/config.mk (cache) > features.mk.
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
include features.mk

# Verbose output (V=1 or VERBOSE=1): the test runners stream Maude's output and the recipes
# are echoed. Display only, so it is not written to config.want or the cache, and changing it
# does not rebuild. Like NAME, an inherited environment value is ignored.
ifneq ($(filter environment%,$(origin V)),)
V := 0
endif
ifneq ($(filter environment%,$(origin VERBOSE)),)
VERBOSE := 0
endif
V ?= $(or $(VERBOSE),0)
Q = $(if $(filter 1,$(V)),,@)
VFLAG = $(if $(filter 1,$(V)),-v)

.PHONY: all src test test-api test-checks docs help config clean distclean FORCE

all: src

# Stop early on invalid values or unmet requirements.
$(if $(filter-out 0 1,$(V)),$(error V must be 0 or 1, got '$(V)'))
$(foreach F,$(FEATURES),\
  $(if $(filter-out 0 1,$($(F))),$(error $(F) must be 0 or 1, got '$($(F))'))\
  $(if $(filter 1,$($(F))),\
    $(foreach G,$($(F)_REQUIRES),$(if $(filter 0,$($(G))),$(error $(F) requires $(G))))))

# Arguments for tools/build/select-main.awk. Disabled features contribute their lists.
DROP_LOADS := $(foreach F,$(FEATURES),$(if $(filter 0,$($(F))),$($(F)_LOADS)))
DROP_MODULES := $(foreach F,$(FEATURES),$(if $(filter 0,$($(F))),$($(F)_MODULES))) $(WITHOUT)

MAIN_SRC := src/main.maude
# Spec files in the stage: every src/*.maude except main.maude and the files of disabled features.
DROP_SRC := $(addprefix src/,$(addsuffix .maude,$(DROP_LOADS)))
SRC_FILES := $(filter-out $(MAIN_SRC) $(DROP_SRC),$(wildcard src/*.maude))

# Test selection. Names are relative to tests/. Files listed in F_TESTS of a disabled feature
# are dropped.
ALL_API_TESTS := $(patsubst tests/%,%,$(wildcard tests/api/test-*.maude))
ALL_CHECK_TESTS := $(patsubst tests/%,%,$(wildcard tests/completion/*.maude tests/gp-conformance/*.maude))
DROP_TESTS := $(foreach F,$(FEATURES),$(if $(filter 0,$($(F))),$($(F)_TESTS)))
API_TESTS := $(filter-out $(DROP_TESTS),$(ALL_API_TESTS))
CHECK_TESTS := $(filter-out $(DROP_TESTS),$(ALL_CHECK_TESTS))
# The test runners run in $(OUT)/tests, a full copy of tests/ with the runners next to it.
# With every API test selected, run the runner without arguments so it checks the total.
API_FULL := $(if $(filter-out $(API_TESTS),$(ALL_API_TESTS)),,1)
API_ARGS = $(if $(API_FULL),,$(addprefix $(OUT)/tests/,$(API_TESTS)))
CHECK_ARGS = $(addprefix $(OUT)/tests/,$(CHECK_TESTS))
TEST_SRCS := $(shell find tests -type f)
RUNNERS := tools/test/run-api-suite.py tools/test/run-checks.py

# Everything that decides the build. config.want changes with every setting; .inputs with the
# selected file list, so deleting a file (which no longer appears in the lists) rebuilds.
FINAL_INPUTS := $(MAIN_SRC) $(SRC_FILES) $(TEST_SRCS) $(RUNNERS) tools/build/select-main.awk features.mk $(OUT)/config.want $(OUT)/.inputs

# The cache is updated last, so a failed build never leaves bad settings behind.
src: $(FINAL_STAMP)
	@echo "config: NAME=$(NAME) $(foreach F,$(FEATURES),$(F)=$($(F))) WITHOUT='$(WITHOUT)'"
	@cmp -s $(OUT)/config.want $(OUT)/config.mk || cp $(OUT)/config.want $(OUT)/config.mk

# Stage, then the build, then the stage is removed. A failure before the touch leaves the stamp
# old, so the next run rebuilds (and starts by removing the stage).
$(FINAL_STAMP): $(FINAL_INPUTS) | $(OUT)
	$(Q)rm -rf $(STAGE) $(OUT)/src $(OUT)/tests
	$(Q)mkdir -p $(STAGE)/src $(STAGE)/tests
	$(Q)awk -v skip_loads='$(DROP_LOADS)' -v skip_mods='$(DROP_MODULES)' -v without='$(WITHOUT)' \
		-f tools/build/select-main.awk $(MAIN_SRC) > $(STAGE)/src/main.maude
	$(Q)$(if $(filter 1,$(INVISIBLE)),echo 'load invisible-reduction' >> $(STAGE)/src/main.maude)
	$(Q)$(if $(SRC_FILES),cp $(SRC_FILES) $(STAGE)/src/)
	$(Q)cp -R tests/. $(STAGE)/tests/
	$(Q)cp $(RUNNERS) $(STAGE)/tests/
	$(Q)printf '%s\n' $(addprefix 'api ',$(API_TESTS)) $(addprefix 'check ',$(CHECK_TESTS)) $(if $(API_FULL),api-full) > $(STAGE)/tests.txt
	$(Q)mv $(STAGE)/src $(STAGE)/tests $(STAGE)/tests.txt $(OUT)/
	$(Q)rm -rf $(STAGE)
	$(Q)touch $@

# Run both suites in $(OUT)/tests. Each runs even if the other fails; the exit status
# is nonzero if either fails.
test: src
	@status=0; \
	$(MAKE) --no-print-directory test-api || status=1; \
	$(MAKE) --no-print-directory test-checks || status=1; \
	exit $$status

test-api: src
	@if [ -z '$(API_TESTS)' ]; then echo '[test-suite] no API tests selected'; else python3 $(OUT)/tests/run-api-suite.py $(VFLAG) $(API_ARGS); fi

test-checks: src
	@if [ -z '$(CHECK_TESTS)' ]; then echo '[checks] no check files selected'; else python3 $(OUT)/tests/run-checks.py $(VFLAG) $(CHECK_ARGS); fi

# Static documentation site from src/ (tools/docs/gen-docs.py). Independent of NAME and the features.
# tools/docs/input/overview.html is the hand-written body of index.html. tools/docs/input/examples.json
# (tools/docs/gen-examples.py) gives the verified examples shown, collapsed, under rules and ops.
docs: tools/docs/input/overview.html tools/docs/gen-docs.py tools/docs/input/examples.json
	python3 tools/docs/gen-docs.py --src src --out build/docs --overview tools/docs/input/overview.html --examples tools/docs/input/examples.json --check

help config:
	@echo "NAME=$(NAME)  OUT=$(OUT)  MAUDE=$(MAUDE)"
	@echo "features:"
	@$(foreach F,$(FEATURES),echo '  $(F)=$($(F))';) echo '  WITHOUT=$(WITHOUT)'
	@echo "  V=$(V)  (display only: not cached; V=1 streams test output)"
	@echo "tests (relative to tests/):"
	@echo "  api:    $(API_TESTS)"
	@echo "  checks: $(CHECK_TESTS)"
	@echo "targets:"
	@echo "  make [src]     build $(OUT)/src and $(OUT)/tests"
	@echo "  make test      test-api and test-checks (V=1: stream each file's Maude output)"
	@echo "  make test-api  API-level tests (run-api-suite.py)"
	@echo "  make test-checks  search/red checks (run-checks.py)"
	@echo "  make docs      build the documentation site in build/docs (open build/docs/index.html)"
	@echo "  make clean     remove $(OUT)"
	@echo "  make distclean remove build/"

clean:
	rm -rf $(OUT)

distclean:
	rm -rf build

$(OUT):
	@mkdir -p $@

# Desired settings, rewritten only when they change. The build depends on it.
# Copied to the cache config.mk (read by the next run) after a successful build.
# Gated so help/clean never create build directories.
ifeq (,$(filter help config clean distclean docs,$(MAKECMDGOALS)))
$(OUT)/config.want: FORCE | $(OUT)
	@printf '%s\n' '# Generated by Makefile. Do not edit.' $(foreach F,$(FEATURES),'$(F) := $($(F))') 'WITHOUT := $(WITHOUT)' > $@.tmp
	@if cmp -s $@.tmp $@; then rm -f $@.tmp; else mv $@.tmp $@; fi

# Selected input files (sorted), rewritten only when the list changes. A deleted or added file
# changes the list, so the stamp becomes stale.
$(OUT)/.inputs: FORCE | $(OUT)
	@printf '%s\n' $(sort $(SRC_FILES) $(TEST_SRCS)) > $@.tmp
	@if cmp -s $@.tmp $@; then rm -f $@.tmp; else mv $@.tmp $@; fi
endif

FORCE:
