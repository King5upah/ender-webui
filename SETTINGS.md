# Settings

Dos bloques: **recomendados** (buen punto de partida para cualquier Ender 3 + PLA) y
**probados** (la config exacta que ya funciona en esta máquina, perfil
[`profiles/ender3_pla_vidrio.ini`](profiles/ender3_pla_vidrio.ini)).

---

## ✅ Settings probados (esta máquina — funcionan)

Ender 3 (V3) con **cama sándwich: vidrio templado + lámina magnética + aluminio**
(~9–10 mm de grosor). Ese sándwich hace que la **superficie llegue más fría que el
sensor**, por eso la cama va más caliente de lo "normal".

### Adhesión / cama
| Ajuste | Valor | Por qué |
|---|---|---|
| Cama (1ª capa y resto) | **70 °C** | compensa el grosor del sándwich de vidrio |
| Superficie | vidrio + **barra adhesiva (Diurex/glue stick)** | suelta al enfriar a ~40 °C |
| Brim | **5 mm** | piezas con base chica / clips altos no se despegan |
| Skirt | 0 (se usa línea de purga en el start-gcode) | |
| 1ª capa altura | **0.24 mm** | aplasta bien contra el vidrio |
| 1ª capa velocidad | **20 mm/s** | tiempo para pegar |

### Z-offset (clave para vidrio)
- **Firmware: `M851 Z-1.47`** guardado con **`M500`** (el `z_offset` del slicer queda en 0).
- Histórico: −1.45 soldaba en vidrio pelón; −1.40/−1.43 **no pegaban**; con
  **Diurex + 70 °C + brim a −1.47** quedó perfecto.
- Start-gcode usa **`M420 S1`** para cargar la malla guardada (`G29` previo, una vez).

### Temperaturas (PLA)
| | Valor |
|---|---|
| Hotend 1ª capa | **210 °C** |
| Hotend resto | **205 °C** |
| Ventilador | 100 % (apagado solo en la 1ª capa) |

### Velocidades / calidad
| | Valor |
|---|---|
| Altura de capa | 0.2 mm (0.12 para roscas/detalle) |
| Perímetros | 3 · perímetro externo **30 mm/s** · perímetro 50 · relleno 60 |
| Relleno | 15 % grid |
| Top/bottom | 4 capas sólidas |
| Travel | 120 mm/s |

### Extrusión / retracción (direct drive)
| | Valor |
|---|---|
| Retracción | **0.8 mm** @ 40 mm/s · lift Z 0.2 |
| E relativa | sí (`use_relative_e_distances`, `layer_gcode = G92 E0`) |

---

## 🟢 Settings recomendados (punto de partida genérico)

Si tu cama es **PEI / superficie estándar** (no el sándwich de vidrio), arranca con:

| Ajuste | Recomendado |
|---|---|
| Hotend PLA | 200–210 °C (1ª capa +5) |
| Cama PLA | **60 °C** (vidrio pelón con cola: 60–70) |
| 1ª capa altura | 0.2–0.24 mm |
| Altura de capa | 0.2 mm general · 0.12 mm detalle/roscas |
| Perímetros | 2–3 |
| Relleno | 15–20 % grid/gyroid |
| Velocidad | 50 mm/s (externo 25–30 para acabado) |
| Retracción (bowden) | 4–6 mm · (direct drive) 0.6–1 mm |
| Brim | 0–5 mm según base |
| Z-offset | calibra con `G29` + `M851`/babystep (`ender` → babystep Z en vivo) |

### PETG (rápido)
- Hotend 235–245 °C · cama 75–85 °C · ventilador 30–50 % · retracción un pelín menor.

---

## Consejos de impresión (aprendidos a golpes)

- **Posicionar para slice por el boundbox de la *malla*, no del sólido**: las roscas
  modeladas inflan `Shape.BoundBox` y la pieza queda **flotando** sobre la cama.
- **Recesses / bolsillos de acople** (tuerca cautiva, imán): dejar **+0.6–0.8 mm**
  entre-caras; un hueco "exacto" no admite la contraparte impresa (FDM sale al nominal
  o un pelín más).
- **PSU física**: el CH340 se alimenta del USB, así que el puerto conecta aunque la
  fuente esté **apagada** — síntoma: target sube pero la temperatura no.
- **No reiniciar `server.py` con una impresión en curso** (resetea la placa).
