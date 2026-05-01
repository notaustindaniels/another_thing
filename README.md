# parallax-engine

Commercial Python engine that generates 2.5D multiplane parallax animation
MP4s from natural-language briefs. Aesthetic target: classic Disney
multiplane (Bambi/Pinocchio) with high-speed FPV drone camera motion.

## Naming — read this first

Three things, three names. They're not interchangeable.

| Name | What it is |
|---|---|
| **`parallax-engine`** | The engine itself. Python package, CLI, renderer, director tier, agent harness. The thing the build produces. |
| **`parallax-skill`** | The Anthropic Skill wrapper. A thin `SKILL.md` + `scripts/run.sh` shim that lets Claude (in claude.ai or via the API) invoke `parallax-engine` from a user brief. |
| **`parallax-skill/` directory** | The repo root you unpacked from the tarball. Contains the spec, the engine package once built, the build harness, the validators, the eventual Skill wrapper. The directory name was chosen before the engine/Skill split was clarified; renaming on disk would invalidate the tarball, so it stays. |

The build produces both the engine and the Skill wrapper. They are
separate deliverables that ship together.

## Status

**Pre-build.** This directory contains the specification, the autonomous
build harness, the validation tools, and the prompts. Running the harness
is what produces `parallax_engine/` (the Python package).

```
parallax-skill/                       (the build directory you unpacked)
├── SPEC.md                           the canonical spec (2,400+ lines)
├── prompts/                          prompts for the autonomous build agent
│   ├── initializer_prompt.md         session 1
│   └── coding_prompt.md              sessions 2+
├── tools/                            validators (DO NOT MODIFY during build)
│   ├── validate_scaffold.py          Phase 1 sanity
│   ├── validate_projection.py        Phase 1 projection math
│   ├── validate_licensing.py         every phase — licensing gate
│   └── validate_portal_equivalence.py  Phase 2 architectural gate
├── runner/                           long-running build harness (Layer 1)
│   ├── autonomous_runner.py          the entry point
│   ├── security.py                   bash command validator
│   ├── budget.py                     token tracking, OAuth-aware
│   ├── integrity.py                  post-session schema enforcement
│   └── agent.py / client.py / progress.py / prompts.py
├── references/                       read-only reference materials
│   └── portal_mechanic.md            the only insight from the prior repo
├── parallax_engine/                  (empty — the agent populates this; Layer 2)
├── tests/                            (empty — the agent populates this)
├── workspace/                        runtime artifacts (state, scenes, frames)
└── evidence/                         per-milestone proof of validation
```

The `parallax-skill/` Skill wrapper directory (Layer 3) gets created during
Phase 5 of the build, inside the repo root. It contains only `SKILL.md` and
`scripts/run.sh`.

## To run the autonomous build

See `SETUP.md` for the full setup. Short version:

```bash
# Auth (Max plan)
export CLAUDE_CODE_OAUTH_TOKEN='your-token'

# Install harness deps
pip install claude-code-sdk pydantic pyyaml

# References — drone-FPV reference videos define the canonical aesthetic:
#   references/sources/polyfjord_drone.mp4    (or your preferred drone reference)
#   references/sources/heygen_parallax.mp4    (optional second reference)
#   references/transcripts/polyfjord.txt      (optional, recommended)
# Phase 2 uses synthetic reference equivalence, so no prior_portal.mp4 needed.

# Launch
python -m runner.autonomous_runner --project-dir . --wallclock-hours 72
```

The runner is resumable — if you Ctrl+C, just run the same command again.

## Cost

On Max plan with `CLAUDE_CODE_OAUTH_TOKEN`: zero marginal cost. The build
draws from your plan's rolling-window and weekly limits; the harness
displays an estimated API-equivalent cost for visibility but does not
enforce it.

On API auth (`ANTHROPIC_API_KEY`): pay-as-you-go. Default cap $1,500
across the full build; override with `--budget-usd`.

Estimated 40–80 sessions end-to-end across 6 phases.

## Specification

`SPEC.md` is canonical. When code, prompts, or this README contradict it,
the spec wins. The agent is forbidden from modifying it.

The naming-conventions table in §1.2 of SPEC.md is the authoritative
reference for `parallax-engine` vs `parallax-skill`.
