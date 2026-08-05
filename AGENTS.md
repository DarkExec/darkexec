# DarkExec Development

Read `share/harness-ops.md` before working here.

- The source repository owns the runtime; the installed workspace is a release binding.
- Codex App owns task creation, visibility, continuation, and history. Every harness pass stays in
  its source task and receives exactly the configured prompt.
- Creation is single-shot; resolve identity from the response and exact App task-list state.
- Keep caller detection, business policy, notifications, and target secrets outside DarkExec.
- Run `./scripts/validate.sh`.
