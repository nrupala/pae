@echo off
rem PAE launcher (Windows shim) — delegates to pae.ps1 beside this file.
rem Usage: pae {start|stop|restart|status|seed|logs|serve|version}
powershell -NoProfile -ExecutionPolicy Bypass -File "%~dp0pae.ps1" %*
exit /b %ERRORLEVEL%
