# ai-engineering-workflow skills

Eleven AI-agent skills: the nine engineering workflow skills from ai-engineering-workflow v0.4, plus `team-next-step` for finding the next action and `team-ux` for standardized UI/UX design packages.

Invoke `$team-next-step` with a feature name or artifact path to get an evidence-based recommendation, the responsible skill/mode or role, and a completion condition. It inspects the existing workflow without changing its state; further execution follows the user's task authorization. It uses the existing team skills and their shared gate checker, so keep those skills available alongside it.

Routing scenarios live in `skills/team-next-step/evals/evals.json`. Evaluate each prompt independently against `SKILL.md`, using the supplied snapshot as fixture evidence and checking its assertions without executing the recommended work.

- `skills/<name>/` = contents of each skill (SKILL.md, agents/, references/, scripts/).
- `skill.zip` packages are NOT committed; rebuild with `cd skills/<name> && zip -r ../../dist/<name>.zip .` when needed.
- The first commit is the unchanged v0.4 originals; later commits are the fixes from the TeamBoard validation report. See `CHANGELOG.md`.
- Scripts shared across skills (`check_upstream_gate.py`, `emit_event.py`, `summarize_metrics.py`, `common-artifact-governance.md`) are intentionally duplicated per skill and must stay byte-identical.
