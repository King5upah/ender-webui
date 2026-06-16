#!/usr/bin/env python3
"""Render incremental de la impresión en curso: pre-genera frames isométricos
del gcode (uno cada K capas) y los sirve según la línea/capa actual.
Solo Pillow (matplotlib NO sirve: pyexpat roto en Py3.14).
"""
import re, math, threading
from io import BytesIO
from PIL import Image, ImageDraw

BG = (13, 17, 23)
SIZE = 460
PAD = 36
MAX_FRAMES = 160

_MOVE = re.compile(r"([XYZE])(-?[\d.]+)")


def _viridis(t):
    stops = [(0.0, (68, 1, 84)), (0.25, (59, 82, 139)), (0.5, (33, 145, 140)),
             (0.75, (94, 201, 98)), (1.0, (253, 231, 37))]
    for i in range(len(stops) - 1):
        a, ca = stops[i]; b, cb = stops[i + 1]
        if t <= b:
            f = (t - a) / (b - a + 1e-9)
            return tuple(int(ca[j] + (cb[j] - ca[j]) * f) for j in range(3))
    return stops[-1][1]


class PrintView:
    """Indexa un gcode y pre-renderiza frames acumulativos en memoria."""
    def __init__(self):
        self.ready = False
        self.frames = {}            # layer_idx -> PNG bytes
        self.frame_keys = []        # layers con frame, ordenados
        self.layer_start = []       # code_idx donde empieza cada capa
        self.layer_z = []           # z de cada capa
        self.total_layers = 0
        self.file = None

    def build_async(self, path, filename):
        self.__init__()
        self.file = filename
        threading.Thread(target=self._build, args=(path,), daemon=True).start()

    # ---- parseo: segmentos de extrusión agrupados por capa ----
    def _parse(self, path):
        x = y = z = 0.0
        absolute = True
        code_idx = -1
        layers = []            # cada capa: lista de (x0,y0,x1,y1,z)
        cur, cur_z = [], 0.0
        starts, zs = [], []
        for raw in open(path, errors="replace"):
            code = raw.split(";", 1)[0].strip()
            if not code:
                continue
            code_idx += 1
            head = code[:3]
            if head == "G90": absolute = True; continue
            if head == "G91": absolute = False; continue
            if head in ("G0 ", "G1 ") or code[:2] in ("G0", "G1"):
                p = dict(_MOVE.findall(code))
                nx = float(p["X"]) if "X" in p else x
                ny = float(p["Y"]) if "Y" in p else y
                nz = float(p["Z"]) if "Z" in p else z
                extr = ("E" in p) and (float(p["E"]) > 0)
                if nz != z and ("Z" in p):     # cambio de capa
                    if cur:
                        layers.append(cur); starts.append(cur_start); zs.append(cur_z)
                    cur, cur_z, cur_start = [], nz, code_idx
                if extr and (nx != x or ny != y):
                    if not cur:
                        cur_start = code_idx; cur_z = nz
                    cur.append((x, y, nx, ny, nz))
                x, y, z = nx, ny, nz
        if cur:
            layers.append(cur); starts.append(cur_start); zs.append(cur_z)
        return layers, starts, zs

    def _build(self, path):
        try:
            layers, starts, zs = self._parse(path)
            if not layers:
                self.ready = True; return
            self.layer_start, self.layer_z, self.total_layers = starts, zs, len(layers)
            # límites globales (proyección iso) a partir de todos los segmentos
            allx = [c for L in layers for s in L for c in (s[0], s[2])]
            ally = [c for L in layers for s in L for c in (s[1], s[3])]
            cx, cy = (min(allx) + max(allx)) / 2, (min(ally) + max(ally)) / 2
            zmin, zmax = min(zs), max(zs)

            def proj(x, y, zz):
                ix = (x - cx) * math.cos(math.radians(30)) - (y - cy) * math.cos(math.radians(30))
                iy = (x - cx) * math.sin(math.radians(30)) + (y - cy) * math.sin(math.radians(30)) - (zz - zmin)
                return ix, iy
            corners = []
            for x in (min(allx), max(allx)):
                for y in (min(ally), max(ally)):
                    for zz in (zmin, zmax):
                        corners.append(proj(x, y, zz))
            pxs = [c[0] for c in corners]; pys = [c[1] for c in corners]
            spanx, spany = max(pxs) - min(pxs) + 1, max(pys) - min(pys) + 1
            scale = min((SIZE - 2 * PAD) / spanx, (SIZE - 2 * PAD) / spany)
            ox = (SIZE - spanx * scale) / 2 - min(pxs) * scale
            oy = (SIZE - spany * scale) / 2 - min(pys) * scale

            def sc(pt):
                return (pt[0] * scale + ox, pt[1] * scale + oy)

            base = Image.new("RGB", (SIZE, SIZE), BG)
            draw = ImageDraw.Draw(base)
            every = max(1, self.total_layers // MAX_FRAMES)
            for li, L in enumerate(layers):
                t = (zs[li] - zmin) / (zmax - zmin + 1e-9)
                col = _viridis(t)
                for (x0, y0, x1, y1, zz) in L:
                    draw.line([sc(proj(x0, y0, zz)), sc(proj(x1, y1, zz))], fill=col, width=1)
                if li % every == 0 or li == self.total_layers - 1:
                    buf = BytesIO(); base.save(buf, "PNG")
                    self.frames[li] = buf.getvalue()
                    self.frame_keys.append(li)
            self.ready = True
        except Exception as e:
            self.error = str(e)
            self.ready = True

    # ---- consulta en vivo ----
    def current_layer(self, done):
        lo, hi, ans = 0, len(self.layer_start) - 1, 0
        while lo <= hi:
            mid = (lo + hi) // 2
            if self.layer_start[mid] <= done:
                ans = mid; lo = mid + 1
            else:
                hi = mid - 1
        return ans

    def frame_for(self, done):
        """PNG bytes del frame <= capa actual."""
        if not self.frames:
            return None
        li = self.current_layer(done)
        key = 0
        for k in self.frame_keys:
            if k <= li:
                key = k
            else:
                break
        return self.frames.get(key)

    def full_frame(self):
        """PNG bytes del modelo COMPLETO (preview del slice entero)."""
        if not self.frame_keys:
            return None
        return self.frames.get(self.frame_keys[-1])

    def info(self, done):
        li = self.current_layer(done) if self.layer_start else 0
        z = self.layer_z[li] if self.layer_z else 0.0
        return {"layer": li + 1, "total_layers": self.total_layers,
                "z": round(z, 2), "ready": self.ready, "file": self.file}
