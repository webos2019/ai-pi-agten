# Pi Agent Higress 网关配置

## 架构总览

```
                         ┌─────────────────────────────────┐
                         │        Higress Gateway          │
                         │     (pi-gateway:80/443)         │
                         └──────────────┬──────────────────┘
                                        │
                    ┌───────────────────┼───────────────────┐
                    │                   │                   │
              /api/* 路由          /assets/* 路由        / 根路由
                    │                   │                   │
                    ▼                   ▼                   ▼
          ┌─────────────────┐ ┌─────────────────┐ ┌─────────────────┐
          │  Pi Agent 后端  │ │  Pi Agent 静态   │ │  Pi Agent 首页  │
          │  (pi-agent:8000)│ │  (pi-agent:8000) │ │  (pi-agent:8000)│
          └─────────────────┘ └─────────────────┘ └─────────────────┘
```

## 四大核心组件

| 组件 | 文件 | 说明 |
|------|------|------|
| **Gateway** | `01-gateway.yaml` | 网关实例，监听 80/443 端口 |
| **Provider (HttpService)** | `02-providers.yaml` | 后端服务注册（Pi Agent FastAPI） |
| **DNS** | `03-dns.yaml` | 域名解析（pi-agent.local → 网关） |
| **Route** | `04-routes.yaml` | 路由规则（/api → 后端，/ → 前端） |

## 部署顺序

```bash
# 0. 确保 Higress 已安装（Helm）
helm repo add higress https://higress.io/helm-charts
helm install higress higress/higress -n higress-system --create-namespace

# 1. 部署 Pi Agent 应用
kubectl apply -f 00-pi-agent-deployment.yaml

# 2. 部署网关配置（按顺序）
kubectl apply -f 01-gateway.yaml
kubectl apply -f 02-providers.yaml
kubectl apply -f 03-dns.yaml
kubectl apply -f 04-routes.yaml

# 3. 验证
kubectl get httproute -n higress-system
kubectl get httpservice -n higress-system
```

## 路由规则

| 路径 | 方法 | 转发目标 | 说明 |
|------|------|----------|------|
| `/api/chat` | POST | pi-agent:8000 | AI 对话（SSE 流式） |
| `/api/chat/steer` | POST | pi-agent:8000 | 流式插话 |
| `/api/conversations` | GET/POST | pi-agent:8000 | 会话管理 |
| `/api/conversations/{id}` | GET/DELETE | pi-agent:8000 | 单会话操作 |
| `/api/stock-analysis` | GET/POST | pi-agent:8000 | 股票分析 |
| `/api/chanlun-analysis` | GET/POST | pi-agent:8000 | 缠论分析 |
| `/api/health` | GET | pi-agent:8000 | 健康检查 |
| `/api/memories` | GET/POST | pi-agent:8000 | 用户记忆 |
| `/api/memories/{key}` | DELETE | pi-agent:8000 | 删除记忆 |
| `/assets/*` | GET | pi-agent:8000 | 前端静态资源 |
| `/` | GET | pi-agent:8000 | 首页 (index.html) |

## 本地开发模式（无需 K8s）

如果只是本地开发，可以直接用 Docker Compose 运行 Higress：

```bash
cd deploy/higress
docker-compose up -d
```

然后访问 `http://localhost:8080` 即可通过网关访问 Pi Agent。
