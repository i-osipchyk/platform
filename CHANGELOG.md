# Changelog

## Unreleased

- bootstrap: `platform-ctrader-token-refresh` (opt-in, `ctrader_token_refresh = true`) keeps the
  account's one cTrader token pair in `/platform/shared/ctrader-*` and is its only writer.
  Projects that each refreshed their own copy spent each other's refresh tokens. Projects: read
  `CTRADER_*` from the shared path, remove your own refresh, then turn the refresher on
  (conventions.md § Secrets, cTrader tokens). Bootstrap-only: no module or workflow change.
- docs: Troubleshooting entry for the symptom; adoption notes for gtb and ags.

## v0.1.2 (2026-10-12)

- Fix: `lambda-deploy.yml` hid a failed `terraform plan` (piped into `tee` without `pipefail`),
  so the job went on to fail in apply with "Cannot apply incomplete plan", and the plan's error
  (stderr) was in neither the summary nor `plan.txt`. The plan step now fails on error and shows
  it in the job summary. Projects: bump every pin to `v0.1.2`; llmkit and the modules are
  unchanged.
- bootstrap: the deploy role may call `states:ValidateStateMachineDefinition` (evaluated against
  `stateMachine:*`). Projects with a Step Functions state machine (ags) failed their first deploy
  without it. Bootstrap-only: re-apply bootstrap, no new tag.
- bootstrap: the `gh-deploy-<slug>` trust policy also accepts GitHub's OIDC subject with
  immutable ids (`repo:<owner>@<id>/<repo>@<id>:environment:<env>`). Without it, repos whose
  tokens use that form fail with "Not authorized to perform sts:AssumeRoleWithWebIdentity".
  Bootstrap-only (applied from a checkout): no new tag needed for projects.
- bootstrap: the `region` variable rejects an empty or malformed value at plan time.
- docs: `deployment.md` § Troubleshooting (empty region, existing OIDC provider / ECR repo, OIDC
  subject mismatch, Lambda concurrency quota of 10) and a quota check in bootstrap § 1;
  `reserved_concurrency` caveat in conventions, the `lambda-app` README and adoption (bondhype).

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
