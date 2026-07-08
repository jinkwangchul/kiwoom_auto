@echo off
setlocal

if "%~1"=="" (
  echo Usage:
  echo   AI_ANALYZE.bat path\to\file.py
  echo   AI_ANALYZE.bat path\to\file.py --prompt call_flow
  echo   AI_ANALYZE.bat path\to\file.py --size 7b
  echo.
  echo Default model: qwen2.5-coder:3b
  echo Reports are saved under reports\ai_analysis\
  exit /b 1
)

python "%~dp0tools\ai_analyzer\ai_analyze.py" %*
exit /b %ERRORLEVEL%
