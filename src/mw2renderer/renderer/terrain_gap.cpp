#include "terrain_gap.h"
#include "mw2er_internal.h"

#include <ctype.h>
#include <stdint.h>
#include <stdio.h>
#include <stdlib.h>
#include <string.h>
#include <string>
#include <vector>

struct TerrainDelta {
    int nvert;
    int nfaces;
    int64_t sum_x;
    int64_t sum_z;
    float dx;
    float dz;
};

struct TerrainLevel {
    char name[32];
    std::vector<TerrainDelta> entries;
};

static std::vector<TerrainLevel> g_levels;
static int g_loaded;

static const char *skip_ws(const char *p)
{
    while (*p && isspace((unsigned char)*p)) {
        ++p;
    }
    return p;
}

static const char *parse_string(const char *p, char *out, size_t out_size)
{
    p = skip_ws(p);
    if (*p != '"') {
        return NULL;
    }
    ++p;
    size_t n = 0;
    while (*p && *p != '"') {
        char c = *p++;
        if (c == '\\' && *p) {
            c = *p++;
        }
        if (n + 1 < out_size) {
            out[n++] = c;
        }
    }
    if (*p != '"') {
        return NULL;
    }
    ++p;
    out[n] = '\0';
    return p;
}

static const char *skip_json_value(const char *p)
{
    p = skip_ws(p);
    if (*p == '"') {
        char tmp[8];
        return parse_string(p, tmp, sizeof(tmp));
    }
    if (*p == '{' || *p == '[') {
        char open = *p;
        char close = (open == '{') ? '}' : ']';
        int depth = 1;
        ++p;
        while (*p && depth > 0) {
            if (*p == '"') {
                char tmp[8];
                p = parse_string(p, tmp, sizeof(tmp));
                if (p == NULL) {
                    return NULL;
                }
                continue;
            }
            if (*p == open) {
                depth += 1;
            } else if (*p == close) {
                depth -= 1;
            }
            ++p;
        }
        return p;
    }
    if (*p == '-' || *p == '+' || (*p >= '0' && *p <= '9')) {
        while (*p && *p != ',' && *p != '}' && *p != ']' && !isspace((unsigned char)*p)) {
            ++p;
        }
        return p;
    }
    if (strncmp(p, "true", 4) == 0) {
        return p + 4;
    }
    if (strncmp(p, "false", 5) == 0) {
        return p + 5;
    }
    if (strncmp(p, "null", 4) == 0) {
        return p + 4;
    }
    return NULL;
}

static const char *parse_i64(const char *p, int64_t *out)
{
    char *end = NULL;
    p = skip_ws(p);
    *out = _strtoi64(p, &end, 10);
    if (end == p) {
        return NULL;
    }
    return end;
}

static const char *parse_f64(const char *p, double *out)
{
    char *end = NULL;
    p = skip_ws(p);
    *out = strtod(p, &end);
    if (end == p) {
        return NULL;
    }
    return end;
}

static const char *parse_entry(const char *p, TerrainDelta *e)
{
    p = skip_ws(p);
    if (*p != '{') {
        return NULL;
    }
    ++p;
    memset(e, 0, sizeof(*e));
    while (*p) {
        char key[32];
        p = skip_ws(p);
        if (*p == '}') {
            return p + 1;
        }
        p = parse_string(p, key, sizeof(key));
        if (p == NULL) {
            return NULL;
        }
        p = skip_ws(p);
        if (*p != ':') {
            return NULL;
        }
        ++p;
        if (strcmp(key, "nvert") == 0 || strcmp(key, "nfaces") == 0 ||
            strcmp(key, "sum_x") == 0 || strcmp(key, "sum_z") == 0) {
            int64_t num = 0;
            p = parse_i64(p, &num);
            if (p == NULL) {
                return NULL;
            }
            if (strcmp(key, "nvert") == 0) {
                e->nvert = (int)num;
            } else if (strcmp(key, "nfaces") == 0) {
                e->nfaces = (int)num;
            } else if (strcmp(key, "sum_x") == 0) {
                e->sum_x = num;
            } else {
                e->sum_z = num;
            }
        } else if (strcmp(key, "dx") == 0 || strcmp(key, "dz") == 0) {
            double num = 0.0;
            p = parse_f64(p, &num);
            if (p == NULL) {
                return NULL;
            }
            if (strcmp(key, "dx") == 0) {
                e->dx = (float)num;
            } else {
                e->dz = (float)num;
            }
        } else {
            p = skip_json_value(p);
            if (p == NULL) {
                return NULL;
            }
        }
        p = skip_ws(p);
        if (*p == ',') {
            ++p;
        }
    }
    return NULL;
}

