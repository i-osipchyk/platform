# Starting a new project

```bash
cd ~/dev
uvx copier copy --trust ~/dev/platform <project-name>
# or, once platform is on GitHub (uses the latest tag):
uvx copier copy --trust gh:i-osipchyk/platform <project-name>
```

Copier asks:

| Question | Notes |
|---|---|
| `project_name` | repo/folder name, kebab-case |
| `slug` | 2–16 chars `[a-z0-9]`, used in AWS names, tags, SSM, llmkit. Keep it short: `gtb`, not `goldtokyobreakout` |
| `package` | Python package (defaults to the slug) |
| `uses_llm` | adds llmkit, `llm.py`, and the DeepSeek key to `.env.example` and `put_secrets.py` |
| `deploy_lambda` | adds `Dockerfile`, `handlers.py`, `infra/`, `deploy.yml`, `scripts/put_secrets.py` |
| `envs` | `demo,live` or `live` only |
| `platform_version` | the platform tag to pin to |

`--trust` lets the template run its two tasks: `git init` and `uv lock`.

## What you get

```
<project>/
├── .copier-answers.yml        # your answers; `copier update` uses them. Don't edit
├── .github/workflows/
│   ├── ci.yml                 # → platform python-ci.yml (ruff, mypy, pytest)
│   └── deploy.yml             # (lambda) → platform lambda-deploy.yml, demo → live
├── CLAUDE.md                  # project rules; points back at ~/dev/CLAUDE.md
├── Dockerfile                 # (lambda) multi-stage, arm64, from uv.lock
├── infra/                     # (lambda) data-bucket + lambda-app modules, env/*.tfvars
├── scripts/put_secrets.py     # (lambda) .env → SSM /<slug>/<env>/*
├── src/<package>/
│   ├── config.py              # load_env(): .env locally, SSM on Lambda
│   ├── handlers.py            # (lambda) entry points
│   └── llm.py                 # (llm) the only LLM seam
└── tests/
```

## After generating

1. `cd <project> && cp .env.example .env` and fill it in.
2. `uv sync && uv run pytest`.
3. Create the GitHub repo and push.
4. If it deploys, follow `deployment.md` § "Wire a repo" (add it to bootstrap, set up GitHub
   Environments, the `AWS_ACCOUNT_ID` variable, and secrets).
5. Fill in the "Project-specific rules" section of `CLAUDE.md`.

## Keeping up with the template

```bash
cd ~/dev/<project> && uvx copier update --trust
```
This re-applies the template at its newest tag using your saved answers, and merges with your
changes (conflicts show as normal git conflicts). It bumps the `platform_version` pins too.

## When the template doesn't fit

The template is a starting point, not a cage. If a new project needs something generic (a new
module such as an API Gateway app, a sink, a workflow input), add it to platform and release a tag,
rather than building it once inside the project. Things that only that project needs stay in the
project.
