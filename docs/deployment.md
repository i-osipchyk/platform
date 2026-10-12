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

Needs admin credentials locally (`aws sts get-caller-identity` works) and the region exported
(conventions.md § Region):

```bash
# One variable per line: in `export A=x B=$A`, $A is expanded before A is set.
export AWS_REGION="${AWS_REGION:-eu-west-1}"
export TF_VAR_region="$AWS_REGION"
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
cp backend.tf.example backend.tf
terraform init -migrate-state \
  -backend-config="bucket=tfstate-$(aws sts get-caller-identity --query Account --output text)-$AWS_REGION" \
  -backend-config="region=$AWS_REGION"
```

**Lambda concurrency quota.** New accounts start at 10 concurrent executions, which is too few
for a project that fans out (ags runs 7 pipelines at once) and makes `reserved_concurrency`
impossible. Check it, and request a raise once per account and region (free; usually approved
within a day):
```bash
aws service-quotas get-service-quota --service-code lambda --quota-code L-B99A9384 --query Quota.Value
aws service-quotas request-service-quota-increase --service-code lambda --quota-code L-B99A9384 --desired-value 1000
```

**Resources that already exist** in the account make the first apply fail with
`EntityAlreadyExists` / `RepositoryAlreadyExistsException`. See Troubleshooting below.

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
   Set `AWS_REGION` only if the project isn't in eu-west-1.
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

## Troubleshooting

Problems hit for real while setting up an account and the first project. Add new ones here.

| Symptom | Cause | Fix |
|---|---|---|
| bootstrap: `InvalidBucketName` for `tfstate-<account>-` | `TF_VAR_region` exported but empty (often `export AWS_REGION=… TF_VAR_region="$AWS_REGION"` on one line) | export the two on separate lines (§ 1). The `region` variable now rejects an empty value at plan time |
| bootstrap: `EntityAlreadyExists` for the GitHub OIDC provider | one per account, created earlier by hand or by another stack | if no other Terraform state manages it: `terraform import aws_iam_openid_connect_provider.github arn:aws:iam::<account>:oidc-provider/token.actions.githubusercontent.com` |
| bootstrap: `RepositoryAlreadyExistsException` for `<slug>` | the project's own (pre-platform) Terraform created that ECR repo | leave the project out of `projects` until it migrates (bootstrap would make the repo IMMUTABLE and add expiry, which breaks a mutable-tag deploy). On migration: `terraform import 'aws_ecr_repository.project["<slug>"]' <slug>` here, and `removed { from = aws_ecr_repository.<name>; lifecycle { destroy = false } }` in the project |
| deploy: `Not authorized to perform sts:AssumeRoleWithWebIdentity` | the token's `sub` doesn't match the role's trust. Read the real one from CloudTrail: `aws cloudtrail lookup-events --lookup-attributes AttributeKey=EventName,AttributeValue=AssumeRoleWithWebIdentity --max-results 3` (`userIdentity.userName`) | GitHub may send `repo:<owner>@<id>/<repo>@<id>:environment:<env>`; the trust accepts both forms since the change after v0.1.1. Otherwise check the repo name in `projects` and that the GitHub Environment matches `envs` |
| deploy: `ReservedConcurrentExecutions … below its minimum value of [10]` | account Lambda concurrency quota still 10 | request the raise (§ 1), or drop `reserved_concurrency` until it's approved |
| deploy: `not authorized to perform: states:ValidateStateMachineDefinition on resource: …:stateMachine:*` | the AWS provider validates a definition before creating a state machine, and IAM checks that call against `stateMachine:*`, outside the deploy role's `<slug>-*` scope | fixed in bootstrap (`Describe` statement); re-apply bootstrap from a checkout that has it |
| deploy: `AccessDenied` on any other unscoped call | the deploy role grants only `<slug>-*` resources plus a short list of read-only actions | read the action and resource from the error, add it to `bootstrap/github_oidc.tf` (to `Describe` if the action can't be resource-scoped, else scoped to `<slug>-*`), PR, apply bootstrap |

## Reusable workflow inputs (`lambda-deploy.yml`)

| Input | Default | |
|---|---|---|
| `project`, `environment`, `aws-account-id` | required | |
| `aws-region` | empty → `eu-west-1` | callers pass `${{ vars.AWS_REGION \|\| 'eu-west-1' }}` |
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
