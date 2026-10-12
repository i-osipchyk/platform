#!/usr/bin/env bash
# Render the copier template in every feature combination and check each result:
# uv sync, ruff, mypy, pytest, actionlint, and (for deploy_lambda) terraform validate.
# llmkit and the Terraform modules are pointed at this checkout, so nothing needs to be pushed.
#
#   scripts/test-template.sh            # all combinations
#   KEEP=1 scripts/test-template.sh     # keep the rendered projects for inspection
set -euo pipefail

ROOT="$(cd "$(dirname "$0")/.." && pwd)"
OUT="$(mktemp -d)"
[[ -z "${KEEP:-}" ]] && trap 'rm -rf "$OUT"' EXIT

# Render from a plain (non-git) copy of the working tree: tests uncommitted changes too.
SRC="$OUT/_template"
rsync -a --exclude .git --exclude .venv --exclude .terraform "$ROOT/" "$SRC/"

render() {  # name uses_llm deploy_lambda envs
  local dest="$OUT/$1"
  uvx --quiet copier copy --quiet --trust --defaults \
    -d project_name="$1" -d description="template test" \
    -d uses_llm="$2" -d deploy_lambda="$3" -d envs="$4" \
    "$SRC" "$dest" >&2
  echo "$dest"
}

check() {
  local dest="$1" deploy="$2"
  (
    cd "$dest"
    # Use the local llmkit / modules instead of the (possibly unpushed) GitHub tag.
    sed -i.bak -E "s#^llmkit = \{.*#llmkit = { path = \"$ROOT/llmkit\" }#" pyproject.toml && rm pyproject.toml.bak
    uv lock -q && uv sync -q
    uv run ruff check -q .
    uv run mypy
    uv run pytest -q
    if [[ "$deploy" == "true" ]]; then
      terraform -chdir=infra fmt -check
      sed -i.bak -E "s#source *= \"git::https://github.com/[^/]+/platform.git//terraform/modules/([a-z-]+)\?ref=[^\"]+\"#source = \"$ROOT/terraform/modules/\1\"#" infra/main.tf && rm infra/main.tf.bak
      terraform -chdir=infra init -backend=false -input=false >/dev/null
      terraform -chdir=infra validate -no-color
    fi
    uvx --quiet --from actionlint-py actionlint .github/workflows/*.yml   # same parser rules as GitHub
  )
}

for combo in "plain false false demo,live" "llm-only true false demo,live" \
             "lambda-only false true live" "full true true demo,live"; do
  read -r name llm deploy envs <<<"$combo"
  echo "=== $name (uses_llm=$llm deploy_lambda=$deploy envs=$envs)"
  dest="$(render "tpl-$name" "$llm" "$deploy" "$envs")"
  check "$dest" "$deploy"
done
echo "all template combinations OK${KEEP:+ (kept in $OUT)}"
