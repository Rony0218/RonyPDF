# Compila RonyPDF como un único ejecutable portable: dist\RonyPDF.exe
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
& $py -m pip install --quiet -r requirements.txt pyinstaller

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
$piArgs = @("-m", "PyInstaller", "--noconfirm", "--clean", "--onefile", "--windowed", "--distpath", $DistPath,
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

if ($LASTEXITCODE -ne 0) { throw "PyInstaller falló. ¿Está abierto $DistPath\RonyPDF.exe? Ciérralo e intenta de nuevo." }
$exe = Get-Item "$DistPath\RonyPDF.exe"

Write-Host "Comprobando que el ejecutable trae todas las herramientas..." -ForegroundColor Cyan
$informe = Join-Path ([System.IO.Path]::GetTempPath()) "ronypdf-comprobar-herramientas.txt"
Remove-Item $informe -ErrorAction SilentlyContinue
$proc = Start-Process -FilePath $exe.FullName -ArgumentList @("--comprobar-herramientas", "`"$informe`"") -Wait -PassThru
$detalle = if (Test-Path $informe) { (Get-Content $informe -Raw -Encoding UTF8).Trim() } else { "sin informe" }
if ($proc.ExitCode -ne 0) { throw "Al ejecutable le faltan herramientas (código $($proc.ExitCode)): $detalle" }
Write-Host $detalle

Write-Host ("Listo: {0} ({1:N1} MB)" -f $exe.FullName, ($exe.Length / 1MB)) -ForegroundColor Green

