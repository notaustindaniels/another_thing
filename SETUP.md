# Setup and Launch

This document walks through everything required to launch the autonomous
This document walks through everything required to launch the autonomous
build of `parallax-engine` (the Python engine; the build directory itself
is named `parallax-skill/` and contains both the engine and its eventual
Anthropic Skill wrapper). Read this once end-to-end before you start
typing. The whole setup takes about 20 minutes.

## 1. Prerequisites

You need:

- **macOS or Linux.** Windows-via-WSL2 should work but isn't tested.
- **Python 3.11 or newer.** Run `python3 --version` to check.
- **Node.js 18+** for the Claude Code CLI. `node --version`.
- **git** for the build's checkpoints. `git --version`.
- **FFmpeg with libopenh264.** This is the licensing-critical one. See
  step 4 below.
- **Anthropic Max plan** with a Claude Code OAuth token, OR an Anthropic
  API key for pay-as-you-go billing.

## 2. Create the project directory

Unpack the tarball. The result is a directory called `parallax-skill/`
with the layout described in README.md. Move it wherever you want it
to live (it'll grow over the build to maybe 200–500MB including
`evidence/` and `workspace/`).

```bash
tar xzf parallax-skill.tar.gz
cd parallax-skill
ls
```

## 3. Add references

The agent uses these as the canonical aesthetic target for the entire
project. Without them, the build will produce technically correct output
that doesn't aesthetically resemble what you want.

**Strongly recommended (the canonical aesthetic):**

- `references/sources/polyfjord_drone.mp4` — the Polyfjord-style Blender
  FPV drone reference. Defines the project's visual target.
- `references/sources/heygen_parallax.mp4` — the 2.5D parallax reference
  showing biome reveals (or whatever your second drone reference is).
- `references/transcripts/polyfjord.txt` — the Blender drone-physics
  technique transcript. Plain text. The camera-pather and director
  agents read this for motion intuition.

**Not used (deliberately):**

- No `prior_portal.mp4`. The Phase 2 architectural gate uses synthetic
  reference equivalence — the validator generates its own test scene with
  known geometry and computes the analytical answer from SPEC.md §2.
  See `references/sources/README.md` for the reasoning. If you have a
  prior portal MP4, leave it out.

**Already included (do not modify):**

- `references/portal_mechanic.md` — the single concept distilled from
  the prior Remotion repo. The agent reads this; that's all.

## 4. Install FFmpeg with libopenh264

This is the most error-prone step because most package managers ship a
GPL build of FFmpeg with libx264, which is exactly what we cannot use
for commercial resale. The validator (`tools/validate_licensing.py`)
will refuse to let the build advance until this is right.

### macOS (Homebrew)

Homebrew's default FFmpeg includes libx264. Install the variant without
GPL components and add openh264 separately:

```bash
brew install openh264
brew install ffmpeg --without-x264 --without-x265 --with-openh264
# If --without-x264 is unavailable in your Homebrew version, you may
# need to build from source. See https://trac.ffmpeg.org/wiki/CompilationGuide/macOS
```

### Linux (Ubuntu/Debian)

```bash
sudo apt update
sudo apt install -y ffmpeg libopenh264-dev
# Verify it's the LGPL build with libopenh264:
ffmpeg -encoders 2>/dev/null | grep -E "libx264|libopenh264"
# Should show libopenh264. Should NOT show libx264.
# If libx264 is present, you have the GPL build — uninstall and rebuild,
# or use a Docker image with LGPL FFmpeg.
```

If your distro insists on shipping libx264, the cleanest path is to run
the build inside a Docker container with a known-clean FFmpeg. The
agent will set this up if Phase 1 detects a contaminated FFmpeg, but
it's faster to fix it yourself first.

### Verify

```bash
PARALLAX_PROJECT_DIR=. python3 tools/validate_licensing.py
```

You should see `system_ffmpeg ... PASS` in the output. If you see
"libx264 present in ffmpeg encoder list", fix the FFmpeg install
before going further. Anything else can wait.

## 5. Install the harness Python dependencies

The harness itself (not the project being built) needs a few packages:

