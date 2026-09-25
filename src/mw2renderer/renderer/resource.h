#ifndef MW2ER_RESOURCE_H
#define MW2ER_RESOURCE_H

#include "mw2er_abi.h"

#include <stdint.h>
#include <vector>

static constexpr char kArchiveSourceExecutable[] = "MECH2.EXE";

struct Mw2erResourceAsset {
    uint32_t type;
    uint32_t resource_id;
    uint16_t width;
    uint16_t height;
    std::vector<uint8_t> bytes;
};

// Archive source bytes survive missions and sessions using the same archive.
// Decoded scene/texture state and guest fallback assets remain mission-owned.
void mw2er_resources_open(const Mw2erSessionInfo &session);
void mw2er_resources_shutdown(void);
void mw2er_resources_begin(uint64_t generation);
void mw2er_resources_end(void);
int32_t mw2er_resources_discover(const Mw2erMemoryView &memory);
uint32_t mw2er_resources_pending(uint64_t generation);
int mw2er_resources_textures_discovered(void);
int32_t mw2er_resources_service(
    const Mw2erResourceService *service,
    Mw2erResourceProgress *progress);

const Mw2erResourceAsset *mw2er_resource_find(uint32_t type, uint32_t resource_id);
int mw2er_resources_have_type(uint32_t type);
int mw2er_resources_allow_guest_fallback(void);
int mw2er_resource_retain_cel(
    uint32_t resource_id,
    uint16_t width,
    uint16_t height,
    const uint8_t *pixels,
    uint32_t size);
int mw2er_resource_retain_poly(
    uint32_t resource_id,
    const uint8_t *bytes,
    uint32_t size);

#endif
