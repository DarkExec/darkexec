# DarkExec

Open this project in Codex App and state the outcome you want. This installed workspace includes the
exact Harness Ops doctrine qualified with this DarkExec release.

## Attached waiting

Interactive runs stay attached to one synchronous `darkexec run` or `darkexec continue` process
until its terminal JSON arrives. The executive turn remains active during that attachment, so
dependent user messages stay queued for the same target after the immediate harness.

When the Codex `functions.exec` orchestration tool is available, start the nested shell command once
and await that same promise. Set both the exec yield and nested shell timeout beyond the intended
run. While it is pending, a timer may read:

```bash
darkexec execution-status --executive-thread "$CODEX_THREAD_ID" --json
```

and emit only a compact `notify` update. `notify` does not settle the exec call or return control to
the model. Do not call the model-facing `wait` tool every few seconds or minutes; that replays the
full executive context without advancing target work.

The orchestration shape is:

```javascript
// @exec: {"yield_time_ms": 3600000, "max_output_tokens": 3000}
let done = false;
const pending = tools.shell_command({
  command: "printf '%s' '<preserved request>' | darkexec run <exact arguments>",
  workdir: "/srv/darkexec",
  timeout_ms: 7200000
}).then(
  value => ({ ok: true, value }),
  error => ({ ok: false, error: String(error) })
).finally(() => { done = true; });

while (!done) {
  const event = await Promise.race([
    pending.then(() => "completed"),
    new Promise(resolve => setTimeout(() => resolve("progress"), 60000))
  ]);
  if (event === "completed") break;
  const status = await tools.shell_command({
    command: "darkexec execution-status --executive-thread \"$CODEX_THREAD_ID\" --json",
    workdir: "/srv/darkexec",
    timeout_ms: 10000
  });
  const line = typeof status === "string"
    ? status.trim().split("\n").at(-1)
    : JSON.stringify(status);
  let snapshot;
  try {
    snapshot = JSON.parse(line);
  } catch {
    notify("DarkExec remains attached.");
    continue;
  }
  notify(
    `DarkExec ${snapshot.phase} · target ${snapshot.target?.threadId || "pending"}`
  );
}
const result = await pending;
text(result.ok ? result.value : result.error);
```

Use the actual active-turn-preserving command construction required by `AGENTS.md`; the placeholder
above illustrates attachment ownership, not prompt escaping. If the platform unexpectedly yields
the exec cell before terminal completion, resume that exact cell once with the longest available
wait. Never restart or replace the DarkExec process.

## Follow-ups to Background control tasks

A Background dispatch control task can accept a rare dependent follow-up without staying
model-active while its target runs. On the later user turn, resolve the immutable receipt by the
current control task ID:

```bash
darkexec status --thread "$CODEX_THREAD_ID" --json
```

If that exact receipt is still live, attach once to its terminal transition:

```bash
darkexec status --thread "$CODEX_THREAD_ID" --wait --json
```

The wait observes the dispatch lock and consumes no model turns. A nonterminal receipt without its
owning dispatch process reports `abandoned` instead of waiting forever. After a completed receipt,
send the complete active user turn only to the receipt's exact `targetPath` and target task ID with
`darkexec continue --source-executive-turn`, then use the normal debounce closeout. Never continue
a failed, interrupted, abandoned, mismatched, or inferred target.
