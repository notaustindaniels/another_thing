# parallax-engine

Commercial Python engine that generates 2.5D multiplane parallax animation
MP4s from natural-language briefs. Aesthetic target: classic Disney
multiplane (Bambi/Pinocchio) with high-speed FPV drone camera motion.

---

## Installation

### 1. Python package

```bash
pip install parallax-engine
```

Requires Python 3.11+. The package installs the `parallax-engine` CLI entry point.

#### Optional: SVG rasterizer (Skia)

`skia-python` provides high-quality SVG rasterization. It falls back to
`rsvg-convert` (if present on PATH) when unavailable.

```bash
pip install "parallax-engine[render]"
```

### 2. FFmpeg — LGPL build with libopenh264 (required)

The engine encodes with `libopenh264` (Cisco pays H.264 royalties for users
of the precompiled binary — downstream commercial use is covered). You **must
not** use a GPL FFmpeg build with `libx264` — that is license-incompatible
with commercial resale.

#### macOS (conda — recommended)

```bash
conda install -c conda-forge ffmpeg
```

The conda-forge build is LGPL-only with `libopenh264` included.

#### macOS (Homebrew)

```bash
brew install openh264
brew install ffmpeg
```

If your Homebrew FFmpeg includes `libx264`, you need to build FFmpeg from
source without `--enable-gpl`. See
<https://trac.ffmpeg.org/wiki/CompilationGuide/macOS>.

#### Linux (Ubuntu/Debian)

```bash
sudo apt update && sudo apt install -y ffmpeg libopenh264-dev
```

Verify the install is LGPL-clean:

```bash
ffmpeg -encoders 2>/dev/null | grep -E "libx264|libopenh264"
# Should show: libopenh264
# Must NOT show: libx264
```

#### Verify

```bash
python tools/validate_licensing.py
# Expected: system_ffmpeg ... PASS; libopenh264=True, libx264=False
```

---

## Usage

### Render a scene directly (CLI)

```bash
# Render a hand-authored scene.yaml to MP4
parallax-engine render path/to/scene.yaml --out output.mp4

# Specify a workspace root for resolving relative asset paths
parallax-engine render scene.yaml --out out.mp4 --workspace ./my-project
```

### Run the agent harness (brief → MP4)

```bash
# Put your brief in a text file, then run the harness
cat > workspace/brief.md << 'EOF'
10-second FPV drone flythrough of a redwood forest at golden hour.
Layers: sky, far mountains, mid-forest canopy, near-ground ferns.
Camera: slow push forward with gentle yaw drift left to right.
EOF

parallax-engine harness --workspace ./workspace --brief "$(cat workspace/brief.md)"
# Final MP4 at workspace/out.mp4
```

### Via the Anthropic Skill (in Claude Code)

