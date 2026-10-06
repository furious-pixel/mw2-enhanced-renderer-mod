#ifndef MW2ER_LEVEL_TWEAK_H
#define MW2ER_LEVEL_TWEAK_H

struct Mw2erSceneExtract;
void mw2er_level_init();
void mw2er_level_shutdown() noexcept;
void mw2er_level_mission_reset() noexcept;
void mw2er_level_capture(const Mw2erSceneExtract &scene);
float mw2er_level_conceal_far();
void mw2er_level_draw_status(unsigned framebuffer, int width, int height);

#endif
