@echo off
rem The live agent: double-click to start, close this window to stop. Every trade asks for
rem your approval in this window. Safe to stop any time: the next start reads every offer
rem that was out back from Steam before it sends anything (agent/run.js).
title TF2 agent
cd /d "%~dp0"
:run
node --no-warnings run.js %*
rem 75 = a restart asked for between cycles (agent restart / the panel): start again here
if %errorlevel%==75 goto run
echo.
echo The agent stopped.
pause
