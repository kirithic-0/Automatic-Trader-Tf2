@echo off
rem One command for the live agent, from any folder (cmd or PowerShell):
rem   agent status          running? mode, kill switch, holdings, the last cycle
rem   agent start           the agent in AUTO mode, in its own window (run-auto.cmd)
rem   agent start manual    the agent asking for each trade, in its own window (run.cmd)
rem   agent stop            kill switch ON: nothing is sent (the agent keeps running)
rem   agent go              kill switch off: sending allowed again
rem   agent resume          after the win-rate stop: count only sales from now on
rem   agent report [--all]  the results so far (--all: every closed trade)
rem   agent trades          every trade: held (with the bid now), sold, never filled
rem   agent dashboard       the charts and every trade as a page, opened in the browser
rem   agent history <item>  one item's top buy order + lowest sell over time, and a chart (--strange, --days N)
rem   agent log [n]         follow the log live, from its last n lines (30)
rem   agent confirm         pending Steam confirmations, accept / cancel by hand
rem   agent code            the Steam Guard login code
rem   agent limits          open limits.json (the agent re-reads it every cycle)
rem   agent set <k> <v>     set one limit, checked first (e.g. agent set max_keys_in_play 3)
rem   agent restart         restart the agent after its cycle (new code, same window and mode)
rem   agent shutdown        stop the agent after its cycle
rem   agent cycle           run the next cycle now instead of waiting
rem   agent sell <item|id>  sell that held item into the best tradable bid at the next cycle
rem   agent tp <item|id> <pct>     its take-profit, percent over the price paid
rem   agent horizon <item|id> <h>  sell it from h hours from now on (reset = bought + 48 h)
rem   agent panel           the live control panel in the browser (engine panel)
setlocal
set "A=%~dp0"
set "N=node --no-warnings"
set "CMD=%~1"
if "%CMD%"=="" set "CMD=help"

if /i "%CMD%"=="status"  ( %N% "%A%status.js" & goto :eof )
if /i "%CMD%"=="stop"    ( %N% "%A%stop.js" & goto :eof )
if /i "%CMD%"=="go"      ( %N% "%A%stop.js" off & goto :eof )
if /i "%CMD%"=="resume"  ( %N% "%A%stop.js" resume & goto :eof )
if /i "%CMD%"=="report"  ( %N% "%A%report.js" %2 & goto :eof )
if /i "%CMD%"=="trades"  ( %N% "%A%trades.js" & goto :eof )
if /i "%CMD%"=="dashboard" ( %N% "%A%dashboard.js" & goto :eof )
if /i "%CMD%"=="confirm" ( %N% "%A%confirm.js" & goto :eof )
if /i "%CMD%"=="code"    ( %N% "%A%code.js" & goto :eof )
if /i "%CMD%"=="limits"  ( start "" notepad "%A%limits.json" & goto :eof )
if /i "%CMD%"=="set"     ( %N% "%A%set.js" %2 %3 & goto :eof )
if /i "%CMD%"=="restart" ( %N% "%A%control.js" restart & goto :eof )
if /i "%CMD%"=="shutdown" ( %N% "%A%control.js" shutdown & goto :eof )
if /i "%CMD%"=="cycle"   ( %N% "%A%control.js" cycle & goto :eof )
if /i "%CMD%"=="sell"    ( %N% "%A%position.js" %2 sell-now & goto :eof )
if /i "%CMD%"=="tp"      ( %N% "%A%position.js" %2 tp %3 & goto :eof )
if /i "%CMD%"=="horizon" ( %N% "%A%position.js" %2 horizon %3 & goto :eof )
if /i "%CMD%"=="panel"   ( start "TF2 panel" /d "%A%..\backpack" python -W ignore -m engine panel --open & goto :eof )
if /i "%CMD%"=="log" goto :log
if /i "%CMD%"=="history" goto :history
if /i "%CMD%"=="start" (
	if /i "%~2"=="manual" ( start "TF2 agent" "%A%run.cmd" ) else ( start "TF2 agent (AUTO)" "%A%run-auto.cmd" )
	goto :eof
)

for /f "tokens=1,* delims= " %%a in ('findstr /b /c:"rem   agent" "%~f0"') do echo %%b
goto :eof

:log
set "T=%~2"
if not defined T set "T=30"
powershell -NoProfile -Command "Get-Content -LiteralPath '%A%..\backpack\logs\agent.log' -Wait -Tail %T%"
goto :eof

:history
set "ARGS=%*"
set "ARGS=%ARGS:*history=%"
%N% "%A%history.js" %ARGS%
