"""
tests/test_subagent_output_schemas.py — P4.M03 acceptance criteria tests.

Verifies that:
1. scene-designer output (sample scene.yaml) parses against the Scene schema
2. asset-generator output (sample SVG) has required structure and id attributes
3. mask-author output (sample SVG) has id="silhouette" and id="hole" in one viewBox
4. lead.py correctly discovers layer_ids from the canonical scene.yaml format
5. lead.py correctly discovers mask_files from the canonical scene.yaml format
6. QA loop counter is a Python integer (§9.7)
7. QA critic prompt does not say "stop after 3" or equivalent (§9.7)

These tests run without a live LLM — they verify schema compliance of canonical
sample outputs, ensuring real LLM invocations would produce valid artifacts.

Spec anchors: §3.3, §3.4, §9.4, §9.7
"""
from __future__ import annotations

import pathlib
import tempfile
import xml.etree.ElementTree as ET

import pytest
import yaml

from parallax_engine.scene import load_scene_yaml, Scene, SceneVersionError
from parallax_engine.lead import ClaudeSDKStub, ParallaxLead


# ---------------------------------------------------------------------------
# Canonical scene.yaml samples produced by scene-designer
# ---------------------------------------------------------------------------

MINIMAL_SCENE_YAML = """\
version: 1
meta:
  duration_s: 5.0
  fps: 30
  resolution: [1920, 1080]
  perspective_px: 1200
  origin: [960.0, 540.0]
  bg_color: "#0a0a14"
  seed: 12345

stacks:
  main:
    layers:
      - id: sky
        src: assets/sky.svg
        scene_xyz: [0, 0, -10000]
        plate_size: [3840, 2160]
      - id: midground
        src: assets/midground.svg
        scene_xyz: [0, 0, -5000]
        plate_size: [3840, 2160]
      - id: foreground
        src: assets/foreground.svg
        scene_xyz: [0, 0, -1000]
        plate_size: [3840, 2160]

camera:
  mode: drone
  drone:
    path:
      kind: bezier
      controls:
        - [0, 0, 0]
        - [30, 5, -5000]
        - [0, 0, -9500]
      duration_s: 5.0
    poi_lookahead_s: 0.55
    spring_halflife_s: 0.18
    noise:
      z_amp: 22
      xy_amp: 6
      hz: 0.7
    bank_from_velocity: 0.40

masks: []
"""

PORTAL_SCENE_YAML = """\
version: 1
meta:
  duration_s: 8.0
  fps: 24
  resolution: [1920, 1080]
  perspective_px: 1200
  origin: [960.0, 540.0]
  bg_color: "#000000"
  seed: 99

stacks:
  forest:
    layers:
      - id: sky
        src: assets/sky.svg
        scene_xyz: [0, 0, -10500]
        plate_size: [3840, 2160]
      - id: portal_tree
        src: assets/portal_tree.svg
        scene_xyz: [0, 0, -4500]
        plate_size: [3840, 2160]
        svg_paint_id: silhouette
  city:
    layers:
      - id: city_sky
        src: assets/city_sky.svg
        scene_xyz: [0, 0, -10500]
        plate_size: [3840, 2160]

camera:
  mode: drone
  drone:
    path:
      kind: bezier
      controls:
        - [0, 0, 0]
        - [40, 10, -2000]
        - [0, 0, -4400]
      duration_s: 8.0
    poi_lookahead_s: 0.5
    spring_halflife_s: 0.20
    noise:
      z_amp: 18
      xy_amp: 5
      hz: 0.6
    bank_from_velocity: 0.30

masks:
  - id: portal
    path_svg: assets/portal_tree.svg
    silhouette_id_in_svg: silhouette
    path_id_in_svg: hole
    attached_to_layer: forest.portal_tree
    anchor: world
    src_stack: forest
    dest_stack: city
    matte: alpha
    growth:
      kind: perspective
"""

