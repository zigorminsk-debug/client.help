<#
 .SYNOPSIS
   Удаление клиента CSL Helpdesk у пользователя (User Configuration -> Scripts -> Logoff,
   либо запуск вручную). Удаляет ярлыки профиля и локальную копию EXE.
   Общий конфиг в ProgramData можно удалить отдельно (требует прав администратора).
#>
param(
    [string]$LocalExeDir   = "$env:LOCALAPPDATA\CSL\HelpdeskClient",
    [string]$ProgramDataDir = "$env:ProgramData\CSL\HelpdeskClient",
    [string]$ShortcutName   = "CSL Helpdesk.lnk"
)
$ErrorActionPreference = "SilentlyContinue"
$desktop   = [Environment]::GetFolderPath("Desktop")
$startMenu = Join-Path ([Environment]::GetFolderPath("StartMenu")) "Programs\CSL"

foreach ($dir in @($desktop, $startMenu)) {
    $lnk = Join-Path $dir $ShortcutName
    if (Test-Path $lnk) { Remove-Item $lnk -Force }
}
if (Test-Path $LocalExeDir) { Remove-Item $LocalExeDir -Recurse -Force }
if (Test-Path $ProgramDataDir) { Remove-Item $ProgramDataDir -Recurse -Force }

Write-Host "CSL Helpdesk client removed for user $env:USERNAME"
