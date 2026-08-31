[CmdletBinding()]
param(
    [string]$Vault = (Join-Path $HOME 'Documents\Obsidian Vault'),
    [string]$OpenCodeRoot = (Join-Path $HOME '.config\opencode'),
    [switch]$WhatIf
)

$ErrorActionPreference = 'Stop'
$sourceRoot = (Resolve-Path (Join-Path $PSScriptRoot '..')).Path
$destinations = @(
    (Join-Path $OpenCodeRoot 'skills\youtube-transcript'),
    (Join-Path $Vault '.obsidian\skills\youtube-transcript')
)
$files = @(
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
$obsoleteNames = @('chrome-auth-profile', 'node_modules', 'package.json', 'package-lock.json')

function Copy-SkillFile([string]$destination, [string]$relative) {
    $source = Join-Path $sourceRoot $relative
    $target = Join-Path $destination $relative
    if (-not (Test-Path -LiteralPath $source)) { throw "Missing source file: $source" }
    $parent = Split-Path -Parent $target
    if (-not $WhatIf) { New-Item -ItemType Directory -Force -Path $parent | Out-Null; Copy-Item -LiteralPath $source -Destination $target -Force }
    Write-Host "SYNC $relative -> $destination"
}

foreach ($destination in $destinations) {
    if (-not $WhatIf) { New-Item -ItemType Directory -Force -Path $destination | Out-Null }
    foreach ($relative in $files) { Copy-SkillFile $destination $relative }
    foreach ($obsolete in $obsoleteNames) {
        $target = Join-Path $destination $obsolete
        if (Test-Path -LiteralPath $target) {
            Write-Host "REMOVE obsolete $target"
            if (-not $WhatIf) { Remove-Item -LiteralPath $target -Force -Recurse }
        }
    }
    foreach ($relative in @('VERSION', 'SKILL.md', 'scripts\extract_transcript.py', 'scripts\extract_frames.py', 'scripts\validate_note.py', 'scripts\video_common.py', 'scripts\video_note.py')) {
        $sourceHash = (Get-FileHash -Algorithm SHA256 -LiteralPath (Join-Path $sourceRoot $relative)).Hash
        $targetHash = if ($WhatIf) { $sourceHash } else { (Get-FileHash -Algorithm SHA256 -LiteralPath (Join-Path $destination $relative)).Hash }
        if ($sourceHash -ne $targetHash) { throw "Hash mismatch for $relative at $destination" }
    }
}

$globalRoot = $OpenCodeRoot
$agentTarget = Join-Path $globalRoot 'agent\video-note.md'
$commandTarget = Join-Path $globalRoot 'command\video-note.md'
if (-not $WhatIf) {
    New-Item -ItemType Directory -Force -Path (Split-Path -Parent $agentTarget), (Split-Path -Parent $commandTarget) | Out-Null
    Copy-Item -LiteralPath (Join-Path $sourceRoot 'opencode\agent\video-note.md') -Destination $agentTarget -Force
    Copy-Item -LiteralPath (Join-Path $sourceRoot 'opencode\command\video-note.md') -Destination $commandTarget -Force
}
Write-Host "youtube-transcript version $((Get-Content -Raw -Encoding UTF8 (Join-Path $sourceRoot 'VERSION')).Trim()) is synchronized. Restart OpenCode to load it."