static const char *find_levels_object(const char *json)
{
    const char *p = json;
    while ((p = strstr(p, "\"levels\"")) != NULL) {
        const char *q = skip_ws(p + 8);
        if (*q == ':') {
            q = skip_ws(q + 1);
            if (*q == '{') {
                return q;
            }
        }
        p += 8;
    }
    return NULL;
}

static void parse_levels(const char *json)
{
    const char *p = find_levels_object(json);
    if (p == NULL) {
        return;
    }
    ++p;
    while (*p) {
        TerrainLevel level;
        char name[32];
        p = skip_ws(p);
        if (*p == '}') {
            return;
        }
        p = parse_string(p, name, sizeof(name));
        if (p == NULL) {
            return;
        }
        p = skip_ws(p);
        if (*p != ':') {
            return;
        }
        ++p;
        p = skip_ws(p);
        if (*p != '[') {
            p = skip_json_value(p);
            if (p == NULL) {
                return;
            }
        } else {
            snprintf(level.name, sizeof(level.name), "%s", name);
            for (char *c = level.name; *c; ++c) {
                *c = (char)tolower((unsigned char)*c);
            }
            ++p;
            while (*p) {
                TerrainDelta e;
                p = skip_ws(p);
                if (*p == ']') {
                    ++p;
                    break;
                }
                p = parse_entry(p, &e);
                if (p == NULL) {
                    return;
                }
                level.entries.push_back(e);
                p = skip_ws(p);
                if (*p == ',') {
                    ++p;
                }
            }
            if (!level.entries.empty()) {
                g_levels.push_back(std::move(level));
            }
        }
        p = skip_ws(p);
        if (*p == ',') {
            ++p;
        }
    }
}

void mw2er_terrain_gap_load(const char *json_path)
{
    FILE *f;
    long size;
    std::string buf;

    g_levels.clear();
    g_loaded = 0;
    if (json_path == NULL || json_path[0] == '\0') {
        return;
    }
    f = fopen(json_path, "rb");
    if (f == NULL) {
        return;
    }
    if (fseek(f, 0, SEEK_END) != 0) {
        fclose(f);
        return;
    }
    size = ftell(f);
    if (size <= 0) {
        fclose(f);
        return;
    }
    rewind(f);
    buf.resize((size_t)size + 1);
    if (fread(&buf[0], 1, (size_t)size, f) != (size_t)size) {
        fclose(f);
        return;
    }
    fclose(f);
    buf[(size_t)size] = '\0';
    parse_levels(buf.c_str());
    g_loaded = 1;
    {
        char msg[128];
        snprintf(
            msg,
            sizeof(msg),
            "mw2renderer: terrain gap levels=%d",
            (int)g_levels.size());
        mw2er_log(msg);
    }
}

int mw2er_terrain_delta(
    const char *mission_name,
    int nvert,
    int nfaces,
    int64_t sum_x,
    int64_t sum_z,
    float *dx,
    float *dz)
{
    char key[32];
    size_t i;
    size_t n;

    if (!g_loaded || mission_name == NULL || mission_name[0] == '\0') {
        return 0;
    }
    n = strlen(mission_name);
    if (n >= sizeof(key)) {
        n = sizeof(key) - 1;
    }
    for (i = 0; i < n; ++i) {
        key[i] = (char)tolower((unsigned char)mission_name[i]);
    }
    key[n] = '\0';
    for (i = 0; i < g_levels.size(); ++i) {
        if (strcmp(g_levels[i].name, key) != 0) {
            continue;
        }
        for (size_t e = 0; e < g_levels[i].entries.size(); ++e) {
            const TerrainDelta &d = g_levels[i].entries[e];
            if (d.nvert == nvert && d.nfaces == nfaces && d.sum_x == sum_x &&
                d.sum_z == sum_z) {
                *dx = d.dx;
                *dz = d.dz;
                return 1;
            }
        }
        return 0;
    }
    return 0;
}
