[CmdletBinding(SupportsShouldProcess)]
param(
    [string]$Vault = (Join-Path $HOME 'Documents\Obsidian Vault'),
    [string]$Python = '',
    [switch]$SkipDependencies,
    [switch]$InstallAsr
)

$ErrorActionPreference = 'Stop'
$sourceRoot = (Resolve-Path (Join-Path $PSScriptRoot '..')).Path
$pluginSource = Join-Path $sourceRoot 'obsidian-plugin'
$pluginId = 'youtube-note-reader'
$pluginTarget = Join-Path $Vault ".obsidian\plugins\$pluginId"
$communityPluginsPath = Join-Path $Vault '.obsidian\community-plugins.json'
$extensionRoot = Join-Path $sourceRoot 'extension'
$legacyRoot = Join-Path $env:LOCALAPPDATA 'YouTubeNoteReader'
$legacyHost = Join-Path $legacyRoot 'youtube_reader_host.py'
$legacyExe = Join-Path $legacyRoot 'youtube-reader-host.exe'
$runPath = 'HKCU:\Software\Microsoft\Windows\CurrentVersion\Run'
$runName = 'YouTubeNoteReader'
$nativeHostPaths = @(
    'HKCU:\Software\Google\Chrome\NativeMessagingHosts\com.youtube_note_reader.host',
    'HKCU:\Software\Microsoft\Edge\NativeMessagingHosts\com.youtube_note_reader.host'
)
$pluginFiles = @('main.js', 'manifest.json', 'styles.css')
$pluginBuildReady = Test-Path -LiteralPath (Join-Path $pluginSource 'main.js') -PathType Leaf

