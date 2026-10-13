# ai-engineering-workflow skills

Nine AI-agent skills (team-requirements → team-release) from ai-engineering-workflow v0.4.

- `skills/<name>/` = contents of each skill (SKILL.md, agents/, references/, scripts/).
- `skill.zip` packages are NOT committed; rebuild with `cd skills/<name> && zip -r ../../dist/<name>.zip .` when needed.
- The first commit is the unchanged v0.4 originals; later commits are the fixes from the TeamBoard validation report. See `CHANGELOG.md`.
- Scripts shared across skills (`check_upstream_gate.py`, `emit_event.py`, `summarize_metrics.py`, `common-artifact-governance.md`) are intentionally duplicated per skill and must stay byte-identical.
