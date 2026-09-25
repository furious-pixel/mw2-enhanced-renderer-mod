#ifndef MW2ER_VERT_STREAM_H
#define MW2ER_VERT_STREAM_H

#include <cstddef>
#include <cstdint>
#include <initializer_list>
#include <vector>

/* Mission-lifetime CPU vertex buffer. clear() keeps capacity. */
struct VertStream {
    std::vector<float> data;

    void clear()
    {
        data.clear();
    }

    void emit(std::initializer_list<float> xs)
    {
        data.insert(data.end(), xs.begin(), xs.end());
    }

    void emit(const float *xs, int n)
    {
        data.insert(data.end(), xs, xs + n);
    }

    size_t floats() const { return data.size(); }
    const float *ptr() const { return data.empty() ? nullptr : data.data(); }
};

/* Mission-lifetime CPU element buffer. clear() keeps capacity. */
struct IndexStream {
    std::vector<uint32_t> data;

    void clear()
    {
        data.clear();
    }

    void tri(uint32_t a, uint32_t b, uint32_t c)
    {
        data.push_back(a);
        data.push_back(b);
        data.push_back(c);
    }

    void line(uint32_t a, uint32_t b)
    {
        data.push_back(a);
        data.push_back(b);
    }

    size_t count() const { return data.size(); }
    const uint32_t *ptr() const { return data.empty() ? nullptr : data.data(); }
};

#endif
