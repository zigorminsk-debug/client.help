<#
 .SYNOPSIS
   Локальная сборка единого EXE на машине администратора (Windows).
   Запуск из корня репозитория:  powershell -File deploy\build-exe.ps1
#>
$ErrorActionPreference = "Stop"

Write-Host ">>> Установка зависимостей..."
python -m pip install --upgrade pip
python -m pip install -r requirements.txt pyinstaller

Write-Host ">>> Сборка EXE (PyInstaller)..."
pyinstaller --onefile --windowed --name CSLHelpdesk `
    --icon=src/app.ico --add-data "src/app.ico;." `
    --collect-all _sounddevice_data `
    --hidden-import=system_info `
    --hidden-import=jira_client `
    --hidden-import=screenshot `
    --hidden-import=audio_recorder `
    --hidden-import=config `
    src/helpdesk_client.py

Write-Host ">>> Готово: dist\CSLHelpdesk.exe"
