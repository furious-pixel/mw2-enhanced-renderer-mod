#include "menu.h"

#include "config.h"
#include "font.h"
#include "gl_program.h"
#include "hud.h"
#include "mem.h"
#include "mw2er_internal.h"
#include "presentation.h"
#include "text_util.h"

#include "gl_api.h"
#include <stb_image.h>

#include <algorithm>
#include <cmath>
#include <cstdio>
#include <cstring>
#include <string>
#include <utility>
#include <vector>

namespace {

enum {
    ADDR_HANDLER_LIST = 0x000E4104,
    ADDR_GAME_TICK = 0x000A58C4,
    ADDR_SHORT_MESSAGE_TOP = 0x000A5600,
    ADDR_SHORT_MESSAGE_BOTTOM = 0x000A5624,
    ADDR_OBJECTIVES_VISIBLE = 0x000A6984,
    ADDR_OBJECTIVE_BLOCK_INDEX = 0x000A6988,
    ADDR_OBJECTIVE_BLOCKS = 0x000B5810,
    ADDR_PLAYER_SLOT = 0x000A5918,
    ADDR_HUD_STYLE_OFFSET = 0x000B4E80,
    ADDR_ENTITY_TABLE = 0x00108B00,
    CALLBACK_FORMATTED_LIST = 0x0002FD00,
    CALLBACK_ENUM_LIST = 0x0002FF40,
    CALLBACK_SLIDER = 0x0002F920,
    OBJECTIVE_BLOCK_STRIDE = 0x2E8A,
    OBJECTIVE_RECORD_OFFSET = 0x3A,
    OBJECTIVE_RECORD_STRIDE = 0xF7,
    MENU_HANDLER_ESC = 4,
    MENU_HANDLER_USER = 5,
    MENU_HANDLER_COMMAND = 6,
    MAX_HANDLERS = 64,
    MAX_MENU_ITEMS = 128,
    MAX_MENU_SPRITES = 256,
    MAX_RESOURCE_SHAPES = 256,
    MAX_STRING_BYTES = 256,
    MENU_TEXT_CAPACITY = 769,
    MENU_ITEM_STRIDE = 0x11,
    OBJECTIVE_ROW_COUNT = 128,
    MAX_OBJECTIVE_BLOCKS = 16,
    OBJECTIVE_TITLE_SLOT = 104,
    OBJECTIVE_FOOTER_SLOT = 105,
    OBJECTIVE_ROW_SLOT_BASE = 106,
    MENU_INDENT_SLOT = 618,
    MENU_SLOT_BASE = 619,
    MENU_SLOTS_PER_HANDLER = 2 + MAX_MENU_ITEMS * 2,
    MESSAGE_SLOT_BASE = 1393,
    MESSAGE_TEXTURE_SIZE = 48,
    MESSAGE_BAR_HEIGHT = MESSAGE_TEXTURE_SIZE,
    MESSAGE_LEFT_CAP_WIDTH = 24,
    MESSAGE_RIGHT_CAP_WIDTH = 25,
    MESSAGE_BAR_VERTEX_COUNT = 36,
};

struct Rect { int left, top, right, bottom; };

struct MenuSlider {
    int value;
    int sprites[4];
};

struct MenuItem {
    char text[MENU_TEXT_CAPACITY];
    char prefix[4];
    char value_text[MENU_TEXT_CAPACITY];
    int prefix_x, prefix_y;
    int text_x, text_y;
    int color;
    int has_slider;
    MenuSlider slider;
};

struct MenuPage {
    int valid;
    int handler_id;
    Rect pane;
    int clear_background;
    int draw_border;
    int background_sprite;
    Rect background_window;
    char title[MENU_TEXT_CAPACITY];
    int title_x, title_y;
    int normal_color;
    int marker_x, marker_y;
    int marker_color;
    int show_marker;
    int marker_sprite;
    MenuItem items[MAX_MENU_ITEMS];
    int item_count;
    int selected_index;
};

struct ObjectiveRow {
    char text[256];
    char continuation[256];
    uint8_t objective_class;
    uint8_t state;
    int y;
};

struct Objectives {
    int visible;
    ObjectiveRow rows[OBJECTIVE_ROW_COUNT];
    int row_count;
    char footer[64];
};

struct ShortMessage {
    char text[MENU_TEXT_CAPACITY];
    int text_offset_x, text_offset_y;
};

struct MessageBarVertex {
    float x, y, u, v;
};

struct MessageBarGpu {
    GlProgram program;
    GLuint vao;
    GLuint vbo;
    GLuint texture;
};

struct SpriteCacheEntry {
    uint32_t key;
    Mw2erSprite sprite;
    GLuint texture;
};

struct ResourceShape {
    int resource;
    uint32_t shape;
};

struct MenuState {
    MenuPage pages[3];
    Objectives objectives;
    ShortMessage messages[2];
    SpriteCacheEntry sprites[MAX_MENU_SPRITES];
    int sprite_count;
    ResourceShape resource_shapes[MAX_RESOURCE_SHAPES];
    int resource_shape_count;
    // Retired names are drained before any new sprite texture is uploaded.
    GLuint retired_textures[MAX_MENU_SPRITES];
    int retired_count;
};

static MenuState g_menu;
static MessageBarGpu g_message_bar;

static int handler_index(int handler_id)
{
    return handler_id >= MENU_HANDLER_ESC && handler_id <= MENU_HANDLER_COMMAND
        ? handler_id - MENU_HANDLER_ESC : -1;
}

static int sane_rect(const Rect &rect)
{
    return rect.right > rect.left && rect.bottom > rect.top &&
           rect.right - rect.left <= 4096 && rect.bottom - rect.top <= 4096;
}

static void read_string(const Mem &mem, uint32_t address,
                        char *out, int capacity)
{
    if (!out || capacity <= 0) return;
    out[0] = '\0';
    if (!address) return;
    const uint8_t *raw = mem.view(address, MAX_STRING_BYTES);
    if (raw) mw2er_cp437_to_utf8(raw, MAX_STRING_BYTES, out, capacity);
}

static void read_reloc_string(const Mem &mem, uint32_t reloc, int bytes,
                              char *out, int capacity)
{
    if (!out || capacity <= 0) return;
    out[0] = '\0';
    const uint8_t *raw = mem.view(mem.rt(reloc), bytes);
    if (raw) mw2er_cp437_to_utf8(raw, bytes, out, capacity);
}

static uint32_t point_entity(const Mem &mem, uint32_t context)
{
    const int active_index = mem.i32_rel(ADDR_PLAYER_SLOT);
    if (active_index < 0 || active_index >= 4096) return 0;
    const uint32_t active = mem.u32_rel(ADDR_ENTITY_TABLE + active_index * 4u);
    if (!active) return 0;
    const int active_group = mem.i32(active + 0x08);
    const int count = mem.i32_rel(0x000A6270);
    if (count <= 0 || count > 4096) return 0;
    for (int i = 0; i < count; ++i) {
        const uint32_t entity = mem.u32_rel(ADDR_ENTITY_TABLE + i * 4u);
        if (entity && mem.i32(entity + 0x08) == active_group &&
            mem.u32(entity + 0x0C) == context) return entity;
    }
    return 0;
}

static int known_getter_value(const Mem &mem, uint32_t getter,
                              uint32_t context, int cached)
{
    if (getter == 0x00030CF0) {
        if (context == 0x9D) return 0;
        if (context == 0x9E) return mem.u8_rel(0x000B56A9);
        if (context == 0x9F) return mem.i32_rel(0x000A7120) == 1;
        if (context == 0x13) return mem.i32_rel(0x000A6314);
        if (context == 0x40) return mem.i32_rel(0x000A8390);
        if (context == 0x3C) return mem.i32_rel(0x000A6278);
        return 0;
    }
    if (getter == 0x00030A70)
        return context < 5 ? mem.i32_rel(0x000A5A4C + context * 4u) : 7;
    if (getter == 0x00030A90) {
        const int team = mem.i32_rel(0x000A633C);
        return team >= 0 && team < 0x10
            ? mem.i32_rel(0x0010B632 + team * 0x26u) : 0;
    }
    if (getter == 0x00030AD0) {
        const uint32_t entity = point_entity(mem, context);
        return entity ? mem.i16(entity + 0x146) + 1 : 0;
    }
    if (getter == 0x000307A0)
        return std::clamp(mem.i32_rel(0x000A5828) * (0x10000 / 15), 0, 0x10000);
    if (getter == 0x0005B000) {
        const uint32_t addresses[3] = {0x000A7ED0, 0x000A7ED4, 0x000A7ED8};
        return context < 3 ? mem.i32_rel(addresses[context]) : cached;
    }
    if (getter == 0x00030E90) {
        const int flags = mem.i32_rel(0x000A713C);
        return (flags & 0x100) == 0 || (flags & 0x200) == 0;
    }
    if (getter == 0x00030EF0) {
        const int flags = mem.i32_rel(0x000A713C);
        return (flags & 0x800) == 0 || mem.i32_rel(0x000A5BA0) != 0;
    }
    if (getter == 0x00030F50)
        return mem.i32_rel(0x000A7190) == 1 || mem.i32_rel(0x000A7138) == 0;
    if (getter == 0x00030FB0) {
        const uint32_t config = mem.u32_rel(0x000A7F08);
        return config ? mem.i32(config + 0x20) : cached;
    }
    if (getter == 0x0004E5B0) return mem.u8_rel(0x000A6DAC);
    return cached;
}

static void append_point_suffix(const Mem &mem, uint32_t context,
                                char *out, int capacity)
{
    const uint32_t entity = point_entity(mem, context);
    if (!entity || mem.i16(entity + 0x146) == 5) return;
    const uint16_t tag = mem.u16(entity + 0x14A);
    const uint32_t kind = tag & 0x0F00u;
    const uint32_t index = tag & 0xFFu;
    char suffix[MENU_TEXT_CAPACITY] = {};
    if (kind == 0x0100u)
        read_reloc_string(mem, 0x00108BF0 + index * 0x54u + 0x28,
                          64, suffix, sizeof(suffix));
    else if (kind == 0x0200u) {
        const uint32_t target = mem.u32_rel(ADDR_ENTITY_TABLE + index * 4u);
        if (target) read_string(mem, target + 0xE8, suffix, sizeof(suffix));
    } else if (kind == 0x0400u)
        read_reloc_string(mem, 0x00104B80 + index * 0x40u + 0x14,
                          44, suffix, sizeof(suffix));
    if (suffix[0]) {
        const size_t used = std::strlen(out);
        if (used < (size_t)capacity - 1)
            snprintf(out + used, capacity - used, "%s", suffix);
    }
}

static uint32_t resource_shape(const Mem &mem, int resource)
{
    for (int i = 0; i < g_menu.resource_shape_count; ++i)
        if (g_menu.resource_shapes[i].resource == resource)
            return g_menu.resource_shapes[i].shape;
    const uint32_t shape = mw2er_resolve_cached_shape(mem, resource);
    if (shape && g_menu.resource_shape_count < MAX_RESOURCE_SHAPES)
        g_menu.resource_shapes[g_menu.resource_shape_count++] = {resource, shape};
    return shape;
}

static int sprite_ref(const Mem &mem, uint32_t key)
{
    if (!key) return -1;
    for (int i = 0; i < g_menu.sprite_count; ++i)
        if (g_menu.sprites[i].key == key) return i;
    if (g_menu.sprite_count >= MAX_MENU_SPRITES) return -1;
    Mw2erSprite sprite;
    if (!mw2er_decode_runtime_sprite(mem, key, 0, &sprite)) return -1;
    SpriteCacheEntry &entry = g_menu.sprites[g_menu.sprite_count];
    entry.key = key;
    entry.sprite = std::move(sprite);
    entry.texture = 0;
    return g_menu.sprite_count++;
}

static void decode_slider(const Mem &mem, uint32_t style, uint32_t getter,
                          uint32_t context, int cached, int use_getter,
                          MenuSlider &slider)
{
    int value;
    if (getter == 0x000307A0)
        value = std::clamp(mem.i32_rel(0x000A582C), 0, 15) * (0x10000 / 15);
    else
        value = use_getter
            ? known_getter_value(mem, getter, context, cached) : cached;
    slider.value = std::clamp(value, 0, 0x10000);
    std::fill(std::begin(slider.sprites), std::end(slider.sprites), -1);
    if (!style) return;
    const int style_offset = mem.u8_rel(ADDR_HUD_STYLE_OFFSET);
    const uint32_t offsets[4] = {0x00, 0x08, 0x10, 0x18};
    for (int i = 0; i < 4; ++i) {
        const int resource = mem.i32(style + offsets[i]) + style_offset;
        slider.sprites[i] = sprite_ref(mem, resource_shape(mem, resource));
    }
}

static void decode_callback_value(const Mem &mem, uint32_t callback,
                                  uint32_t parameter, MenuItem &item)
{
    if (!parameter || (callback != CALLBACK_FORMATTED_LIST &&
        callback != CALLBACK_ENUM_LIST && callback != CALLBACK_SLIDER)) return;
    const uint8_t *data = mem.view(parameter, 0x18);
    if (!data) return;
    const int cached = mem.i32(parameter + 0x04);
    const uint32_t table = mem.u32(parameter + 0x08);
    const uint32_t context = mem.u32(parameter + 0x0C);
    const uint32_t getter_pointer = mem.u32(parameter + 0x14);
    const uint32_t getter = getter_pointer ? getter_pointer - mem.delta : 0;
    const int use_getter = (data[0] & 1) && getter_pointer;
    if (callback == CALLBACK_SLIDER) {
        item.has_slider = 1;
        decode_slider(mem, table, getter, context, cached,
                      use_getter, item.slider);
        return;
    }
    if (!table) return;
    const uint32_t formatter_pointer = mem.u32(table);
    const int count = mem.i32(table + 4);
    if (count <= 0 || count > 256) return;
    const int raw_value = use_getter
        ? known_getter_value(mem, getter, context, cached) : cached;
    const int choice = ((raw_value % count) + count) % count;
    read_string(mem, mem.u32(table + 8 + choice * 4u),
                item.value_text, sizeof(item.value_text));
    if (callback == CALLBACK_FORMATTED_LIST && formatter_pointer &&
        formatter_pointer - mem.delta == 0x00030C60)
        append_point_suffix(mem, context, item.value_text,
                            sizeof(item.value_text));
}

static int snapshot_page(const Mem &mem, uint32_t handler_data,
                         int handler_id, int allow_initial, MenuPage &out)
{
    const uint32_t pane_address = mem.u32(handler_data);
    const uint32_t stack = mem.u32(handler_data + 0x05);
    const int depth = mem.i32(handler_data + 0x09);
    uint32_t page = 0;
    if (stack && depth > 0 && depth <= 128)
        page = mem.u32(stack + (uint32_t)(depth - 1) * 4u);
    else if (allow_initial)
        page = mem.u32(handler_data + 0x65);
    if (!pane_address || !page) return 0;
    Rect pane = {mem.i32(pane_address + 4), mem.i32(pane_address + 8),
                 mem.i32(pane_address + 12), mem.i32(pane_address + 16)};
    if (!sane_rect(pane)) return 0;

    const int item_count = mem.i32(page + 0x09);
    const int selected_index = mem.i32(page + 0x0D);
    if (item_count < 0 || item_count > MAX_MENU_ITEMS) return 0;
    MenuPage &result = out;
    result.valid = 1;
    result.handler_id = handler_id;
    result.pane = pane;
    result.marker_sprite = -1;
    result.background_sprite = -1;
    result.item_count = 0;
    result.selected_index = selected_index;
    const uint8_t flags = mem.u8(handler_data + 0x04);
    result.clear_background = (flags & 0x20) != 0;
    result.draw_border = (flags & 0x04) != 0;
    result.normal_color = mem.u8(handler_data + 0x31);
    result.marker_color = mem.u8(handler_data + 0x35);
    const int visual_count = mem.i32(handler_data + 0x39);
    const int line_spacing = mem.i32(handler_data + 0x41);
    result.title_x = pane.left + mem.i32(handler_data + 0x45);
    result.title_y = pane.top + mem.i32(handler_data + 0x49);
    const int marker_x = mem.i32(handler_data + 0x4D);
    const int marker_y = mem.i32(handler_data + 0x51);
    const int prefix_x = mem.i32(handler_data + 0x55);
    const int prefix_y = mem.i32(handler_data + 0x59);
    const int text_x = mem.i32(handler_data + 0x5D);
    const int text_y = mem.i32(handler_data + 0x61);
    read_string(mem, mem.u32(page + 0x01), result.title, sizeof(result.title));

    const uint32_t draw_window = mem.u32(handler_data + 0x15);
    if (draw_window) {
        result.background_window = {
            mem.i32(draw_window + 4), mem.i32(draw_window + 8),
            mem.i32(draw_window + 12), mem.i32(draw_window + 16)};
        if (!sane_rect(result.background_window))
            result.background_window = pane;
    } else {
        result.background_window = pane;
    }
    const int background_base = mem.i32(handler_data + 0x0D);
    const uint32_t background_shape = mem.u32(handler_data + 0x11);
    if (background_base != -1 && background_shape)
        result.background_sprite = sprite_ref(mem, background_shape);
    const int marker_base = mem.i32(handler_data + 0x19);
    const uint32_t marker_shape = mem.u32(handler_data + 0x1D);
    result.show_marker = marker_base != -1 && marker_shape && !(flags & 0x08);
    if (result.show_marker) result.marker_sprite = sprite_ref(mem, marker_shape);

    int last_back = -1;
    for (int i = 0; i < item_count; ++i) {
        const int type = mem.u8(page + 0x15 + i * MENU_ITEM_STRIDE);
        if (type == 2 || type == 6) last_back = i;
    }
    int selected_visual = selected_index;
    if (!(flags & 0x08) && selected_index == last_back && visual_count > 0)
        selected_visual = visual_count - 1;
    result.marker_x = pane.left + marker_x;
    result.marker_y = pane.top + marker_y + selected_visual * line_spacing;

    int display_number = 1;
    for (int i = 0; i < item_count; ++i) {
        const uint32_t address = page + 0x15 + i * MENU_ITEM_STRIDE;
        const int type = mem.u8(address);
        int visual_index = i;
        if (!(flags & 0x08) && i == last_back && visual_count > 0)
            visual_index = visual_count - 1;
        MenuItem &item = result.items[result.item_count++];
        item.text[0] = '\0';
        item.prefix[0] = '\0';
        item.value_text[0] = '\0';
        item.has_slider = 0;
        std::fill(std::begin(item.slider.sprites),
                  std::end(item.slider.sprites), -1);
        read_string(mem, mem.u32(address + 1), item.text, sizeof(item.text));
        if (handler_id == MENU_HANDLER_USER &&
            std::strcmp(item.text, "Image Emhancement") == 0)
            snprintf(item.text, sizeof(item.text), "Image Enhancement");
        if (type == 2 || type == 6)
            snprintf(item.prefix, sizeof(item.prefix), "0");
        else if (type != 3) {
            snprintf(item.prefix, sizeof(item.prefix), "%d", display_number % 10);
            ++display_number;
        }
        item.color = i == selected_index
            ? result.marker_color : result.normal_color;
        item.prefix_x = pane.left + prefix_x;
        item.prefix_y = pane.top + prefix_y + visual_index * line_spacing;
        item.text_x = pane.left + text_x;
        item.text_y = pane.top + text_y + visual_index * line_spacing;
        const uint32_t callback_pointer = mem.u32(address + 5);
        const uint32_t callback = callback_pointer
            ? callback_pointer - mem.delta : 0;
        decode_callback_value(mem, callback, mem.u32(address + 9), item);
    }
    return 1;
}

static void format_ticks(int64_t ticks, int hundredths,
                         char *out, int capacity)
{
    const double seconds = std::max(0.0, ticks / 182.0);
    const int hours = (int)(seconds / 3600.0);
    const int minutes = (int)(seconds / 60.0) % 60;
    const int whole_seconds = (int)seconds % 60;
    if (hundredths) {
        const int fraction = (int)((seconds - (int64_t)seconds) * 100.0);
        snprintf(out, capacity, "%02d:%02d:%02d.%02d",
                 hours, minutes, whole_seconds, fraction);
    } else {
        snprintf(out, capacity, "%02d:%02d:%02d",
                 hours, minutes, whole_seconds);
    }
}

static void capture_objectives(const Mem &mem)
{
    Objectives &objectives = g_menu.objectives;
    objectives.visible = 0;
    objectives.row_count = 0;
    objectives.footer[0] = '\0';
    if (!mw2er_hud_objectives_available() ||
        mem.i32_rel(ADDR_OBJECTIVES_VISIBLE) == 0) return;
    const int block_index = mem.i32_rel(ADDR_OBJECTIVE_BLOCK_INDEX);
    if (block_index < 0 || block_index >= MAX_OBJECTIVE_BLOCKS) return;
    const uint32_t block = ADDR_OBJECTIVE_BLOCKS +
        (uint32_t)block_index * OBJECTIVE_BLOCK_STRIDE;
    const int count = mem.i32_rel(block);
    if (count < 0 || count > OBJECTIVE_ROW_COUNT) return;
    const uint32_t classes[5] = {1, 2, 4, 0, 8};
    int y = 291;
    for (uint32_t objective_class : classes) {
        for (int i = 0; i < count; ++i) {
            const uint32_t record = block + OBJECTIVE_RECORD_OFFSET +
                (uint32_t)i * OBJECTIVE_RECORD_STRIDE;
            if (!mem.u8_rel(record + 0x5C) ||
                mem.u8_rel(record + 0x01) != objective_class) continue;
            if (objectives.row_count >= OBJECTIVE_ROW_COUNT) break;
            ObjectiveRow &row = objectives.rows[objectives.row_count++];
            row.text[0] = row.continuation[0] = '\0';
            row.objective_class = (uint8_t)objective_class;
            row.y = y;
            row.state = mem.u8_rel(record);
            const uint8_t *primary = mem.view(mem.rt(record + 0x95), 0x20);
            if (primary) {
                int length = 0;
                while (length < 0x20 && primary[length]) ++length;
                mw2er_cp437_to_utf8(primary, length, row.text, sizeof(row.text));
                if (length == 0x20) {
                    const uint8_t *continuation =
                        mem.view(mem.rt(record + 0xB5), 0x42);
                    if (continuation)
                        mw2er_cp437_to_utf8(continuation, 0x42,
                            row.continuation, sizeof(row.continuation));
                }
            }
            y += row.continuation[0] ? 28 : 14;
        }
    }
    const int selector = mem.u8_rel(block + 0x38);
    const int64_t current = mem.i32_rel(ADDR_GAME_TICK);
    const int64_t start = mem.i32_rel(block + 0x04);
    const int64_t duration = mem.i32_rel(block + 0x0C);
    const int64_t elapsed = current - start * 0xB6;
    char time[32];
    if (selector == 0) {
        if (duration > 0) {
            format_ticks((duration + start) * 0xB6 - current,
                         1, time, sizeof(time));
            snprintf(objectives.footer, sizeof(objectives.footer),
                     "Time Remaining: %s", time);
        } else {
            format_ticks(elapsed, 0, time, sizeof(time));
            snprintf(objectives.footer, sizeof(objectives.footer),
                     "Elapsed Time: %s", time);
        }
    } else if (selector == 2 || selector == 3 || selector == 4) {
        format_ticks(elapsed, 0, time, sizeof(time));
        snprintf(objectives.footer, sizeof(objectives.footer), "%s%s",
            selector == 2 ? "Successful at " :
            selector == 3 ? "Out of time at " : "Failed at ", time);
    }
    objectives.visible = 1;
}

static void capture_short_messages(const Mem &mem)
{
    const uint32_t records[2] = {
        ADDR_SHORT_MESSAGE_TOP, ADDR_SHORT_MESSAGE_BOTTOM};
    const int current_tick = mem.i32_rel(ADDR_GAME_TICK);
    for (int i = 0; i < 2; ++i) {
        ShortMessage &message = g_menu.messages[i];
        message.text[0] = '\0';
        const uint32_t record = records[i];
        if (!mem.i32_rel(record + 0x0C) ||
            current_tick >= mem.i32_rel(record + 0x1C)) continue;
        read_string(mem, mem.u32_rel(record),
                    message.text, sizeof(message.text));
        if (!message.text[0]) continue;
        message.text_offset_x = 6 + mem.i32_rel(record + 0x04);
        message.text_offset_y = mem.i32_rel(record + 0x08);
    }
}

static void capture_handlers(const Mem &mem, int handler_mask,
                             int include_pending)
{
    int seen_mask = 0;
    uint32_t visited[MAX_HANDLERS];
    int visited_count = 0;
    uint32_t handler = mem.u32_rel(ADDR_HANDLER_LIST);
    while (handler && visited_count < MAX_HANDLERS) {
        int duplicate = 0;
        for (int i = 0; i < visited_count; ++i)
            if (visited[i] == handler) { duplicate = 1; break; }
        if (duplicate) break;
        visited[visited_count++] = handler;
        const int id = mem.i32(handler);
        const int index = handler_index(id);
        const int current_state = mem.u8(handler + 4);
        const int target_state = mem.u8(handler + 5);
        const uint32_t data = mem.u32(handler + 6);
        if (index >= 0 && (handler_mask & (1 << index))) {
            seen_mask |= 1 << index;
            int active = current_state != 0 || target_state != 0;
            if (!active && data) {
                const uint32_t stack = mem.u32(data + 5);
                active = stack && mem.i32(data + 9) > 0;
            }
            int captured = 0;
            if (data && current_state == 1)
                captured = snapshot_page(mem, data, id, 0, g_menu.pages[index]);
            else if (data && include_pending && current_state == 0 &&
                     target_state == 1)
                captured = snapshot_page(mem, data, id, 1, g_menu.pages[index]);
            if (!captured && !active) g_menu.pages[index].valid = 0;
        }
        if ((seen_mask & handler_mask) == handler_mask) break;
        handler = mem.u32(handler + 0x0E);
    }
    for (int i = 0; i < 3; ++i)
        if ((handler_mask & (1 << i)) && !(seen_mask & (1 << i)))
            g_menu.pages[i].valid = 0;
}

static void palette_color(const uint8_t *palette, int index, float out[4])
{
    index = std::clamp(index, 0, 255);
    out[0] = palette[index * 3] / 255.0f;
    out[1] = palette[index * 3 + 1] / 255.0f;
    out[2] = palette[index * 3 + 2] / 255.0f;
    out[3] = 1.0f;
}

static void append_rect(std::vector<Mw2erHudVertex> &vertices,
                        float left, float top, float right, float bottom,
                        const float color[4])
{
    if (right <= left || bottom <= top) return;
    vertices.push_back({left, top, color[0], color[1], color[2], color[3]});
    vertices.push_back({right, top, color[0], color[1], color[2], color[3]});
    vertices.push_back({left, bottom, color[0], color[1], color[2], color[3]});
    vertices.push_back({left, bottom, color[0], color[1], color[2], color[3]});
    vertices.push_back({right, top, color[0], color[1], color[2], color[3]});
    vertices.push_back({right, bottom, color[0], color[1], color[2], color[3]});
}

static void append_border(std::vector<Mw2erHudVertex> &vertices,
                          float left, float top, float right, float bottom,
                          const float color[4])
{
    append_rect(vertices, left, top, right, top + 1.0f, color);
    append_rect(vertices, left, bottom - 1.0f, right, bottom, color);
    append_rect(vertices, left, top + 1.0f, left + 1.0f, bottom - 1.0f, color);
    append_rect(vertices, right - 1.0f, top + 1.0f, right, bottom - 1.0f, color);
}

static int menu_slot(int handler_id, int role, int item)
{
    const int base = MENU_SLOT_BASE +
        handler_index(handler_id) * MENU_SLOTS_PER_HANDLER;
    if (role == 1) return base;
    if (role == 4) return base + 1;
    return base + 2 + item * 2 + (role == 3);
}

static int draw_text(int slot, const char *text, float x, float y,
                     int color_index, const uint8_t *palette,
                     int width, int height, int opaque)
{
    if (!text[0]) return 1;
    float color[4];
    palette_color(palette, color_index, color);
    return mw2er_font_draw(slot, text, 16, 0.0f, x, y, 1.0f,
                           color, width, height, opaque) == MW2ER_OK;
}

static int ensure_message_bar_resources()
{
    if (g_message_bar.program.ok() && g_message_bar.vao &&
        g_message_bar.vbo && g_message_bar.texture) return 1;
    if (!g_message_bar.program.ok()) {
        const std::string dir = mw2er_shader_dir();
        if (!g_message_bar.program.load((dir + "/message_bar.vert").c_str(),
                                        (dir + "/message_bar.frag").c_str()))
            return 0;
    }
    if (!g_message_bar.texture) {
        const std::string path = std::string(mw2er_mod_dir()) +
            "/textures/msg_bar_tex_dark.png";
        int image_width = 0, image_height = 0;
        stbi_uc *pixels = stbi_load(path.c_str(), &image_width, &image_height,
                                    nullptr, 3);
        if (!pixels) {
            const char *reason = stbi_failure_reason();
            char error[256];
            snprintf(error, sizeof(error),
                     "message bar texture load failed: %s",
                     reason ? reason : "unknown error");
            mw2er_set_error(error);
            return 0;
        }
        if (image_width != MESSAGE_TEXTURE_SIZE ||
            image_height != MESSAGE_TEXTURE_SIZE) {
            stbi_image_free(pixels);
            mw2er_set_error("message bar texture must be 48x48");
            return 0;
        }
        glGenTextures(1, &g_message_bar.texture);
        glBindTexture(GL_TEXTURE_2D, g_message_bar.texture);
        glPixelStorei(GL_UNPACK_ALIGNMENT, 1);
        glTexImage2D(GL_TEXTURE_2D, 0, GL_RGB8, image_width, image_height, 0,
                     GL_RGB, GL_UNSIGNED_BYTE, pixels);
        glPixelStorei(GL_UNPACK_ALIGNMENT, 4);
        glTexParameteri(GL_TEXTURE_2D, GL_TEXTURE_MIN_FILTER, GL_NEAREST);
        glTexParameteri(GL_TEXTURE_2D, GL_TEXTURE_MAG_FILTER, GL_NEAREST);
        glTexParameteri(GL_TEXTURE_2D, GL_TEXTURE_WRAP_S, GL_CLAMP_TO_EDGE);
        glTexParameteri(GL_TEXTURE_2D, GL_TEXTURE_WRAP_T, GL_CLAMP_TO_EDGE);
        glBindTexture(GL_TEXTURE_2D, 0);
        stbi_image_free(pixels);
    }
    if (!g_message_bar.vao) glGenVertexArrays(1, &g_message_bar.vao);
    if (!g_message_bar.vbo) glGenBuffers(1, &g_message_bar.vbo);
    glBindVertexArray(g_message_bar.vao);
    glBindBuffer(GL_ARRAY_BUFFER, g_message_bar.vbo);
    glBufferData(GL_ARRAY_BUFFER,
                 MESSAGE_BAR_VERTEX_COUNT * sizeof(MessageBarVertex),
                 nullptr, GL_STREAM_DRAW);
    glEnableVertexAttribArray(0);
    glVertexAttribPointer(0, 2, GL_FLOAT, GL_FALSE,
                          sizeof(MessageBarVertex), nullptr);
    glEnableVertexAttribArray(1);
    glVertexAttribPointer(1, 2, GL_FLOAT, GL_FALSE,
                          sizeof(MessageBarVertex),
                          (const void *)(2 * sizeof(float)));
    glBindBuffer(GL_ARRAY_BUFFER, 0);
    glBindVertexArray(0);
    return 1;
}

static void append_message_bar(MessageBarVertex *vertices, int &count,
                               float left, float top, float width)
{
    const float center_u = (float)MESSAGE_LEFT_CAP_WIDTH /
        (MESSAGE_TEXTURE_SIZE - 1);
    const float xs[4] = {left, left + MESSAGE_LEFT_CAP_WIDTH,
                         left + width - MESSAGE_RIGHT_CAP_WIDTH, left + width};
    const float us[4] = {0.0f, center_u, center_u, 1.0f};
    const float bottom = top + MESSAGE_BAR_HEIGHT;
    for (int i = 0; i < 3; ++i) {
        const float x0 = xs[i], x1 = xs[i + 1];
        const float u0 = us[i], u1 = us[i + 1];
        vertices[count++] = {x0, top, u0, 0.0f};
        vertices[count++] = {x1, top, u1, 0.0f};
        vertices[count++] = {x0, bottom, u0, 1.0f};
        vertices[count++] = {x0, bottom, u0, 1.0f};
        vertices[count++] = {x1, top, u1, 0.0f};
        vertices[count++] = {x1, bottom, u1, 1.0f};
    }
}

static int render_short_messages(const uint8_t *palette,
                                 int width, int height)
{
    if (!g_menu.messages[0].text[0] && !g_menu.messages[1].text[0]) return 1;
    if (!ensure_message_bar_resources()) return 0;
    const float bar_width = 1024.0f * height / 768.0f;
    const float bar_left = (width - bar_width) * 0.5f;
    MessageBarVertex vertices[MESSAGE_BAR_VERTEX_COUNT];
    int count = 0;
    for (int i = 0; i < 2; ++i) {
        if (!g_menu.messages[i].text[0]) continue;
        append_message_bar(vertices, count, bar_left,
                           i == 0 ? 0.0f : height - MESSAGE_BAR_HEIGHT,
                           bar_width);
    }
    g_message_bar.program.use();
    g_message_bar.program.set("u_bar", 4);
    g_message_bar.program.set2("u_viewport_size", (float)width, (float)height);
    glDisable(GL_BLEND);
    glDisable(GL_SCISSOR_TEST);
    glActiveTexture(GL_TEXTURE4);
    glBindTexture(GL_TEXTURE_2D, g_message_bar.texture);
    glBindVertexArray(g_message_bar.vao);
    glBindBuffer(GL_ARRAY_BUFFER, g_message_bar.vbo);
    glBufferSubData(GL_ARRAY_BUFFER, 0,
                    count * sizeof(MessageBarVertex), vertices);
    glDrawArrays(GL_TRIANGLES, 0, count);
    glBindBuffer(GL_ARRAY_BUFFER, 0);
    glBindVertexArray(0);
    glBindTexture(GL_TEXTURE_2D, 0);
    glActiveTexture(GL_TEXTURE0);
    glUseProgram(0);
    for (int i = 0; i < 2; ++i) {
        const ShortMessage &message = g_menu.messages[i];
        if (!message.text[0]) continue;
        const float top = i == 0 ? 0.0f : height - MESSAGE_BAR_HEIGHT;
        if (!draw_text(MESSAGE_SLOT_BASE + i, message.text,
                       bar_left + message.text_offset_x,
                       top + message.text_offset_y,
                       0x0E, palette, width, height, 1)) return 0;
    }
    return 1;
}

static GLuint sprite_texture(int reference)
{
    if (reference < 0 || reference >= g_menu.sprite_count) return 0;
    SpriteCacheEntry &entry = g_menu.sprites[reference];
    if (!entry.texture)
        entry.texture = mw2er_gl_upload_indexed_sprite(entry.sprite);
    return entry.texture;
}

static int draw_sprite(int reference, float x, float y, float scale,
                       const Rect &clip, int color_override,
                       const uint8_t *palette, int width, int height)
{
    const GLuint texture = sprite_texture(reference);
    if (!texture) return reference < 0;
    const SpriteCacheEntry &entry = g_menu.sprites[reference];
    Mw2erIndexedSpriteDraw draw = {};
    draw.x = x;
    draw.y = y;
    draw.scale_x = scale;
    draw.scale_y = scale;
    draw.clip_left = clip.left;
    draw.clip_top = clip.top;
    draw.clip_right = clip.right;
    draw.clip_bottom = clip.bottom;
    draw.color_override = color_override;
    return mw2er_gl_draw_indexed_sprites(entry.sprite, texture, &draw, 1,
                                         palette, width, height) == MW2ER_OK;
}

static double position_scale(int height)
{
    const double vertical = std::max(1, height) / 768.0;
    return vertical < 1.0 ? vertical
        : 1.0 + std::clamp((double)mw2er_config().hud_position_scaling,
                           0.0, 1.0) * (vertical - 1.0);
}

static void page_origin(const MenuPage &page, int width, int height,
                        float &origin_x, float &origin_y)
{
    if (page.handler_id == MENU_HANDLER_USER ||
        page.handler_id == MENU_HANDLER_COMMAND) {
        const double center_x = (page.pane.left + page.pane.right) * 0.5;
        const double center_y = (page.pane.top + page.pane.bottom) * 0.5;
        const double scale = position_scale(height);
        origin_x = (float)(width * 0.5 + (center_x - 512.0) * scale - center_x);
        origin_y = (float)(height * 0.5 + (center_y - 384.0) * scale - center_y);
        return;
    }
    if (page.background_sprite >= 0) {
        const Mw2erSprite &sprite = g_menu.sprites[page.background_sprite].sprite;
        const double visual_left = page.background_window.left + sprite.x_offset;
        const double visual_top = page.background_window.top + sprite.y_offset;
        origin_x = (float)(width * 0.5 - visual_left - sprite.width * 0.5);
        origin_y = (float)(height * 0.5 - visual_top - sprite.height * 0.5);
    } else {
        origin_x = (float)((width - (page.pane.right - page.pane.left)) * 0.5 -
                           page.pane.left);
        origin_y = (float)((height - (page.pane.bottom - page.pane.top)) * 0.5 -
                           page.pane.top);
    }
}

static int draw_slider(const MenuItem &item, float origin_x, float origin_y,
                       const Rect &clip, const uint8_t *palette,
                       int width, int height)
{
    float x = origin_x + item.text_x;
    const float y = origin_y + item.text_y + 7.0f;
    const int left_ref = item.slider.sprites[0];
    const int track_ref = item.slider.sprites[1];
    const int right_ref = item.slider.sprites[2];
    const int thumb_ref = item.slider.sprites[3];
    if (left_ref >= 0) {
        if (!draw_sprite(left_ref, x, y, 1.0f, clip, -1,
                         palette, width, height)) return 0;
        x += g_menu.sprites[left_ref].sprite.width;
    }
    const int track_width = track_ref >= 0
        ? g_menu.sprites[track_ref].sprite.width : 316;
    if (track_ref >= 0 && !draw_sprite(track_ref, x, y, 1.0f, clip, -1,
                                      palette, width, height)) return 0;
    if (right_ref >= 0 && !draw_sprite(right_ref, x + track_width, y, 1.0f,
                                      clip, -1, palette, width, height)) return 0;
    const int thumb_width = thumb_ref >= 0
        ? g_menu.sprites[thumb_ref].sprite.width : 21;
    const float thumb_offset = item.slider.value * track_width / 65536.0f;
    const float thumb_x = x + thumb_offset - thumb_width * 0.5f;
    return thumb_ref < 0 || draw_sprite(thumb_ref, thumb_x, y, 1.0f,
                                        clip, -1, palette, width, height);
}

static int render_page(const MenuPage &page, const uint8_t *palette,
                       int width, int height)
{
    if (!page.valid) return 1;
    float origin_x, origin_y;
    page_origin(page, width, height, origin_x, origin_y);
    const float left = origin_x + page.pane.left;
    const float top = origin_y + page.pane.top;
    const float right = origin_x + page.pane.right;
    const float bottom = origin_y + page.pane.bottom;
    const Rect page_clip = {(int)std::floor(left), (int)std::floor(top),
                            (int)std::ceil(origin_x + page.pane.right + 1.0f),
                            (int)std::ceil(origin_y + page.pane.bottom + 1.0f)};
    static std::vector<Mw2erHudVertex> vertices;
    if (vertices.capacity() < 64) vertices.reserve(64);
    vertices.clear();
    if (page.clear_background) {
        const float black[4] = {0, 0, 0, 1};
        append_rect(vertices, left, top, right, bottom, black);
        glDisable(GL_BLEND);
        if (!mw2er_hud_submit_rects(vertices.data(), (int)vertices.size(),
                                    width, height)) return 0;
        vertices.clear();
    }
    if (page.background_sprite >= 0) {
        const Rect clip = {
            (int)std::floor(origin_x + page.background_window.left),
            (int)std::floor(origin_y + page.background_window.top),
            (int)std::ceil(origin_x + page.background_window.right + 1.0f),
            (int)std::ceil(origin_y + page.background_window.bottom + 1.0f)};
        if (!draw_sprite(page.background_sprite,
                         origin_x + page.background_window.left,
                         origin_y + page.background_window.top,
                         1.0f, clip, -1, palette, width, height)) return 0;
    }
    float line_color[4];
    palette_color(palette, 0x01, line_color);
    if (page.draw_border)
        append_border(vertices, left, top, right, bottom, line_color);
    const int title_slot = menu_slot(page.handler_id, 1, 0);
    if (page.title[0]) {
        float underline_right = right - 1.0f;
        if (!page.draw_border) {
            Mw2erTextMetrics metrics = {};
            if (mw2er_font_measure(title_slot, page.title, 16, 0.0f,
                                   &metrics) != MW2ER_OK) return 0;
            underline_right = origin_x + page.title_x + metrics.width;
        }
        append_rect(vertices, origin_x + page.title_x,
                    origin_y + page.title_y + 14.0f,
                    underline_right, origin_y + page.title_y + 15.0f,
                    line_color);
    }
    glDisable(GL_BLEND);
    if (!mw2er_hud_submit_rects(vertices.data(), (int)vertices.size(),
                                width, height)) return 0;

    const MenuItem *selected = page.selected_index >= 0 &&
        page.selected_index < page.item_count
        ? &page.items[page.selected_index] : nullptr;
    for (int i = 0; i < page.item_count; ++i) {
        const MenuItem &item = page.items[i];
        if (item.has_slider && !draw_slider(item, origin_x, origin_y,
                                            page_clip, palette, width, height))
            return 0;
    }
    if (page.show_marker && selected && page.marker_sprite >= 0 &&
        !draw_sprite(page.marker_sprite, origin_x + page.marker_x,
                     origin_y + page.marker_y, 1.0f, page_clip, -1,
                     palette, width, height)) return 0;

    const int opaque = page.clear_background || page.background_sprite >= 0;
    if (!draw_text(title_slot, page.title,
                   origin_x + page.title_x, origin_y + page.title_y,
                   page.normal_color, palette, width, height, opaque)) return 0;
    Mw2erTextMetrics indent = {};
    if (mw2er_font_measure(MENU_INDENT_SLOT, "0  ", 16, 0.0f,
                           &indent) != MW2ER_OK) return 0;
    for (int i = 0; i < page.item_count; ++i) {
        const MenuItem &item = page.items[i];
        char label[MENU_TEXT_CAPACITY + 4];
        float label_x = origin_x + item.prefix_x;
        if (item.prefix[0])
            snprintf(label, sizeof(label), "%s  %s", item.prefix, item.text);
        else {
            snprintf(label, sizeof(label), "%s", item.text);
            label_x += indent.width;
        }
        if (!draw_text(menu_slot(page.handler_id, 2, i), label,
                       label_x, origin_y + item.prefix_y,
                       item.color, palette, width, height, opaque)) return 0;
        if (!draw_text(menu_slot(page.handler_id, 3, i), item.value_text,
                       origin_x + item.text_x, origin_y + item.text_y,
                       item.color, palette, width, height, opaque)) return 0;
    }
    if (page.show_marker && selected && page.marker_sprite < 0 &&
        !draw_text(menu_slot(page.handler_id, 4, 0), ">",
                   origin_x + page.marker_x, origin_y + page.marker_y,
                   page.marker_color, palette, width, height, opaque)) return 0;
    return 1;
}

static int render_objectives(const uint8_t *palette, int width, int height)
{
    const Objectives &objectives = g_menu.objectives;
    if (!objectives.visible) return 1;
    const float scale = std::max(1, height) / 768.0f;
    const float origin_x = (width - 1024.0f * scale) * 0.5f;
    const char *title = "MISSION OBJECTIVES";
    Mw2erTextMetrics title_metrics = {};
    if (mw2er_font_measure(OBJECTIVE_TITLE_SLOT, title, 16, 0.0f,
                           &title_metrics) != MW2ER_OK) return 0;
    if (!draw_text(OBJECTIVE_TITLE_SLOT, title,
                   origin_x + 96.0f * scale, 270.0f * scale,
                   0x06, palette, width, height, 0)) return 0;
    float blue[4];
    palette_color(palette, 0x06, blue);
    static std::vector<Mw2erHudVertex> line;
    if (line.capacity() < 6) line.reserve(6);
    line.clear();
    append_rect(line, origin_x + 96.0f * scale, 284.0f * scale,
                origin_x + 96.0f * scale + title_metrics.width,
                284.0f * scale + std::max(1.0f, scale), blue);
    glDisable(GL_BLEND);
    if (!mw2er_hud_submit_rects(line.data(), (int)line.size(),
                                width, height)) return 0;
    for (int i = 0; i < objectives.row_count; ++i) {
        const ObjectiveRow &row = objectives.rows[i];
        const char *label_text = row.objective_class == 1 ? "Primary:   " :
            row.objective_class == 2 ? "Secondary: " : "Tertiary: ";
        const char *status_text = row.state == 5 ? "Successful" :
            row.state == 6 ? "Failed" : "In progress";
        const int status_color = row.state == 5 ? 0x07 :
            row.state == 6 ? 0x0B : 0x0E;
        const int slot = OBJECTIVE_ROW_SLOT_BASE + i * 4;
        Mw2erTextMetrics label = {}, status = {};
        if (mw2er_font_measure(slot, label_text, 16, 0.0f, &label) != MW2ER_OK ||
            mw2er_font_measure(slot + 2, status_text, 16, 0.0f,
                               &status) != MW2ER_OK) return 0;
        const float y = row.y * scale;
        if (!draw_text(slot, label_text, origin_x + 96.0f * scale, y,
                       0x06, palette, width, height, 0) ||
            !draw_text(slot + 1, row.text,
                       origin_x + 96.0f * scale + label.width, y,
                       0x0E, palette, width, height, 0) ||
            !draw_text(slot + 2, status_text,
                       origin_x + 961.0f * scale - status.width, y,
                       status_color, palette, width, height, 0)) return 0;
        if (row.continuation[0] &&
            !draw_text(slot + 3, row.continuation,
                       origin_x + 166.0f * scale, (row.y + 14) * scale,
                       0x0E, palette, width, height, 0)) return 0;
    }
    return draw_text(OBJECTIVE_FOOTER_SLOT, objectives.footer,
                     origin_x + 96.0f * scale, 403.0f * scale,
                     0x06, palette, width, height, 0);
}

} // namespace

