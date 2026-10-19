<#
.SYNOPSIS
  Build the Windows app into dist\windows\<APP_NAME>\.

.DESCRIPTION
  Run from PowerShell on 64-bit Windows 10/11, in the repository folder:

    powershell -ExecutionPolicy Bypass -File packaging\build_windows.ps1            # test, build, verify
    powershell -ExecutionPolicy Bypass -File packaging\build_windows.ps1 -Package   # ... and make the release zip
    ... -SkipTests        skip the unit tests
    ... -SkipSmokeTest    skip starting the built app in mock mode

  Uses its own virtual environment (build\venv-windows) with the pinned versions from
  packaging\constraints.txt, and its own work folder (build\pyinstaller-windows). macOS build
  outputs (dist\arm64, dist\x86_64, dist\*.app) are never touched. User data in %APPDATA% is
  never read or written: the smoke test uses a throwaway data folder.

  Needs: Python 3.11+ (64-bit, from python.org; release builds use 3.14). Set $env:PYTHON to
  choose a specific python.exe.
#>
[CmdletBinding()]
param(
    [switch]$Package,
    [switch]$SkipTests,
    [switch]$SkipSmokeTest
)
Set-StrictMode -Version 3
$ErrorActionPreference = 'Stop'
$Root = Split-Path -Parent $PSScriptRoot
Set-Location $Root

function Step([string]$Message) { Write-Host "`n==> $Message" -ForegroundColor Cyan }
function Fail([string]$Message) { Write-Host "`nBuild failed: $Message" -ForegroundColor Red; exit 1 }
function Note([string]$Message) { Write-Host $Message }

# --- Environment -----------------------------------------------------------------------------
if ([System.Environment]::OSVersion.Platform -ne 'Win32NT') { Fail 'this script builds the Windows app and must run on Windows (macOS: packaging/build_app.sh).' }
if (-not [System.Environment]::Is64BitOperatingSystem) { Fail 'a 64-bit Windows is required (the app is built for x64).' }

$init = Get-Content -Raw -Encoding UTF8 (Join-Path $Root 'mcmanager\__init__.py')
$AppName = [regex]::Match($init, 'APP_NAME = "([^"]+)"').Groups[1].Value
$Version = [regex]::Match($init, '__version__ = "([^"]+)"').Groups[1].Value
if (-not $AppName -or -not $Version) { Fail 'could not read APP_NAME / __version__ from mcmanager\__init__.py' }

# Python: $env:PYTHON, else python.exe on PATH, else the py launcher.
$PyExe = $null; $PyArgs = @()
if ($env:PYTHON) { $PyExe = $env:PYTHON }
elseif (Get-Command python -ErrorAction SilentlyContinue) { $PyExe = 'python' }
elseif (Get-Command py -ErrorAction SilentlyContinue) { $PyExe = 'py'; $PyArgs = @('-3') }
else { Fail 'Python not found. Install Python 3.11+ (64-bit) from https://www.python.org/downloads/windows/ and tick "Add python.exe to PATH".' }
$check = & $PyExe @PyArgs -c 'import struct, sys; print(sys.version_info >= (3, 11), struct.calcsize("P") * 8, sys.version.split()[0])'
if ($LASTEXITCODE -ne 0 -or -not $check) { Fail "could not run Python ($PyExe)." }
$parts = "$check".Trim().Split(' ')
if ($parts[0] -ne 'True') { Fail "Python 3.11 or newer is required (found $($parts[2]))." }
if ($parts[1] -ne '64') { Fail "a 64-bit Python is required (found a 32-bit Python $($parts[2]))." }

$Venv = Join-Path $Root 'build\venv-windows'
$VPy = Join-Path $Venv 'Scripts\python.exe'
$DistRoot = Join-Path $Root 'dist\windows'
$AppDir = Join-Path $DistRoot $AppName
$Exe = Join-Path $AppDir "$AppName.exe"
$ConsoleExe = Join-Path $AppDir "$AppName (console).exe"