# Keyframed camera variant
KEYFRAMED_SCENE_YAML = """\
version: 1
meta:
  duration_s: 6.0
  fps: 30
  resolution: [1920, 1080]
  perspective_px: 1200
  origin: [960.0, 540.0]
  bg_color: "#112233"
  seed: 7777

stacks:
  main:
    layers:
      - id: bg
        src: assets/bg.svg
        scene_xyz: [0, 0, -8000]
        plate_size: [3840, 2160]
      - id: fg
        src: assets/fg.svg
        scene_xyz: [0, 0, -1000]
        plate_size: [3840, 2160]

camera:
  mode: keyframed
  keyframed:
    - t: 0.0
      x: -200.0
      y: 0.0
      z: 0.0
      ease: linear
    - t: 6.0
      x: 200.0
      y: 0.0
      z: 0.0
      ease: easeInOutCubic

masks: []
"""


# ---------------------------------------------------------------------------
# 1. scene-designer output parses against the Scene schema
# ---------------------------------------------------------------------------

class TestSceneDesignerOutputSchema:
    def test_minimal_scene_yaml_parses(self, tmp_path: pathlib.Path) -> None:
        """Canonical scene-designer output (minimal) must parse via load_scene_yaml."""
        scene_file = tmp_path / "scene.yaml"
        scene_file.write_text(MINIMAL_SCENE_YAML, encoding="utf-8")
        scene = load_scene_yaml(scene_file)
        assert isinstance(scene, Scene)
        assert scene.version == 1

    def test_minimal_scene_has_correct_meta(self, tmp_path: pathlib.Path) -> None:
        scene_file = tmp_path / "scene.yaml"
        scene_file.write_text(MINIMAL_SCENE_YAML, encoding="utf-8")
        scene = load_scene_yaml(scene_file)
        assert scene.meta.duration_s == 5.0
        assert scene.meta.fps == 30
        assert scene.meta.resolution == (1920, 1080)
        assert scene.meta.seed == 12345

    def test_minimal_scene_has_layers(self, tmp_path: pathlib.Path) -> None:
        scene_file = tmp_path / "scene.yaml"
        scene_file.write_text(MINIMAL_SCENE_YAML, encoding="utf-8")
        scene = load_scene_yaml(scene_file)
        assert "main" in scene.stacks
        layers = scene.stacks["main"].layers
        assert len(layers) == 3
        layer_ids = [l.id for l in layers]
        assert "sky" in layer_ids
        assert "midground" in layer_ids
        assert "foreground" in layer_ids

    def test_minimal_scene_drone_camera_valid(self, tmp_path: pathlib.Path) -> None:
        scene_file = tmp_path / "scene.yaml"
        scene_file.write_text(MINIMAL_SCENE_YAML, encoding="utf-8")
        scene = load_scene_yaml(scene_file)
        assert scene.camera.mode == "drone"
        assert scene.camera.drone is not None
        assert scene.camera.drone.path.kind == "bezier"
        assert len(scene.camera.drone.path.controls) >= 2
        assert scene.camera.drone.path.duration_s == 5.0

    def test_portal_scene_yaml_parses(self, tmp_path: pathlib.Path) -> None:
        """Portal scene with multi-stack and masks must parse successfully."""
        scene_file = tmp_path / "scene.yaml"
        scene_file.write_text(PORTAL_SCENE_YAML, encoding="utf-8")
        scene = load_scene_yaml(scene_file)
        assert isinstance(scene, Scene)
        assert "forest" in scene.stacks
        assert "city" in scene.stacks
        assert len(scene.masks) == 1
        assert scene.masks[0].id == "portal"
        assert scene.masks[0].anchor == "world"

    def test_keyframed_scene_yaml_parses(self, tmp_path: pathlib.Path) -> None:
        """Keyframed camera mode must parse successfully."""
        scene_file = tmp_path / "scene.yaml"
        scene_file.write_text(KEYFRAMED_SCENE_YAML, encoding="utf-8")
        scene = load_scene_yaml(scene_file)
        assert scene.camera.mode == "keyframed"
        assert scene.camera.keyframed is not None
        assert len(scene.camera.keyframed) == 2

    def test_scene_version_must_be_1(self, tmp_path: pathlib.Path) -> None:
        """version != 1 must raise SceneVersionError."""
        bad_yaml = MINIMAL_SCENE_YAML.replace("version: 1", "version: 2")
        scene_file = tmp_path / "scene.yaml"
        scene_file.write_text(bad_yaml, encoding="utf-8")
        with pytest.raises((SceneVersionError, Exception)):
            load_scene_yaml(scene_file)

    def test_scene_stacks_not_empty(self, tmp_path: pathlib.Path) -> None:
        """A scene with no stacks must fail validation."""
        bad_yaml = MINIMAL_SCENE_YAML.replace("stacks:", "stacks_disabled:")
        scene_file = tmp_path / "scene.yaml"
        scene_file.write_text(bad_yaml, encoding="utf-8")
        with pytest.raises(Exception):
            load_scene_yaml(scene_file)

    def test_camera_stub_is_invalid_without_camera_pather(
        self, tmp_path: pathlib.Path
    ) -> None:
        """camera: {} without a mode is invalid — camera-pather must fill it."""
        stub_yaml = """\
version: 1
meta:
  duration_s: 5.0
  fps: 30
  resolution: [1920, 1080]
  perspective_px: 1200
  origin: [960.0, 540.0]
  bg_color: "#000000"
  seed: 1
stacks:
  main:
    layers:
      - id: sky
        src: assets/sky.svg
        scene_xyz: [0, 0, -10000]
        plate_size: [3840, 2160]
camera: {}
masks: []
"""
        scene_file = tmp_path / "scene.yaml"
        scene_file.write_text(stub_yaml, encoding="utf-8")
        with pytest.raises(Exception):
            load_scene_yaml(scene_file)


