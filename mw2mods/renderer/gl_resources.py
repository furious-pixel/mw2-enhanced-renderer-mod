import moderngl


VERTEX_FORMAT = "3f 1f"
VERTEX_FLOATS = 4
INDEXED_VERTEX_FORMAT = "3f"
INDEXED_VERTEX_FLOATS = 3
MODE4_VERTEX_FORMAT = "3f 1f 1f"
MODE4_VERTEX_FLOATS = 5
BILLBOARD_INSTANCE_FORMAT = "3f 3f 1f /i"
BILLBOARD_INSTANCE_FLOATS = 7
INDEXED_TEXMAP_VERTEX_FORMAT = "3f 2f"
INDEXED_TEXMAP_VERTEX_FLOATS = 5
SCREEN_VERTEX_FORMAT = "2f"


class Mesh:
    def __init__(self, ctx, program, vertices):
        self.vertex_count = 0
        self.buffer = None
        self.vao = None
        self.vertex_count = len(vertices) // 4
        self.buffer = ctx.buffer(vertices.tobytes())
        self.vao = ctx.vertex_array(
            program,
            [(self.buffer, VERTEX_FORMAT, "in_pos", "in_palette_mix")],
        )

    def render(self):
        if self.vertex_count > 0:
            self.vao.render(mode=moderngl.TRIANGLES, vertices=self.vertex_count)

    def release(self):
        if self.vao is not None:
            self.vao.release()
            self.vao = None
        if self.buffer is not None:
            self.buffer.release()
            self.buffer = None

    def __del__(self):
        self.release()


class DynamicMesh:
    def __init__(
        self,
        ctx,
        program,
        attributes,
        render_mode=moderngl.TRIANGLES,
        vertex_format=VERTEX_FORMAT,
        vertex_floats=VERTEX_FLOATS,
    ):
        self.ctx = ctx
        self.program = program
        self.attributes = tuple(attributes)
        self.render_mode = render_mode
        self.vertex_format = vertex_format
        self.vertex_floats = int(vertex_floats)
        self.vertex_count = 0
        self.capacity = 0
        self.buffer = None
        self.vao = None

    def update(self, vertices):
        self.vertex_count = len(vertices) // self.vertex_floats
        if self.vertex_count <= 0:
            self._release_gpu_objects()
            return

        data = _byte_view(vertices)
        if self.buffer is None or data.nbytes > self.capacity:
            self._release_gpu_objects()
            self.capacity = _buffer_capacity(data.nbytes)
            self.buffer = self.ctx.buffer(reserve=self.capacity)
            self.vao = self.ctx.vertex_array(
                self.program,
                [(self.buffer, self.vertex_format, "in_pos", *self.attributes)],
            )

        _replace_buffer_data(self.buffer, data)

    def render(self):
        if self.vertex_count > 0 and self.vao is not None:
            self.vao.render(mode=self.render_mode, vertices=self.vertex_count)

    def _release_gpu_objects(self):
        if self.vao is not None:
            self.vao.release()
            self.vao = None
        if self.buffer is not None:
            self.buffer.release()
            self.buffer = None

    def release(self):
        self._release_gpu_objects()
        self.vertex_count = 0
        self.capacity = 0

    def __del__(self):
        self.release()


class DynamicBillboardMesh(DynamicMesh):
    def __init__(self, ctx, program):
        super().__init__(
            ctx,
            program,
            ("in_other_pos", "in_billboard_flags"),
            vertex_format=BILLBOARD_INSTANCE_FORMAT,
            vertex_floats=BILLBOARD_INSTANCE_FLOATS,
        )
        self.instance_count = 0

    def update(self, instances):
        self.instance_count = len(instances) // BILLBOARD_INSTANCE_FLOATS
        super().update(instances)
        self.vertex_count = self.instance_count * 6

    def render(self):
        if self.instance_count > 0 and self.vao is not None:
            self.vao.render(
                mode=moderngl.TRIANGLES,
                vertices=6,
                instances=self.instance_count,
            )

    def release(self):
        super().release()
        self.instance_count = 0


