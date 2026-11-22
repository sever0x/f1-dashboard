@echo off
title Race recording + web dashboard, no Nano
cd /d "%~dp0.."
py -3 f1_bridge.py --no-nano %*
pause
