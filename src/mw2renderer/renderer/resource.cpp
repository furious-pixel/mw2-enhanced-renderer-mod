#include "resource.h"
#include "prj_archive.h"
#include "config.h"
#include "mem.h"
#include "mw2er_internal.h"

#include <algorithm>
#include <atomic>
#include <chrono>
#include <exception>
#include <filesystem>
#include <memory>
#include <stdio.h>
#include <string.h>
#include <string>
#include <thread>
#include <utility>
#include <vector>

namespace {

enum {
    ADDR_TEXTURE_DESCRIPTOR_TABLE = 0x0013DFE0,
    ADDR_TEXTURE_CELL_TABLE = 0x0013FBE0,
    ADDR_COMPONENT_DESCRIPTOR_TABLE = 0x001310B0,
    ADDR_COMPONENT_DESCRIPTOR_COUNT = 0x000A6DB0,
    ADDR_TEXTURE_TABLE_INIT_STATE = 0x000A6DB8,
    ADDR_RESOURCE_POLY_TYPE = 0x000AE9E8,
    TEXTURE_DESCRIPTOR_STRIDE = 14,
    TEXTURE_DESCRIPTOR_COUNT = 512,
    TEXTURE_CELL_PAGE_STRIDE = 256,
    TEXTURE_CELL_SUB_ENTRY_STRIDE = 8,
    TEXTURE_CELL_SUB_ENTRY_COUNT = 32,
    COMPONENT_DESCRIPTOR_STRIDE = 0x44,
    MAX_COMPONENT_DESCRIPTORS = 4096,
    MIN_READY_TEXTURE_PAGES = 8,
    DUMMY_RESOURCE_ID = 0x07B1,
    MAX_TEXTURE_DIMENSION = 1024,
    MAX_TEXTURE_BYTES = 1024 * 1024,
    WTBO_HEADER_SIZE = 0x20,
    WTBO_VERTEX_STRIDE = 0x10,
    MAX_POLY_VERTICES = 4096,
    MAX_POLY_FACES = 4096,
    MAX_POLY_FACE_VERTICES = 16,
    MAX_POLY_PAYLOAD = 4 * 1024 * 1024,
    RESOURCE_RETRY_LIMIT = 3
};

struct PendingResource {
    uint32_t type;
    uint32_t resource_id;
    uint32_t attempts;
};

static uint64_t g_generation;
static int g_textures_discovered;
static int g_poly_discovered;
static int g_completion_logged;
static int g_resource_wait_state;
static uint32_t g_retained_count;
static uint32_t g_failed_count;
static std::vector<Mw2erResourceAsset> g_assets;
static std::vector<PendingResource> g_pending;
static size_t g_pending_head;

// The worker owns the reader and builds one source cache. A release/acquire
// publication makes the entire vector immutable before any renderer lookup.
// No worker touches guest memory, host logging, mission caches, or GL state.
struct PrjResources {
    std::string path;
    std::string identity;
    Mw2erPrjArchive archive;
    std::vector<Mw2erResourceAsset> assets;
    Mw2erPrjResult result;
    std::atomic<bool> cancel{false};
    std::atomic<bool> done{false};
    std::atomic<uint32_t> pending{1};
    std::thread worker;
    uint64_t bytes = 0;
    double elapsed_ms = 0;
    bool logged = false;