class DynamicIndexedMesh:
    def __init__(
        self,
        ctx,
        program,
        primitive_uniform,
        attributes=(),
        render_mode=moderngl.TRIANGLES,
        vertex_format=INDEXED_VERTEX_FORMAT,
        vertex_floats=INDEXED_VERTEX_FLOATS,
    ):
        self.ctx = ctx
        self.program = program
        self.primitive_uniform = primitive_uniform
        self.attributes = tuple(attributes)
        self.render_mode = render_mode
        self.vertex_format = vertex_format
        self.vertex_floats = int(vertex_floats)
        self.vertex_count = 0
        self.index_count = 0
        self.primitive_count = 0
        self.vertex_capacity = 0
        self.index_capacity = 0
        self.vertex_buffer = None
        self.owns_vertex_buffer = True
        self.index_buffer = None
        self.primitive_texture = None
        self.primitive_texture_width = 0
        self.vao = None

    def update(self, vertices, indices, primitive_data):
        self.vertex_count = len(vertices) // self.vertex_floats
        self.index_count = len(indices)
        self.primitive_count = len(primitive_data)
        if (
            self.vertex_count <= 0
            or self.index_count <= 0
            or self.primitive_count <= 0
        ):
            self._release_gpu_objects()
            self.vertex_count = 0
            self.index_count = 0
            self.primitive_count = 0
            return

        vertex_data = _byte_view(vertices)
        index_data = _byte_view(indices)
        if (
            self.vertex_buffer is None
            or not self.owns_vertex_buffer
            or self.index_buffer is None
            or vertex_data.nbytes > self.vertex_capacity
            or index_data.nbytes > self.index_capacity
        ):
            self._release_buffer_objects()
            self.vertex_capacity = _buffer_capacity(vertex_data.nbytes)
            self.index_capacity = _buffer_capacity(index_data.nbytes)
            self.vertex_buffer = self.ctx.buffer(reserve=self.vertex_capacity)
            self.owns_vertex_buffer = True
            self.index_buffer = self.ctx.buffer(reserve=self.index_capacity)
            self.vao = self.ctx.vertex_array(
                self.program,
                [(self.vertex_buffer, self.vertex_format, "in_pos", *self.attributes)],
                index_buffer=self.index_buffer,
                index_element_size=2,
            )

        _replace_buffer_data(self.vertex_buffer, vertex_data)
        _replace_buffer_data(self.index_buffer, index_data)
        self._update_primitive_texture(primitive_data)

    def update_shared(self, vertex_buffer, vertex_count, indices, primitive_data):
        self.vertex_count = int(vertex_count)
        self.index_count = len(indices)
        self.primitive_count = len(primitive_data)
        if (
            self.vertex_count <= 0
            or self.index_count <= 0
            or self.primitive_count <= 0
        ):
            self._release_gpu_objects()
            self.vertex_count = 0
            self.index_count = 0
            self.primitive_count = 0
            return

        index_data = _byte_view(indices)
        if (
            self.vertex_buffer is not vertex_buffer
            or self.owns_vertex_buffer
            or self.index_buffer is None
            or index_data.nbytes > self.index_capacity
        ):
            self._release_buffer_objects()
            self.vertex_buffer = vertex_buffer
            self.owns_vertex_buffer = False
            self.index_capacity = _buffer_capacity(index_data.nbytes)
            self.index_buffer = self.ctx.buffer(reserve=self.index_capacity)
            self.vao = self.ctx.vertex_array(
                self.program,
                [(self.vertex_buffer, self.vertex_format, "in_pos", *self.attributes)],
                index_buffer=self.index_buffer,
                index_element_size=4,
            )

        _replace_buffer_data(self.index_buffer, index_data)
        self._update_primitive_texture(primitive_data)

    def _update_primitive_texture(self, primitive_data):
        self.primitive_texture, self.primitive_texture_width = (
            _update_scalar_texture(
                self.ctx,
                self.primitive_texture,
                self.primitive_texture_width,
                primitive_data,
            )
        )

    def render(self):
        if (
            self.index_count > 0
            and self.vao is not None
            and self.primitive_texture is not None
        ):
            self.primitive_texture.use(location=2)
            self.program[self.primitive_uniform].value = 2
            self.vao.render(mode=self.render_mode, vertices=self.index_count)

    def _release_buffer_objects(self):
        if self.vao is not None:
            self.vao.release()
            self.vao = None
        if self.vertex_buffer is not None and self.owns_vertex_buffer:
            self.vertex_buffer.release()
        self.vertex_buffer = None
        self.owns_vertex_buffer = True
        if self.index_buffer is not None:
            self.index_buffer.release()
            self.index_buffer = None

    def _release_gpu_objects(self):
        self._release_buffer_objects()
        if self.primitive_texture is not None:
            self.primitive_texture.release()
            self.primitive_texture = None
        self.vertex_capacity = 0
        self.index_capacity = 0
        self.primitive_texture_width = 0

    def release(self):
        self._release_gpu_objects()
        self.vertex_count = 0
        self.index_count = 0
        self.primitive_count = 0

    def __del__(self):
        self.release()


