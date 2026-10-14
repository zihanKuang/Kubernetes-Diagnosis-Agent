# Install Chaos Mesh into the local cluster (once per cluster)
# Usage: .\infra\chaos\install-chaos-mesh.ps1
# Safe to re-run (helm upgrade --install).

param(
    [string]$Namespace = "chaos-mesh",
    [string]$Version = "2.7.0"
)

$ErrorActionPreference = "Stop"

Write-Host "========================================" -ForegroundColor Cyan
Write-Host "Install Chaos Mesh" -ForegroundColor Cyan
Write-Host "========================================" -ForegroundColor Cyan
Write-Host ""

# Chaos Mesh needs privileged containers for fault injection
Write-Host "[1/4] Adding Chaos Mesh Helm repo" -ForegroundColor Yellow
helm repo add chaos-mesh https://charts.chaos-mesh.org 2>&1 | Out-Null
helm repo update | Out-Null
Write-Host "  Repo ready" -ForegroundColor Green

Write-Host "[2/4] Creating namespace '$Namespace'" -ForegroundColor Yellow
kubectl create namespace $Namespace --dry-run=client -o yaml | kubectl apply -f - | Out-Null
Write-Host "  Namespace ready" -ForegroundColor Green

Write-Host "[3/4] Installing Chaos Mesh v$Version" -ForegroundColor Yellow
Write-Host "  (This may take 1-2 minutes...)" -ForegroundColor Gray

# Docker Desktop / kind typically use containerd or docker runtime.
# chaosDaemon.runtime=containerd works for most local clusters;
# override with -Runtime docker if your nodes use Docker.
helm upgrade --install chaos-mesh chaos-mesh/chaos-mesh `
    --namespace $Namespace `
    --version $Version `
    --set chaosDaemon.runtime=containerd `
    --set chaosDaemon.socketPath=/run/containerd/containerd.sock `
    --set dashboard.securityMode=false `
    --wait `
    --timeout 5m

Write-Host "  Chaos Mesh installed" -ForegroundColor Green

Write-Host "[4/4] Verifying controllers" -ForegroundColor Yellow
kubectl get pods -n $Namespace

Write-Host ""
Write-Host "========================================" -ForegroundColor Green
Write-Host "Chaos Mesh ready" -ForegroundColor Green
Write-Host "========================================" -ForegroundColor Green
Write-Host ""
Write-Host "Next steps:" -ForegroundColor White
Write-Host "  1. Ensure otel-demo is running in citrus namespace" -ForegroundColor Gray
Write-Host "  2. Run: .\infra\chaos\run-demo.ps1" -ForegroundColor Yellow
Write-Host ""
Write-Host "If install fails on Docker Desktop with socket errors, retry with:" -ForegroundColor Gray
Write-Host "  helm upgrade --install chaos-mesh chaos-mesh/chaos-mesh -n chaos-mesh ``" -ForegroundColor Gray
Write-Host "    --set chaosDaemon.runtime=docker ``" -ForegroundColor Gray
Write-Host "    --set chaosDaemon.socketPath=/var/run/docker.sock" -ForegroundColor Gray
