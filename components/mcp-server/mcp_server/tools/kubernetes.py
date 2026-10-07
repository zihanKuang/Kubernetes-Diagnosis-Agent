"""kubectl / Kubernetes API helpers used by MCP tools."""

from __future__ import annotations

import json
import os
import re
import subprocess
import time
from datetime import datetime, timedelta, timezone
from typing import Any, Dict, List, Optional

import requests

_DEPLOY_NAME = re.compile(r"^[a-z0-9]([-a-z0-9]*[a-z0-9])?$")
_MAX_REPLICAS = 3
_MIN_REPLICAS = 1


def _as_dict(value: Any) -> Dict[str, Any]:
    return value if isinstance(value, dict) else {}


def _meta(obj: Dict[str, Any]) -> Dict[str, Any]:
    return _as_dict(obj.get("metadata"))


def _first(mapping: Dict[str, Any], *keys: str) -> Any:
    for key in keys:
        if mapping.get(key) is not None:
            return mapping[key]
    return None


def cluster_name() -> str:
    return os.getenv("CLUSTER_NAME") or "local"


def object_identity(
    obj: Dict[str, Any],
    *,
    kind: str = "Pod",
    namespace: str = "",
    cluster: str = "",
) -> Dict[str, str]:
    meta = _meta(obj)
    return {
        "cluster": cluster or cluster_name(),
        "namespace": str(meta.get("namespace") or namespace or ""),
        "kind": kind,
        "name": str(meta.get("name") or "unknown"),
        "uid": str(meta.get("uid") or ""),
    }


def owner_refs(obj: Dict[str, Any]) -> List[Dict[str, str]]:
    meta = _meta(obj)
    raw = meta.get("ownerReferences") or meta.get("owner_references") or []
    out: List[Dict[str, str]] = []
    for ref in raw:
        if not isinstance(ref, dict):
            continue
        kind = str(ref.get("kind") or "")
        name = str(ref.get("name") or "")
        if not kind or not name:
            continue
        out.append({
            "kind": kind,
            "name": name,
            "uid": str(ref.get("uid") or ""),
        })
    return out


def owner_ref_text(obj: Dict[str, Any]) -> str:
    refs = owner_refs(obj)
    if not refs:
        return "-"
    top = refs[0]
    uid = f" uid={top['uid']}" if top["uid"] else ""
    return f"{top['kind']}/{top['name']}{uid}"


def involved_object(event: Dict[str, Any]) -> Dict[str, str]:
    raw = event.get("involvedObject") or event.get("involved_object") or {}
    if not isinstance(raw, dict):
        return {"kind": "Object", "name": "N/A", "uid": "", "namespace": ""}
    return {
        "kind": str(raw.get("kind") or "Object"),
        "name": str(raw.get("name") or "N/A"),
        "uid": str(raw.get("uid") or ""),
        "namespace": str(raw.get("namespace") or ""),
    }


def event_times(event: Dict[str, Any]) -> Dict[str, Any]:
    last = (
        _parse_event_time(_first(event, "lastTimestamp", "last_timestamp"))
        or _parse_event_time(_first(event, "eventTime", "event_time"))
        or _parse_event_time(_first(_meta(event), "creationTimestamp", "creation_timestamp"))
    )
    first = (
        _parse_event_time(_first(event, "firstTimestamp", "first_timestamp"))
        or last
    )
    count = event.get("count")
    if count is None:
        count = 1
    try:
        count = int(count)
    except (TypeError, ValueError):
        count = 1
    return {"first": first, "last": last, "count": count}


def event_as_dict(event: Any) -> Dict[str, Any]:
    if isinstance(event, dict):
        return event
    involved = getattr(event, "involved_object", None)
    meta = getattr(event, "metadata", None)
    inv: Dict[str, Any] = {}
    if involved is not None:
        inv = {
            "kind": getattr(involved, "kind", None),
            "name": getattr(involved, "name", None),
            "uid": getattr(involved, "uid", None),
            "namespace": getattr(involved, "namespace", None),
        }
    return {
        "metadata": {
            "name": getattr(meta, "name", None) if meta is not None else None,
            "namespace": getattr(meta, "namespace", None) if meta is not None else None,
            "uid": getattr(meta, "uid", None) if meta is not None else None,
            "creationTimestamp": (
                getattr(meta, "creation_timestamp", None) if meta is not None else None
            ),
        },
        "type": getattr(event, "type", None),
        "reason": getattr(event, "reason", None),
        "message": getattr(event, "message", None),
        "count": getattr(event, "count", None),
        "firstTimestamp": getattr(event, "first_timestamp", None),
        "lastTimestamp": getattr(event, "last_timestamp", None),
        "eventTime": getattr(event, "event_time", None),
        "involvedObject": inv,
    }


