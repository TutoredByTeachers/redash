# TutoredByTeachers Redash fork

This is a soft fork of [getredash/redash](https://github.com/getredash/redash). We track upstream
releases and carry a small, explicit set of patches on top. This file is the source of truth for
what we carry and why.

## Branch model

- `upstream` remote → `getredash/redash` (read-only). We pin to a **release tag**, not `master`.
- `origin` remote → `TutoredByTeachers/redash` (this fork).
- **Topic branches** (`feat/*`), each branched off upstream `master`, one per change. These are what
  we open PRs from — both upstream (to getredash) and internally (to this fork) for review.
- **Deploy branch** `tbt/release-<ver>` = the upstream release tag **+ our carried patches**, applied
  on top. This is the only branch we build and deploy from. Current: `tbt/release-26.3` (on `v26.3.0`).

Local development happens on `master`-based topic branches (latest code); the deploy branch is the
build artifact pinned to the release we run in production.

## Carried patches

| Patch | Topic branch | Fork PR | Upstream | Kind | Drop when |
|---|---|---|---|---|---|
| Slack (Bot) destination — Block Kit + `chat.postMessage` | `feat/slack-bot-destination` | [#1](https://github.com/TutoredByTeachers/redash/pull/1) | not yet proposed (validating first) | feature, upstream-bound | merged upstream |
| Worker two-stage cancel — SIGINT then force-kill | `feat/worker-two-stage-cancel` | [#2](https://github.com/TutoredByTeachers/redash/pull/2) | not yet proposed (validating first) | fix, upstream-bound | merged upstream |
| SSH tunnel key from `REDASH_SSH_TUNNEL_PRIVATE_KEY` env var | `feat/ssh-tunnel-key-from-env` | [#3](https://github.com/TutoredByTeachers/redash/pull/3) | fork-only (deploy convenience) | feature, fork-only | upstream adds an equivalent |

Legend: **upstream-bound** = we intend to contribute it; carry it only until it merges upstream, then
drop it on the next version bump. **fork-only** = we carry it indefinitely.

## Bumping to a new upstream release

1. Fetch tags: `git fetch upstream --tags`.
2. Create the new deploy branch off the new tag:
   `git checkout -b tbt/release-<new> v<new>`.
3. Replay the still-needed patches (drop any that landed upstream — check the table above):
   `git cherry-pick <slack> <worker> <ssh>` (or rebase the topic branches).
4. Resolve conflicts, run the patch tests, and use `git range-diff v<old>..tbt/release-<old> v<new>..tbt/release-<new>`
   to confirm the delta is intact.
5. Update this file (versions, statuses, drop any merged patches) and tag the image build.

## Building & deploying

The deployment image is built from this fork's source (so our patches and frontend assets
are baked in) with a Doppler layer on top. See [`build/`](build/) — run `build/build.sh
<dev|stage|prod> [tag]`. This supersedes the old `tbt-infra/redash/build` (which patched
the official image). The ECS services and base infrastructure remain Terraform-managed in
`tbt-infra/redash` (`deploy/` and `infra/`); they consume the image by tag — that tag is
the contract between the two repos.

`build/` is deploy-branch-only tooling; carry it forward on a version bump like the code patches.

## Patch tests

```
python -m pytest tests/destinations/test_slack_bot.py \
  tests/tasks/test_worker.py::TestHardLimitingWorkerCancellation \
  tests/test_dynamic_settings.py
```
