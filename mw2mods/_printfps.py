from mod import modhook

ADDR_FRAME_TIMING_CALL = 0x0002CE60
FPS_WINDOW_SECONDS = 3.0

@modhook("MW2.EXE", ADDR_FRAME_TIMING_CALL, "call")
def printfps(modstate, gamemem):
    if modstate.time < 0.0:
        return

    if not hasattr(modstate, "printfps_window_start_frame"):
        modstate.printfps_window_start_frame = modstate.frame
        modstate.printfps_window_start_time = modstate.time
        return

    window_time = modstate.time - modstate.printfps_window_start_time
    if window_time < FPS_WINDOW_SECONDS:
        return

    window_frames = modstate.frame - modstate.printfps_window_start_frame
    if window_time <= 0.0 or window_frames <= 0:
        modstate.printfps_window_start_frame = modstate.frame
        modstate.printfps_window_start_time = modstate.time
        return

    fps = window_frames / window_time
    print(
        f"MOD: FPS: frame={modstate.frame} time={modstate.time:.3f}s "
        f"frame_delta={modstate.frame_delta:.6f}s "
        f"window={window_time:.3f}s frames={window_frames} avg_fps={fps:.2f}",
        flush=True,
    )

    modstate.printfps_window_start_frame = modstate.frame
    modstate.printfps_window_start_time = modstate.time