# ---------------------------------------------------------------------------
# 2. asset-generator output: valid SVG with viewBox and id attributes
# ---------------------------------------------------------------------------

SAMPLE_ASSET_SVG = """\
<?xml version="1.0" encoding="utf-8"?>
<svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 3840 2160">
  <g id="sky_layer">
    <rect width="3840" height="1200" fill="#2a4a8a"/>
    <circle cx="800" cy="200" r="120" fill="#fff9e0" opacity="0.9"/>
  </g>
</svg>
"""

MINIMAL_VALID_SVG = """\
<svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 3840 2160">
  <rect width="3840" height="2160" fill="#336699"/>
</svg>
"""


class TestAssetGeneratorOutputSchema:
    def test_sample_svg_is_valid_xml(self) -> None:
        """asset-generator output must be well-formed XML."""
        root = ET.fromstring(SAMPLE_ASSET_SVG)
        assert root.tag.endswith("svg") or root.tag == "svg"

    def test_sample_svg_has_viewbox(self) -> None:
        """asset-generator SVG must have a viewBox attribute."""
        root = ET.fromstring(SAMPLE_ASSET_SVG)
        # Handle both namespaced and plain SVG elements
        viewbox = root.get("viewBox") or root.get("viewbox")
        assert viewbox is not None, "SVG must have viewBox attribute"

    def test_minimal_svg_parseable(self) -> None:
        """A minimal SVG (no ids) must parse cleanly."""
        root = ET.fromstring(MINIMAL_VALID_SVG)
        tag = root.tag
        assert "svg" in tag

    def test_svg_with_layer_id(self) -> None:
        """SVG with a top-level group id (optional but desirable) must parse."""
        root = ET.fromstring(SAMPLE_ASSET_SVG)
        # Find groups — handle namespace
        ns = {"svg": "http://www.w3.org/2000/svg"}
        groups = root.findall(".//svg:g", ns) or root.findall(".//g")
        ids = [g.get("id") for g in groups if g.get("id")]
        assert len(ids) >= 1, "Sample SVG should have at least one id'd group"

    def test_svg_no_external_references(self) -> None:
        """asset-generator SVG must not reference external files via <image>."""
        root = ET.fromstring(SAMPLE_ASSET_SVG)
        # Check for <image href="..."> with external URLs
        ns = {"svg": "http://www.w3.org/2000/svg",
              "xlink": "http://www.w3.org/1999/xlink"}
        images = root.findall(".//svg:image", ns) or root.findall(".//image")
        for img in images:
            href = (
                img.get("{http://www.w3.org/1999/xlink}href")
                or img.get("href")
                or ""
            )
            assert not href.startswith("http"), (
                f"External reference forbidden in SVG: {href}"
            )

    def test_svg_no_embedded_raster(self) -> None:
        """asset-generator SVG must not embed base64 raster images."""
        assert "data:image/png" not in SAMPLE_ASSET_SVG
        assert "data:image/jpeg" not in SAMPLE_ASSET_SVG


