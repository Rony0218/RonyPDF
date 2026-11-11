# Plantilla de herramienta 2.0: guía de migración

Esta guía es para pasar cada pantalla de herramienta al diseño 2.0. El ejemplo de referencia ya migrado es
`CompressPage` en `ronypdf/tools.py`: cópiale la forma. Las reglas vienen del sistema de diseño
(`README.md` del sistema, componente `ToolPage`).

Todo el texto va en español de Colombia, con trato de tú. Sin emoji, sin nombres de organizaciones.

## 1. La estructura

```
┌ encabezado ─ azulejo lg · título (title-1) · una línea ──────────── [ficha del archivo o botón] ┐
├ panel de ajustes (380 px; 340 px si la ventana mide menos de 1100) ┬ área principal (pozo gris) ─┤
│  ① ¿Pregunta o verbo?                                              │  vista previa, lista de     │
│     una línea de ayuda                                             │  archivos o zona para       │
│     controles                                                      │  soltar                     │
│  ② …                                                               │                             │
├ barra de acción ─ [chips] frase de resumen ────────── [secundario] [PRINCIPAL lg] ───────────────┤
```

El color de la categoría (números de paso, interruptores encendidos, fichas y tarjetas elegidas, casillas,
chips `cat`) lo pone la plantilla sola, según la sección de `gui.SECTIONS` donde está la herramienta.

## 2. La API

El contrato de siempre no cambia: `key`, `title`, `subtitle`, `icon_name`, `action_text`, `build()`,
`ready()`, `run()`, `accept_files(rutas)`, `refresh()`, `self.action` (el botón principal) y
`self.footer_info` (un `QLabel`).

| Llamada (dentro de `build()`) | Qué hace |
|---|---|
| `lay = self.step("¿Qué quieres censurar?", help="Una línea opcional")` | Agrega un paso numerado al panel y devuelve el `QVBoxLayout` de sus controles. Número automático; los pasos ocultos no cuentan. `lay.parentWidget()` es el `Step`: ocultarlo oculta el paso. |
| `self.set_main(widget)` | Pone el widget en el área principal y activa las dos columnas. Sin esta llamada la página queda en una columna (modo heredado). |
| `self.set_header_widget(widget)` | Lo que va a la derecha del encabezado: la ficha del archivo o un botón («Agregar archivos»). |
| `self.use_picker(picker, empty_title=None, empty_hint=None)` | Selector de un PDF en forma compacta: la ficha del archivo va al encabezado y, mientras no hay archivo, una zona grande para soltarlo ocupa el área principal. Con archivo, se ve lo que pasaste a `set_main`. |
| `b = self.add_secondary("Vista previa", funcion)` | Botón `secondary` en la barra de acción, a la izquierda del principal. |
| `self.set_summary(["4 archivos", ("77 % menos", "cat")], "El original no cambia.")` | Chips del resumen (texto, o `(texto, tono)` con `ok`, `warn`, `danger`, `info`, `cat`) y la frase. `text=None` deja la frase. |
| `self.steps` | El `StepList`; `self.steps.steps[i]` es cada `Step` (`set_done`, `add_head_widget`, `set_help`). |
| `self.group(título)` | Compatibilidad: con título es `step(título)`; sin título, una tarjeta suelta. No lo uses en código nuevo. |
| `self.body` | Compatibilidad: sigue aceptando `addWidget`/`addLayout`. En dos columnas, lo que pongas ahí queda en el panel, debajo de los pasos. No lo uses en código nuevo. |

Atributos útiles: `self.cat` (categoría), `self.header`, `self.panel`, `self.main_frame`, `self.actionbar`.

Modo heredado (sin `set_main`): una columna centrada de hasta 860 px con el encabezado dentro, márgenes de
24 px y controles a 13 px (propiedad `denso` en `style.py`), para que las pantallas densas de antes quepan a
1000 px. Al migrar, la página pasa a dos columnas y a los tamaños del sistema (14 px). En dos columnas,
`self.findChild(QScrollArea)` devuelve el desplazamiento del panel de ajustes, no el de la página.

Con la ventana de menos de 1100 px el menú lateral completo se contrae solo al carril (y vuelve al
agrandarla): diseña cada pantalla para 1000 − 64 = 936 px de ancho.

### Componentes (`from .widgets import …`)

