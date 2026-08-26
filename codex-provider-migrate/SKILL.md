---
name: "codex-provider-migrate"
description: "Fixes Codex old-conversation failures after switching API relay/provider by backing up ~/.codex and rewriting model_provider in session files and state_5.sqlite. Invoke when user switches Codex 中转站/model provider and old chats report 'Model provider not found' or config.toml load errors."
---

# Codex Provider 迁移修复（切换中转站后修复旧对话）

Codex 切换供应商（中转站）后，旧对话仍记录旧的 `model_provider` 名称，打开时报错：
`Codex 无法加载 config.toml ... Model provider 'xxx' not found`。

原因：旧 provider 名存储在三层——`config.toml`（用户已改）、`sessions/**/*.jsonl` 的 session_meta 记录、`state_5.sqlite` 的 `threads` 表（Codex 实际读取的数据库）。只改 config.toml 无效，必须修复后两层。

## 关键约束（必读）

1. **先备份，后修改**。备份未完成前禁止写任何修改。
2. **确认 Codex 未在运行**：`Get-Process | Where-Object { $_.Name -match 'codex' }`。有进程则请用户先关闭，否则数据库会被回写。
3. **TRAE 沙箱限制**：Python/sqlite3 进程直接写 `~/.codex` 会被沙箱拦截，但 PowerShell 可以。因此采用「Python 生成到工作区暂存目录 → PowerShell 写回」模式。
4. **删除过期的 `state_5.sqlite-wal` / `-shm`**：修改主库后必须删除这两个残留文件，否则 Codex 下次启动重放旧 WAL 会还原修复。
5. **不要打印** `auth.json`、`config.toml` 中 token/key 的完整值。

## 工作流

### Step 1：诊断（确认新旧 provider 名）

```powershell
python .trae/skills/codex-provider-migrate/scripts/scan.py
```

- 读取 `~/.codex/config.toml` 顶部的 `model_provider`，这是**新** provider 名。
- 脚本输出 session 文件与 `threads` 表中所有 provider 取值及数量。
- 除新 provider 外的取值都是**旧** provider（如 `OpenAI`、`openai`、`deepseek` 等），向用户复述确认。
- 同时检查 `threads` 表的 `model` 列：若存在新中转站不提供的旧模型名（如 `deepseek-v4-flash`），记录数量，迁移时用 `--fix-model` 归一化。

### Step 2：备份

```powershell
$bak = "$env:USERPROFILE\.codex-backup-<YYYYMMDD>"
New-Item -ItemType Directory -Force -Path $bak | Out-Null
Copy-Item "$env:USERPROFILE\.codex\config.toml" $bak
Copy-Item "$env:USERPROFILE\.codex\auth.json" $bak
Copy-Item "$env:USERPROFILE\.codex\session_index.jsonl" $bak
Copy-Item "$env:USERPROFILE\.codex\state_5.sqlite" $bak
Copy-Item "$env:USERPROFILE\.codex\state_5.sqlite-wal" $bak -ErrorAction SilentlyContinue
Copy-Item "$env:USERPROFILE\.codex\state_5.sqlite-shm" $bak -ErrorAction SilentlyContinue
Copy-Item "$env:USERPROFILE\.codex\sqlite" "$bak\sqlite" -Recurse
Copy-Item "$env:USERPROFILE\.codex\sessions" "$bak\sessions" -Recurse
Copy-Item "$env:USERPROFILE\.codex\archived_sessions" "$bak\archived_sessions" -Recurse
```

若当天已存在同名备份目录，先询问用户是覆盖还是加后缀。备份完成后列出目录内容和总大小向用户确认。

### Step 3：迁移（暂存 + 写回）

```powershell
python .trae/skills/codex-provider-migrate/scripts/migrate.py --new <新provider> --old <旧1> --old <旧2>
# 若存在新中转站不支持的旧模型名，追加：--fix-model <旧模型名>=<新模型名>
```

脚本把修复后的文件写入 `<skill目录>/staging/`（只读 `~/.codex`，不直接写）。然后用 PowerShell 写回：

```powershell
$stage = "<工作区绝对路径>\.trae\skills\codex-provider-migrate\staging"
$codex = "$env:USERPROFILE\.codex"
Get-ChildItem $stage -Recurse -File | ForEach-Object {
  $rel = $_.FullName.Substring($stage.Length + 1)
  Copy-Item -LiteralPath $_.FullName -Destination (Join-Path $codex $rel) -Force
}
Remove-Item "$codex\state_5.sqlite-wal" -Force -ErrorAction SilentlyContinue
Remove-Item "$codex\state_5.sqlite-shm" -Force -ErrorAction SilentlyContinue
```

最后清理暂存目录：`Remove-Item $stage -Recurse -Force`。

### Step 4：验证

```powershell
python .trae/skills/codex-provider-migrate/scripts/scan.py
```

通过标准：
- session_meta 的 provider 统计只剩新 provider 名
- `threads` 表 GROUP BY 结果只剩新 provider 名
- `~/.codex` 下无 `state_5.sqlite-wal` / `-shm` 残留

### Step 5：收尾

- 让用户重启 Codex，打开几个旧对话确认可以正常继续。
- 告知备份位置与还原方法：把备份目录中的文件按原路径复制回去即可。
- 若 `config.toml` 的 `[shell_environment_policy.set]` 里还残留旧中转站的环境变量（如 ANTHROPIC_BASE_URL），仅提醒用户，未经确认不要删除。

## 回滚

出现异常时，将 `.codex-backup-<日期>` 中的 `config.toml`、`auth.json`、`sessions`、`archived_sessions`、`state_5.sqlite*`、`sqlite` 用 PowerShell 复制回 `~/.codex` 覆盖即可。

## 脚本说明

- `scripts/scan.py`：只读诊断。统计 session 文件 session_meta 与 `state_5.sqlite` threads 表中的 model_provider/model 分布。
- `scripts/migrate.py`：生成修复后的文件到 `staging/`。修改所有 session_meta 中的旧 provider、UPDATE threads 表、可选归一化模型名。不直接写 `~/.codex`。