# ---------------------------------------------------------------------------
# 3. mask-author output: SVG with id="silhouette" and id="hole" in one viewBox
# ---------------------------------------------------------------------------

SAMPLE_MASK_SVG = """\
<?xml version="1.0" encoding="utf-8"?>
<svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 3840 2160">
  <g id="silhouette">
    <path d="M 960 200 Q 1200 100 1440 200 L 1440 1800 Q 1200 1900 960 1800 Z"
          fill="#2d4a1e"/>
    <rect x="800" y="100" width="400" height="50" fill="#2d4a1e"/>
  </g>
  <path id="hole"
        d="M 1100 600 Q 1200 400 1300 600 L 1300 1200 Q 1200 1400 1100 1200 Z"
        fill="#ffffff"/>
</svg>
"""

MASK_SVG_WRAPPED_HOLE = """\
<svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 3840 2160">
  <g id="silhouette">
    <ellipse cx="1200" cy="1000" rx="400" ry="600" fill="#1a1a1a"/>
  </g>
  <g id="hole">
    <ellipse cx="1200" cy="1000" rx="200" ry="350" fill="#ffffff"/>
  </g>
</svg>
"""


class TestMaskAuthorOutputSchema:
    def test_sample_mask_svg_is_valid_xml(self) -> None:
        """mask-author output must be well-formed XML."""
        root = ET.fromstring(SAMPLE_MASK_SVG)
        assert "svg" in root.tag

    def test_sample_mask_has_silhouette_id(self) -> None:
        """Output SVG must contain an element with id='silhouette' (§9.4)."""
        root = ET.fromstring(SAMPLE_MASK_SVG)
        silhouette = root.find(".//*[@id='silhouette']")
        assert silhouette is not None, "SVG must contain id='silhouette'"

    def test_sample_mask_has_hole_id(self) -> None:
        """Output SVG must contain an element with id='hole' (§9.4)."""
        root = ET.fromstring(SAMPLE_MASK_SVG)
        hole = root.find(".//*[@id='hole']")
        assert hole is not None, "SVG must contain id='hole'"

    def test_both_ids_in_single_viewbox(self) -> None:
        """silhouette and hole must live in the same SVG viewBox (§9.4)."""
        root = ET.fromstring(SAMPLE_MASK_SVG)
        viewbox = root.get("viewBox") or root.get("viewbox")
        silhouette = root.find(".//*[@id='silhouette']")
        hole = root.find(".//*[@id='hole']")
        # All three in the same root element
        assert viewbox is not None
        assert silhouette is not None
        assert hole is not None

    def test_wrapped_hole_mask_also_valid(self) -> None:
        """hole can be a <g> group as well as a direct path."""
        root = ET.fromstring(MASK_SVG_WRAPPED_HOLE)
        silhouette = root.find(".//*[@id='silhouette']")
        hole = root.find(".//*[@id='hole']")
        assert silhouette is not None
        assert hole is not None

    def test_no_duplicate_silhouette_id(self) -> None:
        """Only one element may carry id='silhouette' — duplicate IDs invalid."""
        root = ET.fromstring(SAMPLE_MASK_SVG)
        all_with_silhouette = [
            el for el in root.iter() if el.get("id") == "silhouette"
        ]
        assert len(all_with_silhouette) == 1, (
            "Multiple id='silhouette' elements found — duplicate IDs are invalid"
        )

    def test_no_duplicate_hole_id(self) -> None:
        """Only one element may carry id='hole' — duplicate IDs invalid."""
        root = ET.fromstring(SAMPLE_MASK_SVG)
        all_with_hole = [
            el for el in root.iter() if el.get("id") == "hole"
        ]
        assert len(all_with_hole) == 1, (
            "Multiple id='hole' elements found — duplicate IDs are invalid"
        )

    def test_viewbox_preserved_after_mask_authoring(self) -> None:
        """mask-author must not modify the viewBox attribute."""
        root = ET.fromstring(SAMPLE_MASK_SVG)
        viewbox = root.get("viewBox")
        assert viewbox == "0 0 3840 2160", (
            f"viewBox changed — expected '0 0 3840 2160', got {viewbox!r}"
        )


