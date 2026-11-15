@echo off
title Hardware demo without the game
cd /d "%~dp0.."
py -3 f1_bridge.py --demo %*
pause
