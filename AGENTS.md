# Working on this repository

This file is for coding agents changing the Nitaaq Store code. It is not
loaded by the skill.

- Core skill: `plugins/nitaaq-store/skills/nitaaq-store/`. Helpers are Python 3.9+ standard library only.
- After any change: `python3 tools/build.py` and `python3 -m unittest discover -s tests`.
- Team definitions live in `assets/agent-registry.json`. After editing it or a file under `references/agents/`, run `python3 tools/gen_agents.py`; the build fails if generated agents are stale.
- Native agents must stay read-only toward the store: no `mcp__*`, no Write/Edit, no Agent tool, Codex `sandbox_mode = "read-only"`. `registry.validate()` enforces this.
- Merchant-facing text is Saudi/Gulf Arabic; code identifiers stay English.
- Never add a runtime dependency on agency-agents, a paid API, a backend or a database.
- Tests must not touch a real store or network service. Use `tests/mock_merchant_mcp.py`.
- Do not claim production readiness without evidence; update the verification table in README.md instead.
