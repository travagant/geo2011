# minimal mathutils-compatible shim for testing addon math OUTSIDE Blender
# (float64 pure python; 4x4/3x3 row-major storage, v' = M @ v)
import math


class Vector(tuple):
    def __new__(cls, seq):
        if isinstance(seq, (int, float)):
            seq = (seq,)
        return tuple.__new__(cls, (float(x) for x in seq))

    @property
    def x(self):
        return self[0]

    @property
    def y(self):
        return self[1] if len(self) > 1 else 0.0

    @property
    def z(self):
        return self[2] if len(self) > 2 else 0.0

    @property
    def length(self):
        return math.sqrt(sum(v * v for v in self))

    def normalized(self):
        l = self.length or 1.0
        return Vector(v / l for v in self)

    def normalized_safe(self):
        return self.normalized()

    def __add__(self, o):
        return Vector(a + b for a, b in zip(self, o))

    def __sub__(self, o):
        return Vector(a - b for a, b in zip(self, o))

    def __mul__(self, o):
        if isinstance(o, (int, float)):
            return Vector(v * o for v in self)
        return self.dot(o)

    def __rmul__(self, o):
        return self * o

    def dot(self, o):
        return sum(a * b for a, b in zip(self, o))

    def cross(self, o):
        return Vector((self[1] * o[2] - self[2] * o[1],
                       self[2] * o[0] - self[0] * o[2],
                       self[0] * o[1] - self[1] * o[0]))

    def angle(self, o):
        d = max(-1.0, min(1.0, self.dot(o) / (self.length * o.length)))
        return math.acos(d)


class Quaternion(tuple):
    def __new__(cls, seq):
        if isinstance(seq, Quaternion):
            return seq
        return tuple.__new__(cls, (float(x) for x in seq))

    @property
    def w(self):
        return self[0]

    @property
    def x(self):
        return self[1]

    @property
    def y(self):
        return self[2]

    @property
    def z(self):
        return self[3]

    def normalized(self):
        n = math.sqrt(sum(v * v for v in self)) or 1.0
        return Quaternion(v / n for v in self)

    def to_matrix(self):
        x, y, z, w = self[1], self[2], self[3], self[0]
        return Matrix((
            (1 - 2 * (y * y + z * z), 2 * (x * y - z * w),
             2 * (x * z + y * w)),
            (2 * (x * y + z * w), 1 - 2 * (x * x + z * z),
             2 * (y * z - x * w)),
            (2 * (x * z - y * w), 2 * (y * z + x * w),
             1 - 2 * (x * x + y * y)),
        ))


