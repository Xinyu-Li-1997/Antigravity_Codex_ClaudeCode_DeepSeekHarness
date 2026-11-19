#!/usr/bin/env bash
# Antigravity, Codex, Claude Code & DeepSeek Harness 统一安装脚本 (Linux / WSL2)
set -euo pipefail

# 清空 CDPATH，防止 cd 的额外输出混入安装器路径。
SCRIPT_DIR="$(CDPATH= cd -- "$(dirname -- "${BASH_SOURCE[0]}")" && pwd -P)"
export PYTHONDONTWRITEBYTECODE=1

printf '%s\n' "[*] 启动四端统一配置安装器 (Antigravity / Codex / Claude Code / DSH)..." >&2

seen_executables=$'\n'
for candidate in python3 python python3.14 python3.13 python3.12 python3.11; do
    candidates_out="$(type -ap "${candidate}" 2>/dev/null || true)"
    [[ -n "${candidates_out}" ]] || continue
    while IFS= read -r executable; do
        [[ -n "${executable}" ]] || continue
        if [[ "${seen_executables}" == *$'\n'"${executable}"$'\n'* ]]; then
            continue
        fi
        seen_executables+="${executable}"$'\n'
        if [[ "${executable}" == /mnt/[a-zA-Z]/* || "${executable}" == *.[eE][xX][eE] || "${executable}" == *.[cC][mM][dD] || "${executable}" == *.[bB][aA][tT] ]]; then
            continue
        fi
        resolved_exe="$(readlink -f -- "${executable}" 2>/dev/null || printf '%s' "${executable}")"
        if [[ "${resolved_exe}" == /mnt/[a-zA-Z]/* || "${resolved_exe}" == *.[eE][xX][eE] || "${resolved_exe}" == *.[cC][mM][dD] || "${resolved_exe}" == *.[bB][aA][tT] ]]; then
            continue
        fi
        if probe_output="$("${executable}" -I -B -c 'import sys; print("user-guard-python-ok"); raise SystemExit(0 if sys.version_info >= (3, 11) else 1)' 2>/dev/null)" &&
            [[ "${probe_output}" == "user-guard-python-ok" ]]; then
            exec "${executable}" -B -X utf8 "${SCRIPT_DIR}/install.py" "$@"
        fi
    done <<< "${candidates_out}"
done

printf '%s\n' "[!] 错误：未找到可执行的 Python 3.11+，请检查 PATH (Python 3.11+ not found in PATH)." >&2
exit 1
