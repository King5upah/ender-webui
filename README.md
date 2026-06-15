# ender-webui

Web UI + CLI **sin dependencias** para controlar una impresora 3D Marlin (Ender 3
y compatibles) por **USB directo desde una Mac** — un reemplazo casero de OctoPrint
hecho con **solo la librería estándar de Python 3** (nada de pip).

Nació para una Ender 3 (chip CH340) cuya Raspberry con OctoPrint murió; ahora se
controla directo desde el Mac por el puerto serie.

![web ui](docs/ui.png)

## Qué incluye

- **`server.py`** — servidor web (stdlib `http.server` + `termios`) en
  `http://127.0.0.1:8080`:
  - temperaturas en vivo, jog de ejes, preheat PLA/PETG, consola G-code
  - subir / imprimir `.gcode`, pausa / cancelar, ventilador, velocidad (M220),
    flujo (M221), babystep Z (M290), guardar (M500), paro (M112)
  - **render de la capa actual en vivo** (isométrico, Pillow)
  - **gasto energético aproximado** (integra el PWM `@:`/`B@:` de los calentadores)
  - **timeline de temperatura** (hotend/cama vs objetivo)
- **`ender`** — CLI que habla con la API HTTP (no toca el serial → no choca con la web):
  `status`, `temps`, `watch`, `send`, `console`, `preheat`, `home`, `jog`, `files`,
  `upload`, `print`, `pause`/`resume`/`cancel`, `slice`, `serve`.
- **`printview.py`** — módulo del render de capa en vivo (lo usa `server.py`).
- **`render_gcode.py`** — visor isométrico de un `.gcode` a PNG (preview de slice).
- **`profiles/`** — perfiles de PrusaSlicer de ejemplo (cama de vidrio Ender 3).

## Requisitos

- **macOS** (usa `termios`; en Linux también debería correr).
- **Python 3** del sistema (solo stdlib). Para el render/energía hace falta **Pillow**
  (`pip install pillow`) — opcional; el resto funciona sin nada.
- Impresora **Marlin** por USB (probado con Ender 3 / CH340 a **115200 baud**).
- *(Opcional)* **PrusaSlicer** para `ender slice`.

## Uso rápido

```bash
python3 server.py            # arranca en http://127.0.0.1:8080
```

Desde otra terminal (o pon `ender` en tu PATH):

```bash
./ender status
./ender preheat pla
./ender slice pieza.stl            # -> gcode/pieza.gcode (perfil de vidrio)
./ender print pieza.gcode
./ender watch                      # temps + progreso en vivo
```

## Notas técnicas (lo que costó hacer funcionar)

- **Serial a 115200**: macOS `stty` no le habla bien; el método que funciona es
  **`termios`** (`os.open` + `B115200`, raw, CS8, CLOCAL). La placa **reinicia al
  abrir el puerto** (~2 s, se descarta el banner).
- El **CH340 se alimenta del USB**, así que el puerto aparece **aunque la PSU esté
  apagada** — pero los calentadores/motores no funcionan sin la fuente.
- Se habilita **`M155 S2`** al conectar para que Marlin auto-reporte temperaturas +
  PWM cada 2 s **también durante la impresión** (el poller no manda M105 mientras imprime).
- **Energía aproximada**: `P ≈ (hotend_pwm/127)·Wh + (bed_pwm/127)·Wb + base`,
  integrado en el tiempo. Ajusta `HOTEND_W`/`BED_W`/`BASE_W`/`TARIFA` en `server.py`.
- **Gráficas con Pillow / canvas**, no matplotlib (en algunos Python de Homebrew
  `pyexpat` está roto y matplotlib lo arrastra).

## Seguridad

Escucha en `127.0.0.1` (solo local). No lo expongas a internet sin auth/TLS.
Los perfiles incluyen un **Z-offset y malla calibrados para una impresora concreta**
— recalíbralos para la tuya (`M851`, `G29`).

## Como skill de Claude Code

Incluye [`SKILL.md`](SKILL.md): clónalo en `~/.claude/skills/ender-webui/` y un agente
podrá arrancar el server y controlar la impresora con el CLI.

## Licencia

MIT — ver [LICENSE](LICENSE).
