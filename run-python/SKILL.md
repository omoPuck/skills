---
name: run-python
description: "当任务需要运行本机 Python 代码/脚本/测试，或需要挑选、创建 conda 环境、安装 Python 依赖时，读取本 skill 并遵循其规范：未指定环境则扫描 conda 环境挑选可直接运行该代码的环境，没有则新建环境并经镜像源安装依赖（默认不用 base 环境），并维护本地环境缓存（新建环境首次强制写入安装清单，其余约 10% 概率同步包变化）。"
whenToUse: "需要运行 Python 代码、执行 .py 脚本、跑 pytest 或任何需要选择 conda 环境 / 安装 Python 包的任务；用户显式调用 /run-python 或 $run-python 时也启用。"
---

# run-python — 本机 conda/Python 环境运行规范

只要任务需要"在本机运行 Python"，就先按本 skill 执行。不要随手用裸 `python` / `pip` 或随意挑一个环境；也不要默认用 base 跑用户代码。

## 0. 硬性规则（优先级最高）

1. **默认不用 base 环境运行用户代码**。base 只允许用来跑本 skill 的辅助脚本（env_cache.py / analyze_imports.py）。除非用户明确点名 base。
2. **用户指定了环境** → 直接用它（先确认该环境存在）。
3. **未指定环境** → 扫描 conda 环境，挑"可以直接运行这段代码"的（依赖探测通过）；**都没有** → 新建环境并经**镜像源**安装依赖。
4. **新建环境第一次使用，必须把安装清单完整写进本地缓存**（100% 强制，不走 10% 概率）。
5. **每个会话窗口只在首次调用 Python 时做一次"环境决策"和一次 10% 概率同步**：决策 = 通过缓存/扫描选定环境（或新建）并记录为**本窗口活动环境**；选定后本窗口内后续调用**直接复用**该环境，不再重新扫描、不再掷概率、不再更新缓存（除非新建环境或手工装了新包，见 §7）。
6. 环境的选择、创建、清单变化都要**记录到本地缓存**（env-cache.json），供后续任务/窗口复用。

## 1. 本 skill 的资源与辅助脚本

本 skill 的 Base directory 由 `<skill_resources>` 给出（本机为 `C:\Users\31913\.dsh\skills\run-python`）。以下相对路径都基于它解析。

- `scripts/env_cache.py` —— 环境缓存管理器（仅标准库、不派生子进程、不联网）。
- `scripts/analyze_imports.py` —— 从源码提取第三方顶层导入：`imports`（代码里 import 的名字，用于探测环境）与 `dist_names`（pip 发行包名，用于安装）。

辅助脚本用 conda 根环境的 python 运行（§2 里定位 `<root-python>`，即 base 的 python.exe；跑辅助脚本不算"用 base 跑用户代码"），例如：

```powershell
& $rootPython "C:\Users\31913\.dsh\skills\run-python\scripts\analyze_imports.py" --file "D:\dsh\work\app.py"
```

缓存文件默认 `C:\Users\31913\.dsh\skills\run-python\env-cache.json`；写不进去时脚本自动回退到 `~/.run-python-cache.json`，并在执行变更命令时打印实际路径。以脚本打印的路径为准，不要另立缓存文件。

## 2. 定位 conda 与根 python

```powershell
$condaCmd = Get-Command conda -ErrorAction SilentlyContinue
$conda = $null
if ($condaCmd) { $conda = $condaCmd.Source }
if (-not $conda) {
  $candidates = @(
    "$env:USERPROFILE\anaconda3\Scripts\conda.exe",
    "$env:USERPROFILE\miniconda3\Scripts\conda.exe",
    "$env:USERPROFILE\miniforge3\Scripts\conda.exe",
    "C:\ProgramData\anaconda3\Scripts\conda.exe",
    "C:\ProgramData\miniconda3\Scripts\conda.exe"
  )
  $conda = $candidates | Where-Object { Test-Path $_ } | Select-Object -First 1
}
if (-not $conda) { 停止并告知用户找不到 conda，询问如何处理；不要擅自用系统 python 跑用户代码。 }

$envJson  = & $conda env list --json | ConvertFrom-Json   # envs: 路径数组，第 0 项是 conda 根
$envPaths = @($envJson.envs)
$rootDir  = $envPaths[0]
$rootPython = Join-Path $rootDir "python.exe"
```

- 非 base 环境 `<name>` 的 python：`Join-Path (Join-Path $rootDir "envs") "<name>\python.exe"`，或从 `$envPaths` 取对应路径拼 `python.exe`。
- 环境名 = `Split-Path -Leaf $envPath`；路径等于 `$rootDir` 的就是 base。
- 执行前把 `<conda>`、`<root-python>`、`<skill-dir>` 等占位符全部解析成本机真实绝对路径。

