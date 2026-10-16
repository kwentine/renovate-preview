# renovate-preview
#
#   make install     put renovate-preview on your PATH, running from this checkout
#   make uninstall   take it off again
#   make test        unit tests (never run Renovate)
#   make examples    check examples/ against a real Renovate (RUNTIME=local to skip containers)
#   make lint        ruff
#   make build       wheel and sdist, for PyPI

PREFIX ?= $(HOME)/.local
BINDIR ?= $(PREFIX)/bin
PYTHON ?= python3
RUNTIME ?= auto

SRC    := $(abspath src)
TARGET := $(BINDIR)/renovate-preview

export PYTHONPATH := $(SRC)

.PHONY: install uninstall test examples lint build clean

# A launcher rather than a copy, so edits to the checkout take effect immediately.
# `rm -f` first: writing through a symlink left by an older install would clobber its target.
install:
	@mkdir -p $(BINDIR)
	@rm -f $(TARGET)
	@printf '#!/bin/sh\nPYTHONPATH="$(SRC)" exec $(PYTHON) -m renovate_preview "$$@"\n' > $(TARGET)
	@chmod +x $(TARGET)
	@echo "installed $(TARGET)"

uninstall:
	rm -f $(TARGET)

test:
	$(PYTHON) -m unittest discover -s tests -t . -q

examples:
	RENOVATE_PREVIEW_E2E=1 RENOVATE_PREVIEW_RUNTIME=$(RUNTIME) \
	  $(PYTHON) -m unittest tests.test_examples -v

lint:
	ruff check src tests && ruff format --check src tests

build:
	uv build

clean:
	rm -rf dist build
	find src tests -name __pycache__ -type d -exec rm -rf {} +
