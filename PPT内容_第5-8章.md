# Pi Agent — PPT 内容 第 5-8 章

---

## 第 8 页：第三章 · 多 Agent 协同设计（标题）

**对应评分维度**：多 Agent 协同与自主闭环能力 **25%**

---

## 第 9 页：第三章 · 多 Agent 协同设计（内容）

### Agent 分工

| Agent | 职责 | 工具集 | 特点 |
|-------|------|--------|------|
| **主 Agent（父代理）** | 对话编排、任务分解、结果综合 | 全部 19 个工具 | 接收用户输入，决定直接执行还是委托子代理 |
| **Research 子代理** | 信息检索专家 | web_browse, local-text-read, list_files, get_weather, get_location | 高效收集多源信息，输出结构化摘要 |
| **Analysis 子代理** | 数据分析专家 | calculator, unit_convert, text_transform | 精确计算，展示中间步骤 |
| **Writer 子代理** | 内容创作专家 | 无工具（纯 LLM） | 结构清晰、语言流畅 |
| **Pi Agent** | 受控任务清单生成 | 读取版本方案文件 + 质量门 | 显式入口 + 确定性校验 + 自动修正 |

### 任务拆解示例

```
用户："帮我研究一下京东方，查行情、看技术面、再搜一下相关新闻"

主 Agent 任务拆解:
  ├─ Task 1: 调用 stock_quote 查实时行情 (直接执行)
  ├─ Task 2: 调用 stock_analysis 做技术分析 (直接执行)
  └─ Task 3: 委托 research 子代理搜索新闻
       └─ 子代理独立运行:
            → web_search("京东方 A 新闻")
            → web_fetch(相关页面)
            → 整理为结构化摘要
            → 返回给父代理
  → 父 Agent 综合三个 Task 的结果 → 输出最终回答
```

### 上下文传递与状态流转

```
父 Agent 上下文 (AgentContext)
  ├─ system_prompt: 主 Agent 系统提示
  ├─ messages: 完整对话历史 + 工具结果
  ├─ tools: 当前 Skill 的工具集 + 用户 @ 引用的工具
  └─ model: deepseek-chat

子 Agent 上下文 (独立)
  ├─ system_prompt: 子代理类型专用提示
  ├─ messages: [仅包含委托的任务描述]
  ├─ tools: 子代理类型限定的工具子集
  └─ model: deepseek-chat

状态流转:
  父 Agent → delegate_sub_agent(task, agent_type)
       → 创建子 AgentContext (深度 +1)
       → 运行子 agent_loop (独立 LLM ↔ 工具循环)
       → 子代理事件转发到父流（缩进标记层级）
       → 最终 assistant 消息作为 tool_result 返回
  父 Agent 收到 tool_result → 继续推理
```

### 异常与冲突处理

| 异常类型 | 处理策略 |
|---------|---------|
| **LLM API 超时/错误** | 最多 3 次重试，指数退避（2s/4s/6s），超限后回退到直接回答 |
| **工具执行超时** | asyncio.wait_for 超时（默认 30s，子代理 120s），返回错误让 LLM 重新决策 |
| **LLM 输出截断** | stop_reason=length 时，将不完整的 tool_call 标记为错误，让模型下一轮重新生成（最多 3 次） |
| **子代理嵌套过深** | MAX_SUB_AGENT_DEPTH=2，超过则拒绝执行 |
| **子代理执行异常** | 捕获异常，返回错误信息作为 tool_result，父 Agent 可选择重试或换方案 |

### 流式插话（Steer）安全边界

```
Agent 正在执行工具循环...
    用户发送 Steer: "不要查新闻了，直接看技术面就行"
        ↓
Agent Loop 在下一个 turn 边界检查 SteerQueue
    → 注入 steering 消息到上下文
    → LLM 在下一轮看到用户的新指示
    → 调整执行方向
```

- Steer 只在 turn 边界生效（工具执行完之后），不会中断正在执行的工具
- 流结束后 Steer 请求返回 409
- Steer 消息以 user 角色注入，LLM 自然理解

---

## 第 10 页：第四章 · Skill 工程体系（标题）

**对应评分维度**：Skill 工程体系与生态复用 **25%** ⭐ 必选

---

## 第 11 页：第四章 · Skill 工程体系（内容）

### Skill 清单与任务覆盖