See [Skill Registration](#skill-registration) below to register the Skill
first. Once registered, just talk to Claude:

```
Make a 10-second drone flythrough of a redwood forest at golden hour
```

```
Create a 2.5D explainer that travels through 4 biomes (mountain, river, city, desert)
```

```
Show a portal in a tree opening into a neon city
```

---

## Skill Registration

The `skill/` directory contains a thin Anthropic Skill wrapper that lets
Claude (in claude.ai or Claude Code) invoke `parallax-engine` from a
natural-language brief.

### Prerequisites

1. `parallax-engine` installed and on PATH (see [Installation](#installation))
2. FFmpeg LGPL build verified (see above)
3. Claude Code CLI installed: `npm install -g @anthropic-ai/claude-code`

### Register the Skill

```bash
# From the parallax-skill/ repo root:
claude skill add ./skill

# Verify it registered:
claude skill list | grep parallax
```

### How it works

The Skill (`skill/SKILL.md`) tells Claude to invoke:

```bash
bash scripts/run.sh ./workspace [OPTIONS]
```

which calls `python -m parallax_engine.cli --workspace WORKSPACE [OPTIONS]`.

The Skill is a thin shim — all logic lives in the Python package. It does
not carry multi-agent prompts or renderer code.

### Available flags (via the Skill or directly)

| Flag | Default | Effect |
|---|---|---|
| `--resume` | off | Skip director; re-use cached `storyboard.yaml` |
| `--budget standard` | standard | Cost tier: `thrift` / `standard` / `premium` / `longform` |
| `--director-mode auto` | auto | Director mode: `single` / `decomposed` / `auto` |
| `--out FILE` | workspace/out.mp4 | Output MP4 path |

---

## Examples

### Forest flythrough

```yaml
# scene.yaml
version: 1
meta:
  duration_s: 10
  fps: 30
  resolution: [1920, 1080]
  seed: 42
camera:
  mode: drone
  z_start: -200
  z_end: 800
  speed: 1.0
  noise: {z_amp: 0.3, xy_amp: 0.15, hz: 0.5}
layers:
  - id: sky
    src: sky.svg
    z: -500
  - id: mountains
    src: mountains.svg
    z: -200
  - id: forest
    src: forest.svg
    z: 0
```

```bash
parallax-engine render scene.yaml --out forest.mp4
```

### Portal transition

```yaml
# portal_scene.yaml
version: 1
meta:
  duration_s: 8
  fps: 30
  resolution: [1920, 1080]
  seed: 7
camera:
  mode: keyframed
  keyframes:
    - {t: 0.0, x: 0, y: 0, z: 0}
    - {t: 1.0, x: 0, y: 0, z: 600}
layers:
  - id: forest_bg
    src: forest_bg.svg
    z: 0
  - id: city_reveal
    src: city.svg
    z: 400
masks:
  - id: portal
    anchor: world
    src_stack: [city_reveal]
    dest_stack: [forest_bg]
    path_svg: portal_silhouette.svg
    growth: 0.0
```

```bash
parallax-engine render portal_scene.yaml --out portal.mp4
```

---

## System requirements

| Requirement | Minimum | Notes |
|---|---|---|
| Python | 3.11 | 3.12 also tested |
| FFmpeg | 6.x | LGPL build only; see above |
| RAM | 4 GB | 8 GB recommended for 1080p renders |
| Disk | 500 MB | For workspace, frames, evidence |
| OS | macOS, Linux | Windows via WSL2 (untested) |

---

## Demo reel

Three produced MP4s are included in `evidence/demo/` to demonstrate the
engine's three primary behavior families:

| File | Brief | Behavior family |
|---|---|---|
| `evidence/demo/demo_a.mp4` | Forest drone flythrough at golden hour | `drone` camera: continuous forward push with organic noise |
| `evidence/demo/demo_b.mp4` | Biome keyframed pan across mountain/river/city | `keyframed` camera: explicit t=0→1 waypoints |
| `evidence/demo/demo_c.mp4` | Portal in a tree opens into a neon city | `keyframed` + portal mask: L2 compositing trick |

All three were generated by the agent harness from natural-language briefs
with no manual scene authoring. Rendered at 192×108 10 fps (prototype
resolution). Production resolution is 1920×1080 30 fps.

---

## Cost expectations

> Numbers from SPEC.md §11.12. Assumes standard Anthropic API pricing
> (Claude Sonnet for director; Sonnet/Haiku mix for workers).

| Scenario | Expected API cost | Render time (CPU) |
|---|---|---|
| Simple brief, no retries | ~$0.60 | 3–4 min |
| Typical brief, one scene retry | ~$1.20 | 4–6 min |
| Complex brief, multiple retries | ~$2.50 (cap) | 8–12 min |
| Long-form ≥ 60 s | ~$2.40, cap $5.00 | 15–25 min |

Costs stay under **$2.50/render** on the `standard` budget tier. The
default cap is $2.50; override with `--budget 5.00` for long-form.

Prompt caching (Anthropic 90% discount on cached input tokens) reduces
the director's per-call cost from ~$0.19 cold to ~$0.06 on cache hit.
A typical run saves ~$0.20 from caching alone.

Use `--resume` to skip the director entirely on re-runs — saves the full
director cost when you are only tweaking scene parameters.

---

## Licensing

`parallax-engine` is proprietary software. All dependencies are permissive
(MIT, Apache 2.0, BSD, HPND, or LGPL-with-dynamic-linking). The H.264
codec is provided via Cisco's precompiled `libopenh264` binary — Cisco
pays MPEG-LA royalties on behalf of users of the binary, covering
downstream commercial use.

Run `python tools/validate_licensing.py` at any time to verify the
dependency tree is clean.

See `LICENSES.md` for a complete dependency audit and `EULA.md` for the
commercial customer license template.

---

## Troubleshooting

### FFmpeg not found

```
RuntimeError: FFmpeg not found. Install LGPL FFmpeg with libopenh264.
```

Install via conda (recommended):

```bash
conda install -c conda-forge ffmpeg
```

Verify:

```bash
ffmpeg -version
python tools/validate_licensing.py   # should show libopenh264=True, libx264=False
```

### Wrong FFmpeg build (libx264 detected)

```
[validate_licensing] FAIL  system_ffmpeg ... libx264=True
```

You have a GPL FFmpeg. Remove it and install the LGPL conda-forge build:

```bash
conda install -c conda-forge ffmpeg
```

If you installed via Homebrew, verify `brew info ffmpeg | grep "x264"` — if
it shows `--with-x264`, you must rebuild without that flag or use conda.

### Import error after pip install

```
ModuleNotFoundError: No module named 'parallax_engine'
```

Check that you installed into the active Python environment:

```bash
pip install -e .        # dev install, or
pip install parallax-engine   # release install
python -c "import parallax_engine; print(parallax_engine.__version__)"
```

### Render is slow / running out of memory

- Reduce resolution: edit `meta.resolution: [960, 540]` in your scene YAML.
- Lower FPS for preview: `meta.fps: 10`.
- Reduce `meta.duration_s` to a shorter clip while testing.
- SVG rasterization is the main bottleneck. Install `skia-python` for a
  3–5× speedup over the `rsvg-convert` fallback.

### The agent harness exits with a budget error

```
error_max_budget_usd: budget $2.50 exceeded
```

Use `--budget 5.00` for complex briefs or long-form video. Alternatively:

```bash
parallax-engine harness --workspace ./ws --brief "..." --budget 5.00
```

Or use `--resume` to re-run with the existing storyboard, skipping the
(expensive) director call:

```bash
parallax-engine harness --workspace ./ws --brief "..." --resume
```

### Storyboard validation fails

```
ValidationError: N validation errors for Storyboard
```

The director produced a storyboard that violates the schema. Try:

1. `--director-mode single` (simpler output, less likely to drift)
2. `--budget premium` (Opus model, higher quality reasoning)
3. Check `workspace/logs/director.json` for the raw director output

### SVG assets render as blank / transparent

The engine falls back to a transparent placeholder when SVG rasterization
fails. Install `skia-python` or ensure `rsvg-convert` is on PATH:

```bash
# macOS
brew install librsvg

# Ubuntu/Debian
sudo apt install -y librsvg2-bin
```

---

## Naming — three things, three names

| Name | What it is |
|---|---|
| **`parallax-engine`** | The engine: Python package, CLI, renderer, director tier, agent harness |
| **`parallax-skill`** | The Anthropic Skill wrapper: thin `SKILL.md` + `scripts/run.sh` shim |
| **`parallax-skill/` directory** | The repo root (named before the engine/Skill split was clarified) |

---

## Building from source (for development)

```bash
git clone <repo>
cd parallax-skill

# Create conda env with LGPL FFmpeg
conda create -n parallax python=3.11
conda activate parallax
conda install -c conda-forge ffmpeg numpy pillow opencv pyyaml pydantic

# Install in editable mode
pip install -e ".[dev]"

# Verify
python tools/validate_licensing.py     # 6/6 PASS
pytest tests/ -q                        # all tests pass
```

### Specification

`SPEC.md` is canonical. When code, docs, or this README contradict it,
the spec wins.

### Running the autonomous build

See `SETUP.md` for the full build-harness setup. Short version:

```bash
export CLAUDE_CODE_OAUTH_TOKEN='your-token'
python -m runner.autonomous_runner --project-dir . --wallclock-hours 72
```

The runner is resumable — if you stop it, run the same command again.