    ~PrjResources()
    {
        cancel.store(true, std::memory_order_relaxed);
        if (worker.joinable()) worker.join();
    }
};

static std::unique_ptr<PrjResources> g_prj;

static size_t pending_count(void)
{
    return g_pending.size() - g_pending_head;
}

static uint64_t asset_key(uint32_t type, uint32_t resource_id)
{
    return ((uint64_t)type << 32) | resource_id;
}

static bool asset_less(const Mw2erResourceAsset &a, const Mw2erResourceAsset &b)
{
    return asset_key(a.type, a.resource_id) < asset_key(b.type, b.resource_id);
}

static void load_prj(PrjResources &prj)
{
    const auto start = std::chrono::steady_clock::now();
    try {
        prj.result = prj.archive.open(prj.path.c_str());
        if (prj.result.status == Mw2erPrjStatus::Found) {
            const auto &keys = prj.archive.occupied_keys();
            prj.assets.reserve(keys.size());
            prj.pending.store((uint32_t)keys.size() + 1, std::memory_order_relaxed);
            for (const auto &key : keys) {
                if (prj.cancel.load(std::memory_order_relaxed)) return;
                if (key.type == MW2ER_RESOURCE_CEL || key.type == MW2ER_RESOURCE_POLY) {
                    Mw2erResourceAsset asset = {};
                    prj.result = prj.archive.lookup(key.type, key.resource_id, asset);
                    if (prj.result.status != Mw2erPrjStatus::Found) break;
                    // Bounded independently of metadata and per-resource limits.
                    if (asset.bytes.size() > 64u * 1024u * 1024u - prj.bytes) {
                        prj.result = {Mw2erPrjStatus::Corrupt, "cache", key.type,
                            key.resource_id, "source cache exceeds 64 MiB budget"};
                        break;
                    }
                    prj.bytes += asset.bytes.size();
                    prj.assets.push_back(std::move(asset));
                }
                prj.pending.fetch_sub(1, std::memory_order_relaxed);
            }
            if (prj.result.status == Mw2erPrjStatus::Found)
                std::sort(prj.assets.begin(), prj.assets.end(), asset_less);
        }
    } catch (...) {
        prj.result = {Mw2erPrjStatus::IoError, "cache", 0, 0,
            "background resource allocation failed"};
    }
    prj.elapsed_ms = std::chrono::duration<double, std::milli>(
        std::chrono::steady_clock::now() - start).count();
    prj.done.store(true, std::memory_order_release);
}

static bool prj_ready()
{
    return g_prj && g_prj->done.load(std::memory_order_acquire) &&
        g_prj->result.status == Mw2erPrjStatus::Found;
}

static void log_prj_failure(const char *detail)
{
    mw2er_log("************************************************************");
    mw2er_log("*** PRJ RESOURCE LOADING FAILED ***");
    mw2er_log(detail);
    mw2er_log("*** FALLING BACK TO GAME-MEMORY RESOURCE LOADING ***");
    mw2er_log("************************************************************");
}

static int32_t prj_progress()
{
    if (!g_prj) return MW2ER_OK;
    if (!g_prj->done.load(std::memory_order_acquire)) return MW2ER_ERR_NOT_READY;
    char message[512];
    if (g_prj->result.status != Mw2erPrjStatus::Found) {
        const auto &r = g_prj->result;
        snprintf(message, sizeof(message),
            "mw2renderer: PRJ %s at %s type=%u id=%u: %s (OS error %u)",
            mw2er_prj_status_name(r.status), r.stage, r.type, r.resource_id,
            r.detail, r.system_error);
        log_prj_failure(message);
        // The worker has completed and no partial cache was published. Join
        // before releasing it; subsequent requests use the ordinary guest path.
        g_prj.reset();
        return MW2ER_OK;
    }
    if (!g_prj->logged) {
        size_t cels = 0, polys = 0;
        for (const auto &asset : g_prj->assets) {
            cels += asset.type == MW2ER_RESOURCE_CEL;
            polys += asset.type == MW2ER_RESOURCE_POLY;
        }
        snprintf(message, sizeof(message),
            "mw2renderer: PRJ ready (%zu CEL, %zu POLY, %llu CPU bytes, %.1f ms)",
            cels, polys, (unsigned long long)g_prj->bytes, g_prj->elapsed_ms);
        mw2er_log(message);
        g_prj->logged = true;
    }
    return MW2ER_OK;
}

static std::vector<Mw2erResourceAsset>::iterator asset_position(uint64_t key)
{
    return std::lower_bound(
        g_assets.begin(),
        g_assets.end(),
        key,
        [](const Mw2erResourceAsset &asset, uint64_t value) {
            return asset_key(asset.type, asset.resource_id) < value;
        });
}

static int queue_contains(uint32_t type, uint32_t resource_id)
{
    if (mw2er_resource_find(type, resource_id) != NULL) {
        return 1;
    }
    for (size_t i = g_pending_head; i < g_pending.size(); ++i) {
        if (g_pending[i].type == type && g_pending[i].resource_id == resource_id) {
            return 1;
        }
    }
    return 0;
}

static bool queue_resource(uint32_t type, int32_t resource_id)
{
    if (resource_id < 1 || resource_id == DUMMY_RESOURCE_ID ||
        queue_contains(type, (uint32_t)resource_id)) {
        return true;
    }
    PendingResource item = {(uint32_t)type, (uint32_t)resource_id, 0};
    try {
        g_pending.push_back(item);
    } catch (...) {
        mw2er_set_error("resource discovery: queue allocation failed");
        return false;
    }
    g_completion_logged = 0;
    return true;
}

/* Match Python observe_texture_table_state: pre-init is zeros with
 * init_state -2. Do not treat leftover page indices as a live catalog. */
static int texture_tables_initialized(const Mem &mem)
{
    const int32_t init_state = mem.i32_rel(ADDR_TEXTURE_TABLE_INIT_STATE);
    if (init_state == -2) {
        return 0;
    }
    const int16_t cell_sentinel = mem.i16_rel(ADDR_TEXTURE_CELL_TABLE);
    const int16_t descriptor_sentinel =
        mem.i16_rel(ADDR_TEXTURE_DESCRIPTOR_TABLE + 8);
    return cell_sentinel != 0 ||
        (descriptor_sentinel != 0 && descriptor_sentinel != -2);
}

static int poly_acquire_ready(const Mem &mem)
{
    /* The type context is an argument to the guest acquire call, not a
     * renderer-owned pointer. Its presence is enough to queue POLY work. */
    const uint32_t type = mem.u32_rel(ADDR_RESOURCE_POLY_TYPE);
    return type != 0 && type != 0xFFFFFFFFu;
}

static int texture_page_count(const Mem &mem, uint8_t *pages)
{
    memset(pages, 0, TEXTURE_DESCRIPTOR_COUNT);
    int page_count = 0;
    for (int i = 0; i < TEXTURE_DESCRIPTOR_COUNT; ++i) {
        int page = mem.i16_rel(
            ADDR_TEXTURE_DESCRIPTOR_TABLE + i * TEXTURE_DESCRIPTOR_STRIDE);
        if (page >= 0 && page < TEXTURE_DESCRIPTOR_COUNT && !pages[page]) {
            pages[page] = 1;
            ++page_count;
        }
    }
    return page_count;
}

static uint32_t wtbo_payload_size(const Mem &mem, uint32_t address)
{
    uint8_t header[WTBO_HEADER_SIZE];
    if (!mem.read(address, header, sizeof(header)) ||
        memcmp(header, "WTBO", 4) != 0) {
        return 0;
    }
    const uint32_t vertex_count = (uint32_t)(header[0x18] | (header[0x19] << 8));
    const uint32_t face_count = (uint32_t)(header[0x1A] | (header[0x1B] << 8));
    if (vertex_count < 3 || vertex_count > MAX_POLY_VERTICES ||
        face_count < 1 || face_count > MAX_POLY_FACES) {
        return 0;
    }
    uint32_t offset = WTBO_HEADER_SIZE + vertex_count * WTBO_VERTEX_STRIDE;
    for (uint32_t face = 0; face < face_count; ++face) {
        uint8_t face_header[4];
        if (offset > MAX_POLY_PAYLOAD - sizeof(face_header) ||
            !mem.read(address + offset, face_header, sizeof(face_header))) {
            return 0;
        }
        const uint32_t count = (uint32_t)(face_header[2] | (face_header[3] << 8));
        if (count > MAX_POLY_FACE_VERTICES) {
            return 0;
        }
        const uint32_t raw = 4 + count * 2;
        const uint32_t stride = raw < 12 ? 12 : ((raw + 5) / 6) * 6;
        if (offset > MAX_POLY_PAYLOAD - stride) {
            return 0;
        }
        uint8_t indices[MAX_POLY_FACE_VERTICES * 2];
        if (count != 0 && !mem.read(address + offset + 4, indices, count * 2)) {
            return 0;
        }
        for (uint32_t i = 0; i < count; ++i) {
            const uint32_t index = indices[i * 2] | (indices[i * 2 + 1] << 8);
            if (index >= vertex_count) {
                return 0;
            }
        }
        offset += stride;
    }
    return offset;
}

static int copy_resource(
    const PendingResource &item,
    const Mem &mem,
    uint32_t address)
{
    if (item.type == MW2ER_RESOURCE_CEL) {
        uint8_t dimensions[4];
        if (!mem.read(address, dimensions, sizeof(dimensions))) {
            return 0;
        }
        const uint16_t width = (uint16_t)(dimensions[0] | (dimensions[1] << 8));
        const uint16_t height = (uint16_t)(dimensions[2] | (dimensions[3] << 8));
        const uint32_t size = (uint32_t)width * (uint32_t)height;
        if (width == 0 || height == 0 || width > MAX_TEXTURE_DIMENSION ||
            height > MAX_TEXTURE_DIMENSION || size > MAX_TEXTURE_BYTES) {
            return 0;
        }
        const uint8_t *pixels = mem.view(address + 4, size);
        if (pixels == NULL) {
            return 0;
        }
        return mw2er_resource_retain_cel(
            item.resource_id, width, height, pixels, size);
    }
    if (item.type == MW2ER_RESOURCE_POLY) {
        const uint32_t size = wtbo_payload_size(mem, address);
        if (size == 0) {
            return 0;
        }
        const uint8_t *bytes = mem.view(address, size);
        if (bytes == NULL) {
            return 0;
        }
        return mw2er_resource_retain_poly(item.resource_id, bytes, size);
    }
    return 0;
}

} // namespace

