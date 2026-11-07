# run_tests.ps1 -- every collector test in tests/, one command.
#
#   powershell -ExecutionPolicy Bypass -File collector/run_tests.ps1
#
# Tests live in tests/ and bootstrap their own import path, so they run from anywhere.
# Python is the `python` on PATH (a venv with collector/requirements.txt installed).

$here = Split-Path -Parent $MyInvocation.MyCommand.Path
Set-Location -LiteralPath $here
$testDir = Join-Path $here "tests"

$py = "python"
$pyArgs = @()

$tests = @(
    "test_roster.py",
    "test_scrape_queue.py",
    "test_scrape_run.py",
    "test_book_codec.py",
    "test_watchlist.py",
    "test_held_alert.py",
    "test_book_server.py"
)

$failed = @()
foreach ($t in $tests) {
    $path = Join-Path $testDir $t
    if (-not (Test-Path -LiteralPath $path)) {
        Write-Host ("  SKIP  {0}  (not found)" -f $t) -ForegroundColor DarkYellow
        continue
    }
    if ($t.EndsWith(".ps1")) {
        & powershell -NoProfile -ExecutionPolicy Bypass -File $path | Out-Null
    } else {
        & $py @pyArgs $path | Out-Null
    }
    if ($LASTEXITCODE -eq 0) {
        Write-Host ("  PASS  {0}" -f $t) -ForegroundColor Green
    } else {
        Write-Host ("  FAIL  {0}  (exit {1})" -f $t, $LASTEXITCODE) -ForegroundColor Red
        $failed += $t
    }
}

Write-Host ""
if ($failed.Count -gt 0) {
    Write-Host ("  {0} failed: {1}" -f $failed.Count, ($failed -join ", ")) -ForegroundColor Red
    Write-Host "  re-run one on its own to see why." -ForegroundColor DarkGray
    exit 1
}
Write-Host ("  all {0} test file(s) passed" -f $tests.Count) -ForegroundColor Green