| Componente | Para qué | Notas |
|---|---|---|
| `SwitchRow(texto, help=None, checked=False)` | Sí o no que se aplica al resultado | `.isChecked()`, `.setChecked()`, señal `toggled(bool)`, `.switch` |
| `Switch(checked=False)` | El interruptor solo | `QAbstractButton` checkable |
| `Segmented(opciones, current=0, full=False)` | 2 a 4 opciones excluyentes y cortas | opciones: textos o `(texto, valor[, glifo])`; `.index()`, `.value()`, `.set_value()`, señal `changed(índice, valor)`; `full=True` ocupa todo el ancho del panel |
| `Choices()` + `.add(texto, checked, value, count)` | Elegir varias de una lista corta y visible | fichas rectangulares (radio 8) con palomita; `.values()`, `.set_values()`, señal `changed()` |
| `ChoiceChip(texto, checked, count)` | Una ficha suelta | |
| `OptionGroup()` + `.add(título, help, value)` | Opciones excluyentes que necesitan una línea de explicación | `.value()`, `.set_value()`, señal `changed(valor)`; la primera queda elegida |
| `Note(texto, tone="info")` | Aviso corto (`info`, `ok`, `warn`, `danger`) | `.setText()`, `.set_tone()` |
| `Chip(texto, tone)` | Estado de una o pocas palabras | tonos `ok`, `warn`, `danger`, `info`, `neutral`, `cat`; con `glyph=` (ícono de icons.py) o `dot=True` |
| `FileChip(nombre, meta)` | El archivo como ficha, con «Cambiar» | señal `change_clicked`; casi nunca hace falta crearla: `picker.file_chip()` |
| `GlyphTile(clave, tamaño, cat=None, soft=False)` | Azulejo de categoría | tamaños `tema.TILE_XS` 22, `TILE_SM` 28, `TILE_MD` 40, `TILE_LG` 52, `TILE_XL` 64 |
| `DropArea(título, ayuda, exts, multi, tile_key=, zone=True, button_text=)` | Zona grande para soltar en el área principal | señales `files_dropped(list)`, `clicked` |
| `FileList(...).set_cards(meta_fn, chip_fn=None)` | Lista de archivos como tarjetas (`rp-file`) | columna 0 = nombre; `meta_fn(item)` = línea de datos; `chip_fn(item)` = chip a la derecha; «×» al pasar el puntero quita la fila; las demás columnas siguen guardando datos |
| `PreviewPane(render)` | Vista previa de una página | ya tiene el pozo gris |
| `Count(n, quiet=False)`, `Kbd("Ctrl K")`, `PdfThumb()` | Contador, tecla, miniatura con distintivo PDF | |

Campos de texto, listas desplegables, casillas y botones de opción nativos ya salen con el estilo nuevo
desde `style.py`; no les pongas `setStyleSheet` con colores.

Colores y letras para lo que pintes a mano: `tema.color("ink-2")`, `tema.tonos(self.cat)["band"]`,
`tema.fuente("title-3")`. Nunca escribas un color hexadecimal en un módulo de herramienta.

### Selector de un PDF (forma compacta)

```python
def build(self):
    self.picker = SingleFilePicker()
    self.picker.changed.connect(self._archivo)
    self.use_picker(self.picker, "Arrastra aquí el PDF que quieres proteger")
    self.preview = PreviewPane(self._render)
    self.set_main(self.preview)
    ...
```

- `self.picker.path`, `.info`, `.password`, `.set_path()`, `.clear()` y la señal `changed` siguen igual.
- `accept_files(rutas)` sigue llamando a `self.picker.set_path(rutas[0])`.
- Las pruebas que usan `picker.set_path()` no cambian.

## 3. Antes y después: `CompressPage`

Antes (dos tarjetas blancas apiladas, botones de opción y casilla):

```python
def build(self):
    g = self.group()
    self.list = FileList([...], core.PDF_EXTS, self._row, ...)
    g.addLayout(file_list_buttons(self.list, self.add))
    g.addWidget(self.list)
    lv = self.group("Nivel de compresión")
    ...QRadioButton("Recomendada\nBuena calidad…") en una cuadrícula...
    self.gray = QCheckBox("Convertir todo a escala de grises (reduce aún más el tamaño)")
```

Después (pasos a la izquierda, la lista a la derecha, resumen abajo):

