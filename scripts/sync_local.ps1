[CmdletBinding(SupportsShouldProcess)]
param(
    [string]$Vault = (Join-Path $HOME 'Documents\Obsidian Vault'),
    [string]$OpenCodeRoot = (Join-Path $HOME '.config\opencode'),
    [switch]$IncludeOpenCode
)

$ErrorActionPreference = 'Stop'
$sourceRoot = (Resolve-Path (Join-Path $PSScriptRoot '..')).Path
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
$coreFiles = @(
    'VERSION',
    'SKILL.md',
    'scripts\extract_transcript.py',
    'scripts\extract_frames.py',
    'scripts\validate_note.py',
    'scripts\video_common.py',
    'scripts\video_note.py'
)
$obsoleteNames = @('chrome-auth-profile', 'node_modules', 'package.json', 'package-lock.json')

function Get-NormalizedPath([string]$Path) {
    return [IO.Path]::GetFullPath($Path).TrimEnd('\', '/')
}

function Assert-DirectChild([string]$Parent, [string]$Child) {
    $parentPath = Get-NormalizedPath $Parent
    $childPath = Get-NormalizedPath $Child
    $prefix = $parentPath + [IO.Path]::DirectorySeparatorChar
    if (-not $childPath.StartsWith($prefix, [StringComparison]::OrdinalIgnoreCase)) {
        throw "Refusing to remove path outside Skill destination: $childPath"
    }
    if (Test-Path -LiteralPath $childPath) {
        $item = Get-Item -LiteralPath $childPath -Force
        if (($item.Attributes -band [IO.FileAttributes]::ReparsePoint) -ne 0) {
            throw "Refusing to recursively remove a reparse point: $childPath"
        }
    }
}

function Copy-Skill([string]$Destination) {
    foreach ($relative in $files) {
        $source = Join-Path $sourceRoot $relative
        $target = Join-Path $Destination $relative
        if (-not (Test-Path -LiteralPath $source -PathType Leaf)) { throw "Missing source file: $source" }
        if ($PSCmdlet.ShouldProcess($target, 'Synchronize Skill file')) {
            New-Item -ItemType Directory -Force -Path (Split-Path -Parent $target) | Out-Null
            Copy-Item -LiteralPath $source -Destination $target -Force
        }
    }

    foreach ($obsolete in $obsoleteNames) {
        $target = Join-Path $Destination $obsolete
        if (Test-Path -LiteralPath $target) {
            Assert-DirectChild $Destination $target
            if ($PSCmdlet.ShouldProcess($target, 'Remove obsolete Skill artifact')) {
                Remove-Item -LiteralPath $target -Recurse -Force
            }
        }
    }

    if (-not $WhatIfPreference) {
        foreach ($relative in $coreFiles) {
            $sourceHash = (Get-FileHash -Algorithm SHA256 -LiteralPath (Join-Path $sourceRoot $relative)).Hash
            $targetHash = (Get-FileHash -Algorithm SHA256 -LiteralPath (Join-Path $Destination $relative)).Hash
            if ($sourceHash -ne $targetHash) { throw "Hash mismatch for $relative at $Destination" }
        }
    }
}

if (-not (Test-Path -LiteralPath $Vault -PathType Container)) { throw "Obsidian Vault does not exist: $Vault" }
$vaultSkill = Join-Path $Vault '.obsidian\skills\youtube-transcript'
Copy-Skill $vaultSkill
Write-Host "Vault Skill synchronized: $vaultSkill"

if ($IncludeOpenCode) {
    $openCodeSkill = Join-Path $OpenCodeRoot 'skills\youtube-transcript'
    Copy-Skill $openCodeSkill

    foreach ($mapping in @(
        @{ Source = 'opencode\agent\video-note.md'; Target = (Join-Path $OpenCodeRoot 'agent\video-note.md') },
        @{ Source = 'opencode\command\video-note.md'; Target = (Join-Path $OpenCodeRoot 'command\video-note.md') }
    )) {
        if ($PSCmdlet.ShouldProcess($mapping.Target, 'Install optional OpenCode integration')) {
            New-Item -ItemType Directory -Force -Path (Split-Path -Parent $mapping.Target) | Out-Null
            Copy-Item -LiteralPath (Join-Path $sourceRoot $mapping.Source) -Destination $mapping.Target -Force
        }
    }
    Write-Host "Optional OpenCode integration synchronized: $OpenCodeRoot"
} else {
    Write-Host 'OpenCode synchronization skipped. Use -IncludeOpenCode only for the Skill-only workflow.'
}

Write-Host "youtube-transcript version $((Get-Content -Raw -Encoding UTF8 -LiteralPath (Join-Path $sourceRoot 'VERSION')).Trim()) is synchronized."
