#!/usr/bin/env bash
set -euo pipefail
root="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
for path in AGENTS.md ARCHITECTURE.md LICENSE README.md bin/darkexec share/harness-ops.md share/harness-ops.provenance.json share/workspace/AGENTS.md scripts/install.sh scripts/test_cli.py; do [[ -s "$root/$path" ]] || exit 1; done
grep -Fqx '2. Resolve one exact saved project. Use a single named absolute saved path directly; otherwise run `darkexec projects --json` once. Ambiguous or unsaved targets fail closed.' "$root/share/workspace/AGENTS.md"
grep -Fqx '3. Choose transport once from callable tools already present. Use native task controls only when `read_thread`, `list_threads`, `create_thread`, `send_message_to_thread`, and `wait_threads` are all available; otherwise immediately use `darkexec run --target PATH --prompt-stdin (--read-only-harness | --standard-harness) --json`. Acknowledgement-only, exact-response, no-tools, or no-change requests require `--read-only-harness`; all others require `--standard-harness`. Never omit the mode. Do not inspect memory, source, schemas, sockets, processes, or App Server protocol to choose.' "$root/share/workspace/AGENTS.md"
grep -Fqx '4. On the first delegated request, create one target task, preserve the request byte-for-byte as its first real turn, and wait directly. With `darkexec run`, pipe it with `printf '"'"'%s'"'"'`, never `printf '"'"'%s\n'"'"'`, invoke it once with the longest available timeout, and keep it alive through its immediate same-task harness and terminal JSON. With native controls, immediately send the matching harness prompt from step 5 to that target and wait before reporting. Treat any caller timeout or interruption as terminal and never retry or resume automatically.' "$root/share/workspace/AGENTS.md"
grep -Fqx '5. On a clearly dependent follow-up in this same executive task whose owner is the same exact saved project, reuse the established target with `send_message_to_thread`; never create a replacement. Wait, reread that target to resolve the completed product turn ID, then run `darkexec debounce --target PATH --thread TARGET_ID --turn TURN_ID --harness-mode (read-only|standard) --json` once; it resets an owned 30-minute timer and falls back to immediate closeout if scheduling fails. The timer rereads the exact target and suppresses itself if a manual or automatic harness or newer activity followed the recorded turn; otherwise it sends the matching harness: `FIRE DRILL harness proof only. Review this task'"'"'s completed trajectory without using tools, inspecting files, changing state, continuing product work, or contacting anyone. Briefly report whether the request stayed read-only and whether any harness change is warranted; make no change.` for acknowledgement-only, exact-response, no-tools, or no-change work; otherwise `Let'"'"'s do a harness pass where we take a look at this session and turn trial and error into fast, reliable, and durable execution. Make sure we are following /srv/darkexec/harness-ops.md doctrine.` For an explicit manual harness, send it now, wait, then run `darkexec debounce-cancel --thread TARGET_ID --json`. Flush likewise before dependent cross-task work or a consequential phase change. Background `darkexec run` and `dispatch` always close out immediately. Report identities and results briefly; never infer continuity across executive tasks or resume an interruption.' "$root/share/workspace/AGENTS.md"
grep -Fqx '[[ -e "$host_doctrine" ]] || ln -sfn "$workspace/harness-ops.md" "$host_doctrine"' "$root/scripts/install.sh"
grep -Fqx 'TURN_TIMEOUT = int(os.environ.get("DARKEXEC_TURN_TIMEOUT", "0"))' "$root/bin/darkexec"
python3 - "$root" <<'PY'
import hashlib, json, pathlib, sys
root = pathlib.Path(sys.argv[1])
artifact = root / "share/harness-ops.md"
provenance = json.loads((root / "share/harness-ops.provenance.json").read_text())
actual = hashlib.sha256(artifact.read_bytes()).hexdigest()
assert provenance["repository"] == "https://github.com/DarkExec/harness-ops"
assert len(provenance["commit"]) == 40
assert provenance["sha256"] == actual, (provenance["sha256"], actual)
PY
PYTHONDONTWRITEBYTECODE=1 python3 "$root/scripts/test_cli.py"
bash -n "$root/scripts/install.sh" "$root/scripts/validate.sh"
git -C "$root" diff --check
