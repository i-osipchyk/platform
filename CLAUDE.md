# platform

The shared standards and building blocks for every project in `~/dev`. Other repos consume it
**by tag** from GitHub (`i-osipchyk/platform`), so a change here reaches a project only when it
bumps its pin.

## Layout

- `docs/`: conventions, deployment, llm-observability, new-project, adoption (status of the existing projects)
- `bootstrap/`: account-level Terraform, applied by hand once (state bucket, OIDC, deploy roles, ECR, alarms, budgets)
- `terraform/modules/{lambda-app,data-bucket}`: project-level modules
- `.github/workflows/{python-ci,lambda-deploy}.yml`: reusable workflows (`workflow_call`)
- `llmkit/`: Python package (stdlib core + optional openai) that every LLM call goes through
- `copier.yml` + `template/`: new-project template (Jinja; `.jinja` files are rendered, others copied as is)
- `scripts/check.sh`: runs all checks. `scripts/test-template.sh` renders every template combination and tests it

## Commands

- Everything: `scripts/check.sh`
- llmkit: `cd llmkit && uv run pytest && uv run ruff check . && uv run mypy`
- Terraform: `terraform fmt -recursive`, then for each dir: `terraform init -backend=false && terraform validate`
- Template: `scripts/test-template.sh` (`KEEP=1` keeps the rendered projects)

## Rules for changing this repo

- **Never `terraform apply` bootstrap** without the user asking in the current conversation. It
  changes IAM for every project.
- Changes to a module variable, a workflow input, the llmkit public API or a template question
  are interface changes. Keep them backward compatible where possible. Otherwise note the
  migration in `CHANGELOG.md`.
- The deploy role's permissions (`bootstrap/github_oidc.tf`) and the resource names produced by
  the modules must stay in sync: everything a project creates must be named `<slug>-*`.
- Keep `docs/` true. If you change behaviour, update the doc that describes it in the same commit.
  `docs/adoption.md` tracks the migration status of existing projects.
- Prices in `llmkit/src/llmkit/pricing.py` must come from the provider's pricing page, fetched at
  the time. Bump `PRICING_VERIFIED_ON`. Never guess a price; an unknown model stays unpriced.
- In template files, GitHub Actions expressions must be wrapped in `{% raw %}…{% endraw %}`.
- Run `scripts/check.sh` before committing.
