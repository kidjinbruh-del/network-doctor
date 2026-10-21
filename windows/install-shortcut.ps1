$ErrorActionPreference = "Stop"

$Root = Split-Path -Parent $PSScriptRoot
$Pythonw = Join-Path $Root ".venv\Scripts\pythonw.exe"
$App = Join-Path $Root "app.py"
$Icon = Join-Path $Root "assets\network-doctor.ico"

if (-not (Test-Path -LiteralPath $Pythonw)) {
    throw "pythonw.exe not found. Create .venv and install requirements.txt first."
}
if (-not (Test-Path -LiteralPath $App)) {
    throw "app.py not found in $Root"
}
if (-not (Test-Path -LiteralPath $Icon)) {
    throw "Icon not found: $Icon"
}

$Shell = New-Object -ComObject WScript.Shell
$Targets = @(
    (Join-Path ([Environment]::GetFolderPath("Desktop")) "Network Doctor.lnk"),
    (Join-Path ([Environment]::GetFolderPath("Programs")) "Network Doctor.lnk")
)

foreach ($Target in $Targets) {
    $Shortcut = $Shell.CreateShortcut($Target)
    $Shortcut.TargetPath = $Pythonw
    $Shortcut.Arguments = "`"$App`""
    $Shortcut.WorkingDirectory = $Root
    $Shortcut.IconLocation = "$Icon,0"
    $Shortcut.Description = "Network Doctor - MikroTik Wi-Fi diagnostics"
    $Shortcut.WindowStyle = 1
    $Shortcut.Save()
    Write-Output "Shortcut created: $Target"
}
