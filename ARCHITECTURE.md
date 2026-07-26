# Architecture

DarkExec is a thin executive bridge from one natural request to one accountable target-owned Codex
task.

```text
interactive: installed workspace -> exact saved project -> verbatim target task -> same-task harness
background:  darkexec dispatch -> running Codex App control socket
                              -> App-listed executive and target tasks
                              -> same-target harness -> durable receipt/status
```

`darkexec dispatch` rejects unsaved targets and conflicting job reuse. It records executive, target,
harness, usage, and terminal state under `DARKEXEC_STATE_ROOT`.

Codex App owns task history and interaction. Callers own detection, authorization, deduplication,
scheduling, and external messaging. DarkExec owns execution identity, App visibility, same-task
closeout, and receipt terminalization.

Each release contains `share/harness-ops.md` and `share/harness-ops.provenance.json`. The installed
workspace exposes that exact doctrine. Runtime behavior never depends on a mutable Harness Ops
checkout.

## Trust boundaries

- the authenticated running Codex App and its control socket;
- exact saved-project roots;
- durable local receipt state;
- signal and timeout terminalization;
- release and doctrine identity; and
- the caller's authority and prompt.

This alpha does not provide a hosted service, scheduler, detector, notification transport, or
general sandbox.