class Matrix:
    __slots__ = ('_size', 'r')

    def __new__(cls, rows=None, size=None):
        if isinstance(rows, Matrix):
            m = object.__new__(cls)
            m._size = rows._size
            m.r = [list(v) for v in rows.r]
            return m
        if isinstance(rows, int):
            n = rows
            return cls.Identity(n)
        m = object.__new__(cls)
        m._size = (len(rows), len(rows[0]))
        m.r = [list(v) for v in rows]
        return m

    @property
    def size(self):
        return self._size[0]

    @classmethod
    def Identity(cls, n):
        return cls(tuple(tuple(1.0 if i == j else 0.0
                               for j in range(n)) for i in range(n)))

    @classmethod
    def Translation(cls, v):
        m = cls.Identity(4)
        for i in range(3):
            m.r[i][3] = float(v[i])
        return m

    def __getitem__(self, i):
        return tuple(self.r[i]) if isinstance(i, int) \
            else tuple(self.r[i[0]])[i[1]]

    def __iter__(self):
        return iter(tuple(r) for r in self.r)

    def to_4x4(self):
        m = Matrix.Identity(4)
        for i in range(min(3, len(self.r))):
            for j in range(min(3, len(self.r[0]))):
                m.r[i][j] = self.r[i][j]
        return m

    def to_3x3(self):
        if self._size == (3, 3):
            return Matrix(self.r)
        return Matrix(tuple(tuple(self.r[i][j] for j in range(3))
                            for i in range(3)))

    def transposed(self):
        return Matrix(tuple(tuple(self.r[j][i] for j in range(len(self.r)))
                            for i in range(len(self.r[0]))))

    def inverted(self):
        n = self._size[0]
        a = [self.r[i][:] + [1.0 if i == j else 0.0
                             for j in range(n)] for i in range(n)]
        for c in range(n):
            p = max(range(c, n), key=lambda r: abs(a[r][c]))
            if abs(a[p][c]) < 1e-16:
                raise ValueError('not invertible')
            a[c], a[p] = a[p], a[c]
            pv = a[c][c]
            a[c] = [x / pv for x in a[c]]
            for r in range(n):
                if r != c and a[r][c]:
                    f = a[r][c]
                    a[r] = [x - f * y for x, y in zip(a[r], a[c])]
        return Matrix(tuple(tuple(a[i][n + j] for j in range(n))
                            for i in range(n)))

    @property
    def translation(self):
        return Vector((self.r[0][3], self.r[1][3], self.r[2][3]))

    @translation.setter
    def translation(self, v):
        for i in range(3):
            self.r[i][3] = v[i]

    def __matmul__(self, o):
        if isinstance(o, Vector):     # M @ v
            n = len(self.r[0])
            return Vector(sum(self.r[i][k] * o[k] for k in range(n))
                          for i in range(len(self.r)))
        r2 = o.r
        return Matrix(tuple(tuple(sum(self.r[i][k] * r2[k][j]
                                  for k in range(len(self.r[0])))
                              for j in range(len(r2[0])))
                          for i in range(len(self.r))))

    def __mul__(self, o):
        if isinstance(o, (int, float)):
            return Matrix(tuple(tuple(v * o for v in r) for r in self.r))
        return self.__matmul__(o)

    __rmul__ = __mul__

    def decompose(self):
        loc = self.translation
        rot = self.to_3x3()
        sx = Vector(rot.r[0]).length
        sy = Vector(rot.r[1]).length
        sz = Vector(rot.r[2]).length
        rs = Matrix(tuple(tuple(rot.r[i][j] / l for j in range(3))
                          for i, l in enumerate((sx, sy, sz))))
        quat = _matrix_quat(rs)
        return loc, quat, Vector((sx, sy, sz))

    def to_quaternion(self):
        return _matrix_quat(self.to_3x3())


def _matrix_quat(m):
    r00, r01, r02 = m.r[0]
    r10, r11, r12 = m.r[1]
    r20, r21, r22 = m.r[2]
    tr = r00 + r11 + r22
    if tr > 0:
        S = math.sqrt(tr + 1.0) * 2
        w, x, y, z = (0.25 * S, (r21 - r12) / S,
                      (r02 - r20) / S, (r10 - r01) / S)
    elif r00 > r11 and r00 > r22:
        S = math.sqrt(1.0 + r00 - r11 - r22) * 2
        w, x, y, z = ((r21 - r12) / S, 0.25 * S,
                      (r01 + r10) / S, (r02 + r20) / S)
    elif r11 > r22:
        S = math.sqrt(1.0 + r11 - r00 - r22) * 2
        w, x, y, z = ((r02 - r20) / S, (r01 + r10) / S,
                      0.25 * S, (r12 + r21) / S)
    else:
        S = math.sqrt(1.0 + r22 - r00 - r11) * 2
        w, x, y, z = ((r10 - r01) / S, (r02 + r20) / S,
                      (r12 + r21) / S, 0.25 * S)
    n = math.sqrt(w * w + x * x + y * y + z * z)
    w, x, y, z = w / n, x / n, y / n, z / n
    if w < 0:
        w, x, y, z = -w, -x, -y, -z
    return Quaternion((w, x, y, z))


def _install():
    import sys
    import types
    mod = types.ModuleType('mathutils')
    mod.Vector = Vector
    mod.Matrix = Matrix
    mod.Quaternion = Quaternion
    sys.modules['mathutils'] = mod
    return mod