void mw2er_resources_open(const Mw2erSessionInfo &session)
{
    if (!mw2er_config().load_resources_from_prj) {
        g_prj.reset();
        mw2er_log("mw2renderer: PRJ loading disabled; using guest-memory resource loading");
        return;
    }
    try {
        std::filesystem::path path;
        if (session.resolve_opened_path) {
            char executable_path[MW2ER_PATH_MAX] = {};
            if (session.resolve_opened_path(session.resolve_user, kArchiveSourceExecutable,
                                            executable_path,
                                            sizeof(executable_path)) == MW2ER_OK &&
                executable_path[0]) {
                const auto exe = std::filesystem::u8path(executable_path);
                if (exe.is_absolute()) path = exe.parent_path() / "MW2.PRJ";
            }
        }
        const std::string filename = path.lexically_normal().u8string();
        const std::string identity = session.archive_identity ? session.archive_identity : "";
        // A successfully opened file denies writes/replacement. Its immutable
        // source bytes can therefore span mission and executable generations.
        if (g_prj && g_prj->path == filename && g_prj->identity == identity &&
            (!g_prj->done.load(std::memory_order_acquire) || prj_ready())) return;
        g_prj.reset(); // Cancels and joins before replacing the archive owner.
        g_prj = std::make_unique<PrjResources>();
        g_prj->path = filename;
        g_prj->identity = identity;
        if (path.empty() || !path.is_absolute()) {
            g_prj->result = {Mw2erPrjStatus::IoError, "path", 0, 0,
                "host did not observe an absolute MECH2.EXE path"};
            g_prj->done.store(true, std::memory_order_release);
            prj_progress();
            return;
        }
        g_prj->worker = std::thread(load_prj, std::ref(*g_prj));
        char message[768];
        snprintf(message, sizeof(message),
            "mw2renderer: PRJ background resource loading started: %s", filename.c_str());
        mw2er_log(message);
    } catch (const std::exception &error) {
        g_prj.reset();
        log_prj_failure(error.what());
    } catch (...) {
        g_prj.reset();
        log_prj_failure("mw2renderer: unexpected C++ exception during PRJ setup");
    }
}

