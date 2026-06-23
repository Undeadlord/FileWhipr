param(
    [switch]$SkipInstaller,
    [switch]$NoVenv,
    [string]$PythonCommand = "py -3.13"
)

$ErrorActionPreference = "Stop"

$InstallerDir = Split-Path -Parent $MyInvocation.MyCommand.Path
$RepoRoot = Split-Path -Parent $InstallerDir
Set-Location $RepoRoot
$BuildVenv = Join-Path $InstallerDir ".venv"
$BuildPython = Join-Path $BuildVenv "Scripts\python.exe"
$BuildTemp = Join-Path $InstallerDir ".tmp"
$PythonCommandParts = $PythonCommand -split "\s+"

function Invoke-Checked {
    param(
        [string]$FilePath,
        [string[]]$Arguments
    )

    & $FilePath @Arguments
    if ($LASTEXITCODE -ne 0) {
        throw "Command failed with exit code ${LASTEXITCODE}: $FilePath $($Arguments -join ' ')"
    }
}

function Invoke-BuildPythonCommand {
    param([string[]]$Arguments)

    $filePath = $PythonCommandParts[0]
    $baseArgs = @()
    if ($PythonCommandParts.Count -gt 1) {
        $baseArgs = $PythonCommandParts[1..($PythonCommandParts.Count - 1)]
    }
    Invoke-Checked $filePath ($baseArgs + $Arguments)
}

function Find-InnoCompiler {
    $cmd = Get-Command "ISCC.exe" -ErrorAction SilentlyContinue
    if ($cmd) {
        return $cmd.Source
    }

    $candidates = @(
        "C:\Program Files (x86)\Inno Setup 6\ISCC.exe",
        "C:\Program Files\Inno Setup 6\ISCC.exe"
    )

    foreach ($candidate in $candidates) {
        if (Test-Path -LiteralPath $candidate) {
            return $candidate
        }
    }

    return $null
}

if (-not $NoVenv) {
    New-Item -ItemType Directory -Path $BuildTemp -Force | Out-Null
    $env:TEMP = $BuildTemp
    $env:TMP = $BuildTemp

    if (-not (Test-Path -LiteralPath $BuildPython)) {
        Write-Host "Creating local build virtual environment..."
        Invoke-BuildPythonCommand @("-m", "venv", $BuildVenv)
    }

    Write-Host "Installing build dependencies into local build environment..."
    Invoke-Checked $BuildPython @("-m", "pip", "install", "--disable-pip-version-check", "--upgrade", "pip")
    Invoke-Checked $BuildPython @("-m", "pip", "install", "--disable-pip-version-check", "pyinstaller", "PySide6_Essentials")
    $PythonExe = $BuildPython
}
else {
    $PythonExe = & $PythonCommandParts[0] @($PythonCommandParts[1..($PythonCommandParts.Count - 1)]) -c "import sys; print(sys.executable)"
    if ($LASTEXITCODE -ne 0) {
        throw "Could not resolve Python executable from $PythonCommand"
    }
}

Write-Host "Building FileWhipr with PyInstaller..."
Invoke-Checked $PythonExe @("-m", "PyInstaller", "--version")
Invoke-Checked $PythonExe @("-m", "PyInstaller", "--clean", "--noconfirm", "FileWhipr.spec")

$distDir = Join-Path $RepoRoot "dist\FileWhipr"
$topLevelFiles = @(
    "FileWhipr.ico",
    "LICENSE",
    "NOTICE",
    "README.md"
)

foreach ($file in $topLevelFiles) {
    Copy-Item -LiteralPath (Join-Path $RepoRoot $file) -Destination (Join-Path $distDir $file) -Force
}

$prunePaths = @(
    "_internal\PySide6\translations",
    "_internal\PySide6\plugins\platforminputcontexts",
    "_internal\PySide6\plugins\generic",
    "_internal\PySide6\plugins\networkinformation",
    "_internal\PySide6\plugins\tls"
)

foreach ($path in $prunePaths) {
    $fullPath = Join-Path $distDir $path
    if (Test-Path -LiteralPath $fullPath) {
        Remove-Item -LiteralPath $fullPath -Recurse -Force
    }
}

$unusedQtFiles = @(
    "_internal\PySide6\opengl32sw.dll",
    "_internal\PySide6\Qt6Network.dll",
    "_internal\PySide6\Qt6OpenGL.dll",
    "_internal\PySide6\Qt6Pdf.dll",
    "_internal\PySide6\Qt6Qml.dll",
    "_internal\PySide6\Qt6QmlMeta.dll",
    "_internal\PySide6\Qt6QmlModels.dll",
    "_internal\PySide6\Qt6QmlWorkerScript.dll",
    "_internal\PySide6\Qt6Quick.dll",
    "_internal\PySide6\Qt6Svg.dll",
    "_internal\PySide6\Qt6VirtualKeyboard.dll",
    "_internal\PySide6\QtNetwork.pyd"
)

foreach ($path in $unusedQtFiles) {
    $fullPath = Join-Path $distDir $path
    if (Test-Path -LiteralPath $fullPath) {
        Remove-Item -LiteralPath $fullPath -Force
    }
}

$requiredFiles = @(
    "FileWhipr.exe",
    "FileWhiprLauncher.exe",
    "FileWhipr.ico",
    "LICENSE",
    "NOTICE",
    "README.md"
)

foreach ($file in $requiredFiles) {
    $path = Join-Path $distDir $file
    if (-not (Test-Path -LiteralPath $path)) {
        throw "Expected build output missing: $path"
    }
}

if ($SkipInstaller) {
    Write-Host "PyInstaller build complete. Skipping Inno Setup compile."
    exit 0
}

$iscc = Find-InnoCompiler
if (-not $iscc) {
    throw "Inno Setup compiler was not found. Install Inno Setup 6 or add ISCC.exe to PATH."
}

Write-Host "Compiling installer with Inno Setup..."
Invoke-Checked $iscc @((Join-Path $InstallerDir "FileWhipr.iss"))

Write-Host "Installer build complete."
