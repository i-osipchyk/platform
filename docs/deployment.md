# Deployment: Terraform + GitHub Actions on AWS

```
push ──► CI (python-ci.yml) ──success on main──► Deploy
                                                  ├─ demo: build arm64 image → ECR <slug>:<sha12>
                                                  │        terraform plan/apply (state <slug>/demo.tfstate)
                                                  └─ live: waits for approval → reuses the same image
                                                           terraform plan/apply (state <slug>/live.tfstate)
```

Three layers:

| Layer | What | Applied by | How often |
|---|---|---|---|
| `bootstrap/` | state bucket, GitHub OIDC, `gh-deploy-<slug>` roles, ECR repos, `platform-alarms` topic + Telegram notifier, budgets, cost tags | you, from your laptop | once, then when a project is added |
| `terraform/modules/*` | `lambda-app`, `data-bucket` | (library) | released as platform tags |
| `<project>/infra/` | module calls + project-specific resources | GitHub Actions | every push to main |

## 1. Bootstrap the account (once)

Needs admin credentials locally (`aws sts get-caller-identity` works).

```bash
cd ~/dev/platform/bootstrap
cp terraform.tfvars.example terraform.tfvars      # list projects, set alert_email

# Telegram notifier secrets (the chat that should receive alarms)
aws ssm put-parameter --type SecureString --name /platform/notifier/telegram-bot-token --value '<token>'
aws ssm put-parameter --type SecureString --name /platform/notifier/telegram-chat-id   --value '<chat id>'

# Optional: Langfuse keys shared by every project's Lambdas
aws ssm put-parameter --type SecureString --name /platform/shared/langfuse-public-key --value 'pk-lf-...'
aws ssm put-parameter --type SecureString --name /platform/shared/langfuse-secret-key --value 'sk-lf-...'

terraform init
terraform apply                                   # local state, this once

# Move bootstrap's own state into the bucket it just created
cp backend.tf.example backend.tf                  # fill in the account id
terraform init -migrate-state
```

If the apply fails on `aws_ce_cost_allocation_tag`, AWS hasn't seen the tag yet. Set
`activate_cost_allocation_tags = false`, apply, and turn it back on a day after the first tagged
resource exists. Cost Explorer only shows tag data from the activation date onwards.

### Adding a project later
Add it to `projects` in `terraform.tfvars` and `terraform apply`. That creates its ECR repo,
deploy role and budget.

## 2. Wire a repo (once per project)

1. **GitHub → Settings → Environments.** Create `demo` and `live` (or just `live`). On `live`,
   tick *Required reviewers* and add yourself. Optionally restrict deployment branches to `main`.
2. **GitHub → Settings → Secrets and variables → Actions → Variables.** Set `AWS_ACCOUNT_ID`.
3. **Secrets:** `uv run python scripts/put_secrets.py --env demo`, and again for live.
4. Projects from the template already have `.github/workflows/{ci,deploy}.yml` and `infra/`.
   For existing projects, see `adoption.md`.
5. Push to main. Watch Actions: CI, then Deploy demo, then the live approval.

Functions start with their schedules disabled (`schedules_enabled = false` in `env/*.tfvars`).
Invoke once by hand (`aws lambda invoke --function-name <slug>-demo-<fn> out.json`), check the
logs, then flip the flag in a PR.

## Day to day

| Task | How |
|---|---|
| Deploy | merge to `main` |
| Deploy one env by hand | Actions → Deploy → Run workflow → pick env |
| See what would change | the plan is in the job summary of every Deploy run; locally `terraform plan` (README of the project) |
| Roll back | re-run the Deploy run of the last good commit (its image is still in ECR, so there's no rebuild) |
| Rotate a secret | update `.env`, run `put_secrets.py --env <env>`; the next cold start picks it up (or redeploy to force one) |
| Pause a project | set `schedules_enabled = false` and merge |
| A deploy hits AccessDenied | the service isn't in the deploy role yet: add it, scoped to `<slug>-*`, in `bootstrap/github_oidc.tf` and apply bootstrap |

## Reusable workflow inputs (`lambda-deploy.yml`)

| Input | Default | |
|---|---|---|
| `project`, `environment`, `aws-account-id` | required | |
| `aws-region` | `eu-west-1` | |
| `tf-dir` | `infra` | |
| `tf-vars` | | extra `-var` / `-var-file` flags |
| `runner` | `ubuntu-24.04-arm` | native arm64 build. Any other runner builds under QEMU, which is slower |
| `pre-apply` | | shell run between plan and apply (e.g. ags: wait for running Step Functions executions) |
| `plan-only` | `false` | |

Terraform always receives `-var-file=env/<environment>.tfvars -var=region=... -var=image_uri=<repo@digest>`,
so every project's `infra/variables.tf` declares `env`, `region` and `image_uri`.

## Platform repo visibility

Projects consume the platform from GitHub in three ways: Terraform module sources, reusable
workflows, and llmkit as a git dependency. **Keep this repo public.** It holds no secrets (tfvars and
backend.tf are gitignored), and being public means:
- Terraform and Docker builds clone it without credentials
- any repo can call the reusable workflows

If it must be private, you need three things:
- Settings → Actions → *Access: accessible from repositories owned by the user* (for the workflows)
- a read-only token stored as a secret in each project, set up with
  `git config --global url."https://x-access-token:${TOKEN}@github.com/".insteadOf "https://github.com/"`
  in the deploy job (for Terraform)
- that token passed into `docker build` as a build secret (for llmkit)
