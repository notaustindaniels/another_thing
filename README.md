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

## Licensing

`parallax-engine` is proprietary software. All dependencies are permissive
(MIT, Apache 2.0, BSD, HPND, or LGPL-with-dynamic-linking). The H.264
codec is provided via Cisco's precompiled `libopenh264` binary — Cisco
pays MPEG-LA royalties on behalf of users of the binary, covering
downstream commercial use.

Run `python tools/validate_licensing.py` at any time to verify the
dependency tree is clean.

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
