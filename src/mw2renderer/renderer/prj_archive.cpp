#include "prj_archive.h"

#define NOMINMAX
#include <windows.h>
#include <bcrypt.h>

#include <algorithm>
#include <cstring>
#include <new>
#include <utility>

namespace {

constexpr uint64_t PROFILE_SIZE = 19960257;
constexpr uint8_t PROFILE_SHA256[32] = {
    0x74, 0xdd, 0xb4, 0xf3, 0x72, 0x1c, 0x07, 0x36,
    0xf7, 0xab, 0x59, 0xbd, 0xb2, 0xc0, 0x7e, 0x16,
    0xf6, 0x67, 0x7e, 0x36, 0x05, 0xf7, 0x78, 0x95,
    0xe6, 0xef, 0xba, 0x48, 0x9d, 0x2e, 0x87, 0x46
};
constexpr uint64_t MAX_ARCHIVE_BYTES = 512ull * 1024 * 1024;
constexpr size_t MAX_INDEX_BYTES = 8 * 1024 * 1024;
constexpr size_t MAX_RESOURCE_BYTES = 16 * 1024 * 1024;
constexpr uint64_t MAX_CEL_PIXELS = 16ull * 1024 * 1024;
constexpr uint32_t MAX_DIRECTORY_COUNT = 256;
constexpr uint32_t DATA_HEADER_BYTES = 0x3e;

uint16_t le16(const uint8_t *bytes)
{
    return static_cast<uint16_t>(bytes[0] | (uint16_t(bytes[1]) << 8));
}

uint32_t le32(const uint8_t *bytes)
{
    return uint32_t(bytes[0]) | (uint32_t(bytes[1]) << 8) |
           (uint32_t(bytes[2]) << 16) | (uint32_t(bytes[3]) << 24);
}

uint32_t archive_tag(uint32_t type)
{
    if (type == MW2ER_RESOURCE_CEL) return 0x004c4543;
    if (type == MW2ER_RESOURCE_POLY) return 0x594c4f50;
    return 0;
}

uint32_t resource_type(uint32_t tag)
{
    if (tag == archive_tag(MW2ER_RESOURCE_CEL)) return MW2ER_RESOURCE_CEL;
    if (tag == archive_tag(MW2ER_RESOURCE_POLY)) return MW2ER_RESOURCE_POLY;
    return 0;
}

bool bounded(uint64_t offset, uint64_t size, uint64_t limit)
{
    return offset <= limit && size <= limit - offset;
}

Mw2erPrjResult result(Mw2erPrjStatus status, const char *stage,
                     const char *detail, uint32_t type = 0,
                     uint32_t id = 0, uint32_t system_error = 0)
{
    return {status, stage, type, id, detail, system_error};
}

// BCrypt owns its working storage when the hash object buffer is null.
struct Sha256 {
    BCRYPT_ALG_HANDLE algorithm = nullptr;
    BCRYPT_HASH_HANDLE hash = nullptr;
    ~Sha256()
    {
        if (hash) BCryptDestroyHash(hash);
        if (algorithm) BCryptCloseAlgorithmProvider(algorithm, 0);
    }
};

Mw2erPrjResult validate_body(Mw2erResourceAsset &asset,
                            Mw2erPrjMeshKind &kind)
{
    const uint32_t type = asset.type;
    const uint32_t id = asset.resource_id;
    const std::vector<uint8_t> &body = asset.bytes;
    if (type == MW2ER_RESOURCE_CEL) {
        if (body.size() < 4)
            return result(Mw2erPrjStatus::Corrupt, "CEL", "short dimensions", type, id);
        asset.width = le16(body.data());
        asset.height = le16(body.data() + 2);
        const uint64_t pixels = uint64_t(asset.width) * asset.height;
        if (!asset.width || !asset.height || pixels > MAX_CEL_PIXELS ||
            pixels != body.size() - 4)
            return result(Mw2erPrjStatus::Corrupt, "CEL", "invalid dimensions or pixel length", type, id);
        // Source ownership transfers to the existing pixel-only CEL contract.
        asset.bytes.erase(asset.bytes.begin(), asset.bytes.begin() + 4);
        return result(Mw2erPrjStatus::Found, "CEL", "validated pixels", type, id);
    }

    if (body.size() < 32 || std::memcmp(body.data(), "WTBO", 4) != 0)
        return result(Mw2erPrjStatus::Corrupt, "WTBO", "invalid header", type, id);
    const uint32_t vertices = le16(body.data() + 24);
    const uint32_t faces = le16(body.data() + 26);
    size_t position = 32 + size_t(vertices) * 16;
    if (position > body.size())
        return result(Mw2erPrjStatus::Corrupt, "WTBO", "vertices exceed body", type, id);
    kind = faces ? Mw2erPrjMeshKind::Ordinary : Mw2erPrjMeshKind::NonDrawable;
    for (uint32_t face = 0; face < faces; ++face) {
        if (!bounded(position, 4, body.size()))
            return result(Mw2erPrjStatus::Corrupt, "WTBO", "truncated face header", type, id);
        const uint32_t corners = le16(body.data() + position + 2);
        if (!corners)
            return result(Mw2erPrjStatus::Corrupt, "WTBO", "empty face", type, id);
        const size_t raw_size = 4 + size_t(corners) * 2;
        const size_t stride = std::max(size_t(12), ((raw_size + 5) / 6) * 6);
        if (!bounded(position, stride, body.size()))
            return result(Mw2erPrjStatus::Corrupt, "WTBO", "truncated face or padding", type, id);
        for (uint32_t corner = 0; corner < corners; ++corner) {
            if (le16(body.data() + position + 4 + size_t(corner) * 2) >= vertices)
                return result(Mw2erPrjStatus::Corrupt, "WTBO", "vertex index out of range", type, id);
        }
        if (corners > 16)
            return result(Mw2erPrjStatus::Unsupported, "WTBO", "face interpretation exceeds 16 corners", type, id);
        if (corners < 3) kind = Mw2erPrjMeshKind::PointOrLine;
        position += stride;
    }
    if (id == 0x07b1 && vertices == 1 && faces == 1 &&
        kind == Mw2erPrjMeshKind::PointOrLine)
        kind = Mw2erPrjMeshKind::NonDrawable;
    // position is only the first mesh boundary. Opaque trailing bytes belong to
    // the resource and deliberately remain in asset.bytes.
    return result(Mw2erPrjStatus::Found, "WTBO", "validated complete source body", type, id);
}

} // namespace

