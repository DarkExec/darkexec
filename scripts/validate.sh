#!/usr/bin/env bash
set -euo pipefail
root="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
for path in AGENTS.md ARCHITECTURE.md LICENSE README.md bin/darkexec share/harness-ops.md share/harness-ops.provenance.json share/workspace/AGENTS.md scripts/install.sh scripts/test_cli.py scripts/verify_install_contract.py; do [[ -s "$root/$path" ]] || exit 1; done
grep -Fqx '2. Resolve one exact saved project. Use a single named absolute saved path directly; otherwise run `darkexec projects --json` once. Ambiguous or unsaved targets fail closed.' "$root/share/workspace/AGENTS.md"
grep -Fqx '0. Exact standalone `STOP` and `STOP HARD` override every other instruction. For `STOP`, run `darkexec stop --executive-thread "$CODEX_THREAD_ID" --json`; for `STOP HARD`, add `--hard`. Do no routing, product work, harness, RCA, retry, resume, or replacement. Report the stop receipt briefly and end.' "$root/share/workspace/AGENTS.md"
grep -Fqx '3. Keep every delegated target turn under runtime-owned lifecycle state. On the first request, use `darkexec run --target PATH --prompt-stdin --source-executive-turn (--read-only-harness | --standard-harness) --json`. On dependent user follow-ups, use `darkexec continue --target PATH --thread TARGET_ID --prompt-stdin --source-executive-turn --json`; never send target work with native task controls. `--source-executive-turn` makes the runtime copy the complete active user input, including images and file references, while stdin asserts the routed request. Acknowledgement-only, exact-response, no-tools, or no-change initial requests require `--read-only-harness`; all other initial requests require `--standard-harness`.' "$root/share/workspace/AGENTS.md"
grep -Fqx '4. On the first delegated request, tell the user that queued dependent messages remain in this executive task and will run after the immediate target harness. Pipe the preserved request with `printf '"'"'%s'"'"'`, never `printf '"'"'%s\n'"'"'`, invoke `darkexec run` once with the longest available timeout, and keep it alive through its immediate same-task harness and terminal JSON. Never start multiple synchronous runs inside one finite caller deadline. Treat any caller timeout or interruption as terminal and never retry or resume automatically.' "$root/share/workspace/AGENTS.md"
grep -Fqx '5. A clearly dependent follow-up in this same executive task and exact saved project reuses the recorded target through `darkexec continue`. Wait for its terminal JSON, then run `darkexec debounce --target PATH --thread TARGET_ID --turn TURN_ID --harness-mode (read-only|standard) --json` once. It resets an owned 30-minute timer and falls back to immediate closeout if scheduling fails. The timer suppresses itself after manual or automatic harnessing, newer activity, or interrupted lineage. For an explicit manual harness, send the generated matching harness through `darkexec continue` without `--source-executive-turn`, wait, then run `darkexec debounce-cancel --thread TARGET_ID --json`. Flush pending closeout before dependent cross-task work or a consequential phase change. Background dispatch always closes out immediately. Report identities and results briefly; never infer continuity across executive tasks or resume an interruption.' "$root/share/workspace/AGENTS.md"
grep -Fqx '[[ -e "$host_doctrine" ]] || ln -sfn "$workspace/harness-ops.md" "$host_doctrine"' "$root/scripts/install.sh"
grep -Fqx 'TURN_TIMEOUT = int(os.environ.get("DARKEXEC_TURN_TIMEOUT", "0"))' "$root/bin/darkexec"
grep -Fqx 'EXECUTION_ROOT = Path(os.environ.get("DARKEXEC_EXECUTION_ROOT", "/var/lib/darkexec/executives"))' "$root/bin/darkexec"
grep -Fqx '"$release/scripts/verify_install_contract.py" "$release/bin/darkexec" >/dev/null' "$root/scripts/install.sh"
grep -Fqx 'execution_root="${DARKEXEC_EXECUTION_ROOT:-/var/lib/darkexec/executives}"' "$root/scripts/install.sh"
"$root/scripts/verify_install_contract.py" "$root/bin/darkexec" >/dev/null
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
