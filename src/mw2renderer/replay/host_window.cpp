#include "host_window.h"

#include <SDL.h>
#include <stdio.h>

int mw2er_host_init(void)
{
    SDL_SetMainReady();
    if (SDL_Init(SDL_INIT_VIDEO | SDL_INIT_TIMER) != 0) {
        fprintf(stderr, "SDL_Init failed: %s\n", SDL_GetError());
        return 0;
    }
    return 1;
}

void mw2er_host_quit(void)
{
    SDL_Quit();
}

int mw2er_host_window_open(
    Mw2erHostWindow *w,
    int width,
    int height,
    int visible,
    const char *title)
{
    Uint32 flags;

    if (w == NULL || width <= 0 || height <= 0) {
        return 0;
    }
    w->window = NULL;
    w->gl = NULL;
    w->close_requested = 0;

    SDL_GL_SetAttribute(SDL_GL_CONTEXT_MAJOR_VERSION, 3);
    SDL_GL_SetAttribute(SDL_GL_CONTEXT_MINOR_VERSION, 3);
    SDL_GL_SetAttribute(SDL_GL_CONTEXT_PROFILE_MASK, SDL_GL_CONTEXT_PROFILE_CORE);
    SDL_GL_SetAttribute(SDL_GL_DOUBLEBUFFER, 1);
    SDL_GL_SetAttribute(SDL_GL_DEPTH_SIZE, 24);

    flags = SDL_WINDOW_OPENGL;
    if (!visible) {
        flags |= SDL_WINDOW_HIDDEN;
    }
    w->window = SDL_CreateWindow(
        title != NULL ? title : "mw2renderer",
        SDL_WINDOWPOS_UNDEFINED,
        SDL_WINDOWPOS_UNDEFINED,
        width,
        height,
        flags);
    if (w->window == NULL) {
        fprintf(stderr, "SDL_CreateWindow failed: %s\n", SDL_GetError());
        return 0;
    }
    w->gl = SDL_GL_CreateContext(w->window);
    if (w->gl == NULL) {
        fprintf(stderr, "SDL_GL_CreateContext failed: %s\n", SDL_GetError());
        SDL_DestroyWindow(w->window);
        w->window = NULL;
        return 0;
    }
    if (SDL_GL_MakeCurrent(w->window, w->gl) != 0) {
        fprintf(stderr, "SDL_GL_MakeCurrent failed: %s\n", SDL_GetError());
        mw2er_host_window_destroy(w);
        return 0;
    }
    return 1;
}

void mw2er_host_window_swap(Mw2erHostWindow *w)
{
    if (w != NULL && w->window != NULL) {
        SDL_GL_SwapWindow(w->window);
    }
}

void mw2er_host_window_poll(Mw2erHostWindow *w)
{
    SDL_Event event;

    (void)w;
    while (SDL_PollEvent(&event)) {
        if (event.type == SDL_QUIT && w != NULL) {
            w->close_requested = 1;
        }
    }
}

int mw2er_host_window_close_requested(const Mw2erHostWindow *w)
{
    return w == NULL || w->close_requested;
}

void *mw2er_host_gl_proc(const char *name)
{
    return (void *)SDL_GL_GetProcAddress(name);
}

void mw2er_host_window_destroy(Mw2erHostWindow *w)
{
    if (w == NULL) {
        return;
    }
    if (w->gl != NULL) {
        SDL_GL_DeleteContext(w->gl);
        w->gl = NULL;
    }
    if (w->window != NULL) {
        SDL_DestroyWindow(w->window);
        w->window = NULL;
    }
}
