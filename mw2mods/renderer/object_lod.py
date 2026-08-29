from typing import NamedTuple

import numpy as np


ADDR_COMPONENT_DESCRIPTOR_TABLE = 0x001310B0
ADDR_COMPONENT_DESCRIPTOR_COUNT = 0x000A6DB0
COMPONENT_DESCRIPTOR_STRIDE = 0x44
MAX_COMPONENT_DESCRIPTORS = 4096
COMPONENT_DESCRIPTOR_DTYPE = np.dtype(
    {
        "names": (
            "owner_id", "installed_detail", "resource_ids",
            "installed_node", "model_tree_part",
        ),
        "formats": ("<i4", "<i4", ("<i4", (5,)), "<u4", "<u4"),
        "offsets": (0x00, 0x04, 0x08, 0x24, 0x28),
        "itemsize": COMPONENT_DESCRIPTOR_STRIDE,
    }
)


class ComponentDescriptorSnapshot(NamedTuple):
    """One compact read of the live component descriptor table."""

    records: np.ndarray
    by_node: dict
    by_tree: dict

    @property
    def count(self):
        return len(self.records)

def _add_match(matches, key, descriptor_index):
    """Store one unique descriptor index, or -1 for an ambiguous key."""
    matches[key] = -1 if key in matches else int(descriptor_index)


def snapshot_component_descriptors(gamemem):
    count = int(gamemem.read_reloc_i32(ADDR_COMPONENT_DESCRIPTOR_COUNT))
    if count < 0 or count > MAX_COMPONENT_DESCRIPTORS:
        raise ValueError(f"invalid component descriptor count {count}")
    descriptor_bytes = bytes(
        gamemem.read_reloc_bytes(
            ADDR_COMPONENT_DESCRIPTOR_TABLE,
            count * COMPONENT_DESCRIPTOR_STRIDE,
        )
    )
    records = np.frombuffer(
        descriptor_bytes,
        dtype=COMPONENT_DESCRIPTOR_DTYPE,
        count=count,
    )
    by_node = {}
    by_tree = {}
    for descriptor_index in range(count):
        descriptor = records[descriptor_index]
        installed_node = int(descriptor["installed_node"])
        model_tree_part = int(descriptor["model_tree_part"])
        if installed_node:
            _add_match(by_node, installed_node, descriptor_index)
        if model_tree_part:
            _add_match(by_tree, model_tree_part, descriptor_index)
    return ComponentDescriptorSnapshot(
        records,
        by_node,
        by_tree,
    )


def snapshot_component_installed_nodes(gamemem, count):
    """Read only the mutable render-node binding from each descriptor."""
    count = int(count)
    if count < 0 or count > MAX_COMPONENT_DESCRIPTORS:
        raise ValueError(f"invalid component descriptor count {count}")
    if count == 0:
        return np.empty(0, dtype=np.uint32)
    data = bytes(
        gamemem.read_reloc_bytes(
            ADDR_COMPONENT_DESCRIPTOR_TABLE + 0x24,
            (count - 1) * COMPONENT_DESCRIPTOR_STRIDE + 4,
        )
    )
    return np.ndarray(
        (count,),
        dtype="<u4",
        buffer=data,
        strides=(COMPONENT_DESCRIPTOR_STRIDE,),
    ).copy()


def resolve_component_descriptor_index(snapshot, node_addr, entity_ref):
    if snapshot is None:
        return None
    node_match = snapshot.by_node.get(int(node_addr))
    if node_match is not None and node_match >= 0:
        return int(node_match)
    tree_match = snapshot.by_tree.get(int(entity_ref))
    if tree_match is not None and tree_match >= 0:
        return int(tree_match)
    return None
