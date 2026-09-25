#ifndef MW2ER_SCENE_DRAW_H
#define MW2ER_SCENE_DRAW_H

#include <stdint.h>
#include "scene_extract.h"

int32_t mw2er_scene_resources_init(void);
void mw2er_scene_resources_shutdown(void);
void mw2er_scene_process_init(void);
void mw2er_scene_process_shutdown(void);
void mw2er_scene_mission_reset(void);
int32_t mw2er_scene_capture(Mw2erRenderView primary_view);
int32_t mw2er_scene_draw(int32_t logical_w, int32_t logical_h, int32_t scene_w, int32_t scene_h);
int32_t mw2er_scene_draw_view(const Mw2erCamera *camera,
                              int32_t logical_w, int32_t logical_h,
                              int32_t scene_w, int32_t scene_h,
                              Mw2erRenderView render_view);
int32_t mw2er_scene_draw_target(const Mw2erCamera *camera,
                                int32_t logical_w, int32_t logical_h,
                                int32_t scene_w, int32_t scene_h);
float mw2er_scene_output_focal(const Mw2erCamera &camera,
                               int32_t width, int32_t height);
Mw2erSceneExtract *mw2er_scene_extract_current(void);
int32_t mw2er_scene_capture_target(uint32_t root, uint32_t entity,
                                   int display_mode,
                                   const Mw2erCamera *camera);
void mw2er_scene_last_cpu_timing(double *extract_ms, double *draw_submit_ms);

#endif
