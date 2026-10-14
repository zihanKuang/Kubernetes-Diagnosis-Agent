# Chaos + agent demo: inject PodChaos, then diagnose with Agent CLI
# Usage: .\infra\chaos\run-demo.ps1
#
# Flow:
#   1. Snapshot frontend pods
#   2. Apply PodChaos (kill one frontend pod)
#   3. Wait for kill + ReplicaSet recreate
#   4. Print the Agent CLI command (legacy default; Skills are opt-in)

param(
    [string]$Namespace = "citrus",
    [string]$Selector = "app.kubernetes.io/component=frontend",
    [int]$WaitSeconds = 25
)

$ErrorActionPreference = "Stop"
$RepoRoot = Resolve-Path (Join-Path $PSScriptRoot "..\..")
$ChaosManifest = Join-Path $PSScriptRoot "pod-kill-frontend.yaml"

Write-Host "========================================" -ForegroundColor Cyan
Write-Host "Chaos + Agent Diagnosis Demo" -ForegroundColor Cyan
Write-Host "========================================" -ForegroundColor Cyan
Write-Host ""

# Preconditions
Write-Host "[1/5] Checking Chaos Mesh CRD" -ForegroundColor Yellow
$crd = kubectl get crd podchaos.chaos-mesh.org 2>$null
if (-not $crd) {
    Write-Host "  ERROR: Chaos Mesh not installed." -ForegroundColor Red
    Write-Host "  Run: .\infra\chaos\install-chaos-mesh.ps1" -ForegroundColor Yellow
    exit 1
}
Write-Host "  PodChaos CRD found" -ForegroundColor Green

Write-Host "[2/5] Checking target pods ($Selector)" -ForegroundColor Yellow
$before = kubectl get pods -n $Namespace -l $Selector -o wide 2>$null
if (-not $before) {
    Write-Host "  ERROR: No pods match '$Selector' in namespace '$Namespace'." -ForegroundColor Red
    Write-Host "  Deploy the app first: .\scripts\deployment\2-deploy-application.ps1" -ForegroundColor Yellow
    exit 1
}
Write-Host $before
Write-Host ""

Write-Host "[3/5] Injecting PodChaos (pod-kill)" -ForegroundColor Yellow
# Delete previous experiment so re-apply always triggers a fresh kill
kubectl delete -f $ChaosManifest --ignore-not-found 2>$null | Out-Null
Start-Sleep -Seconds 2
kubectl apply -f $ChaosManifest
Write-Host "  Experiment applied: kill-otel-frontend" -ForegroundColor Green

Write-Host "[4/5] Waiting ${WaitSeconds}s for kill + ReplicaSet self-heal..." -ForegroundColor Yellow
$elapsed = 0
$interval = 5
while ($elapsed -lt $WaitSeconds) {
    Start-Sleep -Seconds $interval
    $elapsed += $interval
    $status = kubectl get pods -n $Namespace -l $Selector `
        -o custom-columns=NAME:.metadata.name,READY:.status.containerStatuses[0].ready,RESTARTS:.status.containerStatuses[0].restartCount,STATUS:.status.phase `
        --no-headers 2>$null
    Write-Host "  t=${elapsed}s | $status" -ForegroundColor Gray
}
Write-Host ""

Write-Host "[5/5] Post-chaos pod state" -ForegroundColor Yellow
kubectl get pods -n $Namespace -l $Selector -o wide
Write-Host ""

Write-Host "========================================" -ForegroundColor Green
Write-Host "Chaos injected. Now run the Agent:" -ForegroundColor Green
Write-Host "========================================" -ForegroundColor Green
Write-Host ""
Write-Host "cd $($RepoRoot)\components" -ForegroundColor Gray
Write-Host "python -m agent_cli `"What just happened to the frontend pods in citrus? Give a short RCA: check events, pod status, and validate recovery.`"" -ForegroundColor Yellow
Write-Host ""
Write-Host "Optional Skills routing (default is still legacy):" -ForegroundColor White
Write-Host "  python -m agent_cli --skill-routing auto `"What just happened to the frontend pods in citrus? Short RCA, then validate_recovery.`"" -ForegroundColor Yellow
Write-Host ""
Write-Host "Or interactive:" -ForegroundColor White
Write-Host "  cd components; python -m agent_cli -i" -ForegroundColor Yellow
Write-Host ""
Write-Host "Cleanup experiment when done:" -ForegroundColor White
Write-Host "  kubectl delete -f infra/chaos/pod-kill-frontend.yaml" -ForegroundColor Gray
Write-Host ""
