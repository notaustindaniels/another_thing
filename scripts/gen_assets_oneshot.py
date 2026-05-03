"""scripts/gen_assets_oneshot.py — One-off SVG generator for examples/.

Bypasses parallax_engine/tools/gen_image.py (which uses Haiku and silently
falls back to placeholder rectangles). Calls Sonnet directly with detailed
per-asset prompts. Hard-validates output (parses, viewBox correct, ≥8 shape
elements). NO placeholder fallback — fails loud.

Usage:
    .conda-env/bin/python scripts/gen_assets_oneshot.py forest [asset_name ...]
    .conda-env/bin/python scripts/gen_assets_oneshot.py portal [asset_name ...]

Env: ANTHROPIC_API_KEY required (CLAUDE_CODE_OAUTH_TOKEN won't work — Messages
API rejects OAuth).
"""

from __future__ import annotations

import os
import re
import sys
from pathlib import Path
from xml.etree import ElementTree as ET

import anthropic

MODEL = "claude-sonnet-4-6"
VIEWBOX = "0 0 3840 2160"
ROOT = Path(__file__).resolve().parent.parent

SYSTEM = """You are an expert vector illustrator producing SVG layers for a 2.5D parallax animation engine called parallax-engine.

OUTPUT CONTRACT — every rule is non-negotiable:
- Output ONLY raw SVG XML. No markdown code fences. No prose before or after.
- Start with `<svg` and end with `</svg>`.
- ViewBox MUST be exactly `0 0 3840 2160` (16:9 landscape master plate).
- Allowed elements: `<svg>`, `<defs>`, `<linearGradient>`, `<radialGradient>`,
  `<stop>`, `<path>`, `<polygon>`, `<rect>`, `<circle>`, `<ellipse>`, `<g>`.
  Disallowed: `<image>`, `<text>`, `<foreignObject>`, `<style>`, `<script>`,
  embedded raster, CSS classes.
- Use absolute path commands (M, L, C, Q, H, V, Z) — no relative variants.
- Minimum 8 distinct shape elements (path/polygon/etc) per layer.
- Solid fills or gradients only. Specify `fill="#hex"` directly on each shape.

COMPOSITING ROLE — this layer is ONE PLANE in a multi-layer composite.
Other layers exist BEHIND and IN FRONT.
- Sky / sky_dawn layers: fill the entire plate (only layer that does).
- All other layers: large transparent areas so layers behind show through.
  DO NOT fill the entire plate with a base color. Sparse silhouettes / detail
  on transparent background is the norm.

AESTHETIC: detailed, painterly vector illustrations — modern motion graphics
quality, illustrated children's-book / Studio Ghibli still / Polyfjord
style. NOT geometric primitives. NOT minimal flat icons. NOT stick figures.
Trees should look like trees, not triangles on rectangles.

SELF-CHECK before responding:
1. Output starts with `<svg` and ends with `</svg>`, with NOTHING else?
2. ViewBox is exactly `0 0 3840 2160`?
3. At least 8 shape elements?
4. Compositing role respected? (sparse for non-sky layers)
5. All colors specified as #hex?

Respond with the SVG and nothing else.
"""

