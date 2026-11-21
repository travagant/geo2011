import struct
import sys
import unittest
import zlib
from pathlib import Path
ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
from d3tool import leader_icon_image, leader_icon_probe, texture


def png(w, h, color=(255, 0, 0, 255)):
    def chunk(name, data):
        return struct.pack('>I', len(data)) + name + data + struct.pack('>I', zlib.crc32(name + data) & 0xffffffff)
    rows = b''.join(b'\0' + bytes(color) * w for _ in range(h))
    return (b'\x89PNG\r\n\x1a\n' + chunk(b'IHDR', struct.pack('>IIBBBBB', w, h, 8, 6, 0, 0, 0))
            + chunk(b'IDAT', zlib.compress(rows)) + chunk(b'IEND', b''))


class ImageTest(unittest.TestCase):
    def test_expected_encodings_and_pixel_bits(self):
        data = png(64, 64, (255, 0, 0, 136))
        out = texture.parse_t(leader_icon_image.png_to_icon(data, 'subicon'))
        self.assertEqual((out.width, out.height, out.gm_format, out.mip_count), (64, 64, 7, 1))
        self.assertEqual(out.payload[:8], b'\x88' * 8)
        self.assertEqual(struct.unpack('<HHI', out.payload[8:16]), (0xf800, 0xf800, 0))
        self.assertEqual(len(out.payload), out.payload_size())
        data = png(128, 128, (255, 0, 0, 127))
        ring = texture.parse_t(leader_icon_image.png_to_icon(data, 'leader_icon'))
        self.assertEqual((ring.gm_format, ring.mip_count), (3, 1))
        self.assertEqual(ring.payload[:2], b'\x00\x7c')
        ring = texture.parse_t(leader_icon_image.png_to_icon(png(128, 128, (0, 255, 0, 128)), 'leader_icon'))
        self.assertEqual(ring.payload[:2], b'\xe0\x83')

    def test_bad_data(self):
        good = png(64, 64)
        cases = [good[:-1], good + b'x', good[:40] + b'x' + good[41:], png(64, 128)]
        for item in cases:
            with self.subTest(size=len(item)), self.assertRaises(ValueError):
                leader_icon_image.png_to_icon(item, 'subicon')
        with self.assertRaises(ValueError):
            leader_icon_image.png_to_icon(good, 'bogus')

if __name__ == '__main__':
    unittest.main()
