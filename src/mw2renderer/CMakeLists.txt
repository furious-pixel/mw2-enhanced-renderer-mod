cmake_minimum_required(VERSION 3.21)
project(mw2renderer LANGUAGES C CXX)

set(CMAKE_CXX_STANDARD 17)
set(CMAKE_CXX_STANDARD_REQUIRED ON)
set(CMAKE_CXX_EXTENSIONS OFF)
set(CMAKE_C_STANDARD 11)

# The renderer DLL is copied next to DOSBox-X rather than installed with a VC
# runtime redistributable. Keep its runtime self-contained in both optimized
# and debug configurations.
if(MSVC)
    set(CMAKE_MSVC_RUNTIME_LIBRARY "MultiThreaded$<$<CONFIG:Debug>:Debug>")
endif()

option(MW2ER_BUILD_TOOLS "Build replay and smoke-test hosts" OFF)

set(MW2ER_STB_INCLUDE "${CMAKE_CURRENT_SOURCE_DIR}/third_party/stb")

include(FetchContent)
if(POLICY CMP0135)
    cmake_policy(SET CMP0135 NEW)
endif()
# Headers and library always come from the same pinned source. For offline
# builds set FETCHCONTENT_SOURCE_DIR_FREETYPE to an extracted source archive.
if(EXISTS "${CMAKE_CURRENT_SOURCE_DIR}/third_party/freetype/CMakeLists.txt")
    set(FETCHCONTENT_SOURCE_DIR_FREETYPE "${CMAKE_CURRENT_SOURCE_DIR}/third_party/freetype"
        CACHE PATH "FreeType source included in the release source package")
endif()
set(BUILD_SHARED_LIBS OFF)
set(CMAKE_POLICY_DEFAULT_CMP0091 NEW)
set(CMAKE_POLICY_VERSION_MINIMUM 3.5)
set(SKIP_INSTALL_ALL ON)
foreach(_dependency ZLIB BZIP2 PNG HARFBUZZ BROTLI)
    set(FT_DISABLE_${_dependency} ON CACHE BOOL "Disable optional FreeType dependency" FORCE)
endforeach()
FetchContent_Declare(freetype
    URL https://downloads.sourceforge.net/project/freetype/freetype2/2.13.1/freetype-2.13.1.tar.xz
    URL_HASH SHA256=ea67e3b019b1104d1667aa274f5dc307d8cbd606b399bc32df308a77f1a564bf
    TLS_VERIFY TRUE
    TIMEOUT 60
)
FetchContent_MakeAvailable(freetype)

# Resolve only the APIs we use through the owning host context.
add_library(mw2er_gl STATIC gl/gl_api.cpp)
target_include_directories(mw2er_gl PUBLIC
    ${CMAKE_CURRENT_SOURCE_DIR}/gl
    ${CMAKE_CURRENT_SOURCE_DIR}/third_party/khronos/include
)
target_compile_definitions(mw2er_gl PRIVATE _CRT_SECURE_NO_WARNINGS)

add_library(mw2renderer SHARED
    renderer/mw2er_api.cpp
    renderer/compass_altimeter.cpp
    renderer/config.cpp
    renderer/terrain_gap.cpp
    renderer/gl_stub.cpp
    renderer/gl_program.cpp
    renderer/font.cpp
    renderer/hud.cpp
    renderer/image_stb.cpp
    renderer/menu.cpp
    renderer/radar.cpp
    renderer/targeting.cpp
    renderer/text_util.cpp
    renderer/presentation.cpp
    renderer/resource.cpp
    renderer/prj_archive.cpp
    renderer/scene_extract.cpp
    renderer/scene_draw.cpp
    renderer/texture.cpp
    renderer/startup_trace.cpp
)
target_compile_definitions(mw2renderer PRIVATE
    MW2ER_RENDERER_EXPORTS
    _CRT_SECURE_NO_WARNINGS
)
target_include_directories(mw2renderer
    PUBLIC
        ${CMAKE_CURRENT_SOURCE_DIR}/include
    PRIVATE
        ${CMAKE_CURRENT_SOURCE_DIR}/renderer
        ${MW2ER_STB_INCLUDE}
)
target_link_libraries(mw2renderer PRIVATE mw2er_gl freetype)
if(WIN32)
    target_link_libraries(mw2renderer PRIVATE bcrypt)
endif()
set_target_properties(mw2renderer PROPERTIES
    OUTPUT_NAME mw2renderer
    RUNTIME_OUTPUT_DIRECTORY ${CMAKE_BINARY_DIR}/bin
    LIBRARY_OUTPUT_DIRECTORY ${CMAKE_BINARY_DIR}/bin
    ARCHIVE_OUTPUT_DIRECTORY ${CMAKE_BINARY_DIR}/lib
)

include(cmake/runtime.cmake)