function Get-NormalizedPath([string]$Path) {
    return [IO.Path]::GetFullPath($Path).TrimEnd('\', '/')
}

function Assert-ExactRemovalPath([string]$Path, [string]$Expected) {
    $actualPath = Get-NormalizedPath $Path
    $expectedPath = Get-NormalizedPath $Expected
    if (-not [string]::Equals($actualPath, $expectedPath, [StringComparison]::OrdinalIgnoreCase)) {
        throw "Refusing to remove unexpected path: $actualPath"
    }
    if (Test-Path -LiteralPath $actualPath) {
        $item = Get-Item -LiteralPath $actualPath -Force
        if (($item.Attributes -band [IO.FileAttributes]::ReparsePoint) -ne 0) {
            throw "Refusing to recursively remove a reparse point: $actualPath"
        }
    }
}

function Remove-LegacyReader {
    $processes = Get-CimInstance Win32_Process -ErrorAction SilentlyContinue | Where-Object {
        ($_.CommandLine -and $_.CommandLine.IndexOf($legacyHost, [StringComparison]::OrdinalIgnoreCase) -ge 0) -or
        ($_.ExecutablePath -and [string]::Equals((Get-NormalizedPath $_.ExecutablePath), (Get-NormalizedPath $legacyExe), [StringComparison]::OrdinalIgnoreCase))
    }
    foreach ($process in $processes) {
        if ($PSCmdlet.ShouldProcess("PID $($process.ProcessId)", 'Stop legacy YouTube Reader process')) {
            Stop-Process -Id $process.ProcessId -Force -ErrorAction SilentlyContinue
            Wait-Process -Id $process.ProcessId -Timeout 10 -ErrorAction SilentlyContinue
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
        Assert-ExactRemovalPath $legacyRoot (Join-Path $env:LOCALAPPDATA 'YouTubeNoteReader')
        if ($PSCmdlet.ShouldProcess($legacyRoot, 'Remove legacy YouTube Reader runtime')) {
            Remove-Item -LiteralPath $legacyRoot -Recurse -Force
        }
    }
}

if (-not (Test-Path -LiteralPath $Vault -PathType Container)) {
    throw "Obsidian Vault does not exist: $Vault"
}
if (-not (Test-Path -LiteralPath (Join-Path $Vault '.obsidian') -PathType Container)) {
    throw "The selected folder is not an Obsidian Vault: $Vault"
}
if (-not (Test-Path -LiteralPath (Join-Path $pluginSource 'package.json') -PathType Leaf)) {
    throw "OBSIDIAN_PLUGIN_SOURCE_MISSING: Complete obsidian-plugin before running install.ps1: $pluginSource"
}
if ($SkipDependencies -and $InstallAsr) {
    throw 'SkipDependencies and InstallAsr cannot be used together.'
}
if (-not $Python) {
    $Python = (Get-Command python -ErrorAction Stop).Source
}
$ffmpeg = (Get-Command ffmpeg -ErrorAction Stop).Source
$node = (Get-Command node -ErrorAction Stop).Source
foreach ($path in @($Python, $ffmpeg, $node)) {
    if (-not (Test-Path -LiteralPath $path -PathType Leaf)) { throw "Required executable is missing: $path" }
}
$npm = $null
if (-not $pluginBuildReady) {
    $npm = (Get-Command npm -ErrorAction Stop).Source
    if (-not (Test-Path -LiteralPath $npm -PathType Leaf)) { throw "Required executable is missing: $npm" }
}

if ($WhatIfPreference) {
    & (Join-Path $PSScriptRoot 'sync_local.ps1') -Vault $Vault -WhatIf
} else {
    & (Join-Path $PSScriptRoot 'sync_local.ps1') -Vault $Vault
}

if (-not $SkipDependencies -and $PSCmdlet.ShouldProcess('Python environment', 'Install youtube-transcript dependencies')) {
    & $Python -m pip install -r (Join-Path $sourceRoot 'requirements.txt')
    if ($LASTEXITCODE -ne 0) { throw 'Python dependency installation failed.' }
    if ($InstallAsr) {
        & $Python -m pip install -r (Join-Path $sourceRoot 'requirements-asr.txt')
        if ($LASTEXITCODE -ne 0) { throw 'Optional ASR dependency installation failed.' }
    }
}

if (-not $pluginBuildReady -and $PSCmdlet.ShouldProcess($pluginSource, 'Install npm dependencies and build Obsidian plugin')) {
    Push-Location $pluginSource
    try {
        if (-not $SkipDependencies) {
            if (Test-Path -LiteralPath (Join-Path $pluginSource 'package-lock.json') -PathType Leaf) {
                & $npm ci
            } else {
                & $npm install
            }
            if ($LASTEXITCODE -ne 0) { throw 'Obsidian plugin dependency installation failed.' }
        }
        & $npm run build
        if ($LASTEXITCODE -ne 0) { throw 'Obsidian plugin build failed.' }
    } finally {
        Pop-Location
    }
}

if (-not $WhatIfPreference) {
    foreach ($name in $pluginFiles) {
        $path = Join-Path $pluginSource $name
        if (-not (Test-Path -LiteralPath $path -PathType Leaf)) { throw "Missing Obsidian plugin build output: $path" }
    }
    $expectedVersion = (Get-Content -Raw -Encoding UTF8 -LiteralPath (Join-Path $sourceRoot 'VERSION')).Trim()
    $pluginManifest = Get-Content -Raw -Encoding UTF8 -LiteralPath (Join-Path $pluginSource 'manifest.json') | ConvertFrom-Json
    if ($pluginManifest.id -ne $pluginId) { throw "Unexpected Obsidian plugin id: $($pluginManifest.id)" }
    if ($pluginManifest.version -ne $expectedVersion) {
        throw "Obsidian plugin version mismatch: $($pluginManifest.version) != $expectedVersion"
    }
}

if ($PSCmdlet.ShouldProcess($pluginTarget, 'Install Obsidian plugin build')) {
    New-Item -ItemType Directory -Force -Path $pluginTarget | Out-Null
    foreach ($name in $pluginFiles) {
        Copy-Item -LiteralPath (Join-Path $pluginSource $name) -Destination (Join-Path $pluginTarget $name) -Force
    }
}

if ($PSCmdlet.ShouldProcess($communityPluginsPath, 'Enable YouTube Note Reader in Obsidian')) {
    $enabledPlugins = @()
    if (Test-Path -LiteralPath $communityPluginsPath -PathType Leaf) {
        $parsedPlugins = Get-Content -Raw -Encoding UTF8 -LiteralPath $communityPluginsPath | ConvertFrom-Json
        $enabledPlugins = @($parsedPlugins | ForEach-Object { [string]$_ })
    }
    if ($enabledPlugins -notcontains $pluginId) {
        $enabledPlugins += $pluginId
        $temporaryPluginsPath = "$communityPluginsPath.$PID.tmp"
        $json = ConvertTo-Json -InputObject @($enabledPlugins) -Compress
        [IO.File]::WriteAllText($temporaryPluginsPath, "$json`n", [Text.UTF8Encoding]::new($false))
        Move-Item -LiteralPath $temporaryPluginsPath -Destination $communityPluginsPath -Force
    }
}

# Remove the old runtime only after the replacement plugin has built and copied successfully.
Remove-LegacyReader

Write-Host ''
Write-Host 'YouTube Reader migration installation completed.'
Write-Host "Obsidian plugin: $pluginTarget"
Write-Host "Chrome extension: $extensionRoot"
Write-Host 'OpenCode was not installed or modified.'
if ($InstallAsr) { Write-Host 'Optional faster-whisper ASR dependencies were installed.' }
Write-Host 'YouTube Note Reader was enabled. Reload Obsidian once, then reload the Chrome extension.'
