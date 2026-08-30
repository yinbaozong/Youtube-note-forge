[CmdletBinding(SupportsShouldProcess)]
param()

$ErrorActionPreference = 'Stop'
$installRoot = Join-Path $env:LOCALAPPDATA 'YouTubeNoteReader'
$hostTarget = Join-Path $installRoot 'youtube_reader_host.py'
$legacyRegistryPath = 'HKCU:\Software\Google\Chrome\NativeMessagingHosts\com.youtube_note_reader.host'
$runPath = 'HKCU:\Software\Microsoft\Windows\CurrentVersion\Run'
$runName = 'YouTubeNoteReader'

if ($PSCmdlet.ShouldProcess($hostTarget, 'Stop YouTube Reader desktop companion')) {
    Get-CimInstance Win32_Process -ErrorAction SilentlyContinue |
        Where-Object { $_.CommandLine -and $_.CommandLine.IndexOf($hostTarget, [StringComparison]::OrdinalIgnoreCase) -ge 0 } |
        ForEach-Object { Stop-Process -Id $_.ProcessId -Force -ErrorAction SilentlyContinue }
}
if (Test-Path $legacyRegistryPath) {
    if ($PSCmdlet.ShouldProcess($legacyRegistryPath, 'Remove legacy Native Messaging registration')) {
        Remove-Item -LiteralPath $legacyRegistryPath -Recurse -Force
    }
}
if (Get-ItemProperty -Path $runPath -Name $runName -ErrorAction SilentlyContinue) {
    if ($PSCmdlet.ShouldProcess($runPath, 'Remove desktop companion startup registration')) {
        Remove-ItemProperty -Path $runPath -Name $runName -Force
    }
}
if (Test-Path -LiteralPath $installRoot) {
    if ($PSCmdlet.ShouldProcess($installRoot, 'Remove YouTube Reader Native Host')) {
        $resolved = (Resolve-Path -LiteralPath $installRoot).Path
        $expected = [IO.Path]::GetFullPath((Join-Path $env:LOCALAPPDATA 'YouTubeNoteReader'))
        if ($resolved -ne $expected) { throw "Refusing to remove unexpected path: $resolved" }
        Remove-Item -LiteralPath $resolved -Recurse -Force
    }
}

Write-Host 'Desktop companion removed. Remove YouTube 阅读器 manually from chrome://extensions.'
Write-Host 'Cookies, notes, OpenCode credentials, and youtube-transcript Skill were preserved.'
