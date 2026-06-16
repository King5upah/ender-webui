#!/usr/bin/env python3
"""
Ender 3 — Web UI local para Mac (sin dependencias externas).
Habla con la impresora por USB (serial) usando solo la librería estándar.
Arranca:  python3 ~/printer/server.py
Luego abre:  http://127.0.0.1:8080
"""
import os, re, glob, time, json, threading, html, collections
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from urllib.parse import urlparse, parse_qs
from printview import PrintView

try:
    import termios
except ImportError:
    termios = None

BAUD = 115200
GCODE_DIR = os.path.join(os.path.dirname(os.path.abspath(__file__)), "gcode")
os.makedirs(GCODE_DIR, exist_ok=True)

# energía (aproximada vía PWM de calentadores) — Ender 3 24V, ajustar si se mide
HOTEND_W = 40
BED_W = 150
BASE_W = 24          # placa + steppers + fans + display (solo cuenta imprimiendo)
TARIFA = 1.5         # MXN por kWh (CFE doméstico aprox.)

TEMP_RE = re.compile(r"T:\s*([\d.]+)\s*/\s*([\d.]+).*?B:\s*([\d.]+)\s*/\s*([\d.]+)")
DUTY_RE = re.compile(r"@:\s*(\d+)\s+B@:\s*(\d+)")     # PWM 0-127 de hotend y cama
POS_RE = re.compile(r"X:\s*(-?[\d.]+)\s+Y:\s*(-?[\d.]+)\s+Z:\s*(-?[\d.]+)")


def find_port():
    for pat in ("/dev/cu.usbserial*", "/dev/cu.wchusbserial*", "/dev/cu.usbmodem*"):
        hits = glob.glob(pat)
        if hits:
            return hits[0]
    return None


