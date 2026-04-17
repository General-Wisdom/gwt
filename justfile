# Use bash with strict flags
set shell := ["bash", "-euo", "pipefail", "-c"]

# CI/output mode detection (inspired by monorepo pattern)
ci := env("CI", "false")
output_mode := env("OUTPUT_MODE", if ci == "true" { "normal" } else { "minimal" })
shellcheck_format := if ci == "true" { "gcc" } else { "tty" }

# Always route commands through the OUTPUT_MODE-aware wrapper
WRAP := "tools/agent-wrap.sh"

# Source sets for Python tooling
SRCS := "gwt.py gwtlib tests"

# Default recipe
default: help

alias c := check
alias t := test
alias f := fmt
alias fc := fmt-check
alias l := lint
alias tc := type-check
alias b := build
alias i := install-local
alias cs := check-shell
alias bv := build-validate

help:
  @echo "gwt tooling commands:"
  @echo "  just check         # fmt-check, lint, type-check, test"
  @echo "  just fmt           # ruff format (all Python)"
  @echo "  just fmt-check     # ruff format --check (all Python)"
  @echo "  just lint          # ruff check (E,F,I)"
  @echo "  just type-check    # ty (non-blocking) on all Python"
  @echo "  just test          # pytest"
  @echo "  just check-shell   # shellcheck for bash/sh scripts"
  @echo "  just build         # create dist/gwt"
  @echo "  just build-validate# build + smoke test"
  @echo "  just install-local # symlink dist/gwt -> ~/.local/bin/gwt"
  @echo "  just clean         # remove build/test artifacts"
  @echo ""
  @echo "OUTPUT_MODE: minimal | json | normal | verbose"

# Formatting
fmt:
  {{WRAP}} uvx ruff format {{SRCS}}

fmt-check:
  {{WRAP}} uvx ruff format --check {{SRCS}}

# Linting with pragmatic rules (configured in pyproject)
lint:
  {{WRAP}} uvx ruff check {{SRCS}}

# Type checking (Ty is alpha; non-blocking)
type-check:
  {{WRAP}} uvx ty check {{SRCS}} --exit-zero

# Test suite
test:
  {{WRAP}} uvx pytest

# Aggregate check
check:
  just fmt-check
  just lint
  just type-check
  just test

# Build executable (gwt.py + gwtlib package)
build:
  rm -rf dist
  mkdir -p dist
  cp gwt.py dist/gwt
  cp -r gwtlib dist/gwtlib
  chmod +x dist/gwt
  @echo "Built dist/gwt"

# Build + smoke test
build-validate: build
  ./dist/gwt --help >/dev/null

# Shell scripts - ShellCheck (all tracked scripts with bash/sh shebang)
check-shell:
  #!/usr/bin/env bash
  set -euo pipefail
  echo "Running ShellCheck on all shell scripts..."
  mapfile -t scripts < <(git ls-files | while read -r f; do
    if [ -f "$f" ] \
       && [[ "$f" != *.tpl ]] \
       && head -n1 "$f" | grep -qE "^#!.*\<(bash|sh)\>"; then
      echo "$f"
    fi
  done)
  if [ ${#scripts[@]} -eq 0 ]; then
    echo "No shell scripts found."
    exit 0
  fi
  echo "Files:"; printf " - %s\n" "${scripts[@]}"
  shellcheck -f {{ shellcheck_format }} "${scripts[@]}"

# Install to ~/.local/bin (common on Linux/macOS)
install-local: build
  mkdir -p "$HOME/.local/bin"
  ln -sfn "$(pwd)/dist/gwt" "$HOME/.local/bin/gwt"
  @echo "Installed to \$HOME/.local/bin/gwt (symlink). Ensure \$HOME/.local/bin is in PATH."

# Clean artifacts
clean:
  rm -rf dist .pytest_cache __pycache__ **/__pycache__
