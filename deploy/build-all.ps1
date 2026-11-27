<#
 .SYNOPSIS
   Локальная сборка ВСЕГО на машине администратора (Windows):
   1) EXE (PyInstaller)  2) подготовка config.json  3) MSI (WiX v4).

 .PARAMETER MsiVersion
   Версия MSI (по умолчанию 1.0.0). Для корректного MajorUpgrade используйте
   возрастающие номера, например 1.0.<номер_релиза>.

 .PARAMETER ConfigSource
   Путь к заполненному администратором config.json. Если не указан — берётся
   deploy\config.json (если есть), иначе шаблон deploy\admin-config.json.template
   (с плейсхолдерами; реальные учётные данные потом задаются через GPO/реестр).
#>
param(
    [string]$MsiVersion = "1.0.0",
    [string]$ConfigSource = ""
)

$ErrorActionPreference = "Stop"

# 1) EXE
Write-Host ">>> Сборка EXE (PyInstaller)..."
python -m pip install --upgrade pip
python -m pip install -r requirements.txt pyinstaller
pyinstaller --onefile --windowed --name CSLHelpdesk `
    --hidden-import=system_info --hidden-import=jira_client `
    --hidden-import=screenshot --hidden-import=audio_recorder `
    --hidden-import=config --hidden-import=updater --hidden-import=_version `
    src/helpdesk_client.py

# 2) config.json для MSI
if (-not $ConfigSource) {
    if (Test-Path deploy\config.json) { $ConfigSource = "deploy\config.json" }
    else { $ConfigSource = "deploy\admin-config.json.template" }
}
if ($ConfigSource -ne "deploy\config.json") {
    Copy-Item $ConfigSource deploy\config.json -Force
}
Write-Host ">>> Используется конфиг: $ConfigSource"

# 3) MSI (WiX v4)
Write-Host ">>> Установка/проверка WiX..."
dotnet tool install --global wix 2>$null
$env:PATH += ";$env:USERPROFILE\.dotnet\tools"
Write-Host ">>> Сборка MSI (версия $MsiVersion)..."
wix build wix/HelpdeskClient.wxs -dMsiVersion=$MsiVersion -o dist/CSLHelpdesk.msi

Write-Host ">>> Готово: dist\CSLHelpdesk.exe и dist\CSLHelpdesk.msi"