ASSETS = {
    "forest": [
        {
            "name": "sky_dawn",
            "out": "examples/forest/assets/sky_dawn.svg",
            "prompt": """Layer role: SKY background at z=-12000 — most distant layer. FULLY covers the plate.

Subject: pre-dawn sky over a remote mountain valley.
- Vertical gradient (linearGradient with 4-5 stops): deep indigo (#1b1844)
  at the top, through warm coral (#f0a36a) and amber (#fbe6a6) at horizon
  (~y=1500), then dusty rose (#cc7a72) for the lowest sky band.
- 5-7 stratus cloud bands, soft and elongated, in tints of peach (#deb088),
  dusty rose (#c79a8e), violet (#8e7195). Use multiple overlapping fills
  in similar tones to suggest softness — DO NOT use opacity, all fills
  opaque hex.
- One small distant sun disc (#fffae0) low on horizon (~x=2300, y=1450),
  partially occluded by a cloud band crossing in front of it.
- Subtle "god-ray" hint: 3-4 very soft, slightly-lighter-tinted bands
  fanning upward from the sun area, at low contrast.
- Just below the horizon line (y=1500-1700), a darker dusky-purple band
  (#5a4a6a) suggesting distant valley shadow.
- NO mountains, NO trees, NO birds — those are in OTHER layers.

Style: smooth gradients, soft cloud edges via multiple overlapping shapes,
atmospheric and painterly.""",
        },
        {
            "name": "mountains",
            "out": "examples/forest/assets/mountains.svg",
            "prompt": """Layer role: distant mountain range silhouette at z=-9000.
Top 50% of plate (y=0 to y=900) MUST be transparent — sky shows through above the ridge.

Subject: a 3-ridge mountain silhouette receding into atmospheric perspective.
- Far ridge (smallest peaks, highest atmospheric haze): peaks at y=620-780
  across full width, very smooth profile. Color: pale blue-grey #7d7898.
- Middle ridge: peaks at y=750-900, smoother profile, mid color #5a5278.
- Near ridge (most defined): peaks at y=900-1100, jagged profile with
  multiple distinct peaks. Color: deep cool blue-violet #3a3258 to #4a4068.
- Two prominent named peaks:
  * One sharp peak slightly left of center (~x=1400) on the front ridge
    with a distinct snow cap on its upper third (#e0deec to #c5c0d8).
  * Another sharp pyramidal peak at right (~x=2700) on the front ridge,
    no snow.
- Each ridge is its own path — fill the silhouette area only and extend
  the bottom of each ridge silhouette downward with a vertical edge to
  y=2160 (so the ridges show as solid silhouettes from peak to bottom).
- Below the ridges: leave the foreground (y=1800-2160) transparent so
  trees_far and beyond show through.

Subtle internal detail: on the front ridge, add 2-3 darker accent paths
(#2a224a) suggesting valleys/slopes within the silhouette.""",
        },
        {
            "name": "trees_far",
            "out": "examples/forest/assets/trees_far.svg",
            "prompt": """Layer role: far-distance forest treeline at z=-6500.
Top 50% transparent. Bottom 50% has tree silhouettes meeting the ground.

Subject: a continuous far treeline of conifers and deciduous trees.
- 28-36 individual tree silhouettes spanning x=0 to x=3840, with their
  tops varying from y=1050 to y=1300 (irregular — some taller, some
  shorter, no rhythmic spacing).
- Mostly conifers (triangular silhouettes with broken jagged top edges
  via 5-7 small bumps along each top, NOT clean triangles) interspersed
  with rounder deciduous crowns.
- Color palette: dark forest green #1f3a26 for the base mass.
- Selected upper portions of trees catch dawn light: a second set of paths
  in warmer rim-light tone #54663e covering just the upper third of maybe
  10-15 of the trees (not all).
- Below the treeline: a band of mist/atmospheric haze (y=1300-1500) using
  a desaturated lavender-fog tone #a59caa as 2-4 elongated soft shapes,
  NOT covering full width — broken/patchy.
- Below mist: ground silhouette in slightly warmer dark color #2a3225
  covering down to y=2160.

Style: organic forest profile via the broken top edges; atmospheric
recession suggested by the mist band.""",
        },
        {
            "name": "trees_mid",
            "out": "examples/forest/assets/trees_mid.svg",
            "prompt": """Layer role: mid-distance forest at z=-4500.
Top 35% transparent (y=0 to y=750). Mid-to-bottom has more distinct trees.

Subject: forest understory and trees seen mid-distance — individual
trunks and crowns become legible.
- 14-18 distinct trees scattered across x=0 to x=3840:
  * Trunks: narrow vertical or slightly bent shapes in #2c1f12 (dark brown).
    Each trunk has a visible taper and 2-3 darker streaks for bark texture.
  * Crowns: irregular organic shapes in #18331a (dark green) with
    secondary internal paths in slightly varying greens (#1f3a22, #143018)
    for shading variation.
- A few foreground branches arching from the bottom-left and bottom-right
  edges (anchor at x=0-300, y=1800-2160 and x=3540-3840, y=1800-2160),
  curving inward briefly. Don't dominate the bottom — these are hints,
  not full coverage.
- Forest floor / ground in dark loamy brown #1a1208 at the very bottom
  (y=1900-2160), with patches of moss (#274a25) and fallen leaves
  (#3d2810, #523218) as small irregular polygons.
- Selective rim-light: 3-5 tree crowns have a small warm-tone (#6f7e3a)
  highlight on their topmost edge — dawn rays catching certain crowns.
  Most crowns do NOT have this highlight.

Style: more individual character per tree than trees_far. Storybook feel.""",
        },
        {
            "name": "trees_near",
            "out": "examples/forest/assets/trees_near.svg",
            "prompt": """Layer role: near forest trees at z=-2500.
Top 30% transparent. Lower 70% dominated by large trunks.

Subject: 5-7 large trees seen close-up — we are flying through a forest,
these are the trees passing by on the LEFT and RIGHT of the camera path.
The CENTER must be a transparent V-shape gap so the camera can see through.

- Trees positioned at the left and right edges:
  * Left side: 2-3 trunks in x=80-700 range, spanning y=200 to y=2160.
  * Right side: 2-3 trunks in x=3140-3760 range, spanning y=200 to y=2160.
  * Optional 1 partial tree silhouette near top-center (x=1700-2140,
    y=0-500) with branches reaching down — a tree we're going under.
- Trunk colors: rich dark woods (#3a2614, #4a3018, #2c1c0c) varying per tree
  for distinction. Each trunk has 4-6 darker vertical streaks suggesting
  bark texture.
- Lower branches reach inward from trunks toward center: large primary
  branches with smaller forking branches. Branch tips do NOT cross x=1300
  on the left or x=2540 on the right (preserve the central gap).
- Leaf clusters at branch tips: dense organic shapes in deep green #1a2e18
  with patches of slightly lighter green #2c4626 where dawn light
  filters through.
- Sparse hanging moss / vines: 2-3 thin curtain shapes in desaturated
  sage #6a8060, only on a couple of trunks.
- Ground level NOT clearly indicated — these trees are too close to see
  their bases.

Style: bold dark silhouettes with internal detail. The dark masses on the
sides frame the brighter distant view through the gap.""",
        },
        {
            "name": "leaves_fg",
            "out": "examples/forest/assets/leaves_fg.svg",
            "prompt": """Layer role: foreground leaves and branches at z=-500.
VERY close to camera. ~70% of plate FULLY TRANSPARENT (the central area where
the camera looks through). Detail concentrated at the EDGES.

Subject: a few branches with leaf clusters whipping past the camera at speed.

- 3-4 branch clusters at the corners/edges:
  * Top-left: large branch from anchor (x=0-300, y=0-200) descending and
    arcing toward center, reaching to ~x=1400, y=900. Multiple sub-branches
    with leaf clusters.
  * Top-right: mirroring the top-left, anchor x=3540-3840, y=0-200, reaching
    to ~x=2440, y=900.
  * Optional: one small branch curling up from bottom-right (x=3500-3840,
    y=1800-2160) briefly into frame.
- Leaves: 60-100 individual leaf shapes per branch cluster. Each leaf is a
  small 4-7 vertex polygon or short curved path (size ~30-80px each in
  viewBox units).
- Branches: 3-5 main branches per cluster with natural taper (wider at
  anchor, narrower at tip). Color: dark brown #1a1008. 1-2 small bumps/knots
  per branch.
- Leaf colors:
  * Base mass: very dark forest green #0f1f0c, #16261a (mix two tones).
  * 8-12 leaves total in slightly lighter accent #2e4520 for natural variation.
  * 4-6 leaves total in WARM DAWN GOLD #c9a45a, sparingly placed on the
    TOPMOST EDGES of the topmost leaves only — these catch the dawn light
    and pop against the dark mass. Be sparse — too many ruin the effect.
- Critical: the central area roughly x=900 to x=2940 and y=200 to y=1900
  must be ALMOST ENTIRELY TRANSPARENT. At most a couple of thin tendril
  branches may cross it — never full leaf coverage.

Style: detailed individual leaves, organic branch curves. Edge silhouette
matters most — the viewer sees this layer rushing past at high speed
(parallax differential).""",
        },
    ],
    "portal": [
        {
            "name": "sky",
            "out": "examples/portal/assets/sky.svg",
            "prompt": """Layer role: SKY background (forest side) at z=-10500. FULLY covers the plate.

Subject: late golden-hour forest sky — warm amber, hint of approaching dusk.
- Vertical gradient (linearGradient with 4-5 stops): deep navy-blue (#1a2244)
  at the very top transitioning through dusty violet (#5a4070), through warm
  amber-gold (#dca050) near the horizon (~y=1500), settling into a smoky
  rose-orange band (#a06848) at the lowest sky.
- 6-9 elongated stratus cloud bands at varying heights, in tints of warm
  cream (#e8c890), peach (#d8a570), dusky violet (#7a5868). Use multiple
  overlapping shapes to suggest soft edges — all fills opaque hex.
- One small bright gleam where the sun is hidden behind a cloud bank, at
  about (x=2400, y=1380), in a near-white warm tone (#fff0c0). Soft halo
  via 2-3 surrounding lighter shapes.
- A subtle bloom of light (#f0c890) on the bottoms of the clouds nearest
  the sun, suggesting back-illumination.
- NO mountains, NO trees, NO foreground — this is sky only.

Style: smooth gradients, painterly atmosphere, golden-hour warmth.""",
        },
        {
            "name": "leaves_mid",
            "out": "examples/portal/assets/leaves_mid.svg",
            "prompt": """Layer role: mid-foreground foliage at z=-2500. Sits BETWEEN portal_tree
(z=-4500, behind) and leaves_fg (z=-500, in front). The portal_tree is the
mask layer; leaves_mid renders ON TOP of the masked portal hole (L2 in-front
rule). So this layer's silhouettes occlude the city revealed through the hole.

Top 25% transparent. Detail at edges and lower portion.

Subject: medium-sized branches with leaf clusters framing the camera view
without blocking the central area where the portal will be visible.

- 4-6 branch clusters distributed at the edges:
  * Left edge (x=0-700): 2 branch arcs descending from y=300-1500.
  * Right edge (x=3140-3840): 2 branch arcs mirroring the left.
  * Bottom-left and bottom-right (y=1700-2160): smaller branch hints
    extending upward briefly into frame.
- Critical: central area x=900 to x=2940 and y=400 to y=1500 should be
  MOSTLY transparent — the portal in the layer behind shows through here.
  At most thin tendril branches may cross this central zone.
- Each branch cluster has 30-50 leaf shapes (irregular polygons, ~40-100px
  in viewBox units).
- Branches: medium-dark brown (#1c1108), tapering, 1-2 bumps each.
- Leaf colors:
  * Mass: #18261a and #1f3322 (mixed dark forest greens).
  * Scattered accent: 6-12 leaves in #2d4528 for variation.
  * 3-5 leaves at the topmost edges in warm amber (#bf9050) — only on
    very tips catching golden-hour light.
- 2-3 hanging vines in desaturated sage #5a7050, thin trailing curtains
  from upper-left or upper-right.

Style: detailed foliage, organic curves. Edge silhouette matters — viewer
sees this layer parallax against the portal_tree behind.""",
        },
        {
            "name": "city_sky",
            "out": "examples/portal/assets/city_sky.svg",
            "prompt": """Layer role: SKY background (city side, revealed through portal) at z=-10500.
FULLY covers the plate. Should feel different from the forest sky (warmer,
more saturated, slightly mythic).

Subject: vibrant golden-hour sky over a fantastical city.
- Vertical gradient (linearGradient with 4-5 stops): deep magenta-violet
  (#3a1850) at top transitioning through hot orange (#f07028), through
  bright gold (#ffc870) near horizon (y=1450-1550), settling to warm cream
  (#fff0d0) at lowest sky.
- 5-8 lenticular / elongated cloud bands in tints of fiery orange (#ee9050),
  warm pink (#e09080), saturated magenta-violet (#8050a0).
- A larger luminous sun disc (#fff8d0) at horizon (~x=1600, y=1400), with
  a halo of 4-6 progressively softer lighter shapes around it (#fff0c0,
  #f8d890).
- Light rays / volumetric beams: 5-7 thin lighter-tinted bands radiating
  upward from the sun area, very subtle.
- A faint hint of a secondary celestial body (moon or planet) high up
  (~x=2900, y=400), small, in dusty rose (#c08080).

Style: more saturated and dramatic than the forest sky. Mythic / fantasy
illustrated.""",
        },
        {
            "name": "city_far",
            "out": "examples/portal/assets/city_far.svg",
            "prompt": """Layer role: distant city silhouettes at z=-7000.
Top 50% transparent. Bottom 50% has city skyline.

Subject: a fantastical illustrated city skyline at distance — minarets,
spires, domes, towers. Persian/Moghul/Ghibli fantasy architecture, NOT
modern flat geometric high-rises.

- Continuous skyline filling x=0 to x=3840, rooftops varying y=750-1200
  (irregular).
- 15-25 distinct buildings of varying heights:
  * 4-6 tall slender minarets/spires reaching y=600-800, bulbous domes or
    pointed tops.
  * 6-8 medium domed structures (round or onion-shaped) at y=900-1100.
  * 5-10 rectangular blocky lower buildings at y=1100-1200.
- Color palette: WARM (golden-hour lit):
  * Primary silhouette: rich warm sienna #8c4a30.
  * Some buildings darker terracotta #6c3a24.
  * Selective "lit" facades on 4-6 buildings: warm amber-gold #c89050 on
    one face suggesting sunset side-lighting.
- Window suggestions: tiny dark squares #3a1810 sparingly placed on a few
  larger buildings.
- Below silhouette (y=1200-2160): mostly transparent with optional soft
  warm haze #a06440 at low coverage suggesting atmospheric depth.

Style: ornate fantasy architecture. Warm palette contrasts with cool
forest. Atmospheric distance.""",
        },
        {
            "name": "city_near",
            "out": "examples/portal/assets/city_near.svg",
            "prompt": """Layer role: closer detailed city buildings at z=-3500.
Top 35% transparent. Lower 65% has nearby city detail.

Subject: 4-7 large foreground city buildings — same fantasy/Persian style
as city_far but with much more detail and warmer-yet-darker palette
(closer = more detail, less haze).

- 4-7 distinct buildings, each occupying significant screen real estate:
  * 1-2 tall ornate minaret/spire structures (one off-left x=200-700, one
    off-right x=3140-3640) reaching y=200-300, with detailed dome/pointed
    tops and decorative balconies.
  * 2-3 broader palatial domed buildings (x=1100-1500 and x=2340-2740)
    at y=600-1100, arched windows, decorative bands.
  * 1-2 lower terraced buildings filling foreground (y=1300-2160) with
    arched doorways and detailed facades.
- Color palette: deeper warm tones, more saturated:
  * Base silhouettes: deep warm sienna #7a3a1c, terracotta #603018,
    darkened crimson #5a2818.
  * Secondary detail (windows, bands, ornaments): warmer accent #9c5a30,
    #b87040.
  * Selective "lit" facets in warm amber-gold #d4a050 on 6-12 small
    surfaces catching golden-hour light from city_sky.
- Architectural ornaments: arched windows (paired or in groups of 3-5),
  decorative horizontal bands, finials on dome tops, geometric patterns
  on visible facades (small repeated shapes).
- A few small lit windows in deep gold #f0c860 sparingly placed.
- Suggested ground level near y=2160: dusty warm earth #5a3018.

Style: detailed illustrated fantasy architecture. Studio Ghibli inspired.
Warm and inviting (contrasts with cool forest).""",
        },
    ],
}


