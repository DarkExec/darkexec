# DarkExec — read `/srv/darkexec/harness-ops.md`, then:
1. Manual harness passes stay here; otherwise resolve the owner and send the request verbatim.
2. List Codex App projects and select the exact saved project path.
3. Create one App task with the request as its first turn.
4. Resolve its identity from the response or current App state.
5. A completed target turn is not terminal: before any final response, send that same task the standard `/srv/darkexec/harness-ops.md` harness prompt, then report its ID and exit; report interruptions without resuming.
