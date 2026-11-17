# =============================================================================
# Script Dinámico Multi-Dispositivo de Compilación C++17 y Base de Datos SQLite3
# No utiliza rutas absolutas fijas: detecta automáticamente la carpeta raíz del
# proyecto, el intérprete de Python, sqlite3.dll y el compilador g++ (MinGW/CLion).
# =============================================================================
$ErrorActionPreference = "Stop"

# Posicionarse dinámicamente en la raíz del proyecto sin importar desde dónde se invoque
$ProjectRoot = Split-Path -Parent $MyInvocation.MyCommand.Definition
Set-Location -Path $ProjectRoot

function Find-PythonCommand {
    foreach ($cmd in @("py", "python", "python3")) {
        $found = Get-Command $cmd -ErrorAction SilentlyContinue
        if ($found) {
            try {
                $ver = & $cmd --version 2>&1
                if ($LASTEXITCODE -eq 0 -and $ver -match "Python") {
                    return $cmd
                }
            } catch {}
        }
    }
    throw "No se encontró una instalación válida de Python en este dispositivo."
}

function Find-GppCompiler {
    # 1. Buscar en el PATH actual
    $inPath = Get-Command "g++.exe" -ErrorAction SilentlyContinue
    if ($inPath) { return $inPath.Source }

    # 2. Buscar dinámicamente en instalaciones estándar de CLion, MSYS2 y MinGW en cualquier disco/usuario
    $searchPatterns = @(
        "$env:ProgramFiles\JetBrains\CLion*\bin\mingw\bin\g++.exe",
        "${env:ProgramFiles(x86)}\JetBrains\CLion*\bin\mingw\bin\g++.exe",
        "$env:LOCALAPPDATA\Programs\CLion*\bin\mingw\bin\g++.exe",
        "$env:LOCALAPPDATA\JetBrains\Toolbox\apps\CLion\*\*\bin\mingw\bin\g++.exe",
        "C:\msys64\ucrt64\bin\g++.exe",
        "C:\msys64\mingw64\bin\g++.exe",
        "C:\mingw64\bin\g++.exe",
        "C:\MinGW\bin\g++.exe",
        "C:\w64devkit\bin\g++.exe"
    )
    foreach ($pattern in $searchPatterns) {
        $matches = Get-Item $pattern -ErrorAction SilentlyContinue | Sort-Object FullName -Descending
        if ($matches) {
            return $matches[0].FullName
        }
    }
    throw "No se encontró el compilador g++.exe (MinGW-w64 / CLion) en este dispositivo."
}

$PyCmd = Find-PythonCommand
Write-Host "[1/3] Inicializando Base de Datos SQLite3 usando '$PyCmd'..." -ForegroundColor Cyan
& $PyCmd "database/db_manager.py" --init --stats

# Detener instancia previa de lpf_backend si estuviera corriendo para liberar los binarios
Get-Process -Name "lpf_backend" -ErrorAction SilentlyContinue | Stop-Process -Force -ErrorAction SilentlyContinue

New-Item -ItemType Directory -Force -Path "bin" | Out-Null

# Localizar dinámicamente sqlite3.dll desde la instalación activa de Python si aún no está en bin/
if (-not (Test-Path "bin/sqlite3.dll")) {
    $SqliteDllPath = (& $PyCmd -c "import _sqlite3, pathlib; p = pathlib.Path(_sqlite3.__file__).resolve().parent / 'sqlite3.dll'; print(p if p.exists() else '')").Trim()
    if ($SqliteDllPath -and (Test-Path $SqliteDllPath)) {
        Copy-Item -Path $SqliteDllPath -Destination "bin/sqlite3.dll" -Force
        Write-Host "      -> sqlite3.dll copiado dinámicamente desde: $SqliteDllPath" -ForegroundColor DarkGray
    }
}

$GppExe = Find-GppCompiler
$GppDir = Split-Path -Parent $GppExe
$env:PATH = "$GppDir;$env:PATH"

Write-Host "`n[2/3] Compilando Backend C++17 con: $GppExe" -ForegroundColor Cyan
& $GppExe -std=c++17 -O3 -Wall -Wextra -Ibackend_cpp/include backend_cpp/src/main.cpp -o bin/lpf_backend.exe -lws2_32 -static
& $GppExe -std=c++17 -O3 -Wall -Wextra -Ibackend_cpp/include backend_cpp/tests/test_linear_algebra.cpp -o bin/test_linear_algebra.exe -lws2_32 -static

Write-Host "`n[3/3] Ejecutando Suite de Pruebas Unitarias y de Concurrencia C++17..." -ForegroundColor Cyan
.\bin\test_linear_algebra.exe