def _strip_fences(text: str) -> str:
    text = text.strip()
    # Strip any <thinking>...</thinking> or <reasoning>...</reasoning> blocks
    # that some models prepend even when the prompt forbids prose. Greedy is
    # fine — we want the LAST occurrence of </thinking> to be the cutoff.
    text = re.sub(r"<thinking>.*?</thinking>", "", text, flags=re.DOTALL)
    text = re.sub(r"<reasoning>.*?</reasoning>", "", text, flags=re.DOTALL)
    text = text.strip()
    # Strip any prose preamble before the first <svg tag.
    svg_start = text.find("<svg")
    if svg_start > 0:
        text = text[svg_start:]
    # Strip markdown fences if present.
    if text.startswith("```"):
        text = re.sub(r"^```(?:xml|svg|html)?\s*\n?", "", text)
        text = re.sub(r"\n?\s*```\s*$", "", text)
    # Trim trailing prose after </svg>.
    svg_end = text.rfind("</svg>")
    if svg_end > 0:
        text = text[: svg_end + len("</svg>")]
    return text.strip()


def validate_svg(content: str, name: str) -> str:
    if not content.startswith("<svg") or not content.endswith("</svg>"):
        raise ValueError(f"{name}: output does not start with <svg or end with </svg>; got "
                         f"{content[:80]!r}…{content[-80:]!r}")
    try:
        root = ET.fromstring(content)
    except ET.ParseError as e:
        raise ValueError(f"{name}: SVG XML did not parse: {e}")
    vb = root.attrib.get("viewBox", "").strip()
    if vb != VIEWBOX:
        raise ValueError(f"{name}: viewBox is {vb!r}, expected {VIEWBOX!r}")
    n_shapes = sum(
        1 for el in root.iter()
        if el.tag.split("}")[-1] in ("path", "polygon", "rect", "circle", "ellipse")
    )
    if n_shapes < 8:
        raise ValueError(f"{name}: only {n_shapes} shape elements (need ≥8)")
    return content