const char *mw2er_prj_status_name(Mw2erPrjStatus status)
{
    switch (status) {
    case Mw2erPrjStatus::Found: return "FOUND";
    case Mw2erPrjStatus::NotFound: return "NOT_FOUND";
    case Mw2erPrjStatus::Unsupported: return "UNSUPPORTED_CONTAINER";
    case Mw2erPrjStatus::Corrupt: return "CORRUPT";
    case Mw2erPrjStatus::IoError: return "IO_ERROR";
    }
    return "UNSUPPORTED_CONTAINER";
}

Mw2erPrjArchive::~Mw2erPrjArchive()
{
    close();
}

void Mw2erPrjArchive::close_unlocked()
{
    if (handle_) CloseHandle(static_cast<HANDLE>(handle_));
    handle_ = nullptr;
    file_size_ = 0;
    directories_.clear();
    keys_.clear();
    state_ = Mw2erPrjResult{};
}

void Mw2erPrjArchive::close()
{
    std::lock_guard<std::mutex> lock(mutex_);
    close_unlocked();
}

Mw2erPrjResult Mw2erPrjArchive::read_range(
    uint64_t offset, size_t size, uint8_t *destination, const char *stage,
    uint32_t type, uint32_t resource_id)
{
    if (!bounded(offset, size, file_size_))
        return result(Mw2erPrjStatus::Corrupt, stage, "range exceeds archive", type, resource_id);
    LARGE_INTEGER position;
    position.QuadPart = static_cast<LONGLONG>(offset);
    if (!SetFilePointerEx(static_cast<HANDLE>(handle_), position, nullptr, FILE_BEGIN))
        return result(Mw2erPrjStatus::IoError, stage, "seek failed", type, resource_id, GetLastError());
    while (size) {
        const DWORD request = static_cast<DWORD>(std::min(size, size_t(1024 * 1024)));
        DWORD received = 0;
        if (!ReadFile(static_cast<HANDLE>(handle_), destination, request, &received, nullptr))
            return result(Mw2erPrjStatus::IoError, stage, "read failed", type, resource_id, GetLastError());
        if (!received)
            return result(Mw2erPrjStatus::IoError, stage, "unexpected end of file", type, resource_id);
        destination += received;
        size -= received;
    }
    return result(Mw2erPrjStatus::Found, stage, "read complete", type, resource_id);
}

