@echo off
setlocal
cd /d "%~dp0"
chcp 65001 >nul
title Sistema de Transformaciones Lineales LPF - C++17 y TUI

:: Detectar dinamicamente el comando de Python disponible en el dispositivo
set "PY_CMD="
py --version >nul 2>&1
if %errorlevel% equ 0 (
    set "PY_CMD=py"
) else (
    python --version >nul 2>&1
    if %errorlevel% equ 0 (
        set "PY_CMD=python"
    )
)

if "%PY_CMD%"=="" (
    echo [ERROR] No se encontro Python instalado en este dispositivo.
    pause
    exit /b 1
)

:: Si el binario C++ aun no existe en este dispositivo, compilarlo dinamicamente
if not exist "%~dp0bin\lpf_backend.exe" (
    echo [INFO] Compilando el motor C++17 por primera vez en este dispositivo...
    powershell -NoProfile -ExecutionPolicy Bypass -File "%~dp0compilar_cpp.ps1"
)

%PY_CMD% "%~dp0frontend_tui\app_tui.py" %*
endlocal
pause