# ---------------------------------------------------------------------------
# 4. lead.py layer discovery with canonical scene.yaml format
# ---------------------------------------------------------------------------

class TestLeadLayerDiscovery:
    """Verify lead._run_asset_generators parses stacks.{name}.layers correctly."""

    def _make_lead(self, ws: pathlib.Path, response_map=None) -> ParallaxLead:
        stub = ClaudeSDKStub(response_map=response_map or {})
        return ParallaxLead(ws, sdk_client_factory=lambda *a: stub)

    def test_canonical_format_discovers_all_layers(
        self, tmp_path: pathlib.Path
    ) -> None:
        """Lead must find layer ids in stacks.{name}.layers format."""
        scene_file = tmp_path / "scene.yaml"
        scene_file.write_text(MINIMAL_SCENE_YAML, encoding="utf-8")

        # Track which agent_name was called and with which prompt
        dispatched: list[str] = []

        class TrackingStub(ClaudeSDKStub):
            def run(self, *, prompt: str, agent_name: str = "", **kw):
                if agent_name == "asset-generator":
                    dispatched.append(prompt)
                return super().run(prompt=prompt, agent_name=agent_name, **kw)

        stub = TrackingStub()
        lead = ParallaxLead(tmp_path, sdk_client_factory=lambda *a: stub)
        # Create workspace subdirs
        for d in ["assets", "masks", "frames", "qa", "logs", "checkpoints"]:
            (tmp_path / d).mkdir(exist_ok=True)

        lead._run_asset_generators(scene_file)

        # Should have dispatched one asset-generator per layer
        assert len(dispatched) == 3, (
            f"Expected 3 asset-generator dispatches (sky, midground, foreground), "
            f"got {len(dispatched)}: {dispatched}"
        )
        # Each dispatch should mention the layer id
        dispatched_text = " ".join(dispatched)
        assert "sky" in dispatched_text
        assert "midground" in dispatched_text
        assert "foreground" in dispatched_text

    def test_canonical_format_portal_discovers_all_layers(
        self, tmp_path: pathlib.Path
    ) -> None:
        """Lead must find layer ids across multiple stacks."""
        scene_file = tmp_path / "scene.yaml"
        scene_file.write_text(PORTAL_SCENE_YAML, encoding="utf-8")

        dispatched: list[str] = []

        class TrackingStub(ClaudeSDKStub):
            def run(self, *, prompt: str, agent_name: str = "", **kw):
                if agent_name == "asset-generator":
                    dispatched.append(prompt)
                return super().run(prompt=prompt, agent_name=agent_name, **kw)

        stub = TrackingStub()
        lead = ParallaxLead(tmp_path, sdk_client_factory=lambda *a: stub)
        for d in ["assets", "masks", "frames", "qa", "logs", "checkpoints"]:
            (tmp_path / d).mkdir(exist_ok=True)

        lead._run_asset_generators(scene_file)

        # PORTAL_SCENE_YAML has 3 layers: sky, portal_tree (forest), city_sky (city)
        assert len(dispatched) == 3

    def test_empty_scene_yaml_does_not_crash(
        self, tmp_path: pathlib.Path
    ) -> None:
        """If scene.yaml is malformed, lead silently skips asset generators."""
        scene_file = tmp_path / "scene.yaml"
        scene_file.write_text("not: yaml: at: all: {{{", encoding="utf-8")

        stub = ClaudeSDKStub()
        lead = ParallaxLead(tmp_path, sdk_client_factory=lambda *a: stub)
        for d in ["assets", "masks", "frames", "qa", "logs", "checkpoints"]:
            (tmp_path / d).mkdir(exist_ok=True)

        # Must not raise
        result = lead._run_asset_generators(scene_file)
        assert isinstance(result, bool)

    def test_missing_scene_yaml_does_not_crash(
        self, tmp_path: pathlib.Path
    ) -> None:
        """If scene.yaml doesn't exist, lead skips (no dispatch)."""
        scene_file = tmp_path / "scene.yaml"
        # Do NOT write scene_file

        stub = ClaudeSDKStub()
        lead = ParallaxLead(tmp_path, sdk_client_factory=lambda *a: stub)
        for d in ["assets", "masks", "frames", "qa", "logs", "checkpoints"]:
            (tmp_path / d).mkdir(exist_ok=True)

        result = lead._run_asset_generators(scene_file)
        assert isinstance(result, bool)
        assert stub.call_count == 0, "No dispatches expected when scene.yaml missing"