| Skill ID | 名称 | 覆盖任务 | 工具数 |
|----------|------|---------|--------|
| `utility-skill` | 实用工具 | 数学计算、日期查询、单位换算、文本处理 | 5 |
| `reader-skill` | 信息读取 | 本地文件读取、网页浏览、天气查询 | 5 |
| `research-skill` | 研究助手 | 子代理委托、复杂任务分解 | 5 |
| `web-skill` | 网络研究 | 搜索、网页抓取、GitHub 分析、YouTube 分析、PDF 提取 | 6 |
| `stock-skill` | 股票行情 | A 股实时行情、股票搜索、技术分析 | 4 |
| `wechat-skill` | 微信公众号 | 公众号文章提取与分析 | 3 |

### 单个 Skill 规格定义

**示例：stock-skill**

```yaml
# skills/stock-skill.md
---
id: stock-skill
name: 股票行情
description: 查询 A 股实时行情，支持股票代码搜索
tool_names: ["stock_quote", "stock_search", "web_search", "delegate_sub_agent"]
output_policy: detailed-explanation
result_policy: auto
routing_hints: ["股票", "行情", "A股", "涨跌", "股价"]
tags: ["stock", "股票", "行情"]
fallback_policy: direct-answer
---
你是一个 A 股行情查询助手...
```

**规格字段说明**：

| 字段 | 类型 | 说明 |
|------|------|------|
| `id` | string | 唯一标识（小写字母+数字+连字符） |
| `tool_names` | list | 该 Skill 可用的工具列表 |
| `output_policy` | enum | 输出风格：concise-utility / detailed-explanation |
| `result_policy` | enum | 结果策略：tool-first / summary-first / auto |
| `routing_hints` | list | 路由提示词（用于自动匹配用户意图到 Skill） |
| `fallback_policy` | enum | 降级策略：direct-answer / skip-capability / retry |

### Skill 失败处理

```
用户请求 → 匹配 Skill → 工具执行失败?
                          ├─ 是 → fallback_policy 决定:
                          │       ├─ direct-answer: 无工具直接回答
                          │       ├─ skip-capability: 跳过该能力
                          │       └─ retry: 重试工具调用
                          └─ 否 → 正常返回结果
```

### Skill 生命周期管理

```
开发: 在 skills/ 目录新建 .md 文件（front-matter + system_prompt）
     ↓
部署: 启动时 discover() 只扫描目录建立索引（不加载内容）
     ↓
运行: 首次 get(skill_id) 时才读文件解析并缓存（progressive disclosure）
     ↓
更新: 修改 .md 文件，重启服务即生效（无需改代码）
     ↓
扩展: 新增工具只需在 tools/ 目录实现 register() 函数，自动注册
```

### 对官方 Skills 的复用

- 工具注册机制兼容 OpenAI Function Calling 格式，可直接接入官方 Skill 工具
- `delegate_sub_agent` 工具支持将任务委托给外部 Agent（包括 AgentTeams Worker）
- Skill 的 front-matter 格式可扩展 `external_tools` 字段对接 MCP 协议

### 复用价值

| 复用层 | 复用物 | 迁移成本 |
|--------|--------|---------|
| **Skill 层** | .md 文件可直接复制到其他项目 | 零代码 |
| **工具层** | 每个 tool 是独立 Python 函数 | 拷贝 + 注册 |
| **Agent Loop** | 钩子化设计，可用于任何 LLM | 移植核心文件 |
| **记忆系统** | ThreadState + UserMemory 独立模块 | 独立部署 |

---

## 第 12 页：第五章 · 工程落地与安全可审计（标题）

**对应评分维度**：工程落地与安全可审计 **20%**

---

## 第 13 页：第五章 · 工程落地与安全可审计（内容）

### 可运行性

| 项目 | 状态 |
|------|------|
| **Web 应用** | 已部署，支持多会话、流式输出、工具调用 |
| **前端** | React + TypeScript，已构建生产版本（static/dist/） |
| **后端** | FastAPI + Uvicorn，支持热重载 |
| **数据库** | DuckDB 嵌入式，零运维 |
| **启动方式** | `python app.py` 即可启动 |

### 运行证据

**股票分析页面**：
- 输入：股票代码 "000725"
- 输出：实时行情 + 7 因子技术分析（MACD/RSI/KDJ/CCI/ROC/Williams/DMI）+ ESG 评分

**子代理委托**：
- 输入："帮我研究一下这篇公众号文章"
- 输出：research 子代理独立运行，前端显示子代理层级和事件

**流式插话**：
- Agent 执行过程中，用户发送 Steer "换个方向"
- 输出：Agent 在下一 turn 调整执行路径

