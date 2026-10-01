#ifndef MW2ER_FRAME_CADENCE_H
#define MW2ER_FRAME_CADENCE_H

#include <algorithm>
#include <cmath>

// CPU submission intervals only. No GPU completion or display timing implied.
struct Mw2erFrameCadence {
    static constexpr int capacity = 240;
    double intervals_ms[capacity] = {};
    double previous_seconds = -1;
    int count = 0;
    int next = 0;

    void record(double seconds)
    {
        if (!std::isfinite(seconds) || seconds < 0) return;
        if (previous_seconds >= 0 && seconds > previous_seconds) {
            intervals_ms[next] = (seconds - previous_seconds) * 1000.0;
            next = (next + 1) % capacity;
            count = std::min(count + 1, capacity);
        } else if (seconds < previous_seconds) {
            *this = {};
        }
        previous_seconds = seconds;
    }

    double interval(int oldest_index) const
    {
        return intervals_ms[(next - count + oldest_index + capacity) % capacity];
    }

    struct Summary {
        double fps = 0;
        double median_ms = 0;
        double p99_ms = 0;
        double max_ms = 0;
        int spikes = 0;
    };

    Summary summarize() const
    {
        Summary result;
        if (!count) return result;
        double sorted[capacity];
        double sum = 0;
        for (int i = 0; i < count; ++i) {
            sorted[i] = interval(i);
            sum += sorted[i];
        }
        std::sort(sorted, sorted + count);
        result.fps = count * 1000.0 / sum;
        result.median_ms = (sorted[(count - 1) / 2] + sorted[count / 2]) * 0.5;
        // Nearest-rank p99 over the same bounded window as the FPS and maximum.
        result.p99_ms = sorted[(99 * count + 99) / 100 - 1];
        result.max_ms = sorted[count - 1];
        for (int i = 0; i < count; ++i)
            result.spikes += sorted[i] > result.median_ms * 1.5;
        return result;
    }
};

#endif
