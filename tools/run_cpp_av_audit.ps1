param(
    [ValidateSet(60, 72, 75)][int]$Fps = 60,
    [ValidateSet('on', 'off')][string]$Vsync = 'on',
    [ValidateSet('250000', 'max')][string]$Cycles = '250000',
    [switch]$AuditOff,
    [switch]$DryRun
)
$ErrorActionPreference = 'Stop'
$renderer = Split-Path $PSScriptRoot -Parent
$exe = Join-Path $renderer 'bin\dosbox-x.exe'
$config = Join-Path $renderer 'dosbox-mw2.conf'
foreach ($path in @($exe, $config)) {
    if (!(Test-Path -LiteralPath $path)) { throw "Missing: $path" }
}
# Use the canonical config unchanged; validate its mount operands before launch.
$mounts = @(Get-Content -LiteralPath $config | Where-Object { $_ -match '^\s*(IMG)?MOUNT\s' })
if ($mounts.Count -ne 2) { throw 'Expected the two canonical game/disc mounts; review the config.' }
foreach ($line in $mounts) {
    if ($line -notmatch '^\s*(?:IMG)?MOUNT\s+[A-Za-z]\s+"([^"]+)"') {
        throw "Unrecognised mount: $line"
    }
    $path = Join-Path $renderer $Matches[1]
    $resolved = (Resolve-Path -LiteralPath $path -ErrorAction Stop).Path
    if (!$resolved) { throw "Empty mount path: $line" }
    if ([IO.Path]::GetExtension($resolved) -ieq '.cue') {
        foreach ($cueLine in Get-Content -LiteralPath $resolved) {
            if ($cueLine -match '^\s*FILE\s+"([^"]+)"') {
                $null = Resolve-Path -LiteralPath (Join-Path (Split-Path $resolved) $Matches[1]) -ErrorAction Stop
            }
        }
    }
}
$label = '{0}-fps{1}-vsync{2}-audit{3}-cycles{4}' -f (Get-Date -Format 'yyyyMMdd-HHmmss-fff'), $Fps, $Vsync, (!$AuditOff), $Cycles
$log = Join-Path $renderer "perf-results\av-audit\$label.log"
$hostVsync = if ($Vsync -eq 'on') { 'true' } else { 'false' }
$arguments = '-console -python -moddir mw2mods -log-fileio -conf ".\dosbox-mw2.conf" ' +
    '-set "sdl fullscreen=true" -set "sdl fullresolution=desktop" -set "sdl showmenu=false" ' +
    '-set "render mod renderer start view=mod-only" ' +
    "-set `"render mod renderer target fps=$Fps`" -set `"render mod renderer host vsync=$hostVsync`" " +
    "-set `"vsync vsyncmode=off`" -set `"cpu cycles=$Cycles`" -set `"log logfile=$log.dosbox.log`""
Write-Host "Manual test: set monitor refresh first; FPS=$Fps V-sync=$Vsync cycles=$Cycles audit=$(!$AuditOff)"
Write-Host "Log: $log"
if ($DryRun) { Write-Host "$exe $arguments"; return }
$running = @(Get-Process -Name 'dosbox-x', 'dosbox-x-av-audit' -ErrorAction SilentlyContinue)
if ($running.Count) {
    throw "Close the existing DOSBox-X instance before running this comparison. Process IDs: $($running.Id -join ', ')"
}
$null = New-Item -ItemType Directory -Path (Split-Path $log) -Force
$oldAudit = $env:MW2_AV_AUDIT
$oldAuditLog = $env:MW2_AV_AUDIT_LOG
$frameLog = Join-Path $renderer 'frame_timing.log'
$frameOffset = if (Test-Path -LiteralPath $frameLog) { (Get-Item -LiteralPath $frameLog).Length } else { 0 }
try {
    $env:MW2_AV_AUDIT_LOG = $log
    $env:MW2_AV_AUDIT = if ($AuditOff) { '0' } else { '1' }
    $process = Start-Process -FilePath $exe -ArgumentList $arguments -WorkingDirectory $renderer -Wait -PassThru
    if (Test-Path -LiteralPath $frameLog) {
        $inputStream = [IO.File]::OpenRead($frameLog)
        try {
            if ($inputStream.Length -ge $frameOffset) { $inputStream.Position = $frameOffset }
            $outputStream = [IO.File]::Open("$log.frames.log", [IO.FileMode]::CreateNew)
            try { $inputStream.CopyTo($outputStream) } finally { $outputStream.Dispose() }
        } finally { $inputStream.Dispose() }
    }
    Write-Host "DOSBox-X exited with code $($process.ExitCode). Log: $log"
} finally {
    $env:MW2_AV_AUDIT = $oldAudit
    $env:MW2_AV_AUDIT_LOG = $oldAuditLog
}
