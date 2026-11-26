# Copyright 2026 Xinyu Li (Xinyu-Li-1997) | SPDX-License-Identifier: Apache-2.0
# Antigravity, Codex, Claude Code & DeepSeek Harness Windows 安装入口。
$ErrorActionPreference = "Stop"
$PSNativeCommandUseErrorActionPreference = $false
try { [Console]::OutputEncoding = [System.Text.UTF8Encoding]::new($false) } catch [System.IO.IOException] {} catch [System.Security.SecurityException] {}
$env:PYTHONDONTWRITEBYTECODE = "1"

function Find-WorkingPython {
    param([string]$Exe, [string[]]$PrefixArgs = @())
    # 跳过应用商店别名，逐个尝试 PATH 中的真实程序。
    $commands = Get-Command $Exe -CommandType Application -All -ErrorAction SilentlyContinue |
        Where-Object { $_.Source -notlike "*\Microsoft\WindowsApps\*" }
    foreach ($commandInfo in $commands) {
        $process = [System.Diagnostics.Process]::new()
        try {
            $process.StartInfo.FileName = $commandInfo.Source
            $process.StartInfo.Arguments = (@($PrefixArgs) + @(
                '-I', '-B', '-c',
                '"import sys; print(''user-guard-python-ok''); raise SystemExit(0 if sys.version_info >= (3, 11) else 1)"'
            )) -join ' '
            $process.StartInfo.UseShellExecute = $false
            $process.StartInfo.CreateNoWindow = $true
            $process.StartInfo.RedirectStandardOutput = $true
            $process.StartInfo.RedirectStandardError = $true
            $null = $process.Start()
            $stdoutTask = $process.StandardOutput.ReadToEndAsync()
            $stderrTask = $process.StandardError.ReadToEndAsync()
            if (-not $process.WaitForExit(5000)) {
                $process.Kill()
                $null = $process.WaitForExit(1000)
                continue
            }
            if (-not $stdoutTask.Wait(1000) -or -not $stderrTask.Wait(1000)) {
                continue
            }
            $probeOutput = $stdoutTask.Result.Trim()
            if ($process.ExitCode -eq 0 -and $probeOutput -eq 'user-guard-python-ok') {
                return $commandInfo.Source
            }
        } catch [System.ComponentModel.Win32Exception] {
            continue
        } catch [System.UnauthorizedAccessException] {
            continue
        } catch [System.InvalidOperationException] {
            continue
        } catch [System.AggregateException] {
            continue
        } finally {
            $process.Dispose()
        }
    }
}

$python = Find-WorkingPython "python.exe"
$launcher = $null
$launcherArgs = @()
if (-not $python) {
    foreach ($pyFlag in @("-3", "-3.14", "-3.13", "-3.12", "-3.11")) {
        $foundLauncher = Find-WorkingPython "py.exe" @($pyFlag)
        if ($foundLauncher) {
            $launcher = $foundLauncher
            $launcherArgs = @($pyFlag)
            break
        }
    }
}
if (-not $python -and -not $launcher) {
    Write-Error ("[!] 错误：未在 PATH 或 py.exe 启动器中找到可执行的 Python 3.11+（请安装 Python 3.11+ 并勾选 Add python.exe to PATH，或运行 py -0p 排查）。`r`n" +
        "[!] Error: Python 3.11+ executable not found in PATH or via py.exe launcher (install Python 3.11+ with 'Add python.exe to PATH' or check 'py -0p').") -ErrorAction Continue
    exit 1
}

function Quote-WindowsArg {
    param([AllowEmptyString()][string]$Value)
    if ($Value.Length -eq 0) {
        return '""'
    }
    if ($Value -notmatch '[\s"]') {
        return $Value
    }
    $escaped = [regex]::Replace($Value, '(\\*)"', '$1$1\"')
    $escaped = [regex]::Replace($escaped, '(\\+)$', '$1$1')
    return '"' + $escaped + '"'
}

# 常规交互模式直接继承控制台标准流（保留无换行提示即时输出并兼容 2>&1 零误判）；只读 JSON 模式重定向标准输出以支持管道捕获。
$exePath = if ($python) { $python } else { $launcher }
$fullArgs = @($launcherArgs) + @('-B', '-X', 'utf8', "$PSScriptRoot\install.py") + @($args)
$captureJsonStdout = (@($args) -contains '--probe-apps') -or (@($args) -contains '--audit-upgrade')
$installerExitCode = 1
$proc = [System.Diagnostics.Process]::new()
try {
    $proc.StartInfo.FileName = $exePath
    $proc.StartInfo.Arguments = ($fullArgs | ForEach-Object { Quote-WindowsArg ([string]$_) }) -join ' '
    $proc.StartInfo.UseShellExecute = $false
    if ($captureJsonStdout) {
        $proc.StartInfo.RedirectStandardOutput = $true
        $proc.StartInfo.StandardOutputEncoding = [System.Text.UTF8Encoding]::new($false)
    }
    $null = $proc.Start()
    if ($captureJsonStdout) {
        $stdoutText = $proc.StandardOutput.ReadToEnd()
        $proc.WaitForExit()
        if ($stdoutText.Length -gt 0) { Write-Output $stdoutText.TrimEnd("`r", "`n") }
    } else {
        $proc.WaitForExit()
    }
    $installerExitCode = $proc.ExitCode
} catch [System.ComponentModel.Win32Exception] {
    Write-Error "[!] 无法启动 Python 安装器。$($_.Exception.Message)" -ErrorAction Continue
    $installerExitCode = 1
} catch [System.InvalidOperationException] {
    Write-Error "[!] 无法启动 Python 安装器。$($_.Exception.Message)" -ErrorAction Continue
    $installerExitCode = 1
} finally {
    $proc.Dispose()
}
exit $installerExitCode
