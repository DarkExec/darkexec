#!/usr/bin/env bash
set -euo pipefail
root="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
for path in AGENTS.md ARCHITECTURE.md LICENSE README.md bin/darkexec share/harness-ops.md share/harness-ops.provenance.json share/workspace/AGENTS.md scripts/install.sh scripts/test_cli.py; do [[ -s "$root/$path" ]] || exit 1; done
grep -Fqx '5. A completed target turn is not terminal: before any final response, send that same task the standard `/srv/darkexec/harness-ops.md` harness prompt, then report its ID and exit; report interruptions without resuming.' "$root/share/workspace/AGENTS.md"
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