class Printer:
    def __init__(self):
        self.fd = None
        self.port = None
        self.lock = threading.RLock()
        self.temps = {"hotend": 0.0, "hotend_t": 0.0, "bed": 0.0, "bed_t": 0.0,
                      "hotend_pwm": 0, "bed_pwm": 0}
        self.energy = {"wh": 0.0, "w": 0.0, "t0": None}      # por impresión
        self.temp_history = collections.deque(maxlen=7200)   # (t,h,ht,b,bt) ~4h a 2s
        self.view = PrintView()                              # render de capa en vivo
        self.log = []          # ring buffer de líneas recientes
        self.connected = False
        self.printing = False
        self.paused = False
        self.cancel = False
        self.progress = {"done": 0, "total": 0, "file": None}
        self.last_err = None
        self.fan = 0          # 0-100 %
        self.speed = 100      # feedrate % (M220)
        self.flow = 100       # flow % (M221)
        self.babystep = 0.0   # acumulado Z (mm)
        self.pos = {"x": 0.0, "y": 0.0, "z": 0.0}

    # ---------- conexión serial ----------
    def connect(self):
        with self.lock:
            if self.connected:
                return True
            self.port = find_port()
            if not self.port or termios is None:
                self.last_err = "No encuentro la impresora (¿USB conectado y encendida?)."
                return False
            try:
                fd = os.open(self.port, os.O_RDWR | os.O_NOCTTY | os.O_NONBLOCK)
                a = termios.tcgetattr(fd)
                a[0] = 0
                a[1] = 0
                a[2] = termios.CS8 | termios.CREAD | termios.CLOCAL
                a[3] = 0
                a[4] = termios.B115200
                a[5] = termios.B115200
                termios.tcsetattr(fd, termios.TCSANOW, a)
                self.fd = fd
                self.connected = True
                self.last_err = None
                time.sleep(2.0)          # la placa reinicia al abrir; esperar arranque
                self._drain(1.5)         # descartar banner
                try:
                    os.write(self.fd, b"M155 S2\n")   # auto-reporta temps+PWM cada 2s (incl. durante print)
                except OSError:
                    pass
                return True
            except OSError as e:
                self.last_err = f"Error abriendo {self.port}: {e}"
                return False

    def reconnect(self):
        with self.lock:
            try:
                if self.fd is not None:
                    os.close(self.fd)
            except OSError:
                pass
            self.fd = None
            self.connected = False
            return self.connect()

    def _note(self, line):
        mp = POS_RE.search(line)
        if mp:
            self.pos = {"x": float(mp.group(1)), "y": float(mp.group(2)), "z": float(mp.group(3))}
        m = TEMP_RE.search(line)
        if m:
            self.temps.update({
                "hotend": float(m.group(1)), "hotend_t": float(m.group(2)),
                "bed": float(m.group(3)), "bed_t": float(m.group(4)),
            })
        md = DUTY_RE.search(line)
        if md:
            self.temps["hotend_pwm"] = int(md.group(1))
            self.temps["bed_pwm"] = int(md.group(2))
        if line:
            self.log.append(line)
            if len(self.log) > 400:
                self.log = self.log[-400:]

    def _drain(self, sec):
        t = time.time()
        buf = b""
        while time.time() - t < sec:
            try:
                d = os.read(self.fd, 4096)
            except OSError:
                d = b""
            if d:
                buf += d
                while b"\n" in buf:
                    line, buf = buf.split(b"\n", 1)
                    self._note(line.decode("ascii", "replace").strip())
            else:
                time.sleep(0.02)

    def send(self, cmd, timeout=8.0):
        """Envía un comando y lee hasta 'ok'. Devuelve las líneas recibidas."""
        cmd = cmd.strip()
        if not cmd:
            return []
        with self.lock:
            if not self.connected and not self.connect():
                return ["! sin conexión"]
            try:
                os.write(self.fd, (cmd + "\n").encode())
            except OSError as e:
                self.connected = False
                return [f"! error: {e}"]
            self.log.append(">> " + cmd)
            lines, buf, t = [], b"", time.time()
            while time.time() - t < timeout:
                try:
                    d = os.read(self.fd, 4096)
                except OSError:
                    d = b""
                if d:
                    buf += d
                    t = time.time()  # sigue llegando: extiende ventana
                    while b"\n" in buf:
                        line, buf = buf.split(b"\n", 1)
                        s = line.decode("ascii", "replace").strip()
                        if not s:
                            continue
                        lines.append(s)
                        self._note(s)
                        if s == "ok" or s.startswith("ok ") or s.startswith("ok"):
                            return lines
                else:
                    time.sleep(0.01)
            return lines  # timeout (p.ej. M109 calentando)

    # ---------- impresión ----------
    def start_print(self, filename):
        path = os.path.join(GCODE_DIR, filename)
        if not os.path.exists(path) or self.printing:
            return False
        threading.Thread(target=self._print_worker, args=(path, filename), daemon=True).start()
        return True

    def _print_worker(self, path, filename):
        with open(path, "r", errors="replace") as f:
            raw = f.readlines()
        lines = []
        for ln in raw:
            code = ln.split(";", 1)[0].strip()
            if code:
                lines.append(code)
        self.printing = True
        self.paused = False
        self.cancel = False
        self.progress = {"done": 0, "total": len(lines), "file": filename}
        self.energy = {"wh": 0.0, "w": 0.0, "t0": time.time()}   # contador por impresión
        self.temp_history.clear()
        self.view.build_async(path, filename)                    # pre-render de frames
        for i, code in enumerate(lines):
            if self.cancel:
                break
            while self.paused and not self.cancel:
                time.sleep(0.2)
            # M109/M190 (esperar temp) pueden tardar minutos: timeout generoso
            to = 600 if code[:4] in ("M109", "M190") else 60
            self.send(code, timeout=to)
            self.progress["done"] = i + 1
        # apagar seguro al terminar/cancelar
        self.send("M104 S0")   # hotend off
        self.send("M140 S0")   # cama off
        self.send("M107")      # ventilador off
        if self.cancel:
            self.send("G91"); self.send("G0 Z10 F600"); self.send("G90")  # subir Z
            self.send("M84")   # motores off
        self.printing = False
        self.paused = False

    def status(self):
        return {
            "connected": self.connected,
            "port": self.port,
            "temps": self.temps,
            "printing": self.printing,
            "paused": self.paused,
            "progress": self.progress,
            "log": self.log[-60:],
            "error": self.last_err,
            "fan": self.fan,
            "speed": self.speed,
            "flow": self.flow,
            "babystep": round(self.babystep, 3),
            "pos": self.pos,
            "energy": {"wh": round(self.energy["wh"], 2), "w": self.energy["w"],
                       "kwh": round(self.energy["wh"] / 1000.0, 3),
                       "costo": round(self.energy["wh"] / 1000.0 * TARIFA, 2)},
        }


