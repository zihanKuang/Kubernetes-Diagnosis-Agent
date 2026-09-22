# Deployment Guide

Complete deployment guide for Citrus-Orchestrator monitoring platform with OpenTelemetry Demo.

## Architecture Overview

```
┌─────────────────────────────────────────────┐
│  Infrastructure Layer (Base Platform)       │
├─────────────────────────────────────────────┤
│  • Prometheus + Grafana (Monitoring)        │
│  • Jaeger (Distributed Tracing)             │
└─────────────────────────────────────────────┘
                    ↑
                    │ OTLP Protocol
                    │
┌─────────────────────────────────────────────┐
│  Application Layer (Microservices)          │
├─────────────────────────────────────────────┤
│  • OpenTelemetry Demo (~17 services)        │
│  • OpenTelemetry Collector (Data Router)    │
│  • Kafka, Valkey, PostgreSQL (Dependencies) │
└─────────────────────────────────────────────┘
```

## Prerequisites

- Kubernetes cluster (Kind, Minikube, or cloud provider)
- kubectl installed and configured
- Helm 3.12+ installed
- PowerShell (for Windows) or Bash (for Linux/Mac)

## Quick Start

### Option 1: Automated Deployment (Recommended)

```powershell
# Step 1: Deploy infrastructure layer
.\scripts\deployment\1-deploy-infrastructure.ps1

# Step 2: Deploy application layer
.\scripts\deployment\2-deploy-application.ps1
```

### Option 2: Manual Deployment

Follow the detailed steps below.

---

## Detailed Deployment Steps

### Step 1: Create Namespace

```bash
kubectl create namespace citrus
```

### Step 2: Add Helm Repositories

```bash
# Prometheus stack
helm repo add prometheus-community https://prometheus-community.github.io/helm-charts

# Jaeger
helm repo add jaegertracing https://jaegertracing.github.io/helm-charts

# OpenTelemetry Demo
helm repo add open-telemetry https://open-telemetry.github.io/opentelemetry-helm-charts

# Update repositories
helm repo update
```

### Step 3: Deploy Infrastructure Layer

#### 3.1 Deploy Prometheus + Grafana

```bash
helm install monitoring prometheus-community/kube-prometheus-stack \
  -n citrus \
  --values deploy/helm/monitoring-stack-values.yaml
```

**What this deploys:**
- Prometheus (metrics storage and querying)
- Grafana (visualization dashboards)
- Prometheus Operator (manages Prometheus instances)
- kube-state-metrics (cluster-level metrics)
- node-exporter (node-level metrics)

**Key configuration:**
- Disabled admission webhooks for Kind/AKS compatibility
- Cross-namespace monitoring enabled (`serviceMonitorNamespaceSelector: {}`)
- Grafana password: `prom-operator`

#### 3.2 Deploy Jaeger

```bash
helm install jaeger jaegertracing/jaeger \
  -n citrus \
  --values deploy/helm/jaeger-values.yaml
```

**What this deploys:**
- Jaeger All-In-One (collector + query + UI in one pod)
- In-memory storage (traces retained up to 10,000)

**Why All-In-One:**
- Simplified deployment for development/demo
- Avoids distributed mode complexity
- Sufficient for workloads < 100 req/s

#### 3.3 Verify Infrastructure

```bash
# Check pods
kubectl get pods -n citrus

# Check services
kubectl get svc -n citrus

# Check Helm releases
helm list -n citrus
```

**Expected output:**
```
NAME          NAMESPACE  STATUS    CHART
monitoring    citrus     deployed  kube-prometheus-stack-87.3.0
jaeger        citrus     deployed  jaeger-4.11.1
```

### Step 4: Deploy Application Layer

#### 4.1 Deploy OpenTelemetry Demo

```bash
helm install otel-demo open-telemetry/opentelemetry-demo \
  --version 0.40.9 \
  -n citrus \
  --values deploy/helm/otel-demo-values.yaml
```

**What this deploys:**
- 17 microservices (frontend, cart, checkout, payment, etc.)
- OpenTelemetry Collector (routes telemetry data)
- Kafka (async messaging)
- Valkey/Redis (cart caching)
- PostgreSQL (data persistence)

**Key configuration:**
- Disabled built-in Prometheus/Grafana/Jaeger (uses infrastructure layer)
- OTel Collector sends traces to `jaeger:4317`
- OTel Collector sends metrics to `monitoring-kube-prometheus-prometheus:9090`

#### 4.2 Wait for Services

```bash
# Monitor pod startup
kubectl get pods -n citrus -w

# Wait for all pods to be Running
kubectl wait --for=condition=ready pod --all -n citrus --timeout=600s
```

**This may take 3-5 minutes** as Kubernetes:
1. Pulls ~20 Docker images
2. Starts all services
3. Waits for dependencies (Kafka, PostgreSQL, Valkey)

### Step 5: Access Services

#### 5.1 Access OpenTelemetry Demo (Web Store)

```bash
kubectl port-forward -n citrus svc/otel-demo-frontendproxy 8080:8080
```

Open browser: http://localhost:8080

