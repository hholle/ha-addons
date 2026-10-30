#!/usr/bin/env bash
# Deploy the vito-optolink add-on.
#   ./deploy.sh         tests, commit and push to git (HA: Check for updates -> Rebuild)
#   ./deploy.sh local   tests, copy straight to the HA host as a local add-on over SSH
#
# local mode settings (override via environment):
#   HA_SSH   SSH target of the HA host      (default: hholle@192.168.178.201)
#   HA_DIR   local add-on dir on the host   (default: /usr/share/hassio/apps/local)
set -euo pipefail

MODE=${1:-git}
HA_SSH=${HA_SSH:-hholle@192.168.178.201}
HA_DIR=${HA_DIR:-/usr/share/hassio/apps/local}

cd "$(dirname "$0")"
ADDON_DIR=$(basename "$PWD")
VERSION=$(sed -n 's/^version: "\(.*\)"/\1/p' config.yaml)

echo "==> Tests"
if ! python3 -c "import yaml, paho.mqtt, serial" 2>/dev/null; then
    [ -d .venv ] || python3 -m venv .venv
    .venv/bin/pip install -q pyyaml "paho-mqtt>=2.0" pyserial
    PY=.venv/bin/python
else
    PY=python3
fi
"$PY" -I -m unittest discover -s . -t .

case "$MODE" in
git)
    cd "$(git rev-parse --show-toplevel)"
    BRANCH=$(git rev-parse --abbrev-ref HEAD)
    git add "$ADDON_DIR"
    if git diff --cached --quiet; then
        echo "Nothing to commit in $ADDON_DIR (version $VERSION)."
        exit 0
    fi
    echo "==> Commit $ADDON_DIR $VERSION on $BRANCH"
    git commit -m "$ADDON_DIR $VERSION" -- "$ADDON_DIR"
    git push origin "$BRANCH"
    echo
    echo "Next: HA -> Settings -> Add-ons -> Add-on Store -> ⋮ -> Check for updates,"
    echo "      then install/rebuild 'Vito Optolink'. Stop the vcontrold add-on first."
    ;;
local)
    # No rsync needed on the host: stream a tar over SSH into a staging dir, then
    # move it into the root-owned add-on dir with sudo (-t so a password prompt works).
    STAGE=".vito-optolink-deploy"
    TARGET="$HA_DIR/$ADDON_DIR"
    echo "==> Copy $ADDON_DIR $VERSION to $HA_SSH:$TARGET"
    COPYFILE_DISABLE=1 tar --no-xattrs -czf - \
        --exclude '.DS_Store' --exclude '__pycache__' --exclude '.venv' --exclude 'tests' \
        --exclude 'PLAN.md' --exclude 'deploy.sh' --exclude 'tmp' . |
        ssh "$HA_SSH" "rm -rf '$STAGE' && mkdir -p '$STAGE' && tar -xzf - -C '$STAGE'"
    ssh -t "$HA_SSH" "S=\"\$HOME/$STAGE\"; sudo mkdir -p '$HA_DIR' && sudo rm -rf '$TARGET' && sudo cp -a \"\$S\" '$TARGET'"
    echo
    echo "Next: HA -> Settings -> Add-ons -> Add-on Store -> ⋮ -> Check for updates."
    echo "      The add-on shows up under 'Local add-ons'. Stop the vcontrold add-on first."
    ;;
*)
    echo "Usage: $0 [git|local]" >&2
    exit 1
    ;;
esac
