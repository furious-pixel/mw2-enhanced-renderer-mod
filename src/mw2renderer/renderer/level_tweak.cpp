#include "level_tweak.h"
#include "level_overrides.h"
#include "scene_extract.h"
#include "mw2er_internal.h"
#include "font.h"
#include "gl_api.h"

#ifndef WIN32_LEAN_AND_MEAN
#define WIN32_LEAN_AND_MEAN
#endif
#define NOMINMAX
#include <windows.h>
#include <bcrypt.h>
#include <algorithm>
#include <cstdio>
#include <cstdlib>
#include <cstring>

using namespace level_tweak;
namespace {
constexpr int status_font_slot = 1401;
Catalog shipped, user;
std::string current_scn, channel;
Override resolved;
float conceal_far = 0;
bool enabled = false;
uint64_t renderer = 0, mission = 0, next_poll = 0, next_attach = 0;
Command accepted{};
Status status{};
char overlay[128]{};
char captured_name[32]{};
HANDLE mapping = nullptr, mutex = nullptr, lease = nullptr;
Shared *shared = nullptr;

void detach() noexcept
{
    if (shared) UnmapViewOfFile(shared);
    if (mapping) CloseHandle(mapping);
    if (mutex) CloseHandle(mutex);
    if (lease) CloseHandle(lease);
    shared = nullptr; mapping = mutex = lease = nullptr;
}

bool valid_header() noexcept
{
    return shared->magic == magic && shared->version == version &&
           shared->size == sizeof(Shared) && shared->reserved == 0;
}

bool lock() noexcept
{
    DWORD result = WaitForSingleObject(mutex, 0);
    if (result == WAIT_ABANDONED) {
        // A process may have died midway through a row. Never consume
        // a partial command or acknowledge an uncommitted save.
        shared->command = {};
        shared->status = {};
        shared->controls = {};
        accepted = {};
    }
    return result == WAIT_OBJECT_0 || result == WAIT_ABANDONED;
}

void attach(uint64_t now)
{
    if (shared || now < next_attach) return;
    next_attach = now + 1000;
    mapping = OpenFileMappingA(FILE_MAP_ALL_ACCESS, FALSE, channel.c_str());
    if (!mapping) return;
    shared = (Shared *)MapViewOfFile(mapping, FILE_MAP_ALL_ACCESS, 0, 0, sizeof(Shared));
    mutex = OpenMutexA(SYNCHRONIZE | MUTEX_MODIFY_STATE, FALSE, (channel + ".data").c_str());
    if (!shared || !mutex) { detach(); return; }
    // Event existence is a process lease, independent of callback threads.
    lease = CreateEventA(nullptr, TRUE, FALSE, (channel + ".renderer").c_str());
    if (!lease || GetLastError() == ERROR_ALREADY_EXISTS) { detach(); return; }
    if (!lock()) { detach(); return; }
    bool valid = valid_header();
    ReleaseMutex(mutex);
    if (!valid) { detach(); return; }
    mw2er_log("mw2renderer: level tweak channel attached");
}

bool fresh(uint64_t stamp, uint64_t now)
{
    return stamp != 0 && stamp <= now && now - stamp <= timeout_ms;
}

void publish(uint64_t now) noexcept
{
    if (!shared || !lock()) return;
    if (valid_header()) {
        status.heartbeat_ms = now;
        shared->status = status;
    }
    ReleaseMutex(mutex);
}
}

void mw2er_level_init()
{
    mw2er_level_shutdown();
    shipped.clear(); user.clear(); mission = 0;
    const std::string base = mw2er_mod_dir();
    if (!load_catalog(base + "/level_overrides.json", true, shipped))
        mw2er_log("mw2renderer: invalid shipped level overrides; using unconstrained defaults");
    if (!load_catalog(base + "/user_level_overrides.json", false, user))
        mw2er_log("mw2renderer: invalid user level overrides; using shipped defaults");
    const char *flag = std::getenv("MW2_LEVEL_TWEAK");
    enabled = flag && std::strcmp(flag, "1") == 0;
    const char *name = std::getenv("MW2_LEVEL_TWEAK_SHM");
    channel = name ? name : "";
    // No default global channel: accidental launches must not control another
    // game. The launcher passes a fresh 128-bit hexadecimal channel identifier.
    if (channel.size() != 32 || channel.find_first_not_of("0123456789abcdef") != std::string::npos) {
        if (enabled) mw2er_log("mw2renderer: level tweak disabled: missing or invalid channel id");
        enabled = false;
    }
    if (enabled && (BCryptGenRandom(nullptr, (PUCHAR)&renderer, sizeof(renderer), BCRYPT_USE_SYSTEM_PREFERRED_RNG) != 0 || !renderer)) {
        mw2er_log("mw2renderer: level tweak disabled: session identity generation failed");
        enabled = false;
    }
    channel = "Local\\mw2_level_tweak_v4_" + channel;
    next_poll = next_attach = 0;
    mw2er_level_mission_reset();
}

