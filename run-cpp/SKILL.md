---
name: run-cpp
description: "当任务需要编译/运行本机 C/C++ 代码、需要选择 MSVC/MinGW 工具链、需要下载或复用外部 C/C++ 库（预编译版）时，读取本 skill 并遵循其规范：所有外部库统一放 D:\\tools\\cpp-libs，按本机架构与工具链优先从国内镜像下载预编译版本，并把 include/lib 路径与下载方式写入本地缓存；每个会话窗口只在首次调用时做一次决策和 10% 概率的缓存核对。"
whenToUse: "需要编译 C/C++ 源码（g++/cl/cmake）、需要外部库（curl/opencv/ssl 等）的头文件与链接库、需要决定下载哪个预编译包（MSVC vs MinGW、x64 vs arm64）时；用户显式调用 /run-cpp 或 $run-cpp 时也启用。"
---

# run-cpp — 本机 C/C++ 编译与外部库规范

只要任务需要"在本机编译 C/C++"或"引入外部 C/C++ 库"，就先按本 skill 执行。不要随手 `g++`/`cl` 裸编译、不要把库乱装到系统目录、不要每次重新下载同一个库。

## 0. 硬性规则（优先级最高）

1. **所有外部库统一安装在 `D:\tools\cpp-libs`**。目录结构：`D:\tools\cpp-libs\<lib>\<version>\<toolchain>-<arch>\`，其下是 `include/`、`lib/`（.lib/.a/.dll）。不改系统目录、不往 `C:\` 装库、不动用户其他工程的库。
2. **默认按本机架构与工具链选预编译版本**：架构取本机 CPU（x64 / arm64），工具链按"已有工具链优先 + 项目需要"决定（MSVC 用 v143+ 的 cl，MinGW 用 g++/gcc）。用户明确指定架构/工具链时按用户指定。
3. **预编译包优先走国内镜像下载**（§6 有镜像清单与回退顺序），镜像失败才走官方源。
4. **每个会话窗口只在首次调用时做一次"工具链/架构决策 + 库盘点"**，并记为本窗口活动工具链；窗口内后续编译直接复用，不再重复探测、不再重复下载。
5. **10% 概率**核对本机 `D:\tools\cpp-libs` 实际内容与缓存是否一致（新增/删除/升级），不一致就更新缓存；只在窗口首次调用时掷一次。
6. 每次下载/登记库都要**写入本地缓存**（`cpp-libs-cache.json`），记录 include/lib 路径与下载来源，供后续任务直接复用。

## 1. 本 skill 的资源与辅助脚本

Base directory 由 `<skill_resources>` 给出（本机为 `C:\Users\31913\.dsh\skills\run-cpp`）。相对路径基于它解析。

- `scripts/analyze_includes.py` —— 扫描 C/C++ 源码的 `#include`，过滤标准库/系统头，输出 `{"headers": [...], "libs": [...], "unknown": [...]}`（libs 是需要的库名）。
- `scripts/cpp_lib_cache.py` —— 外部库缓存管理器（仅标准库、不联网、不派生子进程）。命令：`list` / `get <lib>` / `have <lib>` / `add <lib> --include ...` / `sync <lib>` / `require <lib>` / `now-flags <libs...>` / `active [<lib>]`。

缓存文件默认 `C:\Users\31913\.dsh\skills\run-cpp\cpp-libs-cache.json`；写不进去时回退 `~/.cpp-libs-cache.json`。库目录固定 `D:\tools\cpp-libs`（缓存里 `libsRoot` 字段）。

辅助脚本用任意本机 python 运行（优先 `D:\tools\miniforge3\python.exe` 或 PATH 里的 python）：

```powershell
& $py "C:\Users\31913\.dsh\skills\run-cpp\scripts\analyze_includes.py" --file "D:\dsh\work\main.cpp"
```

## 2. 探测本机工具链与架构（窗口首次）

