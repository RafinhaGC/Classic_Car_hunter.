@echo off
chcp 65001 >nul
title Classic Car Hunter - Link Publico para Rafael e Marcal
cd /d "%~dp0"

echo ===============================================================================
echo   CLASSIC CAR HUNTER - MODO ONLINE COMPARTILHADO (RAFAEL ^& MARCAL)
echo ===============================================================================
echo.
echo [1/2] Iniciando o servidor local na porta 8080...
start "Classic Car Hunter API" /min cmd /c ".venv\Scripts\python.exe -m uvicorn app.main:app --host 127.0.0.1 --port 8080"
timeout /t 3 >nul

echo [2/2] Gerando link HTTPS publico gratuito via Cloudflare Tunnel...
echo       Copie o link terminado em ".trycloudflare.com" que aparecera abaixo
echo       e envie no WhatsApp do Marcal Borborema!
echo.

where cloudflared >nul 2>nul
if %errorlevel% neq 0 (
    echo Baixando cliente oficial Cloudflare Tunnel ^(cloudflared.exe^)...
    powershell -Command "Invoke-WebRequest -Uri 'https://github.com/cloudflare/cloudflared/releases/latest/download/cloudflared-windows-amd64.exe' -OutFile 'cloudflared.exe'"
)

if exist cloudflared.exe (
    cloudflared.exe tunnel --url http://127.0.0.1:8080
) else (
    cloudflared tunnel --url http://127.0.0.1:8080
)
pause
