# Deployment Scripts

Infrastructure as Code (IaC) deployment scripts for Citrus-Orchestrator platform.

## Overview

These PowerShell scripts automate the deployment of the entire observability stack:

```
Infrastructure Layer
├── Prometheus + Grafana (monitoring)
└── Jaeger (distributed tracing)

Application Layer
└── OpenTelemetry Demo (17 microservices)
```

## Scripts

| Script | Purpose | When to Use |
|--------|---------|-------------|
| `0-deploy-all.ps1` | One-click full deployment | First time setup or complete redeployment |
| `1-deploy-infrastructure.ps1` | Deploy monitoring stack only | Setting up base platform |
| `2-deploy-application.ps1` | Deploy application only | Adding/updating applications |

## Usage

### Option 1: One-Click Deployment (Recommended)

```powershell
# Deploy everything
.\scripts\deployment\0-deploy-all.ps1

# Deploy to custom namespace
.\scripts\deployment\0-deploy-all.ps1 -Namespace production
```

**What it does:**
1. Creates Kubernetes namespace
2. Adds Helm repositories
3. Deploys Prometheus + Grafana
4. Deploys Jaeger
5. Deploys OpenTelemetry Demo
6. Waits for services to be ready

**Time:** ~5-8 minutes

### Option 2: Step-by-Step Deployment

```powershell
# Step 1: Deploy infrastructure
.\scripts\deployment\1-deploy-infrastructure.ps1

# Step 2: Deploy application
.\scripts\deployment\2-deploy-application.ps1
```

**When to use:**
- You only need infrastructure (monitoring + tracing)
- You want to deploy multiple applications to same infra
- You're troubleshooting a specific layer

### Option 3: Manual Helm Commands

See [DEPLOYMENT.md](../../docs/DEPLOYMENT.md) for manual Helm commands.

## Prerequisites

- Kubernetes cluster (Kind, Minikube, or cloud provider)
- `kubectl` configured and working
- `helm` 3.12+ installed
- PowerShell 5.1+ (Windows) or PowerShell Core (Linux/Mac)

**Verify prerequisites:**

```powershell
# Check kubectl
kubectl cluster-info

# Check Helm
helm version

# Check PowerShell
$PSVersionTable.PSVersion
```

## Parameters

### Common Parameters

| Parameter | Default | Description |
|-----------|---------|-------------|
| `-Namespace` | `citrus` | Kubernetes namespace to deploy to |

### Application-Specific Parameters

`2-deploy-application.ps1` supports:

| Parameter | Default | Description |
|-----------|---------|-------------|
| `-ReleaseName` | `otel-demo` | Helm release name |
| `-Version` | `0.40.9` | OpenTelemetry Demo chart version |

**Example:**

```powershell
.\scripts\deployment\2-deploy-application.ps1 `
    -Namespace production `
    -ReleaseName my-app `
    -Version 0.40.9
```

## What Gets Deployed

### Infrastructure Layer (`1-deploy-infrastructure.ps1`)

**Monitoring Stack:**
- Prometheus (metrics storage)
- Grafana (visualization)
- Prometheus Operator
- kube-state-metrics
- node-exporter

**Tracing Stack:**
- Jaeger All-In-One (collector + query + UI)

**Helm Releases:**
- `monitoring` - kube-prometheus-stack
- `jaeger` - jaeger

### Application Layer (`2-deploy-application.ps1`)

**Services:**
- Frontend (TypeScript)
- Cart (.NET)
- Checkout (Go)
- Payment (JavaScript)
- Shipping (Rust)
- Email (Ruby)
- Currency (C++)
- Product Catalog (Go)
- Recommendation (Python)
- Ad (Java)
- Quote (PHP)
- Fraud Detection (Kotlin)
- Accounting (.NET)
- Load Generator (Python)

**Dependencies:**
- OpenTelemetry Collector
- Kafka (message queue)
- Valkey/Redis (cache)
- PostgreSQL (database)

**Helm Releases:**
- `otel-demo` - opentelemetry-demo

## Access Services

After deployment completes, access services using port-forward:

```powershell
# Web Store
kubectl port-forward -n citrus svc/otel-demo-frontendproxy 8080:8080
# Visit: http://localhost:8080

# Grafana
kubectl port-forward -n citrus svc/monitoring-grafana 3000:80
# Visit: http://localhost:3000 (admin/prom-operator)

# Jaeger
kubectl port-forward -n citrus svc/jaeger 16686:16686
# Visit: http://localhost:16686

# Prometheus
kubectl port-forward -n citrus svc/monitoring-kube-prometheus-prometheus 9090:9090
# Visit: http://localhost:9090
```

## Troubleshooting

### Script Execution Policy Error

```
.\0-deploy-all.ps1 : File cannot be loaded because running scripts is disabled
```

**Solution:**

```powershell
Set-ExecutionPolicy -ExecutionPolicy RemoteSigned -Scope CurrentUser
```

### Helm Repository Errors

```
Error: repo "prometheus-community" not found
```

**Solution:** Scripts automatically add repositories, but if needed:

```powershell
helm repo add prometheus-community https://prometheus-community.github.io/helm-charts
helm repo update
```

### Pods Not Starting

```powershell
# Check pod status
kubectl get pods -n citrus

# Describe problematic pod
kubectl describe pod <pod-name> -n citrus

# Check logs
kubectl logs <pod-name> -n citrus
```

**Common issues:**
- Image pull errors: Wait for retry (automatic)
- Resource limits: Increase in values.yaml
- Dependency not ready: Wait longer

### Infrastructure Already Exists

Scripts detect existing releases and upgrade instead of failing.

**Manual cleanup:**

```powershell
# Remove application
helm uninstall otel-demo -n citrus

# Remove infrastructure
helm uninstall monitoring -n citrus
helm uninstall jaeger -n citrus

# Remove namespace
kubectl delete namespace citrus
```

## Architecture Benefits

**Infrastructure as Code:**
- All deployment logic in version control
- Reproducible deployments
- Easy rollback with Git
- No manual steps to forget

**Separation of Concerns:**
- Infrastructure can be upgraded independently
- Multiple applications can share monitoring
- Clear ownership boundaries

**Production-Ready:**
- Cross-namespace monitoring
- Centralized telemetry collection
- Standardized observability stack

## Next Steps

1. **Agent demo:** [DEMO.md](../../docs/DEMO.md) — chaos kill, RCA, optional Skills
2. **Explore Dashboards:** Visit Grafana and import `deploy/grafana/citrus-agent-dashboard.json`
3. **Configure Alerts:** [WEBHOOK.md](../../docs/WEBHOOK.md)
4. **Labeled eval:** [EVAL.md](../../docs/EVAL.md)

## Related Documentation

- [Main Deployment Guide](../../docs/DEPLOYMENT.md)
- [Monitoring Stack Configuration](../../deploy/helm/monitoring-stack-values.yaml)
- [Jaeger Configuration](../../deploy/helm/jaeger-values.yaml)
- [Application Configuration](../../deploy/helm/otel-demo-values.yaml)
