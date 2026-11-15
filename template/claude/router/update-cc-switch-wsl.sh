#!/usr/bin/env bash
# Upgrade CC Switch inside WSL using official GitHub release .deb with SHA-256 verification.
# Usage:
#   update-cc-switch-wsl.sh 4.0.31             Download official .deb, verify SHA-256, install via sudo apt
#   update-cc-switch-wsl.sh /path/to/pkg.deb   Install from local .deb
#   Add --no-hash to proceed if GitHub release metadata omits asset digest (aborts by default)
set -euo pipefail

SCRIPT_DIR="$(CDPATH= cd -- "$(dirname -- "${BASH_SOURCE[0]}")" && pwd -P)"
PKGS="$HOME/cc-switch-app.pkgs"
UID_CUR="$(id -u)"
if [ "$#" -lt 1 ] || [ "$#" -gt 2 ] || { [ "$#" -eq 2 ] && [ "$2" != "--no-hash" ]; }; then
    echo "Usage: update-cc-switch-wsl.sh <version|local-deb-path> [--no-hash]" >&2
    exit 2
fi
arg="$1"
allow_nohash="${2:-}"
part=""
cleanup() {
    if [ -n "$part" ]; then rm -f -- "$part"; fi
}
trap cleanup EXIT
trap 'exit 130' INT
trap 'exit 143' TERM
trap 'exit 129' HUP

resolve_launcher() {
    local candidate
    for candidate in \
        "$SCRIPT_DIR/launch-cc-switch-wsl.sh" \
        "$HOME/.claude/router/launch-cc-switch-wsl.sh"; do
        if [ -x "$candidate" ]; then
            printf '%s\n' "$candidate"
            return 0
        fi
    done
    return 1
}

if [ -f "$arg" ]; then
    deb="$(readlink -f -- "$arg")"
else
    ver="${arg#v}"
    if [[ ! "$ver" =~ ^[0-9]+(\.[0-9]+){0,3}(-[A-Za-z0-9][A-Za-z0-9.-]*)?$ ]]; then
        echo "Invalid version or missing local .deb file: $arg" >&2
        exit 2
    fi
    mkdir -p "$PKGS"
    arch_raw="$(uname -m)"
    case "$arch_raw" in
        x86_64|amd64) deb_arch="x86_64" ;;
        aarch64|arm64) deb_arch="aarch64" ;;
        *)
            echo "Unsupported CPU architecture for CC Switch Linux .deb: $arch_raw" >&2
            exit 2
            ;;
    esac
    name="CC-Switch-v${ver}-Linux-${deb_arch}.deb"
    deb="$PKGS/$name"
    want="$(curl -fsSL --proto '=https' --tlsv1.2 --connect-timeout 10 --max-time 30 \
        "https://api.github.com/repos/farion1231/cc-switch/releases/tags/v${ver}" \
        | python3 -I -B -X utf8 -c 'import json,sys; r=json.load(sys.stdin); print(next(((a.get("digest") or "") for a in r["assets"] if a["name"] == sys.argv[1]), ""))' "$name")"
    if [ -z "$want" ] && [ "$allow_nohash" != "--no-hash" ]; then
        echo "GitHub release did not publish SHA-256 for $name; aborting (pass --no-hash to override)." >&2
        exit 1
    fi
    if [[ -n "$want" && ! "$want" =~ ^sha256:[A-Fa-f0-9]{64}$ ]]; then
        echo "Invalid digest format from GitHub release metadata; aborting." >&2
        exit 1
    fi
    echo "Downloading $name (expected digest: ${want:-none})"
    part=$(mktemp "$PKGS/.${name}.XXXXXX.part")
    curl -fL --proto '=https' --tlsv1.2 --connect-timeout 10 --max-time 180 --retry 3 -o "$part" \
        "https://github.com/farion1231/cc-switch/releases/download/v${ver}/${name}"
    got="sha256:$(sha256sum "$part" | cut -d' ' -f1)"
    if [ -n "$want" ] && [ "${want,,}" != "$got" ]; then
        echo "Digest mismatch (expected $want, got $got); aborting." >&2
        exit 1
    fi
    mv -- "$part" "$deb"
    part=""
fi

[ "$(dpkg-deb -f "$deb" Package)" = "cc-switch" ] || { echo "$deb is not a cc-switch package; aborting." >&2; exit 1; }
dpkg-deb -f "$deb" Package Version

LAUNCHER="$(resolve_launcher || true)"
pkill -TERM -u "$UID_CUR" -f '^/usr/bin/cc-switch( |$)' 2>/dev/null || true
if ! sudo apt install -y "$deb"; then
    echo "Installation failed; attempting to restart previous version." >&2
    if [ -n "$LAUNCHER" ]; then "$LAUNCHER" || true; fi
    exit 1
fi
if [ -n "$LAUNCHER" ]; then
    "$LAUNCHER"
fi
installed=$(dpkg-query -W -f='${Version}' cc-switch)
echo "Installed cc-switch: $installed"
