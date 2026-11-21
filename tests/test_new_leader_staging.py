"""Safety tests for experimental staging without private Blender files."""
import json
from pathlib import Path
import sys
import tempfile
import unittest
from unittest.mock import patch

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
from d3tool import new_leader_staging


class StagingSafetyTest(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.root = Path(self.tmp.name)
        self.dest = self.root / 'result'
        self.tex = (ROOT / 'Empire/Leader-Warrior/icon_leader-warrior.t').read_bytes()
        self.geo = (ROOT / 'Empire/Leader-Warrior/character_empire_leader-warrior.g').read_bytes()
        self.model_patch = patch('d3tool.new_leader_staging.new_leader_model.build_geometry', return_value=self.geo)
        self.scene_patch = patch('d3tool.new_leader_staging.new_leader_scene.build_scene', return_value=b'scene')
        self.model_patch.start()
        self.scene_patch.start()
        self.addCleanup(self.model_patch.stop)
        self.addCleanup(self.scene_patch.stop)
        self.addCleanup(self.tmp.cleanup)

    def stage(self, destination=None, atlases=None, textures=None, name='arbitr'):
        return new_leader_staging.stage_visual_assets(None, None, b'donor', name,
               {'body': 'atlas.tga'} if atlases is None else atlases,
               {'atlas.t': self.tex} if textures is None else textures,
               destination or self.dest)

    def test_staging_and_no_overwrite(self):
        report = self.stage()
        self.assertEqual(report, json.loads((self.dest / 'EXPERIMENT.json').read_text()))
        self.assertEqual((self.dest / 'resources/characters/empire/arbitr/arbitr_model.g').read_bytes(), self.geo)
        self.assertIn('not an installable mod', report['status'])
        with self.assertRaisesRegex(ValueError, 'already exists'):
            self.stage(destination=self.root / 'RESULT')
        self.assertEqual((self.dest / 'resources/characters/empire/arbitr/arbitr_model.g').read_bytes(), self.geo)

    def test_bad_inputs_never_create_output(self):
        for kwargs in ({'name': '../oops'}, {'atlases': {'body': '../oops.tga'}},
                       {'textures': {}}, {'textures': {'atlas.t': self.tex[:-10]}},
                       {'textures': {'atlas.t': self.tex, 'ATLAS.T': self.tex}}):
            with self.subTest(kwargs=kwargs), self.assertRaises(ValueError):
                self.stage(**kwargs)
            self.assertFalse(self.dest.exists())
        for tree in ('resources', 'Profiles'):
            path = self.root / tree
            path.mkdir()
            with self.assertRaisesRegex(ValueError, 'outside'):
                self.stage(destination=path / 'new')
            self.assertFalse((path / 'new').exists())

    def test_failed_write_rolls_back_only_new_directory(self):
        original_open = Path.open
        def fail(path, *args, **kwargs):
            if path.name.endswith('.scene'):
                raise OSError('simulated failure')
            return original_open(path, *args, **kwargs)
        neighbor = self.root / 'untouched.txt'
        neighbor.write_text('keep')
        with patch.object(Path, 'open', fail), self.assertRaisesRegex(OSError, 'simulated'):
            self.stage()
        self.assertFalse(self.dest.exists())
        self.assertEqual(neighbor.read_text(), 'keep')


if __name__ == '__main__':
    unittest.main()
