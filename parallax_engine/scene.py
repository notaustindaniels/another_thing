"""
parallax_engine/scene.py — Pydantic v2 models for the scene.yaml schema (§2.2).

The scene schema is versioned (version: 1).  The YAML loader rejects any other
version value with SceneVersionError.  Use ``load_scene_yaml(path)`` to parse a
scene file into a validated :class:`Scene` instance.

Public API
----------
- Scene                 top-level document
- SceneMeta             §2.2 meta block
- StackSpec / LayerSpec layer stacks and individual plate layers
- CameraSpec            drone (§2.3) or keyframed camera
- MaskSpec / MaskGrowth portal / wipe mask definitions (§2.4)
- PostSpec              global post-processing (§2.7)
- load_scene_yaml(path) parse + validate a .yaml file
- dump_scene_yaml(scene) serialize back to a YAML string
- SceneVersionError     raised when version != 1
"""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any, Literal, Union

import yaml
from pydantic import BaseModel, ConfigDict, Field, field_validator, model_validator


# ---------------------------------------------------------------------------
# SceneMeta
# ---------------------------------------------------------------------------

class SceneMeta(BaseModel):
    """§2.2 meta block — top-level scene parameters."""

    model_config = ConfigDict(extra="forbid")

    duration_s: float
    fps: int
    resolution: tuple[int, int]
    perspective_px: float = 1200.0
    origin: tuple[float, float] = (960.0, 540.0)
    bg_color: str = "#000000"
    seed: int


# ---------------------------------------------------------------------------
# Layer
# ---------------------------------------------------------------------------

class LayerPost(BaseModel):
    """Per-layer post-processing parameters (§2.6)."""

    model_config = ConfigDict(extra="forbid")

    dof_blur_px: float = 0.0
    depth_fade: float = 0.0


class LayerSpec(BaseModel):
    """One flat-plate layer inside a stack (§2.2)."""

    model_config = ConfigDict(extra="forbid")

    id: str
    src: str
    scene_xyz: tuple[float, float, float]
    plate_size: tuple[int, int]
    # anchor can be "center", "top-left", or an (px, py) tuple
    anchor: Any = "center"
    post: LayerPost = Field(default_factory=LayerPost)
    # optional: which SVG path id to paint (portal silhouette layers use this)
    svg_paint_id: str | None = None

    @field_validator("anchor", mode="before")
    @classmethod
    def _parse_anchor(cls, v: Any) -> Any:
        """Coerce [px, py] list to (float, float) tuple; keep string literals."""
        if isinstance(v, (list, tuple)) and len(v) == 2:
            return (float(v[0]), float(v[1]))
        if v not in ("center", "top-left"):
            # still allow unknown string forms — schema may evolve
            pass
        return v


# ---------------------------------------------------------------------------
# Stack
# ---------------------------------------------------------------------------

class StackSpec(BaseModel):
    """A named collection of layer plates (a "biome") (§2.2)."""

    model_config = ConfigDict(extra="forbid")

    layers: list[LayerSpec]


# ---------------------------------------------------------------------------
# Camera — drone path
# ---------------------------------------------------------------------------

class BezierPath(BaseModel):
    """Cubic (or higher-order) Bezier control-point path (§2.3)."""

    model_config = ConfigDict(extra="forbid")

    kind: Literal["bezier"]
    controls: list[tuple[float, float, float]]
    duration_s: float

    @field_validator("controls")
    @classmethod
    def _at_least_two(cls, v: list) -> list:
        if len(v) < 2:
            raise ValueError("Bezier path requires at least 2 control points")
        return v


class DroneNoise(BaseModel):
    """Seeded simplex noise for the drone camera wobble (§2.3)."""

    model_config = ConfigDict(extra="forbid")

    z_amp: float
    xy_amp: float
    hz: float


class DroneCamera(BaseModel):
    """Drone-FPV camera parameters (§2.3)."""

    model_config = ConfigDict(extra="forbid")

    path: BezierPath
    poi_lookahead_s: float = 0.55
    spring_halflife_s: float = 0.18
    noise: DroneNoise
    bank_from_velocity: float = 0.40


# ---------------------------------------------------------------------------
# Camera — keyframed mode
# ---------------------------------------------------------------------------

EasingName = Literal["linear", "easeInOutCubic", "easeOutQuint", "easeInOutSine"]


class KeyframedEntry(BaseModel):
    """One keyframe in a keyframed camera track (§2.3)."""

    model_config = ConfigDict(extra="forbid")

    t: float
    x: float = 0.0
    y: float = 0.0
    z: float = 0.0
    yaw: float = 0.0
    pitch: float = 0.0
    roll: float = 0.0
    ease: EasingName = "linear"


