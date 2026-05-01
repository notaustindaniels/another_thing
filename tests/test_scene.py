"""
tests/test_scene.py — Unit tests for parallax_engine/scene.py (P1.M03).

Covers:
- Round-trip parse of all three §6 worked examples
- find_layer() qualified name lookup
- SceneVersionError for version != 1
- Pydantic ValidationError for schema violations
- Default values for optional fields
- drone vs keyframed camera validation
- mask anchor validation (world requires extra fields)
"""

import textwrap

import pytest
import yaml
from pydantic import ValidationError

from parallax_engine.scene import (
    CameraSpec,
    DroneCamera,
    FisheyeSpec,
    GlobalPost,
    GrainSpec,
    KeyframedEntry,
    LayerPost,
    LayerSpec,
    MaskGrowth,
    MaskSpec,
    PostSpec,
    Scene,
    SceneMeta,
    SceneVersionError,
    StackSpec,
    VignetteSpec,
    dump_scene_yaml,
    load_scene_dict,
)

# ---------------------------------------------------------------------------
# §6 example YAML fixtures (inline strings, not files)
# ---------------------------------------------------------------------------

SCENE_6_1 = textwrap.dedent("""\
version: 1
meta:
  duration_s: 10
  fps: 30
  resolution: [1920, 1080]
  perspective_px: 1200
  origin: [960, 540]
  bg_color: "#000000"
  seed: 4242

stacks:
  forest:
    layers:
      - { id: sky,        src: assets/sky_dawn.svg,   scene_xyz: [0,0,-12000], plate_size: [3840,2160] }
      - { id: mountains,  src: assets/mountains.svg,  scene_xyz: [0,0,-9000],  plate_size: [3840,2160], post: { dof_blur_px: 5, depth_fade: 0.4 } }
      - { id: trees_far,  src: assets/trees_far.svg,  scene_xyz: [0,0,-6500],  plate_size: [3840,2160], post: { dof_blur_px: 3, depth_fade: 0.25 } }
      - { id: trees_mid,  src: assets/trees_mid.svg,  scene_xyz: [0,0,-4500],  plate_size: [3840,2160] }
      - { id: trees_near, src: assets/trees_near.svg, scene_xyz: [0,0,-2500],  plate_size: [3840,2160] }
      - { id: foreground, src: assets/leaves_fg.svg,  scene_xyz: [0,0,-500],   plate_size: [3840,2160] }

camera:
  mode: drone
  drone:
    path:
      kind: bezier
      controls: [[0,0,0], [180,30,-3000], [-150,-25,-7000], [0,0,-11500]]
      duration_s: 10
    poi_lookahead_s: 0.55
    spring_halflife_s: 0.18
    noise: { z_amp: 22, xy_amp: 6, hz: 0.7 }
    bank_from_velocity: 0.40

masks: []

post:
  global:
    vignette: { strength: 0.5, radius: 0.85 }
    grain:    { sigma: 4 }
    fisheye:  { k1: 0.20, k2: 0.04 }
""")