## 3. 提取代码依赖

先把要跑的代码落盘（例如工作区 `work\app.py`，可含测试），然后：

```powershell
$req = & $rootPython "C:\Users\31913\.dsh\skills\run-python\scripts\analyze_imports.py" --file "D:\dsh\work\app.py" | ConvertFrom-Json
$imports   = @($req.imports)      # 用于探测：代码里 import 的名字
$distNames = @($req.dist_names)   # 用于安装：pip 发行包名
```

没有文件时可用 `--code "<代码>"` 或管道 stdin；动态生成的代码同样先落盘或 `--code`。

## 4. 选中环境 —— 会话窗口内只做一次

本会话窗口（当前 GUI 会话，用窗口会话 id 区分）**首次**要跑 Python 时，走一次完整决策并把它记为"本窗口活动环境"；之后窗口内直接复用，不再决策。窗口 id 用会话标识（session id）；不指定时用 `default`。注意：`--session` 传 ASCII 安全的 id（如 uuid/数字/短横线命名），不要把中文直接塞进命令行参数（pwsh 原生命令传参中文会乱码）。

**第一步：本窗口是否已有活动环境？** 有 → 直接用，进入 §6，本节结束。

```powershell
$active = (& $rootPython "...\env_cache.py" active --session <窗口id>).Trim()
if ($active -ne "NONE" -and $active) {
  # 用缓存里该环境的 python 直接跑（§6）；无需重新扫描、掷概率。
  进入 §6
}
```

**第二步：用户是否指定了环境？** 指定 → 确认存在后 `active <env> --session <窗口id>` 记录，进入 §6。

**第三步：先查缓存命中**（快路径，仍以探测为准）：

```powershell
$cached = & $rootPython "...\env_cache.py" match --requires $imports
```

对返回的每个环境名：`env_cache.py get <env>` 拿 path，用其 python.exe 做 4c 探测；通过即选用 `active <name> --session <窗口id>`，进入 §6。

**第四步：未命中 → 逐个扫描 conda 环境**（跳到 §4b）。

**4b. 逐个扫描 conda 环境**（跳过 base，除非用户明确允许；按 `$envPaths` 顺序，路径等于 `$rootDir` 的跳过）。

**4c. 探测命令**（对每个候选环境一次）：先写一个探测脚本到工作区（`-c` 内联代码的引号会被 pwsh 原生命令破坏，故用文件），包名逐个传入 argv。

```powershell
# 探测脚本只需写一次，放到工作区 work\ 或 $env:TEMP
$probeFile = Join-Path $env:TEMP "runpy-probe.py"
@'
import importlib, json, sys
out = {}
for p in sys.argv[1:]:
    p = p.lstrip("-")
    try:
        importlib.import_module(p)
        out[p] = True
    except Exception:
        out[p] = False
print(json.dumps(out))
'@ | Set-Content -Encoding utf8 $probeFile

# 对每个候选环境：
$envPy = Join-Path $envPath "python.exe"   # base 的 python 是 $rootPython
if (-not (Test-Path $envPy)) { continue }  # 环境损坏/缺失 → 视为不满足
$raw = & $envPy $probeFile $imports 2>$null
if ($LASTEXITCODE -ne 0 -or -not $raw) { continue }
$probeResult = (($raw -join "`n") | ConvertFrom-Json)
$allOk = $probeResult.PSObject.Properties.Value -notcontains $false
if ($allOk) { 选中 $envPath; break }
```

第一个全部 import 成功的环境即命中。命中后：

```powershell
& $rootPython "...\env_cache.py" touch <env>
& $rootPython "...\env_cache.py" active <env> --session <窗口id>
```

进入 §6。

## 5. 没有可用环境 → 新建（镜像安装）

```powershell
$envName = (& $rootPython "C:\Users\31913\.dsh\skills\run-python\scripts\env_cache.py" name-for --requires $imports).Trim()
# 输出形如 runpy-3f2a9c0b：按依赖集确定性命名，同一依赖集下次直接复用
```

- 缓存里已有同名环境且仍在（探测通过）→ 直接复用（`get` 拿 path，4c 探测）。
- 创建（可能耗时，用 `run_in_background: true` 后台执行，`job_output` 收结果，不要重复发起）：

```powershell
& $conda create -n $envName python=3.12 -y
```

  代码/用户要求特定 Python 版本时用对应版本；默认 3.12。创建后新环境 python 为 `Join-Path $rootDir "envs\$envName\python.exe"`（记为 `$newEnvPy`）。

- 安装（**必须走镜像**，Tsinghua 优先，失败依次换 Aliyun、USTC）：

```powershell
& $newEnvPy -m pip install -i https://pypi.tuna.tsinghua.edu.cn/simple $distNames
# 失败 → -i https://mirrors.aliyun.com/pypi/simple/
# 再失败 → -i https://mirrors.ustc.edu.cn/pypi/simple/
```

  个别包 import 名与发行名不同导致安装失败时，按错误信息装正确的发行包（analyze_imports.py 的 dist_names 已尽量映射）。

- **首次强制记录安装清单**（必做，不掷概率）：

```powershell
$freezeFile = Join-Path $env:TEMP "runpy-freeze-$envName.txt"
& $newEnvPy -m pip list --format=freeze | Out-File -Encoding utf8 $freezeFile
& $rootPython "C:\Users\31913\.dsh\skills\run-python\scripts\env_cache.py" add $envName `
    --python <新环境 python 版本> --path (Split-Path $newEnvPy) `
    --created-for "<这个环境是为哪个任务/哪段代码建的>" `
    --install-command "<上面实际执行的 conda create + pip install 完整命令>" `
    --from-file $freezeFile
