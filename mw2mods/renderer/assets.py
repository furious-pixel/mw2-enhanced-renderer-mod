import struct
import zlib
from pathlib import Path


def _load_png(path, expected_color_type, channels):
    data = Path(path).read_bytes()
    if data[:8] != b"\x89PNG\r\n\x1a\n":
        raise ValueError(f"not a PNG file: {path}")
    offset = 8
    width = height = None
    color_type = bit_depth = None
    compressed = bytearray()
    while offset < len(data):
        chunk_size = struct.unpack(">I", data[offset : offset + 4])[0]
        chunk_type = data[offset + 4 : offset + 8]
        payload = data[offset + 8 : offset + 8 + chunk_size]
        offset += 12 + chunk_size
        if chunk_type == b"IHDR":
            (
                width,
                height,
                bit_depth,
                color_type,
                compression,
                filter_method,
                interlace,
            ) = struct.unpack(">IIBBBBB", payload)
            if (
                bit_depth != 8
                or color_type != expected_color_type
                or compression != 0
                or filter_method != 0
                or interlace != 0
            ):
                raise ValueError(f"unsupported PNG format: {path}")
        elif chunk_type == b"IDAT":
            compressed.extend(payload)
        elif chunk_type == b"IEND":
            break
    if width is None or height is None:
        raise ValueError(f"missing PNG header: {path}")

    raw = zlib.decompress(bytes(compressed))
    stride = int(width) * int(channels)
    rows = []
    source = 0
    previous = bytearray(stride)
    for _row in range(int(height)):
        filter_type = raw[source]
        source += 1
        current = bytearray(raw[source : source + stride])
        source += stride
        if filter_type == 1:
            for index in range(stride):
                left = current[index - channels] if index >= channels else 0
                current[index] = (current[index] + left) & 0xFF
        elif filter_type == 2:
            for index in range(stride):
                current[index] = (current[index] + previous[index]) & 0xFF
        elif filter_type == 3:
            for index in range(stride):
                left = current[index - channels] if index >= channels else 0
                up = previous[index]
                current[index] = (current[index] + ((left + up) >> 1)) & 0xFF
        elif filter_type == 4:
            for index in range(stride):
                left = current[index - channels] if index >= channels else 0
                up = previous[index]
                up_left = previous[index - channels] if index >= channels else 0
                p = left + up - up_left
                pa = abs(p - left)
                pb = abs(p - up)
                pc = abs(p - up_left)
                predictor = left if pa <= pb and pa <= pc else up if pb <= pc else up_left
                current[index] = (current[index] + predictor) & 0xFF
        elif filter_type != 0:
            raise ValueError(f"unsupported PNG filter {filter_type}: {path}")
        rows.append(bytes(current))
        previous = current
    return int(width), int(height), b"".join(rows)


def load_rgb_png(path):
    return _load_png(path, expected_color_type=2, channels=3)


def load_indexed_alpha_png(path):
    return _load_png(path, expected_color_type=4, channels=2)