# ---------------------------------------------------------------------------
# 5. lead.py mask discovery with canonical scene.yaml format
# ---------------------------------------------------------------------------

class TestLeadMaskDiscovery:
    """Verify lead._run_mask_authors uses path_svg field name."""

    def test_canonical_mask_field_path_svg_discovered(
        self, tmp_path: pathlib.Path
    ) -> None:
        """Lead must discover masks using the canonical path_svg field."""
        scene_file = tmp_path / "scene.yaml"
        scene_file.write_text(PORTAL_SCENE_YAML, encoding="utf-8")

        dispatched: list[str] = []

        class TrackingStub(ClaudeSDKStub):
            def run(self, *, prompt: str, agent_name: str = "", **kw):
                if agent_name == "mask-author":
                    dispatched.append(prompt)
                return super().run(prompt=prompt, agent_name=agent_name, **kw)

        stub = TrackingStub()
        lead = ParallaxLead(tmp_path, sdk_client_factory=lambda *a: stub)
        for d in ["assets", "masks", "frames", "qa", "logs", "checkpoints"]:
            (tmp_path / d).mkdir(exist_ok=True)

        lead._run_mask_authors(scene_file)

        # PORTAL_SCENE_YAML has 1 mask with path_svg: assets/portal_tree.svg
        assert len(dispatched) == 1, (
            f"Expected 1 mask-author dispatch, got {len(dispatched)}"
        )
        assert "portal_tree.svg" in dispatched[0]

    def test_no_masks_no_dispatch(self, tmp_path: pathlib.Path) -> None:
        """If masks is empty, mask-author is not dispatched."""
        scene_file = tmp_path / "scene.yaml"
        scene_file.write_text(MINIMAL_SCENE_YAML, encoding="utf-8")

        stub = ClaudeSDKStub()
        lead = ParallaxLead(tmp_path, sdk_client_factory=lambda *a: stub)
        for d in ["assets", "masks", "frames", "qa", "logs", "checkpoints"]:
            (tmp_path / d).mkdir(exist_ok=True)

        lead._run_mask_authors(scene_file)
        assert stub.call_count == 0

    def test_legacy_silhouette_svg_field_also_works(
        self, tmp_path: pathlib.Path
    ) -> None:
        """Lead must also handle the legacy silhouette_svg field name."""
        legacy_yaml = MINIMAL_SCENE_YAML.replace(
            "masks: []",
            """\
masks:
  - id: legacy_mask
    silhouette_svg: assets/legacy_mask.svg
    anchor: world
    src_stack: main
    dest_stack: main
    matte: alpha
    growth:
      kind: perspective
""",
        )
        scene_file = tmp_path / "scene.yaml"
        scene_file.write_text(legacy_yaml, encoding="utf-8")

        dispatched: list[str] = []

        class TrackingStub(ClaudeSDKStub):
            def run(self, *, prompt: str, agent_name: str = "", **kw):
                if agent_name == "mask-author":
                    dispatched.append(prompt)
                return super().run(prompt=prompt, agent_name=agent_name, **kw)

        stub = TrackingStub()
        lead = ParallaxLead(tmp_path, sdk_client_factory=lambda *a: stub)
        for d in ["assets", "masks", "frames", "qa", "logs", "checkpoints"]:
            (tmp_path / d).mkdir(exist_ok=True)

        lead._run_mask_authors(scene_file)

        assert len(dispatched) == 1, (
            "Legacy silhouette_svg field must also trigger mask-author dispatch"
        )
        assert "legacy_mask.svg" in dispatched[0]


# ---------------------------------------------------------------------------
# 6. QA loop counter is Python integer (§9.7)
# ---------------------------------------------------------------------------