void mw2er_level_shutdown() noexcept
{
    status = {};
    if (shared) publish(GetTickCount64());
    detach(); enabled = false; renderer = 0;
    conceal_far = 0; overlay[0] = 0;
}

void mw2er_level_mission_reset() noexcept
{
    ++mission;
    current_scn.clear(); resolved = {}; accepted = {};
    std::memset(captured_name, 0, sizeof(captured_name));
    conceal_far = 0; overlay[0] = 0;
    status = {}; status.renderer = renderer; status.mission = mission; status.preset = Max;
    next_poll = 0;
    if (shared) publish(GetTickCount64());
}

void mw2er_level_capture(const Mw2erSceneExtract &scene)
{
    // Reuse the canonical captured name and native far depth. No extra memory
    // snapshot, node walk, or resource lookup belongs to this feature.
    if (std::memcmp(captured_name, scene.mission_name, sizeof(captured_name)) != 0) {
        mw2er_level_mission_reset();
        std::memcpy(captured_name, scene.mission_name, sizeof(captured_name));
        current_scn = mission_key(captured_name, sizeof(captured_name));
        resolved = resolve(current_scn, shipped, user);
    }
    const uint32_t live = scene.camera.far_depth_fixed > 0 ? (uint32_t)scene.camera.far_depth_fixed / 4 : 0;
    Override applied = resolved;
    uint64_t now = 0;
    bool poll = false;
    if (enabled) {
        now = GetTickCount64();
        if (now >= next_poll) {
            poll = true;
            next_poll = now + 100;
            attach(now);
            if (shared && lock()) {
                Command command = shared->command;
                bool valid = valid_header();
                ReleaseMutex(mutex);
                if (!valid) { detach(); accepted = {}; }
                else if (command.session && command.renderer == renderer && command.mission == mission &&
                         command.revision && command.preset < PresetCount && fresh(command.heartbeat_ms, now) &&
                         std::memchr(command.scn, 0, sizeof(command.scn)) &&
                         current_scn == command.scn && !current_scn.empty() &&
                         (command.save_revision == 0 || command.save_revision == command.revision) &&
                         (command.session != accepted.session || command.revision >= accepted.revision)) {
                    // A revision is immutable. Only its heartbeat may advance.
                    if (command.session != accepted.session || command.revision != accepted.revision ||
                        (command.preset == accepted.preset && command.view_fixed == accepted.view_fixed &&
                         command.save_revision == accepted.save_revision)) {
                        if (command.session != accepted.session) status.save_ack = 0;
                        accepted = command;
                    }
                }
            }
        }
        if (accepted.session && fresh(accepted.heartbeat_ms, now) && !current_scn.empty()) {
            applied = {accepted.preset, accepted.view_fixed};
            if (accepted.save_revision && (status.command_session != accepted.session || status.save_ack != accepted.save_revision)) {
                // The sidecar publishes this only after an atomic, flushed save.
                user[current_scn] = applied;
                resolved = resolve(current_scn, shipped, user);
                status.save_ack = accepted.save_revision;
            }
            status.command_session = accepted.session;
            status.command_revision = accepted.revision;
        } else {
            // Unsaved preview expires. Durable values remain in the catalog.
            status.command_revision = 0;
        }
    }
    conceal_far = current_scn.empty() ? 0 : distance_world(applied, live);
    if (!enabled || !poll) return;
    status.renderer = renderer; status.mission = mission;
    status.in_mission = !current_scn.empty(); status.preset = applied.preset;
    status.live_view_fixed = live;
    status.view_fixed = applied.preset == Game || applied.preset == GameRadial ? live : applied.preset == Max ? 0 : applied.view_fixed;
    std::snprintf(status.scn, sizeof(status.scn), "%s", current_scn.c_str());
    publish(now);
    const char *names[] = {"game", "shipped", "max", "custom", "game-radial", "farpatcher"};
    if (current_scn.empty()) overlay[0] = 0;
    else if (applied.preset == Max) std::snprintf(overlay, sizeof(overlay), "%s  max  unconstrained", status.scn);
    else std::snprintf(overlay, sizeof(overlay), "%s  %s  %.2fm", status.scn, names[applied.preset], conceal_far * 655.36);
}

float mw2er_level_conceal_far() { return conceal_far; }

void mw2er_level_draw_status(unsigned framebuffer, int width, int height)
{
    if (!enabled || !overlay[0]) return;
    Mw2erTextMetrics metrics{};
    if (mw2er_font_measure(status_font_slot, overlay, 18, 0, &metrics) != MW2ER_OK) return;
    glBindFramebuffer(GL_FRAMEBUFFER, framebuffer);
    glViewport(0, 0, width, height);
    glDisable(GL_DEPTH_TEST);
    glDisable(GL_CULL_FACE);
    glDisable(GL_SCISSOR_TEST);
    const float color[] = {0.35f, 1.0f, 0.35f, 0.92f};
    mw2er_font_draw(status_font_slot, overlay, 18, 0, std::max(8.0f, width - metrics.width - 12),
                   std::max(8.0f, height - metrics.height - 10), 1, color, width, height);
    glBindFramebuffer(GL_FRAMEBUFFER, 0);
}