Mw2erPrjResult Mw2erPrjArchive::open(const char *host_path)
{
    return open_impl(host_path, false);
}

#if defined(MW2ER_PRJ_TESTING) && !defined(MW2ER_RENDERER_EXPORTS)
Mw2erPrjResult Mw2erPrjArchive::open_test_profile(const char *host_path)
{
    return open_impl(host_path, true);
}
#endif

Mw2erPrjResult Mw2erPrjArchive::open_impl(const char *host_path, bool synthetic)
{
    std::lock_guard<std::mutex> lock(mutex_);
    close_unlocked();
    auto fail = [this](Mw2erPrjResult failure) {
        close_unlocked();
        state_ = failure;
        return failure;
    };
    if (!host_path || !*host_path)
        return fail(result(Mw2erPrjStatus::IoError, "open", "empty host path"));
    // Denying writes and replacement makes the hash and later reads describe
    // the same file, while allowing the game's independent read handles.
    try {
        const int characters = MultiByteToWideChar(CP_UTF8, MB_ERR_INVALID_CHARS,
                                                   host_path, -1, nullptr, 0);
        if (!characters || characters > 32768)
            return fail(result(Mw2erPrjStatus::IoError, "path", "invalid UTF8 path or path length", 0, 0,
                               characters ? ERROR_FILENAME_EXCED_RANGE : GetLastError()));
        std::vector<wchar_t> path(static_cast<size_t>(characters));
        if (MultiByteToWideChar(CP_UTF8, MB_ERR_INVALID_CHARS, host_path, -1,
                                path.data(), characters) != characters)
            return fail(result(Mw2erPrjStatus::IoError, "path", "UTF8 conversion failed", 0, 0, GetLastError()));
        HANDLE file = CreateFileW(path.data(), GENERIC_READ, FILE_SHARE_READ, nullptr,
                                  OPEN_EXISTING, FILE_ATTRIBUTE_NORMAL, nullptr);
        if (file == INVALID_HANDLE_VALUE)
            return fail(result(Mw2erPrjStatus::IoError, "open", "open failed", 0, 0, GetLastError()));
        handle_ = file;
    } catch (const std::bad_alloc &) {
        return fail(result(Mw2erPrjStatus::IoError, "path", "path allocation failed"));
    }
    LARGE_INTEGER length;
    if (!GetFileSizeEx(static_cast<HANDLE>(handle_), &length))
        return fail(result(Mw2erPrjStatus::IoError, "size", "file size failed", 0, 0, GetLastError()));
    if (length.QuadPart < 0 || uint64_t(length.QuadPart) > MAX_ARCHIVE_BYTES)
        return fail(result(Mw2erPrjStatus::Unsupported, "size", "archive exceeds supported size budget"));
    file_size_ = static_cast<uint64_t>(length.QuadPart);
    try {
#if !defined(MW2ER_PRJ_TESTING) || defined(MW2ER_RENDERER_EXPORTS)
        synthetic = false;
#endif
        if (!synthetic) {
            if (file_size_ != PROFILE_SIZE)
                return fail(result(Mw2erPrjStatus::Unsupported, "qualification", "unqualified archive size"));
            Sha256 hash;
            NTSTATUS status = BCryptOpenAlgorithmProvider(&hash.algorithm, BCRYPT_SHA256_ALGORITHM, nullptr, 0);
            if (status >= 0)
                status = BCryptCreateHash(hash.algorithm, &hash.hash, nullptr, 0, nullptr, 0, 0);
            if (status < 0)
                return fail(result(Mw2erPrjStatus::IoError, "qualification", "SHA256 initialization failed", 0, 0, uint32_t(status)));
            std::vector<uint8_t> buffer(64 * 1024);
            for (uint64_t offset = 0; offset < file_size_;) {
                const size_t count = static_cast<size_t>(std::min(uint64_t(buffer.size()), file_size_ - offset));
                Mw2erPrjResult read = read_range(offset, count, buffer.data(), "qualification");
                if (read.status != Mw2erPrjStatus::Found) return fail(read);
                status = BCryptHashData(hash.hash, buffer.data(), static_cast<ULONG>(count), 0);
                if (status < 0)
                    return fail(result(Mw2erPrjStatus::IoError, "qualification", "SHA256 update failed", 0, 0, uint32_t(status)));
                offset += count;
            }
            uint8_t digest[32];
            status = BCryptFinishHash(hash.hash, digest, sizeof(digest), 0);
            if (status < 0)
                return fail(result(Mw2erPrjStatus::IoError, "qualification", "SHA256 finalization failed", 0, 0, uint32_t(status)));
            if (std::memcmp(digest, PROFILE_SHA256, sizeof(digest)) != 0)
                return fail(result(Mw2erPrjStatus::Unsupported, "qualification", "unqualified archive SHA256"));
        }
        Mw2erPrjResult parsed = parse_index();
        if (parsed.status != Mw2erPrjStatus::Found) return fail(parsed);
        state_ = parsed;
        return state_;
    } catch (const std::bad_alloc &) {
        return fail(result(Mw2erPrjStatus::IoError, "allocation", "archive index allocation failed"));
    }
}

