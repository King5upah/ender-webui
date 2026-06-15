#!/usr/bin/env python3
"""Render isometrico de las trayectorias de extrusion de gcode, solo con Pillow."""
import sys, re, math
from PIL import Image, ImageDraw, ImageFont

files = sys.argv[1:-1]
out = sys.argv[-1]

BG = (13, 17, 23)
CELL = 760           # tamaño por panel
PAD = 60

def parse(path):
    x = y = z = 0.0
    absolute = True
    segs = []
    for line in open(path):
        line = line.split(";")[0].strip()
        if not line:
            continue
        if line.startswith("G90"): absolute = True; continue
        if line.startswith("G91"): absolute = False; continue
        if line.startswith(("G0", "G1")):
            p = dict(re.findall(r"([XYZEF])(-?[\d.]+)", line))
            nx = float(p["X"]) if "X" in p else x
            ny = float(p["Y"]) if "Y" in p else y
            nz = float(p["Z"]) if "Z" in p else z
            extr = ("E" in p) and (float(p["E"]) > 0)
            if extr and (nx != x or ny != y):
                segs.append((x, y, z, nx, ny, nz))
            x, y, z = nx, ny, nz
    return segs

def viridis(t):
    # aproximacion simple del colormap viridis
    stops = [(0.0,(68,1,84)),(0.25,(59,82,139)),(0.5,(33,145,140)),
             (0.75,(94,201,98)),(1.0,(253,231,37))]
    for i in range(len(stops)-1):
        a,ca=stops[i]; b,cb=stops[i+1]
        if t<=b:
            f=(t-a)/(b-a+1e-9)
            return tuple(int(ca[j]+(cb[j]-ca[j])*f) for j in range(3))
    return stops[-1][1]

def render_panel(segs, title):
    img = Image.new("RGB", (CELL, CELL), BG)
    d = ImageDraw.Draw(img)
    if not segs:
        return img
    xs=[s[0] for s in segs]+[s[3] for s in segs]
    ys=[s[1] for s in segs]+[s[4] for s in segs]
    zs=[s[2] for s in segs]+[s[5] for s in segs]
    cx=(min(xs)+max(xs))/2; cy=(min(ys)+max(ys))/2
    zmin,zmax=min(zs),max(zs)
    # proyeccion isometrica
    def proj(x,y,z):
        ix=(x-cx)*math.cos(math.radians(30)) - (y-cy)*math.cos(math.radians(30))
        iy=(x-cx)*math.sin(math.radians(30)) + (y-cy)*math.sin(math.radians(30)) - (z-zmin)
        return ix,iy
    pts=[proj(*s[:3]) for s in segs]+[proj(*s[3:]) for s in segs]
    pxs=[p[0] for p in pts]; pys=[p[1] for p in pts]
    spanx=max(pxs)-min(pxs)+1; spany=max(pys)-min(pys)+1
    scale=min((CELL-2*PAD)/spanx,(CELL-2*PAD)/spany)
    ox=(CELL-spanx*scale)/2-min(pxs)*scale
    oy=(CELL-spany*scale)/2-min(pys)*scale
    def sc(p): return (p[0]*scale+ox, p[1]*scale+oy)
    for (x0,y0,z0,x1,y1,z1) in segs:
        t=(z0-zmin)/(zmax-zmin+1e-9)
        a=sc(proj(x0,y0,z0)); b=sc(proj(x1,y1,z1))
        d.line([a,b],fill=viridis(t),width=1)
    try:
        font=ImageFont.truetype("/System/Library/Fonts/SFNS.ttf",30)
    except Exception:
        font=ImageFont.load_default()
    d.text((20,20),title,fill=(230,237,243),font=font)
    d.text((20,CELL-40),f"{zmax-zmin:.1f} mm alto",fill=(125,133,144),font=font)
    return img

panels=[(p.split("/")[-1].replace("codo_","").replace(".gcode",""), parse(p)) for p in files]
n=len(panels); cols=2 if n>1 else 1; rows=math.ceil(n/cols)
W,H=cols*CELL, rows*CELL+70
canvas=Image.new("RGB",(W,H),BG)
draw=ImageDraw.Draw(canvas)
try:
    tf=ImageFont.truetype("/System/Library/Fonts/SFNS.ttf",40)
except Exception:
    tf=ImageFont.load_default()
draw.text((30,18),"Slice preview — test mecanismo del codo",fill=(230,237,243),font=tf)
for i,(title,segs) in enumerate(panels):
    r,c=divmod(i,cols)
    canvas.paste(render_panel(segs,title),(c*CELL, 70+r*CELL))
canvas.save(out)
print("guardado",out,canvas.size)
