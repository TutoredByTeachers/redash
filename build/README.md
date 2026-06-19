# TBT deployment image build

This directory builds the TutoredByTeachers Redash image we deploy to ECS. It supersedes
`tbt-infra/redash/build` — builds are now managed from this fork so the image and the
source it runs are versioned by the same commit. See [`../FORK.md`](../FORK.md) for the
overall fork/deploy-branch model.

## How it works

`build/build.sh <dev|stage|prod> [tag]` does two builds:

1. **Base image from source** — runs the repo's root `Dockerfile` (frontend + backend),
   so our carried patches *and* frontend assets (e.g. the Slack-bot picker icon) are
   baked in. Tagged `tbt-redash-base:<tag>`.
2. **Doppler layer** — `build/Dockerfile` adds the Doppler CLI and wraps the entrypoint
   in `doppler run`, so secrets (incl. `REDASH_SSH_TUNNEL_PRIVATE_KEY`) are injected at
   start. Tagged `redash-<env>:<tag>` and pushed to ECR.

Then it logs into ECR and pushes `…/redash-<env>:<tag>`.

## Why build from source (not patch the official image)

The old approach was `FROM redash/redash:<tag>` + `sed`/`COPY` patches. Now that the
worker-cancellation and SSH-key changes live in the source tree (and the Slack bot is a
new source file + frontend asset), patching the official image would either miss them or,
for the icon, produce a broken image — webpack bundles the asset, so it must be present
at frontend-build time. Building from source is the correct, complete approach.

Trade-off: a source build compiles the frontend and installs Python deps, so it's slower
than the old overlay (minutes, not seconds). That cost belongs in CI, not on every change.

## Knobs

- **Tag:** defaults to the Redash version on this branch (e.g. `26.3.0`). Pass an explicit
  tag like `26.3.0-2` to re-cut the same upstream version: `build/build.sh prod 26.3.0-2`.
- **Platform:** `linux/amd64` (ECS Fargate). On Apple Silicon this builds under emulation.
- **Slimmer prod image (optional):** the base build uses the root Dockerfile's default
  `install_groups=main,all_ds,dev`. To drop dev deps, pass
  `--build-arg install_groups=main,all_ds` through to the base build.

## Carrying this forward on a version bump

`build/` is deploy-branch-only tooling (never PR'd upstream). When you cut a new
`tbt/release-<ver>` off a new tag, replay this directory along with the code patches
(see [`../FORK.md`](../FORK.md)).
