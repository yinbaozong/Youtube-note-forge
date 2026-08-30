[CmdletBinding(SupportsShouldProcess)]
param(
    [string]$Vault = 'C:\Users\win11\Documents\Obsidian Vault',
    [string]$Python = '',
    [switch]$SkipDependencies
)

$ErrorActionPreference = 'Stop'
Add-Type -AssemblyName System.Net.Http
$sourceRoot = (Resolve-Path (Join-Path $PSScriptRoot '..')).Path
$extensionRoot = Join-Path $sourceRoot 'extension'
$hostSource = Join-Path $sourceRoot 'native_host\youtube_reader_host.py'
$installRoot = Join-Path $env:LOCALAPPDATA 'YouTubeNoteReader'
$hostTarget = Join-Path $installRoot 'youtube_reader_host.py'
$runtimePath = Join-Path $installRoot 'runtime.json'
$runPath = 'HKCU:\Software\Microsoft\Windows\CurrentVersion\Run'
$runName = 'YouTubeNoteReader'
$legacyRegistryPath = 'HKCU:\Software\Google\Chrome\NativeMessagingHosts\com.youtube_note_reader.host'

function Test-CompanionHealth {
    param([string]$ExpectedVersion)
    $handler = [Net.Http.HttpClientHandler]::new()
    $handler.UseProxy = $false
    $client = [Net.Http.HttpClient]::new($handler)
    try {
        $client.Timeout = [TimeSpan]::FromSeconds(3)
        $json = $client.GetStringAsync('http://127.0.0.1:32191/health').GetAwaiter().GetResult()
        $payload = $json | ConvertFrom-Json
        return $payload.status -eq 'ok' -and $payload.version -eq $ExpectedVersion
    } catch {
        return $false
    } finally {
        $client.Dispose()
        $handler.Dispose()
    }
}

if (-not (Test-Path -LiteralPath $Vault -PathType Container)) {
    throw "Obsidian Vault does not exist: $Vault"
}
if (-not $Python) {
    $Python = (Get-Command python -ErrorAction Stop).Source
}
foreach ($command in @('opencode', 'ffmpeg', 'node')) {
    if (-not (Get-Command $command -ErrorAction SilentlyContinue)) {
        throw "Required command is missing: $command"
    }
}

if ($WhatIfPreference) {
    & (Join-Path $PSScriptRoot 'sync_local.ps1') -WhatIf
} else {
    & (Join-Path $PSScriptRoot 'sync_local.ps1')
}

if (-not $SkipDependencies -and $PSCmdlet.ShouldProcess('Python environment', 'Install youtube-transcript dependencies')) {
    & $Python -m pip install -r (Join-Path $sourceRoot 'requirements.txt')
    if ($LASTEXITCODE -ne 0) { throw 'Python dependency installation failed.' }
}

$pythonwCandidate = Join-Path (Split-Path -Parent $Python) 'pythonw.exe'
$launcher = if (Test-Path -LiteralPath $pythonwCandidate -PathType Leaf) { $pythonwCandidate } else { $Python }

if ($PSCmdlet.ShouldProcess($installRoot, 'Install YouTube Reader desktop companion')) {
    New-Item -ItemType Directory -Force -Path $installRoot | Out-Null
    Get-CimInstance Win32_Process -ErrorAction SilentlyContinue |
        Where-Object {
            ($_.CommandLine -and $_.CommandLine.IndexOf($hostTarget, [StringComparison]::OrdinalIgnoreCase) -ge 0) -or
            ($_.ExecutablePath -and $_.ExecutablePath.EndsWith('youtube-reader-host.exe', [StringComparison]::OrdinalIgnoreCase))
        } |
        ForEach-Object {
            Stop-Process -Id $_.ProcessId -Force -ErrorAction SilentlyContinue
            Wait-Process -Id $_.ProcessId -Timeout 10 -ErrorAction SilentlyContinue
        }

    Copy-Item -LiteralPath $hostSource -Destination $hostTarget -Force
    @{
        python = [IO.Path]::GetFullPath($Python)
        launcher = [IO.Path]::GetFullPath($launcher)
        host = [IO.Path]::GetFullPath($hostTarget)
    } | ConvertTo-Json | Set-Content -LiteralPath $runtimePath -Encoding UTF8

    if (Test-Path $legacyRegistryPath) { Remove-Item -LiteralPath $legacyRegistryPath -Recurse -Force }
    foreach ($legacy in @('com.youtube_note_reader.host.json', 'youtube-reader-host.cmd', 'youtube-reader-host.exe')) {
        $legacyPath = Join-Path $installRoot $legacy
        if (Test-Path -LiteralPath $legacyPath) { Remove-Item -LiteralPath $legacyPath -Force }
    }

    New-Item -Path $runPath -Force | Out-Null
    $runCommand = '"' + $launcher + '" "' + $hostTarget + '" --serve'
    New-ItemProperty -Path $runPath -Name $runName -Value $runCommand -PropertyType String -Force | Out-Null
    Start-Process -FilePath $launcher -ArgumentList @($hostTarget, '--serve') -WindowStyle Hidden
}

$expectedVersion = (Get-Content -Raw -Encoding UTF8 -LiteralPath (Join-Path $sourceRoot 'VERSION')).Trim()
$ready = $false
for ($attempt = 0; $attempt -lt 15; $attempt++) {
    Start-Sleep -Milliseconds 400
    if (Test-CompanionHealth -ExpectedVersion $expectedVersion) { $ready = $true; break }
}
if (-not $ready) { throw 'Desktop companion failed its local health check.' }

Write-Host ''
Write-Host "YouTube Reader desktop companion $expectedVersion is running."
Write-Host "Extension directory: $extensionRoot"
Write-Host 'Reload YouTube Reader once in chrome://extensions to activate the local companion transport.'
