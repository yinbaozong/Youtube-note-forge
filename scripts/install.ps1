[CmdletBinding(SupportsShouldProcess)]
param(
    [string]$Vault = 'C:\Users\win11\Documents\Obsidian Vault',
    [string]$Python = '',
    [switch]$SkipDependencies
)

$ErrorActionPreference = 'Stop'
$sourceRoot = (Resolve-Path (Join-Path $PSScriptRoot '..')).Path
$extensionRoot = Join-Path $sourceRoot 'extension'
$hostSource = Join-Path $sourceRoot 'native_host\youtube_reader_host.py'
$installRoot = Join-Path $env:LOCALAPPDATA 'YouTubeNoteReader'
$hostTarget = Join-Path $installRoot 'youtube-reader-host.exe'
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

& $Python -c 'import PyInstaller' 2>$null
if ($LASTEXITCODE -ne 0) {
    if ($SkipDependencies) { throw 'PyInstaller is required to build the desktop companion.' }
    & $Python -m pip install 'pyinstaller>=6.20,<7'
    if ($LASTEXITCODE -ne 0) { throw 'PyInstaller installation failed.' }
}

if ($PSCmdlet.ShouldProcess($installRoot, 'Install YouTube Reader desktop companion')) {
    New-Item -ItemType Directory -Force -Path $installRoot | Out-Null
    $buildRoot = Join-Path $env:TEMP "youtube-reader-host-build-$PID"
    $distRoot = Join-Path $buildRoot 'dist'
    try {
        New-Item -ItemType Directory -Force -Path $distRoot | Out-Null
        & $Python -m PyInstaller --noconfirm --clean --onefile --log-level WARN `
            --name youtube-reader-host --distpath $distRoot --workpath (Join-Path $buildRoot 'work') `
            --specpath $buildRoot $hostSource
        if ($LASTEXITCODE -ne 0) { throw 'Desktop companion executable build failed.' }

        $installedHost = [IO.Path]::GetFullPath($hostTarget)
        Get-Process -Name 'youtube-reader-host' -ErrorAction SilentlyContinue |
            Where-Object { $_.Path -and [IO.Path]::GetFullPath($_.Path) -eq $installedHost } |
            ForEach-Object {
                Stop-Process -Id $_.Id -Force -ErrorAction SilentlyContinue
                Wait-Process -Id $_.Id -Timeout 10 -ErrorAction SilentlyContinue
            }
        $builtHost = Join-Path $distRoot 'youtube-reader-host.exe'
        $copied = $false
        for ($attempt = 0; $attempt -lt 20; $attempt++) {
            try {
                Copy-Item -LiteralPath $builtHost -Destination $hostTarget -Force
                $copied = $true
                break
            } catch [IO.IOException] {
                Start-Sleep -Milliseconds 250
            }
        }
        if (-not $copied) { throw "Desktop companion executable remained locked: $hostTarget" }
    } finally {
        if (Test-Path -LiteralPath $buildRoot) {
            $fullBuild = [IO.Path]::GetFullPath($buildRoot)
            $fullTemp = [IO.Path]::GetFullPath($env:TEMP).TrimEnd('\') + '\'
            if (-not $fullBuild.StartsWith($fullTemp, [StringComparison]::OrdinalIgnoreCase)) {
                throw "Refusing to remove unexpected build directory: $fullBuild"
            }
            Remove-Item -LiteralPath $fullBuild -Recurse -Force
        }
    }

    if (Test-Path $legacyRegistryPath) { Remove-Item -LiteralPath $legacyRegistryPath -Recurse -Force }
    foreach ($legacy in @('com.youtube_note_reader.host.json', 'youtube-reader-host.cmd', 'youtube_reader_host.py')) {
        $legacyPath = Join-Path $installRoot $legacy
        if (Test-Path -LiteralPath $legacyPath) { Remove-Item -LiteralPath $legacyPath -Force }
    }

    New-Item -Path $runPath -Force | Out-Null
    $runCommand = '"' + $hostTarget + '" --serve'
    New-ItemProperty -Path $runPath -Name $runName -Value $runCommand -PropertyType String -Force | Out-Null
    Start-Process -FilePath $hostTarget -ArgumentList '--serve' -WindowStyle Hidden
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
