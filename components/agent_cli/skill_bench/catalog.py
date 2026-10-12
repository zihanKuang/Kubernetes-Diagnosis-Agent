"""Twelve synthetic exam cases. Names do not leak labeled fault classes."""

from __future__ import annotations

from typing import Dict, List

from .clock import COLLECTED_AT, KILL_FIRST, KILL_TIME, STALE_TIME, WINDOW_START
from .models import Case, CaseTruth, ToolFixture

NS = "citrus"


def _pod_line(*, name: str, phase: str, ready: bool, restarts: int, component: str, uid: str, state: str = "", owner: str = "") -> str:
    extra = f" | state={state}" if state and state != "Running" else ""
    owner_text = owner or f"ReplicaSet/{component}-rs"
    return (
        f"- {name} | phase={phase} | ready={ready} | restarts={restarts} | "
        f"component={component} | uid={uid} | ns={NS} | owner={owner_text}{extra}"
    )


def _pods(*lines: str) -> str:
    body = "\n".join(lines)
    return (
        f"Pods in namespace '{NS}' ({len(lines)} total) "
        f"collected_at={COLLECTED_AT} cluster=bench:\n\n{body}"
    )


def _events(*rows: str) -> str:
    header = (
        f"Events in last 10m (namespace={NS}, collected_at={COLLECTED_AT}, "
        f"window_start={WINDOW_START}):\n"
        "TIME is last occurrence (UTC). FIRST is first occurrence. COUNT is the Kubernetes event count.\n"
        "TIME\tTYPE\tREASON\tOBJECT\tMESSAGE\tFIRST\tCOUNT\n"
    )
    return header + "\n".join(rows)


def _status(*, name: str, uid: str, phase: str, ready: bool, restarts: int, state: str, container: str, component: str) -> str:
    return (
        f"Pod: {name}\n"
        f"  Identity: cluster=bench ns={NS} kind=Pod name={name} uid={uid}\n"
        f"  Owner: ReplicaSet/{component}-rs\n"
        f"  Status: {phase}\n"
        f"  Restarts: {restarts}\n"
        f"  Ready: {ready}\n"
        f"  State: {state}\n"
        f"  Container main: {container}\n"
    )


def _recovery(selector: str, status: str, matched: int, ready: int, reason: str) -> str:
    if status == "pass":
        summary = f"PASS: {ready}/{matched} Ready pods (required min_ready=1) for selector '{selector}'"
    elif status == "unknown":
        summary = "UNKNOWN: query failed: replay"
    else:
        summary = f"FAIL: {ready}/{matched} Ready pods (required min_ready=1) for selector '{selector}'"
    return (
        f"{summary}\n"
        f"selector={selector}\nnamespace={NS}\nchecked_at={COLLECTED_AT}\n"
        f"matched={matched}\nready={ready}\nmin_ready=1\nreason={reason}\n"
        "This check only proves Ready at checked_at for the matched objects.\n"
    )


def _obj(name: str, component: str, uid: str, obs: str) -> Dict[str, str]:
    return {
        "name": name,
        "component": component,
        "uid": uid,
        "obs_id": obs,
        "selector": f"app.kubernetes.io/component={component}",
    }


def _case(**kwargs) -> Case:
    kwargs.setdefault("kind", "synthetic")
    kwargs.setdefault("collected_at", COLLECTED_AT)
    kwargs.setdefault("window_start", WINDOW_START)
    kwargs.setdefault("version", "1")
    return Case(**kwargs)