```bash
pip install claude-code-sdk pydantic pyyaml
```

These are *only* for the harness. The agent will create `pyproject.toml`
during Phase 1 and install the project's own dependencies into a venv
inside the project directory.

## 6. Install Claude Code CLI

```bash
npm install -g @anthropic-ai/claude-code
claude --version    # confirm it installed
```

## 7. Set auth

**For Max plan (recommended for this build — no per-token billing):**

```bash
export CLAUDE_CODE_OAUTH_TOKEN='your-token-here'
```

Get the token from console.anthropic.com → your profile → Claude Code.
Persist it in your shell rc file if you want it to survive new terminals.

**For pay-as-you-go billing:**

```bash
export ANTHROPIC_API_KEY='your-api-key-here'
```

If both are set, the harness uses `CLAUDE_CODE_OAUTH_TOKEN`.

## 8. Pre-flight check

```bash
PARALLAX_PROJECT_DIR=. python3 tools/validate_licensing.py
PARALLAX_PROJECT_DIR=. python3 tools/validate_scaffold.py
```

The licensing validator should pass (or only warn about Python deps not
yet installed in the venv — that's fine, the agent will install them).
The scaffold validator will fail with several missing-file errors —
that's expected, since the agent hasn't run yet.

## 9. Launch

```bash
python -m runner.autonomous_runner --project-dir . --wallclock-hours 72
```

If you're on API billing, also pass `--budget-usd 1500` (or whatever
you want the cap to be).

The first session is the **initializer**. Expected duration: 10–25
minutes. It produces:

- `phase_milestones.json` — the milestone list, derived from SPEC.md §8
- The project scaffold (`pyproject.toml`, `init.sh`, etc.)
- An initial git commit

**Read `phase_milestones.json` after session 1 finishes.** If it looks
sensible (40–60 milestones grouped by phase, each with validation
commands), let session 2 start. If it looks broken (wrong milestones,
missing phases, agent invented things outside the spec), Ctrl+C and
re-run — the harness is resumable, but you may need to delete
`phase_milestones.json` and `workspace/.harness/` to force a clean
session 1.

Subsequent sessions are coding sessions. They run continuously with a
5-second pause between sessions. Each one completes one milestone.

## 10. Monitor progress

The runner logs every session and prints a budget summary, integrity
check, and progress summary between sessions. Watch for:

- `[runner] auth: CLAUDE_CODE_OAUTH_TOKEN (dev)` — confirms auth
- `[runner] PASS — N integrity checks` — confirms agent didn't break the
  milestone schema
- `Progress: N/M milestones passing` — overall progress
- Any `## SPEC AMBIGUITIES`, `## VALIDATOR ISSUES`, or `## STUCK`
  sections in `claude-progress.txt` — these mean the agent wants you
  to look at something

You can `tail -f claude-progress.txt` to watch the agent's session-by-
session journal. You can `git log --oneline` to see commits.

## 11. The Phase 2 review point

When the runner reports Phase 1 complete and Phase 2 starting, this is
the most important check-in moment. Phase 2's job is to render the
synthetic test scene that `tools/validate_portal_equivalence.py` writes
to `workspace/synthetic_test/scene.yaml`, and prove the engine's output
matches the validator's analytical reference frames pixel-for-pixel
within tolerance.