if(MW2ER_BUILD_TOOLS)
include(FetchContent)
set(SDL_SHARED OFF CACHE BOOL "" FORCE)
set(SDL_STATIC ON CACHE BOOL "" FORCE)
set(SDL_TEST OFF CACHE BOOL "" FORCE)
set(SDL_TESTS OFF CACHE BOOL "" FORCE)
set(SDL2_DISABLE_SDL2MAIN ON CACHE BOOL "" FORCE)
set(SDL2_DISABLE_INSTALL ON CACHE BOOL "" FORCE)
FetchContent_Declare(
    SDL2
    GIT_REPOSITORY https://github.com/libsdl-org/SDL.git
    GIT_TAG release-2.32.8
    GIT_SHALLOW TRUE
)
FetchContent_MakeAvailable(SDL2)
if(TARGET SDL2::SDL2-static)
    set(MW2ER_SDL_TARGET SDL2::SDL2-static)
elseif(TARGET SDL2-static)
    set(MW2ER_SDL_TARGET SDL2-static)
else()
    message(FATAL_ERROR "SDL2 static target not found")
endif()
file(MAKE_DIRECTORY "${CMAKE_SOURCE_DIR}/artifacts")

set(MW2ER_HOST_COMMON
    replay/host_util.cpp
    replay/host_window.cpp
)

add_executable(mw2er_smoke
    replay/mw2er_smoke.cpp
    ${MW2ER_HOST_COMMON}
)
target_include_directories(mw2er_smoke PRIVATE
    ${CMAKE_CURRENT_SOURCE_DIR}/include
    ${CMAKE_CURRENT_SOURCE_DIR}/replay
    ${MW2ER_STB_INCLUDE}
)
target_compile_definitions(mw2er_smoke PRIVATE
    MW2ER_ABI_NO_LINK
    _CRT_SECURE_NO_WARNINGS
    SDL_MAIN_HANDLED
    "MW2ER_ARTIFACTS_DIR=\"${CMAKE_SOURCE_DIR}/artifacts\""
    "MW2ER_DEFAULT_MOD_DIR=\"${CMAKE_SOURCE_DIR}/../../mw2mods\""
)
target_link_libraries(mw2er_smoke PRIVATE ${MW2ER_SDL_TARGET} mw2er_gl)
set_target_properties(mw2er_smoke PROPERTIES
    RUNTIME_OUTPUT_DIRECTORY ${CMAKE_BINARY_DIR}/bin
)

add_executable(mw2er_replay
    replay/mw2er_replay.cpp
    replay/dump.cpp
    ${MW2ER_HOST_COMMON}
)
target_include_directories(mw2er_replay PRIVATE
    ${CMAKE_CURRENT_SOURCE_DIR}/include
    ${CMAKE_CURRENT_SOURCE_DIR}/replay
    ${MW2ER_STB_INCLUDE}
)
target_compile_definitions(mw2er_replay PRIVATE
    MW2ER_ABI_NO_LINK
    _CRT_SECURE_NO_WARNINGS
    SDL_MAIN_HANDLED
    "MW2ER_DEFAULT_MOD_DIR=\"${CMAKE_SOURCE_DIR}/../../mw2mods\""
    "MW2ER_ARTIFACTS_DIR=\"${CMAKE_SOURCE_DIR}/artifacts\""
)
target_link_libraries(mw2er_replay PRIVATE ${MW2ER_SDL_TARGET} mw2er_gl)
set_target_properties(mw2er_replay PROPERTIES
    RUNTIME_OUTPUT_DIRECTORY ${CMAKE_BINARY_DIR}/bin
)

foreach(_mw2er_host mw2er_smoke mw2er_replay)
    add_custom_command(TARGET ${_mw2er_host} POST_BUILD
        COMMAND ${CMAKE_COMMAND} -E copy_if_different
            $<TARGET_FILE:mw2renderer>
            $<TARGET_FILE_DIR:${_mw2er_host}>
    )
endforeach()
endif()

if(MSVC)
    # Keep full symbols for post-mortem crash dumps without weakening the
    # optimized release binary used for performance measurements.
    set(_mw2er_release_cflags /O2 /Oi /Ot /fp:precise /Gy /Gw /Zi)
    set(_mw2er_targets mw2renderer)
    if(MW2ER_BUILD_TOOLS)
        list(APPEND _mw2er_targets mw2er_smoke mw2er_replay)
    endif()
    foreach(_mw2er_tgt IN LISTS _mw2er_targets)
        target_compile_options(${_mw2er_tgt} PRIVATE
            /W3 /utf-8
            $<$<CONFIG:Release>:${_mw2er_release_cflags}>
            $<$<CONFIG:RelWithDebInfo>:${_mw2er_release_cflags}>
        )
    endforeach()
    target_link_options(mw2renderer PRIVATE
        $<$<CONFIG:Release>:/DEBUG:FULL /OPT:REF /OPT:ICF>
    )
endif()
