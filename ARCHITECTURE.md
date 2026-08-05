# Architecture

DarkExec is a thin executive bridge from a natural request to one accountable target-owned Codex
task. An interactive executive conversation keeps that exact target for dependent follow-ups in the
same saved project; it never discovers or resumes continuity across executive tasks.

```text
interactive: installed workspace -> exact saved project -> verbatim target task
                                                    -> attached same-turn steering
                                                    -> immediate initial harness
                                                    -> same-target follow-up burst
                                                    -> same-session trailing harness
background:  darkexec dispatch -> running Codex App control socket
                              -> App-listed executive and target tasks
                              -> same-target harness -> durable receipt/status
```

`darkexec dispatch` rejects unsaved targets and conflicting job reuse. It records executive, target,
harness, usage, and terminal state under `DARKEXEC_STATE_ROOT`. Interactive debounce state is
separate under `DARKEXEC_SESSION_ROOT` and is readable with `darkexec debounce-status`.
The standard harness prompt is runtime-owned and defaults to the doctrine's normal wind-up request.
`darkexec harness-prompt` reads it, while authenticated control planes may atomically set or reset
the mode-0600 override under `/var/lib/darkexec/config`. Immediate passes read the current value;
each trailing closeout captures it when that closeout generation is scheduled. The read-only fire
drill prompt is fixed separately and cannot be weakened through this setting.

Every terminal harness lifecycle also publishes one immutable private episode under
`DARKEXEC_HARNESS_EPISODE_ROOT`. Immediate, deferred, exact manual, read-only, failed, interrupted,
cancelled/superseded, and pre-harness-negative outcomes retain exact target thread, product turn,
harness turn, generation, mode, runtime, doctrine, configuration, usage, and terminal identity.
Each receipt also carries a closed `episodePurpose`: standard work defaults to `ordinary`, read-only
work defaults to `control`, and trusted owners may explicitly mark `fire_drill` or `gym_meta`.
This append-only journal is a one-way observability output: write failure is attached to the normal
receipt or command result and never changes target completion, retry, notification, or verification.

`schemas/darkexec-harness-episode.v2.schema.json` is an inactive candidate contract for later
Harness Efficiency Cycle qualification. It separates target and harness phase clocks, model calls,
cached and uncached usage, operator interaction counts, and configuration/runtime/doctrine/target/
exposure identities. Every unavailable measurement is explicit `null`; raw prompts, results, and
trajectories are outside the closed schema. Runtime emission remains v1 until the existing
intervention is terminal and Harness Gym publishes an executable profile/schema with an immutable
profile digest. DarkExec will transport those facts but will not interpret metrics or decide
comparability, concentration, intervention selection, or qualification.

Codex App owns task history and interaction. Callers own detection, authorization, deduplication,
scheduling, and external messaging. DarkExec owns execution identity, App visibility, same-task
closeout, and receipt terminalization.

A trusted control plane may inspect an exact saved target task with `darkexec target-status`. The
bounded result reports only whether a newer user turn exists plus its identity, lifecycle status,
and product-or-harness classification. It never returns prompt or result content. This lets a
control plane reconcile native Codex App follow-ups without reconstructing the transcript or
opening raw App Server access.
When that control plane explicitly requests `--include-input` or `--include-result`, the same
exact-target read may also return up to 100 newer user turns for its private assignment journal.
User input is bounded to 20,000 characters and terminal agent results to 12,000 characters. It
fails rather than silently omitting a larger gap. The default remains content-free, and no broader
transcript is returned.

An authenticated multi-host control plane may ask one local DarkExec executive to choose among a
bounded candidate file of `(hostId, targetPath)` pairs with `resolve-global`. The receipt is keyed
to the request and candidate digest, and the result must exactly match a supplied pair. DarkExec
does not connect to the selected host; the control plane retains transport authority and performs
the subsequent exact-host dispatch.

An authenticated multi-host control plane may ask one local DarkExec executive to choose among a
bounded candidate file of `(hostId, targetPath)` pairs with `resolve-global`. The receipt is keyed
to the request and candidate digest, and the result must exactly match a supplied pair. DarkExec
does not connect to the selected host; the control plane retains transport authority and performs
the subsequent exact-host dispatch.

Interactive routing uses runtime-owned `run` and `continue` commands so each executive, runner,
target task, and active turn is durably bound before waiting. The first target result is harnessed
immediately. For delegated user turns, the runtime rereads the one active executive turn and clones
its structured text, images, and local-image references into the target; stdin is only the routing
assertion, so an executive cannot silently drop an attachment while reconstructing a prompt. A
queued dependent message remains in the executive task until that immediate harness finishes, then
reuses the established target and resets one 30-minute systemd timer through `darkexec debounce`.
Generation-keyed state under `/var/lib/darkexec/sessions` makes stale timers harmless. At expiry the
runtime resumes the exact persisted target by both its validated task ID and App-listed rollout
path, then sends the captured configured prompt as at most one trailing harness turn in that same
task. The harness therefore receives the source session's actual context without a copied,
truncated, or separately injected representation. A
manual harness, newer activity, an active turn, or an interrupted lineage makes it stop or defer
without duplicate work. Dependent cross-task work and consequential phase changes flush pending
closeout first. If the timer cannot be scheduled, closeout runs immediately.

Privacy-safe native rollout telemetry may still populate receipts, but it is never injected into the
harness prompt. App Server per-call `last` usage keeps resumed thread history from being mislabeled
as harness usage. Within granted authority, a harness intervention remains nonterminal through local edits, branches,
commits, pushed branches, and draft pull requests; normal review, merge, applicable installation,
identity readback, and cleanup remain part of the same closeout.

`dispatch` retains immediate first harnessing by default. A trusted conversational control plane
may opt into `--defer-initial-harness`; the terminal dispatch receipt marks the first harness
`deferred`, and the caller becomes responsible for immediately arming the same generation-keyed
debounce against the receipt's exact target and product turn. Background never selects this mode.
Such a control plane may also opt into `--resolve-target` while naming the exact saved DarkExec
workspace. The short executive task selects one allowed saved project from the supplied natural
request and attachments, then `dispatch` starts the product turn directly in that owner. The
resolver never becomes an intermediate target, cannot select its own workspace, and the resolved
target identity remains authoritative for steering, follow-ups, and deferred closeout.

The outer Codex caller keeps one orchestration tool call attached to the synchronous process.
Deterministic progress reads use `darkexec execution-status` against the exact executive thread and
may be projected through non-model UI notifications without settling that call. Ordinary waiting
does not re-enter the executive model. The attached App Server wait periodically rereads the exact
target turn after quiet socket intervals, so a dropped completion notification settles from native
task truth without a timeout or another model turn. This preserves the active executive turn and
queued follow-ups while avoiding repeated full-context polling turns.

While a product turn is active, `darkexec steer` delivers one exact executive-scoped instruction
through a mode-0600 local control socket. The attached runner sends native `turn/steer` on its
existing App Server connection and returns the native acknowledgement. The socket exists only
during product turns, requires the exact target and turn precondition, and is absent during harness
or synthetic closeout turns.

The `identity` command proves installed commit, protocol, saved projects, and App Server readiness
without starting work. Coordinated updates may require an exact fetched commit; a mismatch stops
before validation or the atomic current-release switch.

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
- append-only terminal harness episode identity;
- executive-scoped active-run identity and verified process ownership;
- executive-scoped same-turn steering sockets;
- signal and timeout terminalization;
- release and doctrine identity; and
- the caller's authority and prompt.

This alpha does not provide a hosted service, scheduler, detector, notification transport, or
general sandbox.
