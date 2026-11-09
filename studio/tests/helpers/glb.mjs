// Настоящий GLB для тестов: JSON-чанк + BIN с позициями/нормалями/UV/индексами.
// Не заглушка с подписью «glTF»: файл проходит наш же readGlb и любой внешний просмотрщик.
export function buildGlb({
  positions = cubePositions(),
  indices = cubeIndices(),
  uvs = true,
  normals = true,
  nodeScale,
  rig = false,
  clips = 0,
  materials = 1,
  textures = 1,
  mode,
  generator = 'test',
} = {}) {
  const bin = [];
  const views = [];
  const accessors = [];
  let offset = 0;
  const push = (data) => {
    const buffer = Buffer.from(data.buffer, data.byteOffset, data.byteLength);
    const pad = (4 - (offset % 4)) % 4;
    if (pad) { bin.push(Buffer.alloc(pad)); offset += pad; }
    views.push({ buffer: 0, byteOffset: offset, byteLength: buffer.length });
    bin.push(buffer);
    offset += buffer.length;
    return views.length - 1;
  };

  accessors.push({ bufferView: push(Float32Array.from(positions)), componentType: 5126, count: positions.length / 3, type: 'VEC3', min: [-0.5, 0, -0.5], max: [0.5, 1, 0.5] });
  const attributes = { POSITION: 0 };

  if (normals) {
    const data = new Float32Array(positions.length);
    for (let i = 0; i < data.length; i += 3) data[i + 1] = 1;
    accessors.push({ bufferView: push(data), componentType: 5126, count: positions.length / 3, type: 'VEC3' });
    attributes.NORMAL = accessors.length - 1;
  }
  if (uvs) {
    const data = new Float32Array((positions.length / 3) * 2);
    accessors.push({ bufferView: push(data), componentType: 5126, count: positions.length / 3, type: 'VEC2' });
    attributes.TEXCOORD_0 = accessors.length - 1;
  }
  if (rig) {
    const joints = new Uint8Array((positions.length / 3) * 4);
    const weights = new Float32Array((positions.length / 3) * 4);
    for (let i = 0; i < weights.length; i += 4) weights[i] = 1;
    accessors.push({ bufferView: push(joints), componentType: 5121, count: positions.length / 3, type: 'VEC4' });
    attributes.JOINTS_0 = accessors.length - 1;
    accessors.push({ bufferView: push(weights), componentType: 5126, count: positions.length / 3, type: 'VEC4' });
    attributes.WEIGHTS_0 = accessors.length - 1;
  }
  const indexData = positions.length / 3 > 65535 ? Uint32Array.from(indices) : Uint16Array.from(indices);
  accessors.push({ bufferView: push(indexData), componentType: indexData instanceof Uint32Array ? 5125 : 5123, count: indices.length, type: 'SCALAR' });

  const doc = {
    asset: { version: '2.0', generator },
    scene: 0,
    scenes: [{ nodes: [0] }],
    nodes: [{ name: 'Knight', mesh: 0, ...(nodeScale ? { scale: nodeScale } : {}) }],
    meshes: [{ primitives: [{ attributes, indices: accessors.length - 1, ...(mode === undefined ? {} : { mode }) }] }],
    accessors,
    bufferViews: views,
    buffers: [{ byteLength: offset }],
    materials: Array.from({ length: materials }, (_, i) => ({ name: `Mat${i}` })),
    textures: Array.from({ length: textures }, () => ({ source: 0 })),
    ...(rig ? { skins: [{ joints: [0], name: 'Armature' }] } : {}),
    ...(clips ? { animations: Array.from({ length: clips }, (_, i) => ({ name: `clip${i}`, channels: [], samplers: [] })) } : {}),
  };
  let json = Buffer.from(JSON.stringify(doc), 'utf8');
  json = Buffer.concat([json, Buffer.alloc((4 - (json.length % 4)) % 4, 0x20)]);
  let binBuffer = Buffer.concat(bin);
  binBuffer = Buffer.concat([binBuffer, Buffer.alloc((4 - (binBuffer.length % 4)) % 4)]);

  const jsonHeader = Buffer.alloc(8);
  jsonHeader.writeUInt32LE(json.length, 0);
  jsonHeader.writeUInt32LE(0x4e4f534a, 4);
  const binHeader = Buffer.alloc(8);
  binHeader.writeUInt32LE(binBuffer.length, 0);
  binHeader.writeUInt32LE(0x004e4942, 4);
  const header = Buffer.alloc(12);
  header.write('glTF', 0, 'ascii');
  header.writeUInt32LE(2, 4);
  header.writeUInt32LE(12 + 8 + json.length + 8 + binBuffer.length, 8);
  return Buffer.concat([header, jsonHeader, json, binHeader, binBuffer]);
}

export function cubePositions() {
  return [
    -0.5, 0, -0.5, 0.5, 0, -0.5, 0.5, 1, -0.5, -0.5, 1, -0.5,
    -0.5, 0, 0.5, 0.5, 0, 0.5, 0.5, 1, 0.5, -0.5, 1, 0.5,
  ];
}

export function cubeIndices() {
  return [
    0, 1, 2, 0, 2, 3, 4, 6, 5, 4, 7, 6, 0, 4, 5, 0, 5, 1,
    1, 5, 6, 1, 6, 2, 2, 6, 7, 2, 7, 3, 3, 7, 4, 3, 4, 0,
  ];
}
