"""dis3tool reverse-engineering toolkit.

Study / conversion tools for the Disciples 3 `GM` geometry format (`.g`) and
the glTF export produced by the *dis3tool* plugin (``geo2011.dle``).
"""

__version__ = "0.1.0"

from .model import Bone, GltfModel, SkinnedMesh, Vertex
from .gfile import parse_attributes, parse_geometry_file, write_geometry_file
from .gltf import load_gltf, mesh_to_skinned
from .ac import default_ac, detect_anim_files, parse_ac, write_ac
from .gltf_out import write_gltf, write_gltf_to
from .texture import (
    TextureInfo, convert_file, dds_to_t, find_diffuse_texture, parse_dds,
    parse_t, t_to_dds, write_dds, write_t,
)

__version__ = "0.1.0"

__all__ = [
    "Bone", "GltfModel", "SkinnedMesh", "Vertex",
    "parse_attributes", "parse_geometry_file", "write_geometry_file",
    "load_gltf", "mesh_to_skinned",
    "default_ac", "detect_anim_files", "parse_ac", "write_ac",
    "write_gltf", "write_gltf_to", "__version__",
    "TextureInfo", "convert_file", "dds_to_t", "t_to_dds", "parse_dds",
    "parse_t", "find_diffuse_texture", "write_dds", "write_t",
]
