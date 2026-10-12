# Adopting the platform in existing projects

Status and migration plan for the projects that existed before the platform (scanned
2026-10-12). Update the table as steps land.

| Project | Slug | CI | State in S3 | lambda-app module | GH Actions deploy | llmkit |
|---|---|---|---|---|---|---|
| gold-tokyo-breakout | `gtb` | ☐ | ✅ (own bucket) | ☐ | ☐ (`scripts/deploy.sh`) | n/a (no LLM) |
| polymarket-bond-hype | `bondhype` | ☐ | ❌ **local state** | ☐ | ☐ (manual) | ☐ (usage discarded) |
| ags-trading-agentic-system | `ags` | ✅ (`ruff: false`) | ✅ (`ags/live.tfstate`) | ✅ | ✅ (`live` only; first deploy in progress) | ✅ |
| hermes | `hermes` | ☐ | n/a (local only) | n/a | n/a | ☐ |

Order: **bootstrap** → **bondhype state** (biggest risk) → **llmkit everywhere** (cost
visibility) → **CI everywhere** (cheap) → **gtb and bondhype onto modules and Actions** → **ags
builds on the platform**.

Each step is one PR in the project repo. Terraform migrations get a reviewed `plan` showing **only
moves and in-place updates** before anything is applied. Read each plan for `destroy` and stop if
you see one you didn't expect.

---

## Everyone: CI (quick win)

Add `.github/workflows/ci.yml`:

```yaml
name: CI
on: [push, pull_request, workflow_dispatch]
permissions: { contents: read }
jobs:
  ci:
    uses: i-osipchyk/platform/.github/workflows/python-ci.yml@v0.1.1
    with:
      mypy: false        # true where mypy already passes (gtb, hermes)
```

- hermes keeps its dev tools in the `dev` *extra*, so it needs `sync-args: "--extra dev"`.
- ags has no ruff configured, so it needs `ruff: false` until it does.

## polymarket-bond-hype (`bondhype`)

What the scan found:
- **Terraform state exists only on the laptop** (`infra/terraform.tfstate`).
- `deepseek.py` returns only the text, so **token usage is discarded** and live spend is unknown.
- The image uses a mutable `:latest` tag, `pip install` without the lockfile, and the default x86 architecture.
- Region: `variable "region"` already defaults to eu-west-1. `runtime.py` and `storage.py` create
  boto3 clients with no region, which works on Lambda but fails locally unless the profile sets
  one. Call `os.environ.setdefault("AWS_REGION", "eu-west-1")` (mirrored to `AWS_DEFAULT_REGION`)
  at start-up, as the template's `load_env()` does. `.env` has no `AWS_REGION` yet, so add it.
- EventBridge rules (not Scheduler), one role per function, a project-specific alarm → Telegram
  Lambda, no default tags, AWS provider `~> 5.0`.

### 1. Move state to S3 (do first)
After bootstrap, add to `infra/main.tf`:
```hcl
terraform {
  backend "s3" {
    use_lockfile = true
    encrypt      = true
  }
}
```
```bash
export AWS_REGION="${AWS_REGION:-eu-west-1}" TF_VAR_region="${AWS_REGION:-eu-west-1}"
terraform -chdir=infra init -migrate-state \
  -backend-config="bucket=tfstate-<account>-$AWS_REGION" \
  -backend-config="key=bondhype/live.tfstate" -backend-config="region=$AWS_REGION"
# Answer yes, check `terraform plan` shows no changes, then delete the local tfstate files.
```

### 2. llmkit (small change)
Keep the urllib client and report the usage it already receives:
```python
# deepseek.py: inside complete(), around urlopen
started = datetime.now(UTC); t0 = time.perf_counter()
payload = json.load(response)
tracker.record(provider="deepseek", model=model, component=component,
               usage=Usage.from_openai(payload.get("usage")), started_at=started,
               latency_ms=(time.perf_counter() - t0) * 1000, prompt_id=prompt_id)
return payload["choices"][0]["message"]["content"]
```
- Thread `component` (`"reject"` / `"buy"`) and `prompt_id` through `LLMClient.complete`.
- Record failed attempts with `error=`.
- `data/llm_calls/` keeps the full audit trail; llmkit adds usage and cost.

