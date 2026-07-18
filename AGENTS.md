# Pi Agent 项目指令

> 本文件由 Pi Agent 服务端在运行时读取，声明 Agent 的默认配置。
> 机制借鉴 [Pi (pi.dev)](https://pi.dev/) 的 AGENTS.md 项目指令加载约定：
> Pi 从 `~/.pi/agent/`、父目录链、当前目录发现 `AGENTS.md` 并注入上下文；
> 本服务为 Web 后端（cwd 固定为项目根），故直接从项目根读取。

## 默认版本方案

当用户仅输入 `/tasklist` 而未显式 `@docs://versions/*.md` 引用时，
自动使用以下版本方案生成 tasklist 草稿（显式引用仍然优先）：

default_version_plan: docs://versions/v0.3.0-thread-state-short-term-memory.md

## 说明

- `default_version_plan` 字段格式：`docs://versions/<文件名>.md`
- 指向的文件必须存在于 `docs/versions/` 目录下，否则自动发现回退为提示用户手动 @ 引用
- 显式 `@docs://versions/xxx.md` 始终优先于本默认值
- 修改本文件后需重启后端服务生效（当前为非 reload 模式）
