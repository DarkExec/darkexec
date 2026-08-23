#!/usr/bin/env bash
set -euo pipefail
root="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"; commit="$(git -C "$root" rev-parse HEAD)"
install="${DARKEXEC_INSTALL_ROOT:-/opt/darkexec}"; release="$install/releases/$commit"; workspace="${DARKEXEC_WORKSPACE:-/srv/darkexec}"
bin_path="${DARKEXEC_BIN_PATH:-/usr/local/bin/darkexec}"; host_doctrine="${DARKEXEC_HOST_DOCTRINE_PATH:-/srv/harness-ops.md}"
libexec_path="${DARKEXEC_DOCTRINE_REFRESH_PATH:-/usr/local/libexec/darkexec-refresh-harness-ops}"
doctrine_root="${DARKEXEC_HARNESS_OPS_ROOT:-/var/lib/darkexec/harness-ops}"
execution_root="${DARKEXEC_EXECUTION_ROOT:-/var/lib/darkexec/executives}"
harness_episode_root="${DARKEXEC_HARNESS_EPISODE_ROOT:-/var/lib/darkexec/harness-episodes}"
"$root/scripts/validate.sh"; mkdir -p "$install/releases" "$workspace"
[[ -d "$release" ]] || { git clone --quiet --no-local "$root" "$release"; git -C "$release" checkout --quiet --detach "$commit"; }
"$release/scripts/verify_install_contract.py" "$release/bin/darkexec" >/dev/null
mkdir -p "$(dirname "$libexec_path")"; ln -sfn "$release/scripts/refresh_harness_ops.py" "$libexec_path"
DARKEXEC_HARNESS_OPS_ROOT="$doctrine_root" "$libexec_path" --json >/dev/null
ln -sfn "$release" "$install/.current"; mv -Tf "$install/.current" "$install/current"
ln -sfn "$install/current/share/workspace/AGENTS.md" "$workspace/AGENTS.md"; ln -sfn "$install/current/share/workspace/README.md" "$workspace/README.md"
ln -sfn "$install/current/share/harness-ops.md" "$workspace/harness-ops.md"
managed_doctrine="$doctrine_root/current/harness-ops.md"
mkdir -p "$(dirname "$host_doctrine")"
current_host_binding="$(readlink "$host_doctrine" 2>/dev/null || true)"
if [[ ! -e "$host_doctrine" || "$current_host_binding" == "/srv/harness-ops/harness-ops.md" || "$current_host_binding" == "$workspace/harness-ops.md" ]]; then
  ln -sfn "$managed_doctrine" "$host_doctrine"
fi
mkdir -p "$(dirname "$bin_path")"; ln -sfn "$install/current/bin/darkexec" "$bin_path"
mkdir -p "$execution_root" "$harness_episode_root"; chmod 0700 "$execution_root" "$harness_episode_root"
doctrine_sha="$(sha256sum "$host_doctrine" | awk '{print $1}')"
printf '{"commit":"%s","workspace":"%s","binPath":"%s","doctrineSha256":"%s","doctrineDistributionRoot":"%s","turnTimeoutDefault":0,"stopControl":true,"executionRoot":"%s","harnessEpisodeRoot":"%s","nextAction":"Open %s in Codex App and send the outcome you want."}\n' \
  "$commit" "$workspace" "$bin_path" "$doctrine_sha" "$doctrine_root" "$execution_root" "$harness_episode_root" "$workspace"
