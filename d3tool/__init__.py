"""dis3tool reverse-engineering toolkit.

Study / conversion tools for the Disciples 3 `GM` geometry format (`.g`) and
the glTF export produced by the *dis3tool* plugin (``geo2011.dle``).

Every public reader/writer of the submodules is re-exported here, so both
``from d3tool import parse_anim`` and ``from d3tool.anim import parse_anim``
work.
"""

from .model import (
    DEFAULT_DIFFUSE, Bone, GltfModel, MeshPart, MorphTrack, SkinnedMesh, Vertex,
)
from .gfile import (
    parse_attributes, parse_geometry_file, vertex_stride, write_geometry_file,
)
from .gltf import (
    animation_from_gltf, detect_weights_on_vertex, load_gltf, mesh_to_skinned,
)
from .ac import (
    AnimConfig, State, default_ac, detect_anim_files, parse_ac, write_ac,
)
from .anim import (
    AnimFile, BoneAnim, build_anim, parse_anim, write_anim,
)
from .scene import (
    SceneDoc, SceneNode, count_particles, parse_scene, render_scene,
    write_scene,
)
from .alias import (
    AliasDoc, SoundRef, parse_alias, parse_alias_bytes, write_alias,
    write_alias_bytes,
)
from .gltf_out import node_hierarchy, validate_gltf, write_gltf, write_gltf_to
from .texture import (
    TextureInfo, build_dds_header, convert_file, dds_to_t, find_diffuse_texture,
    parse_dds, parse_t, t_to_dds, write_dds, write_t,
)

__version__ = "0.1.0"

__all__ = [
    "__version__",
    # model
    "DEFAULT_DIFFUSE", "Bone", "GltfModel", "MeshPart", "MorphTrack",
    "SkinnedMesh", "Vertex",
    # .g geometry
    "parse_attributes", "parse_geometry_file", "vertex_stride",
    "write_geometry_file",
    # glTF -> GM
    "animation_from_gltf", "detect_weights_on_vertex", "load_gltf",
    "mesh_to_skinned",
    # .ac animation config
    "AnimConfig", "State", "default_ac", "detect_anim_files", "parse_ac",
    "write_ac",
    # .a animation
    "AnimFile", "BoneAnim", "build_anim", "parse_anim", "write_anim",
    # .scene
    "SceneDoc", "SceneNode", "count_particles", "parse_scene",
    "render_scene", "write_scene",
    # .alias
    "AliasDoc", "SoundRef", "parse_alias", "parse_alias_bytes",
    "write_alias", "write_alias_bytes",
    # GM -> glTF
    "node_hierarchy", "validate_gltf", "write_gltf", "write_gltf_to",
    # .t / .dds textures
    "TextureInfo", "build_dds_header", "convert_file", "dds_to_t",
    "find_diffuse_texture", "parse_dds", "parse_t", "t_to_dds", "write_dds",
    "write_t",
]
