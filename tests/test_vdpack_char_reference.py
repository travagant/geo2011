"""Profile resource paths with escaped separators must resolve by folder."""
import sys
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from d3tool import vdpack


class CharReferenceTest(unittest.TestCase):
    def test_doubled_separator_reference_and_wrong_folder(self):
        scene = b'globalsettings\n{\n}\n'
        for ref in (r'resources\\characters\\empire\\arbitr\\arbitr_model.scene',
                    r'resources\characters\empire\arbitr\arbitr_model.scene'):
            files = {'resources/Profiles/arbitr.char': ('id "arbitr";\nunit_scene "' + ref + '";\n').encode(),
                     'resources/characters/empire/arbitr/arbitr_model.scene': scene}
            report = vdpack.reference_findings(files)
            self.assertFalse(report.warnings, report.warnings)
            wrong = ref.replace('empire', 'other')
            files['resources/Profiles/arbitr.char'] = ('id "arbitr";\nunit_scene "' + wrong + '";\n').encode()
            self.assertTrue(vdpack.reference_findings(files).warnings)


if __name__ == '__main__':
    unittest.main()
