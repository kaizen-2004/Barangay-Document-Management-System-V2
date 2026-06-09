param(
    [string]$AppDir = "C:\barangay_system\app",
    [string]$ServiceName = "BarangaySystem",
    [string]$Host = "0.0.0.0",
    [int]$Port = 5000,
    [string]$NssmPath = "nssm",
    [string]$ExecutablePath = ""
)

$ErrorActionPreference = "Stop"

if (-not (Get-Command $NssmPath -ErrorAction SilentlyContinue)) {
    throw "NSSM executable not found. Provide -NssmPath with full path to nssm.exe"
}

if (-not (Test-Path $AppDir)) {
    throw "App directory not found: $AppDir"
}

if ($ExecutablePath -and -not (Test-Path $ExecutablePath)) {
    throw "Executable not found: $ExecutablePath"
}

$appBinary = $null
$appArgs = @()
if ($ExecutablePath) {
    $appBinary = $ExecutablePath
} else {
    $uvCmd = (Get-Command uv -ErrorAction SilentlyContinue)
    if (-not $uvCmd) {
        throw "uv is not installed or not in PATH. Install uv first, or pass -ExecutablePath."
    }
    $appBinary = $uvCmd.Source
    $appArgs = @("run", "waitress-serve", "--host=$Host", "--port=$Port", "wsgi:app")
}

& $NssmPath install $ServiceName $appBinary $appArgs
& $NssmPath set $ServiceName AppDirectory $AppDir
& $NssmPath set $ServiceName Start SERVICE_AUTO_START
& $NssmPath set $ServiceName AppStdout "$AppDir\..\logs\service-out.log"
& $NssmPath set $ServiceName AppStderr "$AppDir\..\logs\service-err.log"
& $NssmPath start $ServiceName

Write-Host "Service '$ServiceName' installed and started."