When Phase 2 completes (or the agent reports it's stuck on Phase 2),
look at:

- `evidence/P2/latest.json` — the validator's per-frame metrics:
  mean/max absolute pixel error, SSIM, color-coverage check
- `workspace/synthetic_test/expected/frame_00000.png` — the analytical
  reference: red field, blue mask hole on the left, green rect on the right
- `workspace/synthetic_test/out.mp4` (or `frames/frame_NNNNN.png`) — the
  engine's actual output. Compare side-by-side with `expected/frame_00000.png`

If the validator passes (mean abs error ≤ 3.0, max ≤ 80, SSIM ≥ 0.97,
all three colors present), Phase 3+ can proceed. The thresholds are
already calibrated for the synthetic case — solid color regions should
be near-exact; only mask-circle and rect boundaries have anti-aliasing
that pulls the error up.

If Phase 2 keeps failing, the agent will iterate (up to 6 attempts per
milestone before declaring `## STUCK`). If `## STUCK` appears, you
need to inspect what went wrong before continuing. Most common Phase 2
failure modes:

- **L0 pixel count is 0** — engine rendered the mask circle as a hole in
  L1 but didn't show L0 underneath. Layer compositing bug.
- **L2 pixel count is 0** — engine ignored `render_pass: above_mask`.
  L2 is being occluded by L1's mask compositing.
- **High mean abs error in solid regions** — engine has color-space bug
  (BGR vs RGB confusion), or applied unwanted color correction.
- **High max abs error at specific pixels** — engine has projection
  off-by-one, or rounded screen coords differently than the validator.

Until Phase 2 passes, the bash hook blocks any end-to-end pipeline
runs. This is by design.

## 12. Stopping and resuming

Ctrl+C at any point sends SIGINT. The harness finishes the current
session cleanly (this matters because the model already burned tokens)
and exits. Re-run the same command to resume — state is in
`workspace/.harness/budget.json` and `phase_milestones.json`.

A second Ctrl+C while the first is still finishing will hard-abort.

## 13. When the build completes

`Progress: N/N milestones passing` and `[runner] all milestones passing
— build complete.` That's the end.

What you have at that point:

- `parallax_engine/` — the Python package, complete with renderer,
  director tier, casting bible, scene designer, project manager
- `tests/` — the test suite
- A `pyproject.toml` you can `pip install` from
- An `evidence/` directory with proof every milestone passed
- A git history with one commit per milestone

Try a render:

```bash
echo "A 10-second drone push through a pine forest at dawn." > brief.md
python -m parallax_engine render --brief brief.md --output forest.mp4
```

(Exact CLI may differ depending on what the agent decides during Phase 5;
check `parallax_engine --help`.)

## Troubleshooting

**The runner immediately exits with "no auth credentials found"**
You haven't exported `CLAUDE_CODE_OAUTH_TOKEN` or `ANTHROPIC_API_KEY` in
this shell. Re-export and retry.

**The runner immediately exits with "project directory not ready"**
Read the error list. Usually it's a missing reference file or an empty
prompts/ directory.

**Sessions keep failing with "command blocked by security hook"**
The agent tried something the hook blocked. Read the hook's reason in
the session log — it's almost always a real licensing or safety issue,
not a hook bug. If you genuinely need the agent to do something the
hook prevents, the cleanest fix is to widen the hook's allowlist in
`runner/security.py` between sessions. Don't bypass it from inside a
session.

**Phase 1 milestones aren't matching SPEC.md §8 — agent invented its own**
This is rare but recoverable. Stop the runner. Delete
`phase_milestones.json` and `workspace/.harness/`. Re-run. Session 1
will redo the initializer with a fresh context. If the second attempt
also goes off-rail, the prompt may need tightening — open
`prompts/initializer_prompt.md` and Step 4 (the milestone schema) is
where to look.

**The integrity check is failing every session**
Look at the integrity report's specific complaint. Most common is the
agent flipping `passes: true` without populating `evidence/`, which the
prompt warns against. The next session will see the violation in
`claude-progress.txt` and self-correct, but if it persists across two
sessions you should manually flip `passes: false` on the offending
milestone and add a note explaining why.

**Phase 2 portal-equivalence keeps failing**
Open `workspace/synthetic_test/out.mp4` (or the PNG sequence) and
`workspace/synthetic_test/expected/frame_00000.png` side-by-side. The
expected image is a red field with a blue circle on the left half and
a green rectangle on the right half (slightly overlapping the circle).
If the engine's output looks obviously different (different colors,
wrong shapes, missing one of the colors), the diagnosis is in
`evidence/P2/latest.json` — read the per-check messages. Most failures
classify into one of: missing layer (color_coverage check), wrong
geometry (max_abs_error spike at a specific frame), or color-space
confusion (mean_abs_error elevated globally). Do not lower the
thresholds in `validate_portal_equivalence.py` to make the test pass —
the synthetic case has a tight analytical ground truth and the
thresholds already account for reasonable rasterization differences.