```python
def build(self):
    g = self.step("¿Para qué lo necesitas?")           # tarjetas de opción: cada una se explica
    self.levels = OptionGroup()
    for key, name, desc in LEVELS:
        self.levels.add(name, desc, key)
    g.addWidget(self.levels)

    o = self.step("Ajustes")                            # sí o no → interruptor
    self.gray = SwitchRow("Pasar a escala de grises", "Baja aún más el peso. Útil para escaneos de texto.")
    o.addWidget(self.gray)

    self.list = FileList([...], core.PDF_EXTS, self._row, allow_duplicates=False)
    self.list.set_cards(self._meta, lambda it: it.text(3) or None)
    self.zone = DropArea("Arrastra aquí los PDF que quieres comprimir", "…", core.PDF_EXTS, multi=True,
                         tile_key="compress", zone=True, button_text="Elegir archivos…")
    self.main_stack = QStackedWidget()                  # vacía: zona para soltar; con archivos: la lista
    self.main_stack.addWidget(self.zone)
    self.main_stack.addWidget(lista_con_su_barra)
    self.set_main(self.main_stack)
    self.set_header_widget(self.b_add)                  # «Agregar archivos», visible solo con lista

def refresh(self):
    super().refresh()
    ...
    self.set_summary([f"{n} archivos", core.human_size(total)],
                     "Cada copia se guarda junto a su original, con «_comprimido» en el nombre.")
```

En `run()` solo cambia cómo se leen los controles (`self.levels.value()`, `self.gray.isChecked()`).

## 4. Reglas

1. **De 2 a 4 pasos.** Título en forma de pregunta o verbo («¿Qué quieres censurar?», «Elige el destino»).
   No numeres a mano: «1. Archivos» pierde el número solo.
2. **Una línea de ayuda por paso**, como máximo (`help=`). Lo demás va en una `Note` o en la ayuda emergente.
3. **Etiqueta encima del control**, nunca a la izquierda; campos a todo el ancho del panel. Usa
   `label("Páginas", "Label")` y debajo el campo. Ejemplos en el marcador de posición («Vacío = todas»).
   Nada de `QFormLayout` con etiquetas a la izquierda.
4. **Control según el caso:**
   - sí o no → `SwitchRow` (no `QCheckBox`);
   - 2 a 4 opciones cortas excluyentes → `Segmented`;
   - varias de una lista corta → `Choices` (fichas);
   - opciones que necesitan explicación → `OptionGroup`;
   - opción secundaria dentro de un paso («Incluir subcarpetas») → `QCheckBox`, que ya tiene estilo;
   - nunca filas de `QRadioButton` sueltos.
5. **Esconde lo que no aplica.** Nada de filas de botones desactivados (p. ej. la barra de flechas de
   `file_list_buttons`): las acciones de fila aparecen al pasar el puntero (`set_cards`) y el orden se
   cambia arrastrando. Solo el botón principal se desactiva mientras falta algo, y el resumen dice qué falta.
6. **Un solo botón principal** por pantalla: `self.action`, en la barra, con verbo + resultado
   («Comprimir PDF», «Censurar y guardar»). Lo demás es `secondary` (`add_secondary`) o `Ghost`.
   No pongas `setObjectName("Primary")` en ningún otro botón; `DropArea` ya usa un secundario.
7. **Mostrar el documento.** Si la herramienta cambia un PDF, el área principal muestra la vista previa
   (antes y después, lo encontrado, la marca puesta). Si trabaja con varios archivos, la lista va ahí.
8. **El archivo vive en el encabezado** (`use_picker` / `set_header_widget`), nunca en una tarjeta propia.
9. **Totales en la barra de acción** («4 archivos», «11 páginas»), no debajo de la lista.
10. **Opciones avanzadas** (lo que casi nadie cambia): plegadas al final del último paso, detrás de un botón
    `Ghost` «Opciones avanzadas».
11. **Textos:** botones con verbo; mayúscula solo al inicio; comillas latinas para nombrar la interfaz
    («Buscar datos»); coma decimal; sin signos de exclamación salvo en un saludo; los errores dicen qué
    pasó y qué hacer.
12. **Nada de colores a mano** en las páginas (`setStyleSheet("color: #…")`): usa los nombres de objeto
    de `style.py` (`Help`, `Label`, `StepTitle`, `BodySm`, `Caption`, `Ghost`, `Secondary`) o `tema.color()`.
13. **Ventana mínima de 1000 × 660.** Todo debe caber sin desplazamiento horizontal; el panel se encoge a
    340 px solo. Prueba a 1000 y a 1240 de ancho.

## 5. Lista de comprobación de cada migración

- [ ] `build()` usa `step()` y `set_main()` (y `use_picker()` si es de un solo PDF).
- [ ] Sin `QFormLayout`, sin `QRadioButton` en filas, sin barras de botones desactivados.
- [ ] Resumen con `set_summary()` en `refresh()`; el principal nombra el resultado.
- [ ] Atributos que usan las pruebas (`page.picker`, `page.list`, `page.action`, …) conservados o
      actualizados en las pruebas solo si cambió la estructura visual a propósito.
- [ ] `python -m pytest -q` y `python scripts/comprobar_arranque.py` pasan.
- [ ] Captura a 1000 × 700 y a 1240 × 820 revisada contra la mesa de trabajo del prototipo.
