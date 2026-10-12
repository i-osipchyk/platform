# bootstrap

Account-level Terraform, applied **by hand, once** (and again when a project is added).
Steps, first-apply caveats and the state migration: [../docs/deployment.md](../docs/deployment.md) § 1.

| File | Creates |
|---|---|
| `main.tf` | state bucket `tfstate-<account>-<region>`, ECR repo per project, budgets, cost allocation tags |
| `github_oidc.tf` | GitHub OIDC provider, `gh-deploy-<slug>` roles scoped to `<slug>-*` resources |
| `alerts.tf` | `platform-alarms` SNS topic, Telegram notifier Lambda (`notifier/handler.py`) |
