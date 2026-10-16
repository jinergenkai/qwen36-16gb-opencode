# Download models + fixed chat template into this folder.
#   .\download-all.ps1                 # Q4 uncensored + template
#   .\download-all.ps1 -All            # + original Q4 (baseline), mmproj (vision), Q5 uncensored
#   .\download-all.ps1 -All -Shutdown  # shut down the PC when done (cancel: shutdown /a)
# Requires: pip install -U huggingface_hub   (provides the `hf` CLI)
param([switch]$All, [switch]$Shutdown)
$ErrorActionPreference = 'Continue'
$root = $PSScriptRoot
$log  = "$root\download.log"

# Keep the PC awake while this process runs (does not change Windows power settings)
Add-Type -Namespace Win32 -Name Power -MemberDefinition '[DllImport("kernel32.dll")] public static extern uint SetThreadExecutionState(uint esFlags);'
[Win32.Power]::SetThreadExecutionState([uint32]'0x80000001') | Out-Null  # ES_CONTINUOUS | ES_SYSTEM_REQUIRED

function Log($msg) { $line = "[{0:HH:mm:ss}] {1}" -f (Get-Date), $msg; Write-Host $line; Add-Content $log $line -Encoding UTF8 }

$uncensored = 'HauhauCS/Qwen3.6-35B-A3B-Uncensored-HauhauCS-Aggressive'
$jobs = @(
    @{ repo = 'sudiptosarkar/Qwen-Fixed-Chat-Templates'; file = 'qwen3.6/chat_template.jinja'; dir = "$root\templates" },
    @{ repo = $uncensored; file = 'Qwen3.6-35B-A3B-Uncensored-HauhauCS-Aggressive-Q4_K_P.gguf'; dir = "$root\models\uncensored" }
)
if ($All) {
    $jobs += @(
        @{ repo = 'unsloth/Qwen3.6-35B-A3B-GGUF'; file = 'Qwen3.6-35B-A3B-UD-Q4_K_XL.gguf'; dir = "$root\models\original" },
        @{ repo = $uncensored; file = 'mmproj-Qwen3.6-35B-A3B-Uncensored-HauhauCS-Aggressive-f16.gguf'; dir = "$root\models\uncensored" },
        @{ repo = $uncensored; file = 'Qwen3.6-35B-A3B-Uncensored-HauhauCS-Aggressive-Q5_K_P.gguf'; dir = "$root\models\uncensored" }
    )
}

Log "=== start ==="
$failed = @()
foreach ($j in $jobs) {
    $ok = $false
    for ($try = 1; $try -le 5 -and -not $ok; $try++) {
        Log "download $($j.file) (try $try)"
        & hf download $j.repo $j.file --local-dir $j.dir
        $ok = ($LASTEXITCODE -eq 0) -and (Test-Path (Join-Path $j.dir $j.file))
        if (-not $ok) { Log "  failed, retry in 60s"; Start-Sleep 60 }
    }
    if ($ok) { Log "  OK: $($j.file)" } else { Log "  FAILED: $($j.file)"; $failed += $j.file }
}

if ($failed.Count -eq 0) { Log "=== all done ===" } else { Log "=== done with errors: $($failed -join ', ') ===" }
if ($Shutdown) {
    Log "shutting down in 5 minutes (cancel: shutdown /a)"
    shutdown /s /t 300 /c "Model download finished. Cancel with: shutdown /a"
}