class SharedDynamicIndexedMeshSet:
    """One shared vertex buffer with independently drawable indexed views."""

    def __init__(
        self,
        ctx,
        program,
        primitive_uniform,
        attributes=(),
        render_mode=moderngl.TRIANGLES,
        vertex_format=INDEXED_VERTEX_FORMAT,
        vertex_floats=INDEXED_VERTEX_FLOATS,
    ):
        self.ctx = ctx
        self.program = program
        self.primitive_uniform = primitive_uniform
        self.attributes = tuple(attributes)
        self.render_mode = render_mode
        self.vertex_format = vertex_format
        self.vertex_floats = int(vertex_floats)
        self.vertex_count = 0
        self.vertex_capacity = 0
        self.vertex_buffer = None
        self.meshes = {}

    def update(self, vertices, grouped_indices, grouped_primitive_data):
        self.vertex_count = len(vertices) // self.vertex_floats
        if self.vertex_count <= 0 or not grouped_indices:
            self.release()
            return

        vertex_data = _byte_view(vertices)
        vertex_buffer_changed = (
            self.vertex_buffer is None or vertex_data.nbytes > self.vertex_capacity
        )
        if vertex_buffer_changed:
            for mesh in self.meshes.values():
                mesh.release()
            self.meshes.clear()
            if self.vertex_buffer is not None:
                self.vertex_buffer.release()
            self.vertex_capacity = _buffer_capacity(vertex_data.nbytes)
            self.vertex_buffer = self.ctx.buffer(reserve=self.vertex_capacity)

        _replace_buffer_data(self.vertex_buffer, vertex_data)

        active_descs = set()
        for desc_idx, indices in grouped_indices.items():
            desc_idx = int(desc_idx)
            active_descs.add(desc_idx)
            mesh = self.meshes.get(desc_idx)
            if mesh is None:
                mesh = DynamicIndexedMesh(
                    self.ctx,
                    self.program,
                    self.primitive_uniform,
                    attributes=self.attributes,
                    render_mode=self.render_mode,
                    vertex_format=self.vertex_format,
                    vertex_floats=self.vertex_floats,
                )
                self.meshes[desc_idx] = mesh
            mesh.update_shared(
                self.vertex_buffer,
                self.vertex_count,
                indices,
                grouped_primitive_data.get(desc_idx, ()),
            )

        for desc_idx in list(self.meshes.keys()):
            if desc_idx in active_descs:
                continue
            self.meshes[desc_idx].release()
            del self.meshes[desc_idx]

    def release(self):
        for mesh in self.meshes.values():
            mesh.release()
        self.meshes.clear()
        if self.vertex_buffer is not None:
            self.vertex_buffer.release()
            self.vertex_buffer = None
        self.vertex_count = 0
        self.vertex_capacity = 0

    def __del__(self):
        self.release()


