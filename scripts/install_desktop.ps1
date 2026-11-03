[CmdletBinding()]
param(
    [Parameter(Mandatory=$true)][string]$Profile,
    [string]$Executable,
    [string]$Desktop = [Environment]::GetFolderPath('Desktop')
)
$ErrorActionPreference = 'Stop'
$project = Split-Path -Parent $PSScriptRoot
if (-not $Executable) {
    $Executable = Join-Path $project 'Virtuoso-LAN-Controller.exe'
    if (-not (Test-Path -LiteralPath $Executable)) {
        $Executable = Join-Path $project 'dist\Virtuoso-LAN-Controller\Virtuoso-LAN-Controller.exe'
    }
}
$exePath = (Resolve-Path -LiteralPath $Executable).Path
$profilePath = (Resolve-Path -LiteralPath $Profile).Path
$desktopPath = (Resolve-Path -LiteralPath $Desktop).Path
$settings = Get-Content -Raw -LiteralPath $profilePath -Encoding UTF8 | ConvertFrom-Json
$statePath = $settings.controller_state
if (-not [IO.Path]::IsPathRooted($statePath)) {
    $statePath = Join-Path (Split-Path -Parent $profilePath) $statePath
}
$statePath = (Resolve-Path -LiteralPath $statePath).Path
foreach ($value in @($exePath, $profilePath, $statePath)) {
    if ($value.Contains('"') -or $value.Contains("`r") -or $value.Contains("`n")) {
        throw 'Unsupported quote or newline in path'
    }
}
$shell = New-Object -ComObject WScript.Shell
$items = @(
    @{Name='Virtuoso 双机联动仿真'; Arguments=('quickstart --profile "{0}" --open-report' -f $profilePath); Description='检查全部节点，运行保存的仿真清单并取回结果 / Run saved multi-node jobs'},
    @{Name='Virtuoso 连接检查'; Arguments=('quickstart --profile "{0}" --check-only --open-report' -f $profilePath); Description='只检查连接，不运行仿真 / Check connections only'},
    @{Name='Virtuoso 仿真控制台'; Arguments=('--state "{0}" gui' -f $statePath); Description='打开已配对节点的图形控制台 / Open paired controller'}
)
foreach ($item in $items) {
    $destination = Join-Path $desktopPath ($item.Name + '.lnk')
    if (Test-Path -LiteralPath $destination) {
        Copy-Item -LiteralPath $destination -Destination ($destination + '.backup-' + [Guid]::NewGuid().ToString('N'))
    }
    $link = $shell.CreateShortcut($destination)
    $link.TargetPath = $exePath
    $link.Arguments = $item.Arguments
    $link.WorkingDirectory = Split-Path -Parent $exePath
    $link.Description = $item.Description
    $link.IconLocation = $exePath + ',0'
    $link.Save()
    Write-Output $destination
}