### 可观测与检索链路

| 层级 | 检索方式 |
|------|---------|
| **会话级** | GET /api/conversations/{id}（ThreadState hydration） |
| **长期记忆** | GET /api/memories?session_id=xxx（向量搜索结果） |
| **前端展示** | 工具调用卡片显示、子代理层级展示、Token 用量统计 |
| **日志** | NDJSON 流包含 tool_call / tool_result / sub_agent_start/end 事件 |

### 安全治理机制

| 风险点 | 防护措施 |
|--------|---------|
| **XSS 攻击** | 用户输入通过 `validators.py` 过滤，转义 HTML 实体 |
| **SQL 注入** | DuckDB 参数化查询，不拼接 SQL |
| **工具参数篡改** | 每个工具有独立 execute 函数，参数校验在函数内部 |
| **LLM 幻觉导致错误工具调用** | 最多 3 次重试，超限后回退到直接回答 |
| **子代理无限嵌套** | MAX_SUB_AGENT_DEPTH=2，超限拒绝执行 |
| **工具执行超时** | asyncio.wait_for 超时保护（30s/120s） |
| **API Key 泄露** | .env 文件不提交 Git，服务端验证 |

### 云产品选型

| 组件 | 选择 | 必要性 |
|------|------|--------|
| **LLM API** | DeepSeek (OpenAI 兼容) | 必需（推理核心） |
| **数据库** | DuckDB (嵌入式) | 必需（记忆持久化） |
| **向量存储** | DuckDB (内置 cosine) | 必需（长期记忆） |
| **Web 服务器** | Uvicorn (ASGI) | 必需（服务托管） |
| **前端构建** | Vite | 可选（开发模式可直接用代理） |

边界：不依赖外部向量数据库（如 Pinecone）、不依赖 Redis、不依赖对象存储，保持轻量化。

---

## 第 14 页：第六章 · 开放 / 开源计划（标题）

**对应评分维度**：开放 / 开源贡献 **5%**

---

## 第 15 页：第六章 · 开放 / 开源计划（内容）

### 可复用成果

