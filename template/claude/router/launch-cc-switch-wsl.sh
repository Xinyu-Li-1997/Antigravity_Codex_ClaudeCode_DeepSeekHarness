#!/usr/bin/env bash
# WSLg launcher for CC Switch using system WebKitGTK, X11 backend, software rendering, and 100% scale.
# Checks Windows RAIL window visibility after launch and restores/restarts once if hidden at 0x0.
set -euo pipefail

APP=/usr/bin/cc-switch
PAT='(^|/)cc-switch( |$)'
UID_CUR="$(id -u)"
LOG_DIR="$HOME/.claude/router/logs"
LOG_FILE="$LOG_DIR/cc-switch-gui.log"
WINDOWS_POWERSHELL="/mnt/c/Windows/System32/WindowsPowerShell/v1.0/powershell.exe"

resolve_powershell() {
    local ps_bin
    if ps_bin="$(command -v powershell.exe 2>/dev/null)" && [ -n "$ps_bin" ]; then
        printf '%s\n' "$ps_bin"
        return 0
    fi
    if [ -x "$WINDOWS_POWERSHELL" ]; then
        printf '%s\n' "$WINDOWS_POWERSHELL"
        return 0
    fi
    return 1
}
if [ ! -x "$APP" ]; then
    echo "CC Switch binary not found or not executable: $APP" >&2
    exit 1
fi

window_state() {
    local ps_bin enc ps_cwd
    ps_bin="$(resolve_powershell)" || return 0
    enc=$(cat <<'PS' | iconv -f UTF-8 -t UTF-16LE | base64 -w0
$ProgressPreference = 'SilentlyContinue'
Add-Type -TypeDefinition 'using System;
using System.Runtime.InteropServices;
using System.Text;
public class RWChk {
    public delegate bool EnumWindowsProc(IntPtr h, IntPtr lp);
    [DllImport("user32.dll")] public static extern bool EnumWindows(EnumWindowsProc cb, IntPtr lp);
    [DllImport("user32.dll")] public static extern int GetWindowText(IntPtr h, StringBuilder sb, int n);
    [DllImport("user32.dll")] public static extern int GetClassName(IntPtr h, StringBuilder sb, int n);
    [DllImport("user32.dll")] public static extern bool IsWindowVisible(IntPtr h);
    [DllImport("user32.dll")] public static extern bool IsIconic(IntPtr h);
    [DllImport("user32.dll")] public static extern bool GetWindowRect(IntPtr h, out RECT r);
    [DllImport("user32.dll")] public static extern bool ShowWindow(IntPtr h, int cmd);
    [DllImport("user32.dll")] public static extern bool SetForegroundWindow(IntPtr h);
    [StructLayout(LayoutKind.Sequential)] public struct RECT { public int L, T, R, B; }
}'
$wins = New-Object System.Collections.ArrayList
$cb = {
    param($h, $lp)
    $c = New-Object System.Text.StringBuilder 256
    [RWChk]::GetClassName($h, $c, 256) | Out-Null
    if ($c.ToString() -eq 'RAIL_WINDOW') {
        $t = New-Object System.Text.StringBuilder 512
        [RWChk]::GetWindowText($h, $t, 512) | Out-Null
        $title = $t.ToString().Trim()
        if ($title -match '(?i)cc[\s_-]*switch') { [void]$wins.Add($h) }
    }
    return $true
}
[RWChk]::EnumWindows($cb, [IntPtr]::Zero) | Out-Null
function TestVis {
    foreach ($h in $wins) {
        $r = New-Object RWChk+RECT
        [RWChk]::GetWindowRect($h, [ref]$r) | Out-Null
        if ([RWChk]::IsWindowVisible($h) -and -not [RWChk]::IsIconic($h) -and ($r.R - $r.L) -ge 200 -and ($r.B - $r.T) -ge 200) { return $true }
    }
    return $false
}
if (TestVis) {
    Write-Output 'RESULT:VISIBLE'
} else {
    foreach ($h in $wins) { [RWChk]::ShowWindow($h, 9) | Out-Null; [RWChk]::SetForegroundWindow($h) | Out-Null }
    Start-Sleep -Milliseconds 800
    if (TestVis) { Write-Output 'RESULT:RESTORED' } else { Write-Output 'RESULT:NOTVISIBLE' }
}
PS
) || return 0
    [ -n "$enc" ] || return 0
    ps_cwd="/mnt/c/Windows/System32"
    [ -d "$ps_cwd" ] || ps_cwd="/"
    (
        cd "$ps_cwd" && timeout 8s "$ps_bin" -NoLogo -NoProfile -NonInteractive -EncodedCommand "$enc"
    ) 2>/dev/null | tr -d '\r' | grep '^RESULT:' | tail -1 | cut -d: -f2 || true
}

start_app() {
    mkdir -p -m 700 "$LOG_DIR"
    cd "$HOME" || exit 1
    setsid nohup env -u LD_LIBRARY_PATH DISPLAY="${DISPLAY:-:0}" LIBGL_ALWAYS_SOFTWARE=1 \
        WEBKIT_DISABLE_DMABUF_RENDERER=1 WEBKIT_DISABLE_COMPOSITING_MODE=1 \
        GDK_BACKEND=x11 GDK_SCALE=1 GDK_DPI_SCALE=1 \
        "$APP" >>"$LOG_FILE" 2>&1 </dev/null &
}

wait_visible() {
    local n="$1" i st
    for i in $(seq 1 "$n"); do
        sleep 2
        if ! pgrep -u "$UID_CUR" -f "$PAT" >/dev/null; then
            echo "CC Switch process exited." >&2
            return 1
        fi
        st=$(window_state)
        case "$st" in
            VISIBLE|RESTORED) echo "Window state: $st"; return 0 ;;
            NOTVISIBLE) ;;
            *) return 2 ;;
        esac
    done
    return 1
}

RUNNING=$( { pgrep -u "$UID_CUR" -f "$PAT" || true; } | tr '\n' ' ')
if [ -n "$RUNNING" ]; then
    echo "CC Switch already running (PID ${RUNNING% }); requesting window restore."
    st=$(window_state)
    case "$st" in
        VISIBLE|RESTORED)
            echo "Window state: $st"
            exit 0
            ;;
        NOTVISIBLE)
            ;;
        *)
            echo "Windows RAIL visibility query unavailable; keeping running instance."
            exit 0
            ;;
    esac
else
    start_app
    if wait_visible 4; then
        exit 0
    else
        status=$?
        if [ "$status" -eq 2 ]; then
            echo "Windows RAIL visibility query unavailable; skipping window check."
            exit 0
        fi
    fi
fi

echo "Window still hidden; restarting CC Switch once."
for p in $(pgrep -u "$UID_CUR" -f "$PAT" || true); do [ "$p" = "$$" ] && continue; kill "$p" 2>/dev/null || true; done
for i in $(seq 1 10); do
    sleep 1
    pgrep -u "$UID_CUR" -f "$PAT" >/dev/null || break
done
for p in $(pgrep -u "$UID_CUR" -f "$PAT" || true); do [ "$p" = "$$" ] && continue; kill -9 "$p" 2>/dev/null || true; done
sleep 1
start_app

if wait_visible 5; then
    echo "Window visible after restart."
    exit 0
else
    status=$?
    if [ "$status" -eq 2 ]; then
        echo "Windows RAIL visibility query unavailable; skipping window check."
        exit 0
    fi
fi
echo "Window still not visible after restart. Check $LOG_FILE and WSLg status."
exit 1