Mw2erPrjResult Mw2erPrjArchive::parse_index()
{
    uint8_t header[26];
    Mw2erPrjResult read = read_range(0, sizeof(header), header, "PROJ");
    if (read.status != Mw2erPrjStatus::Found) return read;
    if (std::memcmp(header, "PROJ", 4) || std::memcmp(header + 12, "DDIT", 4) ||
        uint64_t(le32(header + 4)) + 8 != file_size_)
        return result(Mw2erPrjStatus::Corrupt, "PROJ", "invalid header or declared file length");
    const uint32_t count = le16(header + 24);
    if (!count || count > MAX_DIRECTORY_COUNT)
        return result(Mw2erPrjStatus::Corrupt, "DDIT", "invalid directory count or budget");
    const size_t directory_size = size_t(count) * 24;
    if (!bounded(sizeof(header), directory_size, file_size_))
        return result(Mw2erPrjStatus::Corrupt, "DDIT", "directory exceeds archive");
    std::vector<uint8_t> entries(directory_size);
    read = read_range(sizeof(header), entries.size(), entries.data(), "DDIT");
    if (read.status != Mw2erPrjStatus::Found) return read;
    std::vector<uint32_t> tags;
    size_t index_budget = MAX_INDEX_BYTES;
    for (uint32_t directory = 0; directory < count; ++directory) {
        const uint8_t *entry = entries.data() + size_t(directory) * 24;
        const uint32_t tag = le32(entry);
        const uint32_t type = resource_type(tag);
        if (std::find(tags.begin(), tags.end(), tag) != tags.end())
            return result(Mw2erPrjStatus::Corrupt, "DDIT", "duplicate type tag", type);
        tags.push_back(tag);
        const uint32_t offset = le32(entry + 4);
        const uint32_t size = le32(entry + 8);
        if (!offset || size < 22 || !bounded(offset, size, file_size_) || size > index_budget)
            return result(Mw2erPrjStatus::Corrupt, "INDX", "invalid index range or budget", type);
        index_budget -= size;
        std::vector<uint8_t> index(size);
        read = read_range(offset, size, index.data(), "INDX", type);
        if (read.status != Mw2erPrjStatus::Found) return read;
        if (std::memcmp(index.data(), "INDX", 4) || le32(index.data() + 4) != size - 8 ||
            le32(index.data() + 12) != tag)
            return result(Mw2erPrjStatus::Corrupt, "INDX", "invalid magic, length or owning type", type);
        const uint32_t capacity = le16(index.data() + 16);
        if (size_t(capacity) * 8 > size - 22)
            return result(Mw2erPrjStatus::Corrupt, "INDX", "slot capacity exceeds index", type);
        // Unimplemented directories include FREE bookkeeping spans, whose
        // entries are not resource DATA records. Do not infer their semantics.
        if (!type) continue;
        Directory decoded{type, {}};
        decoded.slots.resize(capacity);
        for (uint32_t id = 0; id < capacity; ++id) {
            const uint8_t *slot_bytes = index.data() + 22 + size_t(id) * 8;
            Slot slot{le32(slot_bytes), le32(slot_bytes + 4)};
            if ((!slot.offset) != (!slot.size) ||
                (slot.offset && (slot.size < DATA_HEADER_BYTES ||
                                 !bounded(slot.offset, slot.size, file_size_))))
                return result(Mw2erPrjStatus::Corrupt, "INDX", "invalid DATA slot range", type, id);
            decoded.slots[id] = slot;
            if (slot.offset) keys_.push_back({type, id});
        }
        directories_.push_back(std::move(decoded));
        // SYMB is optional metadata. No name-based consumer exists here, so
        // numeric lookup neither reads it nor depends on its live-name count.
    }
    return result(Mw2erPrjStatus::Found, "INDX", "qualified numeric index ready");
}

