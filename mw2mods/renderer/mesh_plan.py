from dataclasses import dataclass


@dataclass(slots=True, eq=False, repr=False)
class MeshPlan:
    """Compiled, presentation-independent topology and current adapter views."""

    face_ids: object
    face_flags: object
    source_face_flags: object
    face_modes: object
    face_counts: object
    face_indices: object
    face_normals: object
    face_owner_slots: object
    owner_addrs: tuple
    deferred: object = None
    enhanced_wireframe: object = None
    enhanced_effects: object = None
    satellite_source: object = None
    target_flat: object = None
