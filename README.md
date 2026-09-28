# Pi Agent

基于 FastAPI + DeepSeek + React 的 LLM Agent 平台：工具调用、NDJSON 流式输出、受控 Agent 运行时、多会话短期记忆。

## 快速开始

```bash
pip install -r requirements.txt
cp .env.example .env        # 填入 DEEPSEEK_API_KEY
python app.py               # http://localhost:8089
```

前端开发模式（热更新，`/api` 自动代理到 8089）：

```bash
cd frontend && npm install && npm run dev   # http://localhost:3000
```

`start.bat` 可一键启动，但它把项目路径硬编码为 `c:\newtask-pi`（第 11 行的 `cd /d`），换目录需先改这一行。

## 目录结构

```
app.py                FastAPI 主应用与路由
chat_orchestrator.py  Agent 分支、工具循环、错误恢复
chat_service.py       会话归属与上下文构建
chat_session.py       消息格式转换与模型调用
agent_runtime.py      Pi Agent 受控运行时（状态机 + 质量门）
agent_manager.py      多 Agent 管理
tool_registry.py      工具注册
tool_runtime.py       能力驱动的工具编排
skill_registry.py     技能注册（skills/*.md）
thread_state.py       短期记忆与 compaction
validators.py         输入校验与 XSS 过滤
deepseek.py           DeepSeek 异步客户端
tools/                各工具实现（包内自动注册）
stream/               NDJSON 流式协议
skills/               技能定义 Markdown
frontend/             React + Vite + TS 前端
docs/versions/        版本方案文档（Pi Agent 引用）
deploy/higress/       Higress 网关与 K8s 部署编排
submission/           提交材料
```

## 主要接口

| 方法 | 路径 | 说明 |
| --- | --- | --- |
| POST | `/api/chat` | 发送消息，返回 NDJSON 流（`start` / `tool_call` / `tool_result` / `text` / `done`） |
| GET | `/api/conversations` | 会话列表（按 `session_id`） |
| POST | `/api/conversations` | 新建会话 |
| GET | `/api/conversations/{id}` | 会话详情 hydration |
| PATCH | `/api/conversations/{id}` | 重命名 / 切换选中 / touch |
| DELETE | `/api/conversations/{id}` | 删除会话 |
| GET | `/api/health` | 健康检查 |
| GET | `/docs` | OpenAPI 文档 |
## 项目文档 
submission/Pi-Agent-项目文档.md
## Pi Agent 工作流

`/tasklist @docs://versions/v0.x.x-xxx.md` 触发受控运行时：

```
read_resource → plan_extract → plan_readiness → draft_tasklist
→ validate_tasklist（确定性质量门）→ revise_tasklist（仅 blocking 时）
→ revision_eval → final_answer
```

## 配置项

`.env` 关键变量：`DEEPSEEK_API_KEY`、`DEEPSEEK_API_BASE`、`DEEPSEEK_MODEL`、`EMBEDDING_MODE`（默认 `local`，模型 `BAAI/bge-small-zh-v1.5`）、`DB_PATH`（默认 `data/pi_agent.db`，SQLite WAL）。图像生成可选 `SENSETIME_*` / `AGNES_*`。

## License

MIT
