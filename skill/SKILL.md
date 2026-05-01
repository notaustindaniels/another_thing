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
