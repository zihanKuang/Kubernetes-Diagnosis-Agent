# Workspace baseline (T001)

Captured 2026-10-07. No env files or keys.

- Branch: `main`
- HEAD: `344739759d9f17834027777b32ec86c92bc0890c`
- HEAD subject: `feat(rules): add human-readable code guidelines`
- Dirty tree was kept (existing Skills work, diagnosis, observations, infra/faults, plus this bench).
- Not listed: `.env` contents.

Content fingerprints at capture (sha256):

| path | sha256 | bytes |
|------|--------|-------|
| components/agent_cli/prompts.py | 48fe75fed904bc84f96a1afb0371f5fd277a1252eda99b14cd075b5125a3ab59 | 7585 |
| components/agent_cli/agent.py | 9a106246574ba64449bd810ee681d7d8bd014466530307d027aec654bfe84520 | 28299 |
| components/agent_cli/config.py | e56d64d76ec4154442985dc3ab28d18ae8e0a3b98f7aa3de2384fe140d491a87 | 8107 |
| components/agent_cli/skills/runtime.py | e7cd1f1a486a25764f28a128f5ed38496c17abbe5a9f361af1be04b5ad3d6ce3 | 28653 |
| components/agent_cli/skill_bench/catalog.py | a6efaa1f5a8b7788026fb419a2f5f2953a5537cb55ff05ea54e7a2bb305c2bbd | 27302 |
| components/agent_cli/skill_bench/arms.py | 0ec9880f5213cf0c5e5c872f7bdf913529483aae39bf98ac9d73e2c00cef3324 | 4554 |

catalog.py hash will change after the `_recovery` signature fix; re-freeze with `python -m agent_cli.skill_bench freeze`.