def format_event_object(event: Dict[str, Any]) -> str:
    inv = involved_object(event)
    obj = f"{inv['kind']}/{inv['name']}"
    if inv["uid"]:
        obj += f" uid={inv['uid']}"
    return obj


def _exit_code(terminated: Dict[str, Any]) -> Optional[int]:
    raw = _first(terminated, "exitCode", "exit_code")
    if raw is None or raw == "":
        return None
    try:
        return int(raw)
    except (TypeError, ValueError):
        return None


def pod_container_statuses(pod: Dict[str, Any]) -> List[Dict[str, Any]]:
    """kubectl JSON is camelCase; the Python client to_dict() is snake_case."""
    status = _as_dict(pod.get("status"))
    return status.get("containerStatuses") or status.get("container_statuses") or []


def container_restart_count(container: Dict[str, Any]) -> int:
    return int(container.get("restartCount") or container.get("restart_count") or 0)


def _container_view(container: Dict[str, Any]) -> Dict[str, Any]:
    state = _as_dict(container.get("state"))
    waiting = _as_dict(state.get("waiting"))
    terminated = _as_dict(state.get("terminated"))
    running = state.get("running")
    last = _as_dict(_first(container, "lastState", "last_state") or {})
    last_term = _as_dict(last.get("terminated"))
    return {
        "name": container.get("name") or "container",
        "ready": bool(container.get("ready", False)),
        "restarts": container_restart_count(container),
        "waiting_reason": waiting.get("reason") or "",
        "waiting_message": waiting.get("message") or "",
        "term_reason": terminated.get("reason") or "",
        "term_exit": _exit_code(terminated),
        "running": isinstance(running, dict),
        "last_reason": last_term.get("reason") or "",
        "last_exit": _exit_code(last_term),
    }


def _pod_condition(pod: Dict[str, Any], condition_type: str) -> Dict[str, Any]:
    status = _as_dict(pod.get("status"))
    for condition in status.get("conditions") or []:
        if isinstance(condition, dict) and condition.get("type") == condition_type:
            return condition
    return {}


def summarize_pod_state(pod: Dict[str, Any]) -> str:
    """Primary fault label. OOMKilled wins over a later CrashLoopBackOff."""
    views = [_container_view(container) for container in pod_container_statuses(pod)]
    if any(view["last_reason"] == "OOMKilled" or view["term_reason"] == "OOMKilled" for view in views):
        return "OOMKilled"
    waiting = [view["waiting_reason"] for view in views if view["waiting_reason"]]
    if waiting:
        return waiting[0]
    terminated = [view["term_reason"] for view in views if view["term_reason"]]
    if terminated:
        return terminated[0]
    if views and any(view["running"] and not view["ready"] for view in views):
        return "RunningNotReady"
    if views and all(view["ready"] for view in views):
        return "Running"
    scheduled = _pod_condition(pod, "PodScheduled")
    if str(scheduled.get("status")) == "False":
        return scheduled.get("reason") or "Unschedulable"
    return str(_as_dict(pod.get("status")).get("phase") or "Unknown")


def format_pod_list_line(
    pod: Dict[str, Any],
    *,
    namespace: str = "",
    cluster: str = "",
) -> str:
    """One triage line. Healthy Running pods keep no state= suffix."""
    ident = object_identity(pod, namespace=namespace, cluster=cluster)
    status = _as_dict(pod.get("status"))
    labels = _as_dict(_meta(pod).get("labels"))
    component = (
        labels.get("app.kubernetes.io/component")
        or labels.get("app")
        or labels.get("app.kubernetes.io/name")
        or "n/a"
    )
    views = [_container_view(container) for container in pod_container_statuses(pod)]
    restarts = sum(view["restarts"] for view in views)
    ready = bool(views) and all(view["ready"] for view in views)
    state = summarize_pod_state(pod)
    state_suffix = "" if state == "Running" else f" | state={state}"
    uid = ident["uid"] or "-"
    ns = ident["namespace"] or "-"
    return (
        f"- {ident['name']} | phase={status.get('phase') or 'Unknown'} | "
        f"ready={ready} | restarts={restarts} | component={component} | "
        f"uid={uid} | ns={ns} | owner={owner_ref_text(pod)}{state_suffix}"
    )


