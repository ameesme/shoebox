"""Generate the dithered 1-bit shoebox logo SVGs. Needs numpy, pillow, fonttools and Inter Display Bold.

Usage: python3 assets/logo/generate.py [out_dir]
"""
import math, sys
import numpy as np
from PIL import Image, ImageDraw
from fontTools.ttLib import TTFont
from fontTools.pens.svgPathPen import SVGPathPen
from fontTools.pens.transformPen import TransformPen

OUT = sys.argv[1] if len(sys.argv) > 1 else __import__("os").path.dirname(__file__)
N = int(__import__("os").environ.get("LOGO_GRID", 56))  # dither pixels across the mark
SS = 4                       # supersample for face masks

# ---- isometric geometry -------------------------------------------------
C, S = math.cos(math.radians(30)), math.sin(math.radians(30))
L, W, H = 1.75, 1.0, 0.62    # box length (x), depth (y), height (z)
LIFT, LID_H, OV = 0.38, 0.2, 0.05

def P(x, y, z):
    return ((x - y) * C, (x + y) * S - z)

# Body faces
body_front = [P(0, W, 0), P(L, W, 0), P(L, W, H), P(0, W, H)]          # +y face
body_side = [P(L, 0, 0), P(L, W, 0), P(L, W, H), P(L, 0, H)]           # +x face
body_top = [P(0, 0, H), P(L, 0, H), P(L, W, H), P(0, W, H)]            # opening
# Lid
z0, z1 = H - LID_H + LIFT, H + LIFT
x0, x1, y0, y1 = -OV, L + OV, -OV, W + OV
lid_top = [P(x0, y0, z1), P(x1, y0, z1), P(x1, y1, z1), P(x0, y1, z1)]
lid_front = [P(x0, y1, z0), P(x1, y1, z0), P(x1, y1, z1), P(x0, y1, z1)]
lid_side = [P(x1, y0, z0), P(x1, y1, z0), P(x1, y1, z1), P(x1, y0, z1)]

allpts = sum([body_front, body_side, lid_top, lid_front, lid_side], [])
minx = min(p[0] for p in allpts); maxx = max(p[0] for p in allpts)
miny = min(p[1] for p in allpts); maxy = max(p[1] for p in allpts)
pad = 0.06
span = max(maxx - minx, maxy - miny) + 2 * pad
cx, cy = (minx + maxx) / 2, (miny + maxy) / 2 + 0.02

def to_px(pts, scale):
    return [((x - cx) / span * N * scale + N * scale / 2,
             (y - cy) / span * N * scale + N * scale / 2) for x, y in pts]

R = N * SS
yy, xx = np.mgrid[0:R, 0:R] / R  # 0..1 coords

def mask(pts):
    im = Image.new("L", (R, R), 0)
    ImageDraw.Draw(im).polygon(to_px(pts, SS), fill=255)
    return np.asarray(im) / 255.0

def down(a):
    return a.reshape(N, SS, N, SS).mean(axis=(1, 3))

# tone: 1 = white paper, 0 = ink
tone = np.ones((R, R))
alpha = np.zeros((R, R))

def paint(pts, values):
    global tone, alpha
    m = mask(pts)
    tone = tone * (1 - m) + values * m
    alpha = np.maximum(alpha, m)

# cast shadow: box footprint pushed down-right (light from upper-left)
D = 0.32
shadow_pts = [P(L, 0, 0), P(L + D, 0.1, 0), P(L + D, W + 0.05, 0),
              P(L * .55, W + 0.12, 0), P(0, W, 0), P(L, W, 0)]
sm = Image.new("L", (R, R), 0)
ImageDraw.Draw(sm).polygon(to_px(shadow_pts, SS), fill=255)
from PIL import ImageFilter
sm = np.asarray(sm.filter(ImageFilter.GaussianBlur(SS * 1.5))) / 255.0
tone = 1 - 0.45 * sm