### 3. Module + Actions
- Treat the single environment as `live` (bootstrap: `envs = ["live"]`). Names become
  `bondhype-live-<fn>`. That's a rename, so functions, roles and log groups get **replaced**. Do it
  in a quiet window. Leave the old log groups behind with `removed { lifecycle { destroy = false } }`
  blocks so their history stays.
- Bucket: `module "data" { source = ".../data-bucket"; name = var.bucket_name }` plus
  `moved { from = aws_s3_bucket.data, to = module.data.aws_s3_bucket.this }`, and the same for its
  versioning, encryption and public access block. The deploy role can only manage buckets named
  `bondhype-*`. If the current name doesn't start with that, keep the bucket outside Terraform's
  deploy role (data source) or rename it via copy.
- `functions` from `local.jobs`. Keep `reserved_concurrency = 1` for scanner and tracker, which
  needs the account's Lambda concurrency quota raised above 10 first (deployment.md § Troubleshooting).
  `schedules` from the rules.
- Drop the `alarm` function, the SNS topic and the alarm Lambda. The module sends alarms to `platform-alarms`.
- SSM: re-put secrets under `/bondhype/live/` (`deepseek-api-key`, `telegram-bot-token`,
  `telegram-chat-id`). Update `runtime.py` to load from `SECRETS_PATH`, or adopt the template's
  `config.load_env()`.
- Replace the Dockerfile with the template's (multi-stage, `uv export`, arm64). Keep the
  `COPY config` and `COPY prompts` lines.
