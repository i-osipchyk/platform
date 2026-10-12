# Publishing the platform repo

Projects consume the platform **from GitHub, by tag**:

| What | Reference in a project |
|---|---|
| Terraform modules | `source = "git::https://github.com/i-osipchyk/platform.git//terraform/modules/lambda-app?ref=v0.1.0"` |
| Reusable workflows | `uses: i-osipchyk/platform/.github/workflows/python-ci.yml@v0.1.0` |
| llmkit | `llmkit = { git = "https://github.com/i-osipchyk/platform", subdirectory = "llmkit", tag = "v0.1.0" }` |
| Template | `uvx copier copy --trust gh:i-osipchyk/platform <dir>` (newest tag) |

So nothing works for other projects until the repo is on GitHub **and tagged**. Do this once.

## 1. Create the GitHub repository

Pick one of the two routes.

**Web UI**
1. Go to https://github.com/new.
2. Fill in:
   - Owner: `i-osipchyk`
   - Name: **`platform`** (every reference above uses this name)
   - Visibility: **Public** (see "Why public" below)
   - Leave *Add a README / .gitignore / license* **unticked**. The local repo already has history, and an initial commit on GitHub would conflict with it.
3. Create the repository.

**gh CLI** (`brew install gh && gh auth login` first)
```bash
gh repo create i-osipchyk/platform --public \
  --description "Shared infra, CI/CD, LLM cost tracking and project template for ~/dev"
```

### Why public
It contains no secrets: `bootstrap/terraform.tfvars` and `backend.tf` are gitignored, and the
account id only ever comes from variables. Being public means:
- Terraform `init` and the Docker build (`uv` installing llmkit) clone it without credentials.
- Any of your repos can call the reusable workflows with no extra settings.

If it has to be private, see `deployment.md` § "Platform repo visibility" for the token setup it
needs. You'll need that setup in **every** consuming project.

## 2. Push

```bash
cd ~/dev/platform
git remote add origin https://github.com/i-osipchyk/platform.git
git push -u origin main
```

Then open the repo's **Actions** tab. `platform-ci` runs `scripts/check.sh` (llmkit tests,
Terraform validate, template render). Wait for it to go green before tagging.

## 3. Tag the first release

1. In `CHANGELOG.md`, change `## v0.1.0 (unreleased)` to `## v0.1.0 (YYYY-MM-DD)`, then:
```bash
git commit -am "Release v0.1.0"
git tag -a v0.1.0 -m "v0.1.0"
git push origin main v0.1.0
```
2. Optionally create a GitHub Release from the tag (Releases → Draft a new release → choose
   `v0.1.0` → paste the changelog section). Projects pin the tag, not the Release, so this is only
   for reading.

## 4. Repository settings (once)

| Setting | Where | Value | Why |
|---|---|---|---|
| Branch protection on `main` | Settings → Branches → Add rule | Require status check `platform-ci / check` | broken modules or workflows reach every project when they bump |
| Tag protection | Settings → Rules → Rulesets → New tag ruleset, target `v*` | Restrict deletions and updates | a moved tag silently changes what every pinned project deploys |
| Actions permissions | Settings → Actions → General | Allow all actions (default) | the reusable workflows use `actions/*`, `aws-actions/*`, `docker/*`, `hashicorp/*`, `astral-sh/*` |
| Actions access (only if private) | Settings → Actions → General → Access | Accessible from repositories owned by the user | lets the other repos call the reusable workflows |

## 5. Check that a project can consume it

Run these from any scratch directory. Each one fetches from GitHub at the tag:

```bash
# llmkit installs from the tag
uv run --with "llmkit @ git+https://github.com/i-osipchyk/platform@v0.1.0#subdirectory=llmkit" \
  python -c "import llmkit; print(llmkit.PRICING_VERIFIED_ON)"

# the template renders from GitHub (uses the newest tag)
uvx copier copy --trust --defaults -d project_name=smoke-test gh:i-osipchyk/platform /tmp/smoke-test
cd /tmp/smoke-test && uv sync && uv run pytest && cd - && rm -rf /tmp/smoke-test

# Terraform can fetch the modules
mkdir -p /tmp/tf-smoke && cat > /tmp/tf-smoke/main.tf <<'EOF'
module "bucket" {
  source  = "git::https://github.com/i-osipchyk/platform.git//terraform/modules/data-bucket?ref=v0.1.0"
  project = "smoke"
  env     = "demo"
}
EOF
terraform -chdir=/tmp/tf-smoke init -backend=false && rm -rf /tmp/tf-smoke
```

The reusable workflows are checked the first time a project's CI runs. A wrong ref fails
immediately with "unable to find reusable workflow".

## 6. Point the existing projects at it

Follow `adoption.md`, one project at a time. The quickest first step for each is adding
`.github/workflows/ci.yml` that calls `python-ci.yml@v0.1.0`.

**ags already pins `llmkit` to `tag = "v0.1.0"`** in its `[tool.uv.sources]`. Until step 3 is done,
`uv lock` / `uv sync` there can't resolve it (fresh machines, CI, Docker builds). Once the tag
exists, run `uv lock` in ags and commit the lockfile.

## Releasing later versions

1. Make the change, update the docs it affects, and add a `CHANGELOG.md` entry.
2. Run `scripts/check.sh`.
3. Push and wait for `platform-ci` to pass.
4. Tag: `git tag -a vX.Y.Z -m vX.Y.Z && git push origin vX.Y.Z`.
5. Projects upgrade when **you** choose, by running `uvx copier update --trust` (template projects;
   bumps every pin), or by editing the three pins plus `uv lock`.

Versioning while on `0.x`:
- **patch** (`v0.1.1`): fixes and price updates
- **minor** (`v0.2.0`): anything that changes a module variable, a workflow input, the llmkit API
  or a template question. Write the migration in the changelog.

Never move or delete a published tag. Release a new one instead.
