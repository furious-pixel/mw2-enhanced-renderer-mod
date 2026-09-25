#include "text_util.h"

#ifndef NOMINMAX
#define NOMINMAX
#endif
#include <windows.h>

#include <algorithm>

void mw2er_cp437_to_utf8(const uint8_t *source, int32_t length,
                         char *destination, int32_t capacity)
{
    if (!destination || capacity <= 0) return;
    destination[0] = '\0';
    if (!source || length <= 0) return;
    int32_t count = 0;
    while (count < std::min(length, 256) && source[count]) ++count;
    if (!count) return;

    wchar_t stack_wide[256];
    const int wide_count = MultiByteToWideChar(
        437, 0, reinterpret_cast<const char *>(source), count,
        stack_wide, count);
    if (wide_count <= 0) return;
    const int required = WideCharToMultiByte(
        CP_UTF8, 0, stack_wide, wide_count, nullptr, 0, nullptr, nullptr);
    if (required <= 0) return;
    const int converted = WideCharToMultiByte(
        CP_UTF8, 0, stack_wide, wide_count, destination,
        std::min(required, capacity - 1), nullptr, nullptr);
    if (converted > 0) destination[converted] = '\0';
}
