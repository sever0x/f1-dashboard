@echo off
title Web dashboard test race, no game
cd /d "%~dp0.."
py -3 f1_web.py --demo --open %*
pause
