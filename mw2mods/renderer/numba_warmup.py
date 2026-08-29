import threading
import time

from .numba_cache import configure_numba_cache

configure_numba_cache(__package__)

from numba import types

from . import geometry_numba as kernels


def _array(dtype, dimensions, *, readonly=False):
    return types.Array(
        dtype,
        dimensions,
        "C",
        readonly=readonly,
        aligned=True,
    )


F32_1 = _array(types.float32, 1)
I64_1 = _array(types.int64, 1)
I64_2 = _array(types.int64, 2)
I32_1 = _array(types.int32, 1)
I32_2 = _array(types.int32, 2)
I16_1 = _array(types.int16, 1)
U32_1 = _array(types.uint32, 1)
U16_1 = _array(types.uint16, 1)
U16_2 = _array(types.uint16, 2)
U8_1 = _array(types.uint8, 1)
U8_1_READONLY = _array(types.uint8, 1, readonly=True)
U8_2 = _array(types.uint8, 2)


KERNEL_WARMUP_TASKS = (
    (
        "cockpit_bounds_fixed_kernel",
        kernels.cockpit_bounds_fixed_kernel,
        (
            I64_2,
            types.float64,
            types.float64,
            types.float64,
            types.float64,
            types.float64,
            types.float64,
        ),
    ),
    (
        "relocate_cockpit_effect_vertices_kernel",
        kernels.relocate_cockpit_effect_vertices_kernel,
        (
            I64_2,
            types.float64,
            types.float64,
            types.float64,
            types.float64,
            types.float64,
            types.float64,
            types.float64,
        ),
    ),
    (
        "fill_mode3_billboard_instances",
        kernels.fill_mode3_billboard_instances,
        (F32_1, I64_2, I16_1, I16_1, U8_1, U8_1, I32_1),
    ),
    (
        "decode_geometry_vertex_asset",
        kernels.decode_geometry_vertex_asset,
        (
            I64_2,
            I64_2,
            U8_1,
            I32_2,
            U8_1_READONLY,
            types.int64,
            types.int64,
            types.boolean,
            types.boolean,
        ),
    ),
    (
        "transform_geometry_vertex_asset",
        kernels.transform_geometry_vertex_asset,
        (I64_2, I64_2, U8_1_READONLY, types.int64),
    ),
    (
        "transform_geometry_face_normals",
        kernels.transform_geometry_face_normals,
        (I64_2, I64_2, I64_2, U8_1_READONLY),
    ),
    (
        "face_source_headers_equal",
        kernels.face_source_headers_equal,
        (U8_1_READONLY, U8_1_READONLY, types.int64, types.int64),
    ),
    (
        "refresh_deferred_face_normals",
        kernels.refresh_deferred_face_normals,
        (
            U8_1_READONLY,
            I32_1,
            I64_2,
            I32_1,
            I64_2,
            I32_1,
            I64_2,
            types.int64,
        ),
    ),
    (
        "build_face_triangle_offsets",
        kernels.build_face_triangle_offsets,
        (U8_1, I64_1),
    ),
    (
        "build_wireframe_offsets",
        kernels.build_wireframe_offsets,
        (U8_1, U8_1, I64_1, I64_1, I64_1),
    ),
    (
        "fill_indexed_wireframe_buffers",
        kernels.fill_indexed_wireframe_buffers,
        (
            F32_1,
            U32_1,
            U32_1,
            F32_1,
            I32_1,
            U8_1,
            U16_2,
            U8_1,
            U8_1,
            I64_1,
            I64_1,
            I64_1,
            I64_2,
        ),
    ),
    (
        "fill_wireframe_face_palettes",
        kernels.fill_wireframe_face_palettes,
        (U8_1, U16_1, U8_1),
    ),
    (
        "fill_indexed_flat_vertices",
        kernels.fill_indexed_flat_vertices,
        (F32_1, I64_2),
    ),
    (
        "update_indexed_flat_vertices",
        kernels.update_indexed_flat_vertices,
        (F32_1, I64_2, I64_1, I64_1, U8_1),
    ),
    (
        "fill_indexed_flat_indices_and_palettes",
        kernels.fill_indexed_flat_indices_and_palettes,
        (
            U16_1,
            F32_1,
            I32_1,
            U8_1,
            I64_1,
            U8_1,
            U16_2,
            I64_2,
            I64_1,
            I64_1,
            I64_1,
            I64_2,
            I64_1,
            types.int64,
            types.int64,
            types.int64,
            I64_1,
            I64_1,
        ),
    ),
    (
        "update_indexed_flat_palettes",
        kernels.update_indexed_flat_palettes,
        (
            F32_1,
            I32_1,
            U8_1,
            I64_1,
            U8_1,
            U16_2,
            I64_2,
            I64_1,
            I64_1,
            I64_2,
            I64_1,
            types.int64,
            types.int64,
            types.int64,
            I64_1,
            I64_1,
            U8_1,
        ),
    ),
    (
        "fill_mode4_vertices",
        kernels.fill_mode4_vertices,
        (
            F32_1,
            I32_1,
            I64_1,
            U8_1,
            U16_2,
            I64_2,
            I64_1,
            I64_1,
            I64_2,
            U8_1,
            I64_1,
            types.int64,
            types.int64,
        ),
    ),
    (
        "update_mode4_vertices",
        kernels.update_mode4_vertices,
        (
            F32_1,
            I32_1,
            I64_1,
            U8_1,
            U16_2,
            I64_2,
            I64_1,
            I64_1,
            I64_2,
            U8_1,
            I64_1,
            types.int64,
            types.int64,
            U8_1,
        ),
    ),
    (
        "fill_satellite_mode4_vertices_batched",
        kernels.fill_satellite_mode4_vertices_batched,
        (
            F32_1,
            I32_1,
            U8_1,
            U8_1,
            U16_2,
            I64_1,
            I64_1,
            I64_2,
            I64_1,
            I64_1,
            types.int64,
            types.int64,
            types.int64,
        ),
    ),
    (
        "analyze_mode57_faces",
        kernels.analyze_mode57_faces,
        (I32_1, I16_1, U8_1, U8_2, I64_1, I64_1),
    ),
    (
        "assign_mode57_vertex_offsets",
        kernels.assign_mode57_vertex_offsets,
        (I64_1, U8_2, I64_2, I64_1),
    ),
    (
        "build_mode57_desc_offsets",
        kernels.build_mode57_desc_offsets,
        (I64_1, I64_1),
    ),
    (
        "count_mode57_block_desc_entries",
        kernels.count_mode57_block_desc_entries,
        (U8_2,),
    ),
    (
        "fill_mode57_block_desc_entries",
        kernels.fill_mode57_block_desc_entries,
        (U8_2, I32_1, I16_1),
    ),
    (
        "fill_mode57_grouped_vertices",
        kernels.fill_mode57_grouped_vertices,
        (F32_1, I64_2, I32_2, I64_1, I64_1, I64_2, I64_1, I32_1, I16_1),
    ),
    (
        "update_mode57_grouped_vertices",
        kernels.update_mode57_grouped_vertices,
        (
            F32_1,
            I64_2,
            I32_2,
            I64_1,
            I64_1,
            I64_2,
            I64_1,
            I32_1,
            I16_1,
            U8_1,
        ),
    ),
    (
        "fill_mode57_shared_vertices",
        kernels.fill_mode57_shared_vertices,
        (F32_1, I64_2, I32_2),
    ),
    (
        "update_mode57_shared_vertices",
        kernels.update_mode57_shared_vertices,
        (F32_1, I64_2, I32_2, I64_1, I64_1, U8_1),
    ),
    (
        "fill_mode57_grouped_indices_and_contribution",
        kernels.fill_mode57_grouped_indices_and_contribution,
        (
            U16_1,
            F32_1,
            I32_1,
            I16_1,
            U8_1,
            U16_2,
            I64_2,
            I64_1,
            I64_1,
            I64_2,
            I64_1,
            I64_2,
            I64_1,
            types.int64,
            types.int64,
        ),
    ),
    (
        "fill_mode57_shared_indices_and_contribution",
        kernels.fill_mode57_shared_indices_and_contribution,
        (
            U32_1,
            F32_1,
            I32_1,
            I16_1,
            U8_1,
            U16_2,
            I64_2,
            I64_1,
            I64_1,
            I64_1,
            I64_2,
            I64_1,
            types.int64,
            types.int64,
        ),
    ),
    (
        "update_mode57_grouped_contribution",
        kernels.update_mode57_grouped_contribution,
        (
            F32_1,
            I32_1,
            I16_1,
            U8_1,
            U16_2,
            I64_2,
            I64_1,
            I64_1,
            I64_2,
            I64_1,
            I64_2,
            I64_1,
            types.int64,
            types.int64,
            U8_1,
        ),
    ),
    (
        "update_mode57_shared_contribution",
        kernels.update_mode57_shared_contribution,
        (
            F32_1,
            I32_1,
            I16_1,
            U8_1,
            U16_2,
            I64_2,
            I64_1,
            I64_1,
            I64_1,
            I64_2,
            I64_1,
            types.int64,
            types.int64,
            U8_1,
        ),
    ),
)