void mw2er_menu_mission_reset(void)
{
    for (int i = 0; i < g_menu.sprite_count; ++i) {
        if (g_menu.sprites[i].texture)
            g_menu.retired_textures[g_menu.retired_count++] = g_menu.sprites[i].texture;
        g_menu.sprites[i] = {};
    }
    g_menu.sprite_count = 0;
    g_menu.resource_shape_count = 0;
    for (MenuPage &page : g_menu.pages) page.valid = 0;
    g_menu.objectives.visible = 0;
    g_menu.objectives.row_count = 0;
    for (ShortMessage &message : g_menu.messages) message.text[0] = '\0';
}

void mw2er_menu_gl_reset(void)
{
    for (int i = 0; i < g_menu.sprite_count; ++i) {
        if (g_menu.sprites[i].texture)
            glDeleteTextures(1, &g_menu.sprites[i].texture);
        g_menu.sprites[i].texture = 0;
    }
    if (g_menu.retired_count != 0)
        glDeleteTextures((GLsizei)g_menu.retired_count,
                         g_menu.retired_textures);
    g_menu.retired_count = 0;
    if (g_message_bar.texture)
        glDeleteTextures(1, &g_message_bar.texture);
    if (g_message_bar.vbo) glDeleteBuffers(1, &g_message_bar.vbo);
    if (g_message_bar.vao) glDeleteVertexArrays(1, &g_message_bar.vao);
    g_message_bar.program.destroy();
    g_message_bar.texture = 0;
    g_message_bar.vbo = 0;
    g_message_bar.vao = 0;
}

