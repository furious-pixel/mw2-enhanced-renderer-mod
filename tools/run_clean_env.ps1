param(
    [Parameter(Mandatory = $true, Position = 0)]
    [string]$FilePath,

    [Parameter(Position = 1)]
    [string]$CommandLine = ''
)

$startInfo = [System.Diagnostics.ProcessStartInfo]::new()
$startInfo.FileName = $FilePath
$startInfo.UseShellExecute = $false
$startInfo.WorkingDirectory = (Get-Location).Path

$startInfo.Arguments = $CommandLine

# Windows environment-variable names are case-insensitive, but a process
# environment block can still contain both Path and PATH. Some .NET tools,
# including MSBuild's compiler task, reject that block while constructing a
# case-insensitive dictionary. Rebuild it with one entry per Windows name.
$source = [System.Environment]::GetEnvironmentVariables()
$pathValue = $null
foreach ($key in $source.Keys) {
    if ([string]$key -ceq 'PATH') {
        # Codex augments this spelling with its tool directories.
        $pathValue = [string]$source[$key]
        break
    }
}
if ($null -eq $pathValue) {
    foreach ($key in $source.Keys) {
        if ([string]$key -ieq 'Path') {
            $pathValue = [string]$source[$key]
            break
        }
    }
}

$startInfo.Environment.Clear()
$seen = [System.Collections.Generic.HashSet[string]]::new(
    [System.StringComparer]::OrdinalIgnoreCase
)
foreach ($key in $source.Keys) {
    $name = [string]$key
    if ($name -ieq 'Path') {
        continue
    }
    if ($seen.Add($name)) {
        $startInfo.Environment[$name] = [string]$source[$key]
    }
}
if ($null -ne $pathValue) {
    $startInfo.Environment['Path'] = $pathValue
}

try {
    $process = [System.Diagnostics.Process]::Start($startInfo)
    $process.WaitForExit()
    exit $process.ExitCode
} catch {
    Write-Error "Failed to start '$FilePath': $($_.Exception.Message)"
    exit 1
}
