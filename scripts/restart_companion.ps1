[CmdletBinding()]
param()

$ErrorActionPreference = 'Stop'
Add-Type -AssemblyName System.Net.Http
$installRoot = Join-Path $env:LOCALAPPDATA 'YouTubeNoteReader'
$runtimePath = Join-Path $installRoot 'runtime.json'

if (-not (Test-Path -LiteralPath $runtimePath -PathType Leaf)) {
    throw "Desktop companion is not installed. Run scripts\install.ps1 first. Expected: $runtimePath"
}
$runtime = Get-Content -Raw -Encoding UTF8 -LiteralPath $runtimePath | ConvertFrom-Json
$hostPath = [string]$runtime.host
$launcher = [string]$runtime.launcher
foreach ($path in @($hostPath, $launcher)) {
    if (-not (Test-Path -LiteralPath $path -PathType Leaf)) { throw "Desktop companion runtime file is missing: $path" }
}

Get-CimInstance Win32_Process -ErrorAction SilentlyContinue |
    Where-Object { $_.CommandLine -and $_.CommandLine.IndexOf($hostPath, [StringComparison]::OrdinalIgnoreCase) -ge 0 } |
    ForEach-Object {
        Stop-Process -Id $_.ProcessId -Force -ErrorAction SilentlyContinue
        Wait-Process -Id $_.ProcessId -Timeout 5 -ErrorAction SilentlyContinue
    }

Start-Process -FilePath $launcher -ArgumentList @($hostPath, '--serve') -WindowStyle Hidden

$handler = [Net.Http.HttpClientHandler]::new()
$handler.UseProxy = $false
$client = [Net.Http.HttpClient]::new($handler)
try {
    $client.Timeout = [TimeSpan]::FromSeconds(2)
    for ($attempt = 0; $attempt -lt 12; $attempt++) {
        Start-Sleep -Milliseconds 350
        try {
            $payload = $client.GetStringAsync('http://127.0.0.1:32191/health').GetAwaiter().GetResult() | ConvertFrom-Json
            if ($payload.status -eq 'ok') {
                Write-Host "YouTube Reader desktop companion $($payload.version) is running."
                Write-Host "Host source: $hostPath"
                Write-Host "Launcher: $launcher"
                exit 0
            }
        } catch {
            # Keep waiting until the bounded startup window expires.
        }
    }
} finally {
    $client.Dispose()
    $handler.Dispose()
}

throw 'Desktop companion did not become healthy within 5 seconds.'
