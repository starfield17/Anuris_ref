@echo off
setlocal

set "SCRIPT_DIR=%~dp0"
set "PY_SCRIPT=%SCRIPT_DIR%switch_source_en.py"

if "%~1"=="" goto :usage

set "ACTION=%~1"
set "ARG1=%~2"

if /i "%ACTION%"=="show" (
  python "%PY_SCRIPT%" --show
  exit /b %errorlevel%
)

if /i "%ACTION%"=="pip" (
  if "%ARG1%"=="" goto :usage
  python "%PY_SCRIPT%" --pip "%ARG1%"
  exit /b %errorlevel%
)

if /i "%ACTION%"=="conda" (
  if "%ARG1%"=="" goto :usage
  python "%PY_SCRIPT%" --conda "%ARG1%"
  exit /b %errorlevel%
)

:usage
echo Usage:
echo   switch_source_cli.bat show
echo   switch_source_cli.bat pip ^<tsinghua^|ustc^|aliyun^|tencent^|douban^|default^>
echo   switch_source_cli.bat conda ^<tsinghua^|ustc^|default^>
exit /b 2
