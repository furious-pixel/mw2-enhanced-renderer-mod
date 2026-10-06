# Validate the installation before DOSBox interprets any mount command.
# Remaining arguments are passed to DOSBox after the automatic defaults.
$ErrorActionPreference = 'Stop'
$renderer = Split-Path $PSScriptRoot -Parent
$exe = Join-Path $renderer 'bin\dosbox-x.exe'
$config = Join-Path $renderer 'dosbox-mw2.conf'
foreach ($path in @($exe, $config)) {
    if (!(Test-Path -LiteralPath $path -PathType Leaf)) { throw "Missing: $path" }
}
$mounts = @(Get-Content -LiteralPath $config | Where-Object { $_ -match '^\s*(IMG)?MOUNT\s' })
if ($mounts.Count -ne 2) { throw 'Expected the two canonical game/disc mounts; review dosbox-mw2.conf.' }
foreach ($line in $mounts) {
    if ($line -notmatch '^\s*(?:IMG)?MOUNT\s+[A-Za-z]\s+"([^"]+)"(?:\s+-t\s+iso)?\s*$') {
        throw "Unrecognised mount: $line"
    }
    $mountPath = $Matches[1]
    if (![IO.Path]::IsPathRooted($mountPath)) { $mountPath = Join-Path $renderer $mountPath }
    $resolved = (Resolve-Path -LiteralPath $mountPath -ErrorAction Stop).Path
    if (!$resolved) { throw "Empty mount path: $line" }
    if ([IO.Path]::GetExtension($resolved) -ieq '.cue') {
        $tracks = @(Get-Content -LiteralPath $resolved | Where-Object { $_ -match '^\s*FILE\s' })
        if (!$tracks.Count) { throw "Disc image has no FILE entries: $resolved" }
        foreach ($track in $tracks) {
            if ($track -notmatch '^\s*FILE\s+"([^"]+)"\s+\S+\s*$') { throw "Unrecognised CUE entry: $track" }
            $trackPath = Join-Path (Split-Path $resolved) $Matches[1]
            if (!(Test-Path -LiteralPath $trackPath -PathType Leaf)) { throw "Missing disc track: $trackPath" }
        }
    }
}
$hostArgs = @('-python', '-moddir', 'mw2mods', '-log-fileio', '-conf', '.\dosbox-mw2.conf',
    '-set', 'sdl fullscreen=true', '-set', 'sdl fullresolution=desktop', '-set', 'sdl showmenu=false',
    '-set', 'render mod renderer start view=mod-only', '-set', 'render mod renderer auto fps=true',
    '-set', 'render mod renderer host vsync=true', '-set', 'vsync vsyncmode=off', '-set', 'cpu cycles=max')
$dryRun = $args -contains '-DryRun'
$hostArgs += @($args | Where-Object { $_ -ne '-DryRun' })
if ($dryRun) {
    [PSCustomObject]@{ executable = $exe; working_directory = $renderer; arguments = $hostArgs } | ConvertTo-Json -Depth 3
    exit 0
}
$env:MW2_STARTUP_TRACE = '0'
Push-Location $renderer
try {
    # Encode Windows argv explicitly, preserving embedded quotes and trailing
    # backslashes. Wait also for GUI executables before returning to the caller.
    $nativeArgs = @($hostArgs | ForEach-Object {
        '"' + (($_ -replace '(\\*)"', '$1$1\"') -replace '(\\+)$', '$1$1') + '"'
    }) -join ' '
    $process = Start-Process -FilePath $exe -ArgumentList $nativeArgs -WorkingDirectory $renderer -Wait -PassThru -NoNewWindow
    $result = $process.ExitCode
} finally { Pop-Location }
exit $result