def all_cases() -> List[Case]:
    storefront = _obj("storefront-web-7d9c", "storefront-web", "uid-sf-live", "obs-1")
    storefront_old = _obj("storefront-web-3aa1", "storefront-web", "uid-sf-dead", "obs-2")
    checkout = _obj("payments-api-1f22", "payments-api", "uid-pay-live", "obs-1")
    checkout_old = _obj("payments-api-9bb0", "payments-api", "uid-pay-dead", "obs-2")
    worker_a = _obj("billing-worker-4c1e", "billing-worker", "uid-bill", "obs-1")
    pull_pod = _obj("reports-job-88ae", "reports-job", "uid-rep", "obs-1")
    mem_pod = _obj("cache-node-21b0", "cache-node", "uid-cache", "obs-1")
    probe_pod = _obj("auth-edge-55d2", "auth-edge", "uid-auth", "obs-1")
    pend_pod = _obj("batch-runner-02c8", "batch-runner", "uid-batch", "obs-1")
    mix_old = _obj("storefront-web-old", "storefront-web", "uid-old-sf", "obs-1")
    mix_new = _obj("payments-api-now", "payments-api", "uid-new-pay", "obs-2")
    crash_a = _obj("ingest-a-11", "ingest-a", "uid-ia", "obs-1")
    pull_b = _obj("ingest-b-22", "ingest-b", "uid-ib", "obs-2")
    guess = _obj("auth-edge-55d2", "auth-edge", "uid-auth", "obs-1")

    ready_line = _pod_line(
        name="accounting-ok", phase="Running", ready=True, restarts=1,
        component="accounting", uid="uid-acc",
    )
    return [
        _case(
            case_id="healthy-ready",
            split="dev",
            query="Is anything unhealthy in the citrus namespace right now? If not, say what you checked.",
            objects=[_obj("accounting-ok", "accounting", "uid-acc", "obs-1"), storefront],
            tools={
                "list_pods": ToolFixture(default=_pods(
                    ready_line,
                    _pod_line(name=storefront["name"], phase="Running", ready=True, restarts=0, component="storefront-web", uid=storefront["uid"]),
                )),
                "get_recent_events": ToolFixture(default=_events()),
            },
            truth=CaseTruth(
                focus_names=[],
                required_status=[],
                supported_facts=["current pods Ready"],
                forbidden_facts=["ongoing incident", "OOMKilled"],
                allow_bounded_healthy=True,
            ),
        ),
        _case(
            case_id="storefront-self-heal",
            split="dev",
            query="What just happened to the storefront-web pods in citrus? Short RCA, then validate recovery.",
            objects=[storefront, storefront_old],
            tools={
                "list_pods": ToolFixture(default=_pods(
                    _pod_line(name=storefront["name"], phase="Running", ready=True, restarts=0, component="storefront-web", uid=storefront["uid"]),
                )),
                "get_recent_events": ToolFixture(default=_events(
                    f"{KILL_TIME}\tNormal\tKilling\tPod/{storefront_old['name']}/uid={storefront_old['uid']}\tStopping container\t{KILL_FIRST}\t1",
                    f"{KILL_TIME}\tNormal\tSuccessfulCreate\tReplicaSet/storefront-web-rs/uid=rs-sf\tCreated pod: {storefront['name']}\t{KILL_FIRST}\t1",
                )),
                "get_pod_status": ToolFixture(by_selector={
                    storefront["selector"]: _status(
                        name=storefront["name"], uid=storefront["uid"], phase="Running",
                        ready=True, restarts=0, state="Running", container="running",
                        component="storefront-web",
                    ),
                }),
                "validate_recovery": ToolFixture(by_selector={
                    storefront["selector"]: _recovery(storefront["selector"], "pass", 1, 1, "ready"),
                }),
            },
            truth=CaseTruth(
                focus_names=[storefront_old["name"], storefront["name"]],
                required_status=[storefront["name"]],
                supported_facts=["recent kill", "replacement pod Ready"],
                forbidden_facts=["everything was always healthy", "OOMKilled"],
                require_recent_incident=True,
                recovery_expected="pass",
                recovery_object="storefront-web",
                coverage_objects=["storefront-web"],
            ),
        ),
        _case(
            case_id="crash-exit-unknown",
            split="dev",
            query="Why is the billing-worker workload in citrus unhealthy? Short RCA, then validate recovery.",
            objects=[worker_a],
            tools={
                "list_pods": ToolFixture(default=_pods(
                    _pod_line(name=worker_a["name"], phase="Running", ready=False, restarts=4, component="billing-worker", uid=worker_a["uid"], state="CrashLoopBackOff"),
                )),
                "get_recent_events": ToolFixture(default=_events(
                    f"{KILL_TIME}\tWarning\tBackOff\tPod/{worker_a['name']}/uid={worker_a['uid']}\tBack-off restarting failed container\t{KILL_FIRST}\t6",
                )),
                "get_pod_status": ToolFixture(by_selector={
                    worker_a["selector"]: _status(
                        name=worker_a["name"], uid=worker_a["uid"], phase="Running",
                        ready=False, restarts=4, state="CrashLoopBackOff",
                        container="waiting=CrashLoopBackOff last_termination=Error(exit=1)",
                        component="billing-worker",
                    ),
                }),
                "get_pod_logs": ToolFixture(by_selector={
                    worker_a["selector"]: "Error fetching logs: container is not running",
                }),
                "validate_recovery": ToolFixture(by_selector={
                    worker_a["selector"]: _recovery(worker_a["selector"], "fail", 1, 0, "not_ready"),
                }),
            },
            truth=CaseTruth(
                focus_names=[worker_a["name"]],
                required_status=[worker_a["name"]],
                supported_facts=["CrashLoopBackOff", "exit 1"],
                forbidden_facts=["OOMKilled", "ImagePullBackOff"],
                allow_unknown_cause=True,
                recovery_expected="fail",
                recovery_object="billing-worker",
                coverage_objects=["billing-worker"],
            ),
        ),
        _case(
            case_id="image-pull",
            split="dev",
            query="Why is the reports-job workload in citrus not starting? Short RCA, then validate recovery.",
            objects=[pull_pod],
            tools={
                "list_pods": ToolFixture(default=_pods(
                    _pod_line(name=pull_pod["name"], phase="Pending", ready=False, restarts=0, component="reports-job", uid=pull_pod["uid"], state="ImagePullBackOff"),
                )),
                "get_recent_events": ToolFixture(default=_events(
                    f"{KILL_TIME}\tWarning\tFailed\tPod/{pull_pod['name']}/uid={pull_pod['uid']}\tFailed to pull image registry.example.invalid/reports:missing\t{KILL_FIRST}\t4",
                )),
                "get_pod_status": ToolFixture(by_selector={
                    pull_pod["selector"]: _status(
                        name=pull_pod["name"], uid=pull_pod["uid"], phase="Pending",
                        ready=False, restarts=0, state="ImagePullBackOff",
                        container="waiting=ImagePullBackOff (Failed to pull image)",
                        component="reports-job",
                    ),
                }),
                "get_pod_logs": ToolFixture(by_selector={
                    pull_pod["selector"]: "Error fetching logs: container is not running",
                }),
                "validate_recovery": ToolFixture(by_selector={
                    pull_pod["selector"]: _recovery(pull_pod["selector"], "fail", 1, 0, "not_ready"),
                }),
            },
            truth=CaseTruth(
                focus_names=[pull_pod["name"]],
                required_status=[pull_pod["name"]],
                supported_facts=["ImagePullBackOff", "failed to pull image"],
                forbidden_facts=["application crash", "OOMKilled"],
                recovery_expected="fail",
                recovery_object="reports-job",
                coverage_objects=["reports-job"],
            ),
        ),
        _case(
            case_id="memory-kill",
            split="dev",
            query="What is wrong with the cache-node workload in citrus? Short RCA, then validate recovery.",
            objects=[mem_pod],
            tools={
                "list_pods": ToolFixture(default=_pods(
                    _pod_line(name=mem_pod["name"], phase="Running", ready=False, restarts=3, component="cache-node", uid=mem_pod["uid"], state="CrashLoopBackOff"),
                )),
                "get_recent_events": ToolFixture(default=_events(
                    f"{KILL_TIME}\tWarning\tBackOff\tPod/{mem_pod['name']}/uid={mem_pod['uid']}\tBack-off restarting failed container\t{KILL_FIRST}\t3",
                )),
                "get_pod_status": ToolFixture(by_selector={
                    mem_pod["selector"]: _status(
                        name=mem_pod["name"], uid=mem_pod["uid"], phase="Running",
                        ready=False, restarts=3, state="OOMKilled",
                        container="waiting=CrashLoopBackOff last_termination=OOMKilled(exit=137)",
                        component="cache-node",
                    ) + "\n  Note: last termination is OOMKilled. CrashLoopBackOff here is the restart backoff after the OOM kill, not a separate process bug.",
                }),
                "get_pod_logs": ToolFixture(by_selector={
                    mem_pod["selector"]: "Error fetching logs: container is not running",
                }),
                "validate_recovery": ToolFixture(by_selector={
                    mem_pod["selector"]: _recovery(mem_pod["selector"], "fail", 1, 0, "not_ready"),
                }),
            },
            truth=CaseTruth(
                focus_names=[mem_pod["name"]],
                required_status=[mem_pod["name"]],
                supported_facts=["OOMKilled", "CrashLoopBackOff waiting"],
                forbidden_facts=["image pull"],
                recovery_expected="fail",
                recovery_object="cache-node",
                coverage_objects=["cache-node"],
            ),
        ),
        _case(
            case_id="probe-unready",
            split="dev",
            query="Why is the auth-edge workload in citrus not Ready? Short RCA, then validate recovery.",
            objects=[probe_pod],
            tools={
                "list_pods": ToolFixture(default=_pods(
                    _pod_line(name=probe_pod["name"], phase="Running", ready=False, restarts=0, component="auth-edge", uid=probe_pod["uid"], state="RunningNotReady"),
                )),
                "get_recent_events": ToolFixture(default=_events(
                    f"{KILL_TIME}\tWarning\tUnhealthy\tPod/{probe_pod['name']}/uid={probe_pod['uid']}\tReadiness probe failed: cat: /tmp/healthy: No such file\t{KILL_FIRST}\t8",
                )),
                "get_pod_status": ToolFixture(by_selector={
                    probe_pod["selector"]: _status(
                        name=probe_pod["name"], uid=probe_pod["uid"], phase="Running",
                        ready=False, restarts=0, state="RunningNotReady",
                        container="running",
                        component="auth-edge",
                    ) + "\n  Note: the process is running and has not restarted, but it is not Ready.",
                }),
                "get_pod_logs": ToolFixture(by_selector={
                    probe_pod["selector"]: "listening on :8080\n",
                }),
                "validate_recovery": ToolFixture(by_selector={
                    probe_pod["selector"]: _recovery(probe_pod["selector"], "fail", 1, 0, "not_ready"),
                }),
            },
            truth=CaseTruth(
                focus_names=[probe_pod["name"]],
                required_status=[probe_pod["name"]],
                supported_facts=["RunningNotReady", "readiness probe", "zero restarts"],
                forbidden_facts=["OOMKilled", "CrashLoopBackOff"],
                recovery_expected="fail",
                recovery_object="auth-edge",
                coverage_objects=["auth-edge"],
            ),
        ),
        _case(
            case_id="unschedulable",
            split="dev",
            query="Why is the batch-runner workload in citrus not running? Short RCA, then validate recovery.",
            objects=[pend_pod],
            tools={
                "list_pods": ToolFixture(default=_pods(
                    _pod_line(name=pend_pod["name"], phase="Pending", ready=False, restarts=0, component="batch-runner", uid=pend_pod["uid"], state="Unschedulable"),
                )),
                "get_recent_events": ToolFixture(default=_events(
                    f"{KILL_TIME}\tWarning\tFailedScheduling\tPod/{pend_pod['name']}/uid={pend_pod['uid']}\t0/1 nodes are available: 1 node(s) didn't match Pod's node affinity/selector\t{KILL_FIRST}\t5",
                )),
                "get_pod_status": ToolFixture(by_selector={
                    pend_pod["selector"]: _status(
                        name=pend_pod["name"], uid=pend_pod["uid"], phase="Pending",
                        ready=False, restarts=0, state="Unschedulable",
                        container="no container state",
                        component="batch-runner",
                    ) + "\n  PodScheduled: False — Unschedulable — nodeSelector matches nothing",
                }),
                "get_pod_logs": ToolFixture(by_selector={
                    pend_pod["selector"]: "Error fetching logs: container is not running",
                }),
                "validate_recovery": ToolFixture(by_selector={
                    pend_pod["selector"]: _recovery(pend_pod["selector"], "fail", 1, 0, "not_ready"),
                }),
            },
            truth=CaseTruth(
                focus_names=[pend_pod["name"]],
                required_status=[pend_pod["name"]],
                supported_facts=["Pending", "FailedScheduling", "nodeSelector"],
                forbidden_facts=["ImagePullBackOff", "OOMKilled"],
                recovery_expected="fail",
                recovery_object="batch-runner",
                coverage_objects=["batch-runner"],
            ),
        ),
        _case(
            case_id="mixed-stale-and-fresh",
            split="dev",
            query="What just happened to the payments-api pods in citrus? Short RCA, then validate recovery.",
            objects=[mix_old, mix_new],
            tools={
                "list_pods": ToolFixture(default=_pods(
                    _pod_line(name=mix_new["name"], phase="Running", ready=True, restarts=0, component="payments-api", uid=mix_new["uid"]),
                )),
                "get_recent_events": ToolFixture(default=_events(
                    f"{STALE_TIME}\tNormal\tKilling\tPod/{mix_old['name']}/uid={mix_old['uid']}\tStopping container\t{STALE_TIME}\t1",
                    f"{KILL_TIME}\tNormal\tKilling\tPod/payments-api-gone/uid=uid-pay-gone\tStopping container\t{KILL_FIRST}\t1",
                    f"{KILL_TIME}\tNormal\tSuccessfulCreate\tReplicaSet/payments-api-rs/uid=rs-pay\tCreated pod: {mix_new['name']}\t{KILL_FIRST}\t1",
                )),
                "get_pod_status": ToolFixture(by_selector={
                    mix_new["selector"]: _status(
                        name=mix_new["name"], uid=mix_new["uid"], phase="Running",
                        ready=True, restarts=0, state="Running", container="running",
                        component="payments-api",
                    ),
                }),
                "validate_recovery": ToolFixture(by_selector={
                    mix_new["selector"]: _recovery(mix_new["selector"], "pass", 1, 1, "ready"),
                }),
            },
            truth=CaseTruth(
                focus_names=[mix_new["name"]],
                required_status=[mix_new["name"]],
                supported_facts=["recent payments-api kill", "current replacement Ready"],
                forbidden_facts=["storefront-web current incident", "same incident as stale storefront"],
                require_recent_incident=True,
                recovery_expected="pass",
                recovery_object="payments-api",
                coverage_objects=["payments-api"],
            ),
        ),
        _case(
            case_id="checkout-self-heal",
            split="holdout",
            query="What just happened to the payments-api pods in citrus? Short RCA, then validate recovery.",
            objects=[checkout, checkout_old],
            tools={
                "list_pods": ToolFixture(default=_pods(
                    _pod_line(name=checkout["name"], phase="Running", ready=True, restarts=0, component="payments-api", uid=checkout["uid"]),
                )),
                "get_recent_events": ToolFixture(default=_events(
                    f"{KILL_TIME}\tNormal\tKilling\tPod/{checkout_old['name']}/uid={checkout_old['uid']}\tStopping container\t{KILL_FIRST}\t1",
                    f"{KILL_TIME}\tNormal\tSuccessfulCreate\tReplicaSet/payments-api-rs/uid=rs-pay\tCreated pod: {checkout['name']}\t{KILL_FIRST}\t1",
                )),
                "get_pod_status": ToolFixture(by_selector={
                    checkout["selector"]: _status(
                        name=checkout["name"], uid=checkout["uid"], phase="Running",
                        ready=True, restarts=0, state="Running", container="running",
                        component="payments-api",
                    ),
                }),
                "validate_recovery": ToolFixture(by_selector={
                    checkout["selector"]: _recovery(checkout["selector"], "pass", 1, 1, "ready"),
                }),
            },
            truth=CaseTruth(
                focus_names=[checkout_old["name"], checkout["name"]],
                required_status=[checkout["name"]],
                supported_facts=["recent kill", "replacement Ready"],
                forbidden_facts=["storefront-web"],
                require_recent_incident=True,
                recovery_expected="pass",
                recovery_object="payments-api",
                coverage_objects=["payments-api"],
            ),
        ),
        _case(
            case_id="multi-fault",
            split="holdout",
            query="Why are the ingest workloads in citrus unhealthy? Short RCA, then validate recovery.",
            objects=[crash_a, pull_b],
            tools={
                "list_pods": ToolFixture(default=_pods(
                    _pod_line(name=crash_a["name"], phase="Running", ready=False, restarts=5, component="ingest-a", uid=crash_a["uid"], state="CrashLoopBackOff"),
                    _pod_line(name=pull_b["name"], phase="Pending", ready=False, restarts=0, component="ingest-b", uid=pull_b["uid"], state="ImagePullBackOff"),
                )),
                "get_recent_events": ToolFixture(default=_events(
                    f"{KILL_TIME}\tWarning\tBackOff\tPod/{crash_a['name']}/uid={crash_a['uid']}\tBack-off restarting failed container\t{KILL_FIRST}\t5",
                    f"{KILL_TIME}\tWarning\tFailed\tPod/{pull_b['name']}/uid={pull_b['uid']}\tFailed to pull image\t{KILL_FIRST}\t3",
                )),
                "get_pod_status": ToolFixture(by_selector={
                    crash_a["selector"]: _status(
                        name=crash_a["name"], uid=crash_a["uid"], phase="Running",
                        ready=False, restarts=5, state="CrashLoopBackOff",
                        container="waiting=CrashLoopBackOff last_termination=Error(exit=1)",
                        component="ingest-a",
                    ),
                    pull_b["selector"]: _status(
                        name=pull_b["name"], uid=pull_b["uid"], phase="Pending",
                        ready=False, restarts=0, state="ImagePullBackOff",
                        container="waiting=ImagePullBackOff",
                        component="ingest-b",
                    ),
                }),
                "get_pod_logs": ToolFixture(default="Error fetching logs: container is not running"),
                "validate_recovery": ToolFixture(default=_recovery("ingest", "fail", 2, 0, "not_ready")),
            },
            truth=CaseTruth(
                focus_names=[crash_a["name"], pull_b["name"]],
                required_status=[crash_a["name"], pull_b["name"]],
                supported_facts=["CrashLoopBackOff on ingest-a", "ImagePullBackOff on ingest-b"],
                forbidden_facts=["single fault class for both"],
                allow_unknown_cause=True,
                recovery_expected="fail",
                coverage_objects=["ingest-a", "ingest-b"],
            ),
        ),
        _case(
            case_id="user-guessed-oom",
            split="holdout",
            query="The auth-edge pod looks like an OOM. Confirm the OOM and validate recovery.",
            objects=[guess],
            tools={
                "list_pods": ToolFixture(default=_pods(
                    _pod_line(name=guess["name"], phase="Running", ready=False, restarts=0, component="auth-edge", uid=guess["uid"], state="RunningNotReady"),
                )),
                "get_recent_events": ToolFixture(default=_events(
                    f"{KILL_TIME}\tWarning\tUnhealthy\tPod/{guess['name']}/uid={guess['uid']}\tReadiness probe failed\t{KILL_FIRST}\t4",
                )),
                "get_pod_status": ToolFixture(by_selector={
                    guess["selector"]: _status(
                        name=guess["name"], uid=guess["uid"], phase="Running",
                        ready=False, restarts=0, state="RunningNotReady",
                        container="running",
                        component="auth-edge",
                    ),
                }),
                "get_pod_logs": ToolFixture(by_selector={
                    guess["selector"]: "ready check missed\n",
                }),
                "validate_recovery": ToolFixture(by_selector={
                    guess["selector"]: _recovery(guess["selector"], "fail", 1, 0, "not_ready"),
                }),
            },
            truth=CaseTruth(
                focus_names=[guess["name"]],
                required_status=[guess["name"]],
                supported_facts=["RunningNotReady", "readiness probe"],
                forbidden_facts=["OOMKilled"],
                recovery_expected="fail",
                recovery_object="auth-edge",
                coverage_objects=["auth-edge"],
            ),
        ),
        _case(
            case_id="scan-unavailable",
            split="holdout",
            query="Is anything unhealthy in the citrus namespace right now?",
            objects=[],
            tools={
                "list_pods": ToolFixture(error="ERROR: timed out talking to apiserver"),
                "get_recent_events": ToolFixture(error="ERROR: timed out talking to apiserver"),
            },
            truth=CaseTruth(
                focus_names=[],
                required_status=[],
                supported_facts=["scan failed"],
                forbidden_facts=["namespace is healthy"],
                allow_unknown_cause=True,
                recovery_expected="none",
            ),
        ),
    ]


def by_id() -> Dict[str, Case]:
    return {item.case_id: item for item in all_cases()}


def dev_ids() -> List[str]:
    return [item.case_id for item in all_cases() if item.split == "dev"]


def holdout_ids() -> List[str]:
    return [item.case_id for item in all_cases() if item.split == "holdout"]
