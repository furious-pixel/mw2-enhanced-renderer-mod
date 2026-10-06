#include "level_overrides.h"

#include <algorithm>
#include <charconv>
#include <cerrno>
#include <cmath>
#include <fstream>
#include <map>
#include <stdexcept>
#include <string_view>

namespace level_tweak {
namespace {
// Small bounded JSON reader for the override schema, without a retained DOM.
// Views reference the input only during load. Strings decode ASCII escapes;
// non-ASCII metadata remains opaque and can never become a mission key.
struct Json {
    std::string_view text;
    size_t pos = 0;
    void ws() { while (pos < text.size() && (text[pos] == ' ' || text[pos] == '\n' || text[pos] == '\r' || text[pos] == '\t')) ++pos; }
    bool take(char c) { ws(); if (pos == text.size() || text[pos] != c) return false; ++pos; return true; }
    void need(char c) { if (!take(c)) throw std::runtime_error("invalid JSON punctuation"); }
    std::string string() {
        need('"');
        std::string s;
        while (pos < text.size()) {
            unsigned char c = text[pos++];
            if (c == '"') return s;
            if (c < 32) break;
            if (c == '\\') {
                if (pos == text.size()) break;
                c = text[pos++];
                if (c == 'u') {
                    if (text.size() - pos < 4) break;
                    unsigned value = 0;
                    auto result = std::from_chars(text.data() + pos, text.data() + pos + 4, value, 16);
                    if (result.ec != std::errc() || result.ptr != text.data() + pos + 4) break;
                    pos += 4;
                    c = value < 128 ? (unsigned char)value : 0xff;
                } else {
                    const std::string escapes = "\"\\/bfnrt";
                    const char decoded[] = {'"', '\\', '/', '\b', '\f', '\n', '\r', '\t'};
                    size_t i = escapes.find(c);
                    if (i == std::string::npos) break;
                    c = decoded[i];
                }
            }
            s += (char)c;
        }
        throw std::runtime_error("invalid JSON string");
    }
    double number() {
        ws();
        const size_t start = pos;
        if (pos < text.size() && text[pos] == '-') ++pos;
        if (pos == text.size()) throw std::runtime_error("missing number");
        if (text[pos] == '0') ++pos;
        else {
            if (text[pos] < '1' || text[pos] > '9') throw std::runtime_error("invalid number");
            while (pos < text.size() && text[pos] >= '0' && text[pos] <= '9') ++pos;
        }
        auto digits = [&]() {
            size_t begin = pos;
            while (pos < text.size() && text[pos] >= '0' && text[pos] <= '9') ++pos;
            if (begin == pos) throw std::runtime_error("missing digits");
        };
        if (pos < text.size() && text[pos] == '.') { ++pos; digits(); }
        if (pos < text.size() && (text[pos] == 'e' || text[pos] == 'E')) {
            ++pos;
            if (pos < text.size() && (text[pos] == '+' || text[pos] == '-')) ++pos;
            digits();
        }
        double value = 0;
        auto result = std::from_chars(text.data() + start, text.data() + pos, value);
        if (result.ec != std::errc() || result.ptr != text.data() + pos || !std::isfinite(value))
            throw std::runtime_error("number out of range");
        return value;
    }
    void skip(unsigned depth = 0) {
        ws();
        if (depth > 16 || pos == text.size()) throw std::runtime_error("invalid JSON nesting");
        if (text[pos] == '"') { string(); return; }
        if (take('{')) {
            if (take('}')) return;
            do { string(); need(':'); skip(depth + 1); } while (take(','));
            need('}'); return;
        }
        if (take('[')) {
            if (take(']')) return;
            do { skip(depth + 1); } while (take(','));
            need(']'); return;
        }
        for (auto literal : {"true", "false", "null"}) {
            std::string_view word(literal);
            if (text.substr(pos, word.size()) == word) { pos += word.size(); return; }
        }
        number();
    }
    std::map<std::string, std::string_view> object() {
        std::map<std::string, std::string_view> fields;
        need('{');
        if (!take('}')) {
            do {
                std::string key = string(); need(':'); ws();
                size_t start = pos; skip();
                if (!fields.emplace(key, text.substr(start, pos - start)).second)
                    throw std::runtime_error("duplicate JSON key");
            } while (take(','));
            need('}');
        }
        ws();
        if (pos != text.size()) throw std::runtime_error("trailing JSON data");
        return fields;
    }
};
}

std::string mission_key(const char *scn, size_t size)
{
    std::string key;
    for (size_t i = 0; i < size && scn[i]; ++i) {
        unsigned char c = scn[i];
        if (!((c >= 'a' && c <= 'z') || (c >= 'A' && c <= 'Z') ||
              (c >= '0' && c <= '9') || c == '_' || c == '-' || c == '.')) return {};
        key += c >= 'A' && c <= 'Z' ? (char)(c + 32) : (char)c;
    }
    if (key.size() >= 32 || key.find("scn") == std::string::npos) return {};
    return key;
}

bool load_catalog(const std::string &path, bool shipped, Catalog &out)
{
    try {
        std::ifstream file(path, std::ios::binary | std::ios::ate);
        if (!file) {
            // Missing is normal; unreadable existing files are errors.
            if (errno != ENOENT) return false;
            out.clear(); return true;
        }
        auto size = file.tellg();
        if (size < 0 || size > 1024 * 1024) return false;
        std::string text((size_t)size, '\0');
        file.seekg(0);
        if (!file.read(text.data(), size)) return false;
        auto root = Json{text}.object();
        if (!root.count("schema_version") || !root.count("overrides")) return false;
        std::string_view schema_text = root.at("schema_version");
        unsigned schema = 0;
        auto parsed = std::from_chars(schema_text.data(), schema_text.data() + schema_text.size(), schema);
        if (parsed.ec != std::errc() || parsed.ptr != schema_text.data() + schema_text.size() ||
            (schema != 1 && schema != 2)) return false;
        Catalog next;
        for (auto &entry : Json{root.at("overrides")}.object()) {
            std::string key = mission_key(entry.first.c_str(), entry.first.size() + 1);
            if (key.empty() || key.size() != entry.first.size()) return false;
            auto row = Json{entry.second}.object();
            Override value{shipped ? Shipped : Custom, 0};
            if (row.count("preset")) {
                std::string name = Json{row.at("preset")}.string();
                const char *names[] = {"game", "shipped", "max", "custom", "game-radial", "farpatcher"};
                value.preset = PresetCount;
                for (uint32_t i = 0; i < PresetCount; ++i) if (name == names[i]) value.preset = i;
                if (value.preset == PresetCount) return false;
            }
            if (shipped) value.preset = Shipped;
            if (row.count("metres")) {
                double metres = Json{row.at("metres")}.number();
                if (metres < 0 || metres > UINT32_MAX / 100.0) return false;
                if ((!shipped && value.preset != Farpatcher && value.preset != Shipped) || metres < 8000)
                    value.view_fixed = (uint32_t)std::nearbyint(metres * 100.0);
            }
            if (value.preset == Max) value.view_fixed = 0;
            if (!next.emplace(key, value).second) return false;
        }
        out.swap(next);
        return true;
    } catch (const std::exception &) { return false; }
}

Override resolve(const std::string &scn, const Catalog &shipped, const Catalog &user)
{
    auto s = shipped.find(scn), u = user.find(scn);
    if (u != user.end()) {
        Override value = u->second;
        if (value.preset == Shipped && s != shipped.end() && s->second.view_fixed)
            value.view_fixed = s->second.view_fixed;
        if (value.preset == Shipped && !value.view_fixed) return {};
        return value;
    }
    return s != shipped.end() && s->second.view_fixed ? s->second : Override{};
}

float distance_world(Override value, uint32_t live_view_fixed)
{
    if (value.preset == Max) return 0;
    if (value.preset == Game || value.preset == GameRadial)
        return (float)(live_view_fixed / 65536.0 * (value.preset == GameRadial ? std::sqrt(2.0) : 1.0));
    return value.view_fixed / 65536.0f;
}
}
