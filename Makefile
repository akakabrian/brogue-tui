VENDOR := vendor/BrogueCE
# All Brogue engine sources. Everything under src/brogue + src/variants;
# from src/platform we pull in only the shared helpers (platformdependent,
# null-platform) plus our py-platform.c. `main.c` is explicitly excluded
# — that CLI shim is replaced by brogue_run() in py-platform.c.
BROGUE_SRC := $(wildcard $(VENDOR)/src/brogue/*.c) \
              $(wildcard $(VENDOR)/src/variants/*.c) \
              $(VENDOR)/src/platform/platformdependent.c \
              $(VENDOR)/src/platform/null-platform.c \
              $(VENDOR)/src/platform/py-platform.c

LIB := vendor/libbroguepy.so

# Non-SDL, non-curses build — the py-platform is self-contained.
CFLAGS_BROGUE := -std=c99 -O2 -fPIC -w \
    -I$(VENDOR)/src/brogue -I$(VENDOR)/src/platform -I$(VENDOR)/src/variants \
    -DDATADIR=\".\" -DBROGUE_EXTRA_VERSION=\"\"

# macOS needs this to link against Python-ish undefined symbols — we
# don't actually use any here, but we keep the parity with the
# simcity-tui recipe so the Makefile is robust if this moves.
LDFLAGS_EXT := $(if $(filter Darwin,$(shell uname -s)),-undefined dynamic_lookup,)

.PHONY: all bootstrap engine run clean venv test test-only

all: bootstrap engine venv

bootstrap: $(VENDOR)/.git $(VENDOR)/src/platform/py-platform.c
$(VENDOR)/.git:
	@echo "==> fetching BrogueCE into vendor/ (~15 MB, one time)"
	@mkdir -p vendor
	git clone --depth=1 https://github.com/tmewett/BrogueCE.git $(VENDOR)
	@echo "==> placing custom py-platform.c"
	@cp vendor-patches/py-platform.c $(VENDOR)/src/platform/py-platform.c 2>/dev/null || true

engine: $(LIB)

$(LIB): $(BROGUE_SRC)
	@mkdir -p vendor
	gcc -shared $(LDFLAGS_EXT) $(CFLAGS_BROGUE) $(BROGUE_SRC) -lm -o $(LIB)

venv: .venv/bin/python
.venv/bin/python:
	python3 -m venv .venv
	.venv/bin/pip install -e .

run: venv $(LIB)
	.venv/bin/python brogue.py

# Full test suite — TUI scenarios via Pilot + perf bench.
test: venv $(LIB)
	.venv/bin/python -m tests.qa
	.venv/bin/python -m tests.perf

# Filtered subset. Usage: make test-only PAT=cursor
test-only: venv $(LIB)
	.venv/bin/python -m tests.qa $(PAT)

clean:
	rm -f $(LIB)
