[CmdletBinding()]
param(
    [string]$OutputDirectory = (Join-Path $HOME 'Downloads\YouTubeNoteForge-Releases')
)

$ErrorActionPreference = 'Stop'
$sourceRoot = (Resolve-Path (Join-Path $PSScriptRoot '..')).Path
$version = (Get-Content -Raw -Encoding UTF8 -LiteralPath (Join-Path $sourceRoot 'VERSION')).Trim()
$outputRoot = [IO.Path]::GetFullPath($OutputDirectory)
$tempBase = [IO.Path]::GetFullPath([IO.Path]::GetTempPath()).TrimEnd('\', '/')
$stagingName = 'YouTubeNoteForge-package-' + [Guid]::NewGuid().ToString('N')
$stagingRoot = Join-Path $tempBase $stagingName

function Copy-ReleaseFile([string]$Relative, [string]$DestinationRoot) {
    $source = Join-Path $sourceRoot $Relative
    if (-not (Test-Path -LiteralPath $source -PathType Leaf)) { throw "Missing release file: $source" }
    $target = Join-Path $DestinationRoot $Relative
    New-Item -ItemType Directory -Force -Path (Split-Path -Parent $target) | Out-Null
    Copy-Item -LiteralPath $source -Destination $target -Force
}

function Test-ExcludedReleasePath([string]$Relative) {
    $normalized = $Relative.Replace('/', '\')
    if ($normalized -match '(^|\\)(node_modules|dist|coverage|\.cache|\.parcel-cache|\.turbo|__pycache__)(\\|$)') { return $true }
    $leaf = Split-Path -Leaf $normalized
    if ($leaf -match '^(\.env($|\.)|\.npmrc$|data\.json$)') { return $true }
    if ($leaf -match '(?i)(cookie|credential|secret|token)' -or $leaf -match '(?i)\.(log|key|pem)$') { return $true }
    return $false
}

function Copy-ReleaseTree([string]$RelativeRoot, [string]$DestinationRoot) {
    $sourceDirectory = Join-Path $sourceRoot $RelativeRoot
    if (-not (Test-Path -LiteralPath $sourceDirectory -PathType Container)) {
        throw "Missing release directory: $sourceDirectory"
    }
    $queue = [Collections.Generic.Queue[object]]::new()
    $queue.Enqueue([pscustomobject]@{ Source = $sourceDirectory; Relative = '' })
    while ($queue.Count -gt 0) {
        $current = $queue.Dequeue()
        foreach ($item in Get-ChildItem -LiteralPath $current.Source -Force) {
            $relativeChild = if ($current.Relative) { Join-Path $current.Relative $item.Name } else { $item.Name }
            if (Test-ExcludedReleasePath $relativeChild) { continue }
            if (($item.Attributes -band [IO.FileAttributes]::ReparsePoint) -ne 0) {
                throw "Release source contains a reparse point: $($item.FullName)"
            }
            if ($item.PSIsContainer) {
                $queue.Enqueue([pscustomobject]@{ Source = $item.FullName; Relative = $relativeChild })
                continue
            }
            $target = Join-Path (Join-Path $DestinationRoot $RelativeRoot) $relativeChild
            New-Item -ItemType Directory -Force -Path (Split-Path -Parent $target) | Out-Null
            Copy-Item -LiteralPath $item.FullName -Destination $target -Force
        }
    }
}

function Remove-StagingDirectory {
    if (-not (Test-Path -LiteralPath $stagingRoot)) { return }
    $resolved = (Resolve-Path -LiteralPath $stagingRoot).Path
    $expected = [IO.Path]::GetFullPath((Join-Path $tempBase $stagingName)).TrimEnd('\', '/')
    if (-not [string]::Equals($resolved.TrimEnd('\', '/'), $expected, [StringComparison]::OrdinalIgnoreCase)) {
        throw "Refusing to remove unexpected staging path: $resolved"
    }
    if (-not $expected.StartsWith($tempBase + [IO.Path]::DirectorySeparatorChar + 'YouTubeNoteForge-package-', [StringComparison]::OrdinalIgnoreCase)) {
        throw "Refusing to remove staging path outside the expected temp namespace: $expected"
    }
    $item = Get-Item -LiteralPath $resolved -Force
    if (($item.Attributes -band [IO.FileAttributes]::ReparsePoint) -ne 0) {
        throw "Refusing to recursively remove a reparse point: $resolved"
    }
    Remove-Item -LiteralPath $resolved -Recurse -Force
}

if (-not (Test-Path -LiteralPath (Join-Path $sourceRoot 'obsidian-plugin\package.json') -PathType Leaf)) {
    throw 'OBSIDIAN_PLUGIN_SOURCE_MISSING: Full release packaging requires obsidian-plugin/package.json.'
}

$pluginSource = Join-Path $sourceRoot 'obsidian-plugin'
$npm = (Get-Command npm -ErrorAction Stop).Source
Push-Location $pluginSource
try {
    & $npm ci
    if ($LASTEXITCODE -ne 0) { throw 'Obsidian plugin dependency installation failed during packaging.' }
    & $npm run build
    if ($LASTEXITCODE -ne 0) { throw 'Obsidian plugin build failed during packaging.' }
} finally {
    Pop-Location
}
if (-not (Test-Path -LiteralPath (Join-Path $pluginSource 'main.js') -PathType Leaf)) {
    throw 'Obsidian plugin build did not produce main.js.'
}

New-Item -ItemType Directory -Force -Path $outputRoot, $stagingRoot | Out-Null
try {
    $skillBundleRoot = Join-Path $stagingRoot "youtube-transcript-skill-v$version"
    $skillRoot = Join-Path $skillBundleRoot 'youtube-transcript'
    foreach ($relative in @(
        'VERSION',
        'SKILL.md',
        'requirements.txt',
        'requirements-asr.txt',
        'references\note-contract.md',
        'scripts\deduplicate_transcript.py',
        'scripts\extract_frames.py',
        'scripts\extract_transcript.py',
        'scripts\validate_note.py',
        'scripts\video_common.py',
        'scripts\video_note.py'
    )) {
        Copy-ReleaseFile $relative $skillRoot
    }
    foreach ($relative in @(
        'opencode\agent\video-note.md',
        'opencode\command\video-note.md'
    )) {
        Copy-ReleaseFile $relative $skillBundleRoot
    }

    $skillZip = Join-Path $outputRoot "youtube-transcript-skill-v$version.zip"
    if (Test-Path -LiteralPath $skillZip) { Remove-Item -LiteralPath $skillZip -Force }
    Compress-Archive -LiteralPath $skillBundleRoot -DestinationPath $skillZip -CompressionLevel Optimal

    $fullRoot = Join-Path $stagingRoot "YouTube-Note-Reader-v$version"
    foreach ($relative in @(
        'VERSION',
        'SKILL.md',
        'README.md',
        'LICENSE',
        'requirements.txt',
        'requirements-asr.txt',
        'references\note-contract.md',
        'scripts\deduplicate_transcript.py',
        'scripts\extract_frames.py',
        'scripts\extract_transcript.py',
        'scripts\install.ps1',
        'scripts\package_release.ps1',
        'scripts\sync_local.ps1',
        'scripts\uninstall.ps1',
        'scripts\validate_note.py',
        'scripts\verify_install.ps1',
        'scripts\video_common.py',
        'scripts\video_note.py'
    )) {
        Copy-ReleaseFile $relative $fullRoot
    }
    Copy-ReleaseTree 'extension' $fullRoot
    Copy-ReleaseTree 'obsidian-plugin' $fullRoot

    $fullZip = Join-Path $outputRoot "youtube-reader-chrome-obsidian-v$version.zip"
    if (Test-Path -LiteralPath $fullZip) { Remove-Item -LiteralPath $fullZip -Force }
    Compress-Archive -LiteralPath $fullRoot -DestinationPath $fullZip -CompressionLevel Optimal

    Write-Host "Skill-only package (includes OpenCode agent/command): $skillZip"
    Write-Host "Full Chrome + Obsidian package: $fullZip"
} finally {
    Remove-StagingDirectory
}
