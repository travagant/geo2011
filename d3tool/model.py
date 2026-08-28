"""Neutral data model shared between the dis3tool glTF side and the original
Disciples 3 (GM) side.

The GM engine file format (`.g`) and the glTF exported by *dis3tool* describe the
same skinned mesh.  The geometry is a flat list of vertices with:

* position (3 x float32)
* normal   (3 x float32)
* diffuse  (u32 RGBA colour, the exporter almost always writes 0xFFFFFFFF)
* uv       (2 x float32)
* stored weights (float32) -- the first ``weights_on_vertex - 1`` weights
* bone indices  (u8)       -- ``weights_on_vertex`` influencing bones

The game engine only stores the first ``w-1`` weights; the final weight is
implicitly ``1 - sum(stored)`` so the four influence weights always sum to one.
``weights_on_vertex`` (``w``) is therefore the number of *influence slots*.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Dict, List, Optional, Tuple


# default per-vertex "diffuse" GP colour written by dis3tool (opaque white)
DEFAULT_DIFFUSE: int = 0xFFFFFFFF


@dataclass
class Vertex:
    position: Tuple[float, float, float] = (0.0, 0.0, 0.0)
    normal: Tuple[float, float, float] = (0.0, 0.0, 1.0)
    uv: Tuple[float, float] = (0.0, 0.0)
    diffuse: int = DEFAULT_DIFFUSE
    # first (weights_on_vertex - 1) stored weights; the last is implied
    stored_weights: Tuple[float, ...] = (1.0,)
    # influence bone indices (length == weights_on_vertex)
    bones: Tuple[int, ...] = (0, 0)

    def influence_weights(self) -> Tuple[float, ...]:
        """Weights for the ``len(bones)`` influence slots (last implied)."""
        n = len(self.bones)
        # A vertex with no stored influence slots is bound rigidly to the
        # single bone (``weights_on_vertex == 1``): it must read as weight 1.0.
        if n == 0:
            return (1.0,)
        w = list(self.stored_weights[: n - 1])
        while len(w) < n - 1:
            w.append(0.0)
        w.append(max(0.0, 1.0 - sum(w)))
        return tuple(w[:n])

    @property
    def gltf_weights(self) -> Tuple[float, float, float, float]:
        w = list(self.influence_weights())
        while len(w) < 4:
            w.append(0.0)
        return (w[0], w[1], w[2], w[3])

    @property
    def gltf_joints(self) -> Tuple[int, int, int, int]:
        # A vertex with no influence slots is rigid → joint 0 in the first slot.
        if not self.bones:
            return (0, 0, 0, 0)
        b = list(self.bones)
        while len(b) < 4:
            b.append(0)
        return (b[0], b[1], b[2], b[3])


@dataclass
class Bone:
    name: str
    # 4x4 row-major local (bind) transform relative to the parent bone
    matrix: Tuple[float, ...] = field(default_factory=lambda: (
        1.0, 0.0, 0.0, 0.0,
        0.0, 1.0, 0.0, 0.0,
        0.0, 0.0, 1.0, 0.0,
        0.0, 0.0, 0.0, 1.0,
    ))


@dataclass
class SkinnedMesh:
    name: str = ""
    geometry_file: str = ""
    vertex_count: int = 0
    tri_count: int = 0
    vertices: List[Vertex] = field(default_factory=list)
    indices: List[int] = field(default_factory=list)
    bones: List[Bone] = field(default_factory=list)
    material_diffuse: str = ""
    # 4-byte per-vertex prefix written by dis3tool (asset specific)
    vertex_magic: bytes = bytes.fromhex("5ce6ac0b")
    # number of influence slots per vertex (2, 3 or 4)
    weights_on_vertex: int = 2
    # raw bytes of the prelude + scene-node block (asset specific)
    preamble: bytes = b""
    # raw 120-byte binary header (asset specific)
    header: bytes = b""
    # logical armature / node name (from the `name` attribute), may differ
    # from the leading `name1` string of the binary for LOD files
    unit_name: str = ""
    # any trailing bytes after the bone descriptor array (morph / shadow etc.)
    trailing: bytes = b""
    # raw frame marker: when set, write_geometry_file emits these bytes
    # verbatim instead of re-serialising (used for compound / non-character
    # .g files whose layout is not a single character mesh)
    raw: bytes = b""


@dataclass
class GltfModel:
    """glTF side of the asset (reconstructed from the dis3tool export)."""

    mesh_name: str = ""
    geometry_file: str = ""
    vertex_count: int = 0
    tri_count: int = 0
    vertices: List[Vertex] = field(default_factory=list)
    indices: List[int] = field(default_factory=list)
    nodes: List[dict] = field(default_factory=list)
    bones: List[Bone] = field(default_factory=list)
    bone_indices: Dict[str, int] = field(default_factory=dict)
    frames: List[float] = field(default_factory=list)
    animation: Optional[dict] = None
    # node index -> {"rotation": [quat], "translation": [vec3]} per frame
    anim_channels: Dict[int, dict] = field(default_factory=dict)
    # number of influence slots carried by the source glTF (2/3/4); used for
    # fidelity when reverse-exporting (0 == auto-detect).
    weights_on_vertex: int = 0
