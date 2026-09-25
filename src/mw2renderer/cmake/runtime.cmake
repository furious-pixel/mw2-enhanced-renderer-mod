# Explicit runtime assets: never copy logs, user settings, or a stale DLL.
get_filename_component(_mod_root "${CMAKE_CURRENT_SOURCE_DIR}/../.." ABSOLUTE)
install(TARGETS mw2renderer RUNTIME DESTINATION mw2mods COMPONENT Runtime)
install(FILES "${_mod_root}/LICENSE" "${_mod_root}/THIRD_PARTY_NOTICES.md"
    DESTINATION . COMPONENT Runtime)
install(DIRECTORY "${_mod_root}/licenses/" DESTINATION licenses COMPONENT Runtime)
set(_runtime_assets
    "mw2mods/fonts/FreeType-LICENSE.txt"
    "mw2mods/fonts/OFL.txt"
    "mw2mods/fonts/Squarish Sans CT Regular.ttf"
    "mw2mods/shaders/blit.frag"
    "mw2mods/shaders/blit.vert"
    "mw2mods/shaders/camera_view_blit.frag"
    "mw2mods/shaders/camera_view_blit.vert"
    "mw2mods/shaders/camo_texmap.frag"
    "mw2mods/shaders/font.frag"
    "mw2mods/shaders/font.vert"
    "mw2mods/shaders/geometry.frag"
    "mw2mods/shaders/geometry.vert"
    "mw2mods/shaders/hud_scale.frag"
    "mw2mods/shaders/hud_scale.vert"
    "mw2mods/shaders/indexed_geometry.frag"
    "mw2mods/shaders/indexed_geometry.vert"
    "mw2mods/shaders/indexed_texmap.frag"
    "mw2mods/shaders/indexed_texmap.vert"
    "mw2mods/shaders/indexed_texmap_common.glsl"
    "mw2mods/shaders/message_bar.frag"
    "mw2mods/shaders/message_bar.vert"
    "mw2mods/shaders/mode4.frag"
    "mw2mods/shaders/mode4.vert"
    "mw2mods/shaders/overlay_line.frag"
    "mw2mods/shaders/overlay_line.vert"
    "mw2mods/shaders/overlay_rect.frag"
    "mw2mods/shaders/overlay_rect.vert"
    "mw2mods/shaders/overlay_sprite.frag"
    "mw2mods/shaders/overlay_sprite.vert"
    "mw2mods/shaders/radar_ellipse.frag"
    "mw2mods/shaders/radar_ellipse.vert"
    "mw2mods/shaders/radar_line.frag"
    "mw2mods/shaders/radar_line.vert"
    "mw2mods/shaders/rotor.frag"
    "mw2mods/shaders/rotor.vert"
    "mw2mods/shaders/rotor_outline.frag"
    "mw2mods/shaders/rotor_outline.vert"
    "mw2mods/shaders/scene_lighting.glsl"
    "mw2mods/shaders/sky.frag"
    "mw2mods/shaders/sky.vert"
    "mw2mods/shaders/texmap.frag"
    "mw2mods/shaders/texmap.vert"
    "mw2mods/shaders/textured.frag"
    "mw2mods/shaders/textured.vert"
    "mw2mods/shaders/wireframe_occluder.frag"
    "mw2mods/shaders/wireframe_occluder.vert"
    "mw2mods/terrain_block_deltas.json"
    "mw2mods/textures/msg_bar_tex.png"
    "mw2mods/textures/msg_bar_tex_dark.png"
)
foreach(_asset IN LISTS _runtime_assets)
    get_filename_component(_destination "${_asset}" DIRECTORY)
    install(FILES "${_mod_root}/${_asset}" DESTINATION "${_destination}" COMPONENT Runtime)
endforeach()
# Seed new installs without replacing existing local configuration.
install(FILES "${_mod_root}/mw2mods/mod.conf" DESTINATION mw2mods
    RENAME mod.conf.example COMPONENT Runtime)
install(FILES "${freetype_SOURCE_DIR}/LICENSE.TXT"
    "${freetype_SOURCE_DIR}/docs/GPLv2.TXT" "${freetype_SOURCE_DIR}/docs/FTL.TXT"
    DESTINATION licenses/freetype COMPONENT Runtime)
# Source package assembly adds the matching project source to this component.
install(DIRECTORY "${freetype_SOURCE_DIR}/"
    DESTINATION src/mw2renderer/third_party/freetype COMPONENT DependencySource
    PATTERN ".git" EXCLUDE)
if(MSVC)
    install(FILES "$<TARGET_PDB_FILE:mw2renderer>"
        DESTINATION symbols COMPONENT Symbols OPTIONAL)
endif()
