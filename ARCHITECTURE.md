# Architecture

DarkExec is a thin executive bridge from a natural request to one accountable target-owned Codex
task. An interactive executive conversation keeps that exact target for dependent follow-ups in the
same saved project; it never discovers or resumes continuity across executive tasks.

```text
interactive: installed workspace -> exact saved project -> verbatim target task
                                                    -> immediate initial harness
                                                    -> same-target follow-up burst
                                                    -> 30-minute trailing harness
background:  darkexec dispatch -> running Codex App control socket
                              -> App-listed executive and target tasks
                              -> same-target harness -> durable receipt/status
```

`darkexec dispatch` rejects unsaved targets and conflicting job reuse. It records executive, target,
harness, usage, and terminal state under `DARKEXEC_STATE_ROOT`. Interactive debounce state is
separate under `DARKEXEC_SESSION_ROOT` and is readable with `darkexec debounce-status`.

Codex App owns task history and interaction. Callers own detection, authorization, deduplication,
scheduling, and external messaging. DarkExec owns execution identity, App visibility, same-task
closeout, and receipt terminalization.

Interactive native routing uses a leading-and-trailing closeout policy. The first target result is
harnessed immediately. A clearly dependent follow-up in the same executive conversation and saved
project reuses that target and resets one 30-minute systemd timer through `darkexec debounce`.
Generation-keyed state under `/var/lib/darkexec/sessions` makes stale timers harmless. At expiry the
runtime rereads the target and sends at most one trailing harness after the latest product turn; a
manual harness, newer activity, an active turn, or an interrupted lineage makes it stop or defer
without duplicate work. Dependent cross-task work and consequential phase changes flush pending
closeout first. If the timer cannot be scheduled, closeout runs immediately.

`darkexec run` and `darkexec dispatch` remain one-shot/background paths and harness immediately.
Their target and harness turns wait without a runtime deadline by default; explicit positive
`DARKEXEC_TURN_TIMEOUT` values retain bounded-caller behavior, and signals still interrupt the
active native turn.

Each release contains `share/harness-ops.md` and `share/harness-ops.provenance.json`. The installed
workspace exposes that exact doctrine. Runtime behavior never depends on a mutable Harness Ops
checkout. The installer also binds `/srv/harness-ops.md` to that installed snapshot for target
harnesses that use the conventional host path, preserves a readable existing host doctrine binding,
and repairs a missing or broken binding.

## Trust boundaries

- the authenticated running Codex App and its control socket;
- exact saved-project roots;
- durable local receipt state;
- signal and timeout terminalization;
- release and doctrine identity; and
- the caller's authority and prompt.

This alpha does not provide a hosted service, scheduler, detector, notification transport, or
general sandbox.
