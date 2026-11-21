#!/usr/bin/env python3
"""Exercise index UI and reports with bpy and disposable loose-resource files."""
import os
import sys
import tempfile
import traceback
from pathlib import Path
from types import SimpleNamespace

repo = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path[:0] = [os.path.join(repo, 'blender'), repo]
import bpy
import io_scene_d3unit as addon
from d3tool import vdpack


class Layout:
    def __init__(self):
        self.labels = []
    def label(self, **kwargs):
        self.labels.append(kwargs['text'])


def main():
    addon.register()
    bpy.ops.wm.read_factory_settings(use_empty=True)
    scene = bpy.context.scene
    assert not scene.d3unit_index_auto and not scene.d3unit_index_add_auto
    with tempfile.TemporaryDirectory() as temp:
        root = Path(temp)
        folder = root / 'resources/Characters/Empire/Inquisitor'
        folder.mkdir(parents=True)
        index = root / 'index'
        _, original = vdpack.build_index([('resources/Characters/Empire/Inquisitor/unit.g', b'old')], name='test', uuid='test')
        index.write_bytes(original)
        (folder / 'unit.g').write_bytes(b'new')
        (folder / 'unindexed.t').write_bytes(b'texture')
        scene.d3unit_index_path, scene.d3unit_index_folder = str(index), str(folder)
        layout = Layout()
        addon.index_ui.D3UNIT_OT_update_index.draw(SimpleNamespace(layout=layout), bpy.context)
        assert str(index) in ' '.join(layout.labels) and str(folder) in ' '.join(layout.labels)
        assert bpy.ops.d3unit.update_index() == {'FINISHED'}
        result = scene['d3unit_index_result']
        assert 'обновлено 1' in result and 'резервная копия:' in result
        report = bpy.data.texts[addon.index_ui.REPORT].as_string()
        assert 'вручную' in report and 'unindexed.t' in report and str(folder) in report
        backup = next(root.glob('index.backup_*'))
        assert backup.read_bytes() == original
        assert bpy.ops.d3unit.update_index() == {'FINISHED'}
        assert 'изменений не было, копия не создавалась' in scene['d3unit_index_result']
        saved = index.read_bytes()
        scene.d3unit_index_path = str(root / 'missing')
        try:
            assert bpy.ops.d3unit.update_index() == {'CANCELLED'}
        except RuntimeError:
            pass
        assert index.read_bytes() == saved
        assert 'Индекс не обновлён' in scene['d3unit_index_result']
        report = bpy.data.texts[addon.index_ui.REPORT].as_string()
        assert 'Индекс не обновлён' in report and str(root / 'missing') in report
    addon.unregister()
    print('PASS index UI: confirmation details, backup, no-op and error report', flush=True)


if __name__ == '__main__':
    try:
        main()
    except Exception:
        traceback.print_exc()
        sys.stdout.flush()
        sys.stderr.flush()
        os._exit(1)
    sys.stdout.flush()
    sys.stderr.flush()
    os._exit(0)
