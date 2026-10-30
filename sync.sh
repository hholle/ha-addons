#!/usr/bin/env bash
# Sync add-ons straight to the HA host as local add-ons over SSH (no git push needed).
#   ./sync.sh                  sync every add-on (each dir containing a config.yaml)
#   ./sync.sh kaco-powador …   sync only the named add-ons
#   ./sync.sh -n               dry run: list what would be synced
#
# Settings (override via environment):
#   HA_SSH   SSH target of the HA host      (default: hholle@192.168.178.201)
#   HA_DIR   local add-on dir on the host   (default: /usr/share/hassio/apps/local)
set -euo pipefail

HA_SSH=${HA_SSH:-hholle@192.168.178.201}
HA_DIR=${HA_DIR:-/usr/share/hassio/apps/local}
STAGE=".ha-addons-sync"

cd "$(dirname "$0")"

DRY=0
if [ "${1:-}" = "-n" ]; then DRY=1; shift; fi

if [ $# -gt 0 ]; then
    ADDONS=("$@")
else
    ADDONS=()
    for c in */config.yaml; do ADDONS+=("$(dirname "$c")"); done
fi

for a in "${ADDONS[@]}"; do
    [ -f "$a/config.yaml" ] || { echo "Not an add-on (no config.yaml): $a" >&2; exit 1; }
done

for a in "${ADDONS[@]}"; do
    echo "$a $(sed -n 's/^version: "\(.*\)"/\1/p' "$a/config.yaml")"
done
[ "$DRY" = 1 ] && exit 0

# No rsync needed on the host: stream a tar per add-on over SSH into a staging dir,
# then move everything into the root-owned add-on dir with one sudo call
# (-t so a password prompt works).
ssh "$HA_SSH" "rm -rf '$STAGE' && mkdir -p '$STAGE'"
for a in "${ADDONS[@]}"; do
    echo "==> Copy $a"
    COPYFILE_DISABLE=1 tar --no-xattrs -czf - -C "$a" \
        --exclude '.DS_Store' --exclude '__pycache__' --exclude '.venv' \
        --exclude 'node_modules' --exclude 'tests' --exclude 'tmp' \
        --exclude 'PLAN.md' --exclude 'deploy.sh' . |
        ssh "$HA_SSH" "mkdir -p '$STAGE/$a' && tar -xzf - -C '$STAGE/$a'"
done

echo "==> Install into $HA_DIR (sudo)"
ssh -t "$HA_SSH" "sudo mkdir -p '$HA_DIR' && for a in ${ADDONS[*]}; do sudo rm -rf \"$HA_DIR/\$a\" && sudo cp -a \"\$HOME/$STAGE/\$a\" \"$HA_DIR/\$a\"; done; rm -rf \"\$HOME/$STAGE\""

echo
echo "Next: HA -> Settings -> Add-ons -> Add-on Store -> ⋮ -> Check for updates,"
echo "      then install/rebuild the add-ons under 'Local add-ons'."
