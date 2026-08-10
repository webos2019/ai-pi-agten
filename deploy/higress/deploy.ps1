# Pi Agent Higress 网关部署脚本
# 用法:
#   .\deploy.ps1 -Mode local    # Docker Compose 本地模式
#   .\deploy.ps1 -Mode k8s      # Kubernetes 模式

param(
    [Parameter(Position=0)]
    [ValidateSet("local", "k8s", "stop", "clean")]
    [string]$Mode = "local"
)

$ErrorActionPreference = "Stop"
$ScriptDir = Split-Path -Parent $MyInvocation.MyCommand.Definition

Write-Host "═══════════════════════════════════════════════════" -ForegroundColor Cyan
Write-Host "  Pi Agent Higress 网关部署" -ForegroundColor Cyan
Write-Host "═══════════════════════════════════════════════════" -ForegroundColor Cyan

switch ($Mode) {
    "local" {
        Write-Host "`n[1/4] 检查 .env 文件..." -ForegroundColor Yellow
        $envFile = Join-Path $ScriptDir ".env"
        if (-not (Test-Path $envFile)) {
            Write-Host "  .env 不存在，从模板创建..." -ForegroundColor Yellow
            Copy-Item (Join-Path $ScriptDir ".env.example") $envFile
            Write-Host "  ⚠️  请编辑 $envFile 填入真实 API Key 后重新运行" -ForegroundColor Red
            Write-Host "     notepad $envFile" -ForegroundColor Gray
            exit 1
        }
        Write-Host "  ✓ .env 已存在" -ForegroundColor Green

        Write-Host "`n[2/4] 检查 Docker 环境..." -ForegroundColor Yellow
        $dockerOk = docker info 2>$null
        if ($LASTEXITCODE -ne 0) {
            Write-Host "  ❌ Docker 未运行，请先启动 Docker Desktop" -ForegroundColor Red
            exit 1
        }
        Write-Host "  ✓ Docker 正常" -ForegroundColor Green

        Write-Host "`n[3/4] 构建 Pi Agent 镜像..." -ForegroundColor Yellow
        docker compose -f (Join-Path $ScriptDir "docker-compose.yaml") build pi-agent
        if ($LASTEXITCODE -ne 0) {
            Write-Host "  ❌ 镜像构建失败" -ForegroundColor Red
            exit 1
        }
        Write-Host "  ✓ 镜像构建完成" -ForegroundColor Green

        Write-Host "`n[4/4] 启动服务..." -ForegroundColor Yellow
        docker compose -f (Join-Path $ScriptDir "docker-compose.yaml") up -d
        if ($LASTEXITCODE -ne 0) {
            Write-Host "  ❌ 启动失败" -ForegroundColor Red
            exit 1
        }

        Write-Host "`n═══════════════════════════════════════════════════" -ForegroundColor Green
        Write-Host "  ✅ 部署完成！" -ForegroundColor Green
        Write-Host "═══════════════════════════════════════════════════" -ForegroundColor Green
        Write-Host ""
        Write-Host "  网关地址:  http://localhost:8080" -ForegroundColor Cyan
        Write-Host "  直连地址:  http://localhost:8000" -ForegroundColor Gray
        Write-Host ""
        Write-Host "  查看日志:  docker compose -f docker-compose.yaml logs -f" -ForegroundColor Gray
        Write-Host "  停止服务:  .\deploy.ps1 -Mode stop" -ForegroundColor Gray
    }

    "k8s" {
        Write-Host "`n[1/5] 检查 kubectl..." -ForegroundColor Yellow
        $kubectlOk = kubectl version --client 2>$null
        if ($LASTEXITCODE -ne 0) {
            Write-Host "  ❌ kubectl 未安装" -ForegroundColor Red
            exit 1
        }
        Write-Host "  ✓ kubectl 正常" -ForegroundColor Green

        Write-Host "`n[2/5] 检查 Higress..." -ForegroundColor Yellow
        $higressNs = kubectl get namespace higress-system 2>$null
        if (-not $higressNs) {
            Write-Host "  Higress 未安装，正在安装..." -ForegroundColor Yellow
            helm repo add higress https://higress.io/helm-charts
            helm install higress higress/higress -n higress-system --create-namespace
        }
        Write-Host "  ✓ Higress 已就绪" -ForegroundColor Green

        Write-Host "`n[3/5] 部署 Pi Agent 应用..." -ForegroundColor Yellow
        kubectl apply -f (Join-Path $ScriptDir "00-pi-agent-deployment.yaml")
        Write-Host "  ✓ Pi Agent Deployment 已创建" -ForegroundColor Green

        Write-Host "`n[4/5] 部署网关配置..." -ForegroundColor Yellow
        kubectl apply -f (Join-Path $ScriptDir "01-gateway.yaml")
        kubectl apply -f (Join-Path $ScriptDir "02-providers.yaml")
        kubectl apply -f (Join-Path $ScriptDir "03-dns.yaml")
        kubectl apply -f (Join-Path $ScriptDir "04-routes.yaml")
        Write-Host "  ✓ Gateway / Provider / DNS / Route 已创建" -ForegroundColor Green

        Write-Host "`n[5/5] 等待 Pod 就绪..." -ForegroundColor Yellow
        kubectl wait --for=condition=ready pod -l app=pi-agent -n higress-system --timeout=120s

        Write-Host "`n═══════════════════════════════════════════════════" -ForegroundColor Green
        Write-Host "  ✅ K8s 部署完成！" -ForegroundColor Green
        Write-Host "═══════════════════════════════════════════════════" -ForegroundColor Green
        Write-Host ""
        Write-Host "  获取网关 IP:" -ForegroundColor Cyan
        Write-Host "    kubectl get svc -n higress-system | findstr gateway" -ForegroundColor Gray
        Write-Host ""
        Write-Host "  添加本地 hosts:" -ForegroundColor Cyan
        Write-Host "    <GATEWAY_IP> pi-agent.local" -ForegroundColor Gray
        Write-Host ""
        Write-Host "  访问: http://pi-agent.local" -ForegroundColor Cyan
    }

    "stop" {
        Write-Host "`n停止本地服务..." -ForegroundColor Yellow
        docker compose -f (Join-Path $ScriptDir "docker-compose.yaml") down
        Write-Host "  ✓ 已停止" -ForegroundColor Green
    }

    "clean" {
        Write-Host "`n清理所有资源..." -ForegroundColor Yellow
        docker compose -f (Join-Path $ScriptDir "docker-compose.yaml") down -v
        Write-Host "  ✓ 已清理（含数据卷）" -ForegroundColor Green
    }
}
