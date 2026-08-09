# DarkExec Runtime

This repository owns accountable native Codex task execution and the installed executive workspace contract. Start with one request route, inspect its implementation and tests, and retrieve deeper context only for a named unresolved decision.

## Request routing

- For dispatch, continuation, steering, debounce, stop control, prompt settings, receipts, or App Server transport, start in `bin/darkexec` and the closest fixtures in `scripts/test_cli.py`.
- For installed executive behavior and operator-facing execution instructions, start in `share/workspace/AGENTS.md`, `share/workspace/README.md`, and their exact assertions in `scripts/validate.sh`.
- For harness episode schemas, privacy boundaries, or evaluator transport, start in `schemas/` and `scripts/test_harness_episode_v2_contract.py`.
- For installation, upgrades, installed identity, or release bindings, start in `scripts/install.sh`, `scripts/verify_install_contract.py`, and the install fixtures in `scripts/test_cli.py`.

Start with one route. Add another only for a distinct unresolved decision.

## Working loop

1. Inspect the routed implementation, closest tests, and current repository state before broader documentation or history.
2. Name what local evidence leaves unresolved, then choose one context route below; continue without more documentation when no decision remains open.
3. Make the smallest runtime-owned change, run the cheapest focused falsifier, then advance to the full contract suite and installed identity proof required by the claim.
4. For an authorized change, finish commit, publication, merge, canonical synchronization, installation, identity readback, cleanup, and concise handoff unless the user limits delivery or a real external gate blocks it.

## Context routing

- For stable lifecycle meaning, component ownership, or trust boundaries, read the relevant section of [Architecture](ARCHITECTURE.md).
- For credentials, private state, prompt storage, attachment handling, or filesystem authority, read the relevant section of [Security](SECURITY.md).
- For operator behavior, supported invocation, installation, or recovery expectations, read the relevant section of [README](README.md).
- For harness-prompt or episode semantics that local code leaves unresolved, read the relevant section of the released [Harness Ops snapshot](share/harness-ops.md); it is a release artifact, not a substitute for current target evidence.

Do not preload all four documents. Follow another route only when its evidence can change the current decision.

## Boundaries

- The source repository owns the runtime; the installed workspace is a release binding.
- Codex App owns task creation, visibility, continuation, and history. Every harness pass stays in its source task and receives exactly the configured prompt.
- Creation is single-shot; resolve identity from the response and exact App task-list state.
- Keep caller detection, business policy, notifications, and target secrets outside DarkExec.
- Keep Markdown prose and list items on one physical line; never hard-wrap.

## Validation

Run `./scripts/validate.sh`. After merge, run `./scripts/install.sh` and read back `darkexec identity --json` before claiming the runtime is installed.
