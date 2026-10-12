# Changelog

## v0.1.1 (2026-10-12)

- Fix: `lambda-deploy.yml` failed to parse on GitHub ("Invalid workflow file", line 39). An
  input description contained an unquoted `: `. Another description held a `${{ }}`
  expression, which isn't allowed there. Both are now quoted plain text.
- `scripts/check.sh` and the template test now run actionlint on every workflow, using the same
  YAML and expression rules as GitHub.
- Template, docs and examples pin `v0.1.1`. **Projects must not reference
  `lambda-deploy.yml@v0.1.0`.** llmkit and the Terraform modules are unchanged from v0.1.0.

## v0.1.0 (2026-10-12)

First version.
- `bootstrap/`: state bucket, GitHub OIDC with `gh-deploy-<slug>` roles, ECR per project,
  `platform-alarms` topic + Telegram notifier, per-project budgets, cost allocation tags.
- `terraform/modules/lambda-app`: container Lambdas + Scheduler + alarms. `data-bucket`.
- Reusable workflows `python-ci.yml`, `lambda-deploy.yml`.
- `llmkit` 0.1.0: `LLM.chat` (OpenAI-compatible), `Tracker.record` (any SDK), DeepSeek/OpenAI/
  Anthropic usage normalisation, price table with DeepSeek peak and off-peak rates as
  documented (deepseek-flash, deepseek-v4-flash, deepseek-v4-pro), JSONL / stdout /
  Langfuse (OTLP) sinks, `llmkit report`.
- copier template (`uses_llm`, `deploy_lambda`, `envs`).
- Region: `AWS_REGION` everywhere, defaulting to `eu-west-1` (no region baked into generated files).
- Docs: conventions, deployment, LLM observability, new project, adoption plan.
