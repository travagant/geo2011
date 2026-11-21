"""The profile preview must not hide unresolved equipment or change donors."""
import sys
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
from d3tool import char, new_leader_profile


class PreviewTest(unittest.TestCase):
    def setUp(self):
        self.donor = (ROOT / 'Profiles/empire_leader-warrior.char').read_bytes()
        p = char.parse_char(self.donor)
        self.assertTrue(p.set_id('arbitr'))
        self.source = char.encode_char(p)

    def test_only_scene_changes_and_all_slots_reported(self):
        out, report = new_leader_profile.preview_visual_profile(self.source, 'arbitr')
        before = self.source.splitlines(keepends=True)
        after = out.splitlines(keepends=True)
        diff = [(a, b) for a, b in zip(before, after) if a != b]
        self.assertEqual(len(before), len(after))
        self.assertEqual(len(diff), 1)
        self.assertTrue(diff[0][0].lstrip().startswith(b'unit_scene '))
        self.assertEqual(report['new_scene'], r'resources\\characters\\empire\\arbitr\\arbitr_model.scene')
        self.assertEqual(len(report['equipment']), 21)
        self.assertIn('leader-warrior', report['lod'])
        self.assertTrue(all('leader-warrior' in slot['path'] for slot in report['equipment']))
        self.assertEqual((ROOT / 'Profiles/empire_leader-warrior.char').read_bytes(), self.donor)

    def test_no_sets_candidate_matches_composition_and_preserves_lod(self):
        from d3tool import leader_set_probe
        ordinary, _ = new_leader_profile.preview_visual_profile(self.source, 'arbitr')
        expected = leader_set_probe.without_donor_sets(ordinary, 'arbitr')
        out, report = new_leader_profile.preview_visual_profile_without_warrior_sets(self.source, 'arbitr')
        self.assertEqual(out, expected)
        p = char.parse_char(out)
        self.assertFalse(p.blocks)
        self.assertEqual(p.unit_scene_lod, char.parse_char(self.source).unit_scene_lod)
        self.assertEqual(len(report['removed_equipment']), 21)
        self.assertEqual(report['equipment'], [])
        self.assertIn('not an installable mod', report['status'])
        self.assertEqual((ROOT / 'Profiles/empire_leader-warrior.char').read_bytes(), self.donor)

    def test_no_sets_candidate_rejects_unknown_equipment(self):
        malformed = self.source.replace(b'    feet ', b'    unknown ', 1)
        with self.assertRaises(ValueError):
            new_leader_profile.preview_visual_profile_without_warrior_sets(malformed, 'arbitr')

    def test_reject_ambiguous_or_invalid_input(self):
        bad = [
            (self.source, '../arbitr'),
            (self.source, 'another'),
            (self.source + b'\nunit_scene "other.scene";\n', 'arbitr'),
            (self.source.replace(b'id \t\t"arbitr";', b'id "arbitr";\nid "arbitr";'), 'arbitr'),
        ]
        for data, name in bad:
            with self.subTest(name=name, data=data[-30:]), self.assertRaises(ValueError):
                new_leader_profile.preview_visual_profile(data, name)


if __name__ == '__main__':
    unittest.main()
