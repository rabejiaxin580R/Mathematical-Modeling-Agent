# 数学建模助教 · Windows 打包脚本
#
# 产出一个「自带 Python 运行时」的便携目录，再压成 zip。流程：
#   1. 下载 python-build-standalone（可重定位的 CPython，含 pip）
#   2. 用它把 requirements.txt 装进内置运行时
#   3. 拷贝 app（backend/frontend/scripts + 只读数据：knowledge / problems 等）
#   4. 用 PyInstaller 把 launcher/launch.py 编成「启动助教.exe」
#   5. 组装成 dist/数学建模助教/，并打 zip
#
# 之所以不直接 PyInstaller 整个 app：app 在运行时要 spawn `python script.py`
# 跑模型生成的代码、还有 IDE 终端，必须带一个**真**解释器。单文件 exe 会让
# sys.executable 指向 exe 自身，这些核心功能会失效。
#
# 用法（在 packaging/ 目录或任意目录）：
#   powershell -ExecutionPolicy Bypass -File build_windows.ps1
# 可选参数：
#   -PyVersion 3.13.5  指定内置 Python 版本（需有对应的 standalone 发布）

param(
  [string]$PyVersion = "3.13.5",
  [string]$PbsTag    = "20250712",   # python-build-standalone 发布 tag（见下方说明）
  [switch]$SkipInstaller             # 跳过 Inno Setup 编译，只出便携目录 + zip
)

$ErrorActionPreference = "Stop"
$PSNativeCommandUseErrorActionPreference = $false

# ── 路径 ──
$PkgDir   = $PSScriptRoot                      # packaging/
$Root     = Split-Path $PkgDir -Parent         # revent/
$AppSrc   = Join-Path $Root "agent"            # 源 app
$Work     = Join-Path $PkgDir "_work"          # 临时工作区（下载/解压）
$DistDir  = Join-Path $PkgDir "dist"
$OutName  = "数学建模助教"
$OutDir   = Join-Path $DistDir $OutName        # 最终便携目录
$AppDst   = Join-Path $OutDir "app"
$PyDst    = Join-Path $OutDir "python"

Write-Host "============================================" -ForegroundColor Cyan
Write-Host "  数学建模助教 · Windows 打包" -ForegroundColor Cyan
Write-Host "  Python $PyVersion (pbs $PbsTag)" -ForegroundColor Cyan
Write-Host "============================================" -ForegroundColor Cyan

if (-not (Test-Path (Join-Path $AppSrc "backend\main.py"))) {
  throw "找不到 app 源码：$AppSrc\backend\main.py"
}

# ── 0. 清理并建目录 ──
if (Test-Path $OutDir) { Remove-Item $OutDir -Recurse -Force }
New-Item -ItemType Directory -Force -Path $Work, $OutDir, $AppDst | Out-Null

# ── 1. 下载 python-build-standalone ──
# 资产命名形如：cpython-3.13.5+20250712-x86_64-pc-windows-msvc-install_only.tar.gz
$asset = "cpython-$PyVersion+$PbsTag-x86_64-pc-windows-msvc-install_only.tar.gz"
$url   = "https://github.com/astral-sh/python-build-standalone/releases/download/$PbsTag/$asset"
$tarball = Join-Path $Work $asset

if (-not (Test-Path $tarball)) {
  Write-Host "[1/5] 下载内置 Python：$url" -ForegroundColor Yellow
  try {
    Invoke-WebRequest -Uri $url -OutFile $tarball -UseBasicParsing
  } catch {
    throw "下载内置 Python 失败。请确认 PbsTag/PyVersion 组合在 python-build-standalone 的 releases 中存在：`n$url`n原始错误：$($_.Exception.Message)"
  }
} else {
  Write-Host "[1/5] 已存在安装包，跳过下载：$asset" -ForegroundColor DarkGray
}

