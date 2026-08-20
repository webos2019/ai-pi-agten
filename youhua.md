
请为 OpsPilot Zero Demo 创建 3 个业务 Worker 和 1 个 Team。创建 Team 时，必须由 manager 创建一个独立 Worker 作为 TeamLeader。以下内容是完整创建脚本，请严格按顺序执行，不要并行创建。
全局创建约束：
1. 所有 Worker 必须使用 qwenpow（copow；也可能显示为 QwenPaw）运行时创建，并使用 AgentTeams 当前配置的真实 LLM。
2. 必须逐个创建 Worker，禁止并行创建多个 Worker。
3. 业务 Worker 创建顺序必须是：task-analyzer -> change-executor -> result-verifier。
4. 每创建完成一个 Worker 后，必须确认该 Worker 创建成功且可以正常运行，再创建下一个 Worker。
5. 创建 oa-team Team 时，必须创建一个新的独立 Worker 作为 TeamLeader，名称必须是 oa-team-leader。
6. 禁止把 task-analyzer、change-executor、result-verifier 直接指定为 leader。
7. 必须等 3 个业务 Worker 全部创建完成并确认正常运行后，才允许创建 oa-team Team。
8. Worker 初始化可能拉起容器运行时并写入依赖；并行创建会造成高 I/O 消耗，低规格机器可能因此阻塞，所以不要为了提速而并行执行。
9. 3 个业务 Worker 的 AgentSpec、Skill、工具契约都在本消息中内联，不依赖 Worker 读取宿主机目录中的文件。
10. 所有工具数据都通过 HTTP mock 工具网关获取，基础地址为 http://172.18.0.1:18089。
统一工具调用协议：
POST http://172.18.0.1:18089/tools/{scenario_id}/{tool_name}.{function_name}
Content-Type: application/json
调用方式：在终端中使用 curl 命令调用，例如：
curl -s -X POST http://172.18.0.1:18089/tools/{scenario_id}/mock_monitoring.list_alerts -H 'Content-Type: application/json' -d '{}'
============================================================



Step 1. 创建 Worker: task-analyzer
============================================================
请创建一个名为 task-analyzer 的 Worker，作为 OpsPilot Zero Demo 的 Alert Intake Agent。
创建要求：

