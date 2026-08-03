# DarkExec Development

Read `share/harness-ops.md` before working here.

- The source repository owns the runtime; the installed workspace is a release binding.
- Codex App owns task creation, visibility, continuation, and history. Immediate and manual harnesses
  stay in the source task; automatic trailing closeout uses one identity-bound bounded capsule in a
  fresh target-owned task.
- Creation is single-shot; resolve identity from the response and exact App task-list state.
- Keep caller detection, business policy, notifications, and target secrets outside DarkExec.
- Run `./scripts/validate.sh`.