Step "Preparing build environment (build\venv-windows, Python $($parts[2]))"
if (-not (Test-Path $VPy)) {
    & $PyExe @PyArgs -m venv $Venv
    if ($LASTEXITCODE -ne 0) { Fail "could not create $Venv" }
}
& $VPy -m pip install -q --upgrade pip
if ($LASTEXITCODE -ne 0) { Fail 'could not update pip (offline?)' }
& $VPy -m pip install -q -r (Join-Path $Root 'packaging\requirements-build.txt')
if ($LASTEXITCODE -ne 0) { Fail 'could not install the pinned build dependencies (see the pip output above).' }
Note "PyInstaller $(& $VPy -m PyInstaller --version), Python $($parts[2]), x64"

if (-not $SkipTests) {
    Step 'Running tests'
    & $VPy -m pytest -q -p no:cacheprovider
    if ($LASTEXITCODE -ne 0) { Fail 'tests failed; fix them or rerun with -SkipTests.' }
}

$running = Get-Process -ErrorAction SilentlyContinue | Where-Object { $_.Path -and $_.Path.StartsWith($AppDir, [StringComparison]::OrdinalIgnoreCase) }
if ($running) { Fail "the app is running from $AppDir. Quit it first, then build again." }

# --- Build -----------------------------------------------------------------------------------
Step 'Building the Windows app'
& $VPy -m PyInstaller --noconfirm --clean --log-level WARN `
    --distpath $DistRoot --workpath (Join-Path $Root 'build\pyinstaller-windows') `
    (Join-Path $Root 'packaging\mcmanager_windows.spec')
if ($LASTEXITCODE -ne 0) { Fail 'PyInstaller failed (see the messages above).' }

# --- Verify ----------------------------------------------------------------------------------
Step 'Verifying'
foreach ($f in @($Exe, $ConsoleExe)) { if (-not (Test-Path $f)) { Fail "missing: $f" } }

function Get-PeMachine([string]$Path) {
    $bytes = [System.IO.File]::ReadAllBytes($Path)
    $pe = [BitConverter]::ToInt32($bytes, 0x3C)
    return [BitConverter]::ToUInt16($bytes, $pe + 4)
}
foreach ($f in @($Exe, $ConsoleExe)) {
    if ((Get-PeMachine $f) -ne 0x8664) { Fail "$f is not an x64 executable." }
}
Note 'Executables: x64 (windowed app + console diagnostics)'

$internal = Join-Path $AppDir '_internal'
foreach ($plugin in @('qwindows.dll', 'qsvg.dll')) {
    if (-not (Get-ChildItem -Path $internal -Recurse -Filter $plugin -ErrorAction SilentlyContinue)) { Fail "Qt plugin $plugin is missing." }
}
Note 'Qt plugins: platform (qwindows) and SVG present'
foreach ($f in @('LICENSE', 'THIRD_PARTY_NOTICES.md', 'LGPL-3.0.txt', 'GPL-3.0.txt')) {
    if (-not (Test-Path (Join-Path $internal "licenses\$f"))) { Fail "license file missing: licenses\$f" }
}
Note 'Licenses: third-party notices and license texts included'

$info = (Get-Item $Exe).VersionInfo
if ($info.ProductName -ne $AppName -or $info.ProductVersion -ne $Version) { Fail "version info is '$($info.ProductName) $($info.ProductVersion)', expected '$AppName $Version'." }
Note "Version info: $($info.ProductName) $($info.ProductVersion)"

Add-Type -AssemblyName System.Drawing
$exeIcon = [System.Drawing.Icon]::ExtractAssociatedIcon($Exe)
if (-not $exeIcon -or $exeIcon.Width -lt 16) { Fail 'the .exe has no usable icon.' }
$shell32 = Add-Type -PassThru -Name IconCount -Namespace McmBuild -MemberDefinition '[DllImport("shell32.dll", CharSet = CharSet.Unicode)] public static extern uint ExtractIconExW(string file, int index, System.IntPtr[] large, System.IntPtr[] small, uint count);'
if ($shell32::ExtractIconExW($Exe, -1, $null, $null, 0) -lt 1) { Fail 'the .exe contains no icon resource.' }
Note 'Icon: embedded in the .exe'