# 解压（standalone 包顶层就是 python/ 目录）
Write-Host "      解压内置 Python…" -ForegroundColor DarkGray
$extract = Join-Path $Work "py_extract"
if (Test-Path $extract) { Remove-Item $extract -Recurse -Force }
New-Item -ItemType Directory -Force -Path $extract | Out-Null
tar -xzf $tarball -C $extract
$pySrc = Join-Path $extract "python"
if (-not (Test-Path (Join-Path $pySrc "python.exe"))) {
  throw "解压后未找到 python\python.exe，安装包结构可能有变。"
}
Copy-Item $pySrc -Destination $PyDst -Recurse -Force
$PyExe = Join-Path $PyDst "python.exe"

# ── 修复 GitHub Actions 中文路径 → pip 编码崩溃（cp1252 无法编码中文） ──
$env:PYTHONUTF8       = 1
$env:PYTHONIOENCODING = "utf-8"
[Console]::OutputEncoding = [System.Text.Encoding]::UTF8

# ── 2. 安装依赖到内置运行时 ──
Write-Host "[2/5] 安装依赖到内置 Python…" -ForegroundColor Yellow
& $PyExe -m pip install --upgrade pip --no-warn-script-location
& $PyExe -m pip install -r (Join-Path $AppSrc "requirements.txt") --no-warn-script-location
if ($LASTEXITCODE -ne 0) { throw "依赖安装失败（exit $LASTEXITCODE）" }

# ── 3. 拷贝 app（只带运行所需，排除虚拟环境与用户数据） ──
Write-Host "[3/5] 拷贝 app 源码与只读资源…" -ForegroundColor Yellow
Copy-Item (Join-Path $AppSrc "backend")  -Destination (Join-Path $AppDst "backend")  -Recurse -Force
Copy-Item (Join-Path $AppSrc "frontend") -Destination (Join-Path $AppDst "frontend") -Recurse -Force
if (Test-Path (Join-Path $AppSrc "scripts")) {
  Copy-Item (Join-Path $AppSrc "scripts") -Destination (Join-Path $AppDst "scripts") -Recurse -Force
}
Copy-Item (Join-Path $AppSrc "requirements.txt") -Destination $AppDst -Force
if (Test-Path (Join-Path $AppSrc ".env.example")) {
  Copy-Item (Join-Path $AppSrc ".env.example") -Destination $AppDst -Force
}

# 只读数据：知识库 + 真题库 + 附件 + 论文缓存（不带 conversations/runs/profiles 等运行时数据）
$dataDst = Join-Path $AppDst "data"
New-Item -ItemType Directory -Force -Path $dataDst | Out-Null
foreach ($d in @("knowledge", "problems", "problem_assets", "problem_papers")) {
  $src = Join-Path $AppSrc "data\$d"
  if (Test-Path $src) {
    Copy-Item $src -Destination (Join-Path $dataDst $d) -Recurse -Force
    Write-Host "      + data\$d" -ForegroundColor DarkGray
  }
}

# 清掉拷进来的 __pycache__，减小体积
Get-ChildItem $AppDst -Recurse -Directory -Filter "__pycache__" | Remove-Item -Recurse -Force -ErrorAction SilentlyContinue

# ── 4. PyInstaller 编译启动器 ──
Write-Host "[4/5] 编译一键启动器…" -ForegroundColor Yellow
# 用内置 Python 装 pyinstaller，保证编出来的 exe 与运行时一致
& $PyExe -m pip install pyinstaller --no-warn-script-location
if ($LASTEXITCODE -ne 0) { throw "PyInstaller 安装失败" }

$launcher = Join-Path $PkgDir "launcher\launch.py"
$iconArg  = @()
$iconPath = Join-Path $PkgDir "assets\app.ico"
if (Test-Path $iconPath) { $iconArg = @("--icon", $iconPath) }