| 成果 | 描述 | 复用方式 |
|------|------|---------|
| **Agent Loop** | pi.dev 风格双层循环，钩子化设计 | 拷贝 agent_loop.py + agent_context.py |
| **Skill 系统** | 文件化定义，front-matter + system_prompt | 拷贝 skill_registry.py + skills/*.md |
| **记忆系统** | 三层短期记忆 + 向量长期记忆 | 拷贝 thread_state.py + user_memory.py |
| **工具库** | 19 个开箱即用的工具 | 拷贝 tools/ 目录 |
| **子代理框架** | 支持递归委托、事件转发 | 拷贝 sub_agent.py |
| **流式协议** | NDJSON chunk + 生命周期管理 | 拷贝 stream/ 目录 |

### 接口契约与文档示例

**工具接口规范**：

```python
def register():
    tool_registry.register(ChatToolDefinition(
        name="calculator",
        description="数学计算器，支持加减乘除",
        parameters={
            "type": "object",
            "properties": {
                "expression": {"type": "string", "description": "数学表达式"}
            },
            "required": ["expression"]
        },
        execute=execute,  # async def execute(args, context): str
        format_input=lambda args: f"计算: {args.get('expression', '')}",
        result_is_authoritative=False,
        planning_category="calculation",
        decision_weight=0.95,
        keywords=["计算", "加减乘除", "算数"],
    ))
```

**Skill 文件规范**：

```yaml
---
id: my-skill
name: 我的技能
description: 技能描述
tool_names: ["tool1", "tool2"]
output_policy: detailed-explanation
result_policy: auto
---
这里是 system_prompt 内容...
```

### 开源协议

- **协议**：MIT License
- **第三方依赖**：FastAPI, Pydantic, httpx, duckdb, OpenAI SDK（MIT/Apache 2.0）
- **依赖来源**：均为 OSI 认可的开源协议

---

## 第 16 页：第七章 · 落地计划与进展（标题）

**对应**：「当前进展」与整体可行性 **10%**

---

## 第 17 页：第七章 · 落地计划与进展（内容）

### 当前进展

| 模块 | 完成度 | 说明 |
|------|--------|------|
| **Agent Loop** | 100% | 双层循环、钩子化、Steer/Follow-up 支持 |
| **子代理系统** | 100% | 3 种类型、递归深度限制、事件转发 |
| **Skill 系统** | 100% | 7 个 Skill、文件化定义、懒加载 |
| **工具库** | 100% | 19 个工具、自动注册 |
| **短期记忆** | 100% | 三层结构、compaction、DuckDB 持久化 |
| **长期记忆** | 100% | 向量搜索、语义召回、跨会话复用 |
| **Pi Agent** | 100% | /tasklist 入口、7 步状态机、质量门、自动修正 |
| **前端** | 100% | React + TypeScript、多会话、富文本编辑器 |
| **流式输出** | 100% | NDJSON 协议、错误恢复、Token 统计 |

### 里程碑

| 阶段 | 完成时间 | 产出 |
|------|---------|------|
| **MVP** | 已完成 | 基础对话 + 3 个工具 + 单 Agent |
| **多 Agent** | 已完成 | 子代理系统 + 3 种类型 |
| **Skill 系统** | 已完成 | 文件化定义 + 7 个 Skill |
| **记忆系统** | 已完成 | ThreadState + UserMemory |
| **流式插话** | 已完成 | Steer Queue + 前端集成 |
| **股票分析** | 已完成 | stock_analysis 工具 + React 组件 |
| **公众号阅读** | 已完成 | wechat_article 工具 |
| **Web 研究** | 已完成 | web_search + web_fetch + GitHub/YouTube/PDF |

### 落地计划

| 时间 | 目标 | 输出 |
|------|------|------|
| **初赛提交前** | PPT 完善 + Demo 录制 | 初赛作品（PPT + Demo 视频） |
| **决赛前** | 性能优化 + 更多工具 | 30+ 工具、支持更多数据源 |
| **决赛阶段** | 对标 AgentTeams | 复刻官方 Demo 的运维故障排查场景 |
| **长期** | 开源社区 | 发布到 GitHub，贡献文档和教程 |

### 风险控制

| 风险 | 应对 |
|------|------|
| **LLM API 不稳定** | 3 次重试 + 指数退避 + 回退到直接回答 |
| **工具执行超时** | asyncio.wait_for 超时保护 |
| **子代理无限嵌套** | MAX_SUB_AGENT_DEPTH=2 限制 |
| **向量搜索失败** | 降级为空数组，不影响主流程 |
| **前端流式解析异常** | 前端 try-catch 包裹，显示错误提示 |

---

## 第 18 页：第八章 · 团队介绍（标题）

---

## 第 19 页：第八章 · 团队介绍（内容）

### 1. 成员背景

| 成员 | 背景 | 核心技能 |
|------|------|---------|
| **参赛者** | AI 工程师 / 全栈开发者 | Python, FastAPI, React, LLM Agent 系统, 向量数据库 |

### 2. 团队分工

| 角色 | 职责 |
|------|------|
| **架构设计** | Agent Loop、Skill 系统、记忆系统架构 |
| **后端开发** | FastAPI、工具库、子代理、Pi Agent |
| **前端开发** | React + TypeScript、流式解析、富文本编辑器 |
| **测试与优化** | 性能测试、错误处理、文档编写 |

### 3. 团队成果

- 已完成可运行的 LLM Agent 平台
- 实现了 pi.dev 风格的 Agent Loop 双层循环
- 支持多 Agent 协同、流式插话、三层记忆
- 开源代码已托管（请填写 GitHub 链接）

---

## 作品简介（500 字）

Pi Agent 是一个基于多 Agent 协同的智能助手平台，解决个人用户在跨多个信息源（网页、文件、股票、公众号、GitHub）获取和处理信息时面临的工具分散、无法多步推理、缺乏长期记忆等痛点。

项目采用 pi.dev 风格的双层 Agent Loop 架构，构建了"父 Agent + 3 类子 Agent（research/analysis/writer）+ 7 个 Skill + 19 个工具"的协同体系。父 Agent 负责对话编排和任务分解，子代理专注信息检索、数据分析和内容创作。配套三层短期记忆（recent messages + summary + pinned decisions）和向量长期记忆，实现上下文持续感知。

核心创新点包括：① 受控 Agent 运行时（/tasklist 显式入口 + 确定性质量门 + 自动修正）；② 流式插话机制（Steer），用户可在 Agent 执行中途改变方向；③ Skill 文件化懒加载，新增能力只需在 skills/ 目录加一个 .md 文件，无需改代码。

当前已实现可运行的 Web 应用（FastAPI + React），支持多会话、流式输出、工具调用、子代理委托、长期记忆、股票分析页面等功能。Skill 和 Agent Loop 钩子化设计可复用于任何 LLM 应用场景，具有较好的行业可复制性。