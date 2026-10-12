# Frozen arm A (T004)

Arm A is the **current default diagnostic prompt** `DEFAULT_SRE_SYSTEM_INSTRUCTION`
in `components/agent_cli/prompts.py`, with `skill_routing=legacy` (full tool catalog
to the model, no on-demand Skill bodies).

Arm A is **not** `LEGACY_SRE_SYSTEM_INSTRUCTION`, the shorter historical ablation
prompt selected by `CITRUS_ABLATION=legacy`. Those two strings are fingerprinted
separately in `FINGERPRINTS.json`.

Shared infrastructure used by A/B/C (replay provider, budget consume-before-call,
per-pod status parse, evidence publish path) may change for correctness. A's
*diagnostic strategy* stays that default prompt plus legacy tool offering.
