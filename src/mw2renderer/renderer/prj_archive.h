#ifndef MW2ER_PRJ_ARCHIVE_H
#define MW2ER_PRJ_ARCHIVE_H

#include "resource.h"

#include <mutex>
#include <stdint.h>
#include <vector>

enum class Mw2erPrjStatus { Found, NotFound, Unsupported, Corrupt, IoError };
enum class Mw2erPrjMeshKind { Ordinary, PointOrLine, NonDrawable };

struct Mw2erPrjResult {
    Mw2erPrjStatus status = Mw2erPrjStatus::Unsupported;
    const char *stage = "open";
    uint32_t type = 0;
    uint32_t resource_id = 0;
    const char *detail = "archive is not open";
    uint32_t system_error = 0;
};

struct Mw2erPrjKey {
    uint32_t type;
    uint32_t resource_id;
};

// One immutable, qualified archive handle. Lookups serialize the seek/read pair;
// callers must finish using occupied_keys() before reopening or closing it.
class Mw2erPrjArchive {
public:
    Mw2erPrjArchive() = default;
    ~Mw2erPrjArchive();
    Mw2erPrjArchive(const Mw2erPrjArchive &) = delete;
    Mw2erPrjArchive &operator=(const Mw2erPrjArchive &) = delete;

    Mw2erPrjResult open(const char *host_path);
    void close();
    Mw2erPrjResult lookup(uint32_t type, uint32_t resource_id,
                         Mw2erResourceAsset &asset,
                         Mw2erPrjMeshKind *mesh_kind = nullptr);
    const std::vector<Mw2erPrjKey> &occupied_keys() const { return keys_; }

#if defined(MW2ER_PRJ_TESTING) && !defined(MW2ER_RENDERER_EXPORTS)
    // Synthetic format fixtures only; absent from the renderer DLL build.
    Mw2erPrjResult open_test_profile(const char *host_path);
#endif

private:
    struct Slot { uint32_t offset; uint32_t size; };
    struct Directory { uint32_t type; std::vector<Slot> slots; };

    Mw2erPrjResult open_impl(const char *host_path, bool synthetic);
    Mw2erPrjResult parse_index();
    Mw2erPrjResult read_range(uint64_t offset, size_t size, uint8_t *destination,
                             const char *stage, uint32_t type = 0,
                             uint32_t resource_id = 0);
    void close_unlocked();

    void *handle_ = nullptr;
    uint64_t file_size_ = 0;
    Mw2erPrjResult state_;
    std::vector<Directory> directories_;
    std::vector<Mw2erPrjKey> keys_;
    std::mutex mutex_;
};

const char *mw2er_prj_status_name(Mw2erPrjStatus status);

#endif