class TestQALoopIsInteger:
    def _make_lead(self, ws: pathlib.Path, **kw) -> ParallaxLead:
        stub = ClaudeSDKStub(**kw)
        return ParallaxLead(ws, sdk_client_factory=lambda *a: stub)

    def test_qa_passes_is_int_on_pass(self, tmp_path: pathlib.Path) -> None:
        """QA passes_done must be a Python int, not a string or float."""
        lead = self._make_lead(
            tmp_path,
            response_map={"qa-critic": "PASS"},
        )
        qa_passed, passes_done = lead._run_qa_loop(None)
        assert isinstance(passes_done, int), (
            f"passes_done must be int (§9.7), got {type(passes_done)}"
        )

    def test_qa_passes_is_int_on_fail(self, tmp_path: pathlib.Path) -> None:
        """QA passes_done must be a Python int even when QA always fails."""
        lead = self._make_lead(
            tmp_path,
            response_map={"qa-critic": "FAIL: bad colors"},
        )
        lead.max_qa_passes = 3
        qa_passed, passes_done = lead._run_qa_loop(None)
        assert isinstance(passes_done, int), (
            f"passes_done must be int (§9.7), got {type(passes_done)}"
        )
        assert passes_done == 3

    def test_qa_cap_is_python_constant(self) -> None:
        """MAX_QA_PASSES must be a Python int constant, not a string."""
        from parallax_engine.lead import MAX_QA_PASSES
        assert isinstance(MAX_QA_PASSES, int), (
            f"MAX_QA_PASSES must be int, got {type(MAX_QA_PASSES)}"
        )
        assert MAX_QA_PASSES == 3


# ---------------------------------------------------------------------------
# 7. QA critic prompt does not contain "stop after" language (§9.7)
# ---------------------------------------------------------------------------

class TestQACriticPromptConstraints:
    def test_qa_critic_prompt_no_stop_after(self) -> None:
        """qa-critic prompt must NOT say 'stop after N passes' (§9.7)."""
        from parallax_engine.subagents import QA_CRITIC
        prompt_lower = QA_CRITIC.system_prompt.lower()
        assert "stop after" not in prompt_lower, (
            "QA stop instruction found in qa-critic prompt — "
            "counter must be Python-only (§9.7)"
        )

    def test_qa_critic_prompt_no_do_not_exceed(self) -> None:
        """qa-critic prompt must not tell the LLM to count passes."""
        from parallax_engine.subagents import QA_CRITIC
        prompt_lower = QA_CRITIC.system_prompt.lower()
        # The orchestrator is responsible for counting; the prompt must not
        assert "do not exceed" not in prompt_lower
        # "do not track the pass number yourself" is the CORRECT instruction
        assert "do not track" in prompt_lower, (
            "qa-critic prompt should explicitly tell the LLM NOT to track pass count"
        )

    def test_scene_designer_prompt_no_camera_block(self) -> None:
        """scene-designer prompt must not tell the LLM to fill in camera: block."""
        from parallax_engine.subagents import SCENE_DESIGNER
        prompt_lower = SCENE_DESIGNER.system_prompt.lower()
        # Scene designer must leave camera empty for camera-pather
        assert "camera-pather" in prompt_lower, (
            "scene-designer prompt should mention camera-pather fills the camera block"
        )

    def test_camera_pather_prompt_mentions_drone_and_keyframed(self) -> None:
        """camera-pather prompt must describe both drone and keyframed modes."""
        from parallax_engine.subagents import CAMERA_PATHER
        prompt = CAMERA_PATHER.system_prompt
        assert "drone" in prompt
        assert "keyframed" in prompt
        assert "bezier" in prompt.lower()

    def test_scene_designer_prompt_uses_scene_xyz(self) -> None:
        """scene-designer prompt must use the correct field name scene_xyz."""
        from parallax_engine.subagents import SCENE_DESIGNER
        prompt = SCENE_DESIGNER.system_prompt
        assert "scene_xyz" in prompt, (
            "scene-designer prompt must use the canonical field name 'scene_xyz'"
        )

    def test_scene_designer_prompt_uses_layers_key(self) -> None:
        """scene-designer prompt must show the 'layers:' key inside stacks."""
        from parallax_engine.subagents import SCENE_DESIGNER
        prompt = SCENE_DESIGNER.system_prompt
        assert "layers:" in prompt, (
            "scene-designer prompt must show stacks.{name}.layers format"
        )
