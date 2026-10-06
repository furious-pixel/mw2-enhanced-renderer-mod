#ifndef MW2ER_LEVEL_TWEAK_PROTOCOL_H
#define MW2ER_LEVEL_TWEAK_PROTOCOL_H

#include <cstddef>
#include <cstdint>

// Windows local-session mapping, little endian. All access is under the named
// data mutex; no cross-language atomics or packed/unaligned loads. Separate
// owner events admit one renderer, one sidecar, and one game-control hook.
namespace level_tweak {
constexpr uint32_t magic = 0x3454574d; // MWT4
constexpr uint32_t version = 4;
constexpr uint64_t timeout_ms = 2000;
enum Preset : uint32_t { Game, Shipped, Max, Custom, GameRadial, Farpatcher, PresetCount };
struct Command {
    uint64_t session;
    uint64_t renderer;
    uint64_t mission;
    uint64_t revision;
    uint64_t heartbeat_ms;
    uint64_t save_revision;
    uint32_t view_fixed;
    uint32_t preset;
    char scn[32];
    // Independent from the distance/save revision; consumed by the Python
    // game hook, which owns guest writes. The renderer remains read-only.
    uint64_t control_revision;
    uint32_t time_mode; // 0 normal, 1 quarter speed, 2 eight times speed
    uint32_t control_reserved;
};
struct Status {
    uint64_t renderer;
    uint64_t mission;
    uint64_t heartbeat_ms;
    uint64_t command_session;
    uint64_t command_revision;
    uint64_t save_ack;
    uint32_t preset;
    uint32_t view_fixed;
    uint32_t live_view_fixed;
    uint32_t in_mission;
    char scn[32];
};
struct Shared {
    uint32_t magic;
    uint32_t version;
    uint32_t size;
    uint32_t reserved;
    Command command;
    Status status;
    struct Controls {
        uint64_t session;
        uint64_t renderer;
        uint64_t mission;
        uint64_t revision;
        uint64_t heartbeat_ms;
        uint32_t time_mode;
        uint32_t available;
    } controls;
};
static_assert(sizeof(Command) == 104 && sizeof(Status) == 96);
static_assert(offsetof(Shared, command) == 16 && offsetof(Shared, status) == 120);
static_assert(offsetof(Shared, controls) == 216 && sizeof(Shared) == 264);
}
#endif
