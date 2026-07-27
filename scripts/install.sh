#!/usr/bin/env bash
set -euo pipefail
root="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"; commit="$(git -C "$root" rev-parse HEAD)"
install="${DARKEXEC_INSTALL_ROOT:-/opt/darkexec}"; release="$install/releases/$commit"; workspace="${DARKEXEC_WORKSPACE:-/srv/darkexec}"
bin_path="${DARKEXEC_BIN_PATH:-/usr/local/bin/darkexec}"; host_doctrine="${DARKEXEC_HOST_DOCTRINE_PATH:-/srv/harness-ops.md}"
"$root/scripts/validate.sh"; mkdir -p "$install/releases" "$workspace"
[[ -d "$release" ]] || { git clone --quiet --no-local "$root" "$release"; git -C "$release" checkout --quiet --detach "$commit"; }
ln -sfn "$release" "$install/.current"; mv -Tf "$install/.current" "$install/current"
ln -sfn "$install/current/share/workspace/AGENTS.md" "$workspace/AGENTS.md"; ln -sfn "$install/current/share/workspace/README.md" "$workspace/README.md"
ln -sfn "$install/current/share/harness-ops.md" "$workspace/harness-ops.md"
[[ -e "$host_doctrine" || -L "$host_doctrine" ]] || ln -s "$workspace/harness-ops.md" "$host_doctrine"
mkdir -p "$(dirname "$bin_path")"; ln -sfn "$install/current/bin/darkexec" "$bin_path"
doctrine_sha="$(sha256sum "$release/share/harness-ops.md" | awk '{print $1}')"
printf '{"commit":"%s","workspace":"%s","binPath":"%s","doctrineSha256":"%s","nextAction":"Open %s in Codex App and send the outcome you want."}\n' \
  "$commit" "$workspace" "$bin_path" "$doctrine_sha" "$workspace"
