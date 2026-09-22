# MCP Server: Deployment & RBAC Verification Guide

Step-by-step guide to build the MCP server image and deploy it **inside** the
cluster over Streamable HTTP, with least-privilege RBAC and a real
verification pass (not just "trust me, it's secure").

For the shorter chaos-injection demo path, see [DEMO.md](DEMO.md). This guide
stands up the in-cluster MCP server and checks that RBAC, not the model,
blocks dangerous writes.

## Prerequisites

- Kubernetes cluster running (Docker Desktop, kind, or minikube)
- kubectl configured and connected
- Docker installed
- `citrus` namespace exists

Verify:
```bash
kubectl cluster-info
kubectl get namespace citrus
```

---

## Step 1: Build the Docker Image

```powershell
cd components/mcp-server
docker build -t mcp-server:v3 .
```

The tag matters: `infra/manifests/mcp-server-deployment.yaml` and
`infra/manifests/rbac-test-pod.yaml` both hardcode `image: mcp-server:v3`.
If you build a different tag, update those two files too (or the Pod won't
find your image and you'll get `ImagePullBackOff`).

**Verify build:**
```bash
docker images | findstr mcp-server
```

The base image is `python:3.12-slim`, not distroless — see
[README design notes](../README.md#design-decisions--trade-offs) for
why that trade-off was made. Don't expect a sub-100MB image; slim + the
`kubernetes`/`mcp` Python deps land in the low hundreds of MB, which is still
far smaller than a full `python:3.12` image.

---

## Step 2: Load the Image into Your Cluster

### Docker Desktop (Kubernetes)
No extra step — Docker Desktop shares its image cache with its own cluster.

### kind
```bash
kind load docker-image mcp-server:v3
```

### minikube
```bash
minikube image load mcp-server:v3
```

**Verify image is loaded (kind/minikube):**
```bash
docker exec -it <kind-control-plane-node> crictl images | findstr mcp-server
```

---

## Step 3: Apply RBAC (ServiceAccount + Role + RoleBinding)

```bash
kubectl apply -f ../../infra/rbac/mcp-server-rbac.yaml
```

**Verify the resources exist:**
```bash
kubectl get serviceaccount mcp-server-sa -n citrus
kubectl get role mcp-server-readonly -n citrus
kubectl get rolebinding mcp-server-readonly-binding -n citrus
```

**Verify the permissions themselves, before deploying the Pod:**
```bash
# Should be allowed
kubectl auth can-i get pods --as=system:serviceaccount:citrus:mcp-server-sa -n citrus
# Expected: yes

# Should be denied
kubectl auth can-i delete pods --as=system:serviceaccount:citrus:mcp-server-sa -n citrus
# Expected: no

# Should be denied (wrong namespace)
kubectl auth can-i get pods --as=system:serviceaccount:citrus:mcp-server-sa -n kube-system
# Expected: no
```

If any of these don't match, fix `infra/rbac/mcp-server-rbac.yaml` before
continuing — everything downstream assumes this is correct.

---

## Step 4: Apply the Auth Secret, Service, and NetworkPolicy

The Deployment (next step) runs the server over **Streamable HTTP** and reads
its Bearer token from a Secret at startup — it will fail to start without it.
Apply these three, in order, before the Deployment:

```bash
kubectl apply -f ../../infra/manifests/mcp-server-secret.yaml
kubectl apply -f ../../infra/manifests/mcp-server-service.yaml
kubectl apply -f ../../infra/manifests/mcp-server-networkpolicy.yaml
```

- **Secret** (`mcp-server-auth`): the shared token the agent must present as
  `Authorization: Bearer <token>`. The default value (`change-me-citrus-mcp`)
  is fine for a local demo — rotate it before using this anywhere real.
- **Service** (`ClusterIP`): stable DNS name (`mcp-server.citrus.svc`) and the
  target for `kubectl port-forward`.
- **NetworkPolicy**: only pods in the same namespace can reach port 8080.
  There is no Ingress — this server is never meant to be reachable from
  outside the cluster.

---

## Step 5: Deploy the MCP Server

```bash
kubectl apply -f ../../infra/manifests/mcp-server-deployment.yaml
```

**Watch the rollout:**
```bash
kubectl get pods -n citrus -l app=mcp-server -w
```

**Expected:**
```
NAME                          READY   STATUS    RESTARTS   AGE
mcp-server-xxxxxxxxxx-xxxxx   1/1     Running   0          30s
```

**If it's not `Running`:**
```bash
kubectl describe pod -n citrus -l app=mcp-server
```
- `ImagePullBackOff` → image wasn't loaded into the cluster (redo Step 2), or the tag doesn't match Step 1.
- `CreateContainerConfigError` → the Secret from Step 4 is missing or misnamed — check `envFrom`/`secretKeyRef` against `mcp-server-auth`.
- `CrashLoopBackOff` → application error, check logs (Step 6).
- `Pending` → resource constraints; check the cluster has enough CPU/memory.

---

## Step 6: Verify the Deployment

**Logs** (this runs `--transport streamable-http`, not stdio):
```bash
kubectl logs -n citrus -l app=mcp-server --tail=50
```

**Expected:**
```
[OK] Kubernetes client initialized (in-cluster mode)
[INFO] MCP Server initialized: citrus-k8s-ops (namespace=citrus, prometheus=http://monitoring-kube-prometheus-prometheus:9090)
[INFO] Starting MCP server on http://0.0.0.0:8080/mcp
```

**Health check over the Service:**
```bash
kubectl port-forward -n citrus svc/mcp-server 8080:8080
curl http://127.0.0.1:8080/health
```

**In-cluster identity (no local kubeconfig involved):**
```powershell
$POD_NAME = kubectl get pods -n citrus -l app=mcp-server -o jsonpath='{.items[0].metadata.name}'
kubectl exec -n citrus $POD_NAME -- cat /var/run/secrets/kubernetes.io/serviceaccount/namespace
# Expected: citrus
```

---

## Step 7: RBAC Security Verification — the most important step

This proves the read-only claim instead of just asserting it. There's a
ready-made test Pod for this — `infra/manifests/rbac-test-pod.yaml` — that
runs under the *same* `mcp-server-sa` ServiceAccount and attempts both
allowed and forbidden operations.

```bash
kubectl apply -f ../../infra/manifests/rbac-test-pod.yaml
kubectl wait --for=condition=Ready pod/rbac-test -n citrus --timeout=30s || true
kubectl logs -n citrus rbac-test
kubectl delete pod -n citrus rbac-test
```

**Expected output (from the script's own print statements):**
```
Test 1: List pods (should SUCCEED)
PASS: Found N pods

Test 2: Get pod logs (should SUCCEED)
PASS: Retrieved logs from <pod-name>

Test 3: Delete pod (should BE FORBIDDEN)
PASS: Permission denied as expected (403 Forbidden)

Test 4: Create pod (should BE FORBIDDEN)
PASS: Permission denied as expected (403 Forbidden)

Test 5: Access other namespace (should BE FORBIDDEN)
PASS: Permission denied as expected (403 Forbidden)
```

**If a "should SUCCEED" test fails:** check the RoleBinding references the
right ServiceAccount/Role (Step 3), and that `pods`/`pods/log` are in the
Role's `resources`.

**If a "should BE FORBIDDEN" test instead succeeds:** stop and treat this as
a real incident, not a doc issue — it means the RBAC Role has write verbs it
shouldn't. Re-check `infra/rbac/mcp-server-rbac.yaml` only has
`get`/`list`/`watch`.

---

## Step 8: Manual verification

Same checks as Step 7, run from the MCP pod. The delete is expected to fail
because the ServiceAccount Role has no write verbs — not because the agent
refused.

```bash
$POD_NAME = kubectl get pods -n citrus -l app=mcp-server -o jsonpath='{.items[0].metadata.name}'

kubectl exec -n citrus $POD_NAME -- python3 -c "
from kubernetes import client, config
from kubernetes.client.rest import ApiException
config.load_incluster_config()
v1 = client.CoreV1Api()
try:
    v1.delete_namespaced_pod('some-pod-name', 'citrus')
    print('SECURITY ISSUE: delete succeeded!')
except ApiException as e:
    print(f'Blocked as expected: {e.status} {e.reason}')
"
```

**Expected:** `Blocked as expected: 403 Forbidden`.

---

## Success Criteria

- `python:3.12-slim`, non-root image built and loaded into the cluster
- RBAC (ServiceAccount, Role, RoleBinding), Secret, Service, and NetworkPolicy all applied
- MCP Server Pod `Running`, logs show `in-cluster mode` and `streamable-http` (not stdio, not kubectl-CLI mode)
- `/health` responds over the port-forwarded Service
- `rbac-test-pod.yaml`: all 5 checks PASS
- Manual delete attempt: blocked with 403

---

## Cleanup

```bash
kubectl delete -f ../../infra/manifests/mcp-server-deployment.yaml
kubectl delete -f ../../infra/manifests/mcp-server-networkpolicy.yaml
kubectl delete -f ../../infra/manifests/mcp-server-service.yaml
kubectl delete -f ../../infra/manifests/mcp-server-secret.yaml
kubectl delete -f ../../infra/rbac/mcp-server-rbac.yaml
docker rmi mcp-server:v3
```

---

## Troubleshooting

### Pod stuck in `ImagePullBackOff`
```bash
docker images | findstr mcp-server
# kind:
kind load docker-image mcp-server:v3
# minikube:
minikube image load mcp-server:v3
```

### Pod stuck in `CreateContainerConfigError`
The Deployment expects a Secret key that doesn't exist yet — redo Step 4
(`mcp-server-secret.yaml`) before re-applying the Deployment.

### Pod stuck in `CrashLoopBackOff`
```bash
kubectl logs -n citrus -l app=mcp-server --tail=100
```
Common causes: missing dependency in `requirements.txt`, a Python syntax
error in `mcp_server/server.py`/`mcp_server/tools/kubernetes.py`, or
`PROMETHEUS_URL` pointing at a service that doesn't exist in this cluster.

### RBAC test says a read operation was blocked
```bash
kubectl get rolebinding mcp-server-readonly-binding -n citrus -o yaml
kubectl get pod -n citrus -l app=mcp-server -o jsonpath='{.items[0].spec.serviceAccountName}'
# Expected: mcp-server-sa
```
Check the RoleBinding's `subjects.name` and `roleRef.name`, and that the Pod
is actually using `mcp-server-sa` (not the namespace's `default` SA).

---

## Related Files

- `Dockerfile` — single-stage `python:3.12-slim`, non-root
- `../../infra/rbac/mcp-server-rbac.yaml` — ServiceAccount / Role / RoleBinding
- `../../infra/manifests/mcp-server-secret.yaml` — Bearer auth token
- `../../infra/manifests/mcp-server-service.yaml` — ClusterIP Service
- `../../infra/manifests/mcp-server-networkpolicy.yaml` — same-namespace-only ingress
- `../../infra/manifests/mcp-server-deployment.yaml` — the Deployment itself
- `../../infra/manifests/rbac-test-pod.yaml` — automated RBAC verification Pod
- `mcp_server/tools/kubernetes.py` — in-cluster vs kubectl-CLI mode auto-detection

## See also

- [DEMO.md](DEMO.md) — the shorter chaos-injection + diagnosis walkthrough
- [../README.md](../README.md) — quick start, layout, resume bullets
