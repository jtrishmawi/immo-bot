---
name: release
description: Tag and release a new version of immo-bot. Use when asked to cut a release, tag a version, or publish a new version.
---

# Release a New Version

immo-bot uses simple `vX.Y.Z` git tags. GitHub Actions builds and pushes Docker images on every push to `main`; a tag creates a named release on top of that.

## Versioning convention

`MAJOR.MINOR.PATCH`
- **PATCH** — bug fixes, config tweaks (e.g. `1.0.1`)
- **MINOR** — new scraper source, new notification platform, new bot command (e.g. `1.1.0`)
- **MAJOR** — breaking change to `.env` format, complete rewrite of a module (e.g. `2.0.0`)

## Steps

### 1 — Confirm main is clean and tests pass

```bash
git status          # must be clean
git log --oneline -5
.venv/Scripts/pytest tests/ -v
```

### 2 — Choose the next version

Look at the last tag:
```bash
git tag --sort=-version:refname | head -5
```

### 3 — Create and push the tag

```bash
git tag vX.Y.Z
git push origin vX.Y.Z
```

This does **not** trigger a new Docker build (CI only builds on `main` branch pushes).
The tag serves as a named checkpoint in git history.

### 4 — Create a GitHub release (optional)

```bash
gh release create vX.Y.Z \
  --title "vX.Y.Z" \
  --notes "$(git log $(git describe --tags --abbrev=0 HEAD^)..HEAD --pretty=format:'- %s')"
```

This auto-generates release notes from commit messages since the previous tag.

### 5 — Update running containers

If deploying the release to production:
```bash
docker compose pull
docker compose up -d
```

## What gets released

The Docker images on GHCR are always `:latest` from the most recent `main` push.
Tags are git-only checkpoints; they don't produce separate Docker image tags unless
`.github/workflows/docker.yml` is updated to also push `tags: ghcr.io/...:vX.Y.Z`.
