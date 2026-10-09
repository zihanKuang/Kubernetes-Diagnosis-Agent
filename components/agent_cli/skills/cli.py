"""Offline skill list / show / validate / preview. No model, MCP, or cluster."""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path
from typing import Any, Dict, List, Optional

from .context import build_request_context
from .errors import SkillError
from .models import ObservationSnapshot, ObjectFacts
from .registry import SkillRegistry
from .router import route_post_scan, route_pre_scan
from .settings import resolve_skill_settings
from .workflows import compile_workflow, preview_plan


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="python -m agent_cli.skills",
        description="Citrus Skill registry commands (offline)",
    )
    parser.add_argument("--skill-dir", action="append", default=[], help="Extra local Skill root")
    parser.add_argument("--disable-skill", action="append", default=[], help="Disable a skill id")
    sub = parser.add_subparsers(dest="command", required=True)

    sub.add_parser("list", help="List discovered skills")
    show = sub.add_parser("show", help="Show one skill")
    show.add_argument("skill_id")
    validate = sub.add_parser("validate", help="Validate builtin and extra skill packages")
    validate.add_argument("path", nargs="?", help="Optional extra skill directory")
    preview = sub.add_parser("preview", help="Compile a plan; optional observation JSON")
    preview.add_argument("--query", required=True)
    preview.add_argument("--skill", default="")
    preview.add_argument("--entrypoint", default="cli")
    preview.add_argument("--observation-file", default="")
    preview.add_argument("--engine", default="react")
    return parser


def main(argv: Optional[List[str]] = None) -> int:
    parser = build_parser()
    args = parser.parse_args(argv)
    extra = list(args.skill_dir or [])
    if args.command == "validate" and args.path:
        extra.append(args.path)
    try:
        settings = resolve_skill_settings(
            cli_dirs=extra or None,
            cli_disabled=list(args.disable_skill or []) or None,
        )
        registry = SkillRegistry(
            extra_dirs=settings.extra_dirs,
            disabled=settings.disabled,
        )
        if args.command == "list":
            return _cmd_list(registry)
        if args.command == "show":
            return _cmd_show(registry, args.skill_id)
        if args.command == "validate":
            return _cmd_validate(registry)
        if args.command == "preview":
            return _cmd_preview(registry, args)
    except SkillError as exc:
        print(str(exc), file=sys.stderr)
        return 1
    parser.print_help()
    return 2


def _visible(registry: SkillRegistry):
    return [row for row in registry.list(include_disabled=True) if not row.meta.id.startswith("fragment:")]


def _cmd_list(registry: SkillRegistry) -> int:
    rows = _visible(registry)
    if not rows:
        print("no skills registered")
        return 1
    for row in rows:
        state = "disabled" if row.disabled else "enabled"
        print(
            f"{row.meta.id:20} v{row.meta.version:8} {state:9} "
            f"{row.meta.source:9} workflow={row.meta.workflow_id} "
            f"entry={','.join(row.meta.entrypoints)}"
        )
    return 0


def _cmd_show(registry: SkillRegistry, skill_id: str) -> int:
    row = registry.get(skill_id)
    plan = compile_workflow(row.workflow, registry, skill_id=row.meta.id)
    payload = {
        "id": row.meta.id,
        "version": row.meta.version,
        "source": row.meta.source,
        "enabled": (not row.disabled) and row.meta.enabled,
        "entrypoints": row.meta.entrypoints,
        "workflow_id": row.meta.workflow_id,
        "description": row.meta.description,
        "plan": plan.as_dict(),
    }
    print(json.dumps(payload, indent=2, ensure_ascii=False))
    return 0


def _cmd_validate(registry: SkillRegistry) -> int:
    rows = _visible(registry)
    errors = 0
    for row in rows:
        try:
            compile_workflow(row.workflow, registry, skill_id=row.meta.id)
            print(f"ok  {row.meta.id}@{row.meta.version}")
        except SkillError as exc:
            errors += 1
            print(f"ERR {row.meta.id}: {exc}")
    if errors:
        return 1
    if not rows:
        print("no skills to validate", file=sys.stderr)
        return 1
    return 0


def _cmd_preview(registry: SkillRegistry, args: Any) -> int:
    snapshot = None
    obs = None
    if args.observation_file:
        raw = json.loads(Path(args.observation_file).read_text(encoding="utf-8"))
        obs = _snapshot_from_json(raw)
    request = build_request_context(
        args.query,
        entrypoint=args.entrypoint,
        routing_mode="auto",
        skill_override=args.skill,
        engine=args.engine,
    )
    catalog = ["list_pods", "get_recent_events", "get_pod_status", "get_pod_logs", "validate_recovery"]
    if obs is None:
        decision = route_pre_scan(request, registry, catalog_names=catalog)
        needs_live = True
    else:
        decision = route_post_scan(request, obs, registry, catalog_names=catalog)
        needs_live = False
    skill = registry.get(decision.skill_id)
    payload = preview_plan(skill, registry, snapshot=obs.as_dict() if obs else None)
    payload["decision"] = decision.as_dict()
    payload["needs_live_evidence"] = needs_live or payload["needs_live_evidence"]
    payload["note"] = (
        "Preview does not execute tools. Skill choice after scan still needs live FOCUS evidence."
        if needs_live else payload["note"]
    )
    print(json.dumps(payload, indent=2, ensure_ascii=False))
    return 0


def _snapshot_from_json(raw: Dict[str, Any]) -> ObservationSnapshot:
    focus = []
    for item in raw.get("focus") or []:
        focus.append(ObjectFacts(
            obs_id=str(item.get("obs_id") or item.get("id") or ""),
            name=str(item.get("name") or ""),
            component=str(item.get("component") or ""),
            phase=str(item.get("phase") or ""),
            state=str(item.get("state") or ""),
            ready=item.get("ready"),
            restarts=int(item.get("restarts") or 0),
            present=bool(item.get("present", True)),
            waiting_reason=str(item.get("waiting_reason") or item.get("state") or ""),
            last_termination=str(item.get("last_termination") or ""),
            event_reasons=list(item.get("event_reasons") or []),
            uid=str(item.get("uid") or ""),
            window=str(item.get("window") or ""),
        ))
    return ObservationSnapshot(
        scan_status=str(raw.get("scan_status") or "not_scanned"),
        collected_at=str(raw.get("collected_at") or ""),
        focus=focus,
        context_count=int(raw.get("context_count") or 0),
        observation_ids=list(raw.get("observation_ids") or [item.obs_id for item in focus]),
        list_pods_ok=bool(raw.get("list_pods_ok", raw.get("scan_status") in {"success", "empty"})),
        events_ok=bool(raw.get("events_ok", False)),
    )


if __name__ == "__main__":
    sys.exit(main())