Mw2erPrjResult Mw2erPrjArchive::lookup(
    uint32_t type, uint32_t resource_id, Mw2erResourceAsset &asset,
    Mw2erPrjMeshKind *mesh_kind)
{
    std::lock_guard<std::mutex> lock(mutex_);
    asset = Mw2erResourceAsset{};
    if (mesh_kind) *mesh_kind = Mw2erPrjMeshKind::NonDrawable;
    if (state_.status != Mw2erPrjStatus::Found) {
        Mw2erPrjResult failure = state_;
        failure.type = type;
        failure.resource_id = resource_id;
        return failure;
    }
    if (!archive_tag(type))
        return result(Mw2erPrjStatus::Unsupported, "lookup", "resource type is not implemented", type, resource_id);
    const auto directory = std::find_if(directories_.begin(), directories_.end(),
        [type](const Directory &candidate) { return candidate.type == type; });
    if (directory == directories_.end() || resource_id >= directory->slots.size() ||
        !directory->slots[resource_id].offset)
        return result(Mw2erPrjStatus::NotFound, "lookup", "unoccupied numeric slot", type, resource_id);
    const Slot slot = directory->slots[resource_id];
    if (slot.size - DATA_HEADER_BYTES > MAX_RESOURCE_BYTES)
        return result(Mw2erPrjStatus::Corrupt, "DATA", "resource exceeds allocation budget", type, resource_id);
    uint8_t header[DATA_HEADER_BYTES];
    Mw2erPrjResult read = read_range(slot.offset, sizeof(header), header, "DATA", type, resource_id);
    if (read.status != Mw2erPrjStatus::Found) return read;
    if (std::memcmp(header, "DATA", 4) || le32(header + 4) != slot.size - 8 ||
        le32(header + 12) != archive_tag(type) || le16(header + 24) != resource_id)
        return result(Mw2erPrjStatus::Corrupt, "DATA", "invalid magic, length, type or resource id", type, resource_id);
    try {
        Mw2erResourceAsset loaded{};
        loaded.type = type;
        loaded.resource_id = resource_id;
        loaded.bytes.resize(slot.size - DATA_HEADER_BYTES);
        read = read_range(uint64_t(slot.offset) + DATA_HEADER_BYTES, loaded.bytes.size(),
                          loaded.bytes.data(), "body", type, resource_id);
        if (read.status != Mw2erPrjStatus::Found) return read;
        Mw2erPrjMeshKind kind = Mw2erPrjMeshKind::NonDrawable;
        Mw2erPrjResult validated = validate_body(loaded, kind);
        if (validated.status != Mw2erPrjStatus::Found) return validated;
        asset = std::move(loaded);
        if (mesh_kind) *mesh_kind = kind;
        return validated;
    } catch (const std::bad_alloc &) {
        return result(Mw2erPrjStatus::IoError, "allocation", "resource allocation failed", type, resource_id);
    }
}
