[CmdletBinding()]
param(
    [string]$Vault = (Join-Path $HOME 'Documents\Obsidian Vault')
)

$ErrorActionPreference = 'Stop'
$sourceRoot = (Resolve-Path (Join-Path $PSScriptRoot '..')).Path
$expectedVersion = (Get-Content -Raw -Encoding UTF8 -LiteralPath (Join-Path $sourceRoot 'VERSION')).Trim()
$pluginId = 'youtube-note-reader'
$pluginSource = Join-Path $sourceRoot 'obsidian-plugin'
$pluginTarget = Join-Path $Vault ".obsidian\plugins\$pluginId"
$communityPluginsPath = Join-Path $Vault '.obsidian\community-plugins.json'
$pluginFiles = @('main.js', 'manifest.json', 'styles.css')
$skillSourceFiles = @(
    'VERSION',
    'SKILL.md',
    'requirements.txt',
    'requirements-asr.txt',
    'scripts\extract_transcript.py',
    'scripts\extract_frames.py',
    'scripts\validate_note.py',
    'scripts\video_common.py',
    'scripts\video_note.py',
    'scripts\deduplicate_transcript.py',
    'references\note-contract.md'
)
$vaultSkill = Join-Path $Vault '.obsidian\skills\youtube-transcript'
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

if (-not (Test-Path -LiteralPath $Vault -PathType Container)) { throw "Obsidian Vault does not exist: $Vault" }
foreach ($command in @('python', 'ffmpeg', 'node')) {
    if (-not (Get-Command $command -ErrorAction SilentlyContinue)) { throw "Required command is missing: $command" }
}

foreach ($relative in $skillSourceFiles) {
    $source = Join-Path $sourceRoot $relative
    $installed = Join-Path $vaultSkill $relative
    if (-not (Test-Path -LiteralPath $source -PathType Leaf)) { throw "Missing Skill source file: $source" }
    if (-not (Test-Path -LiteralPath $installed -PathType Leaf)) { throw "Missing installed Skill file: $installed" }
    $sourceHash = (Get-FileHash -Algorithm SHA256 -LiteralPath $source).Hash
    $installedHash = (Get-FileHash -Algorithm SHA256 -LiteralPath $installed).Hash
    if ($sourceHash -ne $installedHash) { throw "Installed Skill hash mismatch: $relative" }
}

if (-not (Test-Path -LiteralPath (Join-Path $pluginSource 'package.json') -PathType Leaf)) {
    throw "OBSIDIAN_PLUGIN_SOURCE_MISSING: $pluginSource"
}
foreach ($name in $pluginFiles) {
    $source = Join-Path $pluginSource $name
    $installed = Join-Path $pluginTarget $name
    if (-not (Test-Path -LiteralPath $source -PathType Leaf)) { throw "Missing plugin build output: $source" }
    if (-not (Test-Path -LiteralPath $installed -PathType Leaf)) { throw "Missing installed plugin file: $installed" }
    if ((Get-FileHash -Algorithm SHA256 -LiteralPath $source).Hash -ne (Get-FileHash -Algorithm SHA256 -LiteralPath $installed).Hash) {
        throw "Installed Obsidian plugin hash mismatch: $name"
    }
}

if (-not (Test-Path -LiteralPath $communityPluginsPath -PathType Leaf)) {
    throw "Obsidian community plugin list is missing: $communityPluginsPath"
}
$parsedPlugins = Get-Content -Raw -Encoding UTF8 -LiteralPath $communityPluginsPath | ConvertFrom-Json
$enabledPlugins = @($parsedPlugins | ForEach-Object { [string]$_ })
if ($enabledPlugins -notcontains $pluginId) { throw 'YouTube Note Reader is installed but not enabled in Obsidian.' }

$sourceManifest = Get-Content -Raw -Encoding UTF8 -LiteralPath (Join-Path $pluginSource 'manifest.json') | ConvertFrom-Json
$installedManifest = Get-Content -Raw -Encoding UTF8 -LiteralPath (Join-Path $pluginTarget 'manifest.json') | ConvertFrom-Json
foreach ($manifest in @($sourceManifest, $installedManifest)) {
    if ($manifest.id -ne $pluginId) { throw "Unexpected Obsidian plugin id: $($manifest.id)" }
    if ($manifest.version -ne $expectedVersion) { throw "Obsidian plugin version mismatch: $($manifest.version) != $expectedVersion" }
}

$extensionManifestPath = Join-Path $sourceRoot 'extension\manifest.json'
if (-not (Test-Path -LiteralPath $extensionManifestPath -PathType Leaf)) { throw "Missing Chrome extension manifest: $extensionManifestPath" }
$extensionManifest = Get-Content -Raw -Encoding UTF8 -LiteralPath $extensionManifestPath | ConvertFrom-Json
if ($extensionManifest.version -ne $expectedVersion) { throw "Chrome extension version mismatch: $($extensionManifest.version) != $expectedVersion" }
if ($extensionManifest.permissions -contains 'nativeMessaging') { throw 'Chrome extension still requests Native Messaging.' }
if ($extensionManifest.host_permissions -notcontains 'http://127.0.0.1:32191/*') {
    throw 'Chrome extension cannot reach the Obsidian plugin: missing http://127.0.0.1:32191/* host permission.'
}

if (Get-ItemProperty -Path $runPath -Name $runName -ErrorAction SilentlyContinue) {
    throw 'Legacy YouTube Reader startup registration still exists.'
}
foreach ($registryPath in $nativeHostPaths) {
    if (Test-Path -LiteralPath $registryPath) { throw "Legacy Native Messaging registration still exists: $registryPath" }
}
$legacyProcesses = @(Get-CimInstance Win32_Process -ErrorAction SilentlyContinue | Where-Object {
    ($_.CommandLine -and $_.CommandLine.IndexOf($legacyHost, [StringComparison]::OrdinalIgnoreCase) -ge 0) -or
    ($_.ExecutablePath -and [string]::Equals((Get-NormalizedPath $_.ExecutablePath), (Get-NormalizedPath $legacyExe), [StringComparison]::OrdinalIgnoreCase))
})
if ($legacyProcesses.Count -gt 0) { throw 'A legacy YouTube Reader desktop companion process is still running.' }
if (Test-Path -LiteralPath $legacyRoot) { throw "Legacy YouTube Reader runtime still exists: $legacyRoot" }

& (Get-Command node -ErrorAction Stop).Source --check (Join-Path $pluginTarget 'main.js')
if ($LASTEXITCODE -ne 0) { throw 'Installed Obsidian plugin JavaScript syntax check failed.' }

Write-Host "YouTube Reader $expectedVersion installation is valid."
Write-Host "Vault Skill: $vaultSkill"
Write-Host "Obsidian plugin: $pluginTarget"
Write-Host "Chrome extension: $(Join-Path $sourceRoot 'extension')"
Write-Host 'Legacy desktop companion: absent'
