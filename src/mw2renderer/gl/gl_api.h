#pragma once
#ifndef NOMINMAX
#define NOMINMAX
#endif
#include <GL/glcorearb.h>

using Mw2erGlProc = void (*)(void);
using Mw2erGlResolver = Mw2erGlProc (*)(const char *);

#define MW2ER_GL(type, name) extern type name;
#include "gl_functions.inc"
#undef MW2ER_GL
extern PFNGLPUSHDEBUGGROUPPROC glPushDebugGroup;
extern PFNGLPOPDEBUGGROUPPROC glPopDebugGroup;

// Called only with the owning context current. Failure clears every pointer.
bool mw2er_load_gl(Mw2erGlResolver resolver);
