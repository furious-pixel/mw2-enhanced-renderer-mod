#ifndef MW2ER_LEVEL_OVERRIDES_H
#define MW2ER_LEVEL_OVERRIDES_H

#include "level_tweak_protocol.h"
#include <string>
#include <unordered_map>

namespace level_tweak {
struct Override {
    uint32_t preset = Max;
    uint32_t view_fixed = 0;
};
using Catalog = std::unordered_map<std::string, Override>;
// Transactional: malformed files leave the destination unchanged. Missing
// files are an empty catalog. Unknown metadata is validated then skipped.
bool load_catalog(const std::string &path, bool shipped, Catalog &out);
std::string mission_key(const char *scn, size_t size);
Override resolve(const std::string &scn, const Catalog &shipped, const Catalog &user);
float distance_world(Override value, uint32_t live_view_fixed);
}
#endif
