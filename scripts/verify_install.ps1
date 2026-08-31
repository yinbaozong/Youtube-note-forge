[CmdletBinding()]
param(
    [string]$Vault = (Join-Path $HOME 'Documents\Obsidian Vault')
)

$ErrorActionPreference = 'Stop'
Add-Type -AssemblyName System.Net.Http
$sourceRoot = (Resolve-Path (Join-Path $PSScriptRoot '..')).Path
$expectedVersion = (Get-Content -Raw -Encoding UTF8 -LiteralPath (Join-Path $sourceRoot 'VERSION')).Trim()
$installRoot = Join-Path $env:LOCALAPPDATA 'YouTubeNoteReader'
$hostTarget = Join-Path $installRoot 'youtube_reader_host.py'
$runtimePath = Join-Path $installRoot 'runtime.json'
$runPath = 'HKCU:\Software\Microsoft\Windows\CurrentVersion\Run'
$runName = 'YouTubeNoteReader'
$legacyRegistryPath = 'HKCU:\Software\Google\Chrome\NativeMessagingHosts\com.youtube_note_reader.host'
$openCodeVersion = Join-Path $HOME '.config\opencode\skills\youtube-transcript\VERSION'

foreach ($path in @(
    (Join-Path $sourceRoot 'extension\manifest.json'),
    $hostTarget,
    $runtimePath,
    (Join-Path $Vault '.obsidian\skills\youtube-transcript\VERSION'),
    $openCodeVersion
)) {
    if (-not (Test-Path -LiteralPath $path)) { throw "Missing installation file: $path" }
}

$runtime = Get-Content -Raw -Encoding UTF8 -LiteralPath $runtimePath | ConvertFrom-Json
foreach ($path in @($runtime.python, $runtime.launcher, $runtime.host)) {
    if (-not (Test-Path -LiteralPath $path -PathType Leaf)) { throw "Missing companion runtime path: $path" }
}
$sourceHostHash = (Get-FileHash -Algorithm SHA256 -LiteralPath (Join-Path $sourceRoot 'native_host\youtube_reader_host.py')).Hash
$installedHostHash = (Get-FileHash -Algorithm SHA256 -LiteralPath $hostTarget).Hash
if ($sourceHostHash -ne $installedHostHash) { throw 'Installed desktop companion source hash does not match the repository.' }

$runCommand = (Get-ItemProperty -Path $runPath -Name $runName -ErrorAction Stop).$runName
if ($runCommand -ne ('"' + $runtime.launcher + '" "' + $hostTarget + '" --serve')) { throw 'Desktop companion startup registration is incorrect.' }
if (Test-Path $legacyRegistryPath) { throw 'Legacy Chrome Native Messaging registration is still present.' }

$manifest = Get-Content -Raw -Encoding UTF8 -LiteralPath (Join-Path $sourceRoot 'extension\manifest.json') | ConvertFrom-Json
if ($manifest.version -ne $expectedVersion) { throw "Extension version mismatch: $($manifest.version) != $expectedVersion" }
if ($manifest.permissions -contains 'nativeMessaging') { throw 'Extension must not depend on Chrome Native Messaging.' }
if ($manifest.host_permissions -notcontains 'http://127.0.0.1:32191/*') { throw 'Extension local companion permission is missing.' }

foreach ($versionPath in @(
    (Join-Path $Vault '.obsidian\skills\youtube-transcript\VERSION'),
    $openCodeVersion
)) {
    $actual = (Get-Content -Raw -Encoding UTF8 -LiteralPath $versionPath).Trim()
    if ($actual -ne $expectedVersion) { throw "Skill version mismatch at ${versionPath}: $actual != $expectedVersion" }
}

& $runtime.python $hostTarget --self-test
if ($LASTEXITCODE -ne 0) { throw 'Desktop companion self-test failed.' }

$handler = [Net.Http.HttpClientHandler]::new()
$handler.UseProxy = $false
$client = [Net.Http.HttpClient]::new($handler)
try {
    $client.Timeout = [TimeSpan]::FromSeconds(3)
    $json = $client.GetStringAsync('http://127.0.0.1:32191/health').GetAwaiter().GetResult()
    $health = $json | ConvertFrom-Json
    if ($health.status -ne 'ok' -or $health.version -ne $expectedVersion) {
        throw "Desktop companion health mismatch: $json"
    }
} finally {
    $client.Dispose()
    $handler.Dispose()
}

foreach ($script in Get-ChildItem -LiteralPath (Join-Path $sourceRoot 'extension') -Filter '*.js') {
    node --check $script.FullName
    if ($LASTEXITCODE -ne 0) { throw "JavaScript syntax check failed: $($script.FullName)" }
}
python -m unittest discover -s (Join-Path $sourceRoot 'tests') -p 'test*.py'
if ($LASTEXITCODE -ne 0) { throw 'Skill tests failed.' }

Write-Host "YouTube Reader $expectedVersion installation is valid."
Write-Host "Extension: $(Join-Path $sourceRoot 'extension')"
Write-Host "Desktop companion: $hostTarget"
Write-Host 'Health: http://127.0.0.1:32191/health'
Write-Host "Job log: $(Join-Path $installRoot 'last-job.jsonl')"