**Available endpoints:**
- `/` - Main web store
- `/grafana` - Grafana dashboards
- `/jaeger/ui` - Jaeger tracing UI
- `/loadgen` - Load generator UI

#### 5.2 Access Prometheus

```bash
kubectl port-forward -n citrus svc/monitoring-kube-prometheus-prometheus 9090:9090
```

Open browser: http://localhost:9090

**What to check:**
- Status -> Targets: Should show all ServiceMonitors
- Graph: Query metrics like `up`, `http_requests_total`

#### 5.3 Access Grafana

```bash
kubectl port-forward -n citrus svc/monitoring-grafana 3000:80
```

Open browser: http://localhost:3000

**Login credentials:**
- Username: `admin`
- Password: `prom-operator`

**Pre-installed dashboards:**
- Kubernetes / Compute Resources / Namespace (Pods)
- Kubernetes / Networking / Namespace (Pods)
- OpenTelemetry Demo dashboards (if available)

#### 5.4 Access Jaeger UI

```bash
kubectl port-forward -n citrus svc/jaeger 16686:16686
```

Open browser: http://localhost:16686

**What to check:**
- Search for traces from `frontend`, `cartservice`, `checkoutservice`
- View service dependency graph
- Analyze request latency breakdown

---

## Configuration Files

All configuration is stored in `deploy/helm/`:

| File | Purpose | Deployed By |
|------|---------|-------------|
| `monitoring-stack-values.yaml` | Prometheus + Grafana configuration | `helm install monitoring` |
| `jaeger-values.yaml` | Jaeger tracing configuration | `helm install jaeger` |
| `otel-demo-values.yaml` | OpenTelemetry Demo application | `helm install otel-demo` |

### Key Configuration Decisions

#### Monitoring Stack

```yaml
# Disable webhooks for Kind/AKS compatibility
prometheusOperator:
  admissionWebhooks:
    enabled: false
    
# Enable cross-namespace monitoring
prometheus:
  prometheusSpec:
    serviceMonitorSelectorNilUsesHelmValues: false
    serviceMonitorNamespaceSelector: {}
```

#### OpenTelemetry Collector

```yaml
# Route traces to Jaeger
exporters:
  otlp/jaeger:
    endpoint: jaeger:4317
    
# Route metrics to Prometheus
exporters:
  otlphttp/prometheus:
    endpoint: http://monitoring-kube-prometheus-prometheus:9090/api/v1/otlp
```

---

## Troubleshooting

### Pods Not Starting

```bash
# Check pod status
kubectl get pods -n citrus

# Describe problematic pod
kubectl describe pod <pod-name> -n citrus

# Check logs
kubectl logs <pod-name> -n citrus
```

**Common issues:**
- Image pull errors: Check internet connection, wait for retries
- OOMKilled: Increase resource limits in values.yaml
- CrashLoopBackOff: Check application logs for errors

### Services Not Discovered by Prometheus

```bash
# Check ServiceMonitors
kubectl get servicemonitor -n citrus

# Check Prometheus targets
kubectl port-forward -n citrus svc/monitoring-kube-prometheus-prometheus 9090:9090
# Visit http://localhost:9090/targets
```

**Solution:**
- Verify ServiceMonitor labels match Prometheus selector
- Check service has correct labels (`app`, `release`)

### Traces Not Appearing in Jaeger

```bash
# Check OTel Collector logs
kubectl logs -n citrus -l app.kubernetes.io/name=opentelemetry-collector

# Verify Jaeger endpoint
kubectl get svc jaeger -n citrus
```

**Solution:**
- Verify Jaeger service is accessible: `jaeger:4317`
- Check OTel Collector exporter configuration
- Ensure services are sending traces (check app logs)

---

## Cleanup

### Remove Application Layer

```bash
helm uninstall otel-demo -n citrus
```

### Remove Infrastructure Layer

```bash
helm uninstall monitoring -n citrus
helm uninstall jaeger -n citrus
```

### Remove Namespace

```bash
kubectl delete namespace citrus
```

---

## Next Steps

1. **Explore Dashboards**: Import custom Grafana dashboards
2. **Configure Alerts**: Set up Prometheus AlertManager
3. **Add Custom Services**: Deploy your own applications
4. **Integrate with CI/CD**: Automate deployments with GitHub Actions

---

## Architecture Benefits

**Separation of Concerns:**
- Infrastructure layer can be upgraded independently
- Multiple applications can share same monitoring stack
- Clear ownership boundaries (SRE team vs Dev team)

**Infrastructure as Code:**
- All configuration in version control
- Reproducible deployments
- Easy rollback with Git

**Production-Ready Patterns:**
- Cross-namespace monitoring
- Centralized telemetry collection (OTel Collector)
- Standardized observability stack

---

## References

- [Prometheus Operator Documentation](https://prometheus-operator.dev/)
- [Jaeger Documentation](https://www.jaegertracing.io/docs/)
- [OpenTelemetry Demo](https://opentelemetry.io/docs/demo/)
- [Helm Best Practices](https://helm.sh/docs/chart_best_practices/)