void mw2er_menu_capture_primary(const Mw2erMemoryView &view)
{
    const Mem mem = Mem::from(view);
    if (!mem.ok()) return;
    capture_handlers(mem, 0x07, 1);
    capture_objectives(mem);
}

void mw2er_menu_capture_late(const Mw2erMemoryView &view)
{
    const Mem mem = Mem::from(view);
    if (mem.ok()) {
        capture_handlers(mem, 0x01, 0);
        capture_short_messages(mem);
    }
}

int32_t mw2er_menu_render(int32_t width, int32_t height,
                          const uint8_t *palette)
{
    if (!palette || width <= 0 || height <= 0) return MW2ER_ERR_INVALID_ARGUMENT;
    if (g_menu.retired_count != 0) {
        glDeleteTextures((GLsizei)g_menu.retired_count,
                         g_menu.retired_textures);
        g_menu.retired_count = 0;
    }
    if (!render_objectives(palette, width, height) ||
        !render_page(g_menu.pages[1], palette, width, height) ||
        !render_page(g_menu.pages[2], palette, width, height) ||
        !render_page(g_menu.pages[0], palette, width, height) ||
        !render_short_messages(palette, width, height))
        return MW2ER_ERR_GL;
    glDisable(GL_SCISSOR_TEST);
    glDisable(GL_BLEND);
    return MW2ER_OK;
}