SCENE_6_2 = textwrap.dedent("""\
version: 1
meta:
  duration_s: 24
  fps: 30
  resolution: [1920, 1080]
  perspective_px: 1200
  origin: [960, 540]
  seed: 7

stacks:
  mountains:
    layers:
      - { id: bg, src: assets/m_bg.svg, scene_xyz: [0,0,-7000], plate_size: [3840,2160] }
      - { id: mg, src: assets/m_mg.svg, scene_xyz: [0,0,-4000], plate_size: [3840,2160] }
      - { id: fg, src: assets/m_fg.svg, scene_xyz: [0,0,-1500], plate_size: [3840,2160] }
  river:
    layers:
      - { id: bg, src: assets/r_bg.svg, scene_xyz: [0,0,-7000], plate_size: [3840,2160] }
      - { id: mg, src: assets/r_mg.svg, scene_xyz: [0,0,-4000], plate_size: [3840,2160] }
  lanterns:
    layers:
      - { id: bg, src: assets/l_bg.svg, scene_xyz: [0,0,-7000], plate_size: [3840,2160] }
  desert:
    layers:
      - { id: bg, src: assets/d_bg.svg, scene_xyz: [0,0,-7000], plate_size: [3840,2160] }
  night:
    layers:
      - { id: bg, src: assets/n_bg.svg, scene_xyz: [0,0,-7000], plate_size: [3840,2160] }

camera:
  mode: keyframed
  keyframed:
    - { t: 0,  x: 0,    y: 0, z: 0,     yaw: 0,    pitch: 0, roll: 0, ease: easeInOutCubic }
    - { t: 6,  x: 300,  y: 0, z: -1500, yaw: 0.10 }
    - { t: 12, x: -200, y: 0, z: -3000, yaw: -0.05 }
    - { t: 18, x: 200,  y: 0, z: -4500, yaw: 0.05 }
    - { t: 24, x: 0,    y: 0, z: -6000, yaw: 0 }

masks:
  - { id: w1, src_stack: mountains, dest_stack: river,    anchor: screen, growth: { kind: radius,    t0: 5.0,  t1: 6.0,  r0: 0, r1: 1400, feather_px: 60 },  matte: alpha }
  - { id: w2, src_stack: river,     dest_stack: lanterns, anchor: screen, growth: { kind: radius,    t0: 11.0, t1: 12.0, r0: 0, r1: 1400, feather_px: 200 }, matte: alpha }
  - { id: w3, src_stack: lanterns,  dest_stack: desert,   anchor: screen, growth: { kind: gradient,  t0: 17,   t1: 18,   axis: x }, matte: luminance }
  - { id: w4, src_stack: desert,    dest_stack: night,    anchor: screen, growth: { kind: displaced_edge, t0: 22, t1: 23, displacement_map: assets/disp.png, amp: 80 }, matte: alpha }

post:
  global:
    vignette: { strength: 0.3 }
    grain:    { sigma: 2 }
""")

SCENE_6_3 = textwrap.dedent("""\
version: 1
meta:
  duration_s: 8
  fps: 30
  resolution: [1920, 1080]
  perspective_px: 1200
  origin: [960, 540]
  seed: 1

stacks:
  forest:
    layers:
      - { id: sky,         src: assets/sky.svg,         scene_xyz: [0,0,-10500], plate_size: [3840,2160] }
      - { id: trees_far,   src: assets/trees_far.svg,   scene_xyz: [0,0,-8500],  plate_size: [3840,2160] }
      - { id: trees_mid,   src: assets/trees_mid.svg,   scene_xyz: [0,0,-6500],  plate_size: [3840,2160] }
      - { id: portal_tree, src: assets/portal_tree.svg, scene_xyz: [0,0,-4500],  plate_size: [3840,2160], svg_paint_id: silhouette }
      - { id: leaves_mid,  src: assets/leaves_mid.svg,  scene_xyz: [0,0,-2500],  plate_size: [3840,2160] }
      - { id: leaves_fg,   src: assets/leaves_fg.svg,   scene_xyz: [0,0,-500],   plate_size: [3840,2160] }
  city:
    layers:
      - { id: city_sky,  src: assets/city_sky.svg,  scene_xyz: [0,0,-10500], plate_size: [3840,2160] }
      - { id: city_far,  src: assets/city_far.svg,  scene_xyz: [0,0,-7000],  plate_size: [3840,2160] }
      - { id: city_near, src: assets/city_near.svg, scene_xyz: [0,0,-3500],  plate_size: [3840,2160] }

camera:
  mode: drone
  drone:
    path:
      kind: bezier
      controls: [[0,0,0],[40,10,-2000],[-30,-10,-3500],[0,0,-4400]]
      duration_s: 8
    poi_lookahead_s: 0.5
    spring_halflife_s: 0.20
    noise: { z_amp: 18, xy_amp: 5, hz: 0.6 }
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
    growth: { kind: perspective }

post:
  global:
    vignette: { strength: 0.45 }
    grain:    { sigma: 3 }
    fisheye:  { k1: 0.18 }
""")


def _raw(yaml_str: str) -> dict:
    return yaml.safe_load(yaml_str)


# ===========================================================================
# TestSceneMeta
# ===========================================================================