void mw2er_resources_shutdown(void)
{
    mw2er_resources_end();
    g_prj.reset();
}

void mw2er_resources_begin(uint64_t generation)
{
    g_generation = generation;
    g_textures_discovered = 0;
    g_poly_discovered = 0;
    g_completion_logged = 0;
    g_resource_wait_state = -1;
    g_retained_count = 0;
    g_failed_count = 0;
    g_assets.clear();
    g_pending.clear();
    g_pending_head = 0;
}

void mw2er_resources_end(void)
{
    g_generation = 0;
    g_textures_discovered = 0;
    g_poly_discovered = 0;
    g_completion_logged = 0;
    g_resource_wait_state = -1;
    g_retained_count = 0;
    g_failed_count = 0;
    g_assets.clear();
    g_pending.clear();
    g_pending_head = 0;
}

static void maybe_log_preload_complete(void)
{
    if (g_completion_logged || pending_count() != 0 || !g_textures_discovered) {
        return;
    }
    char message[128];
    snprintf(
        message,
        sizeof(message),
        "mw2renderer: resource preload complete (%u retained, %u unavailable)",
        g_retained_count,
        g_failed_count);
    mw2er_log(message);
    g_completion_logged = 1;
}

int32_t mw2er_resources_discover(const Mw2erMemoryView &memory)
{
    if (g_generation != 0 && g_prj) {
        const int32_t result = prj_progress();
        if (g_prj) {
            return result;
        }
        // An unavailable archive switches the whole source provider. Discover
        // guest resources normally instead of marking the preload complete.
    }
    if (g_generation == 0 || (g_textures_discovered && g_poly_discovered)) {
        return MW2ER_OK;
    }
    const Mem mem = Mem::from(memory);
    if (!g_textures_discovered) {
        uint8_t pages[TEXTURE_DESCRIPTOR_COUNT];
        const int initialized = texture_tables_initialized(mem);
        const int page_count = texture_page_count(mem, pages);
        int cel_requests = -1;
        if (initialized && page_count >= MIN_READY_TEXTURE_PAGES) {
            cel_requests = 0;
            for (int page = 0; page < TEXTURE_DESCRIPTOR_COUNT; ++page) {
                if (!pages[page]) {
                    continue;
                }
                const uint32_t base = ADDR_TEXTURE_CELL_TABLE +
                    page * TEXTURE_CELL_PAGE_STRIDE;
                for (int sub = 0; sub < TEXTURE_CELL_SUB_ENTRY_COUNT; ++sub) {
                    const int32_t resource_id = mem.i16_rel(
                        base + sub * TEXTURE_CELL_SUB_ENTRY_STRIDE);
                    if (resource_id > 0 && resource_id != DUMMY_RESOURCE_ID) {
                        ++cel_requests;
                        if (!queue_resource(MW2ER_RESOURCE_CEL, resource_id)) {
                            return MW2ER_ERR_GENERIC;
                        }
                    }
                }
            }
            if (cel_requests) {
                g_textures_discovered = 1;
                char message[128];
                snprintf(
                    message,
                    sizeof(message),
                    "mw2renderer: resource preload queued (%u pending)",
                    (uint32_t)pending_count());
                mw2er_log(message);
            }
        }
        if (!g_textures_discovered) {
            const int state = (initialized ? 1 : 0) |
                (page_count >= MIN_READY_TEXTURE_PAGES ? 2 : 0);
            if (state != g_resource_wait_state) {
                char message[160];
                snprintf(
                    message, sizeof(message),
                    "mw2renderer: resource discover waiting "
                    "(tables=%d pages=%d cel_requests=%d)",
                    initialized, page_count, cel_requests);
                mw2er_log(message);
                g_resource_wait_state = state;
            }
        }
    }
    const int32_t descriptor_count = g_poly_discovered
        ? 0
        : mem.i32_rel(ADDR_COMPONENT_DESCRIPTOR_COUNT);
    if (!g_poly_discovered && poly_acquire_ready(mem) &&
        descriptor_count >= 1 &&
        descriptor_count <= MAX_COMPONENT_DESCRIPTORS) {
        for (int32_t i = 0; i < descriptor_count; ++i) {
            const uint32_t base = ADDR_COMPONENT_DESCRIPTOR_TABLE +
                (uint32_t)i * COMPONENT_DESCRIPTOR_STRIDE;
            for (int detail = 0; detail < 4; ++detail) {
                if (!queue_resource(
                    MW2ER_RESOURCE_POLY,
                    mem.i32_rel(base + 0x08 + (uint32_t)detail * 4))) {
                    return MW2ER_ERR_GENERIC;
                }
            }
        }
        g_poly_discovered = 1;
    }
    maybe_log_preload_complete();
    return MW2ER_OK;
}

