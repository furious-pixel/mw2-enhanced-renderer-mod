from __future__ import annotations

from array import array
from dataclasses import dataclass
from pathlib import Path

import freetype
import moderngl

from .shaders import load_program


FONT_PATH = (
    Path(__file__).resolve().parent.parent
    / "fonts"
    / "Squarish Sans CT Regular.ttf"
)
DEFAULT_FONT_SIZE = 50
DEFAULT_OVERSAMPLE = 1
SMALL_CAPS_SCALE = 0.8
ATLAS_SIZE = 1024
ATLAS_PADDING = 1
INITIAL_VERTEX_BUFFER_SIZE = 4096


@dataclass(frozen=True, slots=True)
class AtlasGlyph:
    page: int
    width: float
    height: float
    bearing_x: float
    bearing_y: float
    advance: float
    u0: float
    v0: float
    u1: float
    v1: float


@dataclass(frozen=True, slots=True)
class PositionedGlyph:
    glyph: AtlasGlyph
    x: float
    y: float


@dataclass(frozen=True, slots=True)
class GlyphBatch:
    page: int
    vertex_count: int
    vertex_bytes: bytes


@dataclass(frozen=True, slots=True)
class TextLayout:
    width: float
    height: float
    batches: tuple[GlyphBatch, ...]


class TextLayoutSlot:
    __slots__ = ("text", "letter_spacing", "size_px", "layout")

    def __init__(self):
        self.text = None
        self.letter_spacing = 0.0
        self.size_px = 0
        self.layout = None


def _bitmap_bytes(bitmap):
    width = int(bitmap.width)
    rows = int(bitmap.rows)
    pitch = abs(int(bitmap.pitch))
    if width <= 0 or rows <= 0:
        return b""

    data = bytes(bitmap.buffer)
    lines = [data[row * pitch : row * pitch + width] for row in range(rows)]
    if bitmap.pitch < 0:
        lines.reverse()
    return b"".join(lines)


class _GlyphAtlasPage:
    def __init__(self, ctx, size):
        self.size = int(size)
        self.texture = ctx.texture(
            (self.size, self.size),
            components=1,
            data=bytes(self.size * self.size),
            alignment=1,
        )
        self.texture.filter = (moderngl.NEAREST, moderngl.NEAREST)
        self.texture.repeat_x = False
        self.texture.repeat_y = False
        self.next_x = ATLAS_PADDING
        self.next_y = ATLAS_PADDING
        self.row_height = 0

    def place(self, width, height, pixels):
        padded_width = width + ATLAS_PADDING * 2
        padded_height = height + ATLAS_PADDING * 2
        if padded_width > self.size or padded_height > self.size:
            return None
        if self.next_x + padded_width > self.size:
            self.next_x = ATLAS_PADDING
            self.next_y += self.row_height
            self.row_height = 0
        if self.next_y + padded_height > self.size:
            return None

        x = self.next_x + ATLAS_PADDING
        y = self.next_y + ATLAS_PADDING
        self.texture.write(
            pixels,
            viewport=(x, y, width, height),
            alignment=1,
        )
        self.next_x += padded_width
        self.row_height = max(self.row_height, padded_height)
        scale = 1.0 / self.size
        return (
            x * scale,
            y * scale,
            (x + width) * scale,
            (y + height) * scale,
        )

    def release(self):
        self.texture.release()


class _GlyphAtlas:
    def __init__(self, ctx, size=ATLAS_SIZE):
        self.ctx = ctx
        self.size = int(size)
        self.pages = []

    def add(self, width, height, pixels):
        for page_index, page in enumerate(self.pages):
            coordinates = page.place(width, height, pixels)
            if coordinates is not None:
                return page_index, coordinates
        page = _GlyphAtlasPage(self.ctx, self.size)
        self.pages.append(page)
        coordinates = page.place(width, height, pixels)
        if coordinates is None:
            raise ValueError(f"glyph bitmap {width}x{height} exceeds atlas size")
        return len(self.pages) - 1, coordinates

    def release(self):
        for page in self.pages:
            page.release()
        self.pages.clear()


