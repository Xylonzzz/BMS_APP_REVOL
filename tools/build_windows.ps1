[CmdletBinding()]
param(
    [string]$Python = "",
    [string]$Version = "0.1.0-candidate"
)

$ErrorActionPreference = "Stop"
$projectRoot = Split-Path -Parent $PSScriptRoot
if (-not $Python) {
    $Python = Join-Path $projectRoot ".venv\Scripts\python.exe"
}
if (-not (Test-Path -LiteralPath $Python -PathType Leaf)) {
    throw "找不到构建 Python：$Python。请先在项目 .venv 安装 requirements-build.txt。"
}
if ($Version -notmatch '^[A-Za-z0-9][A-Za-z0-9._-]*$') {
    throw "版本号只能包含字母、数字、点、下划线与连字符。"
}

Push-Location $projectRoot
try {
    & $Python -c "import sys, struct, tkinter, PyInstaller; print('Python', sys.version.split()[0], str(struct.calcsize('P') * 8) + '-bit'); print('PyInstaller', PyInstaller.__version__)"
    if ($LASTEXITCODE -ne 0) {
        throw "构建依赖检查失败；此脚本不会自动安装软件。"
    }
    $architecture = (& $Python -c "import struct; print('x64' if struct.calcsize('P') == 8 else 'x86')").Trim()
    if ($LASTEXITCODE -ne 0) {
        throw "无法识别构建 Python 的位数。"
    }
    $buildStamp = Get-Date -Format "yyyyMMdd-HHmmss"
    $releaseRoot = Join-Path $projectRoot "dist\windows-portable\$Version-$buildStamp"
    $workRoot = Join-Path $projectRoot "runtime\live-check\build-portable\$buildStamp"
    New-Item -ItemType Directory -Path $releaseRoot, $workRoot -Force | Out-Null
    $bundleArguments = @(
        "--noconfirm", "--onedir", "--windowed", "--name", "REVOL",
        "--distpath", $releaseRoot,
        "--workpath", (Join-Path $workRoot "work"),
        "--specpath", $workRoot,
        "--paths", $projectRoot,
        "--add-data", "$(Join-Path $projectRoot 'apps\web');apps/web",
        "--add-data", "$(Join-Path $projectRoot 'schemas');schemas",
        (Join-Path $projectRoot "apps\desktop_launcher.py")
    )
    & $Python -m PyInstaller @bundleArguments
    if ($LASTEXITCODE -ne 0) {
        throw "PyInstaller 构建失败。"
    }
    $productDirectory = Join-Path $releaseRoot "REVOL"
    $executable = Join-Path $productDirectory "REVOL.exe"
    if (-not (Test-Path -LiteralPath $executable -PathType Leaf)) {
        throw "没有生成预期的 REVOL.exe。"
    }
    Copy-Item -LiteralPath (Join-Path $projectRoot "docs\Windows便携版使用说明.md") -Destination (Join-Path $productDirectory "使用说明.md")
    $sourceRevision = & git rev-parse --short HEAD
    if ($LASTEXITCODE -ne 0) {
        $sourceRevision = "unknown"
    }
    $manifest = [ordered]@{
        product = "REVOL"
        version = $Version
        packaging = "PyInstaller one-directory portable candidate"
        architecture = $architecture
        builtAt = (Get-Date).ToString("o")
        sourceRevision = $sourceRevision
        sourceIncludesUncommittedChanges = $true
        exeSha256 = (Get-FileHash -LiteralPath $executable -Algorithm SHA256).Hash.ToLowerInvariant()
        hardwareValidated = $false
    }
    $manifest | ConvertTo-Json | Set-Content -LiteralPath (Join-Path $productDirectory "version.json") -Encoding utf8
    $archive = Join-Path $releaseRoot "REVOL-Windows-$architecture-$Version.zip"
    Compress-Archive -LiteralPath $productDirectory -DestinationPath $archive -CompressionLevel Optimal
    $archiveHash = (Get-FileHash -LiteralPath $archive -Algorithm SHA256).Hash.ToLowerInvariant()
    "$archiveHash  $([System.IO.Path]::GetFileName($archive))" | Set-Content -LiteralPath "$archive.sha256" -Encoding ascii
    Write-Output "可运行目录：$productDirectory"
    Write-Output "便携压缩包：$archive"
    Write-Output "SHA256：$archiveHash"
}
finally {
    Pop-Location
}