class DynamicWireframeMesh:
    def __init__(self, ctx, occluder_program, line_program):
        self.ctx = ctx
        self.occluder_program = occluder_program
        self.line_program = line_program
        self.vertex_count = 0
        self.occluder_index_count = 0
        self.line_index_count = 0
        self.vertex_capacity = 0
        self.occluder_index_capacity = 0
        self.line_index_capacity = 0
        self.vertex_buffer = None
        self.occluder_index_buffer = None
        self.line_index_buffer = None
        self.occluder_vao = None
        self.line_vao = None
        self.line_palette_texture = None
        self.line_palette_width = 0

    def update(
        self,
        vertices,
        occluder_indices,
        line_indices,
        line_palettes,
    ):
        self.vertex_count = len(vertices) // INDEXED_VERTEX_FLOATS
        self.occluder_index_count = len(occluder_indices)
        self.line_index_count = len(line_indices)
        line_primitive_count = len(line_palettes)
        if self.line_index_count != line_primitive_count * 2:
            raise ValueError("wireframe line palette count does not match line indices")
        if self.occluder_index_count % 3:
            raise ValueError("wireframe occluder index count is not triangular")
        if self.vertex_count <= 0 or (
            self.occluder_index_count <= 0 and self.line_index_count <= 0
        ):
            self.release()
            return

        vertex_data = _byte_view(vertices)
        occluder_data = _byte_view(occluder_indices)
        line_data = _byte_view(line_indices)
        rebuild = (
            self.vertex_buffer is None
            or vertex_data.nbytes > self.vertex_capacity
            or (
                self.occluder_index_count > 0
                and (
                    self.occluder_index_buffer is None
                    or occluder_data.nbytes > self.occluder_index_capacity
                )
            )
            or (
                self.line_index_count > 0
                and (
                    self.line_index_buffer is None
                    or line_data.nbytes > self.line_index_capacity
                )
            )
        )
        if rebuild:
            self._release_buffer_objects()
            self.vertex_capacity = _buffer_capacity(vertex_data.nbytes)
            self.vertex_buffer = self.ctx.buffer(reserve=self.vertex_capacity)
            if self.occluder_index_count > 0:
                self.occluder_index_capacity = _buffer_capacity(
                    occluder_data.nbytes
                )
                self.occluder_index_buffer = self.ctx.buffer(
                    reserve=self.occluder_index_capacity
                )
                self.occluder_vao = self.ctx.vertex_array(
                    self.occluder_program,
                    [(self.vertex_buffer, INDEXED_VERTEX_FORMAT, "in_pos")],
                    index_buffer=self.occluder_index_buffer,
                    index_element_size=4,
                )
            if self.line_index_count > 0:
                self.line_index_capacity = _buffer_capacity(line_data.nbytes)
                self.line_index_buffer = self.ctx.buffer(
                    reserve=self.line_index_capacity
                )
                self.line_vao = self.ctx.vertex_array(
                    self.line_program,
                    [(self.vertex_buffer, INDEXED_VERTEX_FORMAT, "in_pos")],
                    index_buffer=self.line_index_buffer,
                    index_element_size=4,
                )

        _replace_buffer_data(self.vertex_buffer, vertex_data)
        if self.occluder_index_count > 0:
            _replace_buffer_data(self.occluder_index_buffer, occluder_data)
        if self.line_index_count > 0:
            _replace_buffer_data(self.line_index_buffer, line_data)
        self._update_line_palette_texture(line_palettes)

    def _update_line_palette_texture(self, line_palettes):
        self.line_palette_texture, self.line_palette_width = (
            _update_scalar_texture(
                self.ctx,
                self.line_palette_texture,
                self.line_palette_width,
                line_palettes,
            )
        )

    def render_occluders(self):
        if self.occluder_index_count > 0 and self.occluder_vao is not None:
            self.occluder_vao.render(
                mode=moderngl.TRIANGLES,
                vertices=self.occluder_index_count,
            )

    def render_lines(self):
        if (
            self.line_index_count > 0
            and self.line_vao is not None
            and self.line_palette_texture is not None
        ):
            self.line_palette_texture.use(location=2)
            self.line_program["u_primitive_palette"].value = 2
            self.line_vao.render(
                mode=moderngl.LINES,
                vertices=self.line_index_count,
            )

    def _release_buffer_objects(self):
        if self.occluder_vao is not None:
            self.occluder_vao.release()
            self.occluder_vao = None
        if self.line_vao is not None:
            self.line_vao.release()
            self.line_vao = None
        if self.vertex_buffer is not None:
            self.vertex_buffer.release()
            self.vertex_buffer = None
        if self.occluder_index_buffer is not None:
            self.occluder_index_buffer.release()
            self.occluder_index_buffer = None
        if self.line_index_buffer is not None:
            self.line_index_buffer.release()
            self.line_index_buffer = None
        self.vertex_capacity = 0
        self.occluder_index_capacity = 0
        self.line_index_capacity = 0

    def release(self):
        self._release_buffer_objects()
        if self.line_palette_texture is not None:
            self.line_palette_texture.release()
            self.line_palette_texture = None
        self.line_palette_width = 0
        self.vertex_count = 0
        self.occluder_index_count = 0
        self.line_index_count = 0

    def __del__(self):
        self.release()