# ---------------------------------------------------------------------------
# Camera — top-level
# ---------------------------------------------------------------------------

class CameraSpec(BaseModel):
    """Camera specification, either drone or keyframed mode (§2.3)."""

    model_config = ConfigDict(extra="forbid")

    mode: Literal["drone", "keyframed"]
    drone: DroneCamera | None = None
    keyframed: list[KeyframedEntry] | None = None

    @model_validator(mode="after")
    def _validate_mode_block(self) -> "CameraSpec":
        if self.mode == "drone" and self.drone is None:
            raise ValueError("camera.mode='drone' requires a drone: block")
        if self.mode == "keyframed":
            if self.keyframed is None or len(self.keyframed) < 2:
                raise ValueError(
                    "camera.mode='keyframed' requires at least 2 keyframes"
                )
        return self


# ---------------------------------------------------------------------------
# Mask growth
# ---------------------------------------------------------------------------

GrowthKind = Literal[
    "perspective", "radius", "gradient", "matte_seq", "displaced_edge"
]


class MaskGrowth(BaseModel):
    """Growth/animation parameters for a mask (§2.4)."""

    model_config = ConfigDict(extra="forbid")

    kind: GrowthKind

    # Timing — used by radius / gradient / matte_seq / displaced_edge
    t0: float | None = None
    t1: float | None = None

    # radius
    r0: float | None = None
    r1: float | None = None
    feather_px: float | None = None

    # gradient
    axis: Literal["x", "y"] | None = None

    # displaced_edge
    displacement_map: str | None = None
    amp: float | None = None

    @model_validator(mode="after")
    def _validate_kind_fields(self) -> "MaskGrowth":
        kind = self.kind
        if kind == "radius":
            _require(self, "radius", "t0", "t1", "r0", "r1")
        elif kind == "gradient":
            _require(self, "gradient", "t0", "t1", "axis")
        elif kind == "displaced_edge":
            _require(self, "displaced_edge", "t0", "t1", "displacement_map", "amp")
        elif kind == "matte_seq":
            _require(self, "matte_seq", "t0", "t1")
        # "perspective" needs nothing extra
        return self


def _require(model: BaseModel, kind_name: str, *fields: str) -> None:
    missing = [f for f in fields if getattr(model, f) is None]
    if missing:
        raise ValueError(
            f"growth.kind='{kind_name}' requires field(s): {missing}"
        )


# ---------------------------------------------------------------------------
# Mask
# ---------------------------------------------------------------------------

MaskAnchor = Literal["world", "screen", "layer-plane"]
MatteMode = Literal["alpha", "luminance"]


class MaskSpec(BaseModel):
    """A mask / portal definition (§2.2, §2.4)."""

    model_config = ConfigDict(extra="forbid")

    id: str

    # world-anchor fields — required only when anchor == "world"
    path_svg: str | None = None
    silhouette_id_in_svg: str | None = None
    path_id_in_svg: str | None = None
    attached_to_layer: str | None = None  # qualified "<stack>.<layer_id>"

    anchor: MaskAnchor
    src_stack: str
    dest_stack: str
    matte: MatteMode = "alpha"
    invert: bool = False
    growth: MaskGrowth

    @model_validator(mode="after")
    def _validate_world_fields(self) -> "MaskSpec":
        if self.anchor == "world":
            required = [
                "path_svg",
                "silhouette_id_in_svg",
                "path_id_in_svg",
                "attached_to_layer",
            ]
            missing = [f for f in required if getattr(self, f) is None]
            if missing:
                raise ValueError(
                    f"anchor='world' mask '{self.id}' requires: {missing}"
                )
        return self


# ---------------------------------------------------------------------------
# Post-processing
# ---------------------------------------------------------------------------

class VignetteSpec(BaseModel):
    model_config = ConfigDict(extra="forbid")
    strength: float
    radius: float = 0.85


class GrainSpec(BaseModel):
    model_config = ConfigDict(extra="forbid")
    sigma: float


class LightLeaksSpec(BaseModel):
    model_config = ConfigDict(extra="forbid")
    sprite: str
    opacity: float
    blend: str = "screen"


class FisheyeSpec(BaseModel):
    model_config = ConfigDict(extra="forbid")
    k1: float
    k2: float = 0.0


class ColorGradeSpec(BaseModel):
    model_config = ConfigDict(extra="forbid")
    lut: str


