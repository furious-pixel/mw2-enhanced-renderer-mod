param([switch]$PanelOnly, [string]$Channel)

Set-StrictMode -Version Latest
$ErrorActionPreference = 'Stop'
$Root = Split-Path -Parent (Split-Path -Parent $PSScriptRoot)

function Require-Path([string]$Path) {
    if ([string]::IsNullOrWhiteSpace($Path) -or -not (Test-Path -LiteralPath $Path)) {
        throw "Required path is missing: $Path"
    }
    $resolved = (Resolve-Path -LiteralPath $Path).ProviderPath
    if ([string]::IsNullOrWhiteSpace($resolved)) { throw "Path resolved empty: $Path" }
    return $resolved
}

$Python = Require-Path (Join-Path $Root '.venv\Scripts\pythonw.exe')
$Sidecar = Require-Path (Join-Path $PSScriptRoot 'sidecar.py')
if ($PanelOnly) {
    if ($Channel -notmatch '^[0-9a-f]{32}$') { throw 'Pass the channel printed by launch_level_tweak.bat.' }
} else {
    $Channel = [guid]::NewGuid().ToString('N')
}
$env:MW2_LEVEL_TWEAK = '1'
$env:MW2_LEVEL_TWEAK_SHM = $Channel

# Position both windows together once, before either is created.
Add-Type -AssemblyName System.Windows.Forms
$Work = [System.Windows.Forms.Screen]::PrimaryScreen.WorkingArea
$GameW = 1024
$GameH = 768
$Gap = 8
$Chrome = 48
$StripW = if ($env:MW2_LEVEL_TWEAK_WIDTH) { [int]$env:MW2_LEVEL_TWEAK_WIDTH } else { 1200 }
$StripH = if ($env:MW2_LEVEL_TWEAK_HEIGHT) { [int]$env:MW2_LEVEL_TWEAK_HEIGHT } else { 260 }
$StripW = [Math]::Max(640, [Math]::Min($StripW, $Work.Width))
$StripH = [Math]::Max(220, $StripH)
$NeedH = $GameH + $Chrome + $Gap + $StripH
if ($Work.Height -ge $NeedH) {
    $GameX = $Work.Left + [int]([Math]::Max(0, ($Work.Width - [Math]::Max($GameW, $StripW)) / 2))
    $GameY = $Work.Top + [int](($Work.Height - $NeedH) / 2)
    $StripX = $GameX
    $StripY = $GameY + $GameH + $Chrome + $Gap
} else {
    $StripW = [Math]::Max(640, [Math]::Min($StripW, $Work.Width - $GameW - $Gap))
    $NeedW = $GameW + $Gap + $StripW
    $GameX = $Work.Left + [int]([Math]::Max(0, ($Work.Width - $NeedW) / 2))
    $GameY = $Work.Top + [int]([Math]::Max(0, ($Work.Height - [Math]::Max($GameH + $Chrome, $StripH)) / 2))
    $StripX = [Math]::Max($Work.Left, [Math]::Min($GameX + $GameW + $Gap, $Work.Right - $StripW))
    $StripY = $GameY
}
if (-not ($env:MW2_LEVEL_TWEAK_X -and $env:MW2_LEVEL_TWEAK_Y)) {
    $env:MW2_LEVEL_TWEAK_X = "$StripX"
    $env:MW2_LEVEL_TWEAK_Y = "$StripY"
}
$env:MW2_LEVEL_TWEAK_WIDTH = "$StripW"
$env:MW2_LEVEL_TWEAK_HEIGHT = "$StripH"

if (-not $PanelOnly) {
    $Launcher = Require-Path (Join-Path $Root 'tools\launch_mw2.ps1')
    $null = Require-Path (Join-Path $Root 'mw2mods\mw2renderer.dll')
    $gameArgs = @('-set', 'sdl fullscreen=false',
        '-set', "sdl windowresolution=${GameW}x${GameH}",
        '-set', "sdl windowposition=$GameX,$GameY", '-set', 'sdl autolock=false')
    # Share the ordinary launcher's mount/disc validation and startup defaults.
    # Validate before opening the panel, then revalidate at the actual launch.
    & powershell.exe -NoProfile -ExecutionPolicy Bypass -File $Launcher -DryRun @gameArgs | Out-Null
    if ($LASTEXITCODE -ne 0) { throw 'Game installation validation failed.' }
}

Write-Host "Level tweak channel: $Channel"
Write-Host "To reopen a closed panel: level_tweaker.bat $Channel"
$ready = [Threading.EventWaitHandle]::new($false, [Threading.EventResetMode]::ManualReset, "Local\mw2_level_tweak_v4_$Channel.ready")
$shutdown = $null
$panel = $null
$leavePanel = $false
try {
    if (-not $PanelOnly) {
        $shutdown = [Threading.EventWaitHandle]::new($false, [Threading.EventResetMode]::ManualReset, "Local\mw2_level_tweak_v4_$Channel.shutdown")
    }
    $ready.Reset() | Out-Null
    $panel = Start-Process -FilePath $Python -ArgumentList @('"' + $Sidecar + '"') -WorkingDirectory $Root -WindowStyle Hidden -PassThru
    $deadline = [DateTime]::UtcNow.AddSeconds(20)
    while (-not $ready.WaitOne(100)) {
        if ($panel.HasExited) { throw "Level tweak panel exited during startup ($($panel.ExitCode))." }
        if ([DateTime]::UtcNow -gt $deadline) { throw 'Level tweak panel did not become ready; check its error window.' }
    }
    if ($PanelOnly) { $leavePanel = $true; exit 0 }
    # The canonical launcher waits on its own game process. Inputs and mission
    # selection remain under user control; no process-name scanning is needed.
    & powershell.exe -NoProfile -ExecutionPolicy Bypass -File $Launcher @gameArgs
    $exitCode = $LASTEXITCODE
} finally {
    $ready.Dispose()
    if ($shutdown) { $null = $shutdown.Set() }
    if ($panel -and -not $leavePanel -and -not $panel.HasExited) {
        # The panel closes itself once any active atomic save has finished.
        $null = $panel.WaitForExit(3000)
    }
    if ($shutdown) { $shutdown.Dispose() }
    if ($panel) { $panel.Dispose() }
}
exit $exitCode