```powershell
# 架构
$arch = if ($env:PROCESSOR_ARCHITEW6432) { $env:PROCESSOR_ARCHITEW6432 } else { $env:PROCESSOR_ARCHITECTURE }
# AMD64→x64, ARM64→arm64, x86→x86
switch ($arch) { "AMD64" { $arch = "x64" } "ARM64" { $arch = "arm64" } "x86" { $arch = "x86" } }

# MSVC（vswhere 找 VS 安装 + cl.exe）
$vswhere = "${env:ProgramFiles(x86)}\Microsoft Visual Studio\Installer\vswhere.exe"
if (Test-Path $vswhere) {
  $vs = & $vswhere -latest -products * -requires Microsoft.VisualStudio.Component.VC.Tools.x86.x64 -property installationPath 2>$null
  if ($vs) {
    $msvc = Get-ChildItem (Join-Path $vs "VC\Tools\MSVC") -Directory -ErrorAction SilentlyContinue | Sort-Object Name -Descending | Select-Object -First 1
    if ($msvc) { $clPath = Join-Path $msvc.FullName "bin\Host$($arch)\$($arch)\cl.exe" }
  }
}
# MinGW（本机已知：D:\tools\QT\Tools\mingw1310_64\bin\g++.exe；也可 PATH 找）
$gxx = (Get-Command g++ -ErrorAction SilentlyContinue).Source
if (-not $gxx -and (Test-Path "D:\tools\QT\Tools\mingw1310_64\bin\g++.exe")) { $gxx = "D:\tools\QT\Tools\mingw1310_64\bin\g++.exe" }
```

- 本机实测：有 MSVC（Visual Studio 18 Community，vswhere 可定位）+ MinGW（QT 自带 mingw1310_64，g++ 13.1.0）+ cmake（D:\tools\cmake\...）。
- 决策：项目/用户没指定时，**默认 MinGW（g++）**，因为它开箱即用、无需进 VS 环境；需要 MSVC 特性（/MT、Windows SDK 深度集成）或用户指定时用 MSVC。工具链选定后记为本窗口活动工具链（§7）。

## 3. 盘点源码需要哪些库

```powershell
$req = & $py "C:\Users\31913\.dsh\skills\run-cpp\scripts\analyze_includes.py" --file "D:\dsh\work\main.cpp" | ConvertFrom-Json
$needLibs = @($req.libs)     # 例如 curl, openssl, sdl2
$unknown  = @($req.unknown)  # 没识别出的头，人工确认是否是项目内头
```

有多个源文件就多次 `--file`（或先用通配符把文件拼进一个临时列表）。`unknown` 里的头如果确实是项目自己写的（在源码目录里）就忽略；如果像 `something/special.h` 且不在项目里，说明映射表缺项，按库名手动处理并登记。

## 4. 窗口首次：登记/核对库缓存

**第一步：本窗口是否已有活动工具链？** 有 → 直接用它，跳到 §5。

```powershell
$active = (& $py "C:\Users\31913\.dsh\skills\run-cpp\scripts\cpp_lib_cache.py" active --session <窗口id>)
if ($active -ne "NONE") { 读 JSON 取 toolchain/arch/libs；跳到 §5 }
```

**第二步：决策工具链与架构**（§2 探测结果），并把窗口活动状态记下（此刻还没有库，先只记 toolchain/arch）：

```powershell
& $py "...\cpp_lib_cache.py" active --session <窗口id> --toolchain $tc --arch $arch   # 先空库集
```

**第三步：10% 概率核对缓存与实际库目录**（仅窗口首次掷一次）：

```powershell
$roll = Get-Random -Minimum 0 -Maximum 10
if ($roll -eq 0) {
  # 对照 D:\tools\cpp-libs 实际目录与缓存：新增/删除/升级都更新缓存
  & $py "...\cpp_lib_cache.py" list                 # 看缓存里有哪些库
  Get-ChildItem "D:\tools\cpp-libs" -Directory | ForEach-Object {
    & $py "...\cpp_lib_cache.py" sync $_.Name --toolchain $tc --arch $arch
  }
}
```

**第四步：对每个需要的库 `require`**（在缓存里 → 直接用；不在 → 走 §6 下载）：

```powershell
foreach ($lib in $needLibs) {
  & $py "...\cpp_lib_cache.py" require $lib --toolchain $tc --arch $arch
  if ($LASTEXITCODE -ne 0) { 该库需要下载 → §6 }
}
```

## 5. 编译命令（含 include/lib 参数）

```powershell
# 拿到所有 -I/-L 参数
$flags = & $py "...\cpp_lib_cache.py" now-flags $needLibs --toolchain $tc --arch $arch

# MinGW
& $gxx "D:\dsh\work\main.cpp" $flags -o "D:\dsh\work\app.exe"
# MSVC（需先在 VS 开发者环境里，或调用 vcvars64.bat）
& $cl "D:\dsh\work\main.cpp" /EHsc $flags /Fe:"D:\dsh\work\app.exe"
```

