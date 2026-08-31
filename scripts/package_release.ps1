[CmdletBinding()]
param(
    [string]$OutputDirectory = (Join-Path $HOME 'Downloads\YouTubeNoteForge-Releases')
)

$ErrorActionPreference = 'Stop'
$sourceRoot = (Resolve-Path (Join-Path $PSScriptRoot '..')).Path
$version = (Get-Content -Raw -Encoding UTF8 -LiteralPath (Join-Path $sourceRoot 'VERSION')).Trim()
$outputRoot = [IO.Path]::GetFullPath($OutputDirectory)
$tempBase = [IO.Path]::GetFullPath([IO.Path]::GetTempPath())
$stagingRoot = Join-Path $tempBase ("YouTubeNoteForge-package-" + [Guid]::NewGuid().ToString('N'))

function Copy-ReleaseFile([string]$Relative, [string]$DestinationRoot) {
    $source = Join-Path $sourceRoot $Relative
    if (-not (Test-Path -LiteralPath $source -PathType Leaf)) { throw "Missing release file: $source" }
    $target = Join-Path $DestinationRoot $Relative
    New-Item -ItemType Directory -Force -Path (Split-Path -Parent $target) | Out-Null
    Copy-Item -LiteralPath $source -Destination $target -Force
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

    $fullRoot = Join-Path $stagingRoot "YouTube-Note-Forge-v$version"
    foreach ($relative in @(
        'VERSION',
        'SKILL.md',
        'README.md',
        'LICENSE',
        'requirements.txt',
        'requirements-asr.txt',
        'docs\workflow.svg',
        'extension\manifest.json',
        'extension\options.css',
        'extension\options.html',
        'extension\options.js',
        'extension\popup.css',
        'extension\popup.html',
        'extension\popup.js',
        'extension\service_worker.js',
        'extension\icons\icon16.png',
        'extension\icons\icon32.png',
        'extension\icons\icon48.png',
        'extension\icons\icon128.png',
        'native_host\youtube_reader_host.py',
        'opencode\agent\video-note.md',
        'opencode\command\video-note.md',
        'references\note-contract.md',
        'scripts\deduplicate_transcript.py',
        'scripts\extract_frames.py',
        'scripts\extract_transcript.py',
        'scripts\install.ps1',
        'scripts\package_release.ps1',
        'scripts\restart_companion.ps1',
        'scripts\sync_local.ps1',
        'scripts\uninstall.ps1',
        'scripts\validate_note.py',
        'scripts\verify_install.ps1',
        'scripts\video_common.py',
        'scripts\video_note.py'
    )) {
        Copy-ReleaseFile $relative $fullRoot
    }

    $fullZip = Join-Path $outputRoot "youtube-reader-full-v$version.zip"
    if (Test-Path -LiteralPath $fullZip) { Remove-Item -LiteralPath $fullZip -Force }
    Compress-Archive -LiteralPath $fullRoot -DestinationPath $fullZip -CompressionLevel Optimal

    Write-Host "Skill-only package: $skillZip"
    Write-Host "Full reader package: $fullZip"
} finally {
    $resolvedStaging = [IO.Path]::GetFullPath($stagingRoot)
    if ($resolvedStaging.StartsWith($tempBase, [StringComparison]::OrdinalIgnoreCase) -and (Test-Path -LiteralPath $resolvedStaging)) {
        Remove-Item -LiteralPath $resolvedStaging -Recurse -Force
    }
}