class DynamicGeometryResources:
    """One independently owned set of dynamic geometry GPU resources."""

    def __init__(
        self,
        ctx,
        geometry_program,
        indexed_geometry_program,
        wireframe_occluder_program,
        mode4_program,
        textured_program,
        indexed_texmap_program,
    ):
        self.ctx = ctx
        self.textured_program = textured_program
        self.indexed_texmap_program = indexed_texmap_program
        self.dynamic_triangle_mesh = DynamicMesh(
            ctx,
            geometry_program,
            ("in_palette_index",),
        )
        self.dynamic_indexed_triangle_mesh = DynamicIndexedMesh(
            ctx,
            indexed_geometry_program,
            "u_primitive_palette",
        )
        self.dynamic_mode4_mesh = DynamicMesh(
            ctx,
            mode4_program,
            ("in_c_in", "in_contribution"),
            vertex_format=MODE4_VERTEX_FORMAT,
            vertex_floats=MODE4_VERTEX_FLOATS,
        )
        self.dynamic_point_mesh = DynamicMesh(
            ctx,
            geometry_program,
            ("in_palette_index",),
            render_mode=moderngl.POINTS,
        )
        self.dynamic_indexed_wireframe_mesh = DynamicWireframeMesh(
            ctx,
            wireframe_occluder_program,
            indexed_geometry_program,
        )
        self.dynamic_line_mesh = DynamicMesh(
            ctx,
            geometry_program,
            ("in_palette_index",),
            render_mode=moderngl.LINES,
        )
        self.dynamic_billboard_meshes = {}
        self.dynamic_indexed_texmap_mesh_set = SharedDynamicIndexedMeshSet(
            ctx,
            indexed_texmap_program,
            "u_primitive_contribution",
            attributes=("in_uv",),
            vertex_format=INDEXED_TEXMAP_VERTEX_FORMAT,
            vertex_floats=INDEXED_TEXMAP_VERTEX_FLOATS,
        )
        self.dynamic_indexed_texmap_meshes = (
            self.dynamic_indexed_texmap_mesh_set.meshes
        )
        self.grouped_indexed_texmap_meshes = {}
        self.dynamic_rotor_meshes = []
        self.dynamic_geometry_has_vertices = False
        # Texture objects are shared and remain cached after a descriptor stops.
        # This per-owner set records which descriptors the current snapshot
        # actually resolved and therefore permits this geometry owner to draw.
        self.active_texture_descs = frozenset()

    def release(self):
        for mesh in (
            self.dynamic_triangle_mesh,
            self.dynamic_indexed_triangle_mesh,
            self.dynamic_mode4_mesh,
            self.dynamic_point_mesh,
            self.dynamic_indexed_wireframe_mesh,
            self.dynamic_line_mesh,
        ):
            mesh.release()
        for mesh in self.dynamic_billboard_meshes.values():
            mesh.release()
        self.dynamic_billboard_meshes.clear()
        self.dynamic_indexed_texmap_mesh_set.release()
        for mesh in self.grouped_indexed_texmap_meshes.values():
            mesh.release()
        self.grouped_indexed_texmap_meshes.clear()
        for mesh in self.dynamic_rotor_meshes:
            mesh.release()
        self.dynamic_rotor_meshes.clear()
        self.dynamic_geometry_has_vertices = False
        self.active_texture_descs = frozenset()


def mesh_dict_has_vertices(meshes):
    return any(mesh.vertex_count > 0 for mesh in meshes.values())


def _byte_view(values):
    view = memoryview(values)
    if view.format == "B" and view.ndim == 1:
        return view
    return view.cast("B")


def _replace_buffer_data(buffer, data):
    try:
        buffer.orphan()
    except Exception:
        pass
    buffer.write(data)


def _update_scalar_texture(ctx, texture, old_width, values):
    width = len(values)
    if width <= 0:
        if texture is not None:
            texture.release()
        return None, 0
    data = _byte_view(values)
    if texture is None or old_width != width:
        if texture is not None:
            texture.release()
        texture = ctx.texture((width, 1), 1, data=data, dtype="f4")
        texture.filter = (moderngl.NEAREST, moderngl.NEAREST)
        texture.repeat_x = False
        texture.repeat_y = False
    else:
        texture.write(data)
    return texture, width


def _buffer_capacity(size):
    capacity = 4096
    while capacity < size:
        capacity *= 2
    return capacity
