$ErrorActionPreference = "Stop"

# Script location is <repo>/scripts, so parent is project root.
$projectRoot = Split-Path -Parent $PSScriptRoot
Set-Location $projectRoot

$logDir = Join-Path $projectRoot "output\logs"
New-Item -ItemType Directory -Path $logDir -Force | Out-Null
$stamp = Get-Date -Format "yyyyMMdd_HHmmss"
$logPath = Join-Path $logDir "newscollector_$stamp.log"

$venvPython = Join-Path $projectRoot ".venv\Scripts\python.exe"
$pythonExe = if (Test-Path $venvPython) { $venvPython } else { "python" }

$exitCode = 1
Start-Transcript -Path $logPath -Append | Out-Null
try {
    Write-Host "[INFO] Started at $(Get-Date -Format o)"
    Write-Host "[INFO] Project root: $projectRoot"
    Write-Host "[INFO] Python: $pythonExe"

    & $pythonExe -m newscollector.main
    $exitCode = $LASTEXITCODE
    Write-Host "[INFO] Exit code: $exitCode"
}
catch {
    Write-Host "[ERROR] $($_.Exception.Message)"
    $exitCode = 1
}
finally {
    Stop-Transcript | Out-Null
}

exit $exitCode
