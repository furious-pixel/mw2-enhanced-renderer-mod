#ifndef MW2ER_STARTUP_TRACE_H
#define MW2ER_STARTUP_TRACE_H

#include "mw2er_abi.h"

enum Mw2erStartupWorkKind {
    MW2ER_STARTUP_HIST, MW2ER_STARTUP_ATLAS, MW2ER_STARTUP_CLASSIFY,
    MW2ER_STARTUP_POLY, MW2ER_STARTUP_GPU_CEL,
    MW2ER_STARTUP_CLASSIFY_HIT, MW2ER_STARTUP_IDENTITY, MW2ER_STARTUP_WORK_COUNT
};
struct Mw2erStartupWork {
    uint32_t count[MW2ER_STARTUP_WORK_COUNT] = {};
    double ms[MW2ER_STARTUP_WORK_COUNT] = {};
};
extern Mw2erStartupWork *mw2er_startup_work;
double mw2er_startup_now_ms(void);
struct Mw2erStartupScope {
    Mw2erStartupWork *work = mw2er_startup_work;
    Mw2erStartupWorkKind kind;
    double start;
    explicit Mw2erStartupScope(Mw2erStartupWorkKind k)
        : kind(k), start(work ? mw2er_startup_now_ms() : 0.0) {}
    ~Mw2erStartupScope() {
        if (work) { ++work->count[kind]; work->ms[kind] += mw2er_startup_now_ms() - start; }
    }
};
void mw2er_startup_init(void);
void mw2er_startup_flush(const char *reason);
void mw2er_startup_capture(const Mw2erCaptureInput &input);
void mw2er_startup_present(bool loading, bool handoff, bool scene, uint32_t pending);
void mw2er_startup_texture_context(bool valid, uint64_t remap, uint64_t classification);

#endif
