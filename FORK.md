# TutoredByTeachers Redash fork

Soft fork of [getredash/redash](https://github.com/getredash/redash). `master` is an
upstream base plus our merged fork patches; the `tbt/release-*` **deploy branch** is
`master` + the `build/` tooling and is what we build the deployment image from.

## Remotes

- `upstream` → `getredash/redash` (fetch-only; the base we pin to).
- `origin` → `TutoredByTeachers/redash` (our fork; we push and deploy from here).

## Current state (2026-07)

- **Base:** upstream `26.07.0-dev` snapshot (commit `26ca18e6`).
- **Merged fork patches on `master`:**
  | Patch | PR | What | Upstream |
  |---|---|---|---|
  | `allow_html` | #4 | Opt-in "Allow HTML content" for custom alert templates. Fixes Slack alert `<@mentions>`/`<links>` pulled from query data — the real cause was HTML-escaping applied in a destination-agnostic render path. Safe-by-default. | open PR (validating) |
  | SSH tunnel key from env | #3 | `ssh_tunnel_auth()` loads the key from `REDASH_SSH_TUNNEL_PRIVATE_KEY` (optional `…_PASSWORD`); auto-detects Ed25519/ECDSA/RSA; unchanged when unset. **Replaces** the old `tbt-infra/redash/build/dynamic_settings.py` monkey patch. | fork-only (not proposed) |
- **Dropped:** worker two-stage cancel (finite query timeouts cover the risk; parked on branch `feat/worker-two-stage-cancel` for revival if stuck-job alerts fire); `slack_bot` destination (superseded by `allow_html`).
- **Deploy branch:** `tbt/release-26.7` = `master` + `build/`.

## Build & deploy

`build/build.sh <dev|stage|prod> [tag]` builds the image from fork source (the repo's root
Dockerfile — patches + frontend assets baked in) with a Doppler layer on top, then pushes to
the `redash-prod` ECR repo. The Terraform in `tbt-infra/redash` (`deploy/` + `infra/`) consumes
the image **by tag** (`image_tag` tfvar) — the tag is the contract between the two repos.

Set `REDASH_SSH_TUNNEL_PRIVATE_KEY` in Doppler (already present in `prd`/`dev`). The fork image
uses the fork's in-tree `dynamic_settings.py`, so no `dynamic_settings.py` `COPY` override is
needed anymore.

## Bumping to a new upstream release

Move `master`'s base to the newer upstream tag, replay/verify the merged patches (`git range-diff`,
run the patch tests), re-cut `tbt/release-<ver>` off master + `build/`, then rebuild and re-tag
the image. Drop any patch upstream has since absorbed.