_warmup_lock = threading.Lock()
_warmup_complete = False


def _assert_manifest_complete():
    manifest_names = {name for name, _dispatcher, _signature in KERNEL_WARMUP_TASKS}
    entry_names = {
        name
        for name, value in vars(kernels).items()
        if not name.startswith("_")
        and hasattr(value, "compile")
        and hasattr(value, "signatures")
    }
    if manifest_names != entry_names:
        missing = sorted(entry_names - manifest_names)
        stale = sorted(manifest_names - entry_names)
        raise RuntimeError(
            "Numba warmup manifest mismatch: "
            f"missing={missing!r} stale={stale!r}"
        )


def _compile_task(task):
    name, dispatcher, signature = task
    dispatcher.compile(signature)
    if signature not in dispatcher.signatures:
        raise RuntimeError(f"Numba dispatcher did not publish signature for {name}")


def _cache_event_count(events):
    return sum(int(count) for count in events.values())


def warmup_numba_kernels():
    global _warmup_complete
    with _warmup_lock:
        if _warmup_complete:
            return 0.0

        _assert_manifest_complete()
        task_count = len(KERNEL_WARMUP_TASKS)
        progress_prefix = "Preparing Numba kernels ..."
        print(f"{progress_prefix} 0/{task_count}", end="", flush=True)
        started = time.perf_counter()
        failures = []
        cache_hits = 0
        cache_misses = 0

        for completed, task in enumerate(KERNEL_WARMUP_TASKS, start=1):
            name, dispatcher, _signature = task
            before_hits = _cache_event_count(dispatcher.stats.cache_hits)
            before_misses = _cache_event_count(dispatcher.stats.cache_misses)
            try:
                _compile_task(task)
            except Exception as exc:
                failures.append((name, exc))
            cache_hits += (
                _cache_event_count(dispatcher.stats.cache_hits) - before_hits
            )
            cache_misses += (
                _cache_event_count(dispatcher.stats.cache_misses)
                - before_misses
            )
            print(
                f"\r{progress_prefix} {completed}/{task_count}",
                end="",
                flush=True,
            )

        elapsed_ms = (time.perf_counter() - started) * 1000.0
        if failures:
            print(
                f" ({elapsed_ms / 1000.0:.2f} s; "
                f"cache hits={cache_hits}, compiled={cache_misses}; failed)",
                flush=True,
            )
            details = "; ".join(
                f"{name}: {type(exc).__name__}: {exc}"
                for name, exc in failures
            )
            raise RuntimeError(f"Numba kernel warmup failed: {details}")

        print(
            f" ({elapsed_ms / 1000.0:.2f} s; "
            f"cache hits={cache_hits}, compiled={cache_misses})",
            flush=True,
        )
        _warmup_complete = True
        return elapsed_ms