Push-Location $Work
& $PyExe -m PyInstaller --onefile --console --name "启动助教" `
    --distpath (Join-Path $Work "launcher_dist") `
    --workpath (Join-Path $Work "launcher_build") `
    --specpath $Work `
    @iconArg $launcher
$plExit = $LASTEXITCODE
Pop-Location
if ($plExit -ne 0) { throw "PyInstaller 编译失败（exit $plExit）" }

Copy-Item (Join-Path $Work "launcher_dist\启动助教.exe") -Destination (Join-Path $OutDir "启动助教.exe") -Force

# 顺手放一份「使用说明」与「双击我启动.bat」兜底入口
$readmeTxt = @"
数学建模助教 —— 使用说明

1. 双击「启动助教.exe」即可启动；稍等十几秒，会自动打开浏览器。
2. 第一次使用会弹出「连接 AI 大模型」向导，二选一：
   - 用我们提供的额度：按提示去注册站点领 Key，粘回来即可；
   - 用我自己的 API Key：填 DeepSeek / 通义 / OpenAI 等自己的 Key。
3. 关闭弹出的黑色窗口即退出程序。你的数据保存在本机用户目录，卸载程序不会删除。

如果浏览器没自动打开，手动访问启动窗口里显示的地址（形如 http://127.0.0.1:8000/）。
"@
Set-Content -Path (Join-Path $OutDir "使用说明.txt") -Value $readmeTxt -Encoding utf8

$batTxt = "@echo off`r`nchcp 65001 >nul`r`ncd /d %~dp0`r`nstart """" ""启动助教.exe""`r`n"
Set-Content -Path (Join-Path $OutDir "双击我启动.bat") -Value $batTxt -Encoding utf8

# ── 5. 打 zip ──
Write-Host "[5/5] 打包 zip…" -ForegroundColor Yellow
$zipPath = Join-Path $DistDir "$OutName-windows-x64.zip"
if (Test-Path $zipPath) { Remove-Item $zipPath -Force }
Compress-Archive -Path $OutDir -DestinationPath $zipPath -CompressionLevel Optimal

$sizeMB = [math]::Round((Get-ChildItem $OutDir -Recurse -File | Measure-Object Length -Sum).Sum / 1MB, 0)
Write-Host "============================================" -ForegroundColor Green
Write-Host "  便携目录：$OutDir  (~$sizeMB MB)" -ForegroundColor Green
Write-Host "  压缩包：  $zipPath" -ForegroundColor Green
Write-Host "============================================" -ForegroundColor Green

# ── 6. （可选）Inno Setup 图形安装程序 ──
if (-not $SkipInstaller) {
  $iss = Join-Path $PkgDir "installer\setup.iss"
  $iscc = $null
  foreach ($c in @(
      "${env:ProgramFiles(x86)}\Inno Setup 6\ISCC.exe",
      "$env:ProgramFiles\Inno Setup 6\ISCC.exe")) {
    if (Test-Path $c) { $iscc = $c; break }
  }
  if (-not $iscc) { $iscc = (Get-Command iscc -ErrorAction SilentlyContinue).Source }

  if ($iscc -and (Test-Path $iss)) {
    Write-Host "[6] 编译 Inno Setup 安装程序…" -ForegroundColor Yellow
    & $iscc "/DSourceDir=$OutDir" "/DOutputDir=$DistDir" $iss
    if ($LASTEXITCODE -eq 0) {
      Write-Host "  安装程序已生成到：$DistDir" -ForegroundColor Green
    } else {
      Write-Host "  Inno Setup 编译失败（exit $LASTEXITCODE），便携包仍可用。" -ForegroundColor DarkYellow
    }
  } else {
    Write-Host "[6] 未检测到 Inno Setup（ISCC.exe），跳过安装程序。" -ForegroundColor DarkYellow
    Write-Host "    安装 Inno Setup 后重跑即可生成图形安装程序：https://jrsoftware.org/isdl.php" -ForegroundColor DarkGray
  }
}
