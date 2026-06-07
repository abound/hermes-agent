@echo off
chcp 65001 >nul
cd /d "%~dp0.."
set PYTHONUTF8=1
set PYTHONPATH=%CD%
echo.
echo [Debug run_agent.py] 监听 5678，请在 PyCharm 附加调试...
echo 断点建议: run_agent.py 的 main() / AIAgent.run_conversation()
echo.
"%~dp0..\venv\Scripts\python.exe" -m debugpy --listen 5678 --wait-for-client "%~dp0..\run_agent.py" --query=你好 --model=deepseek-v4-pro --max_turns=5 --enabled_toolsets=terminal,file
pause