- 运行 .exe 时如果库是 DLL，把 `D:\tools\cpp-libs\<lib>\<version>\<tc>-<arch>\bin`（或 lib 目录里的 dll）加入 PATH，或复制 dll 到 exe 旁。
- 编译报缺头/缺库时，回到 §3 检查 `unknown` 与映射，缺失的库走 §6。

## 6. 下载预编译库 —— 国内镜像优先

**选型**：
- 架构：默认本机 `$arch`（x64/arm64），用户指定则按指定。
- 工具链：按 §2 决策的活动工具链（默认 MinGW）。包名/资产名优先匹配 `mingw`（或 `gcc`）或 `msvc` + `x64`/`arm64`/`win64`/`windows` 关键字。
- 版本：优先与已缓存同库版本一致；其次最新稳定版；用户指定版本则按指定。

**下载顺序（镜像优先，逐个失败再下一个）**：

```powershell
# 1) GitHub Releases 经国内镜像（先试最快的几个）
$mirrors = @(
  "https://ghproxy.net/https://github.com/",
  "https://mirror.ghproxy.com/https://github.com/",
  "https://gh-proxy.com/https://github.com/",
  "https://github.com/"
)
foreach ($m in $mirrors) {
  try {
    Invoke-WebRequest -Uri ($m + $releaseUrlPath) -OutFile $dest -TimeoutSec 120
    if ($LASTEXITCODE -eq 0 -and (Test-Path $dest)) { break }
  } catch { continue }
}
# 2) 非 GitHub 官方站点的包：先试清华/阿里/中科大镜像再试官网
$cn = @("https://mirrors.tuna.tsinghua.edu.cn/", "https://mirrors.aliyun.com/", "https://mirrors.ustc.edu.cn/")
```

**解压与归位**：下载的 zip/tar.gz 解压后，把 `include/`、`lib/`（.lib/.a/.dll）整理到：

```text
D:\tools\cpp-libs\<lib>\<version>\<toolchain>-<arch>\include\...
D:\tools\cpp-libs\<lib>\<version>\<toolchain>-<arch>\lib\...
```

（头文件直接平铺或带子目录都行，缓存里记 include 绝对路径即可。）

**登记到缓存**（必做，含下载来源）：

```powershell
& $py "...\cpp_lib_cache.py" add $lib --toolchain $tc --arch $arch --version <版本> `
    --include "D:\tools\cpp-libs\$lib\<version>\$tc-$arch\include" `
    --lib-path "D:\tools\cpp-libs\$lib\<version>\$tc-$arch\lib" `
    --install-command "镜像: <实际URL>; 解压到: <路径>"
```

登记后 `require $lib` 即通过。窗口活动库集补上该库：`cpp_lib_cache.py active $lib --session <窗口id> --toolchain $tc --arch $arch`。

## 7. 会话窗口语义

- **每个窗口只做一次决策**：工具链/架构探测（§2）、10% 概率核对（§4）、库盘点（§3）都在窗口**首次**调用时做，结果写进缓存 `session.<窗口id>`（toolchain/arch/libs）。
- 窗口内后续编译：直接用活动工具链 + `now-flags` 输出参数，不再探测、不再下载。
- 新窗口（不同 session id）首次调用时才会再次决策。
- 缓存是跨窗口共享的：窗口 A 下载登记过的库，窗口 B 直接 `require` 复用，不重复下载。

## 8. 纪律与常见坑

- 不往系统目录装库；不动 `C:\Program Files`；不改 PATH 全局变量（运行 dll 时临时设）。
- 不在 base/项目外乱建环境；MinGW 和 MSVC 的库**不混用**（ABI 不同），同一库两个工具链各放各的目录。
- 下载必须记录来源（`--install-command` 里写清镜像 URL），否则缓存不可追溯。
- `analyze_includes.py` 的映射表覆盖常见库；没覆盖到的头会出现在 `unknown`，手动确认后按真实库名登记。
- 探测/编译失败要报出原因，不静默换工具链。

## 9. 收尾汇报

任务结束回复用户时说明：用了哪个工具链与架构、涉及哪些外部库、include/lib 路径（或 -I/-L 参数）、缓存是否已更新。
