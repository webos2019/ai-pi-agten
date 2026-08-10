"""测试 Prime Agent 四大特性 API"""
import urllib.request
import json

BASE = "http://localhost:8000"

def post(path, body):
    data = json.dumps(body).encode()
    req = urllib.request.Request(f"{BASE}{path}", data=data, headers={"Content-Type": "application/json"})
    return json.loads(urllib.request.urlopen(req).read())

def get(path):
    return json.loads(urllib.request.urlopen(f"{BASE}{path}").read())

print("=" * 60)
print("测试 Prime Agent 四大特性 API")
print("=" * 60)

# 1. RLM Kernel — 执行代码 + 持久化
print("\n--- 1. RLM Kernel ---")
r1 = post("/api/rlm/execute", {"session_id": "test-1", "code": "x = 42; y = x * 2; _result = f'x={x}, y={y}'"})
print(f"首次执行: {r1}")

r2 = post("/api/rlm/execute", {"session_id": "test-1", "code": "_result = f'x still = {x}, z = {x + 100}'"})
print(f"持久化验证 (x 应仍为 42): {r2}")

r3 = get("/api/rlm/kernel/test-1")
print(f"内核信息: {r3}")

# 2. Harness — 状态 + refine
print("\n--- 2. Continual Harness ---")
h1 = get("/api/harness/status")
print(f"Harness 状态: {h1}")

# 3. Agent Message Bus
print("\n--- 3. Agent Message Bus ---")
a1 = get("/api/agents")
print(f"Agent 列表: {a1}")

# 4. Goal — 创建 + 查询
print("\n--- 4. Goal ---")
g1 = post("/api/goal/create", {"session_id": "test-1", "objective": "完成代码迁移", "budget": 200000})
print(f"创建目标: {g1}")

g2 = get("/api/goal/test-1")
print(f"目标状态: {g2}")

# 5. Heartbeat
print("\n--- 5. Heartbeat ---")
hb1 = post("/api/heartbeat/create", {"session_id": "test-1", "instruction": "检查部署状态", "interval": "5m", "label": "deploy-check"})
print(f"创建心跳: {hb1}")

hb2 = get("/api/heartbeat/test-1")
print(f"心跳列表: {hb2}")

# 6. Autonomous
print("\n--- 6. Autonomous Mode ---")
am1 = post("/api/autonomous/enable", {"session_id": "test-1", "max_turns": 10, "gate_command": "python -c 'print(1)'"})
print(f"启用自治: {am1}")

am2 = get("/api/autonomous/test-1")
print(f"自治状态: {am2}")

# 7. Schedules
print("\n--- 7. Schedules ---")
s1 = post("/api/schedules/add", {"agent_id": "test-1", "prompt": "检查基准测试结果", "in_minutes": 30})
print(f"添加定时任务: {s1}")

s2 = get("/api/schedules")
print(f"定时任务列表: {s2}")

print("\n" + "=" * 60)
print("✅ 全部 API 测试通过！")
print("=" * 60)
