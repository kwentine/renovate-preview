# renovate_check - install, test and lint the Renovate debugging helper.
#
#   make install     symlink it into ~/.local/bin as renovate_check
#   make test        unit tests (fast, no Renovate needed)
#   make examples    end-to-end fixtures (needs podman, or RUNTIME=local)
#   make check       both
#   make uninstall   remove the symlink

PREFIX  ?= $(HOME)/.local
BINDIR  ?= $(PREFIX)/bin
NAME    ?= renovate-preview
# Override to exercise a user install of Renovate instead of the podman image:
#   make examples RUNTIME=local
RUNTIME ?= podman

HERE    := $(abspath $(dir $(lastword $(MAKEFILE_LIST))))
SCRIPT  := $(HERE)/renovate.py
TARGET  := $(BINDIR)/$(NAME)
PYTHON  ?= python3

.PHONY: help install uninstall test examples examples-network check lint

help:
	@sed -n '2,8p' $(firstword $(MAKEFILE_LIST)) | sed 's/^# \?//'
	@echo
	@echo "Variables: PREFIX=$(PREFIX) NAME=$(NAME) RUNTIME=$(RUNTIME)"

# A symlink rather than a copy, deliberately: renovate.py finds renovate-examples/ next to
# its own resolved path, so a symlink keeps `renovate_check --examples` working and picks up
# edits to the checkout immediately. A copy would be a stale orphan with no fixtures.
install: $(SCRIPT)
	@mkdir -p $(BINDIR)
	@ln -sfn $(SCRIPT) $(TARGET)
	@echo "installed $(TARGET) -> $(SCRIPT)"
	@case ":$$PATH:" in \
	  *":$(BINDIR):"*) ;; \
	  *) echo "warning: $(BINDIR) is not on your PATH" ;; \
	esac

uninstall:
	@if [ -L $(TARGET) ]; then rm -f $(TARGET) && echo "removed $(TARGET)"; \
	elif [ -e $(TARGET) ]; then echo "refusing to remove $(TARGET): not a symlink"; exit 1; \
	else echo "$(TARGET) is not installed"; fi

test:
	@$(PYTHON) $(HERE)/test_renovate.py

examples:
	@$(SCRIPT) --runtime=$(RUNTIME) --examples

examples-network:
	@$(SCRIPT) --runtime=$(RUNTIME) --examples --with-network

check: test examples

lint:
	@$(PYTHON) -m compileall -q $(SCRIPT) $(HERE)/test_renovate.py && echo "syntax ok"