$report = & $ConsoleExe --self-check 2>$null
$code = $LASTEXITCODE
$json = $null
try { $json = ($report -join "`n") | ConvertFrom-Json } catch { }
if ($code -ne 0 -or -not $json -or -not $json.ok) { Write-Host ($report -join "`n"); Fail 'the built app is missing dependencies or resources (self-check report above).' }
if ($json.platform -ne 'win32' -or $json.machine -ne 'x86_64') { Fail "self-check ran as $($json.platform)/$($json.machine), expected win32/x86_64." }
Note "Self-check: all dependencies and resources load (Prism: $($json.prism); notifications: $($json.notifications.backend))"

# --- Smoke test: the packaged app in mock mode (fake games, throwaway data) ------------------------
if (-not $SkipSmokeTest) {
    Step 'Smoke test: packaged app in mock mode'
    $tmp = Join-Path ([System.IO.Path]::GetTempPath()) ("mcm-smoke-" + [guid]::NewGuid().ToString('N'))
    New-Item -ItemType Directory -Path $tmp | Out-Null
    $shot = Join-Path $tmp 'smoke.png'
    $env:MCMANAGER_DATA_DIR = $tmp
    try {
        $p = Start-Process -FilePath $Exe -PassThru -ArgumentList @('--mock', '--launch-all-on-start', '--screenshot', "`"$shot`"", '--screenshot-delay', '30')
        if (-not $p.WaitForExit(150000)) { $p.Kill(); Fail 'the app did not finish the smoke test within 150 s.' }
    } finally {
        Remove-Item Env:MCMANAGER_DATA_DIR -ErrorAction SilentlyContinue
        # The fake games outlive the dashboard by design; stop the ones this test started.
        Get-CimInstance Win32_Process | Where-Object { $_.CommandLine -and $_.CommandLine -like '*--mcmanager-fake-client*' -and $_.CommandLine -like "*$tmp*" } |
            ForEach-Object { Stop-Process -Id $_.ProcessId -Force -ErrorAction SilentlyContinue }
    }
    $logFile = Join-Path $tmp 'logs\app.log'
    if (-not (Test-Path $logFile)) { Fail "the app wrote no log ($logFile)." }
    $log = Get-Content -Raw -Encoding UTF8 $logFile
    $expect = [ordered]@{
        'Launch queue finished: 4 started' = 'staggered launch of 4 fake games';
        'Mock Crashy: process' = 'game process tracking';
        '-> Crashed' = 'crash detection';
        'Window icon: small set, big set' = 'taskbar and title bar icon';
        'Closing dashboard' = 'clean shutdown'
    }
    foreach ($k in $expect.Keys) {
        if (-not $log.Contains($k)) { Write-Host $log; Fail "smoke test: no '$k' in the log ($($expect[$k]))." }
    }
    if ($log -match 'Unhandled exception') { Write-Host $log; Fail 'smoke test: the app logged an unhandled exception.' }
    if (-not (Test-Path $shot)) { Fail 'smoke test: no screenshot was saved.' }
    $smokeOut = Join-Path $Root 'build\windows-smoke'
    New-Item -ItemType Directory -Force -Path $smokeOut | Out-Null
    Copy-Item $shot (Join-Path $smokeOut 'smoke.png') -Force
    Copy-Item $logFile (Join-Path $smokeOut 'app.log') -Force
    Note "Smoke test: 4 fake games launched and tracked, crash detected, screenshot in build\windows-smoke\"
}

# --- Release zip -----------------------------------------------------------------------------
if ($Package) {
    Step 'Packaging release zip'
    $ReleaseDir = Join-Path $Root 'dist\release'
    New-Item -ItemType Directory -Force -Path $ReleaseDir | Out-Null
    $ZipName = "$($AppName -replace ' ', '-')-$Version-Windows-x64.zip"
    $Zip = Join-Path $ReleaseDir $ZipName
    if (Test-Path $Zip) { Fail "$Zip already exists. Move it away first (it is never overwritten)." }
    Compress-Archive -Path $AppDir -DestinationPath $Zip
    $hash = (Get-FileHash -Algorithm SHA256 $Zip).Hash.ToLower()
    "$hash  $ZipName" | Out-File -Encoding ascii "$Zip.sha256"
    Note "Created:  $Zip ($([math]::Round((Get-Item $Zip).Length / 1MB)) MB)"
    Note "SHA-256:  $hash"
}

Step 'Done'
Note "App:  $Exe"
Note "Diagnostics: run `"$ConsoleExe`" (shows the log in a console; add --self-check for a report)"
