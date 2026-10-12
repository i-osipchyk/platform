# platform

Shared defaults for every project in `~/dev`: how they're built, deployed and observed.

| Need | Use | Docs |
|---|---|---|
| Start a project | copier template (`copier.yml` + `template/`) | [docs/new-project.md](docs/new-project.md) |
| Naming, tags, secrets, envs, Python tooling | conventions | [docs/conventions.md](docs/conventions.md) |
| AWS account setup (once) | `bootstrap/` | [docs/deployment.md](docs/deployment.md) |
| Scheduled container Lambdas | `terraform/modules/lambda-app` | [module README](terraform/modules/lambda-app/README.md) |
| Data buckets | `terraform/modules/data-bucket` | |
| CI | `.github/workflows/python-ci.yml` (reusable) | [docs/deployment.md](docs/deployment.md) |
| Deploy | `.github/workflows/lambda-deploy.yml` (reusable) | [docs/deployment.md](docs/deployment.md) |
| LLM calls, cost per project | `llmkit/` | [docs/llm-observability.md](docs/llm-observability.md), [llmkit/README.md](llmkit/README.md) |
| Bringing an existing project in line | | [docs/adoption.md](docs/adoption.md) |
| Putting this repo on GitHub, releasing versions | | [docs/publishing.md](docs/publishing.md) |

## Stack in one paragraph

Python 3.12 + uv. Terraform state lives in one S3 bucket per account, using native locking.
Every project ships one arm64 container image to its own ECR repo and runs it as one Lambda per
handler. EventBridge Scheduler triggers the handlers, and Lambda errors raise alarms that go to
Telegram through one shared notifier. Secrets are SSM SecureStrings at `/<slug>/<env>/*`. GitHub
Actions deploys with OIDC: demo, then live after your approval, running the same image digest.
Every LLM call goes through llmkit, which records project / env / component, tokens and cost
(including DeepSeek's peak/off-peak pricing). Records go to local JSONL, CloudWatch and Langfuse.

## Versioning

One version for the whole repo: tags `vMAJOR.MINOR.PATCH`. Projects pin it in three places, all
written by the template, and `copier update` bumps all three:
- Terraform module sources (`?ref=v0.1.2`)
- reusable workflows (`@v0.1.2`)
- llmkit (`tag = "v0.1.2"`)

First-time setup (create the GitHub repo, push, tag `v0.1.2`) and the release steps:
[docs/publishing.md](docs/publishing.md).

Breaking changes (module variables, workflow inputs, llmkit API) bump MINOR while on `0.x` and are
called out in the changelog with the migration.

## Develop

```bash
scripts/check.sh                 # everything below
cd llmkit && uv run pytest       # llmkit
terraform fmt -check -recursive  # terraform style
scripts/test-template.sh         # render every template combination and test the results
```
