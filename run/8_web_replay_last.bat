@echo off
title Web dashboard replay of the last recording
cd /d "%~dp0.."
py -3 f1_web.py --replay --open %*
pause
