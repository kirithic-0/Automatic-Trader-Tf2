# run_tests.ps1 -- every engine test in backpack/tests/, one command. The collector's
# own tests are collector/run_tests.ps1; several of these import its writers
# (snapshot_store, bot_registry) from collector/.
#
# There is no test runner here and the suite is split across Python and
# PowerShell, which is exactly how a failing test gets committed: each file
# passes when you run it and nobody runs all of them.
#
#   powershell -ExecutionPolicy Bypass -File run_tests.ps1
#
# Tests live in tests/ and set their import path through tests/_paths.py, so they run from
# anywhere. Python is the `python` on PATH (a venv with requirements.txt installed).

$here = Split-Path -Parent $MyInvocation.MyCommand.Path
Set-Location -LiteralPath $here
$testDir = Join-Path $here "tests"

$py = "python"
$pyArgs = @()

$tests = @(
    "test_book_codec.py",
    "test_engine_book.py",
    "test_engine_pipeline.py",
    "test_bot_registry.py",
    "test_bot_groups.py",
    "test_bot_features.py",
    "test_forecast.py",
    "test_universe.py",
    "test_listings.py",
    "test_recheck.py",
    "test_exits.py",
    "test_replay.py",
    "test_untrusted.py",
    "test_sync.py",
    "test_collector_copies.py",
    "test_fetch_book.py",
    "test_history.py",
    "test_stats.py",
    "test_panel.py"
)

$failed = @()
$skipped = @()
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
    } elseif ($LASTEXITCODE -eq 77) {       # tests/_paths.py SKIP_EXIT: needs the collector
        Write-Host ("  SKIP  {0}  (needs the collector)" -f $t) -ForegroundColor DarkYellow
        $skipped += $t
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
if ($skipped.Count -gt 0) {
    Write-Host ("  {0} test file(s) passed, {1} skipped: {2}" -f ($tests.Count - $skipped.Count), $skipped.Count, ($skipped -join ", ")) -ForegroundColor Green
} else {
    Write-Host ("  all {0} test file(s) passed" -f $tests.Count) -ForegroundColor Green
}
