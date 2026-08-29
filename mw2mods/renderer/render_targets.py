import moderngl


class RenderTarget:
    def __init__(self, ctx, sample_scale=1):
        self.ctx = ctx
        self.sample_scale = max(1, int(sample_scale))
        self.size = (0, 0)
        self.render_size = (0, 0)
        self.texture = None
        self.depth = None
        self.fbo = None

    def set_sample_scale(self, sample_scale):
        sample_scale = max(1, int(sample_scale))
        if self.sample_scale == sample_scale:
            return
        self.release()
        self.sample_scale = sample_scale

    def render_size_for(self, size):
        return (
            max(1, int(size[0])) * self.sample_scale,
            max(1, int(size[1])) * self.sample_scale,
        )

    def ensure_size(self, size):
        size = (max(1, int(size[0])), max(1, int(size[1])))
        if self.fbo is not None and self.size == size:
            return
        self.release()
        self.size = size
        self.render_size = self.render_size_for(size)
        try:
            self.texture = self.ctx.texture(
                self.render_size,
                components=4,
                dtype="f1",
            )
            texture_filter = (
                moderngl.LINEAR
                if self.sample_scale > 1
                else moderngl.NEAREST
            )
            self.texture.filter = (texture_filter, texture_filter)
            self.texture.repeat_x = False
            self.texture.repeat_y = False
            self.depth = self.ctx.depth_renderbuffer(self.render_size)
            self.fbo = self.ctx.framebuffer(
                color_attachments=[self.texture],
                depth_attachment=self.depth,
            )
        except Exception:
            self.release()
            raise

    def release(self):
        if self.fbo is not None:
            self.fbo.release()
            self.fbo = None
        if self.depth is not None:
            self.depth.release()
            self.depth = None
        if self.texture is not None:
            self.texture.release()
            self.texture = None
        self.size = (0, 0)
        self.render_size = (0, 0)
