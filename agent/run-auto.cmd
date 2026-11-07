@echo off
rem The live agent in AUTO mode: no approvals, every trade inside limits.json goes out on its
rem own. Double-click to start, close this window to stop. Safe to stop any time: the next start reads every offer
rem that was out back from Steam before it sends anything (agent/run.js).
title TF2 agent (AUTO)
cd /d "%~dp0"
:run
node --no-warnings run.js --auto %*
rem 75 = a restart asked for between cycles (agent restart / the panel): start again here
if %errorlevel%==75 goto run
echo.
echo The agent stopped.
pause
