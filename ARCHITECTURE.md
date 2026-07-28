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

Interactive routing uses runtime-owned `run` and `continue` commands so each executive, runner,
target task, and active turn is durably bound before waiting. The first target result is harnessed
immediately. For delegated user turns, the runtime rereads the one active executive turn and clones
its structured text, images, and local-image references into the target; stdin is only the routing
assertion, so an executive cannot silently drop an attachment while reconstructing a prompt. A
queued dependent message remains in the executive task until that immediate harness finishes, then
reuses the established target and resets one 30-minute systemd timer through `darkexec debounce`.
Generation-keyed state under `/var/lib/darkexec/sessions` makes stale timers harmless. At expiry the
runtime rereads the target and sends at most one trailing harness after the latest product turn; a
manual harness, newer activity, an active turn, or an interrupted lineage makes it stop or defer
without duplicate work. Dependent cross-task work and consequential phase changes flush pending
closeout first. If the timer cannot be scheduled, closeout runs immediately.

The outer Codex caller keeps one orchestration tool call attached to the synchronous process.
Deterministic progress reads use `darkexec execution-status` against the exact executive thread and
may be projected through non-model UI notifications without settling that call. Ordinary waiting
does not re-enter the executive model. This preserves the active executive turn and queued
follow-ups while avoiding repeated full-context polling turns.

`darkexec run` and `darkexec dispatch` remain one-shot/background paths and harness immediately.
Their target and harness turns wait without a runtime deadline by default; explicit positive
`DARKEXEC_TURN_TIMEOUT` values retain bounded-caller behavior, and signals still interrupt the
active native turn. Installation verifies that effective unset default from the release artifact
before switching the current symlink, so a bounded-default regression cannot reach a fresh install
or update.

Executive lifecycle state is stored with private permissions under `DARKEXEC_EXECUTION_ROOT`, keyed
by the exact Codex executive thread ID. `STOP` signals only the recorded runner and relies on its
native `turn/interrupt` acknowledgement. `STOP HARD` first interrupts the recorded target turn
through App Server, then may force-kill only a runner whose PID and Linux process-start identity
still match. Stop-time control uses recorded task identity rather than saved-project or visibility
rediscovery, so a previously accepted target remains stoppable when unloaded or inaccessible.
Absent, terminal, stale, and partial states fail narrowly and never select another task.

Each release contains `share/harness-ops.md` and `share/harness-ops.provenance.json`. The installed
workspace exposes that exact doctrine. Runtime behavior never depends on a mutable Harness Ops
checkout. The installer also binds `/srv/harness-ops.md` to that installed snapshot for target
harnesses that use the conventional host path, preserves a readable existing host doctrine binding,
and repairs a missing or broken binding.

## Trust boundaries

- the authenticated running Codex App and its control socket;
- exact saved-project roots;
- durable local receipt state;
- executive-scoped active-run identity and verified process ownership;
- signal and timeout terminalization;
- release and doctrine identity; and
- the caller's authority and prompt.

This alpha does not provide a hosted service, scheduler, detector, notification transport, or
general sandbox.
