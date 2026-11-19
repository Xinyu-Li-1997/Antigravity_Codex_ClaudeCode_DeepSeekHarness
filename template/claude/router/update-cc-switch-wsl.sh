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
deb_link_dir=""
cleanup() {
    if [ -n "$part" ]; then rm -f -- "$part"; fi
    if [ -n "$deb_link_dir" ]; then rm -rf -- "$deb_link_dir"; fi
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
        if [ -f "$candidate" ]; then
            printf '%s\n' "$candidate"
            return 0
        fi
    done
    return 1
}

run_launcher() {
    if [ -f "$LAUNCHER" ] && [ ! -x "$LAUNCHER" ]; then
        bash "$LAUNCHER"
    else
        "$LAUNCHER"
    fi
}

if [ -f "$arg" ]; then
    deb="$(readlink -f -- "$arg")"
    if [[ "$deb" != *.deb ]]; then
        deb_link_dir="$(mktemp -d -t cc-switch-deb.XXXXXX)"
        chmod 755 "$deb_link_dir"
        ln -s -- "$deb" "$deb_link_dir/cc-switch-local.deb"
        deb="$deb_link_dir/cc-switch-local.deb"
    fi
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
    meta_json="$(curl -sSL --proto '=https' --tlsv1.2 --connect-timeout 10 --max-time 30 \
        "https://api.github.com/repos/farion1231/cc-switch/releases/tags/v${ver}")"
    asset_info="$(printf '%s' "$meta_json" | python3 -I -B -X utf8 -c '
import json, sys
try:
    r = json.load(sys.stdin)
except Exception:
    print("INVALID_JSON")
    sys.exit(0)
assets = r.get("assets") if isinstance(r, dict) else None
if not isinstance(assets, list):
    msg = str(r.get("message") or "").strip() if isinstance(r, dict) else ""
    print("NO_ASSETS:" + msg)
    sys.exit(0)
match = next((a for a in assets if isinstance(a, dict) and a.get("name") == sys.argv[1]), None)
if match is None:
    print("MISSING")
else:
    print("FOUND:" + str(match.get("digest") or ""))
' "$name")"
    if [ "$asset_info" = "INVALID_JSON" ]; then
        echo "GitHub release metadata for v${ver} is not valid JSON; aborting." >&2
        exit 1
    fi
    if [[ "$asset_info" == NO_ASSETS:* ]]; then
        api_msg="${asset_info#NO_ASSETS:}"
        echo "GitHub release metadata for v${ver} is missing assets list${api_msg:+ ($api_msg)}; aborting." >&2
        exit 1
    fi
    if [ "$asset_info" = "MISSING" ]; then
        echo "GitHub release v${ver} does not contain asset $name; aborting." >&2
        exit 1
    fi
    want="${asset_info#FOUND:}"
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

APP_PAT='(^|/)cc-switch( |$)'
LAUNCHER="$(resolve_launcher || true)"
matching_app_pids() {
    local pid raw_pids
    raw_pids="$(pgrep -u "$UID_CUR" -f "$APP_PAT" 2>/dev/null)" || true
    for pid in $raw_pids; do
        if [ "$pid" != "$$" ] && [ "$pid" != "$PPID" ] && [ "$(ps -o ppid= -p "$pid" 2>/dev/null | tr -d ' ')" != "$$" ]; then
            printf '%s\n' "$pid"
        fi
    done
}
signal_app_pids() {
    local sig="$1" pid
    shift
    for pid in "$@"; do
        if [ "$pid" != "$$" ] && [ "$pid" != "$PPID" ]; then
            kill "-$sig" "$pid" 2>/dev/null || true
        fi
    done
    if [ "$#" -gt 0 ] && [ "$(type -t pkill 2>/dev/null || true)" = "function" ]; then
        pkill "-$sig" -u "$UID_CUR" -f "$APP_PAT" 2>/dev/null || true
    fi
}
WAS_RUNNING=0
mapfile -t TARGET_PIDS < <(matching_app_pids)
if [ "${#TARGET_PIDS[@]}" -gt 0 ]; then
    WAS_RUNNING=1
    signal_app_pids TERM "${TARGET_PIDS[@]}"
    for _ in $(seq 1 20); do
        mapfile -t TARGET_PIDS < <(matching_app_pids)
        if [ "${#TARGET_PIDS[@]}" -eq 0 ]; then
            break
        fi
        sleep 0.25
    done
    mapfile -t TARGET_PIDS < <(matching_app_pids)
    if [ "${#TARGET_PIDS[@]}" -gt 0 ]; then
        signal_app_pids KILL "${TARGET_PIDS[@]}"
        sleep 0.2
    fi
fi
if ! sudo apt install -y "$deb"; then
    if [ "$WAS_RUNNING" -eq 1 ] && [ -n "$LAUNCHER" ]; then
        echo "Installation failed; attempting to restart previous version." >&2
        run_launcher || echo "Warning: failed to restart previous CC Switch GUI." >&2
    fi
    exit 1
fi
installed=$(dpkg-query -W -f='${Version}' cc-switch)
echo "Installed cc-switch: $installed"
if [ "$WAS_RUNNING" -eq 1 ] && [ -n "$LAUNCHER" ]; then
    if ! run_launcher; then
        echo "Warning: cc-switch upgraded to $installed, but GUI relaunch failed." >&2
    fi
fi
