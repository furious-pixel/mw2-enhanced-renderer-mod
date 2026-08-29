from .cockpit_effects import CockpitEffectTracker


class EntityLodResourceStore:
    """Mission-generation ownership for decoded exterior entity assets."""

    __slots__ = (
        "state",
        "resource_ids",
        "assets",
        "error",
        "frame",
        "selection_history",
        "model_radii",
        "material_indices",
        "compiled_assets",
        "view_batch_caches",
    )

    def __init__(self):
        self.assets = {}
        self.selection_history = {}
        self.model_radii = {}
        self.material_indices = {}
        self.compiled_assets = {}
        self.view_batch_caches = {}
        self.clear()

    def clear(self):
        self.state = "IDLE"
        self.resource_ids = ()
        self.assets.clear()
        self.error = ""
        self.frame = None
        self.selection_history.clear()
        self.model_radii.clear()
        self.material_indices.clear()
        self.compiled_assets.clear()
        self.view_batch_caches.clear()


class MissionResourceStore:
    """Own renderer CPU caches for one mission generation."""

    __slots__ = (
        "mission_generation",
        "cached_geometry",
        "cached_geometry_signature",
        "static_geometry",
        "dynamic_batches",
        "mesh_assets",
        "topology_volatility",
        "texture_generation",
        "texture_executable_delta",
        "texture_tables_initialized",
        "texture_descriptor_sentinel",
        "texture_init_state",
        "texture_assets",
        "enhanced_imaging_effect_descriptors",
        "texture_preload_state",
        "texture_preload_requests",
        "texture_preload_cursor",
        "texture_preload_retry_counts",
        "texture_preload_bindings",
        "texture_preload_error",
        "hud_texture_preloads",
        "target_key",
        "target_mesh_assets",
        "cockpit_far_depth_fixed",
        "cockpit_radius_fixed",
        "cockpit_effect_tracker",
        "entity_lod",
    )

    def __init__(self):
        self.mission_generation = 0
        self.cached_geometry = None
        self.cached_geometry_signature = None
        self.static_geometry = {}
        self.dynamic_batches = {}
        self.mesh_assets = {}
        self.topology_volatility = {}
        self.texture_generation = -1
        self.texture_assets = {}
        self.reset_texture_state()
        self.target_key = None
        self.target_mesh_assets = {}
        self.cockpit_far_depth_fixed = 0.0
        self.cockpit_radius_fixed = 0.0
        self.cockpit_effect_tracker = CockpitEffectTracker()
        self.entity_lod = EntityLodResourceStore()

    def clear_assets(self):
        self.cached_geometry = None
        self.cached_geometry_signature = None
        self.static_geometry.clear()
        self.dynamic_batches.clear()
        self.mesh_assets.clear()
        self.topology_volatility.clear()
        self.reset_texture_state()
        self.target_key = None
        self.target_mesh_assets.clear()
        self.cockpit_far_depth_fixed = 0.0
        self.cockpit_radius_fixed = 0.0
        self.cockpit_effect_tracker.clear()
        self.entity_lod.clear()

    def reset_texture_state(self):
        self.texture_generation += 1
        self.texture_executable_delta = None
        self.texture_tables_initialized = False
        self.texture_descriptor_sentinel = None
        self.texture_init_state = None
        self.texture_assets.clear()
        self.enhanced_imaging_effect_descriptors = frozenset()
        self._reset_texture_preload()

    def _reset_texture_preload(self):
        self.texture_preload_state = "IDLE"
        self.texture_preload_requests = ()
        self.texture_preload_cursor = 0
        self.texture_preload_retry_counts = {}
        self.texture_preload_bindings = []
        self.texture_preload_error = ""
        self.hud_texture_preloads = ()

    def observe_texture_tables(
        self,
        executable_delta,
        initialized,
        descriptor_sentinel,
        init_state,
    ):
        executable_delta = int(executable_delta)
        initialized = bool(initialized)
        delta_changed = (
            self.texture_executable_delta is not None
            and int(self.texture_executable_delta) != executable_delta
        )
        tables_torn_down = self.texture_tables_initialized and not initialized
        if delta_changed or tables_torn_down:
            self.reset_texture_state()

        self.texture_executable_delta = executable_delta
        self.texture_tables_initialized = initialized
        self.texture_descriptor_sentinel = int(descriptor_sentinel)
        self.texture_init_state = int(init_state)

    def begin_mission(self):
        self.mission_generation += 1
        self.clear_assets()

    def target_assets(self, target_key):
        if self.target_key != target_key:
            self.target_key = target_key
            self.target_mesh_assets.clear()
        return self.target_mesh_assets
