# lambda-app

Scheduled container Lambdas for one project/env: one image, one function per handler.

Creates: a function role (logs, SSM `/<project>/<env>/*` + `/platform/shared/*` read, optional
bucket access), log groups, functions (arm64, image pinned by digest), async retry config,
EventBridge Scheduler entries, and an Errors alarm per function that publishes to the shared
`platform-alarms` topic.

It does **not** create the ECR repository (bootstrap does: one per project, shared by envs) or
buckets (use `data-bucket`).

No region variable: the module uses the provider's region (the root sets it from `AWS_REGION`,
default eu-west-1). Lambda sets `AWS_REGION` inside the functions itself.

Every function gets these env vars: `PROJECT`, `ENV`, `FUNCTION`, `LLMKIT_PROJECT`, `LLMKIT_ENV`,
`SECRETS_PATH=/<project>/<env>`, `SHARED_SECRETS_PATH=/platform/shared`.

```hcl
module "app" {
  source    = "git::https://github.com/i-osipchyk/platform.git//terraform/modules/lambda-app?ref=v0.1.0"
  project   = "gtb"
  env       = var.env
  image_uri = var.image_uri

  functions = {
    morning  = { handler = "gtb.runs.app.morning_handler", timeout = 360, retries = 2 }
    watchdog = { handler = "gtb.runs.app.watchdog_handler", timeout = 360 }
  }
  schedules = {
    morning   = { function = "morning", expression = "cron(0 9 ? * MON-FRI *)" }
    watchdog1 = { function = "watchdog", expression = "cron(51 20 ? * MON-FRI *)" }
    watchdog2 = { function = "watchdog", expression = "cron(52 20 ? * MON-FRI *)" }
  }
  schedules_enabled = var.schedules_enabled
  write_bucket_arns = [module.records.arn]
  ssm_write         = true # token refresh
}
```

Project-specific extras (Step Functions, SQS, ...) go next to the module call in the project's
own `infra/`, named `<project>-<env>-*` so the deploy role can manage them. Grant the functions
access through `extra_policy_json`.