def _fmt_exit(code: Optional[int]) -> str:
    if code is None:
        return ""
    return f" exit={code}"


def format_pod_status(
    pod: Dict[str, Any],
    *,
    namespace: str = "",
    cluster: str = "",
) -> str:
    """Status block that names the failure class, not just phase and restart count."""
    status = _as_dict(pod.get("status"))
    ident = object_identity(pod, namespace=namespace, cluster=cluster)
    views = [_container_view(container) for container in pod_container_statuses(pod)]
    restarts = sum(view["restarts"] for view in views)
    ready = bool(views) and all(view["ready"] for view in views)
    state = summarize_pod_state(pod)
    lines = [
        f"Pod: {ident['name']}",
        (
            f"  Identity: cluster={ident['cluster']} ns={ident['namespace'] or '-'} "
            f"kind=Pod name={ident['name']} uid={ident['uid'] or '-'}"
        ),
        f"  Owner: {owner_ref_text(pod)}",
        f"  Status: {status.get('phase') or 'Unknown'}",
        f"  Restarts: {restarts}",
        f"  Ready: {ready}",
        f"  State: {state}",
    ]
    for view in views:
        bits = [f"  Container {view['name']}:"]
        if view["waiting_reason"]:
            message = f" ({view['waiting_message']})" if view["waiting_message"] else ""
            bits.append(f"waiting={view['waiting_reason']}{message}")
        if view["term_reason"]:
            bits.append(f"terminated={view['term_reason']}{_fmt_exit(view['term_exit'])}")
        if view["last_reason"]:
            bits.append(
                f"last_termination={view['last_reason']}{_fmt_exit(view['last_exit'])}"
            )
        if view["running"]:
            bits.append("running")
        if len(bits) == 1:
            bits.append("no container state")
        lines.append(" ".join(bits))

    ready_cond = _pod_condition(pod, "Ready")
    if ready_cond and str(ready_cond.get("status")) == "False":
        extra = " — ".join(
            part for part in (ready_cond.get("reason") or "", ready_cond.get("message") or "") if part
        )
        if extra:
            lines.append(f"  Ready condition: {extra}")
    scheduled = _pod_condition(pod, "PodScheduled")
    if scheduled and str(scheduled.get("status")) == "False":
        detail = " — ".join(
            part for part in (scheduled.get("reason") or "Unschedulable", scheduled.get("message") or "") if part
        )
        lines.append(f"  PodScheduled: False — {detail}")
    if state == "OOMKilled" and any(view["waiting_reason"] == "CrashLoopBackOff" for view in views):
        lines.append(
            "  Note: last termination is OOMKilled. CrashLoopBackOff here is the "
            "restart backoff after the OOM kill, not a separate process bug."
        )
    if (
        state == "RunningNotReady"
        and restarts == 0
        and not any(view["waiting_reason"] for view in views)
    ):
        lines.append(
            "  Note: the process is running and has not restarted, but it is not Ready. "
            "Check events for a failing readiness or liveness probe (reason Unhealthy)."
        )
    return "\n".join(lines)


def recovery_note(
    passed: bool,
    restart_total: int,
    *,
    min_ready: int = 1,
    matched: int = 0,
    ready_count: int = 0,
    checked_at: str = "",
) -> str:
    """PASS/FAIL here is Ready-at-check-time, not lasting recovery."""
    when = checked_at or "checked_at"
    lines = [
        (
            "This check only proves Ready at checked_at for the matched objects. "
            "It does not prove sustained recovery or that user requests succeed."
        ),
    ]
    if passed:
        lines.append(
            f"Ready at {when}: {ready_count}/{matched or ready_count} matched "
            "pods met the check. That is not a lasting-health verdict."
        )
        if min_ready < matched:
            lines.append(
                f"min_ready={min_ready} passing does not mean every replica is Ready "
                f"(ready={ready_count}/{matched})."
            )
        if restart_total > 0:
            lines.append(
                "Elevated restart counts after a chaos kill are expected at this "
                "instant; they are not evidence that the outage stayed gone."
            )
    else:
        lines.append(
            "FAIL means matching pods are not Running and Ready at checked_at. "
            "A ReplicaSet existing is not recovery. CrashLoopBackOff, "
            "ImagePullBackOff, OOMKilled, a failing probe, or Unschedulable "
            "does not clear just because a controller object exists."
        )
    return "\n" + "\n".join(lines)