class TestSceneMeta:
    def test_fields_parsed(self):
        scene = load_scene_dict(_raw(SCENE_6_1))
        m = scene.meta
        assert m.duration_s == 10.0
        assert m.fps == 30
        assert m.resolution == (1920, 1080)
        assert m.perspective_px == 1200.0
        assert m.origin == (960.0, 540.0)
        assert m.bg_color == "#000000"
        assert m.seed == 4242

    def test_bg_color_default(self):
        # §6.2 has no bg_color key — should default to "#000000"
        scene = load_scene_dict(_raw(SCENE_6_2))
        assert scene.meta.bg_color == "#000000"

    def test_resolution_is_tuple(self):
        scene = load_scene_dict(_raw(SCENE_6_1))
        assert isinstance(scene.meta.resolution, tuple)
        assert scene.meta.resolution == (1920, 1080)

    def test_origin_is_tuple(self):
        scene = load_scene_dict(_raw(SCENE_6_1))
        assert isinstance(scene.meta.origin, tuple)
        assert scene.meta.origin == (960.0, 540.0)

    def test_extra_field_rejected(self):
        raw = _raw(SCENE_6_1)
        raw["meta"]["unknown_field"] = 42
        with pytest.raises(ValidationError):
            load_scene_dict(raw)


# ===========================================================================
# TestLayerSpec
# ===========================================================================

class TestLayerSpec:
    def test_layer_fields(self):
        scene = load_scene_dict(_raw(SCENE_6_1))
        sky = scene.stacks["forest"].layers[0]
        assert sky.id == "sky"
        assert sky.src == "assets/sky_dawn.svg"
        assert sky.scene_xyz == (0.0, 0.0, -12000.0)
        assert sky.plate_size == (3840, 2160)

    def test_layer_post_defaults(self):
        scene = load_scene_dict(_raw(SCENE_6_1))
        sky = scene.stacks["forest"].layers[0]
        assert sky.post.dof_blur_px == 0.0
        assert sky.post.depth_fade == 0.0

    def test_layer_post_non_defaults(self):
        scene = load_scene_dict(_raw(SCENE_6_1))
        mountains = scene.stacks["forest"].layers[1]
        assert mountains.post.dof_blur_px == 5.0
        assert mountains.post.depth_fade == 0.4

    def test_layer_svg_paint_id(self):
        scene = load_scene_dict(_raw(SCENE_6_3))
        portal_tree = scene.stacks["forest"].layers[3]
        assert portal_tree.id == "portal_tree"
        assert portal_tree.svg_paint_id == "silhouette"

    def test_layer_svg_paint_id_absent(self):
        scene = load_scene_dict(_raw(SCENE_6_1))
        sky = scene.stacks["forest"].layers[0]
        assert sky.svg_paint_id is None

    def test_scene_xyz_is_tuple(self):
        scene = load_scene_dict(_raw(SCENE_6_1))
        xyz = scene.stacks["forest"].layers[0].scene_xyz
        assert isinstance(xyz, tuple)
        assert len(xyz) == 3

    def test_anchor_center_default(self):
        scene = load_scene_dict(_raw(SCENE_6_1))
        assert scene.stacks["forest"].layers[0].anchor == "center"


# ===========================================================================
# TestCameraSpec — drone mode
# ===========================================================================

class TestDroneCamera:
    def test_mode(self):
        scene = load_scene_dict(_raw(SCENE_6_1))
        assert scene.camera.mode == "drone"
        assert scene.camera.drone is not None
        assert scene.camera.keyframed is None

    def test_bezier_path(self):
        cam = load_scene_dict(_raw(SCENE_6_1)).camera.drone
        assert cam.path.kind == "bezier"
        assert len(cam.path.controls) == 4
        assert cam.path.duration_s == 10.0
        # first control point
        assert cam.path.controls[0] == (0.0, 0.0, 0.0)

    def test_spring_parameters(self):
        cam = load_scene_dict(_raw(SCENE_6_1)).camera.drone
        assert cam.poi_lookahead_s == 0.55
        assert cam.spring_halflife_s == 0.18
        assert cam.bank_from_velocity == 0.40

    def test_noise(self):
        cam = load_scene_dict(_raw(SCENE_6_1)).camera.drone
        assert cam.noise.z_amp == 22.0
        assert cam.noise.xy_amp == 6.0
        assert cam.noise.hz == 0.7

    def test_drone_missing_block_raises(self):
        raw = _raw(SCENE_6_1)
        del raw["camera"]["drone"]
        with pytest.raises(ValidationError, match="drone"):
            load_scene_dict(raw)

    def test_bezier_one_control_raises(self):
        raw = _raw(SCENE_6_1)
        raw["camera"]["drone"]["path"]["controls"] = [[0, 0, 0]]
        with pytest.raises(ValidationError):
            load_scene_dict(raw)


# ===========================================================================
# TestCameraSpec — keyframed mode
# ===========================================================================