- Add `deploy.yml` from the template with `envs = live`.
- ECR: the `bondhype` repo exists already (this project's `aws_ecr_repository.app`), so bondhype is
  left out of bootstrap's `projects` until now. Add it back (`envs = ["live"]`), `terraform import
  'aws_ecr_repository.project["bondhype"]' bondhype` in bootstrap, and replace the resource here with
  `removed { from = aws_ecr_repository.app; lifecycle { destroy = false } }`. Bootstrap makes the
  repo IMMUTABLE, so switch to the template's SHA-tag deploy in the same step.

## gold-tokyo-breakout (`gtb`)

The most mature stack. The platform module was extracted from it, and names already match
(`gtb-<env>-<fn>`, `gtb-<env>-lambda`, `gtb-<env>-records-<acct>`, schedule group `gtb-<env>`).

### 1. State → the shared bucket
```bash
export AWS_REGION="${AWS_REGION:-eu-west-1}" TF_VAR_region="${AWS_REGION:-eu-west-1}"
terraform -chdir=infra init -migrate-state \
  -backend-config="bucket=tfstate-<account>-$AWS_REGION" \
  -backend-config="key=gtb/demo.tfstate" -backend-config="region=$AWS_REGION"
```
After both envs are moved, delete the old `gtb-terraform-state-*` bucket.

### 2. Module, using `moved` blocks (no replacements expected)
```hcl
module "records" { source = ".../data-bucket?ref=v0.1.1"; project = "gtb"; env = var.env; purpose = "records" }
module "app" {
  source = ".../lambda-app?ref=v0.1.1"
  project = "gtb"; env = var.env; image_uri = var.image_uri
  functions = {
    morning   = { handler = "gtb.runs.app.morning_handler",   timeout = 360, retries = 2, max_event_age = 300 }
    heartbeat = { handler = "gtb.runs.app.heartbeat_handler", timeout = 120, retries = 2, max_event_age = 300 }
    eod       = { handler = "gtb.runs.app.eod_handler",       timeout = 360, retries = 2, max_event_age = 300 }
    watchdog  = { handler = "gtb.runs.app.watchdog_handler",  timeout = 360, retries = 2, max_event_age = 300 }
  }
  schedules         = merge(local.schedules, local.early_close)   # early-close one-offs as at(...)
  schedules_enabled = var.schedules_enabled
  write_bucket_arns = [module.records.arn]
  ssm_write         = true                                         # token refresh writes back
  log_retention_days = 400
}
moved { from = aws_lambda_function.run["morning"]  to = module.app.aws_lambda_function.fn["morning"] }
moved { from = aws_iam_role.lambda                 to = module.app.aws_iam_role.fn }
moved { from = aws_s3_bucket.records               to = module.records.aws_s3_bucket.this }
# … one per resource; `terraform plan` lists any you missed as create/destroy pairs.
```
- The S3 permissions in the module cover the whole bucket, while gtb's are scoped to
  `<env>/records/*`. Pass a tighter `extra_policy_json` instead if that scoping matters.
- Drop: `gtb-<env>-alarm` function, `gtb-<env>-alarms` topic and the per-env ECR repo. ECR is now the
  bootstrap-owned `gtb` repo. Use `removed { from = aws_ecr_repository.gtb; lifecycle { destroy = false } }`,
  then delete the old repo by hand once deploys use the new one.
- Default tag `project` changes from `gold-tokyo-breakout` to `gtb`. That's an in-place tag update on everything.

### 3. Actions instead of `scripts/deploy.sh`
- `deploy.sh` generates `early_close.auto.tfvars.json` before planning. Commit that file instead,
  plus a test asserting it matches `python -m gtb.schedules`, so CI needs no generation step.
- Region: `deploy.sh` and `put_secrets.py` currently *require* `AWS_REGION` and fail without it,
  and `variable "region"` has no default. Make all three fall back to eu-west-1
  (`${AWS_REGION:-eu-west-1}`, `os.getenv("AWS_REGION", "eu-west-1")`, `default = "eu-west-1"`).
- The "type 'live'" prompt becomes the required reviewer on the `live` GitHub Environment.
- Keep `deploy.sh --plan` for local plans if useful, but delete its apply path.

## ags-trading-agentic-system (`ags`)

Not deployed yet. Build Stages 27–30 of `docs/IMPLEMENTATION_PLAN.md` on the platform instead of
from scratch:
- **Stage 30 bootstrap** (state bucket, OIDC, deploy role) is done by platform `bootstrap/`.
  Remove it from the plan.
- **Region:** the plan says "Region from `AWS_REGION`". Keep that, with the eu-west-1 fallback
  (conventions.md § Region).
- **Secrets Manager → SSM** at `/ags/<env>/*`. For cTrader token refresh, set `ssm_write = true`.
- **Step Functions** go in `infra/` next to the module, named `ags-<env>-cycle`, with log group
  `/aws/vendedlogs/states/ags-<env>-*`. Grant the functions what they need with `extra_policy_json`.
- **Schedules:** Scheduler with `timezone = "America/New_York"` (the module supports it). The
  scheduler role in the module only invokes Lambdas. Schedules that start the state machine need
  their own target and role next to it.
- **The "wait for no RUNNING executions" step** becomes the `pre-apply` input of `lambda-deploy.yml`.
- **Alarms** go to `platform-alarms`. ags's own `notify` keeps sending the digest.
- **llmkit:** `DeepSeekChatClient.complete()` becomes `llm.chat(model=..., messages=..., tools=...,
  component=<agent name>, metadata={"commodity": ...})`. Add `component` to the `ChatClient`
  protocol. Wrap each pipeline run in `llm.trace(f"{commodity}-{run_id}")`.
- The **digest's "LLM spend by agent / commodity"** can come from CloudWatch Logs Insights over the
  llmkit lines (`logs:StartQuery` on `/aws/lambda/ags-*`), or from Langfuse's API. Either way it
  shouldn't need a second bookkeeping system.

## hermes (`hermes`)

Local only, and an AI advisor gates backtest trades.
- **Bug found:** `DeepSeekProvider._usage` sets `cache_read_tokens=0`. DeepSeek reports
  `prompt_cache_hit_tokens`, so every cached token is billed at the full input rate and backtest
  cost is overstated. Fix it with `Usage.from_openai(response.usage)`.
- `deepseek-flash`, the model in `.env`, has no row in hermes's price table and falls through to
  the generic `deepseek` row with stale rates. llmkit prices it correctly, including peak/off-peak.
- Replace `observability.compute_cost` / `_PRICING` with `llmkit.cost` so there's one table. Keep
  `LLMObservabilityLog`, which is a per-backtest result artefact. Feed it from llmkit's
  `CallRecord`.
- In both providers, after each **live** call (not decision-cache hits), call
  `tracker.record(...)` with `component="advisor"` and metadata `{strategy, symbol}`.
- Add `llmkit` to the `ai` / `deepseek` extras.