def format_recovery_report(
    *,
    status: str,
    selector: str,
    namespace: str,
    checked_at: str,
    matched: int,
    ready_count: int,
    min_ready: int,
    restart_total: int,
    object_lines: List[str],
    reason: str,
    error_text: str = "",
) -> str:
    if status == "pass":
        summary = (
            f"PASS: {ready_count}/{matched} Ready pods "
            f"(required min_ready={min_ready}) for selector '{selector}'"
        )
    elif status == "unknown":
        detail = error_text or "query failed"
        summary = f"UNKNOWN: query failed: {detail}"
    elif reason == "no_matching_objects":
        summary = (
            f"FAIL: No pods found matching '{selector}' in "
            f"namespace '{namespace}'. Service may still be down."
        )
    else:
        summary = (
            f"FAIL: {ready_count}/{matched} Ready pods "
            f"(required min_ready={min_ready}) for selector '{selector}'"
        )
    lines = [
        summary,
        f"status={status}",
        f"reason={reason}",
        f"checked_at={checked_at}",
        f"matched={matched}",
        f"ready={ready_count}",
        f"required={min_ready}",
        "scope=readiness_at_check_time",
    ]
    lines.extend(object_lines)
    if status == "unknown":
        lines.append(
            "This is not a Ready/not-Ready verdict. The list query failed."
        )
        return "\n".join(lines)
    note = recovery_note(
        status == "pass",
        restart_total,
        min_ready=min_ready,
        matched=matched,
        ready_count=ready_count,
        checked_at=checked_at,
    )
    if note:
        lines.append(note.lstrip("\n"))
    return "\n".join(lines)


def resolve_namespace(explicit: Optional[str] = None) -> str:
    """Prefer explicit arg, then NAMESPACE / KUBERNETES_NAMESPACE env, else citrus."""
    if explicit:
        return explicit
    return (
        os.getenv("NAMESPACE")
        or os.getenv("KUBERNETES_NAMESPACE")
        or "citrus"
    )


def default_prometheus_url() -> str:
    """
    Local: localhost (expects port-forward).
    In-cluster: kube-prometheus-stack service DNS unless PROMETHEUS_URL is set.
    """
    if os.getenv("PROMETHEUS_URL"):
        return os.getenv("PROMETHEUS_URL")
    if os.path.exists("/var/run/secrets/kubernetes.io/serviceaccount/token"):
        return "http://monitoring-kube-prometheus-prometheus:9090"
    return "http://localhost:9090"


def _parse_event_time(value: Any) -> Optional[datetime]:
    """Parse K8s event timestamp into aware UTC datetime."""
    if value is None:
        return None
    if isinstance(value, datetime):
        if value.tzinfo is None:
            return value.replace(tzinfo=timezone.utc)
        return value.astimezone(timezone.utc)
    if isinstance(value, str):
        text = value.strip()
        if not text or text == "N/A":
            return None
        # kubectl JSON uses RFC3339; tolerate trailing Z
        if text.endswith("Z"):
            text = text[:-1] + "+00:00"
        try:
            dt = datetime.fromisoformat(text)
        except ValueError:
            return None
        if dt.tzinfo is None:
            dt = dt.replace(tzinfo=timezone.utc)
        return dt.astimezone(timezone.utc)
    return None