def gen_one(client: anthropic.Anthropic, asset: dict, max_retries: int = 2):
    name = asset["name"]
    out = ROOT / asset["out"]
    out.parent.mkdir(parents=True, exist_ok=True)
    last_err = None
    for attempt in range(max_retries + 1):
        print(f"[{name}] attempt {attempt + 1}/{max_retries + 1}…", flush=True)
        msg = client.messages.create(
            model=MODEL,
            max_tokens=16384,
            system=[{
                "type": "text",
                "text": SYSTEM,
                "cache_control": {"type": "ephemeral"},
            }],
            messages=[{"role": "user", "content": asset["prompt"]}],
        )
        text = "".join(b.text for b in msg.content if hasattr(b, "text"))
        text = _strip_fences(text)
        try:
            svg = validate_svg(text, name)
        except ValueError as e:
            last_err = e
            print(f"[{name}] validation failed: {e}", flush=True)
            continue
        out.write_text(svg)
        u = msg.usage
        cached = getattr(u, "cache_read_input_tokens", 0) or 0
        print(f"[{name}] OK ({len(svg)} bytes, in={u.input_tokens} cached_read={cached} "
              f"out={u.output_tokens}) → {out}", flush=True)
        return u
    raise RuntimeError(f"[{name}] all {max_retries + 1} attempts failed; last: {last_err}")


def main():
    if len(sys.argv) < 2 or sys.argv[1] not in ASSETS:
        print(f"usage: {sys.argv[0]} {{forest|portal}} [asset_name ...]", file=sys.stderr)
        sys.exit(2)
    target = sys.argv[1]
    requested = set(sys.argv[2:]) if len(sys.argv) > 2 else None

    api_key = os.environ.get("ANTHROPIC_API_KEY")
    if not api_key:
        print("ERROR: ANTHROPIC_API_KEY not set", file=sys.stderr)
        sys.exit(1)
    client = anthropic.Anthropic(api_key=api_key)

    total_in = total_cached = total_out = 0
    n = 0
    for asset in ASSETS[target]:
        if requested and asset["name"] not in requested:
            continue
        u = gen_one(client, asset)
        total_in += u.input_tokens
        total_cached += getattr(u, "cache_read_input_tokens", 0) or 0
        total_out += u.output_tokens
        n += 1
    print(f"\n=== TOTAL ({n} assets): in={total_in} cached_read={total_cached} out={total_out} ===")


if __name__ == "__main__":
    main()