class FreeTypeRasterFont:
    def __init__(self, atlas, font_path, size_px, oversample=1):
        self.atlas = atlas
        self.size_px = int(size_px)
        self.oversample = max(1, int(oversample))
        self.face = freetype.Face(str(font_path))
        self.face.set_pixel_sizes(0, self.size_px * self.oversample)
        self.ascender = self.face.size.ascender / 64.0 / self.oversample
        self.descender = self.face.size.descender / 64.0 / self.oversample
        self.line_height = self.face.size.height / 64.0 / self.oversample
        self.glyphs = {}
        self.kernings = {}

    def glyph(self, character):
        cached = self.glyphs.get(character)
        if cached is not None:
            return cached

        self.face.load_char(
            character,
            freetype.FT_LOAD_RENDER | freetype.FT_LOAD_TARGET_NORMAL,
        )
        slot = self.face.glyph
        bitmap = slot.bitmap
        bitmap_width = int(bitmap.width)
        bitmap_height = int(bitmap.rows)
        if bitmap_width > 0 and bitmap_height > 0:
            page, coordinates = self.atlas.add(
                bitmap_width,
                bitmap_height,
                _bitmap_bytes(bitmap),
            )
        else:
            page = 0
            coordinates = (0.0, 0.0, 0.0, 0.0)

        cached = AtlasGlyph(
            page=page,
            width=bitmap_width / self.oversample,
            height=bitmap_height / self.oversample,
            bearing_x=slot.bitmap_left / self.oversample,
            bearing_y=slot.bitmap_top / self.oversample,
            advance=slot.advance.x / 64.0 / self.oversample,
            u0=coordinates[0],
            v0=coordinates[1],
            u1=coordinates[2],
            v1=coordinates[3],
        )
        self.glyphs[character] = cached
        return cached

    def kerning(self, left, right):
        if not left:
            return 0.0
        key = (left, right)
        cached = self.kernings.get(key)
        if cached is not None:
            return cached
        left_index = self.face.get_char_index(left)
        right_index = self.face.get_char_index(right)
        if not left_index or not right_index:
            value = 0.0
        else:
            value = (
                self.face.get_kerning(left_index, right_index).x
                / 64.0
                / self.oversample
            )
        self.kernings[key] = value
        return value

    def release(self):
        self.glyphs.clear()
        self.kernings.clear()


