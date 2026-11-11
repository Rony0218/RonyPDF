# Compila RonyPDF como una carpeta portable (dist\RonyPDF\RonyPDF.exe) y la empaqueta en
# dist\RonyPDF-<versión>-portable.zip. No se instala: se extrae y se abre RonyPDF.exe.
# Carpeta y no un solo .exe: un .exe «onefile» se descomprime entero en %TEMP% cada vez que se abre (lento en
# discos mecánicos y deja basura si la app se cierra mal); la carpeta arranca directo.
# Uso:  powershell -ExecutionPolicy Bypass -File build.ps1
param([string]$DistPath = "dist")
$ErrorActionPreference = "Stop"
Set-Location $PSScriptRoot

if (-not (Test-Path ".venv\Scripts\python.exe")) {
    Write-Host "Creando entorno virtual..." -ForegroundColor Cyan
    python -m venv .venv
}
$py = ".venv\Scripts\python.exe"
& $py -m pip install --quiet --upgrade pip
& $py -m pip install --quiet -r requirements.txt pyinstaller==6.22.3

Write-Host "Generando icono..." -ForegroundColor Cyan
& $py scripts\make_icon.py build\app.ico

Write-Host "Compilando ejecutable (tarda unos minutos)..." -ForegroundColor Cyan
$exclude = @(
    "tkinter", "numpy", "onnxruntime", "pymupdf4llm", "pymupdf_layout", "pytest",
    "PySide6.QtWebEngineCore", "PySide6.QtWebEngineWidgets", "PySide6.QtWebChannel",
    "PySide6.QtQml", "PySide6.QtQuick", "PySide6.QtQuickWidgets", "PySide6.QtMultimedia",
    "PySide6.QtPdf", "PySide6.QtPdfWidgets", "PySide6.QtNetwork", "PySide6.QtSql",
    "PySide6.Qt3DCore", "PySide6.QtCharts", "PySide6.QtDataVisualization", "PySide6.QtOpenGL",
    "PySide6.QtSvg", "PySide6.QtXml", "PySide6.QtTest", "PySide6.QtBluetooth", "PySide6.QtPositioning"
)
$piArgs = @("-m", "PyInstaller", "--noconfirm", "--clean", "--onedir", "--windowed", "--distpath", $DistPath,
          "--name", "RonyPDF", "--icon", "build\app.ico",
          "--version-file", "scripts\version_info.txt",
          "--add-data", "ronypdf\tessdata;ronypdf\tessdata",
          "--add-data", "ronypdf\assets;ronypdf\assets",
          "--collect-submodules", "ronypdf")
foreach ($m in $exclude) { $piArgs += @("--exclude-module", $m) }
# Las herramientas de ronypdf\*_ui.py se cargan por nombre (ver extensiones.py), así que PyInstaller no las
# ve al analizar el código: se incluyen una por una para no depender de --collect-submodules.
foreach ($m in Get-ChildItem "ronypdf\*_ui.py") { $piArgs += @("--hidden-import", "ronypdf.$($m.BaseName)") }
$piArgs += "run.py"
& $py @piArgs

if ($LASTEXITCODE -ne 0) { throw "PyInstaller falló. ¿Está abierto $DistPath\RonyPDF\RonyPDF.exe? Ciérralo e intenta de nuevo." }
$carpeta = Join-Path $DistPath "RonyPDF"
$exe = Get-Item (Join-Path $carpeta "RonyPDF.exe")

Write-Host "Comprobando que el ejecutable trae todas las herramientas..." -ForegroundColor Cyan
$informe = Join-Path ([System.IO.Path]::GetTempPath()) "ronypdf-comprobar-herramientas.txt"
Remove-Item $informe -ErrorAction SilentlyContinue
$proc = Start-Process -FilePath $exe.FullName -ArgumentList @("--comprobar-herramientas", "`"$informe`"") -Wait -PassThru
$detalle = if (Test-Path $informe) { (Get-Content $informe -Raw -Encoding UTF8).Trim() } else { "sin informe" }
if ($proc.ExitCode -ne 0) { throw "Al ejecutable le faltan herramientas (código $($proc.ExitCode)): $detalle" }
Write-Host $detalle

Write-Host "Empaquetando el zip portable..." -ForegroundColor Cyan
$version = (Select-String -Path "ronypdf\__init__.py" -Pattern '__version__ = "(.+)"').Matches[0].Groups[1].Value
Copy-Item "LICENSE", "TERMINOS-ADICIONALES.md" $carpeta -Force
Copy-Item "scripts\LEEME.txt" (Join-Path $carpeta "LEEME.txt") -Force
$zip = Join-Path $DistPath "RonyPDF-$version-portable.zip"
Remove-Item $zip -ErrorAction SilentlyContinue
# El tar de Windows (bsdtar) escribe zip estándar y es mucho más rápido que Compress-Archive.
& "$env:SystemRoot\System32\tar.exe" -a -c -f $zip -C $DistPath "RonyPDF"
if ($LASTEXITCODE -ne 0) { throw "No se pudo crear $zip" }
$tam = (Get-ChildItem $carpeta -Recurse -File | Measure-Object Length -Sum).Sum
Write-Host ("Listo: {0} ({1:N1} MB; la carpeta extraída ocupa {2:N0} MB)" -f (Resolve-Path $zip), ((Get-Item $zip).Length / 1MB), ($tam / 1MB)) -ForegroundColor Green