P = Printer()


def poller():
    last = time.time()
    while True:
        try:
            if P.connected and not P.printing:
                P.send("M105", timeout=3)   # durante el print Marlin auto-reporta (M155)
        except Exception:
            pass
        now = time.time(); dt = min(now - last, 10); last = now
        try:
            t = P.temps
            w = (t.get("hotend_pwm", 0) / 127) * HOTEND_W \
                + (t.get("bed_pwm", 0) / 127) * BED_W \
                + (BASE_W if P.printing else 0)
            P.energy["w"] = round(w, 1)
            P.energy["wh"] += w * dt / 3600.0
            if P.connected:
                P.temp_history.append([round(now, 1), t.get("hotend", 0), t.get("hotend_t", 0),
                                       t.get("bed", 0), t.get("bed_t", 0)])
        except Exception:
            pass
        time.sleep(2)


PAGE = """<!DOCTYPE html><html lang="es"><head><meta charset="utf-8">
<meta name="viewport" content="width=device-width,initial-scale=1">
<title>Ender 3 · Control</title>
<style>
:root{--bg:#0e1116;--card:#171c24;--line:#262d38;--txt:#e6edf3;--mut:#8b97a7;--acc:#ff7a45;--ok:#3fb950;--blue:#388bfd}
*{box-sizing:border-box}body{margin:0;background:var(--bg);color:var(--txt);font:14px/1.45 -apple-system,BlinkMacSystemFont,"SF Pro",sans-serif}
header{display:flex;align-items:center;gap:12px;padding:14px 20px;border-bottom:1px solid var(--line)}
header h1{font-size:16px;margin:0;font-weight:600}
.dot{width:10px;height:10px;border-radius:50%;background:#555}.dot.on{background:var(--ok)}.dot.print{background:var(--acc);animation:p 1s infinite}
@keyframes p{50%{opacity:.3}}
.wrap{display:grid;grid-template-columns:1fr 1fr;gap:16px;padding:16px;max-width:1100px;margin:0 auto}
.card{background:var(--card);border:1px solid var(--line);border-radius:12px;padding:16px}
.card h2{font-size:12px;text-transform:uppercase;letter-spacing:.5px;color:var(--mut);margin:0 0 12px}
.temps{display:flex;gap:24px}.temp b{font-size:28px;font-weight:650}.temp span{color:var(--mut);font-size:12px}
.row{display:flex;gap:8px;flex-wrap:wrap;margin-top:8px}
button{background:#222a35;color:var(--txt);border:1px solid var(--line);border-radius:8px;padding:9px 12px;font-size:13px;cursor:pointer}
button:hover{border-color:var(--acc)}button:active{transform:translateY(1px)}
button.acc{background:var(--acc);border-color:var(--acc);color:#1a1008;font-weight:600}
button.warn{background:#b62324;border-color:#b62324;color:#fff;font-weight:600}
.jog{display:grid;grid-template-columns:repeat(3,46px);gap:6px;justify-content:center}
.jog button{padding:10px 0}
input,select{background:#0e1116;color:var(--txt);border:1px solid var(--line);border-radius:8px;padding:8px;font-size:13px}
#console{background:#0a0d11;border:1px solid var(--line);border-radius:8px;height:200px;overflow:auto;padding:10px;font:12px/1.4 "SF Mono",Menlo,monospace;white-space:pre-wrap}
#console .tx{color:var(--blue)}#console .rx{color:var(--mut)}
.full{grid-column:1/-1}
.bar{height:8px;background:#0a0d11;border-radius:6px;overflow:hidden;margin-top:8px}.bar i{display:block;height:100%;background:var(--acc);width:0}
small{color:var(--mut)}
label{display:block;color:var(--mut);font-size:12px;margin:8px 0 4px}
</style></head><body>
<header><span id="dot" class="dot"></span><h1>Ender 3 · Control</h1><small id="port"></small></header>
<div class="wrap">
  <div class="card">
    <h2>Temperaturas</h2>
    <div class="temps">
      <div class="temp">🔥 <b id="th">--</b>°<span id="tht"> /--</span><div><small>Hotend</small></div></div>
      <div class="temp">🛏️ <b id="tb">--</b>°<span id="tbt"> /--</span><div><small>Cama</small></div></div>
    </div>
    <div class="row">
      <button onclick="preheat(200,60)" class="acc">PLA 200/60</button>
      <button onclick="preheat(240,80)">PETG 240/80</button>
      <button onclick="cooldown()">Enfriar ❄️</button>
    </div>
    <div class="row">
      <label style="margin:0">Hotend</label><input id="ph_h" type="number" value="200" style="width:70px">
      <label style="margin:0">Cama</label><input id="ph_b" type="number" value="60" style="width:70px">
      <button onclick="preheat(+ph_h.value,+ph_b.value)">Aplicar</button>
    </div>
  </div>

  <div class="card">
    <h2>Movimiento</h2>
    <div class="jog">
      <span></span><button onclick="jog('Y',d())">Y+</button><span></span>
      <button onclick="jog('X',-d())">X−</button><button onclick="home('X Y')">⌂ XY</button><button onclick="jog('X',d())">X+</button>
      <span></span><button onclick="jog('Y',-d())">Y−</button><span></span>
    </div>
    <div class="row" style="justify-content:center">
      <button onclick="jog('Z',d())">Z+</button><button onclick="home('Z')">⌂ Z</button><button onclick="jog('Z',-d())">Z−</button>
    </div>
    <div class="row" style="justify-content:center">
      <label style="margin:0">paso</label>
      <select id="step"><option>0.1</option><option selected>1</option><option>10</option><option>50</option></select>
      <button onclick="home('')">⌂ Home todo</button>
      <button onclick="cmd('M84')">Soltar motores</button>
    </div>
  </div>

  <div class="card">
    <h2>Filamento 🧵</h2>
    <div class="row">
      <button onclick="extrude(10)">⬇ Extruir 10</button>
      <button onclick="extrude(50)">⬇ Extruir 50</button>
      <button onclick="extrude(-10)">⬆ Retraer 10</button>
    </div>
    <small>Requiere hotend ≥170°. Velocidad lenta (carga/purga).</small>
    <h2 style="margin-top:16px">Babystep Z 🔬</h2>
    <div class="row">
      <button onclick="baby(-0.02)">Z −0.02</button>
      <button onclick="baby(-0.05)">Z −0.05</button>
      <span style="padding:8px">offset: <b id="baby">0.000</b></span>
      <button onclick="baby(0.05)">Z +0.05</button>
      <button onclick="baby(0.02)">Z +0.02</button>
    </div>
    <small>− baja boquilla · + sube. Guarda con 💾 al terminar.</small>
  </div>

  <div class="card">
    <h2>Ajustes en vivo ⚡</h2>
    <label>Ventilador <b id="fanv">0</b>%</label>
    <input id="fan" type="range" min="0" max="100" value="0" style="width:100%" onchange="fan(this.value)">
    <label>Velocidad <b id="speedv">100</b>%</label>
    <input id="speed" type="range" min="50" max="200" value="100" style="width:100%" onchange="speed(this.value)">
    <label>Flujo <b id="flowv">100</b>%</label>
    <input id="flow" type="range" min="80" max="120" value="100" style="width:100%" onchange="flow(this.value)">
    <div class="row" style="margin-top:12px">
      <button onclick="save()">💾 Guardar (M500)</button>
      <button onclick="cmd2('/api/motors')">🧲 Soltar motores</button>
      <button onclick="cmd2('/api/reconnect')">🔌 Reconectar</button>
      <button onclick="estop()" class="warn">🛑 PARO</button>
    </div>
    <small id="posv" style="display:block;margin-top:8px"></small>
  </div>

  <div class="card full">
    <h2>Imprimir archivo</h2>
    <div class="row">
      <input id="file" type="file" accept=".gcode,.gco,.g">
      <button onclick="upload()">Subir</button>
      <select id="files" style="min-width:220px"></select>
      <button onclick="startPrint()" class="acc">▶ Imprimir</button>
      <button onclick="cmd2('/api/pause')" id="pausebtn">⏸ Pausa</button>
      <button onclick="cmd2('/api/cancel')" class="warn">⏹ Cancelar</button>
    </div>
    <div class="bar"><i id="prog"></i></div>
    <small id="progtxt"></small>
  </div>

  <div class="card">
    <h2>Render en vivo 🖨️</h2>
    <img id="layerview" style="width:100%;border-radius:8px;background:#0d1117;display:block" alt="">
    <small id="layerinfo" style="display:block;margin-top:8px;text-align:center">— sin impresión activa —</small>
  </div>

  <div class="card">
    <h2>Preview del slice 🧩</h2>
    <img id="sliceview" style="width:100%;border-radius:8px;background:#0d1117;display:block" alt="">
    <small id="sliceinfo" style="display:block;margin-top:8px;text-align:center">— sin impresión activa —</small>
  </div>

  <div class="card">
    <h2>Energía ⚡</h2>
    <div class="temps">
      <div class="temp"><b id="e_w">--</b><span> W ahora</span></div>
      <div class="temp"><b id="e_wh">--</b><span> Wh acum.</span></div>
    </div>
    <div class="row" style="margin-top:12px">
      <span style="padding:6px 0">≈ <b id="e_kwh">--</b> kWh · <b id="e_cost">--</b> aprox.</span>
    </div>
    <small>Estimado por PWM de calentadores (hotend 40W, cama 150W, base 24W). Se reinicia con cada impresión.</small>
  </div>

  <div class="card full">
    <h2>Temperatura (timeline) 📈</h2>
    <canvas id="tchart" height="220" style="width:100%;display:block"></canvas>
    <small>🔥 hotend · 🛏️ cama · líneas punteadas = objetivo</small>
  </div>

  <div class="card full">
    <h2>Consola G-code</h2>
    <div id="console"></div>
    <div class="row">
      <input id="gc" placeholder="ej: G28  ·  M114  ·  M503" style="flex:1" onkeydown="if(event.key==='Enter')sendgc()">
      <button onclick="sendgc()">Enviar</button>
    </div>
  </div>
</div>
<script>
const $=id=>document.getElementById(id);
function d(){return +$('step').value}
async function post(u,b){return fetch(u,{method:'POST',headers:{'Content-Type':'application/json'},body:JSON.stringify(b||{})})}
function cmd(c){post('/api/cmd',{cmd:c})}
function cmd2(u){post(u,{})}
function jog(ax,dist){post('/api/jog',{axis:ax,dist:dist})}
function home(ax){post('/api/home',{axes:ax})}
function preheat(h,b){post('/api/preheat',{hotend:+h,bed:+b})}
function cooldown(){post('/api/cooldown',{})}
async function extrude(mm){let r=await(await post('/api/extrude',{mm:mm,feed:120})).json();if(r&&r.ok===false)alert(r.msg)}
function baby(z){post('/api/babystep',{z:z})}
function fan(v){$('fanv').textContent=v;post('/api/fan',{percent:+v})}
function speed(v){$('speedv').textContent=v;post('/api/speed',{percent:+v})}
function flow(v){$('flowv').textContent=v;post('/api/flow',{percent:+v})}
function save(){post('/api/save',{})}
function estop(){if(confirm('¿PARO DE EMERGENCIA? Detiene todo; habrá que reconectar.'))post('/api/estop',{})}
function sendgc(){let c=$('gc').value.trim();if(c){cmd(c);$('gc').value=''}}
function startPrint(){let f=$('files').value;if(f)post('/api/print',{file:f})}
async function upload(){
  let f=$('file').files[0];if(!f)return;
  let txt=await f.text();
  await fetch('/api/upload?name='+encodeURIComponent(f.name),{method:'POST',body:txt});
  loadFiles();
}
async function loadFiles(){
  let r=await(await fetch('/api/files')).json();
  $('files').innerHTML=r.files.map(f=>`<option>${f}</option>`).join('')||'<option value="">(sin archivos)</option>';
}
let lastlog=0;
async function tick(){
  try{
    let s=await(await fetch('/api/status')).json();
    $('dot').className='dot'+(s.printing?' print':(s.connected?' on':''));
    $('port').textContent=s.connected?(s.port||''):(s.error||'desconectada');
    $('th').textContent=s.temps.hotend.toFixed(1);$('tht').textContent=' /'+s.temps.hotend_t.toFixed(0);
    $('tb').textContent=s.temps.bed.toFixed(1);$('tbt').textContent=' /'+s.temps.bed_t.toFixed(0);
    let c=$('console');let atBottom=c.scrollTop+c.clientHeight>=c.scrollHeight-30;
    c.innerHTML=s.log.map(l=>{let cls=l.startsWith('>>')?'tx':'rx';return `<span class="${cls}">${l.replace(/[<>&]/g,x=>({'<':'&lt;','>':'&gt;','&':'&amp;'}[x]))}</span>`}).join('\\n');
    if(atBottom)c.scrollTop=c.scrollHeight;
    let p=s.progress;
    if(p.total){let pct=100*p.done/p.total;$('prog').style.width=pct+'%';$('progtxt').textContent=`${p.file} — ${p.done}/${p.total} (${pct.toFixed(1)}%)`+(s.paused?' · ⏸ PAUSA':'');}
    $('pausebtn').textContent=s.paused?'▶ Reanudar':'⏸ Pausa';
    $('baby').textContent=(s.babystep||0).toFixed(3);
    $('fanv').textContent=s.fan;$('speedv').textContent=s.speed;$('flowv').textContent=s.flow;
    if(!document.activeElement||document.activeElement.type!=='range'){
      $('fan').value=s.fan;$('speed').value=s.speed;$('flow').value=s.flow;}
    if(s.pos)$('posv').textContent=`📍 X${s.pos.x} Y${s.pos.y} Z${s.pos.z}`;
    if(s.energy){$('e_w').textContent=s.energy.w;$('e_wh').textContent=s.energy.wh;
      $('e_kwh').textContent=s.energy.kwh;$('e_cost').textContent='$'+s.energy.costo;}
    if(s.printing){
      let v=await(await fetch('/api/print/view')).json();
      if(v.ready&&v.total_layers){
        $('layerview').src='/api/print/frame.png?d='+(s.progress.done||0);
        $('layerinfo').textContent=`Capa ${v.layer}/${v.total_layers} · Z ${v.z} mm`;
        if(!$('sliceview').src||$('sliceview').dataset.f!==v.file){
          $('sliceview').src='/api/print/preview.png?f='+encodeURIComponent(v.file);
          $('sliceview').dataset.f=v.file;}
        $('sliceinfo').textContent=v.file+' — '+v.total_layers+' capas';
      }else{$('layerinfo').textContent='preparando render…';$('sliceinfo').textContent='preparando preview…';}
    }else{$('layerinfo').textContent='— sin impresión activa —';$('sliceinfo').textContent='— sin impresión activa —';}
  }catch(e){$('dot').className='dot';$('port').textContent='servidor caído'}
}
async function drawChart(){
  let c=$('tchart');if(!c)return;
  let d;try{d=await(await fetch('/api/temp/history')).json();}catch(e){return;}
  let pts=d.pts||[];let W=c.clientWidth||600,H=220;c.width=W;c.height=H;
  let g=c.getContext('2d');g.fillStyle='#0d1117';g.fillRect(0,0,W,H);
  if(pts.length<2)return;
  let tmax=pts[pts.length-1][0]||1;
  let vmax=Math.max(60,...pts.map(p=>Math.max(p[1],p[2],p[3],p[4])))*1.12;
  let X=t=>42+(W-54)*t/tmax,Y=v=>H-18-(H-30)*v/vmax;
  g.strokeStyle='#262d38';g.fillStyle='#8b97a7';g.font='10px -apple-system,sans-serif';
  for(let v=0;v<=vmax;v+=50){g.beginPath();g.moveTo(42,Y(v));g.lineTo(W-10,Y(v));g.stroke();g.fillText(v+'°',6,Y(v)+3);}
  function L(idx,color,dash){g.strokeStyle=color;g.lineWidth=1.6;g.setLineDash(dash||[]);g.beginPath();
    pts.forEach((p,i)=>{let x=X(p[0]),y=Y(p[idx]);i?g.lineTo(x,y):g.moveTo(x,y);});g.stroke();g.setLineDash([]);}
  L(2,'#ff7a45',[3,3]);L(1,'#ff7a45');L(4,'#388bfd',[3,3]);L(3,'#388bfd');
}
loadFiles();setInterval(tick,1000);tick();setInterval(drawChart,3000);drawChart();
</script></body></html>"""


