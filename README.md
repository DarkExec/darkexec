# DarkExec

DarkExec is an accountable executive for native Codex software work.

Give it a natural request. DarkExec resolves one exact saved project, creates visible native Codex
App tasks, preserves the request as the target's first turn, completes a Harness Ops closeout in the
same target task, and writes a durable receipt.

> Natural intent in. Durable software outcomes out.

## Alpha status

This is an early self-hosted Linux alpha for experienced Codex App operators. It currently assumes:

- Python 3.11 or newer;
- Git;
- Codex CLI and Codex App configured for the same Linux host;
- a running Codex App control socket;
- exact target directories saved in Codex configuration; and
- permission to use the default `/opt/darkexec`, `/srv/darkexec`, `/var/lib/darkexec`, and
  `/usr/local/bin` paths, or equivalent environment overrides.

Codex App integration is an evolving boundary. Treat upgrades as requiring requalification.
Non-root test installations can set `DARKEXEC_INSTALL_ROOT`, `DARKEXEC_WORKSPACE`, and
`DARKEXEC_BIN_PATH`.

## Validate and install

```bash
git clone https://github.com/DarkExec/darkexec.git
cd darkexec
./scripts/validate.sh
sudo ./scripts/install.sh
```

Add `/srv/darkexec` as a saved Codex App project. Open it as a normal task and state the outcome you
want.

For a background caller:

```bash
printf '%s' 'Inspect the target and report its current test status. Make no changes.' |
  sudo darkexec dispatch \
    --target /absolute/saved/project \
    --job-id example-read-only-1 \
    --prompt-stdin \
    --read-only-harness \
    --json

sudo darkexec status --job-id example-read-only-1 --json
```

The same job ID is idempotent only for the same target and exact request. Conflicting reuse fails
closed.

## What remains inspectable

Receipts record:

- executive and target task IDs;
- exact App-listed roots and visibility;
- target and same-task harness results;
- separate model usage where supplied by Codex App;
- terminal status, error, and timestamps.

State defaults to `/var/lib/darkexec/jobs` with private file permissions.

## Harness Ops

Every release vendors an exact snapshot from
[`DarkExec/harness-ops`](https://github.com/DarkExec/harness-ops). See
`share/harness-ops.provenance.json` for its upstream revision and SHA-256.

## Recovery boundaries

Installation is commit-addressed and switches `/opt/darkexec/current` atomically. Reinstall a known
checkout to roll back. The alpha does not yet provide an automated rollback or uninstall command;
remove only the documented DarkExec-managed symlinks and release paths after preserving any receipts
you need.

## Repository map

- `bin/darkexec` — dispatch and status CLI
- `share/workspace/` — installed Codex App project
- `share/harness-ops.md` — pinned operating doctrine
- `scripts/install.sh` — commit-addressed installer
- `scripts/test_cli.py` — deterministic fake-App contract tests
- `scripts/validate.sh` — release and doctrine validation

## Licence

Apache-2.0. See `LICENSE`.
