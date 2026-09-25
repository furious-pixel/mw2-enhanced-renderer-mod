#include "gl_api.h"
#include <cstdio>
#include <cstring>

#define MW2ER_GL(type, name) type name = nullptr;
#include "gl_functions.inc"
#undef MW2ER_GL
PFNGLPUSHDEBUGGROUPPROC glPushDebugGroup = nullptr;
PFNGLPOPDEBUGGROUPPROC glPopDebugGroup = nullptr;

static void reset_gl()
{
#define MW2ER_GL(type, name) name = nullptr;
#include "gl_functions.inc"
#undef MW2ER_GL
    glPushDebugGroup = nullptr;
    glPopDebugGroup = nullptr;
}

bool mw2er_load_gl(Mw2erGlResolver resolver)
{
    reset_gl();
    if (!resolver) return false;
    glGetString = reinterpret_cast<PFNGLGETSTRINGPROC>(resolver("glGetString"));
    const char *version = glGetString
        ? reinterpret_cast<const char *>(glGetString(GL_VERSION)) : nullptr;
    int major = 0, minor = 0;
    if (!version || std::sscanf(version, "%d.%d", &major, &minor) != 2 ||
        major < 3 || (major == 3 && minor < 3)) {
        reset_gl();
        return false;
    }
    bool complete = true;
#define MW2ER_GL(type, name) \
    name = reinterpret_cast<type>(resolver(#name)); \
    complete = complete && name != nullptr;
#include "gl_functions.inc"
#undef MW2ER_GL
    if (!complete) {
        reset_gl();
        return false;
    }
    bool debug_groups = major > 4 || (major == 4 && minor >= 3);
    if (!debug_groups) {
        GLint count = 0;
        glGetIntegerv(GL_NUM_EXTENSIONS, &count);
        for (GLint i = 0; i < count; ++i) {
            const char *extension = reinterpret_cast<const char *>(glGetStringi(GL_EXTENSIONS, i));
            if (extension && std::strcmp(extension, "GL_KHR_debug") == 0) {
                debug_groups = true;
                break;
            }
        }
    }
    if (debug_groups) {
        glPushDebugGroup = reinterpret_cast<PFNGLPUSHDEBUGGROUPPROC>(resolver("glPushDebugGroup"));
        glPopDebugGroup = reinterpret_cast<PFNGLPOPDEBUGGROUPPROC>(resolver("glPopDebugGroup"));
        if (!glPushDebugGroup || !glPopDebugGroup) {
            glPushDebugGroup = nullptr;
            glPopDebugGroup = nullptr;
        }
    }
    return true;
}