```

  确认 `env_cache.py get $envName` 输出里有完整 `packages` 清单，并把它记为本窗口活动环境：

```powershell
& $rootPython "C:\Users\31913\.dsh\skills\run-python\scripts\env_cache.py" active $envName --session <窗口id>
```

  最终回复用户时也要写明：新建了哪个环境、装了哪些包（完整清单）、用了哪个镜像。

## 6. 运行代码

本窗口已有活动环境（§4 记录过）时，直接用它，**不要**再扫描/选择/掷概率：

```powershell
$active = (& $rootPython "...\env_cache.py" active --session <窗口id>).Trim()
# 从缓存拿 active 环境的 path：env_cache.py get $active
& $envPy "D:\dsh\work\app.py" [参数]          # 脚本
& $envPy -m pytest ...                        # 测试
& $envPy -c "<单行代码>"                       # 内联
```

优先用环境自己的 python.exe 直接执行；`conda run -n <env> python ...` 仅作兜底。

## 7. 缓存同步——本会话窗口只在首次调用 Python 时触发

- **每个会话窗口只在首次调用 Python 时**做一次 10% 概率同步，此后窗口内不再触发。首次 = env_cache 还没有该窗口活动环境记录的那一次调用（§4 决策 + 可选同步一起做）。
- **强制（不走概率）**：本次新建的环境（§5 已强制记录）；以及本次手工 `pip install` 装了新包的环境 —— 直接用同步命令。
- **10% 概率（仅在窗口首次调用时掷一次）**：其余首次调用，掷一次：

```powershell
$freezeFile = Join-Path $env:TEMP "runpy-freeze-$envName.txt"
& $envPy -m pip list --format=freeze | Out-File -Encoding utf8 $freezeFile
& $rootPython "...\env_cache.py" maybe-sync $envName --from-file $freezeFile --chance 0.1
```

  `maybe-sync` 内部以 `random.random() < 0.1` 判定：命中才对比缓存，有新增/修改/删除才写入并打印 added/removed/changed；未命中打印 `SKIPPED`，什么都不改。
- 兜底掷骰法（不用 maybe-sync 时）：`if ((Get-Random -Minimum 0 -Maximum 10) -eq 0) { 同步 }`。
- **本窗口内后续 Python 调用直接复用活动环境，不再触发同步、不再更新缓存**。只在另一个会话窗口（不同 session id）首次调用时才会再次掷一次概率并可能重新选环境。

## 8. 纪律与常见坑

- 不在 base 里 `pip install`（用户要求除外）；不修改别人的环境。
- 探测失败/环境损坏 → 明确报错原因，不要静默换一个 python 继续。
- import 名 ≠ 发行包名的常见映射（analyze_imports.py 已内置；探测用 import 名、安装用发行名）：PIL→Pillow、bs4→beautifulsoup4、yaml→PyYAML、cv2→opencv-python、sklearn→scikit-learn、skimage→scikit-image、dotenv→python-dotenv、dateutil→python-dateutil、zmq→pyzmq、nacl→PyNaCl、Crypto→pycryptodome、jwt→PyJWT、flask_cors→Flask-Cors。
- 创建环境/装包耗时 → 用后台任务执行并 `job_output` 收结果，不要重复发起同一安装命令。

## 9. 收尾汇报

任务结束回复用户时说明：本窗口使用了哪个环境（或新建了 `runpy-xxxx` 环境）、依赖安装清单、所用镜像、缓存是否已更新。
