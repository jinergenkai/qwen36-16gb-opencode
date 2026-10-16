# Start llama-server for Qwen3.6-35B-A3B on a 16GB GPU + 32GB RAM box. Examples:
#   .\run-qwen.ps1 -Model q4               (uncensored Q4_K_P, 262K ctx)
#   .\run-qwen.ps1 -Model q5 -Ctx 131072
#   .\run-qwen.ps1 -Model q4 -Vision       (load mmproj for image input)
# Stop with Ctrl+C; VRAM/RAM are released when the process exits.
param(
    [ValidateSet('q4', 'q5', 'orig')] [string]$Model = 'q4',
    [int]$Ctx = 262144,
    [int]$FitTarget = 1024,   # MiB of VRAM left free for Windows. OOM -> raise to 1536
    [int]$CacheRam = -2,      # MiB of RAM for the prompt cache. Default: 8192, 6144 with -Vision (mmproj ~1GB), 4096 for q5
    [switch]$Vision,
    [switch]$Force            # stop a running llama-server and start this one instead
)

# Don't start a second server: two copies would fight over VRAM/RAM and port 8080.
$running = Get-Process llama-server -ErrorAction SilentlyContinue
if ($running) {
    $current = try { (Invoke-RestMethod http://127.0.0.1:8080/v1/models -TimeoutSec 3).data[0].id } catch { '(still loading)' }
    if (-not $Force) {
        Write-Host "llama-server is already running (PID $($running.Id -join ', '), model: $current)." -ForegroundColor Yellow
        Write-Host "Use it as is, stop it with Ctrl+C in its window, or rerun with -Force to replace it."
        exit 0
    }
    Write-Host "Stopping running llama-server ($current)..." -ForegroundColor Yellow
    $running | Stop-Process -Force
    Start-Sleep 3
}
if ($CacheRam -eq -2) { $CacheRam = if ($Model -eq 'q5') { 4096 } elseif ($Vision) { 6144 } else { 8192 } }

$root = $PSScriptRoot
$models = @{
    q4   = @{ file = "$root\models\uncensored\Qwen3.6-35B-A3B-Uncensored-HauhauCS-Aggressive-Q4_K_P.gguf"; alias = 'qwen36-uncensored-q4' }
    q5   = @{ file = "$root\models\uncensored\Qwen3.6-35B-A3B-Uncensored-HauhauCS-Aggressive-Q5_K_P.gguf"; alias = 'qwen36-uncensored-q5' }
    orig = @{ file = "$root\models\original\Qwen3.6-35B-A3B-UD-Q4_K_XL.gguf"; alias = 'qwen36-original-q4' }
}
$m = $models[$Model]

$srvArgs = @(
    '-m', $m.file, '--alias', $m.alias,
    '--jinja', '--chat-template-file', "$root\templates\qwen3.6\chat_template.jinja",
    '--chat-template-kwargs', '{\"preserve_thinking\":true}',   # PS 5.1 strips " when calling an exe, so escape them
    '-c', $Ctx, '--fit', 'on', '--fit-ctx', $Ctx, '--fit-target', $FitTarget,
    '-np', '1', '-fa', 'on',
    '--load-mode', 'none',    # no mmap: RAM holds only the CPU-side experts, not a second copy of GPU weights
    '--cache-ram', $CacheRam, # prompt cache lives in RAM and grows over a session; cap it
    '-ctk', 'q8_0', '-ctv', 'q8_0',
    '-b', '2048', '-ub', '2048',
    '--temp', '0.6', '--top-p', '0.95', '--top-k', '20', '--min-p', '0',
    '--presence-penalty', '0', '--repeat-penalty', '1.0',
    '--host', '127.0.0.1', '--port', '8080'
)
if ($Vision) { $srvArgs += @('--mmproj', "$root\models\uncensored\mmproj-Qwen3.6-35B-A3B-Uncensored-HauhauCS-Aggressive-f16.gguf") }
else { $srvArgs += '--no-mmproj' }

Write-Host "Model: $($m.alias)  ctx=$Ctx  vision=$Vision  cache-ram=$CacheRam" -ForegroundColor Cyan
& "$root\llama.cpp\llama-server.exe" @srvArgs
