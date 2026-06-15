---
name: ender-webui
description: Controlar una impresora 3D Marlin (Ender 3 y compatibles) por USB desde Mac, sin dependencias. Úsalo para arrancar la Web UI local, leer temperaturas/estado, precalentar, slicear STL, subir e imprimir gcode, pausar/cancelar, jog de ejes, y ver render de capa en vivo / energía / timeline de temperatura. Disparadores - "controlar la impresora", "imprimir este gcode/stl", "precalentar la Ender", "estado de la impresora", "slicear para imprimir", "Ender 3 por USB".
---

# ender-webui

Web UI + CLI (stdlib pura) para una impresora Marlin por USB en Mac.

## Arrancar el servidor

```bash
python3 server.py     # http://127.0.0.1:8080
```
El CLI `ender` habla con la API HTTP (no toca el serial → no choca con la web).

## Comandos CLI

```bash
./ender status                      # estado + temperaturas
./ender temps | watch               # temperaturas (una vez / en vivo)
./ender preheat pla|petg|<h> <b>    # precalentar
./ender cool                        # apagar calentadores
./ender home [xyz] | jog X 10       # mover
./ender send "G28"                  # G-code crudo
./ender slice pieza.stl [escala%] [perfil]   # STL -> gcode (PrusaSlicer)
./ender files | upload x.gcode | print x.gcode
./ender pause | resume | cancel
```

## Importante

- Serial **115200** vía `termios` (no `stty`). La placa **reinicia al abrir el puerto**.
- El **CH340 se alimenta del USB**: el puerto aparece aunque la **PSU física esté
  apagada**, pero sin fuente NO calienta ni mueve motores (target sube, temp no).
- **No reiniciar `server.py` con una impresión en curso**: resetea la placa y mata el print.
- Antes de imprimir: confirmar PSU encendida, **cama despejada** y filamento cargado
  (acciones físicas que el agente no puede ver ni hacer).
- Endpoints útiles: `/api/status`, `/api/energy`, `/api/temp/history`,
  `/api/print/view`, `/api/print/frame.png`.

## Slicer

`ender slice` usa PrusaSlicer (`/Applications/PrusaSlicer.app/...`) con los perfiles
de `profiles/`. El Z-offset/malla de los perfiles está calibrado para una impresora
concreta — recalibrar para la tuya.
