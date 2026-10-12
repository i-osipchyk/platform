# Changelog

## v0.1.0 (unreleased)

First version.
- `bootstrap/`: state bucket, GitHub OIDC with `gh-deploy-<slug>` roles, ECR per project,
  `platform-alarms` topic + Telegram notifier, per-project budgets, cost allocation tags.
- `terraform/modules/lambda-app`: container Lambdas + Scheduler + alarms. `data-bucket`.
- Reusable workflows `python-ci.yml`, `lambda-deploy.yml`.
- `llmkit` 0.1.0: `LLM.chat` (OpenAI-compatible), `Tracker.record` (any SDK), DeepSeek/OpenAI/
  Anthropic usage normalisation, price table with DeepSeek peak/off-peak, JSONL / stdout /
  Langfuse (OTLP) sinks, `llmkit report`.
- copier template (`uses_llm`, `deploy_lambda`, `envs`).
- Docs: conventions, deployment, LLM observability, new project, adoption plan.