uint32_t mw2er_resources_pending(uint64_t generation)
{
    if (!generation || generation != g_generation) return 0;
    if (g_prj) {
        if (prj_ready()) return 0;
        // Keep scheduling until progress either publishes PRJ or selects guest.
        return std::max(1u, g_prj->pending.load(std::memory_order_relaxed));
    }
    return (uint32_t)pending_count();
}

int mw2er_resources_textures_discovered(void)
{
    if (g_prj)
        return g_generation != 0 && prj_ready();
    return g_generation != 0 && g_textures_discovered;
}

int32_t mw2er_resources_service(
    const Mw2erResourceService *service,
    Mw2erResourceProgress *progress)
{
    if (progress != NULL && progress->struct_size >= sizeof(*progress)) {
        progress->processed = 0;
        progress->retained = 0;
        progress->failed = 0;
        progress->pending = mw2er_resources_pending(g_generation);
    }
    if (g_generation != 0 && g_prj) {
        const int32_t result = prj_progress();
        if (g_prj) return result;
        if (progress != NULL && progress->struct_size >= sizeof(*progress))
            progress->pending = (uint32_t)pending_count();
        // PRJ failure resumes the same validated guest service below.
    }
    if (g_generation == 0 || service == NULL ||
        service->struct_size < sizeof(*service) || service->max_resources == 0 ||
        service->acquire == NULL || service->release == NULL ||
        !mw2er_memory_ok(&service->memory)) {
        mw2er_set_error("service_resources: invalid service");
        return MW2ER_ERR_INVALID_ARGUMENT;
    }
    const int32_t discovered = mw2er_resources_discover(service->memory);
    if (discovered != MW2ER_OK) {
        if (progress != NULL && progress->struct_size >= sizeof(*progress)) {
            progress->pending = (uint32_t)pending_count();
        }
        return discovered;
    }
    const Mem mem = Mem::from(service->memory);
    uint32_t processed = 0;
    const size_t available = pending_count();
    const uint32_t batch = service->max_resources < available
        ? service->max_resources
        : (uint32_t)available;
    while (processed < batch && g_pending_head < g_pending.size()) {
        PendingResource &item = g_pending[g_pending_head];
        Mw2erResourceKey key = {};
        key.struct_size = sizeof(key);
        key.type = item.type;
        key.resource_id = item.resource_id;
        key.resource_generation = g_generation;
        uint32_t address = 0;
        const int32_t acquired = service->acquire(service->user, &key, &address);
        if (acquired == MW2ER_ERR_NOT_READY) {
            if (progress != NULL && progress->struct_size >= sizeof(*progress)) {
                progress->processed = processed;
                progress->pending = (uint32_t)pending_count();
            }
            return MW2ER_ERR_NOT_READY;
        }
        int retained = 0;
        int32_t released = MW2ER_OK;
        if (acquired == MW2ER_OK && address != 0) {
            try {
                retained = copy_resource(item, mem, address);
            } catch (...) {
                // Contain copy failures here so the acquired guest resource is
                // released below, including for unexpected exception types.
                retained = 0;
                mw2er_set_error("service_resources: copy failed");
            }
            released = service->release(service->user, &key);
        }
        ++processed;
        if (retained && released == MW2ER_OK) {
            ++g_pending_head;
            ++g_retained_count;
            if (progress != NULL && progress->struct_size >= sizeof(*progress)) {
                ++progress->retained;
            }
        } else if (released != MW2ER_OK) {
            ++g_pending_head;
            ++g_failed_count;
            mw2er_set_error("service_resources: release failed");
            return MW2ER_ERR_GENERIC;
        } else if (++item.attempts >= RESOURCE_RETRY_LIMIT) {
            ++g_pending_head;
            ++g_failed_count;
            if (progress != NULL && progress->struct_size >= sizeof(*progress)) {
                ++progress->failed;
            }
        } else {
            // Preserve retry order without allocating or changing the queue size.
            const auto first = g_pending.begin() + g_pending_head;
            std::rotate(first, first + 1, g_pending.end());
        }
    }
    if (g_pending_head == g_pending.size()) {
        g_pending.clear();
        g_pending_head = 0;
    }
    if (progress != NULL && progress->struct_size >= sizeof(*progress)) {
        progress->processed = processed;
        progress->pending = (uint32_t)pending_count();
    }
    maybe_log_preload_complete();
    return MW2ER_OK;
}

