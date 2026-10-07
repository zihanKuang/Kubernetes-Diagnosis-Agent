"""Pod status formatting — no cluster. Fixture JSON stands in for kubectl."""
import asyncio
import json
import subprocess

from mcp_server.tools.kubernetes import (
    KubernetesTools,
    event_times,
    format_event_object,
    format_pod_list_line,
    format_pod_status,
    involved_object,
    object_identity,
    owner_refs,
    recovery_note,
    summarize_pod_state,
)


def _pod(name, phase, containers, conditions=None, labels=None):
    return {
        "metadata": {
            "name": name,
            "labels": labels or {"app.kubernetes.io/component": name},
        },
        "status": {
            "phase": phase,
            "conditions": conditions or [],
            "containerStatuses": containers,
        },
    }


def test_healthy_list_line_omits_state():
    pod = _pod(
        "frontend-abc",
        "Running",
        [{"name": "frontend", "ready": True, "restartCount": 0, "state": {"running": {"startedAt": "t"}}}],
    )
    line = format_pod_list_line(pod)
    assert "phase=Running" in line
    assert "ready=True" in line
    assert "state=" not in line
    assert summarize_pod_state(pod) == "Running"


def test_crashloop_names_waiting_reason_and_exit_code():
    pod = _pod(
        "citrus-crashloop-abc",
        "Running",
        [
            {
                "name": "crashloop",
                "ready": False,
                "restartCount": 4,
                "state": {
                    "waiting": {
                        "reason": "CrashLoopBackOff",
                        "message": "back-off 20s restarting failed container",
                    }
                },
                "lastState": {
                    "terminated": {"reason": "Error", "exitCode": 1},
                },
            }
        ],
    )
    text = format_pod_status(pod)
    assert summarize_pod_state(pod) == "CrashLoopBackOff"
    assert "state=CrashLoopBackOff" in format_pod_list_line(pod)
    assert "waiting=CrashLoopBackOff" in text
    assert "last_termination=Error exit=1" in text
    assert "OOMKilled" not in text


def test_oom_wins_over_later_crashloop_backoff():
    """kubectl JSON is camelCase. The client uses snake_case; both must name OOM."""
    camel = _pod(
        "citrus-oom-abc",
        "Running",
        [
            {
                "name": "oom",
                "ready": False,
                "restartCount": 3,
                "state": {
                    "waiting": {
                        "reason": "CrashLoopBackOff",
                        "message": "back-off 20s restarting failed container",
                    }
                },
                "lastState": {"terminated": {"reason": "OOMKilled", "exitCode": 137}},
            }
        ],
    )
    snake = {
        "metadata": {"name": "citrus-oom-abc", "labels": {}},
        "status": {
            "phase": "Running",
            "container_statuses": [
                {
                    "name": "oom",
                    "ready": False,
                    "restart_count": 3,
                    "state": {"waiting": {"reason": "CrashLoopBackOff", "message": "back-off"}},
                    "last_state": {"terminated": {"reason": "OOMKilled", "exit_code": 137}},
                }
            ],
        },
    }
    for pod in (camel, snake):
        text = format_pod_status(pod)
        assert summarize_pod_state(pod) == "OOMKilled"
        assert "last_termination=OOMKilled exit=137" in text
        assert "not a separate process bug" in text


def test_imagepull_surfaces_the_image_message():
    pod = _pod(
        "citrus-imagepull-abc",
        "Pending",
        [
            {
                "name": "imagepull",
                "ready": False,
                "restartCount": 0,
                "state": {
                    "waiting": {
                        "reason": "ImagePullBackOff",
                        "message": 'Back-off pulling image "registry.invalid/citrus/missing:no-such-tag"',
                    }
                },
            }
        ],
    )
    text = format_pod_status(pod)
    assert summarize_pod_state(pod) == "ImagePullBackOff"
    assert "registry.invalid/citrus/missing:no-such-tag" in text
    assert "state=ImagePullBackOff" in format_pod_list_line(pod)


def test_running_not_ready_with_zero_restarts_points_at_a_probe():
    pod = _pod(
        "citrus-probe-abc",
        "Running",
        [
            {
                "name": "probe",
                "ready": False,
                "restartCount": 0,
                "state": {"running": {"startedAt": "t"}},
            }
        ],
        conditions=[
            {
                "type": "Ready",
                "status": "False",
                "reason": "ContainersNotReady",
                "message": "containers with unready status: [probe]",
            }
        ],
    )
    text = format_pod_status(pod)
    assert summarize_pod_state(pod) == "RunningNotReady"
    assert "readiness or liveness probe" in text
    assert "ContainersNotReady" in text
    assert "OOMKilled" not in text


def test_unschedulable_pending_pod_has_no_container_status():
    pod = _pod(
        "citrus-unschedulable-abc",
        "Pending",
        [],
        conditions=[
            {
                "type": "PodScheduled",
                "status": "False",
                "reason": "Unschedulable",
                "message": "0/1 nodes are available: 1 node(s) didn't match Pod's node affinity/selector.",
            }
        ],
    )
    text = format_pod_status(pod)
    assert summarize_pod_state(pod) == "Unschedulable"
    assert "didn't match" in text
    assert "state=Unschedulable" in format_pod_list_line(pod)
    assert "ready=False" in format_pod_list_line(pod)


