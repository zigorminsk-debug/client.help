<#
 .SYNOPSIS
   Развёртывание клиента CSL Helpdesk через групповые политики (GPO).
   Назначение: User Configuration -> Windows Settings -> Scripts -> Logon.

 .DESCRIPTION
   Скрипт выполняется от имени пользователя при входе в систему и:
   1) копирует административный конфиг (общий логин/пароль Jira + настройки
      автообновления) в C:\ProgramData\CSL\HelpdeskClient (читается приложением,
      недоступен для записи пользователю);
   2) копирует EXE в %LOCALAPPDATA%\CSL\HelpdeskClient — профиль пользователя,
      чтобы ОН САМ мог обновляться из GitHub без прав администратора;
   3) создаёт ярлык в Рабочем столе и меню Пуск пользователя.

   Общие учётные данные Jira вводит администратор один раз — они лежат в
   config.json на сетевом ресурсе и копируются на каждый ПК.

 .NOTES
   Почему per-user (LocalAppData), а не ProgramData+Public Desktop?
   Чтобы встроенное автообновление из GitHub могло заменить EXE без запроса
   прав администратора (ProgramData пользователь не может перезаписать).
   Базовую версию всё равно раскатывает GPO; GitHub даёт оперативные обновления
   между входами в систему.
#>

param(
    [string]$SourceShare   = "\\YOURDOMAIN\sysvol\YOURDOMAIN\Policies\{Helpdesk-GPO}\Helpdesk",
    [string]$LocalExeDir   = "$env:LOCALAPPDATA\CSL\HelpdeskClient",
    [string]$ProgramDataDir = "$env:ProgramData\CSL\HelpdeskClient",
    [string]$ExeName       = "CSLHelpdesk.exe",
    [string]$ConfigName    = "config.json",
    [string]$ShortcutName  = "CSL Helpdesk.lnk"
)

$ErrorActionPreference = "SilentlyContinue"

# 1) Административный конфиг (общий логин/пароль) -> ProgramData (read-only для user)
if (-not (Test-Path $ProgramDataDir)) { New-Item -ItemType Directory -Path $ProgramDataDir -Force | Out-Null }
$srcCfg = Join-Path $SourceShare $ConfigName
$dstCfg = Join-Path $ProgramDataDir $ConfigName
if (Test-Path $srcCfg) { Copy-Item $srcCfg $dstCfg -Force }

# 2) EXE -> LocalAppData пользователя (обновляется самим приложением)
if (-not (Test-Path $LocalExeDir)) { New-Item -ItemType Directory -Path $LocalExeDir -Force | Out-Null }
$srcExe = Join-Path $SourceShare $ExeName
$dstExe = Join-Path $LocalExeDir $ExeName
if (Test-Path $srcExe) {
    $copy = $true
    if (Test-Path $dstExe) {
        if ((Get-Item $srcExe).Length -eq (Get-Item $dstExe).Length) { $copy = $false }
    }
    if ($copy) { Copy-Item $srcExe $dstExe -Force }
}

# 3) Ярлык в профиле пользователя (Рабочий стол + меню Пуск)
$desktop   = [Environment]::GetFolderPath("Desktop")
$startMenu = Join-Path ([Environment]::GetFolderPath("StartMenu")) "Programs\CSL"
if (-not (Test-Path $startMenu)) { New-Item -ItemType Directory -Path $startMenu -Force | Out-Null }

foreach ($dir in @($desktop, $startMenu)) {
    $lnk = Join-Path $dir $ShortcutName
    $ws = New-Object -ComObject WScript.Shell
    $l  = $ws.CreateShortcut($lnk)
    $l.TargetPath       = $dstExe
    $l.WorkingDirectory = $LocalExeDir
    $l.Description       = "Отправить заявку в техподдержку CSL"
    $l.Save()
}

Write-Host "CSL Helpdesk client deployed for user $env:USERNAME"