class KubernetesTools:
    """Provides Kubernetes cluster inspection capabilities via MCP protocol."""

    def __init__(self, namespace: str = None, use_kubectl: bool = None):
        """
        Args:
            namespace: Target namespace. If None, resolve from env.
            use_kubectl: True=kubectl CLI, False=K8s Python client,
                         None=auto-detect (SA token => in-cluster).
        """
        self.namespace = resolve_namespace(namespace)
        self.cluster = cluster_name()
        self.prometheus_url = default_prometheus_url()

        if use_kubectl is None:
            self.use_kubectl = not os.path.exists(
                "/var/run/secrets/kubernetes.io/serviceaccount/token"
            )
        else:
            self.use_kubectl = use_kubectl

        if not self.use_kubectl:
            self._init_k8s_client()

    def _init_k8s_client(self):
        """Initialize Kubernetes Python client for in-cluster usage."""
        try:
            from kubernetes import client, config

            config.load_incluster_config()
            self.v1 = client.CoreV1Api()
            print("[OK] Kubernetes client initialized (in-cluster mode)")
            print(f"    Namespace: {self.namespace}")
            print(f"    Prometheus: {self.prometheus_url}")
        except Exception as e:
            print(f"[ERROR] Failed to initialize K8s client: {e}")
            print("        Falling back to kubectl CLI mode")
            self.use_kubectl = True

    def _kubectl(self, *args) -> str:
        cmd = ["kubectl", "-n", self.namespace] + list(args)
        result = subprocess.run(
            cmd,
            capture_output=True,
            text=True,
            encoding="utf-8",
            errors="replace",
            check=True,
        )
        return result.stdout.strip() if result.stdout else ""

    def _parse_label_selector(self, pod_selector: str) -> str:
        label_dict = dict(
            item.split("=", 1) for item in pod_selector.split(",") if "=" in item
        )
        return ",".join(f"{k}={v}" for k, v in label_dict.items())

    @staticmethod
    def _container_statuses(pod: Dict[str, Any]) -> List[Dict[str, Any]]:
        """kubectl JSON is camelCase; the Python client to_dict() is snake_case."""
        return pod_container_statuses(pod)

    @staticmethod
    def _restart_count(container: Dict[str, Any]) -> int:
        return container_restart_count(container)

    async def list_pods(self) -> str:
        """List all pods in the namespace with phase, readiness, restarts, labels."""
        try:
            if self.use_kubectl:
                output = self._kubectl("get", "pods", "-o", "json")
                data = json.loads(output)
            else:
                pods = self.v1.list_namespaced_pod(namespace=self.namespace)
                data = {"items": [pod.to_dict() for pod in pods.items]}

            items = data.get("items", [])
            collected_at = datetime.now(timezone.utc).isoformat(timespec="seconds")
            if not items:
                return (
                    f"No pods found in namespace '{self.namespace}' "
                    f"collected_at={collected_at}"
                )

            lines = [
                (
                    f"Pods in namespace '{self.namespace}' ({len(items)} total) "
                    f"collected_at={collected_at} cluster={self.cluster}:"
                ),
                "",
            ]
            for pod in items:
                lines.append(
                    format_pod_list_line(
                        pod,
                        namespace=self.namespace,
                        cluster=self.cluster,
                    )
                )

            return "\n".join(lines)

        except subprocess.CalledProcessError as e:
            error_msg = e.stderr if e.stderr else str(e)
            return f"Error listing pods: {error_msg}"
        except Exception as e:
            return f"Unexpected error: {str(e)}"

    async def validate_recovery(
        self,
        pod_selector: str,
        min_ready: int = 1,
    ) -> str:
        """Read-only check: matching pods Running + Ready at this instant."""
        checked_at = datetime.now(timezone.utc).isoformat(timespec="seconds")
        try:
            if self.use_kubectl:
                output = self._kubectl("get", "pods", "-l", pod_selector, "-o", "json")
                data = json.loads(output)
            else:
                label_selector_str = self._parse_label_selector(pod_selector)
                pods = self.v1.list_namespaced_pod(
                    namespace=self.namespace,
                    label_selector=label_selector_str,
                )
                data = {"items": [pod.to_dict() for pod in pods.items]}

            items = data.get("items", [])
            if not items:
                return format_recovery_report(
                    status="fail",
                    selector=pod_selector,
                    namespace=self.namespace,
                    checked_at=checked_at,
                    matched=0,
                    ready_count=0,
                    min_ready=min_ready,
                    restart_total=0,
                    object_lines=[],
                    reason="no_matching_objects",
                )

            ready_count = 0
            restart_total = 0
            details = []
            for pod in items:
                ident = object_identity(
                    pod,
                    namespace=self.namespace,
                    cluster=self.cluster,
                )
                phase = _as_dict(pod.get("status")).get("phase") or "Unknown"
                views = [_container_view(c) for c in self._container_statuses(pod)]
                restarts = sum(view["restarts"] for view in views)
                restart_total += restarts
                is_ready = (
                    phase == "Running"
                    and bool(views)
                    and all(view["ready"] for view in views)
                )
                if is_ready:
                    ready_count += 1
                uid = ident["uid"] or "-"
                details.append(
                    f"- {ident['name']}: phase={phase}, ready={is_ready}, "
                    f"restarts={restarts}, uid={uid}, ns={ident['namespace'] or self.namespace}"
                )

            passed = ready_count >= min_ready
            return format_recovery_report(
                status="pass" if passed else "fail",
                selector=pod_selector,
                namespace=self.namespace,
                checked_at=checked_at,
                matched=len(items),
                ready_count=ready_count,
                min_ready=min_ready,
                restart_total=restart_total,
                object_lines=details,
                reason="ready_at_check_time" if passed else "not_ready",
            )

        except subprocess.CalledProcessError as e:
            error_msg = e.stderr if e.stderr else str(e)
            return format_recovery_report(
                status="unknown",
                selector=pod_selector,
                namespace=self.namespace,
                checked_at=checked_at,
                matched=0,
                ready_count=0,
                min_ready=min_ready,
                restart_total=0,
                object_lines=[],
                reason="query_failed",
                error_text=error_msg,
            )
        except Exception as e:
            return format_recovery_report(
                status="unknown",
                selector=pod_selector,
                namespace=self.namespace,
                checked_at=checked_at,
                matched=0,
                ready_count=0,
                min_ready=min_ready,
                restart_total=0,
                object_lines=[],
                reason="query_failed",
                error_text=str(e),
            )

    async def get_pod_logs(self, pod_selector: str, lines: int = 50) -> str:
        """Get recent logs from pods matching a label selector."""
        try:
            if self.use_kubectl:
                output = self._kubectl(
                    "logs",
                    "-l",
                    pod_selector,
                    "--tail",
                    str(lines),
                    "--prefix",
                )
                if not output:
                    return f"No logs found for pods matching '{pod_selector}'"
                return output

            label_selector_str = self._parse_label_selector(pod_selector)
            pods = self.v1.list_namespaced_pod(
                namespace=self.namespace,
                label_selector=label_selector_str,
            )

            if not pods.items:
                return f"No pods found matching '{pod_selector}'"

            log_output = []
            for pod in pods.items:
                pod_name = pod.metadata.name
                try:
                    logs = self.v1.read_namespaced_pod_log(
                        name=pod_name,
                        namespace=self.namespace,
                        tail_lines=lines,
                    )
                    prefixed_logs = "\n".join(
                        f"[{pod_name}] {line}" for line in logs.split("\n")
                    )
                    log_output.append(prefixed_logs)
                except Exception as e:
                    log_output.append(f"[{pod_name}] Error: {str(e)}")

            return "\n".join(log_output)

        except subprocess.CalledProcessError as e:
            error_msg = e.stderr if e.stderr else str(e)
            return f"Error fetching logs: {error_msg}"
        except Exception as e:
            return f"Unexpected error: {str(e)}"

    async def get_pod_status(self, pod_selector: str) -> str:
        """Get status for pods matching a label selector."""
        try:
            if self.use_kubectl:
                output = self._kubectl("get", "pods", "-l", pod_selector, "-o", "json")
                data = json.loads(output)
            else:
                label_selector_str = self._parse_label_selector(pod_selector)
                pods = self.v1.list_namespaced_pod(
                    namespace=self.namespace,
                    label_selector=label_selector_str,
                )
                data = {"items": [pod.to_dict() for pod in pods.items]}

            blocks = [
                format_pod_status(
                    pod,
                    namespace=self.namespace,
                    cluster=self.cluster,
                )
                for pod in data.get("items", [])
            ]
            if not blocks:
                return f"No pods found matching '{pod_selector}'"

            return "\n\n".join(blocks)

        except subprocess.CalledProcessError as e:
            error_msg = e.stderr if e.stderr else str(e)
            return f"Error fetching pod status: {error_msg}"
        except Exception as e:
            return f"Unexpected error: {str(e)}"

    async def get_recent_events(self, minutes: int = 10) -> str:
        """Kubernetes events in the last `minutes`. TIME is last occurrence, UTC."""
        try:
            collected_at = datetime.now(timezone.utc)
            cutoff = collected_at - timedelta(minutes=max(1, minutes))
            rows: List[tuple] = []

            if self.use_kubectl:
                output = self._kubectl("get", "events", "-o", "json")
                raw_items = json.loads(output).get("items", []) if output else []
            else:
                events = self.v1.list_namespaced_event(namespace=self.namespace)
                raw_items = list(events.items)

            for raw in raw_items:
                event = event_as_dict(raw)
                times = event_times(event)
                last = times["last"]
                if last is None or last < cutoff:
                    continue
                first = times["first"] or last
                rows.append(
                    (
                        last,
                        event.get("type") or "N/A",
                        event.get("reason") or "N/A",
                        format_event_object(event),
                        event.get("message") or "N/A",
                        first,
                        times["count"],
                    )
                )

            collected = collected_at.isoformat(timespec="seconds")
            window_start = cutoff.isoformat(timespec="seconds")
            if not rows:
                return (
                    f"No events in namespace '{self.namespace}' "
                    f"within the last {minutes} minute(s) "
                    f"collected_at={collected} window_start={window_start}"
                )

            rows.sort(key=lambda r: r[0])
            rows = rows[-40:]
            legacy = os.getenv("CITRUS_ABLATION", "").strip().lower() == "legacy"
            if legacy:
                lines = [
                    f"Events in last {minutes}m (namespace={self.namespace}):",
                    "TIME\tTYPE\tREASON\tMESSAGE",
                ]
                for ts, evt_type, reason, _obj_ref, message, _first, _count in rows:
                    lines.append(f"{ts.isoformat()}\t{evt_type}\t{reason}\t{message}")
            else:
                lines = [
                    (
                        f"Events in last {minutes}m (namespace={self.namespace}, "
                        f"collected_at={collected}, window_start={window_start}):"
                    ),
                    "TIME is last occurrence (UTC). FIRST is first occurrence. "
                    "COUNT is the Kubernetes event count. Do not treat collected_at "
                    "as event time.",
                    "NOTE: Events are correlated by the OBJECT column (kind/name/uid), "
                    "not by timestamp proximity alone. Events for different objects "
                    "(different pod names or UIDs) may be unrelated even if they "
                    "occur seconds apart -- this commonly happens when a previous "
                    "experiment/resource is deleted right before a new one is created.",
                    "TIME\tTYPE\tREASON\tOBJECT\tMESSAGE\tFIRST\tCOUNT",
                ]
                for ts, evt_type, reason, obj_ref, message, first, count in rows:
                    lines.append(
                        f"{ts.isoformat()}\t{evt_type}\t{reason}\t{obj_ref}\t"
                        f"{message}\t{first.isoformat()}\t{count}"
                    )
            return "\n".join(lines)

        except subprocess.CalledProcessError as e:
            error_msg = e.stderr if e.stderr else str(e)
            return f"Error fetching events: {error_msg}"
        except Exception as e:
            return f"Unexpected error: {str(e)}"

    async def query_prometheus(
        self,
        promql: str,
        prometheus_url: str = None,
    ) -> str:
        """Execute a PromQL query against Prometheus."""
        url_base = prometheus_url or self.prometheus_url
        try:
            url = f"{url_base.rstrip('/')}/api/v1/query"
            response = requests.get(url, params={"query": promql}, timeout=10)
            response.raise_for_status()

            data = response.json()
            if data.get("status") != "success":
                return f"Prometheus query failed: {data.get('error', 'Unknown error')}"

            results = data["data"]["result"]
            if not results:
                return f"No data returned for query: {promql}"

            output_lines = []
            for result in results:
                metric = result["metric"]
                value = result["value"][1]
                metric_str = ",".join(f'{k}="{v}"' for k, v in metric.items())
                output_lines.append(f"Metric: {metric_str}\nValue: {value}\n")

            return "\n".join(output_lines)

        except requests.RequestException as e:
            return (
                f"Prometheus query failed (url={url_base}): {str(e)}. "
                "Local tip: kubectl port-forward -n citrus "
                "svc/monitoring-kube-prometheus-prometheus 9090:9090"
            )
        except Exception as e:
            return f"Unexpected error: {str(e)}"

    @staticmethod
    def _check_deploy_name(name: str) -> Optional[str]:
        if not name or not _DEPLOY_NAME.match(name) or len(name) > 63:
            return (
                f"ERROR: invalid deployment name {name!r}. "
                "Expected a DNS-1123 label (lowercase, digits, hyphens)."
            )
        return None

    def _kubectl_unchecked(self, *args: str) -> subprocess.CompletedProcess:
        cmd = ["kubectl", "-n", self.namespace, *args]
        return subprocess.run(
            cmd,
            capture_output=True,
            text=True,
            encoding="utf-8",
            errors="replace",
        )

    def _deployment_selector(self, name: str) -> str:
        return f"app.kubernetes.io/component={name}"

    async def _recovery_after_write(self, name: str) -> str:
        # ReplicaSet needs a moment to roll. Short wait, then the existing
        # read-only checker. Agent prompt also asks for validate_recovery.
        time.sleep(5)
        selector = self._deployment_selector(name)
        result = await self.validate_recovery(selector, min_ready=1)
        if result.startswith("FAIL: No pods found"):
            fallback = f"app={name}"
            extra = await self.validate_recovery(fallback, min_ready=1)
            return (
                f"{result}\nRetried selector '{fallback}':\n{extra}"
            )
        return result

    async def restart_deployment(self, name: str) -> str:
        """Low-risk write: kubectl rollout restart. Always followed by validate_recovery."""
        bad = self._check_deploy_name(name)
        if bad:
            return bad
        probe = self._kubectl_unchecked("get", "deployment", name, "-o", "name")
        if probe.returncode != 0:
            err = (probe.stderr or probe.stdout or "not found").strip()
            return f"ERROR: deployment {name!r} not found in {self.namespace}: {err}"
        rolled = self._kubectl_unchecked("rollout", "restart", f"deployment/{name}")
        if rolled.returncode != 0:
            err = (rolled.stderr or rolled.stdout or "rollout restart failed").strip()
            return f"ERROR: rollout restart failed for {name!r}: {err}"
        recovery = await self._recovery_after_write(name)
        return (
            f"restarted deployment/{name} in {self.namespace}\n"
            f"{(rolled.stdout or '').strip()}\n"
            f"validate_recovery:\n{recovery}"
        )

    async def scale_deployment(self, name: str, replicas: int) -> str:
        """Low-risk write: scale within 1..3 replicas, then validate_recovery."""
        bad = self._check_deploy_name(name)
        if bad:
            return bad
        try:
            replicas_i = int(replicas)
        except (TypeError, ValueError):
            return f"ERROR: replicas must be an integer, got {replicas!r}"
        if replicas_i < _MIN_REPLICAS or replicas_i > _MAX_REPLICAS:
            return (
                f"ERROR: replicas={replicas_i} out of low-risk range "
                f"{_MIN_REPLICAS}..{_MAX_REPLICAS}"
            )
        probe = self._kubectl_unchecked("get", "deployment", name, "-o", "name")
        if probe.returncode != 0:
            err = (probe.stderr or probe.stdout or "not found").strip()
            return f"ERROR: deployment {name!r} not found in {self.namespace}: {err}"
        scaled = self._kubectl_unchecked(
            "scale", f"deployment/{name}", f"--replicas={replicas_i}"
        )
        if scaled.returncode != 0:
            err = (scaled.stderr or scaled.stdout or "scale failed").strip()
            return f"ERROR: scale failed for {name!r}: {err}"
        recovery = await self._recovery_after_write(name)
        return (
            f"scaled deployment/{name} to replicas={replicas_i} in {self.namespace}\n"
            f"{(scaled.stdout or '').strip()}\n"
            f"validate_recovery:\n{recovery}"
        )

    async def rollback_deployment(self, name: str) -> str:
        """Low-risk write: kubectl rollout undo. Always followed by validate_recovery."""
        bad = self._check_deploy_name(name)
        if bad:
            return bad
        probe = self._kubectl_unchecked("get", "deployment", name, "-o", "name")
        if probe.returncode != 0:
            err = (probe.stderr or probe.stdout or "not found").strip()
            return f"ERROR: deployment {name!r} not found in {self.namespace}: {err}"
        undone = self._kubectl_unchecked("rollout", "undo", f"deployment/{name}")
        if undone.returncode != 0:
            err = (undone.stderr or undone.stdout or "rollout undo failed").strip()
            return f"ERROR: rollout undo failed for {name!r}: {err}"
        recovery = await self._recovery_after_write(name)
        return (
            f"rolled back deployment/{name} in {self.namespace}\n"
            f"{(undone.stdout or '').strip()}\n"
            f"validate_recovery:\n{recovery}"
        )
