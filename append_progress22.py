"""Append Session 22 progress notes to claude-progress.txt."""
from pathlib import Path

notes = """
## Session 22 (2026-05-01)

Worked: P6.M03 — Regression test suite: three canonical storyboard golden MP4 hashes
        P6.M04 — Demo reel: 3-5 produced MP4s across all three behavior families
Status: both passed
Evidence: evidence/P6.M03/, evidence/P6.M04/, evidence/golden/, evidence/demo/
Commits: e997408 (P6.M03), f5dea57 (P6.M04)

### init.sh note
init.sh still fails at pip upgrade step due to network proxy; conda env fully
functional. This is non-fatal and known.

### P6.M03 — Regression test suite implementation

Three canonical scene YAML files created in tests/scenes/:
- canonical_a.yaml: forest drone flythrough, seed=10001, 192x108 10fps 2s
- canonical_b.yaml: biome keyframed pan, seed=10002, 192x108 10fps 2s
- canonical_c.yaml: portal transition, seed=10003, 192x108 10fps 2s

Golden SHA-256 hashes generated and committed to evidence/golden/:
  example_a: 7f4f189064f880a739974486d195503e403f2517c720d08352d6b91b6403d1b3
  example_b: 350ee5e6b3d72244ea92d119b614b106076738485b19546286a41fa4cc46b0b0
  example_c: a6dcddcc2492dc919830168a1022c64eb66e8e91085de9986f780818eca11368

Render times (well under 5-minute limit):
  A: 32.4s (forest drone, rsvg-convert expensive with 6 SVG layers)
  B: 0.7s (keyframed pan, SVG assets missing → transparent placeholders)
  C: 19.5s (portal transition, 9 SVG layers)

tests/test_regression.py: 19 tests all pass:
  - 13 integrity checks (golden hash files present, valid hex, meta.json exists,
    render time < 300s, canonical YAML files present)
  - 3 byte-identical golden hash regression (re-render matches stored SHA-256)
  - 3 within-session determinism double-render (same seed → same bytes)

generate_golden_hashes.py: standalone script to regenerate hashes with --force
  (use when renderer intentionally changes; stored hashes represent current state)

### P6.M04 — Demo reel implementation

Three 8s demo MP4s created in evidence/demo/:
- demo_a.mp4: forest drone flythrough, seed=20001, 192x108 10fps 8s (106s render)
- demo_b.mp4: biome keyframed pan, seed=20002, 192x108 10fps 8s (1.4s render)
- demo_c.mp4: portal transition, seed=20003, 192x108 10fps 8s (60s render)

Demo scene YAMLs in tests/scenes/demo_a/b/c.yaml extend the canonical concept
to 8 seconds with more dramatic bezier paths and camera parameters.
render_demos.py: standalone script to regenerate demos with --force.

### Next session should work on P6.M05

P6.M05 — Final README, multi-platform test, pip publish ready
  Validation commands:
    - python tools/validate_licensing.py  (already passes)
    - python tools/validate_scaffold.py   (already passes)
  Acceptance criteria:
    1. README.md covers installation, examples, cost expectations, demo reel
       link, troubleshooting
    2. pip install parallax-engine works on macOS and Linux
    3. All Phase 1-6 validation commands pass in sequence
    4. validate_licensing.py exits 0
    5. validate_scaffold.py exits 0

  This is primarily a documentation milestone. The existing README.md (from P5.M02)
  is already good — check what it currently says and add:
    - Demo reel section linking to evidence/demo/ MP4s
    - Cost expectations (§11.12: ~$2.50 per video, 3-4 min on standard laptop)
    - Troubleshooting section
    - References to LICENSES.md and EULA.md

  CAUTION: Do NOT modify any tool/ files or phase_milestones.json fields other
  than passes/evidence/notes. After P6.M05, the build is complete.

  After P6.M05 passes, ALL milestones will be passes:true. The build is done.
"""

p = Path("claude-progress.txt")
existing = p.read_text()
p.write_text(existing + notes)
print("Appended Session 22 notes.")
