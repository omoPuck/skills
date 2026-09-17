# Skills 技能库

本仓库存放我在本机使用的 AI Agent 技能（Skills）。每个技能目录包含一份 `SKILL.md`（给 AI 的完整指令）和必要的辅助脚本，供 AI 助手在对应场景下加载并执行。

## 技能一览

| 技能 | 用途 | 关键脚本 |
| --- | --- | --- |
| [codex-provider-migrate](./codex-provider-migrate) | Codex 切换 API 中转站/供应商后，修复旧对话打不开（`Model provider not found`）的问题 | `scan.py` / `migrate.py` |
| [run-cpp](./run-cpp) | 本机编译运行 C/C++ 代码的规范：工具链选择（MSVC/MinGW）、外部库统一管理与国内镜像下载 | `analyze_includes.py` / `cpp_lib_cache.py` |
| [run-python](./run-python) | 本机运行 Python 代码的规范：conda 环境挑选/新建、镜像源安装依赖、环境缓存维护 | `analyze_imports.py` / `env_cache.py` |

---

## codex-provider-migrate — Codex 供应商切换修复

切换 Codex 的中转站/API 供应商后，旧对话仍记录旧的 `model_provider` 名称，打开时报错 `Model provider 'xxx' not found`。

**问题根源**：旧 provider 名存储在三层 —— `config.toml`（用户已改）、`sessions/**/*.jsonl` 的 session_meta 记录、`state_5.sqlite` 的 `threads` 表（Codex 实际读取的数据库）。只改 `config.toml` 无效，必须修复后两层。

**工作流程**：

1. **诊断**：`scan.py` 扫描新旧 provider 名与分布
2. **备份**：完整备份 `~/.codex` 到 `.codex-backup-<日期>`（先备份后修改）
3. **迁移**：`migrate.py` 生成修复后的文件到暂存目录，PowerShell 写回 `~/.codex`（规避沙箱限制），并删除过期的 `state_5.sqlite-wal/-shm`
4. **验证**：再次 `scan.py` 确认只剩新 provider 名
5. **收尾**：重启 Codex 验证旧对话可继续，告知备份位置与还原方法

**安全约束**：先备份后修改；确认 Codex 未运行；不打印 token/key；异常时可一键回滚。

## run-cpp — 本机 C/C++ 编译与外部库规范

本机编译 C/C++ 代码、引入外部库时的统一规范，避免裸 `g++`/`cl` 编译、乱装系统目录、重复下载同一库。

**硬性规则**：

- 所有外部库统一安装在 `D:\tools\cpp-libs\<lib>\<version>\<toolchain>-<arch>\`，不改系统目录
- 按本机架构（x64/arm64）与工具链（MSVC/MinGW）选择预编译版本，默认 MinGW
- 预编译包优先走国内镜像（ghproxy 等）下载，失败才回退官方源
- 每个会话窗口只在首次调用时做一次「工具链/架构决策 + 库盘点」，后续直接复用
- 每次下载/登记库都写入本地缓存 `cpp-libs-cache.json`，记录 include/lib 路径与下载来源
- 10% 概率核对缓存与实际库目录的一致性

**辅助脚本**：

- `analyze_includes.py`：扫描源码 `#include`，识别需要的第三方库
- `cpp_lib_cache.py`：库缓存管理器（list / get / have / add / sync / require / now-flags / active）

## run-python — 本机 conda/Python 环境运行规范

本机运行 Python 代码、挑选/创建 conda 环境、安装依赖时的统一规范，避免用裸 `python`/`pip` 或随意挑环境。

**硬性规则**：

- 默认不用 base 环境运行用户代码（base 只跑辅助脚本）
- 未指定环境时扫描 conda 环境，挑「可直接运行这段代码」的；都没有则新建环境并经国内镜像（清华/阿里/中科大）安装依赖
- 新建环境首次使用**强制**把安装清单完整写入本地缓存 `env-cache.json`
- 每个会话窗口只在首次调用 Python 时做一次环境决策 + 10% 概率同步，窗口内直接复用活动环境
- 按依赖集确定性命名新环境（`runpy-<hash>`），同一依赖集下次直接复用

**辅助脚本**：

- `analyze_imports.py`：提取源码第三方导入（import 名 + pip 发行包名映射，如 PIL→Pillow、cv2→opencv-python）
- `env_cache.py`：环境缓存管理器（active / match / get / add / touch / maybe-sync / name-for）

---

## 使用方式

将本仓库克隆到 AI 助手的 skills 目录（例如 DSH 的 `~/.dsh/skills/`、TRAE 的 `.trae/skills/`），助手即可在对应场景自动加载对应技能：

```powershell
git clone https://github.com/omoPuck/skills.git <你的 skills 目录>
```

> 注意：`SKILL.md` 内部引用的辅助脚本路径（如 `C:\Users\31913\.dsh\skills\run-cpp\scripts\...`）是作者本机的绝对路径，克隆到其他机器时需按 `SKILL.md` 中的说明替换为实际路径。