def test_recovery_note_does_not_claim_lasting_health():
    healed = recovery_note(
        True,
        2,
        min_ready=1,
        matched=1,
        ready_count=1,
        checked_at="2026-10-06T18:00:00+00:00",
    )
    assert "only proves Ready at checked_at" in healed
    assert "lasting-health" in healed
    assert "ReplicaSet self-heal restored Ready capacity" not in healed
    assert "Elevated restart counts" in healed
    stuck = recovery_note(False, 4, min_ready=1, matched=1, ready_count=0, checked_at="t")
    assert "FAIL" in stuck
    assert "CrashLoopBackOff" in stuck
    instant = recovery_note(True, 0, min_ready=1, matched=1, ready_count=1, checked_at="t")
    assert instant
    assert "lasting-health" in instant
    partial = recovery_note(True, 0, min_ready=1, matched=3, ready_count=1, checked_at="t")
    assert "min_ready=1 passing does not mean every replica is Ready" in partial


def test_camel_and_snake_identity_and_owner_match():
    camel = {
        "metadata": {
            "name": "web-abc",
            "namespace": "citrus",
            "uid": "uid-1",
            "labels": {"app.kubernetes.io/component": "web"},
            "ownerReferences": [{"kind": "ReplicaSet", "name": "web-rs", "uid": "rs-1"}],
        },
        "status": {
            "phase": "Running",
            "containerStatuses": [
                {"name": "c", "ready": True, "restartCount": 0, "state": {"running": {}}}
            ],
        },
    }
    snake = {
        "metadata": {
            "name": "web-abc",
            "namespace": "citrus",
            "uid": "uid-1",
            "labels": {"app.kubernetes.io/component": "web"},
            "owner_references": [{"kind": "ReplicaSet", "name": "web-rs", "uid": "rs-1"}],
        },
        "status": {
            "phase": "Running",
            "container_statuses": [
                {"name": "c", "ready": True, "restart_count": 0, "state": {"running": {}}}
            ],
        },
    }
    assert object_identity(camel)["uid"] == object_identity(snake)["uid"] == "uid-1"
    assert owner_refs(camel) == owner_refs(snake)
    line = format_pod_list_line(camel, namespace="citrus", cluster="local")
    assert "uid=uid-1" in line
    assert "ns=citrus" in line
    assert "owner=ReplicaSet/web-rs uid=rs-1" in line
    assert "uid=uid-1" in format_pod_list_line(snake)
    status = format_pod_status(camel, namespace="citrus", cluster="local")
    assert "kind=Pod name=web-abc uid=uid-1" in status
    assert "Owner: ReplicaSet/web-rs uid=rs-1" in status


def test_event_times_normalize_camel_snake_and_timezone():
    camel = {
        "lastTimestamp": "2026-10-06T18:23:34Z",
        "firstTimestamp": "2026-10-06T18:23:30Z",
        "count": 2,
        "involvedObject": {"kind": "Pod", "name": "web-1", "uid": "u1", "namespace": "citrus"},
    }
    snake = {
        "last_timestamp": "2026-10-06T18:23:34+00:00",
        "first_timestamp": "2026-10-06T18:23:30+00:00",
        "count": 2,
        "involved_object": {"kind": "Pod", "name": "web-1", "uid": "u1", "namespace": "citrus"},
    }
    a = event_times(camel)
    b = event_times(snake)
    assert a["last"] == b["last"]
    assert a["first"] == b["first"]
    assert a["count"] == b["count"] == 2
    assert involved_object(camel) == involved_object(snake)
    assert format_event_object(camel) == "Pod/web-1 uid=u1"


def _tools():
    return KubernetesTools(namespace="citrus", use_kubectl=True)


def test_validate_recovery_no_pods_is_fail_not_query_error():
    tools = _tools()
    tools._kubectl = lambda *a: json.dumps({"items": []})
    text = asyncio.run(tools.validate_recovery("app.kubernetes.io/component=web"))
    assert "status=fail" in text
    assert "reason=no_matching_objects" in text
    assert "scope=readiness_at_check_time" in text
    assert "UNKNOWN" not in text.splitlines()[0]


def test_validate_recovery_query_error_is_unknown():
    tools = _tools()

    def boom(*_a):
        raise subprocess.CalledProcessError(1, "kubectl", stderr="timeout")

    tools._kubectl = boom
    text = asyncio.run(tools.validate_recovery("app.kubernetes.io/component=web"))
    assert text.startswith("UNKNOWN")
    assert "status=unknown" in text
    assert "reason=query_failed" in text
    assert "not a Ready/not-Ready verdict" in text


def test_validate_recovery_pass_is_ready_now_not_lasting():
    tools = _tools()
    tools._kubectl = lambda *a: json.dumps({
        "items": [
            {
                "metadata": {
                    "name": "web-a",
                    "uid": "uid-a",
                    "namespace": "citrus",
                    "labels": {"app.kubernetes.io/component": "web"},
                },
                "status": {
                    "phase": "Running",
                    "containerStatuses": [
                        {"name": "c", "ready": True, "restartCount": 3, "state": {"running": {}}}
                    ],
                },
            },
            {
                "metadata": {
                    "name": "web-b",
                    "uid": "uid-b",
                    "namespace": "citrus",
                    "labels": {"app.kubernetes.io/component": "web"},
                },
                "status": {
                    "phase": "Running",
                    "containerStatuses": [
                        {"name": "c", "ready": True, "restartCount": 0, "state": {"running": {}}}
                    ],
                },
            },
        ]
    })
    text = asyncio.run(tools.validate_recovery("app.kubernetes.io/component=web", min_ready=1))
    assert text.startswith("PASS")
    assert "status=pass" in text
    assert "reason=ready_at_check_time" in text
    assert "uid=uid-a" in text
    assert "only proves Ready at checked_at" in text
    assert "min_ready=1 passing does not mean every replica is Ready" in text
    assert "ReplicaSet self-heal restored Ready capacity" not in text
