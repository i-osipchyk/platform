#!/usr/bin/env bash
# Every check this repo has. CI runs the same script.
set -euo pipefail
ROOT="$(cd "$(dirname "$0")/.." && pwd)"
cd "$ROOT"

echo "== llmkit"
(cd llmkit && uv sync -q && uv run pytest -q && uv run ruff check -q . && uv run mypy)

echo "== notifier"
uvx --quiet ruff check -q --line-length 100 --select E,F,W,I,B,UP bootstrap/notifier

echo "== terraform"
terraform fmt -check -recursive
for dir in bootstrap terraform/modules/lambda-app terraform/modules/data-bucket; do
  terraform -chdir="$dir" init -backend=false -input=false >/dev/null
  terraform -chdir="$dir" validate -no-color
done

echo "== template"
scripts/test-template.sh
