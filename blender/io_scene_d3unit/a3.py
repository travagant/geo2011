# -*- coding: utf-8 -*-
"""Reader/writer for Disciples III animation files (.a).

    u32 clip_count        (number of logical clips; see .ac)
    u32 payload_size      (file size - 8)
    u32 track_count
    u32 frame_count
    u32 fps
    track_count * {
        u32 id            (Max bone id; not unique - keep verbatim)
        u32 parent_id
        u32 frames        (== frame_count)
        f32 scale         (0.01 - cm->m; applied to positions)
        cstr  name        (NUL terminated, no length prefix)
        cstr  parent_name (NUL terminated)
        frames * { f32 qx,qy,qz,qw,  f32 px,py,pz }   # 28 bytes, local
    }

Keyframes are parent-space (relative to parent bone), positions already
in meters (raw * scale).  Quaternions (x,y,z,w) are unit length.
"""
import struct


def read_a(path):
    d = open(path, 'rb').read()
    clips, size, ntr, nfr, fps = struct.unpack_from('<5I', d, 0)
    p = 20
    tracks = []
    for _ in range(ntr):
        tid, pid, n, sc = struct.unpack_from('<3If', d, p)
        p += 16
        e = d.index(b'\x00', p)
        name = d[p:e].decode('utf-8', 'replace')
        p = e + 1
        e = d.index(b'\x00', p)
        pname = d[p:e].decode('utf-8', 'replace')
        p = e + 1
        keys = struct.unpack_from('<%df' % (7 * n), d, p)
        p += 28 * n
        tracks.append({'id': tid, 'parent_id': pid, 'name': name,
                       'parent': pname, 'scale': sc,
                       'keys': [(keys[7 * i + 0], keys[7 * i + 1],
                                 keys[7 * i + 2], keys[7 * i + 3],
                                 keys[7 * i + 4], keys[7 * i + 5],
                                 keys[7 * i + 6]) for i in range(n)]})
    return {'clips': clips, 'tracks': ntr, 'frames': nfr, 'fps': fps,
            'track_data': tracks}


def write_a(path, a):
    out = bytearray()
    nfr = a['frames']
    # build payload first to know its size
    body = bytearray()
    for t in a['track_data']:
        body += struct.pack('<3If', t['id'], t['parent_id'], nfr, t['scale'])
        body += t['name'].encode('utf-8') + b'\x00'
        body += t['parent'].encode('utf-8') + b'\x00'
        for k in t['keys']:
            body += struct.pack('<7f', *k)
    out += struct.pack('<5I', a['clips'], len(body) + 12, a['tracks'], nfr,
                       a['fps'])
    out += body
    open(path, 'wb').write(bytes(out))
    return len(out)
