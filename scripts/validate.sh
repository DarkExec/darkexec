#!/usr/bin/env bash
set -euo pipefail
root="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
for path in AGENTS.md ARCHITECTURE.md LICENSE README.md bin/darkexec share/harness-ops.md share/harness-ops.provenance.json share/workspace/AGENTS.md scripts/install.sh scripts/test_cli.py; do [[ -s "$root/$path" ]] || exit 1; done
grep -Fqx '2. Resolve one exact saved project. Use a single named absolute saved path directly; otherwise list projects once. Ambiguous or unsaved targets fail closed.' "$root/share/workspace/AGENTS.md"
grep -Fqx '3. Choose transport once from callable tools already present. Use native task controls only when `list_threads`, `create_thread`, `send_message_to_thread`, and `wait_threads` are all available; otherwise immediately use `darkexec run --target PATH --prompt-stdin [--read-only-harness] --json`. Do not inspect memory, source, schemas, sockets, processes, or App Server protocol to choose.' "$root/share/workspace/AGENTS.md"
grep -Fqx '4. Preserve the request verbatim as the target task'"'"'s first real turn and wait directly. With `darkexec run`, send it through stdin and treat complete terminal JSON as identity and status proof; reread only if that result is missing or contradictory.' "$root/share/workspace/AGENTS.md"
grep -Fqx '5. A completed target turn is not terminal; `darkexec run` owns this step when used. For an acknowledgement-only, exact-response, no-tools, or no-change request, send that same task: `FIRE DRILL harness proof only. Review this task'"'"'s completed trajectory without using tools, inspecting files, changing state, continuing product work, or contacting anyone. Briefly report whether the request stayed read-only and whether any harness change is warranted; make no change.` Otherwise send it: `Let'"'"'s do a harness pass where we take a look at this session and turn trial and error into fast, reliable, and durable execution. Make sure we are following /srv/darkexec/harness-ops.md doctrine.` Keep any local App proxy/session alive and wait directly for that harness turn to complete. Only then report its ID and result; report interruptions without resuming.' "$root/share/workspace/AGENTS.md"
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
