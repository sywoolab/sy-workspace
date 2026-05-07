# 가민 동기화 윈도우 PowerShell 래퍼
# macOS 등가: workout/scripts/run_garmin_sync.sh
# 두 OS의 git sync 정책은 동일하게 유지한다 (L0 §크로스플랫폼 동일성)
#
# 수동 실행: powershell -ExecutionPolicy Bypass -File workout\scripts\run_garmin_sync.ps1
# 자동 실행: Task Scheduler 등록 (매시 정각 권장)

$ErrorActionPreference = 'Continue'

# 레포 루트로 이동 (스크립트 위치 기준 ../..)
$RepoRoot = Resolve-Path (Join-Path $PSScriptRoot '..\..')
Set-Location $RepoRoot

# .env 로드 (Python load_dotenv가 메인. 여기서는 BOT_TOKEN 매핑용 최소 로드)
$EnvFile = Join-Path $RepoRoot '.env'
if (Test-Path $EnvFile) {
    Get-Content $EnvFile | ForEach-Object {
        if ($_ -match '^\s*([^#=]+?)\s*=\s*(.*?)\s*$') {
            $key = $matches[1]
            $val = $matches[2].Trim('"').Trim("'")
            [Environment]::SetEnvironmentVariable($key, $val, 'Process')
        }
    }
}
# BOT_TOKEN fallback 체인 (운동 봇 표준 — L0 §환경변수 부트스트랩)
if (-not $env:BOT_TOKEN) { $env:BOT_TOKEN = $env:TRAINING_BOT_TOKEN }
if (-not $env:BOT_TOKEN) { $env:BOT_TOKEN = $env:TELEGRAM_BOT_TOKEN }
if (-not $env:CHAT_ID)   { $env:CHAT_ID   = $env:TELEGRAM_CHAT_ID }

$LogDir = Join-Path $RepoRoot 'data\logs'
if (-not (Test-Path $LogDir)) { New-Item -ItemType Directory -Path $LogDir -Force | Out-Null }
$LogFile = Join-Path $LogDir ("garmin_sync_{0}.log" -f (Get-Date -Format 'yyyyMMdd'))

function Log($msg) { Add-Content -Path $LogFile -Value $msg -Encoding UTF8 }

Log "[$(Get-Date -Format 'yyyy-MM-dd HH:mm:ss')] 로컬 가민 동기화 시작"

# === [BOOT] origin 변경 항상 먼저 흡수 (분기 누적 차단) ===
& git fetch origin 2>&1 | Tee-Object -FilePath $LogFile -Append | Out-Null
$dirty = $false
& git diff --quiet 2>$null;        if ($LASTEXITCODE -ne 0) { $dirty = $true }
& git diff --cached --quiet 2>$null; if ($LASTEXITCODE -ne 0) { $dirty = $true }
if (-not $dirty) {
    & git pull --ff-only 2>&1 | Tee-Object -FilePath $LogFile -Append | Out-Null
    if ($LASTEXITCODE -ne 0) { Log "[INFO] fast-forward 불가 — commit 단계에서 rebase 시도" }
}

# === [SYNC] ===
$Python = if (Get-Command py -ErrorAction SilentlyContinue) { 'py -3' } else { 'python' }
& cmd /c "$Python workout\scripts\garmin_sync.py sync >> `"$LogFile`" 2>&1"
$ExitCode = $LASTEXITCODE

# === [PUSH] ===
if ($ExitCode -eq 0) {
    & git add workout/workout_log.json workout/data/garmin_health.json workout/data/sync_state.json 2>$null
    & git diff --staged --quiet 2>$null
    if ($LASTEXITCODE -ne 0) {
        & git commit -m "garmin sync: local auto update" 2>&1 | Tee-Object -FilePath $LogFile -Append | Out-Null
        & git pull --rebase --autostash 2>&1 | Tee-Object -FilePath $LogFile -Append | Out-Null
        if ($LASTEXITCODE -ne 0) {
            & git rebase --abort 2>$null
            Log "[ERROR] pull --rebase 충돌 — 자동 reset 금지. 수동 개입 필요. 로컬 commit 보존"
        } else {
            & git push 2>&1 | Tee-Object -FilePath $LogFile -Append | Out-Null
            if ($LASTEXITCODE -ne 0) { Log "[WARN] git push 실패 — 다음 실행 시 재시도" }
        }
    }
}

Log "[$(Get-Date -Format 'yyyy-MM-dd HH:mm:ss')] 완료 (exit=$ExitCode)"

# 7일 이전 로그 정리
Get-ChildItem -Path $LogDir -Filter 'garmin_sync_*.log' |
    Where-Object { $_.LastWriteTime -lt (Get-Date).AddDays(-7) } |
    Remove-Item -Force -ErrorAction SilentlyContinue

exit $ExitCode