# light comes from upper-left
paint(body_side, 0.16 + 0.10 * (1 - yy))                       # darkest
paint(body_front, 0.36 + 0.22 * (1 - xx) - 0.10 * (yy - 0.5))   # mid
paint(body_top, np.zeros_like(xx) + 0.0)                         # interior
paint(lid_side, 0.30 + 0.08 * (1 - yy))
paint(lid_front, 0.74 + 0.14 * (1 - xx))
paint(lid_top, 0.86 + 0.14 * (1 - (xx + yy) / 2))

t = down(tone)

# ---- 8x8 Bayer ordered dither ------------------------------------------
def bayer(n):
    if n == 1:
        return np.array([[0]])
    b = bayer(n // 2)
    return np.block([[4 * b, 4 * b + 2], [4 * b + 3, 4 * b + 1]])
B = (bayer(4) + 0.5) / 16
thr = np.tile(B, (N // 4 + 1, N // 4 + 1))[:N, :N]
ink = t < thr

# crisp 1px outline around each face edge (silhouette + lid seam)
def edges(pts):
    im = Image.new("1", (N, N), 0)
    ImageDraw.Draw(im).line(to_px(pts, 1) + [to_px(pts, 1)[0]], fill=1, width=1)
    return np.asarray(im).astype(bool)
outline = np.zeros((N, N), bool)
for f in (body_front, body_side, lid_front, lid_side, lid_top):
    outline |= edges(f)
ink |= outline

# ---- emit SVG rects (row runs) -----------------------------------------
def runs(mat):
    out = []
    for r in range(mat.shape[0]):
        c = 0
        while c < mat.shape[1]:
            if mat[r, c]:
                s = c
                while c < mat.shape[1] and mat[r, c]:
                    c += 1
                out.append(f"M{s} {r}h{c - s}v1h-{c - s}z")
            else:
                c += 1
    return "".join(out)

mark_path = runs(ink)
box = down(alpha) > 0.5
# visible box interior (the gap under the lifted lid) stays dark in the white variant
lid = mask(lid_top) + mask(lid_front) + mask(lid_side)
gap = down(mask(body_top) * (lid == 0)) > 0.5
mark_path_dark = runs(((~ink & box) | outline) & ~gap)

# ---- wordmark ----------------------------------------------------------
font = TTFont("/usr/share/fonts/opentype/inter/InterDisplay-Bold.otf")
gs = font.getGlyphSet(); cmap = font.getBestCmap()
upm = font["head"].unitsPerEm
hmtx = font["hmtx"]

def text_path(text, size, x, baseline, tracking=-0.02):
    pen = SVGPathPen(gs)
    sc = size / upm
    cur = x
    for ch in text:
        g = cmap[ord(ch)]
        tp = TransformPen(pen, (sc, 0, 0, -sc, cur, baseline))
        gs[g].draw(tp)
        cur += hmtx[g][0] * sc + tracking * size
    return pen.getCommands(), cur - tracking * size

def svg(fg, bg, layout):
    mark_path = mark_path_dark if fg == "#fff" else globals()["mark_path"]
    if layout == "mark":
        w = h = N
        body = f'<path fill="{fg}" d="{mark_path}"/>'
    else:
        size = N * 0.40
        word, end = text_path("shoebox", size, N + N * 0.16, N * 0.64)
        w, h = math.ceil(end + N * 0.08), N
        body = (f'<path fill="{fg}" d="{mark_path}" shape-rendering="crispEdges"/>'
                f'<path fill="{fg}" d="{word}"/>')
    bgrect = f'<rect width="{w}" height="{h}" fill="{bg}"/>' if bg else ""
    return (f'<svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 {w} {h}" '
            f'width="{round(w*448/N)}" height="{round(h*448/N)}" shape-rendering="crispEdges">'
            f'<title>shoebox</title>{bgrect}{body}</svg>\n')

for name, fg, bg, layout in [
    ("logo", "#000", None, "lockup"),
    ("logo-dark", "#fff", None, "lockup"),
    ("logo-mark", "#000", None, "mark"),
    ("logo-mark-dark", "#fff", None, "mark"),
]:
    open(f"{OUT}/{name}.svg", "w").write(svg(fg, bg, layout))
print("ok")
