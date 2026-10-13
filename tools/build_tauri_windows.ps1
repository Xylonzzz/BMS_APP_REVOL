[CmdletBinding()]
param(
    [string]$Python = "",
    [switch]$SkipSidecar,
    [switch]$NoBundle
)

$ErrorActionPreference = "Stop"
$projectRoot = Split-Path -Parent $PSScriptRoot
$desktopRoot = Join-Path $projectRoot "apps\desktop"
if (-not $Python) { $Python = Join-Path $projectRoot ".venv\Scripts\python.exe" }
$taskToolchains = Join-Path $projectRoot "runtime\live-check\toolchains"
$localCargo = Join-Path $taskToolchains "cargo\bin\cargo.exe"
$previousCargo = $env:CARGO_HOME
$previousRustup = $env:RUSTUP_HOME
$previousPath = $env:Path
if (Test-Path -LiteralPath $localCargo) {
    $env:CARGO_HOME = Join-Path $taskToolchains "cargo"
    $env:RUSTUP_HOME = Join-Path $taskToolchains "rustup"
    $env:Path = "$env:CARGO_HOME\bin;$env:Path"
}
Push-Location $projectRoot
try {
    & cargo --version
    if ($LASTEXITCODE -ne 0) { throw "Cargo 不可用；需要 Windows MSVC Rust 工具链。" }
    if (-not (Test-Path -LiteralPath (Join-Path $desktopRoot "node_modules\@tauri-apps\cli"))) {
        throw "请先在 apps/desktop 执行 npm ci；此脚本不会自动安装依赖。"
    }
    if (-not $SkipSidecar) {
        $buildWork = Join-Path $projectRoot "runtime\live-check\build-tauri-sidecar"
        New-Item -ItemType Directory -Path $buildWork -Force | Out-Null
        $pyArguments = @(
            "--noconfirm", "--onedir", "--console", "--name", "revol-sidecar",
            "--distpath", (Join-Path $desktopRoot "sidecar"),
            "--workpath", (Join-Path $buildWork "work"),
            "--specpath", $buildWork,
            "--paths", $projectRoot,
            "--add-data", "$(Join-Path $projectRoot 'apps\web');apps/web",
            "--add-data", "$(Join-Path $projectRoot 'schemas');schemas",
            (Join-Path $desktopRoot "sidecar\host.py")
        )
        & $Python -m PyInstaller @pyArguments
        if ($LASTEXITCODE -ne 0) { throw "Python sidecar 构建失败。" }
    }
    Push-Location $desktopRoot
    try {
        $tauriArguments = @("tauri", "build")
        if ($NoBundle) { $tauriArguments += "--no-bundle" }
        & npm exec -- @tauriArguments
        if ($LASTEXITCODE -ne 0) { throw "Tauri Windows 构建失败。" }
    }
    finally { Pop-Location }
    Write-Output "Tauri EXE：$(Join-Path $desktopRoot 'src-tauri\target\release\revol-desktop.exe')"
    if (-not $NoBundle) { Write-Output "安装包：$(Join-Path $desktopRoot 'src-tauri\target\release\bundle\nsis')" }
}
finally {
    Pop-Location
    $env:CARGO_HOME = $previousCargo
    $env:RUSTUP_HOME = $previousRustup
    $env:Path = $previousPath
}