class GlobalPost(BaseModel):
    """Global post-processing applied after all stacks are composited (§2.7)."""

    model_config = ConfigDict(extra="forbid")

    vignette: VignetteSpec | None = None
    grain: GrainSpec | None = None
    light_leaks: LightLeaksSpec | None = None
    fisheye: FisheyeSpec | None = None
    color_grade: ColorGradeSpec | None = None


class PostSpec(BaseModel):
    """Container for global (and future per-stack) post settings (§2.7)."""

    model_config = ConfigDict(extra="forbid", populate_by_name=True)

    # YAML key is "global"; Python attribute is global_ to avoid keyword clash
    global_: GlobalPost | None = Field(default=None, alias="global")


# ---------------------------------------------------------------------------
# Top-level Scene
# ---------------------------------------------------------------------------

class Scene(BaseModel):
    """
    The complete parsed scene document (§2.2).

    ``version`` must be 1; all other versions are rejected.
    """

    model_config = ConfigDict(extra="forbid")

    version: int
    meta: SceneMeta
    stacks: dict[str, StackSpec]
    camera: CameraSpec
    masks: list[MaskSpec] = Field(default_factory=list)
    post: PostSpec | None = None

    @field_validator("version")
    @classmethod
    def _version_must_be_1(cls, v: int) -> int:
        if v != 1:
            raise ValueError(
                f"Unsupported scene version {v!r}; only version 1 is supported"
            )
        return v

    @field_validator("stacks")
    @classmethod
    def _stacks_not_empty(cls, v: dict) -> dict:
        if not v:
            raise ValueError("Scene must contain at least one stack")
        return v

    def find_layer(self, qualified_name: str) -> LayerSpec:
        """
        Look up a layer by its qualified ``<stack>.<layer_id>`` name (§2.4).

        Used by the mask system to locate the layer a mask is attached to.

        Raises:
            ValueError: malformed qualified name
            KeyError: stack or layer not found
        """
        if "." not in qualified_name:
            raise ValueError(
                f"Qualified layer name must be '<stack>.<layer_id>'; "
                f"got {qualified_name!r}"
            )
        stack_name, layer_id = qualified_name.split(".", 1)
        if stack_name not in self.stacks:
            raise KeyError(
                f"Stack {stack_name!r} not found (available: "
                f"{list(self.stacks)})"
            )
        for layer in self.stacks[stack_name].layers:
            if layer.id == layer_id:
                return layer
        raise KeyError(
            f"Layer {layer_id!r} not found in stack {stack_name!r}"
        )


# ---------------------------------------------------------------------------
# Loader / serializer
# ---------------------------------------------------------------------------

class SceneVersionError(ValueError):
    """Raised before Pydantic validation when the YAML version field != 1."""


def load_scene_yaml(path: Union[str, Path]) -> Scene:
    """
    Parse a ``scene.yaml`` file and return a validated :class:`Scene`.

    Raises:
        SceneVersionError:          version field != 1
        pydantic.ValidationError:   schema validation failure
        FileNotFoundError:          file does not exist
        ValueError:                 not a YAML mapping
    """
    path = Path(path)
    raw: Any = yaml.safe_load(path.read_text(encoding="utf-8"))
    return _validate_raw(raw)


def load_scene_dict(raw: dict) -> Scene:
    """
    Parse a pre-loaded dict (e.g. from ``yaml.safe_load``) into a :class:`Scene`.

    Useful in tests to avoid temporary files.

    Raises:
        SceneVersionError:          version field != 1
        pydantic.ValidationError:   schema validation failure
    """
    return _validate_raw(raw)


def _validate_raw(raw: Any) -> Scene:
    if not isinstance(raw, dict):
        raise ValueError("scene YAML must be a top-level mapping")
    version = raw.get("version")
    if version != 1:
        raise SceneVersionError(
            f"Unsupported scene version {version!r}; only version 1 is supported"
        )
    return Scene.model_validate(raw)


def dump_scene_yaml(scene: Scene) -> str:
    """
    Serialize a :class:`Scene` to a YAML string.

    Uses ``model_dump_json`` → ``json.loads`` to produce plain Python
    dicts/lists (no !!python/tuple tags) before passing to ``yaml.dump``.
    ``exclude_none=True`` omits all optional absent fields.
    """
    data = json.loads(
        scene.model_dump_json(by_alias=True, exclude_none=True)
    )
    return yaml.dump(
        data,
        default_flow_style=False,
        sort_keys=False,
        allow_unicode=True,
    )
