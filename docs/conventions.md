# Conventions

The defaults every `~/dev` project follows. Each rule has a **why**, so you can tell when a
project really needs to differ. Deviations go in that project's `CLAUDE.md`, along with the reason.

## Identity: one slug per project

Every project has a **slug**: 2–16 characters, lowercase letters and digits, no dashes
(`gtb`, `bondhype`, `ags`). The same slug is used for:

| Where | Form |
|---|---|
| AWS resource names | `<slug>-<env>-<name>` (e.g. `gtb-demo-morning`) |
| `project` tag (cost allocation) | `<slug>` |
| Terraform state key | `<slug>/<env>.tfstate` |
| SSM secrets | `/<slug>/<env>/<kebab-name>` |
| ECR repository | `<slug>` |
| GitHub deploy role | `gh-deploy-<slug>` |
| llmkit `LLMKIT_PROJECT` | `<slug>` |

**Why:** one key joins AWS cost, LLM cost, logs and permissions. The deploy role is limited to
`<slug>-*` resources, so the naming *is* the security boundary. No dashes, because a dash already
separates the slug from the env and the name.

## Environments

- `demo` (paper/demo account) and `live` (real money), or just `live` for forward tests that
  need no staging. `local` is your laptop and only appears in llmkit records.
- Each env has its own Terraform state, SSM path, functions and buckets. demo and live run **the
  same image digest**.
- `live` is a GitHub Environment with a required reviewer. Nothing reaches live without your click.

## Region: `AWS_REGION`, default `eu-west-1`

The region is set in exactly one place, the **`AWS_REGION` env var**. When it isn't set, every
consumer falls back to **`eu-west-1`**. Never hard-code a region anywhere else.

| Consumer | How it gets the region |
|---|---|
| Python (boto3, scripts) | `config.load_env()` does `os.environ.setdefault("AWS_REGION", "eu-west-1")` and mirrors it to `AWS_DEFAULT_REGION`. Clients are created after that, so they need no `region_name` |
| Lambda | the runtime sets `AWS_REGION` itself (it's reserved; never set it in Terraform) |
| Shell scripts | `AWS_REGION="${AWS_REGION:-eu-west-1}"` at the top |
| Terraform roots | `variable "region" { default = "eu-west-1" }` → `provider "aws" { region = var.region }`. Terraform can't read arbitrary env vars, so CI passes `-var region=$AWS_REGION`, and locally `TF_VAR_region` mirrors it (shell profile below) |
| Terraform modules | inherit the provider's region (`data "aws_region"`), so they have no region variable |
| GitHub Actions | repo variable `AWS_REGION` (optional), passed as `aws-region: ${{ vars.AWS_REGION \|\| 'eu-west-1' }}`. The reusable workflow falls back to eu-west-1 too |
| `.env` / `.env.example` | always contains `AWS_REGION=eu-west-1` |

Shell profile (`~/.zshrc`), so local tools and Terraform agree:
```bash
export AWS_REGION="${AWS_REGION:-eu-west-1}"
export TF_VAR_region="$AWS_REGION"
```

The region is also part of the state bucket name (`tfstate-<account>-<region>`). A project in
another region therefore needs bootstrap applied in that region too.

## AWS / Terraform

- Terraform `>= 1.10`, AWS provider `~> 6.0`.
- State lives in S3 in the account's bucket `tfstate-<account>-<region>`, using native S3 locking
  (`use_lockfile = true`). Backend config is partial and passed at `init`. **Never use local state.**
- Default tags on every provider: `project`, `env`, `managed_by = "terraform"`, `repo`.
- Scheduled work uses **container Lambdas**:
  - one image per project, with one function per handler (`image_config.command`)
  - arm64, with the image pinned **by digest**
  - built from `uv.lock` in a multi-stage Dockerfile
  - use the `lambda-app` module rather than hand-writing these resources
- Schedules use **EventBridge Scheduler**, not EventBridge rules. **Why:** it supports time zones
  and one-off `at()` schedules.
- Reserved concurrency only once the account's Lambda concurrency quota is above 10 (new
  accounts start at 10, and AWS keeps 10 unreserved). Bootstrap § 1 requests the raise.
- Async retries default to **0**. **Why:** a retried trading job can act twice. Raise retries only
  for idempotent handlers.
- Data buckets use the `data-bucket` module: versioned, encrypted, private, `prevent_destroy`.
  Function roles get no `DeleteObject`, so stored data is append-only.
- Alarms go to the shared `platform-alarms` SNS topic, which forwards to Telegram. Don't build a
  separate alarm forwarder in each project.
- Log retention is 90 days unless the project needs an audit trail (gtb keeps 400).

## Secrets

- **SSM Parameter Store SecureString** at `/<slug>/<env>/<kebab-name>`. Secrets shared by every
  project (the Langfuse keys) go in `/platform/shared/*`. **Why not Secrets Manager:** it costs
  $0.40 per secret per month and adds nothing here unless you need managed rotation.
- Values are set by `scripts/put_secrets.py` (from `.env`), **never by Terraform**. Otherwise they
  end up in state.
- At Lambda cold start, `config.load_env()` copies parameters into `os.environ`, mapping kebab-case
  names to UPPER_SNAKE (`deepseek-api-key` → `DEEPSEEK_API_KEY`). The same code then works locally
  with `.env`.
- `.env` is gitignored. `.env.example` is committed and lists every key.

## Deployment

- **GitHub Actions only.** On every push, CI runs. A successful CI run on `main` triggers Deploy,
  which goes demo, then live (live after approval). Projects call the platform's reusable
  workflows, pinned to a tag.
- AWS access uses **OIDC** (no stored keys). Each project has its own role, trusted only from
  that repo's GitHub Environments.
- Never run `terraform apply` from a laptop against demo or live. `plan` locally is fine.
- Rollback: re-run the Deploy workflow for the last good commit (Actions → the old run →
  Re-run). The image for that commit still exists, so it redeploys as is.

## Python

- **uv** with a committed `uv.lock`, Python 3.12, `src/` layout, hatchling.
- ruff: line length 100, rules `E F W I B UP SIM`. mypy `strict` on the package.
- pytest. Tests that hit real services are marked `integration` and excluded by default.
- Configuration comes from env vars, loaded once at start-up (`config.load_env()`).

## LLM calls

- **Every call goes through llmkit**, and every call names its `component`. Each project has one
  module (`llm.py`) that owns the client. See `llm-observability.md`.
- Use temperature 0 and structured output (JSON mode or tool calling) for anything a program parses.
- Version prompts as files (`prompts/<name>_v<N>.txt`) and pass the id as `prompt_id`.
  **Why:** when a prompt changes you can see its effect on behaviour and cost.
- Use one DeepSeek API key per project. **Why:** it gives a provider-side cross-check of
  llmkit's numbers.

## Repo hygiene

- Every project has a `CLAUDE.md` holding its commands, layout and domain rules. Shared rules
  live in `~/dev/CLAUDE.md` and in this repo, so don't copy them into projects.
- Never commit: `.env`, `*.tfstate*`, `tfplan`, `infra/.terraform/`.