class TestKeyframedCamera:
    def test_mode(self):
        scene = load_scene_dict(_raw(SCENE_6_2))
        assert scene.camera.mode == "keyframed"
        assert scene.camera.keyframed is not None
        assert scene.camera.drone is None

    def test_keyframe_count(self):
        kfs = load_scene_dict(_raw(SCENE_6_2)).camera.keyframed
        assert len(kfs) == 5

    def test_first_keyframe_fields(self):
        kf = load_scene_dict(_raw(SCENE_6_2)).camera.keyframed[0]
        assert kf.t == 0.0
        assert kf.x == 0.0
        assert kf.ease == "easeInOutCubic"

    def test_keyframe_defaults(self):
        # second keyframe has no pitch/roll/ease
        kf = load_scene_dict(_raw(SCENE_6_2)).camera.keyframed[1]
        assert kf.t == 6.0
        assert kf.yaw == pytest.approx(0.10)
        assert kf.pitch == 0.0
        assert kf.roll == 0.0
        assert kf.ease == "linear"

    def test_keyframed_single_kf_raises(self):
        raw = _raw(SCENE_6_2)
        raw["camera"]["keyframed"] = [{"t": 0.0}]
        with pytest.raises(ValidationError, match="keyframe"):
            load_scene_dict(raw)

    def test_invalid_easing_raises(self):
        raw = _raw(SCENE_6_2)
        raw["camera"]["keyframed"][0]["ease"] = "easeInExpo"
        with pytest.raises(ValidationError):
            load_scene_dict(raw)


# ===========================================================================
# TestMaskSpec
# ===========================================================================

class TestMaskSpec:
    def test_screen_radius_mask(self):
        scene = load_scene_dict(_raw(SCENE_6_2))
        m = scene.masks[0]
        assert m.id == "w1"
        assert m.anchor == "screen"
        assert m.src_stack == "mountains"
        assert m.dest_stack == "river"
        assert m.matte == "alpha"
        assert m.invert is False
        assert m.growth.kind == "radius"
        assert m.growth.t0 == 5.0
        assert m.growth.t1 == 6.0
        assert m.growth.r0 == 0.0
        assert m.growth.r1 == 1400.0
        assert m.growth.feather_px == 60.0

    def test_screen_gradient_mask(self):
        m = load_scene_dict(_raw(SCENE_6_2)).masks[2]
        assert m.growth.kind == "gradient"
        assert m.growth.axis == "x"
        assert m.matte == "luminance"

    def test_screen_displaced_edge_mask(self):
        m = load_scene_dict(_raw(SCENE_6_2)).masks[3]
        assert m.growth.kind == "displaced_edge"
        assert m.growth.displacement_map == "assets/disp.png"
        assert m.growth.amp == 80.0

    def test_world_anchor_portal_mask(self):
        scene = load_scene_dict(_raw(SCENE_6_3))
        m = scene.masks[0]
        assert m.id == "portal"
        assert m.anchor == "world"
        assert m.path_svg == "assets/portal_tree.svg"
        assert m.silhouette_id_in_svg == "silhouette"
        assert m.path_id_in_svg == "hole"
        assert m.attached_to_layer == "forest.portal_tree"
        assert m.growth.kind == "perspective"

    def test_world_anchor_missing_path_svg_raises(self):
        raw = _raw(SCENE_6_3)
        del raw["masks"][0]["path_svg"]
        with pytest.raises(ValidationError, match="world"):
            load_scene_dict(raw)

    def test_radius_growth_missing_r0_raises(self):
        raw = _raw(SCENE_6_2)
        del raw["masks"][0]["growth"]["r0"]
        with pytest.raises(ValidationError, match="r0"):
            load_scene_dict(raw)

    def test_empty_masks_list(self):
        scene = load_scene_dict(_raw(SCENE_6_1))
        assert scene.masks == []


# ===========================================================================
# TestPostSpec
# ===========================================================================

