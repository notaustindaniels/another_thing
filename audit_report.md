# parallax-engine end-to-end audit

**Date:** 2026-05-01
**Trigger:** Build reports 34/34 milestones complete, but `python -m parallax_engine harness --brief /tmp/brief.md` produces no MP4 — the brief is ignored, a smoke-test scene is seeded, and a `RENDER_DONE` checkpoint is pre-written before any render attempt.

## TL;DR

The build's "completeness" is, in large part, an artifact of the validation strategy. The real seven-stage pipeline (director → scene-designer → merger → asset-generator → renderer → encoder) **does exist** in code as `ProjectManager` ([parallax_engine/manager.py](parallax_engine/manager.py)), and is wired to a real CLI entry point (`cmd_run`, [parallax_engine/cli.py:254-323](parallax_engine/cli.py#L254-L323)). However:

1. The user's invocation hit the wrong subcommand. The `harness` subcommand is **explicitly a stub-only smoke test** — it pre-seeds a hard-coded `scene.yaml`, pre-writes the render-done checkpoint, and uses `ClaudeSDKStub` with canned per-agent responses. The CLI's own docstring at [cli.py:9-12](parallax_engine/cli.py#L9-L12) says so: *"Uses ClaudeSDKStub in offline mode so the smoke test is self-contained. Exits 0 when the pipeline completes (with or without a render output)."*
2. **Every E2E test in the repo runs against stubs.** `tests/test_harness_e2e.py` injects `_WritingStub(ClaudeSDKStub)`. `tests/integration/test_e2e_storyboards.py` runs `ProjectManager(..., dry_run=True)`. There is **no test** that drives a brief through real Anthropic calls and asserts an authentic MP4. Milestone validation passes against stubs.
3. The Skill wrapper ([skill/scripts/run.sh](skill/scripts/run.sh)) does correctly route to the real `cmd_run` path (not the harness), so the production code path *exists*. But it has never been exercised end-to-end with a real API key in CI, and the harness command — which is what most operators reach for first because of its name — is a dead-end.

In short: the milestones validated that the plumbing runs and produces files; they did not validate that the plumbing produces *correct* files from a real brief.

---

## Addendum — 2026-05-01 (auth investigation, post-audit)

A debugging session attempted to drive the production `cmd_run` path with `CLAUDE_CODE_OAUTH_TOKEN` (Max-plan absorption) by patching the four LLM call sites to pass the token via the Anthropic SDK's `auth_token=` kwarg — i.e. `Authorization: Bearer <oauth_token>` instead of `x-api-key: <token>`. The request reached `api.anthropic.com/v1/messages` cleanly and was rejected by the API:

```
Error code: 401 - {'type': 'error', 'error': {'type': 'authentication_error',
'message': 'OAuth authentication is currently not supported.'}}
request_id: req_011CacqGzwiSkMjjLxmJ1gt6
```

**Conclusion.** The raw Messages API does not accept OAuth bearer tokens. `CLAUDE_CODE_OAUTH_TOKEN` is only valid via the `claude_code_sdk` routing path (which subprocess-launches the Claude Code CLI and reaches a backend that knows how to absorb usage into a Max plan). The four call sites in this engine — director [parallax_engine/director/agent.py:184](parallax_engine/director/agent.py#L184), scene-designer [parallax_engine/scene/designer.py:637](parallax_engine/scene/designer.py#L637), gen_image [parallax_engine/tools/gen_image.py:120](parallax_engine/tools/gen_image.py#L120), qa.critic [parallax_engine/qa/critic.py:490](parallax_engine/qa/critic.py#L490) — all call `anthropic.Anthropic().messages.create(...)`, which speaks directly to the Messages API. **`ANTHROPIC_API_KEY` is therefore the only viable credential** for this engine as architected.

The auth-precedence patch installed in that session (`ANTHROPIC_API_KEY` → `api_key=`; else `CLAUDE_CODE_OAUTH_TOKEN` → `auth_token=`; else raise) is correct in shape and matches the SDK's API, but the `auth_token=` branch is **effectively dead code** for these call sites until either (a) the Messages API begins accepting OAuth, or (b) the engine is refactored onto `claude_code_sdk.query()`. Refactoring to `claude_code_sdk` was explicitly rejected as architecturally wrong for a commercial engine that will be configured with end-user API keys at deployment.

**Future-session expectation:** `ANTHROPIC_API_KEY` is required in the environment for any real render. Do not re-investigate OAuth paths against the Messages API.

A separate latent bug surfaced from the same investigation: [tests/test_auth.py:42-46](tests/test_auth.py#L42-L46)'s `_remove_auth_vars()` mutates `os.environ` directly instead of using `monkeypatch.delenv`, polluting the process env for every subsequent test in the suite. The pre-existing silent-degradation paths in `gen_image` (placeholder fallback) and `qa.critic` (stub PASS) had been masking it. This is paired with audit fix #3 (silent-degradation hardening) — both have to land together so the suite can run cleanly with credentials present.

---

## Addendum — 2026-05-02 (director duration override observed)

In a follow-up debugging session, the director was given the brief *"A 6-second drone push through a foggy pine forest at dawn."* (via `cmd_run`, real Opus call). It produced a structurally good 3-scene storyboard but **expanded the duration from 6 seconds to 15 seconds** without being asked. The director's own notes in [storyboard.yaml:81-87](file:///tmp/real_render_6/storyboard.yaml#L81-L87) explained the choice:

> *"Brief asked for 6s; target_duration is 15s. I have honored the target and expanded the single forward push into a three-beat arc (hush → disclosure → opening) so the longer runtime earns its length rather than padding a six-second idea."*

**The brief contained no `target_duration`.** Either (a) the director's prompt has a hardcoded duration floor or default that the model is referring to as "target_duration," (b) something in `DirectorBrief` injects a target duration the user never specified, or (c) the model is confabulating a constraint to justify expanding short briefs. The `total_duration_s: 15.0` field was written as if it were authoritative, and downstream scenes (3 × 5s) were sized to match.

**Impact:** brief duration is not honored. A user who asks for 6s of video gets 15s. Cost scales with duration, so this is also a billing concern for production use.

**Status:** not blocking the current end-to-end render investigation; recorded for follow-up. Likely sites to check: [parallax_engine/director/prompt.py](parallax_engine/director/prompt.py) (`DirectorBrief` class and the prompt template — search for `target_duration`, `15`, or `duration`), and the Storyboard schema for any default duration field.

---

## 1. What works end-to-end with real LLM calls

**Nothing, verified.** No test in the repo exercises a real Anthropic call across the full chain, so no claim of "this works end-to-end" can be supported by the test suite as it stands.

The following components individually call real LLMs when invoked with a real API key, and *should* compose if `cmd_run` is invoked with `dry_run=False` (the default for `cmd_run`):

- Director ([parallax_engine/director/agent.py:256-277](parallax_engine/director/agent.py#L256-L277)) — calls `client.messages.create()` against `claude-opus-4-7-20261231` to produce storyboard YAML.
- Scene-designer ([parallax_engine/scene/designer.py:684-751](parallax_engine/scene/designer.py#L684-L751)) — lazy-creates `_AnthropicClient`, calls Sonnet for per-scene fragments.
- Asset-generator → SVG backend ([parallax_engine/tools/gen_image.py:120-125](parallax_engine/tools/gen_image.py#L120-L125)) — calls `claude-haiku-4-5` for SVG generation when `ANTHROPIC_API_KEY` or `CLAUDE_CODE_OAUTH_TOKEN` is set; otherwise silently returns a placeholder SVG ([gen_image.py:80](parallax_engine/tools/gen_image.py#L80)).
- QA tiers (asset/scene/storyboard) ([parallax_engine/qa/critic.py](parallax_engine/qa/critic.py)) — implemented and wired into `ProjectManager._run_qa()`, gated on `self.dry_run`.

These compose **in code** (the I/O contracts line up: storyboard.yaml → fragments → merged scene.yaml → SVGs → frames → MP4). They have not been demonstrated to compose at runtime with real LLMs.

## 2. What works only with stubs / canned responses

- **`cmd_harness` ([cli.py:130-246](parallax_engine/cli.py#L130-L246))** — the `harness` subcommand. Pre-seeds [`_SMOKE_SCENE_YAML`](parallax_engine/cli.py#L36-L70) into `workspace/scene.yaml`, pre-writes the `PHASE_RENDER_DONE` checkpoint to skip the real render ([cli.py:182-188](parallax_engine/cli.py#L182-L188)), then runs `ParallaxLead` with a hard-coded stub factory whose response map is:

  ```python
  _stub_responses = {
      "scene-designer": "scene written: 1 layers, 1 masks, duration 1.0s",
      "asset-generator": "ok: assets/sky_smoke.svg",
      "mask-author": "ok: silhouette + hole paths added to assets/portal_smoke.svg",
      "camera-pather": "camera path written: drone path with 3 control points",
      "qa-critic": "PASS",
  }
  ```
  The `--brief` argument *is* read into `brief: str = args.brief or "test scene"` ([cli.py:152](parallax_engine/cli.py#L152)) and passed to `lead.run(brief)`, but `ParallaxLead` never invokes a director — it operates on the pre-seeded scene. The brief is decorative.

- **`tests/test_harness_e2e.py`** (742 lines, ~28 test methods) — uses `_WritingStub(ClaudeSDKStub)` injected via `sdk_client_factory`. The MP4-existence test asserts only `out_mp4.exists() and out_mp4.stat().st_size > 0`. Zero real LLM calls.

- **`tests/integration/test_e2e_storyboards.py`** — drives `ProjectManager(..., dry_run=True)`. In `dry_run`, the manager stubs director and scene-designer, and bypasses QA entirely. Real `render_scene()` is called on hand-written scene YAML — that demonstrates the renderer is real, but says nothing about LLM-driven inputs.

- **`_default_sdk_factory` fallback ([parallax_engine/lead.py:273-296](parallax_engine/lead.py#L273-L296))** — silently returns `ClaudeSDKStub` when `anthropic`/`claude-code-sdk` is not importable. This means a CI environment without the SDK installed will still run "successfully" — against stubs, with no warning loud enough to fail the build.

## 3. What's implemented but not wired to any CLI default

- **The full real pipeline (`ProjectManager`, [parallax_engine/manager.py](parallax_engine/manager.py))** is wired to `cmd_run` ([cli.py:254-323](parallax_engine/cli.py#L254-L323)), which is reached when the CLI is invoked **without** a subcommand: `python -m parallax_engine --workspace DIR --brief "..."`. The Skill's [skill/scripts/run.sh](skill/scripts/run.sh) does invoke this path. But the `harness` subcommand exists alongside it and is the one a developer reaches for when they see "harness" in the docs — that's the foot-gun.

- **The QA tiers in [parallax_engine/qa/critic.py](parallax_engine/qa/critic.py)** (asset/scene/storyboard) are wired into `ProjectManager._run_qa()` but gated on `not self.dry_run`. Every integration test runs with `dry_run=True`, so the QA tiers have no real-input test coverage. They are production code that has never been exercised against real model output in tests.

- **Phase 4.5 director-tier evidence directories** under `evidence/P4_5.M*/` exist but contain only `.gitkeep` files in the spots I sampled.

## 4. What's missing entirely

- **A real-LLM end-to-end test.** No test in the repo asserts that a real brief, fed through real Anthropic calls, produces a valid MP4. Without this, "build complete" cannot be verified except by running it manually.

- **A loud failure when the SDK is unavailable.** [lead.py:290](parallax_engine/lead.py#L290) logs a `warning` and silently substitutes the stub. There is no mode that says "I refuse to run without a real SDK" so CI cannot enforce real-call coverage.

- **A `harness` deprecation or rename.** The `harness` subcommand's docstring [cli.py:448-454](parallax_engine/cli.py#L448-L454) does say *"For production use, omit the subcommand"*, but the name itself ("harness") implies it is the primary entry point. Operators (and the user, in this audit) reach for it first.

- **Documentation in [skill/SKILL.md](skill/SKILL.md) is consistent with the real path** (`bash scripts/run.sh ./workspace`) but the skill description still says *"a multi-agent harness"* — an unfortunate echo of the name of the stub-only command.

- **A check that the brief actually drove the storyboard.** The `harness` command accepts and prints the brief but never compares it to anything that was generated. There is no integrity assertion linking input → output.

## 5. Minimum work to render one real video from a brief

The infrastructure is *almost* there. The cheapest path to a true real-brief render:

1. **Set credentials.** Export `ANTHROPIC_API_KEY` in the shell that will run the pipeline. (See Addendum above: `CLAUDE_CODE_OAUTH_TOKEN` is **not** a substitute — the Messages API rejects OAuth bearer tokens with a 401.) Without `ANTHROPIC_API_KEY`, the post-patch director/scene-designer/gen_image/qa.critic call sites raise `RuntimeError("no credentials")`; the pre-patch behavior was to silently fall back to placeholder SVGs ([parallax_engine/tools/gen_image.py:80](parallax_engine/tools/gen_image.py#L80)) and stub-PASS QA verdicts.

2. **Use the right entry point — not `harness`.** Replace
   ```
   python -m parallax_engine harness --brief /tmp/brief.md --workspace /tmp/first_render
   ```
   with one of:
   ```
   bash skill/scripts/run.sh /tmp/first_render --brief "$(cat /tmp/brief.md)"
   # or
   python -m parallax_engine --workspace /tmp/first_render --brief "$(cat /tmp/brief.md)"
   ```
   This routes through `cmd_run` ([cli.py:254-323](parallax_engine/cli.py#L254-L323)) → `ProjectManager` with `dry_run=False` (the default). Note that `--brief` here takes the brief **text**, not a path; the user's original command passed `/tmp/brief.md` as text, which would also have been wrong on the real path.

3. **Audit the silent SDK fallback.** Edit [parallax_engine/lead.py:285-296](parallax_engine/lead.py#L285-L296) so that when no API credentials are found and a `ClaudeSDKStub` would be substituted, the run aborts with a non-zero exit unless an explicit `--allow-stub` flag is passed. Same change at [parallax_engine/tools/gen_image.py](parallax_engine/tools/gen_image.py) — fail loudly when no key is set instead of returning a placeholder SVG. Without this, even step 2 can silently degrade to stubs.

4. **Add a real-LLM smoke test.** In `tests/integration/`, add `test_real_render_from_brief.py` that:
   - Skips if `ANTHROPIC_API_KEY` is not set.
   - Writes a tiny brief (e.g. "1-second push-in on a single tree") to `workspace/brief.md`.
   - Invokes `ProjectManager(..., dry_run=False)` end-to-end with a tight `max_budget_usd` (~$0.50).
   - Asserts: `storyboard.yaml` exists and references the brief's keywords; `scene.yaml` exists and was *not* the smoke-test sentinel; `out.mp4` exists, plays, and is non-trivial in size.
   This is the test that proves the build is "complete." Wire it into CI as a nightly job (not per-PR, due to cost).

5. **Rename or hide `harness`.** Either remove the `harness` subcommand entirely (now that `cmd_run` is the production path) or rename it to `smoke` so its purpose is unambiguous. At minimum, edit the subparser help string at [cli.py:447-454](parallax_engine/cli.py#L447-L454) to lead with "STUB-ONLY SMOKE TEST — does not produce a real MP4".

6. **Tighten milestone validation.** The milestone gates that stamped 34/34 are passing because their assertions are file-existence checks against stub output. For each milestone whose claimed scope includes "produces output X from input Y", add at least one assertion that input Y *materially affected* output X (e.g. brief keyword appears in storyboard, storyboard scene count matches storyboard duration / scene budget). Without this, a stub run looks identical to a real run from the gate's perspective.

## Honest assessment

The build is not "complete" in the sense most readers would assume. The code for a real brief→MP4 pipeline exists and is wired, but it has never been demonstrated to work end-to-end in any committed test, and the most prominent CLI entry point (`harness`) is a stub-only path that pre-fakes its outputs. The 34/34 milestone count reflects "every component has tests that pass against stubs," not "the system can render a real video from a real brief." The minimum delta from where the repo is today to a verifiable real-render is small (steps 1–6 above), but it has to actually be done before "complete" is a meaningful claim.