const Mw2erResourceAsset *mw2er_resource_find(uint32_t type, uint32_t resource_id)
{
    const uint64_t key = asset_key(type, resource_id);
    if (g_prj) {
        if (!g_generation || !prj_ready()) return NULL;
        const auto &assets = g_prj->assets;
        const auto it = std::lower_bound(assets.begin(), assets.end(), key,
            [](const Mw2erResourceAsset &asset, uint64_t value) {
                return asset_key(asset.type, asset.resource_id) < value;
            });
        if (it != assets.end() && asset_key(it->type, it->resource_id) == key)
            return &*it;
        // Only a genuine absent key reaches the mission-owned guest fallback.
    }
    const auto it = asset_position(key);
    if (it != g_assets.end() && asset_key(it->type, it->resource_id) == key) {
        return &*it;
    }
    return NULL;
}

int mw2er_resources_have_type(uint32_t type)
{
    if (g_prj) {
        if (!g_generation || !prj_ready()) return 0;
        const auto &assets = g_prj->assets;
        const auto it = std::lower_bound(assets.begin(), assets.end(), asset_key(type, 0),
            [](const Mw2erResourceAsset &asset, uint64_t value) {
                return asset_key(asset.type, asset.resource_id) < value;
            });
        if (it != assets.end() && it->type == type) return 1;
    }
    const auto it = asset_position(asset_key(type, 0));
    return it != g_assets.end() && it->type == type;
}