class TestPostSpec:
    def test_global_post_parsed(self):
        scene = load_scene_dict(_raw(SCENE_6_1))
        assert scene.post is not None
        gp = scene.post.global_
        assert gp is not None
        assert gp.vignette.strength == 0.5
        assert gp.vignette.radius == 0.85
        assert gp.grain.sigma == 4.0
        assert gp.fisheye.k1 == pytest.approx(0.20)
        assert gp.fisheye.k2 == pytest.approx(0.04)

    def test_vignette_radius_default(self):
        # §6.3 vignette has no radius key
        scene = load_scene_dict(_raw(SCENE_6_3))
        gp = scene.post.global_
        assert gp.vignette.radius == pytest.approx(0.85)

    def test_fisheye_k2_default(self):
        # §6.3 fisheye has only k1
        scene = load_scene_dict(_raw(SCENE_6_3))
        gp = scene.post.global_
        assert gp.fisheye.k2 == pytest.approx(0.0)

    def test_post_absent(self):
        raw = _raw(SCENE_6_1)
        del raw["post"]
        scene = load_scene_dict(raw)
        assert scene.post is None

    def test_light_leaks_absent(self):
        scene = load_scene_dict(_raw(SCENE_6_1))
        assert scene.post.global_.light_leaks is None

    def test_color_grade_absent(self):
        scene = load_scene_dict(_raw(SCENE_6_1))
        assert scene.post.global_.color_grade is None


# ===========================================================================
# TestFindLayer
# ===========================================================================

class TestFindLayer:
    def test_find_existing_layer(self):
        scene = load_scene_dict(_raw(SCENE_6_3))
        layer = scene.find_layer("forest.portal_tree")
        assert layer.id == "portal_tree"
        assert layer.src == "assets/portal_tree.svg"

    def test_find_city_layer(self):
        scene = load_scene_dict(_raw(SCENE_6_3))
        layer = scene.find_layer("city.city_sky")
        assert layer.id == "city_sky"

    def test_find_missing_layer_raises(self):
        scene = load_scene_dict(_raw(SCENE_6_3))
        with pytest.raises(KeyError, match="nonexistent"):
            scene.find_layer("forest.nonexistent")

    def test_find_missing_stack_raises(self):
        scene = load_scene_dict(_raw(SCENE_6_3))
        with pytest.raises(KeyError, match="jungle"):
            scene.find_layer("jungle.sky")

    def test_find_malformed_name_raises(self):
        scene = load_scene_dict(_raw(SCENE_6_3))
        with pytest.raises(ValueError, match="<stack>"):
            scene.find_layer("sky")  # no dot


# ===========================================================================
# TestVersionValidation
# ===========================================================================

class TestVersionValidation:
    def test_version_1_accepted(self):
        scene = load_scene_dict(_raw(SCENE_6_1))
        assert scene.version == 1

    def test_version_2_raises_scene_version_error(self):
        raw = _raw(SCENE_6_1)
        raw["version"] = 2
        with pytest.raises(SceneVersionError):
            load_scene_dict(raw)

    def test_version_0_raises(self):
        raw = _raw(SCENE_6_1)
        raw["version"] = 0
        with pytest.raises(SceneVersionError):
            load_scene_dict(raw)

    def test_version_none_raises(self):
        raw = _raw(SCENE_6_1)
        raw["version"] = None
        with pytest.raises(SceneVersionError):
            load_scene_dict(raw)

    def test_version_string_raises(self):
        raw = _raw(SCENE_6_1)
        raw["version"] = "1"
        # "1" != 1 (strict equality in _validate_raw)
        with pytest.raises(SceneVersionError):
            load_scene_dict(raw)

    def test_not_a_dict_raises(self):
        with pytest.raises(ValueError, match="mapping"):
            from parallax_engine.scene import _validate_raw
            _validate_raw("not a dict")


# ===========================================================================
# TestSchemaErrors
# ===========================================================================

class TestSchemaErrors:
    def test_missing_duration_s_raises(self):
        raw = _raw(SCENE_6_1)
        del raw["meta"]["duration_s"]
        with pytest.raises(ValidationError):
            load_scene_dict(raw)

    def test_missing_seed_raises(self):
        raw = _raw(SCENE_6_1)
        del raw["meta"]["seed"]
        with pytest.raises(ValidationError):
            load_scene_dict(raw)

    def test_empty_stacks_raises(self):
        raw = _raw(SCENE_6_1)
        raw["stacks"] = {}
        with pytest.raises(ValidationError):
            load_scene_dict(raw)

    def test_unknown_meta_field_raises(self):
        raw = _raw(SCENE_6_1)
        raw["meta"]["bogus"] = True
        with pytest.raises(ValidationError):
            load_scene_dict(raw)

    def test_unknown_camera_mode_raises(self):
        raw = _raw(SCENE_6_1)
        raw["camera"]["mode"] = "orbital"
        with pytest.raises(ValidationError):
            load_scene_dict(raw)

    def test_missing_stacks_raises(self):
        raw = _raw(SCENE_6_1)
        del raw["stacks"]
        with pytest.raises(ValidationError):
            load_scene_dict(raw)


