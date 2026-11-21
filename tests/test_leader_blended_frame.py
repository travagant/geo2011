import unittest

from d3tool import frame


class BlendedFrameTest(unittest.TestCase):
    def test_mixed_frames_roundtrip(self):
        identity = frame.IDENTITY
        rotated = [[0., -1., 0., 2.], [1., 0., 0., -1.],
                   [0., 0., 1., 0.5], [0., 0., 0., 1.]]
        # K maps per-joint Blender -> donor. Forward skinning must blend
        # inverse(K), and its inverse is NOT the blend of K.
        k = {'a': identity, 'b': rotated}
        lanes = [(0.3, 'a'), (0.7, 'b')]
        inv_b = frame.inv4(rotated)
        forward = [[0.3 * identity[i][j] + 0.7 * inv_b[i][j]
                    for j in range(4)] for i in range(4)]
        original = [0.1, 0.5, -0.2, 1.]
        blender = frame.mvec(forward, original)
        normal_g = [0., 1., 0.]
        normal_b = frame.mvec(forward, normal_g + [0.])[:3]
        pos, normal = frame.invert_blended_frame(blender[:3], normal_b, lanes, k, {})
        for a, b in zip(pos, original):
            self.assertAlmostEqual(a, b, places=7)
        for a, b in zip(normal, normal_g):
            self.assertAlmostEqual(a, b, places=7)
        old = frame.convert_positions(blender[:3], lanes, k)
        self.assertGreater(sum((a-b)**2 for a, b in zip(old, original)), 0.001)

    def test_rejects_missing_and_singular(self):
        with self.assertRaisesRegex(ValueError, 'Missing frame'):
            frame.invert_blended_frame([0, 0, 0], [1, 0, 0], [(1., 'x')], {}, {})
        opposite = [[-1., 0., 0., 0.], [0., -1., 0., 0.],
                    [0., 0., 1., 0.], [0., 0., 0., 1.]]
        with self.assertRaisesRegex(ValueError, 'singular'):
            frame.invert_blended_frame([0, 0, 0], [1, 0, 0], [(0.5, 'a'), (0.5, 'b')],
                                       {'a': frame.IDENTITY, 'b': opposite}, {})


if __name__ == '__main__':
    unittest.main()