int mw2er_resources_allow_guest_fallback(void)
{
    return !g_prj || prj_ready();
}

int mw2er_resource_retain_cel(
    uint32_t resource_id,
    uint16_t width,
    uint16_t height,
    const uint8_t *pixels,
    uint32_t size)
{
    if (!mw2er_resources_allow_guest_fallback() ||
        width == 0 || height == 0 || pixels == NULL ||
        size != (uint32_t)width * (uint32_t)height) {
        return 0;
    }
    const Mw2erResourceAsset *existing = mw2er_resource_find(
        MW2ER_RESOURCE_CEL, resource_id);
    if (existing != NULL) {
        return existing->width == width && existing->height == height &&
            existing->bytes.size() == size &&
            memcmp(existing->bytes.data(), pixels, size) == 0;
    }
    Mw2erResourceAsset asset = {};
    asset.type = MW2ER_RESOURCE_CEL;
    asset.resource_id = resource_id;
    asset.width = width;
    asset.height = height;
    asset.bytes.assign(pixels, pixels + size);
    const uint64_t key = asset_key(asset.type, asset.resource_id);
    g_assets.insert(asset_position(key), std::move(asset));
    return 1;
}

int mw2er_resource_retain_poly(
    uint32_t resource_id,
    const uint8_t *bytes,
    uint32_t size)
{
    if (!mw2er_resources_allow_guest_fallback() ||
        bytes == NULL || size < WTBO_HEADER_SIZE ||
        size > MAX_POLY_PAYLOAD || memcmp(bytes, "WTBO", 4) != 0) {
        return 0;
    }
    const Mw2erResourceAsset *existing = mw2er_resource_find(
        MW2ER_RESOURCE_POLY, resource_id);
    if (existing != NULL) {
        return existing->bytes.size() == size &&
            memcmp(existing->bytes.data(), bytes, size) == 0;
    }
    Mw2erResourceAsset asset = {};
    asset.type = MW2ER_RESOURCE_POLY;
    asset.resource_id = resource_id;
    asset.bytes.assign(bytes, bytes + size);
    const uint64_t key = asset_key(asset.type, asset.resource_id);
    g_assets.insert(asset_position(key), std::move(asset));
    return 1;
}
