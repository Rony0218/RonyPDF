# Seguridad de RonyPDF

## La única fuente oficial

RonyPDF se publica solo aquí: **https://github.com/Rony0218/RonyPDF**. El zip portable oficial está en
[Versiones (Releases)](https://github.com/Rony0218/RonyPDF/releases).

Solo el autor puede cambiar este repositorio y sus versiones. Como el código es abierto, cualquiera puede hacer una
copia (un *fork*) y cambiarla, pero esa copia **no es RonyPDF**: no descargues el programa de otras páginas, enlaces
acortados, correos, grupos de chat ni copias de este repositorio.

## Cómo comprobar que tu RonyPDF es el original

Cada versión trae dos pruebas:

1. **Suma SHA-256.** En PowerShell, en la carpeta donde descargaste el zip:

   ```powershell
   Get-FileHash .\RonyPDF-2.0.0-portable.zip -Algorithm SHA256
   ```

   El resultado debe ser igual al de `SHA256SUMS.txt` y al de la nota de la versión. `SHA256SUMS.txt` trae
   también la suma de `RonyPDF.exe`, por si quieres comprobar el programa ya extraído.

2. **Procedencia firmada.** Con [GitHub CLI](https://cli.github.com/):

   ```powershell
   gh attestation verify RonyPDF-2.0.0-portable.zip --repo Rony0218/RonyPDF
   ```

   Comprueba, con una firma de [Sigstore](https://www.sigstore.dev/), que el archivo lo compiló GitHub Actions a
   partir del código de este repositorio, sin pasar por ningún computador personal. Funciona igual con
   `RonyPDF\RonyPDF.exe` después de extraer el zip.

Si alguna de las dos no coincide, no abras el archivo, bórralo y avisa (ver abajo).

## Cómo se protegen el código y las versiones

- Solo el autor puede escribir en el repositorio. Las propuestas de otras personas llegan como solicitudes
  (*pull requests*) y no entran sin que él las revise y las acepte.
- La rama principal no se puede borrar ni reescribir, y las etiquetas de versión no se pueden mover ni borrar.
- Las versiones publicadas son inmutables: nadie puede cambiar los archivos de una versión que ya salió.
- El programa se compila en los servidores de GitHub con un flujo público (`.github/workflows/publicar.yml`), y las
  acciones que usa están fijadas por su hash para que nadie pueda cambiarlas por debajo.
- RonyPDF no se conecta a internet ni se actualiza solo, así que nadie puede colarle una actualización falsa.
  Las versiones nuevas se descargan a mano desde la página oficial.

## Reportar un problema de seguridad

Usa **«Report a vulnerability»** en la pestaña [Security](https://github.com/Rony0218/RonyPDF/security) del
repositorio: el reporte es privado y solo lo ve el autor. Por favor no lo publiques en un *issue*.

Si encuentras una copia modificada que se hace pasar por RonyPDF, repórtala igual: según los
[términos adicionales](TERMINOS-ADICIONALES.md) de la licencia, una versión modificada no puede usar el nombre ni el
logo de RonyPDF.
