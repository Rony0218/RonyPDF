"""Aviso de cierre inesperado: una tarjeta abajo a la derecha de la ventana que no bloquea nada.

Aparece después de mostrar la ventana cuando ``diagnostico.cierre_anterior()`` dice que la sesión anterior no
terminó bien.  «Guardar informe…» guarda un .txt donde la persona elija (``diagnostico.guardar_informe``: datos
técnicos y los registros de las últimas sesiones, sin contenido de documentos); RonyPDF no lo envía a ningún lado.
"""

from __future__ import annotations

from pathlib import Path

from PySide6.QtCore import QEvent, Qt
from PySide6.QtWidgets import QFrame, QHBoxLayout, QLabel, QMessageBox, QPushButton, QVBoxLayout

from . import diagnostico, tema
from .componentes import label
from .icons import pixmap

TEXTO = "RonyPDF se cerró de forma inesperada la última vez."
DETALLE = ("Puedes guardar un informe técnico para entender qué pasó. No incluye el contenido de tus documentos "
           "y RonyPDF no lo envía a ningún lado.")
FILTRO = "Texto (*.txt)"


class AvisoCierre(QFrame):
    """Tarjeta flotante (``AvisoFlotante``) con «Guardar informe…» y «Ahora no»."""

    MARGEN = 24
    ANCHO = 420

    def __init__(self, ventana):
        super().__init__(ventana)
        self.ventana = ventana
        self.setObjectName("AvisoFlotante")
        tema.sombra(self, 3)
        lay = QHBoxLayout(self)
        lay.setContentsMargins(16, 14, 16, 14)
        lay.setSpacing(12)
        icono = QLabel()
        icono.setFixedSize(18, 20)
        icono.setPixmap(pixmap("info", tema.color("warn"), 18, 1.9))
        lay.addWidget(icono, 0, Qt.AlignmentFlag.AlignTop)
        col = QVBoxLayout()
        col.setContentsMargins(0, 0, 0, 0)
        col.setSpacing(4)
        self.titulo = label(TEXTO, "BodyStrong", wrap=True)
        col.addWidget(self.titulo)
        col.addWidget(label(DETALLE, "Caption", wrap=True))
        botones = QHBoxLayout()
        botones.setContentsMargins(0, 8, 0, 0)
        botones.setSpacing(8)
        botones.addStretch(1)
        self.b_no = QPushButton("Ahora no")
        self.b_no.setObjectName("Ghost")
        self.b_no.clicked.connect(self.cerrar)
        self.b_guardar = QPushButton("Guardar informe…")
        self.b_guardar.setObjectName("Secondary")
        self.b_guardar.setProperty("tam", "sm")
        self.b_guardar.clicked.connect(self.guardar)
        for b in (self.b_no, self.b_guardar):
            b.setCursor(Qt.CursorShape.PointingHandCursor)
            botones.addWidget(b)
        col.addLayout(botones)
        lay.addLayout(col, 1)
        self.setFixedWidth(self.ANCHO)
        ventana.installEventFilter(self)
        self._colocar()
        self.show()
        self.raise_()

    def _colocar(self) -> None:
        self.adjustSize()
        v = self.ventana
        self.move(max(0, v.width() - self.width() - self.MARGEN), max(0, v.height() - self.height() - self.MARGEN))

    def eventFilter(self, obj, e):  # noqa: N802 — método de Qt
        if obj is self.ventana and e.type() == QEvent.Type.Resize:
            self._colocar()
        return False

    def guardar(self) -> None:
        from .widgets import ask_save, ctx, show_done
        ruta = ask_save(self.ventana, str(Path(ctx.last_dir) / diagnostico.nombre_informe()), FILTRO,
                        "Guardar informe")
        if not ruta:
            return
        try:
            diagnostico.guardar_informe(ruta)
        except OSError as exc:
            QMessageBox.warning(self.ventana, "No se pudo guardar",
                                f"No se pudo guardar el informe:\n{exc.strerror or exc}")
            return
        diagnostico.evento("informe de diagnóstico guardado")
        self.cerrar()
        show_done(self.ventana, "Informe guardado",
                  "Puedes enviárselo a quien te ayude con RonyPDF. No incluye el contenido de tus documentos.",
                  file=ruta)

    def cerrar(self) -> None:
        try:
            self.ventana.removeEventFilter(self)
        except RuntimeError:
            pass
        self.hide()
        self.deleteLater()


def mostrar_aviso(ventana) -> AvisoCierre:
    aviso = AvisoCierre(ventana)
    ventana._aviso_cierre = aviso
    return aviso
