# Audio and presentation diagnostics

`run_cpp_av_audit.ps1` is a development-only manual comparison launcher. It uses this checkout's `bin/dosbox-x.exe`, renderer, and `dosbox-mw2.conf`; it does not depend on an audio worktree. Install a matching host build with `MW2_AV_AUDIT` support (host commit `93dec0229` or later). The original v0.10.0 player host predates these diagnostics.

## Run

From the renderer repository root, close DOSBox-X, select the monitor refresh rate in Windows, then run:

```powershell
powershell.exe -NoProfile -ExecutionPolicy Bypass -File .\tools\run_cpp_av_audit.ps1 -Fps 60 -Vsync on -Cycles max
```

The execution-policy override applies only to that PowerShell process. The launcher uses fullscreen desktop and mod-only output, validates configured mounts and referenced disc paths, and leaves mission selection and gameplay to you. It does not change the monitor refresh rate.

Options:

| Option | Values / purpose |
| --- | --- |
| `-Fps` | 60, 72, 75; default 60 |
| `-Vsync` | on, off; default on |
| `-Cycles` | 250000, max; default 250000 |
| `-AuditOff` | Disable the dedicated audit for overhead comparisons |
| `-DryRun` | Validate paths and print the command without launching |

Use the same mission and similar actions for comparisons. Capture loading and about 30 seconds of gameplay. Exit normally to preserve buffered output and the frame-timing excerpt. A forced stop may lose the tail of the audit or the frame excerpt.

## Output and interpretation

Each run writes uniquely timestamped files under ignored `perf-results/av-audit/` in this checkout. Earlier sessions are preserved:

- `.log`: dedicated audio/scheduler audit (absent with `-AuditOff`).
- `.log.dosbox.log`: ordinary host log.
- `.log.frames.log`: newly appended frame timing, copied after normal process exit when available.

The environment settings are restored when the launcher finishes. Existing older captures remain at their original locations; this tool does not move or delete them.

Check actual display refresh and active swap interval first. Compare complete gameplay windows separately from startup, loading, outro, and shutdown. `emu_ratio` near 1 means guest timer progress matches wall time, not that the guest CPU is fully utilized. `cycles_now` is the effective CPU budget. `discarded_ticks`, audio underruns, inserted silence, and dropped samples distinguish timer loss, empty audio queues, and queue trimming. None alone proves the cause of an audible click.

Use `FRAME_TIMING` guest-frame averages to assess achieved game FPS; presentation counts alone can include repeated frames. Inspect maximum/percentile presentation gaps as well as averages. Audit overhead has not been measured independently; diagnostics add counters and clock reads when enabled.

This script and guide are tracked development tools. The player packager selects runtime tools explicitly and does not include them; repository source archives do include them. No game data or generated captures should be committed.
