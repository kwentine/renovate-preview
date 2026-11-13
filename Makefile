# renovate-preview
#
#   make help          show this helpful list
#   make install       put renovate-preview on your PATH, running from this checkout
#   make uninstall     take it off again
#   make check         unit tests (never run Renovate); also: make test
#   make examples      check examples/ against a real Renovate, in podman or docker
#   make lint          ruff
#   make build         wheel and sdist, in dist/
#   make distcheck     build, check the metadata, and run the wheel as installed
#   make publish       upload dist/ to PyPI (needs UV_PUBLISH_TOKEN): see docs/packaging.org
#   make clean
#
# Variables follow the GNU conventions, and can be set on the command line:
#   make install prefix=/usr/local              (default: ~/.local, so no root needed)
#   make install DESTDIR=/tmp/stage             (a staged install, e.g. for a package)
#   make publish PUBLISH_URL=https://test.pypi.org/legacy/      (TestPyPI first)
# https://www.gnu.org/prep/standards/html_node/Makefile-Conventions.html
# One exception: no `all`. There is nothing to build: `install` runs this checkout.

SHELL = /bin/sh
.SUFFIXES:

prefix      = $(HOME)/.local
exec_prefix = $(prefix)
bindir      = $(exec_prefix)/bin

INSTALL     = install
PYTHON      = python3
PUBLISH_URL = https://upload.pypi.org/legacy/

SRC      := $(abspath src)
LAUNCHER  = $(DESTDIR)$(bindir)/renovate-preview

export PYTHONPATH := $(SRC)

.DEFAULT_GOAL := help

.PHONY: help install uninstall check test examples lint build distcheck publish clean

help:
	@sed -n 's/^#   make/make/p' $(firstword $(MAKEFILE_LIST))

# A launcher rather than a copy of the code, so edits to the checkout take effect at once.
# `rm -f` first: writing through a symlink left by an older install would clobber its target.
install:
	$(INSTALL) -d $(DESTDIR)$(bindir)
	rm -f $(LAUNCHER)
	printf '#!/bin/sh\nPYTHONPATH="$(SRC)" exec $(PYTHON) -m renovate_preview "$$@"\n' > $(LAUNCHER)
	chmod 755 $(LAUNCHER)

uninstall:
	rm -f $(LAUNCHER)

check:
	$(PYTHON) -m pytest -q

test: check

examples:
	RENOVATE_PREVIEW_E2E=1 $(PYTHON) -m pytest tests/test_examples.py -v

lint:
	ruff check src tests && ruff format --check src tests

# dist/ is emptied first, so that `make publish` uploads this version only.
build:
	rm -rf dist
	uv build

distcheck: build
	uvx twine check --strict dist/*
	uv run --isolated --no-project --with dist/*.whl renovate-preview --version

publish: distcheck
	uv publish --publish-url $(PUBLISH_URL) dist/*

clean:
	rm -rf dist build
	find src tests -name __pycache__ -type d -exec rm -rf {} +
