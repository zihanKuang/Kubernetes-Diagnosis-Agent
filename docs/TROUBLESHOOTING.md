# Troubleshooting Guide: Citrus-Orchestrator

This document chronicles all issues encountered and their resolutions. Each entry follows the format: **Symptom -> Investigation -> Root Cause -> Solution**.

---

## Table of Contents

1. [Deployment & Configuration](#deployment--configuration)
2. [Networking & Service Discovery](#networking--service-discovery)
3. [Observability Stack](#observability-stack)
4. [Resource Management](#resource-management)
5. [Cloud Provider Compatibility](#cloud-provider-compatibility)

---

## Deployment & Configuration

### 1. Helm Template Parse Error: Unexpected EOF

**Symptom:**
```bash
$ helm template citrus ./citrus-app
Error: parse error at (citrus-app/templates/all-in.yaml:67): unexpected EOF
```

**Investigation:**
- Checked line 67: found `{{- if eq $name "loadgenerator" }}`
- Scanned downward: no corresponding `{{- end }}`
- Used VS Code bracket matching to identify unclosed block

**Root Cause:**
Added conditional logic for loadgenerator env vars but forgot closing tag, breaking Go template parser.

**Solution:**
```yaml
{{- if eq $name "loadgenerator" }}
- name: FRONTEND_ADDR
  value: "frontend:80"
{{- end }}  # Added missing end tag
```

**Prevention:**
- Use `helm template . --debug` to catch syntax errors before apply
- Format templates with proper indentation for visual block matching

---

### 2. Service Selector Label Mismatch

**Symptom:**
```bash
$ kubectl get endpoints frontend -n citrus
NAME       ENDPOINTS   AGE
frontend   <none>      5m
```

Traffic to frontend LoadBalancer results in 503 Service Unavailable.

**Investigation:**
```bash
$ kubectl describe service frontend -n citrus
Selector:  app.kubernetes.io/name=frontend  # Wrong

$ kubectl get pods -n citrus -l app=frontend
NAME                        READY   STATUS    RESTARTS   AGE
frontend-xxx                1/1     Running   0          5m  # Pod exists
```

**Root Cause:**
Service selector used chart-generated label (`app.kubernetes.io/name`), but pod template only had `app: frontend`. Labels must match exactly for traffic routing.

**Solution:**
Updated `all-in.yaml` Service template:
```yaml
spec:
  selector:
    app: {{ $name }}  # Simplified to match pod labels
```

**Lesson Learned:**
Always verify selector-to-label alignment with:
```bash
kubectl get pods --show-labels -n citrus
```

---

## Networking & Service Discovery

### 3. Connection Refused: cartservice:7070

**Symptom:**
Frontend logs showing:
```
{"error":"could not retrieve cart: rpc error: code = Unavailable desc = connection error: 
desc = \"transport: Error while dialing: dial tcp 10.0.99.59:7070: connect: connection refused\""}
```

**Investigation:**
```bash
# Check if cartservice is running
$ kubectl get pods -n citrus | grep cart
cartservice-xxx   0/1   CrashLoopBackOff

# Check cartservice logs
$ kubectl logs cartservice-xxx -n citrus
Error: failed to connect to Redis at redis-cart:6379
```

**Root Cause:**
Two-layer problem:
1. Port mismatch: Originally configured as 8080, but Dockerfile exposed 7070
2. Missing dependency: cartservice requires Redis, which wasn't deployed

**Solution:**
```bash
# Fix 1: Deploy Redis
$ kubectl run redis-cart --image=redis:alpine -n citrus

# Fix 2: Update values.yaml
services:
  cartservice:
    service: { port: 7070, targetPort: 7070 }  # Changed from 8080
```

**Prevention:**
- Check Dockerfile `EXPOSE` directive before setting service ports
- Use `helm dependency update` for sub-chart managed dependencies

---

### 4. Headless Service Port-Forward Fails

**Symptom:**
```bash
$ kubectl port-forward svc/citrus-jaeger-agent 6831:6831 -n citrus
Error: unable to forward port: service has no exposed ports
```

**Investigation:**
```bash
$ kubectl get svc citrus-jaeger-agent -n citrus -o yaml
spec:
  clusterIP: None  # <- Headless service
  ports:
  - port: 6831
```

**Root Cause:**
Headless services (clusterIP: None) are designed for stateful sets and don't support port-forwarding because there's no virtual IP to bind to.

**Solution:**
Manually created a regular ClusterIP service for development access:
```bash
$ kubectl expose pod citrus-jaeger-xxx --name=jaeger-ui \\
    --port=16686 --target-port=16686 -n citrus
```

For production, use Ingress instead of port-forward.

---

## Observability Stack

### 5. Jaeger UI Shows Only jaeger-all-in-one Service

**Symptom:**
- Jaeger UI loads successfully at localhost:16686
- Service dropdown only lists `jaeger-all-in-one`
- No traces from frontend despite `ENABLE_TRACING=1`

**Investigation Phase 1: Protocol Check**
```bash
$ kubectl get deployment frontend -n citrus -o yaml | grep OTEL
- name: OTEL_EXPORTER_OTLP_ENDPOINT
  value: http://citrus-jaeger:4318
- name: OTEL_EXPORTER_OTLP_PROTOCOL
  value: http/protobuf
```

Changed to gRPC (port 4317) - still no traces.

**Investigation Phase 2: Source Code**
```bash
$ grep -r "ENABLE_TRACING" src/frontend/
main.go:114:  if os.Getenv("ENABLE_TRACING") == "1" {
main.go:178:  mustMapEnv(&svc.collectorAddr, "COLLECTOR_SERVICE_ADDR")
```

**Discovery:** Frontend ignores standard `OTEL_*` env vars

**Root Cause:**
Google's demo frontend predates OpenTelemetry standardization and uses custom environment variables:
- `ENABLE_TRACING=1` (feature flag)
- `COLLECTOR_SERVICE_ADDR` (endpoint)

**Solution:**
Added frontend-specific env vars in `all-in.yaml`:
```yaml
{{- if eq $name "frontend" }}
- name: ENABLE_TRACING
  value: "1"
- name: COLLECTOR_SERVICE_ADDR
  value: "jaeger-grpc-solid:4317"
{{- end }}
```

**Validation:**
```bash
$ curl http://localhost:16686/api/services
{"data":["jaeger-all-in-one","frontend"]}  # Success
```

**Lesson Learned:**
Never assume compliance with standards - always inspect source code when docs fail.

---

### 6. gocql: No Hosts Available in the Pool

**Symptom:**
Jaeger Query API returns:
```json
{"errors":[{"code":500,"msg":"gocql: unable to create session: no hosts available in the pool"}]}
```

**Investigation:**
```bash
$ kubectl get pods -n citrus | grep cassandra
# (no results)

$ helm get values citrus -n citrus | grep cassandra
provisionCassandra: false
```

**Root Cause:**
Despite setting `provisionCassandra: false`, Jaeger Helm chart still tried to initialize Cassandra schema scripts, which attempt to connect to a non-existent database.

**Solution:**
Explicitly disabled schema jobs in `values.yaml`:
```yaml
jaeger:
  provisionCassandra: false
  schema:
    mode: none  # Added: prevents schema init jobs
  storage:
    type: memory  # Force in-memory storage
```

**Alternative for Production:**
Deploy actual Cassandra cluster or use Elasticsearch backend:
```yaml
storage:
  type: elasticsearch
  elasticsearch:
    host: elasticsearch-master
    port: 9200
```

---

### 7. Prometheus Shows 0/0 Targets

**Symptom:**
Prometheus UI -> Status -> Targets shows empty list.

**Investigation:**
```bash
# Check ServiceMonitor creation
$ kubectl get servicemonitor -n citrus
NAME                 AGE
frontend-monitor     10m
cartservice-monitor  10m
...

# Check Prometheus config
$ kubectl exec -it prometheus-citrus-xxx-0 -n citrus -- cat /etc/prometheus/prometheus.yml
# (No scrape configs for citrus namespace)
```

**Root Cause:**
Prometheus Operator namespace selector defaulted to `release: <prometheus-release-name>`, excluding the `citrus` namespace.

**Solution 1: Fix Namespace Selector**
```yaml
# values.yaml
kube-prometheus-stack:
  prometheus:
    prometheusSpec:
      serviceMonitorSelectorNilUsesHelmValues: false  # Allow all namespaces
```

**Solution 2: Add Labels to ServiceMonitors**
```yaml
# servicemonitor.yaml
metadata:
  labels:
    release: monitoring-stack  # Match Prometheus selector
```

**Validation:**
```bash
$ kubectl port-forward svc/citrus-prometheus 9090:9090 -n citrus
# Open http://localhost:9090/targets
# Should now show citrus/* services
```

---

### 8. HPA Shows \<unknown\> / 50%

**Symptom:**
```bash
$ kubectl get hpa frontend-hpa -n citrus
NAME           REFERENCE            TARGETS         MINPODS   MAXPODS   REPLICAS
frontend-hpa   Deployment/frontend  <unknown>/50%   1         3         1
```

**Investigation:**
```bash
$ kubectl top pod frontend-xxx -n citrus
error: Metrics API not available
```

**Root Cause:**
Metrics Server not installed (or not ready).

**Solution:**
```bash
# For AKS, metrics-server is pre-installed
# Verify:
$ kubectl get deployment metrics-server -n kube-system

# If missing:
$ kubectl apply -f https://github.com/kubernetes-sigs/metrics-server/releases/latest/download/components.yaml

# Wait 60-90 seconds for metrics to populate
$ kubectl top nodes  # Should show CPU/memory now
```

**Note:** HPA also requires `resources.requests` to be defined in deployment.

---

## Cloud Provider Compatibility

### 10. CrashLoopBackOff: Profiler Initialization Failed

**Symptom:**
```bash
$ kubectl logs paymentservice-xxx -n citrus
ERROR: profiler: failed to initialize: rpc error: code = Unauthenticated
```

**Root Cause:**
Services like `paymentservice` and `currencyservice` are compiled with Google Cloud Profiler client, which attempts to authenticate with GCP metadata service. On Azure, this fails.

**Solution:**
```yaml
# all-in.yaml - inject for all services
- name: DISABLE_PROFILER
  value: "1"
```

**Alternative:**
Rebuild images with profiler dependencies removed from Dockerfile.

---

### 11. Metadata Server Not Found

**Symptom:**
Some services log warnings:
```
metadata: Get http://metadata.google.internal: dial tcp: lookup metadata.google.internal: no such host
```

**Root Cause:**
Google's demo code checks for GKE environment by querying GCP metadata endpoint.

**Solution:**
```yaml
# all-in.yaml
- name: ENV_PLATFORM
  value: "local"  # Tells app to skip GCP-specific initialization
```

**Code Reference:**
```go
// main.go
if os.Getenv("ENV_PLATFORM") != "local" {
    // GCP-specific initialization
}
```

---

## Quick Reference Commands

### Debugging Pods
```bash
# Get recent crash logs
kubectl logs <pod> -n citrus --previous

# Describe for events
kubectl describe pod <pod> -n citrus

# Execute into running pod
kubectl exec -it <pod> -n citrus -- sh
```

### Debugging Services
```bash
# Check endpoints
kubectl get endpoints <service> -n citrus

# Test internal connectivity
kubectl run -it --rm debug --image=busybox -n citrus -- wget -O- http://frontend:80
```

### Debugging Helm
```bash
# Dry-run to see rendered YAML
helm template citrus ./citrus-app --debug

# Check actual deployed values
helm get values citrus -n citrus

# See all resources created by release
helm get manifest citrus -n citrus
```

---

## Lessons Learned Summary

| Category | Key Insight |
|----------|-------------|
| **Networking** | Always verify port alignment: Dockerfile -> targetPort -> Service port |
| **Observability** | Don't assume OTEL standard compliance - check source code |
| **Resources** | Start with 2x the expected memory, tune down based on `kubectl top` |
| **Templating** | Use `helm template --debug` before every apply |
| **Cloud Migration** | Disable all cloud-specific SDKs (profiler, metadata client, etc.) |

---

**Last Updated:** March 2026  
**Engineer:** Zihan Kuang  
**Project:** Citrus-Orchestrator - Production Microservices on AKS
