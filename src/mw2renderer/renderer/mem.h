#ifndef MW2ER_MEM_H
#define MW2ER_MEM_H

#include "mw2er_abi.h"

#include <cstring>
#include <stdint.h>

/* Guest dump / live linear image. Reloc helpers add delta. */
struct Mem {
    const uint8_t *bytes;
    uint32_t size;
    uint32_t runtime_base;
    uint32_t delta;

    static Mem from(const Mw2erMemoryView &v)
    {
        Mem m;
        m.bytes = v.bytes;
        m.size = v.size;
        m.runtime_base = v.runtime_base;
        m.delta = v.delta;
        return m;
    }

    bool ok() const
    {
        return bytes != nullptr && size > 0;
    }

    uint32_t rt(uint32_t reloc) const
    {
        return reloc + delta;
    }

    const uint8_t *view(uint32_t runtime, uint32_t n) const
    {
        if (!ok() || runtime < runtime_base) {
            return nullptr;
        }
        const uint32_t off = runtime - runtime_base;
        if (off > size || n > size - off) {
            return nullptr;
        }
        return bytes + off;
    }

    bool read(uint32_t runtime, void *dst, uint32_t n) const
    {
        if (n == 0) {
            return true;
        }
        const uint8_t *src = view(runtime, n);
        if (src == nullptr) {
            return false;
        }
        std::memcpy(dst, src, n);
        return true;
    }

    uint8_t u8(uint32_t runtime) const
    {
        uint8_t v = 0;
        read(runtime, &v, 1);
        return v;
    }

    uint16_t u16(uint32_t runtime) const
    {
        uint16_t v = 0;
        read(runtime, &v, 2);
        return v;
    }

    int16_t i16(uint32_t runtime) const
    {
        return (int16_t)u16(runtime);
    }

    uint32_t u32(uint32_t runtime) const
    {
        uint32_t v = 0;
        read(runtime, &v, 4);
        return v;
    }

    int32_t i32(uint32_t runtime) const
    {
        return (int32_t)u32(runtime);
    }

    uint8_t u8_rel(uint32_t reloc) const { return u8(rt(reloc)); }
    uint16_t u16_rel(uint32_t reloc) const { return u16(rt(reloc)); }
    int16_t i16_rel(uint32_t reloc) const { return i16(rt(reloc)); }
    uint32_t u32_rel(uint32_t reloc) const { return u32(rt(reloc)); }
    int32_t i32_rel(uint32_t reloc) const { return i32(rt(reloc)); }

    bool read_rel(uint32_t reloc, void *dst, uint32_t n) const
    {
        return read(rt(reloc), dst, n);
    }
};

#endif
