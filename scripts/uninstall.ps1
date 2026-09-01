[CmdletBinding(SupportsShouldProcess)]
param(
    [string]$Vault = (Join-Path $HOME 'Documents\Obsidian Vault')
)

$ErrorActionPreference = 'Stop'
$pluginId = 'youtube-note-reader'
$pluginsRoot = Join-Path $Vault '.obsidian\plugins'
$pluginTarget = Join-Path $pluginsRoot $pluginId
$legacyRoot = Join-Path $env:LOCALAPPDATA 'YouTubeNoteReader'
$legacyHost = Join-Path $legacyRoot 'youtube_reader_host.py'
$legacyExe = Join-Path $legacyRoot 'youtube-reader-host.exe'
$runPath = 'HKCU:\Software\Microsoft\Windows\CurrentVersion\Run'
$runName = 'YouTubeNoteReader'
$nativeHostPaths = @(
    'HKCU:\Software\Google\Chrome\NativeMessagingHosts\com.youtube_note_reader.host',
    'HKCU:\Software\Microsoft\Edge\NativeMessagingHosts\com.youtube_note_reader.host'
)

function Get-NormalizedPath([string]$Path) {
    return [IO.Path]::GetFullPath($Path).TrimEnd('\', '/')
}

function Assert-SafeDirectory([string]$Path, [string]$Expected, [string]$Parent) {
    $actualPath = Get-NormalizedPath $Path
    $expectedPath = Get-NormalizedPath $Expected
    $parentPath = Get-NormalizedPath $Parent
    if (-not [string]::Equals($actualPath, $expectedPath, [StringComparison]::OrdinalIgnoreCase)) {
        throw "Refusing to remove unexpected path: $actualPath"
    }
    if (-not $actualPath.StartsWith($parentPath + [IO.Path]::DirectorySeparatorChar, [StringComparison]::OrdinalIgnoreCase)) {
        throw "Refusing to remove path outside expected parent: $actualPath"
    }
    if (Test-Path -LiteralPath $actualPath) {
        $item = Get-Item -LiteralPath $actualPath -Force
        if (($item.Attributes -band [IO.FileAttributes]::ReparsePoint) -ne 0) {
            throw "Refusing to recursively remove a reparse point: $actualPath"
        }
    }
}

$processes = Get-CimInstance Win32_Process -ErrorAction SilentlyContinue | Where-Object {
    ($_.CommandLine -and $_.CommandLine.IndexOf($legacyHost, [StringComparison]::OrdinalIgnoreCase) -ge 0) -or
    ($_.ExecutablePath -and [string]::Equals((Get-NormalizedPath $_.ExecutablePath), (Get-NormalizedPath $legacyExe), [StringComparison]::OrdinalIgnoreCase))
}
foreach ($process in $processes) {
    if ($PSCmdlet.ShouldProcess("PID $($process.ProcessId)", 'Stop legacy YouTube Reader process')) {
        Stop-Process -Id $process.ProcessId -Force -ErrorAction SilentlyContinue
    }
}

if (Get-ItemProperty -Path $runPath -Name $runName -ErrorAction SilentlyContinue) {
    if ($PSCmdlet.ShouldProcess("$runPath\$runName", 'Remove legacy startup registration')) {
        Remove-ItemProperty -Path $runPath -Name $runName -Force
    }
}
foreach ($registryPath in $nativeHostPaths) {
    if ((Test-Path -LiteralPath $registryPath) -and $PSCmdlet.ShouldProcess($registryPath, 'Remove legacy Native Messaging registration')) {
        Remove-Item -LiteralPath $registryPath -Recurse -Force
    }
}

if (Test-Path -LiteralPath $legacyRoot) {
    Assert-SafeDirectory $legacyRoot (Join-Path $env:LOCALAPPDATA 'YouTubeNoteReader') $env:LOCALAPPDATA
    if ($PSCmdlet.ShouldProcess($legacyRoot, 'Remove legacy YouTube Reader runtime')) {
        Remove-Item -LiteralPath $legacyRoot -Recurse -Force
    }
}
if (Test-Path -LiteralPath $pluginTarget) {
    Assert-SafeDirectory $pluginTarget (Join-Path $pluginsRoot $pluginId) $pluginsRoot
    if ($PSCmdlet.ShouldProcess($pluginTarget, 'Remove installed Obsidian plugin')) {
        Remove-Item -LiteralPath $pluginTarget -Recurse -Force
    }
}

Write-Host 'YouTube Note Reader Obsidian plugin and legacy desktop runtime were removed.'
Write-Host 'The Chrome extension can be removed manually from chrome://extensions.'
Write-Host 'Skill files, cookies, generated notes, images, and SRT files were preserved.'