class FontRenderer:
    def __init__(
        self,
        ctx,
        font_path=FONT_PATH,
        size_px=DEFAULT_FONT_SIZE,
        oversample=DEFAULT_OVERSAMPLE,
    ):
        self.fonts = {}
        self.program = None
        self.vertex_buffer = None
        self.vao = None
        self.atlas = None
        if not Path(font_path).is_file():
            raise FileNotFoundError(font_path)

        self.ctx = ctx
        self.font_path = Path(font_path)
        self.default_size_px = int(size_px)
        self.oversample = max(1, int(oversample))
        self.atlas = _GlyphAtlas(ctx)
        self.program = load_program(ctx, "font")
        self.program["u_glyph"].value = 0
        self.vertex_buffer_size = INITIAL_VERTEX_BUFFER_SIZE
        self.vertex_buffer = ctx.buffer(reserve=self.vertex_buffer_size)
        self.vao = ctx.vertex_array(
            self.program,
            [(self.vertex_buffer, "2f 2f", "in_pos", "in_uv")],
        )

    def _font(self, size_px=None):
        size_px = self.default_size_px if size_px is None else int(size_px)
        font = self.fonts.get(size_px)
        if font is None:
            font = FreeTypeRasterFont(
                self.atlas,
                self.font_path,
                size_px,
                self.oversample,
            )
            self.fonts[size_px] = font
        return font

    def _text_fonts(self, size_px):
        full_font = self._font(size_px)
        small_size_px = max(1, int(round(full_font.size_px * SMALL_CAPS_SCALE)))
        return full_font, self._font(small_size_px)

    @staticmethod
    def _styled_character(character, full_font, small_font):
        uppercase = character.upper()
        if character.islower() and len(uppercase) == 1:
            return uppercase, small_font
        return character, full_font

    @staticmethod
    def _pair_kerning(previous, current):
        if previous is None:
            return 0.0
        previous_character, previous_font = previous
        current_character, current_font = current
        kerning_font = (
            previous_font
            if previous_font.size_px <= current_font.size_px
            else current_font
        )
        return kerning_font.kerning(previous_character, current_character)

    def _layout(self, text, letter_spacing, size_px, cache_slot=None):
        text = str(text)
        resolved_size = self.default_size_px if size_px is None else int(size_px)
        resolved_spacing = float(letter_spacing)
        if (
            cache_slot is not None
            and cache_slot.layout is not None
            and cache_slot.text == text
            and cache_slot.letter_spacing == resolved_spacing
            and cache_slot.size_px == resolved_size
        ):
            return cache_slot.layout

        full_font, small_font = self._text_fonts(resolved_size)
        pen_x = 0.0
        baseline_y = full_font.ascender
        previous = None
        glyphs = []
        for character in text:
            current = self._styled_character(character, full_font, small_font)
            if previous is not None:
                pen_x += self._pair_kerning(previous, current)
                pen_x += resolved_spacing
            styled_character, font = current
            glyph = font.glyph(styled_character)
            if glyph.width > 0.0 and glyph.height > 0.0:
                glyphs.append(
                    PositionedGlyph(
                        glyph,
                        pen_x + glyph.bearing_x,
                        baseline_y - glyph.bearing_y,
                    )
                )
            pen_x += glyph.advance
            previous = current

        batches = []
        if glyphs:
            page = glyphs[0].glyph.page
            vertices = []
            for positioned in glyphs:
                if positioned.glyph.page != page:
                    batches.append(self._glyph_batch(page, vertices))
                    page = positioned.glyph.page
                    vertices = []
                self._append_quad(vertices, positioned)
            batches.append(self._glyph_batch(page, vertices))

        layout = TextLayout(
            width=pen_x,
            height=max(full_font.line_height, full_font.ascender - full_font.descender),
            batches=tuple(batches),
        )
        if cache_slot is not None:
            cache_slot.text = text
            cache_slot.letter_spacing = resolved_spacing
            cache_slot.size_px = resolved_size
            cache_slot.layout = layout
        return layout

    def measure(self, text, letter_spacing=0.0, size_px=None, cache_slot=None):
        layout = self._layout(text, letter_spacing, size_px, cache_slot)
        return layout.width, layout.height

    @staticmethod
    def _append_quad(vertices, positioned):
        glyph = positioned.glyph
        x0 = positioned.x
        y0 = positioned.y
        x1 = x0 + glyph.width
        y1 = y0 + glyph.height
        vertices.extend(
            (
                x0, y0, glyph.u0, glyph.v0,
                x1, y0, glyph.u1, glyph.v0,
                x0, y1, glyph.u0, glyph.v1,
                x0, y1, glyph.u0, glyph.v1,
                x1, y0, glyph.u1, glyph.v0,
                x1, y1, glyph.u1, glyph.v1,
            )
        )

    @staticmethod
    def _glyph_batch(page, vertices):
        return GlyphBatch(
            page=page,
            vertex_count=len(vertices) // 4,
            vertex_bytes=array("f", vertices).tobytes(),
        )

    def _render_batch(self, batch):
        required = len(batch.vertex_bytes)
        if required > self.vertex_buffer_size:
            while self.vertex_buffer_size < required:
                self.vertex_buffer_size *= 2
            self.vertex_buffer.orphan(self.vertex_buffer_size)
        self.vertex_buffer.write(batch.vertex_bytes)
        self.atlas.pages[batch.page].texture.use(location=0)
        self.vao.render(
            mode=moderngl.TRIANGLES,
            vertices=batch.vertex_count,
        )

    def draw_text(
        self,
        text,
        x,
        y,
        viewport_size,
        color=(1.0, 1.0, 1.0, 1.0),
        letter_spacing=0.0,
        size_px=None,
        cache_slot=None,
        horizontal_scale=1.0,
    ):
        layout = self._layout(text, letter_spacing, size_px, cache_slot)
        if not layout.batches:
            return

        self.program["u_viewport_size"].value = tuple(viewport_size)
        self.program["u_origin"].value = (round(float(x)), round(float(y)))
        self.program["u_scale"].value = (float(horizontal_scale), 1.0)
        self.program["u_color"].value = tuple(color)
        for batch in layout.batches:
            self._render_batch(batch)

    def release(self):
        if self.vao is not None:
            self.vao.release()
            self.vao = None
        if self.vertex_buffer is not None:
            self.vertex_buffer.release()
            self.vertex_buffer = None
        if self.program is not None:
            self.program.release()
            self.program = None
        for font in self.fonts.values():
            font.release()
        self.fonts.clear()
        if self.atlas is not None:
            self.atlas.release()
            self.atlas = None

    def __del__(self):
        self.release()
