---
name: parallax-video
description: |
  Generate 2.5D multiplane camera animations as MP4 from a written brief.
  Use whenever the user asks for a parallax animation, layered 2D scene
  video, drone-FPV-through-illustration flythrough, biome-reveal explainer,
  After-Effects-style 2.5D parallax video, masked layer transition, or
  portal transition between two illustrated worlds. Even when the user
  doesn't say "parallax" — invoke this skill for any request that
  describes stacked illustrated layers with a moving camera, a multiplane-
  camera-like flythrough, or a transition that reveals one scene through
  a shape cut out of another.
---

# Parallax Video Skill

Generates an MP4 from a written brief. The skill invokes a multi-agent
harness that designs the scene, generates SVG assets, plans the camera
path, renders deterministically, and runs a QA loop.

## Usage

1. If the user has not yet provided one, ask for a brief covering: target
   duration, visual style references (forest, city, surreal, etc.), the
   kind of motion (drone-FPV, cinematic pan, biome reveals, portal),
   and any narrative beats. Write the brief to `workspace/brief.md`.
2. Invoke the harness:
   ```bash
   bash scripts/run.sh ./workspace
   ```
3. Stream progress to the user. The harness prints one line per phase.
4. The final MP4 is at `workspace/out.mp4`.

## Notes

- The harness spawns its own multi-agent system; do not delegate to other
  Anthropic features (Research, etc.) for this work.
- Default budget cap is $2.50/render. Override with
  `bash scripts/run.sh ./workspace --budget 5.00`.
- If the harness fails three QA passes, it emits the best partial result
  and a list of residual issues. Surface those to the user verbatim.

## Examples

- "Make a 10-second drone flythrough of a redwood forest at golden hour"
- "Create a 2.5D explainer that travels through 4 biomes (mountain, river,
  city, desert)"
- "Show a portal in a tree opening into a neon city"

---

## Director-Era Flags (Phase 4.5+)

The following flags are available after the director tier is active. Most
users will never need them — the defaults are correct for typical briefs.

### `--resume`

Resume a previous run without re-running the director.

The harness diffs `workspace/storyboard.yaml` against the cached version
from the last run. Only scenes that changed (or were added/removed) are
re-designed. Use this when you have tweaked the brief slightly and want
to avoid paying the full director cost again.

```bash
bash scripts/run.sh ./workspace --resume
```

When to use: the brief was lightly edited (tone tweak, duration change);
the broad narrative arc is unchanged.

When NOT to use: a major structural change (new characters, different
number of scenes, different arc structure). For major changes, a full
re-run gives better results.

### `--budget TIER`

Select the cost tier. Determines which Claude model tier the director
and scene-designer use, and sets the dollar cap.

| Tier | Director model | Scene-designer model | Cap |
|------|---------------|---------------------|-----|
| `thrift` | Sonnet 4.6 | Haiku 4.5 | $1.00 |
| `standard` (default) | Opus 4.7 | Sonnet 4.6 | $2.50 |
| `premium` | Opus 4.7 | Opus 4.7 | $5.00 |
| `longform` | Opus 4.7 (decomposed) | Sonnet 4.6 | $5.00 |

```bash
bash scripts/run.sh ./workspace --budget thrift
bash scripts/run.sh ./workspace --budget premium
bash scripts/run.sh ./workspace --budget longform
```

`longform` enables decomposed-mode director for briefs >= 60 s; the
director calls the breakdown, visual vocabulary, casting, and beat
sub-agents separately rather than in one monolithic call.

### `--director-mode MODE`

Override the automatic director mode selection.

| Mode | Behaviour |
|------|-----------|
| `single` (default for < 60 s) | One Opus call produces the full storyboard |
| `decomposed` (default for >= 60 s) | Four sequential calls: breakdown → vocabulary → casting → beats |
| `auto` (default) | Chosen deterministically: `decomposed` if brief >= 60 s or `save_the_cat` structure, else `single` |

```bash
bash scripts/run.sh ./workspace --director-mode single
bash scripts/run.sh ./workspace --director-mode decomposed
bash scripts/run.sh ./workspace --director-mode auto
```

The `auto` mode is the default. Override only when you have a strong
reason — e.g. `--director-mode single` for a long brief you know is
structurally simple, or `--director-mode decomposed` for a short brief
you want maximum creative richness on.

---

## Full CLI Reference

```
bash scripts/run.sh WORKSPACE [OPTIONS]

Options:
  --brief FILE          Path to brief.md (default: WORKSPACE/brief.md)
  --resume              Skip director; re-use cached storyboard
  --budget TIER         thrift | standard | premium | longform (default: standard)
  --director-mode MODE  single | decomposed | auto (default: auto)
  --out FILE            Output MP4 path (default: WORKSPACE/out.mp4)
```