# ===========================================================================
# TestRoundTrip
# ===========================================================================

class TestRoundTrip:
    """Parse → dump → parse and assert the two Scene objects are equivalent."""

    def _roundtrip(self, yaml_str: str) -> tuple:
        raw1 = yaml.safe_load(yaml_str)
        scene1 = load_scene_dict(raw1)
        dumped = dump_scene_yaml(scene1)
        raw2 = yaml.safe_load(dumped)
        scene2 = load_scene_dict(raw2)
        return scene1, scene2

    def _assert_equal(self, scene1: "Scene", scene2: "Scene"):
        d1 = scene1.model_dump()
        d2 = scene2.model_dump()
        assert d1 == d2, "Round-trip scenes differ"

    def test_roundtrip_6_1_forest(self):
        s1, s2 = self._roundtrip(SCENE_6_1)
        self._assert_equal(s1, s2)

    def test_roundtrip_6_2_biomes(self):
        s1, s2 = self._roundtrip(SCENE_6_2)
        self._assert_equal(s1, s2)

    def test_roundtrip_6_3_portal(self):
        s1, s2 = self._roundtrip(SCENE_6_3)
        self._assert_equal(s1, s2)

    def test_roundtrip_preserves_meta(self):
        s1, s2 = self._roundtrip(SCENE_6_1)
        assert s1.meta.seed == s2.meta.seed
        assert s1.meta.fps == s2.meta.fps
        assert s1.meta.resolution == s2.meta.resolution

    def test_roundtrip_preserves_layers(self):
        s1, s2 = self._roundtrip(SCENE_6_1)
        layers1 = s1.stacks["forest"].layers
        layers2 = s2.stacks["forest"].layers
        assert len(layers1) == len(layers2)
        for l1, l2 in zip(layers1, layers2):
            assert l1.id == l2.id
            assert l1.scene_xyz == l2.scene_xyz

    def test_roundtrip_preserves_masks(self):
        s1, s2 = self._roundtrip(SCENE_6_2)
        assert len(s1.masks) == len(s2.masks)
        for m1, m2 in zip(s1.masks, s2.masks):
            assert m1.id == m2.id
            assert m1.growth.kind == m2.growth.kind

    def test_roundtrip_portal_mask(self):
        s1, s2 = self._roundtrip(SCENE_6_3)
        m1 = s1.masks[0]
        m2 = s2.masks[0]
        assert m1.path_svg == m2.path_svg
        assert m1.attached_to_layer == m2.attached_to_layer

    def test_dump_is_valid_yaml(self):
        scene = load_scene_dict(_raw(SCENE_6_1))
        dumped = dump_scene_yaml(scene)
        reparsed = yaml.safe_load(dumped)
        assert isinstance(reparsed, dict)
        assert reparsed["version"] == 1


# ===========================================================================
# TestAllExamplesParse (smoke)
# ===========================================================================

class TestAllExamplesParse:
    @pytest.mark.parametrize("yaml_str,expected_stacks", [
        (SCENE_6_1, ["forest"]),
        (SCENE_6_2, ["mountains", "river", "lanterns", "desert", "night"]),
        (SCENE_6_3, ["forest", "city"]),
    ])
    def test_stack_names(self, yaml_str, expected_stacks):
        scene = load_scene_dict(yaml.safe_load(yaml_str))
        assert list(scene.stacks.keys()) == expected_stacks

    @pytest.mark.parametrize("yaml_str,expected_mask_count", [
        (SCENE_6_1, 0),
        (SCENE_6_2, 4),
        (SCENE_6_3, 1),
    ])
    def test_mask_count(self, yaml_str, expected_mask_count):
        scene = load_scene_dict(yaml.safe_load(yaml_str))
        assert len(scene.masks) == expected_mask_count

    @pytest.mark.parametrize("yaml_str,expected_mode", [
        (SCENE_6_1, "drone"),
        (SCENE_6_2, "keyframed"),
        (SCENE_6_3, "drone"),
    ])
    def test_camera_mode(self, yaml_str, expected_mode):
        scene = load_scene_dict(yaml.safe_load(yaml_str))
        assert scene.camera.mode == expected_mode