class H(BaseHTTPRequestHandler):
    def log_message(self, *a):
        pass

    def _send(self, code, body, ctype="application/json"):
        b = body.encode() if isinstance(body, str) else body
        self.send_response(code)
        self.send_header("Content-Type", ctype)
        self.send_header("Content-Length", str(len(b)))
        self.end_headers()
        self.wfile.write(b)

    def _body(self):
        n = int(self.headers.get("Content-Length", 0))
        return self.rfile.read(n) if n else b""

    def _json(self):
        try:
            return json.loads(self._body() or b"{}")
        except Exception:
            return {}

    def do_GET(self):
        u = urlparse(self.path)
        if u.path == "/":
            return self._send(200, PAGE, "text/html; charset=utf-8")
        if u.path == "/api/status":
            return self._send(200, json.dumps(P.status()))
        if u.path == "/api/files":
            fs = sorted(os.listdir(GCODE_DIR))
            return self._send(200, json.dumps({"files": fs}))
        if u.path == "/api/energy":
            kwh = P.energy["wh"] / 1000.0
            return self._send(200, json.dumps({
                "wh": round(P.energy["wh"], 2), "w": P.energy["w"],
                "kwh": round(kwh, 4), "costo": round(kwh * TARIFA, 2), "tarifa": TARIFA}))
        if u.path == "/api/temp/history":
            h = list(P.temp_history)
            step = max(1, len(h) // 300)
            pts = h[::step]
            t0 = pts[0][0] if pts else 0
            return self._send(200, json.dumps({
                "pts": [[round(p[0] - t0, 1), p[1], p[2], p[3], p[4]] for p in pts]}))
        if u.path == "/api/print/view":
            return self._send(200, json.dumps(P.view.info(P.progress["done"])))
        if u.path == "/api/print/frame.png":
            png = P.view.frame_for(P.progress["done"])
            if png:
                return self._send(200, png, "image/png")
            return self._send(404, "{}")
        if u.path == "/api/print/preview.png":
            png = P.view.full_frame()      # preview del slice completo
            if png:
                return self._send(200, png, "image/png")
            return self._send(404, "{}")
        return self._send(404, "{}")

    def do_POST(self):
        u = urlparse(self.path)
        q = parse_qs(u.query)
        if u.path == "/api/cmd":
            cmd = self._json().get("cmd", "")
            head = cmd.strip().upper()[:4]
            slow = {"G28": 90, "G29": 240, "M109": 600, "M190": 600,
                    "M303": 900, "M48": 300, "G34": 120, "M420": 30}
            P.send(cmd, timeout=slow.get(head.split()[0] if head else "", 8))
            return self._send(200, "{}")
        if u.path == "/api/jog":
            j = self._json(); ax = j.get("axis", "X"); dist = float(j.get("dist", 0))
            P.send("G91"); P.send(f"G0 {ax}{dist} F{3000 if ax!='Z' else 600}"); P.send("G90")
            return self._send(200, "{}")
        if u.path == "/api/home":
            ax = self._json().get("axes", "")
            P.send(("G28 " + ax).strip(), timeout=60)
            return self._send(200, "{}")
        if u.path == "/api/preheat":
            j = self._json()
            P.send(f"M104 S{int(j.get('hotend',0))}"); P.send(f"M140 S{int(j.get('bed',0))}")
            return self._send(200, "{}")
        if u.path == "/api/cooldown":
            P.send("M104 S0"); P.send("M140 S0"); P.send("M107")
            return self._send(200, "{}")
        if u.path == "/api/upload":
            name = (q.get("name", ["upload.gcode"])[0]).replace("/", "_")
            with open(os.path.join(GCODE_DIR, name), "wb") as f:
                f.write(self._body())
            return self._send(200, json.dumps({"ok": True, "name": name}))
        if u.path == "/api/print":
            ok = P.start_print(self._json().get("file", ""))
            return self._send(200, json.dumps({"ok": ok}))
        if u.path == "/api/pause":
            P.paused = not P.paused
            return self._send(200, "{}")
        if u.path == "/api/cancel":
            P.cancel = True; P.paused = False
            return self._send(200, "{}")
        if u.path == "/api/extrude":
            j = self._json(); mm = float(j.get("mm", 5)); feed = int(j.get("feed", 100))
            if P.temps["hotend"] < 170:
                return self._send(200, json.dumps({"ok": False, "msg": "Hotend frío (<170°). Precalienta primero."}))
            P.send("M83"); P.send(f"G1 E{mm} F{feed}", timeout=max(8, abs(mm)))
            return self._send(200, json.dumps({"ok": True}))
        if u.path == "/api/fan":
            pct = max(0, min(100, int(self._json().get("percent", 0))))
            P.fan = pct
            P.send("M107" if pct == 0 else f"M106 S{round(pct*255/100)}")
            return self._send(200, "{}")
        if u.path == "/api/speed":
            pct = max(10, min(300, int(self._json().get("percent", 100))))
            P.speed = pct; P.send(f"M220 S{pct}")
            return self._send(200, "{}")
        if u.path == "/api/flow":
            pct = max(50, min(150, int(self._json().get("percent", 100))))
            P.flow = pct; P.send(f"M221 S{pct}")
            return self._send(200, "{}")
        if u.path == "/api/babystep":
            z = float(self._json().get("z", 0))
            P.babystep += z; P.send(f"M290 Z{z}")
            return self._send(200, "{}")
        if u.path == "/api/save":
            P.send("M500")
            return self._send(200, "{}")
        if u.path == "/api/estop":
            P.send("M112", timeout=2); P.connected = False
            return self._send(200, "{}")
        if u.path == "/api/motors":
            P.send("M84")
            return self._send(200, "{}")
        if u.path == "/api/position":
            P.send("M114", timeout=4)
            return self._send(200, json.dumps(P.pos))
        if u.path == "/api/reconnect":
            ok = P.reconnect()
            return self._send(200, json.dumps({"ok": ok, "port": P.port}))
        return self._send(404, "{}")


if __name__ == "__main__":
    P.connect()
    threading.Thread(target=poller, daemon=True).start()
    srv = ThreadingHTTPServer(("127.0.0.1", 8080), H)
    print("\n  Ender 3 Web UI  ->  http://127.0.0.1:8080")
    print("  Puerto serial:", P.port or "NO DETECTADO", "| conectada:", P.connected)
    print("  Ctrl+C para salir.\n")
    try:
        srv.serve_forever()
    except KeyboardInterrupt:
        pass