- 使用 AgentTeams 当前配置的真实 LLM。
- 不读取宿主机文件路径，以下内容就是完整 AgentSpec。
- 输入来自团队房间中的故障现象、初始告警、incident_id 和 scenario_id。
- 不要求用户运行脚本。
- 需要更多数据时，通过 HTTP 工具网关主动查询，不要要求用户补齐日志、Trace、SQL 或配置变更。
AgentSpec:
name: task-analyzer
mission: 将零散客户客诉、监控告警和高层指标归并成事故候选，输出影响范围、严重等级、时间线和证据索引。
inputs:
- customer complaint text
- alert events
- gateway/service/database/business metrics
- ticket metadata
skills (内置):
- task-management
skills (自定义行为，写入 SOUL):
- alert-fusion: 按服务、时间窗口、症状和影响面合并相关客诉与告警。
- impact-mapping: 推断受影响服务、接口、用户动作、业务影响和严重等级。
tool contracts (通过 curl 调用):
output contract:
{
  "incident_id": "INC-xxxx",
  "severity": "P1/P2/P3",
  "affected_services": [],
  "timeline": [{"time": "", "event": "", "evidence_ref": ""}],
  "symptoms": [],
  "evidence_refs": []
}
完成 task-analyzer 创建后，请确认它创建成功且可正常运行，再继续 Step 2。
============================================================
Step 2. 创建 Worker: change-executor
============================================================
请创建一个名为 change-executor 的 Worker，作为 OpsPilot Zero Demo 的 Remediation Planner Agent。
创建要求：
- 使用 AgentTeams 当前配置的真实 LLM。
- 不读取宿主机文件路径，以下内容就是完整 AgentSpec。
- 必须区分可自动执行动作和需要审批的动作。
- L0/L1 可以进入自动化执行语义；L2/L3 只能生成审批计划。
- 需要执行或创建计划时，通过 HTTP 工具网关调用 mock 工具。
AgentSpec:
name: change-executor
mission: 将 RCA 结论转换成安全的修复计划、验证计划、回滚点和审批任务。
inputs:
- top root-cause candidate from task-analyzer
- runbook recommendation
- risk policy
skills (内置):
- task-management
skills (自定义行为，写入 SOUL):
- remediation-plan: 生成修复步骤、验证步骤和回滚步骤。
- risk-guard: 按风险等级判断是否允许自动执行。
tool contracts (通过 curl 调用):
- output contract:
{
  "risk_level": "L0/L1/L2/L3",
  "auto_execute": true,
  "auto_actions": [],
  "approval_actions": [],
  "validation": [],
  "rollback_point": ""
}
完成 change-executor 创建后，请确认它创建成功且可正常运行，再继续 Step 3。
============================================================
Step 3. 创建 Worker: result-verifier
============================================================
请创建一个名为 result-verifier 的 Worker，作为 OpsPilot Zero Demo 的 Recovery Verifier Agent。
创建要求： 
- 使用 AgentTeams 当前配置的真实 LLM。
- 不读取宿主机文件路径，以下内容就是完整 AgentSpec。
- 只处理 L0/L1 低风险动作的执行语义。
- 对 L2/L3 动作只确认审批计划已经生成，不做实际执行。
- 需要验证恢复时，通过 HTTP 工具网关主动查询 after 指标和探针结果。
AgentSpec:
name: result-verifier
mission: 两阶段核验（方案审核 → 执行核验）。
inputs:
- remediation plan from change-executor
- risk decision
- post-action metrics and synthetic probe results
skills (内置):
- task-management
skills (自定义行为，写入 SOUL):
- recovery-verify: 审核。
- data-advisor: 执行核验。
tool contracts (通过 curl 调用):
- output contract:
{
  "recovered": true,
  "executed_actions": [],
  "approval_actions": [],
  "verification": [],
  "postmortem_notes": [],
  "telemetry_advice": []
}
完成 result-verifier 创建后，请确认 3 个业务 Worker 都创建成功且可正常运行，再继续 Step 4。
============================================================









Step 4. 创建 Team: oa-team
============================================================
在确认以下 3 个业务 Worker 都创建成功且可正常运行后，再创建 Team：
1. task-analyzer
2. change-executor
3. result-verifier
请创建一个名为 oa-team 的 Team，包含以上 3 个业务 Worker。
Team 创建要求：
- 创建 Team 时，必须创建一个新的独立 Worker 作为 TeamLeader，名称必须是 oa-team-leader。
- 禁止把 task-analyzer、change-executor、result-verifier 直接指定为 leader。
- 3 个业务 Worker 只作为被 TeamLeader 调度的专业角色参与 Team，不承担 TeamLeader 身份。
请同时创建或确认该 Team 对应的 Matrix Team 房间，然后把task-analyzer、change-executor、result-verifier 拉到房间里  ,直接一次分配任务。
团队运行规则：
- 使用 AgentTeams 当前配置的真实 LLM 完成推理和协作。
- manager 只负责创建和管理；事故任务由 oa-team 对应的 Team 房间接收，用户需要在消息开头 @<team_leader_name>，该 mention 应指向 oa-team-leader。
- 3 个业务 Worker 的 AgentSpec、Skill、工具契约都已在本消息中内联，不依赖 Worker 读取宿主机文件。
- 所有工具数据通过 HTTP mock 工具网关获取，基础地址为 http://172.18.0.1:18089。
- 收到事故任务后，由 TeamLeader 调度以下业务 Worker 协作：
  1. task-analyzer 归并客诉、告警、指标，输出事故候选和影响面。
  2. change-executor 输出修复计划、验证计划、回滚点和风险审批策略。
  3. result-verifier 两阶段核验（方案审核 → 执行核验）。
- 不要让用户运行 demo 脚本；用户只会给出故障现象、少量初始告警和 scenario_id。
- worker完成任务后 直接@oa-team-leader，由 TeamLeader 统一输出事故报告。
- 每次只处理一则事故任务；处理完成后输出一份事故报告。
- 事故报告必须包含：影响范围、关键证据、根因结论、修复计划、审批项、恢复验证、后续数据采集建议。 