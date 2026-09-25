#ifndef MW2ER_HOST_WINDOW_H
#define MW2ER_HOST_WINDOW_H

/*
 * Thin SDL2 window + OpenGL 3.3 core context.
 * There is no SDL2Window type; SDL uses SDL_Window.
 */

struct SDL_Window;

typedef struct Mw2erHostWindow {
    struct SDL_Window *window;
    void *gl;
    int close_requested;
} Mw2erHostWindow;

int mw2er_host_init(void);
void mw2er_host_quit(void);

int mw2er_host_window_open(
    Mw2erHostWindow *w,
    int width,
    int height,
    int visible,
    const char *title);
void mw2er_host_window_swap(Mw2erHostWindow *w);
void mw2er_host_window_poll(Mw2erHostWindow *w);
int mw2er_host_window_close_requested(const Mw2erHostWindow *w);
void mw2er_host_window_destroy(Mw2erHostWindow *w);
void *mw2er_host_gl_proc(const char *name);

#endif /* MW2ER_HOST_WINDOW_H */
