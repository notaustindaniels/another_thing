# parallax-engine — License Audit

**Product:** parallax-engine v0.1.0  
**Audit date:** 2026-05-01  
**Auditor:** automated session (parallax-engine build harness)  
**Commercial resale status:** CLEAR — all runtime dependencies are permissive (MIT, BSD, Apache 2.0,
HPND, LGPL-dynamic-link). No GPL-tainted dependency. FFmpeg build is LGPL-only with
libopenh264; H.264 patent royalties on the Cisco precompiled binary are covered by Cisco's
MPEG-LA agreement.

---

## 1. parallax-engine itself

| Field | Value |
|---|---|
| Distribution | `parallax-engine` |
| Version | 0.1.0 |
| License | Proprietary — all rights reserved |
| Commercial resale | PERMITTED (this is the product) |

---

## 2. Runtime dependencies (shipped to end-users)

These packages are required at runtime. Customers who install `parallax-engine` will
transitively receive them.

### 2.1 Core rendering stack

| Package | Version | License | Copyright / Maintainer | Resale status |
|---|---|---|---|---|
| `Pillow` | 12.2.0 | HPND (MIT-equivalent) | Jeffrey A. Clark and contributors | ✅ Permissive |
| `numpy` | 2.4.4 | BSD-3-Clause | NumPy contributors | ✅ Permissive |
| `scipy` | 1.17.1 | BSD-3-Clause | SciPy contributors / Enthought | ✅ Permissive |
| `opencv-python-headless` | 4.13.0.92 | Apache-2.0 | OpenCV team / Intel | ✅ Permissive |
| `skia-python` | 144.0.post2 | BSD-3-Clause | Skia / Google; Python bindings by kyamagu | ✅ Permissive |
| `noise` | 1.2.2 | MIT | Casey Duncan | ✅ Permissive |

### 2.2 Schema and configuration

| Package | Version | License | Copyright / Maintainer | Resale status |
|---|---|---|---|---|
| `pydantic` | 2.13.3 | MIT | Samuel Colvin and contributors | ✅ Permissive |
| `pydantic-core` | 2.46.3 | MIT | Samuel Colvin and contributors | ✅ Permissive |
| `pydantic-settings` | 2.14.0 | MIT | Samuel Colvin and contributors | ✅ Permissive |
| `pydantic-yaml` | 1.6.0 | MIT | Anatoly Makarevich | ✅ Permissive |
| `PyYAML` | 6.0.3 | MIT | Kirill Simonov | ✅ Permissive |
| `ruamel.yaml` | 0.18.17 | MIT | Anthon van der Neut | ✅ Permissive |
| `ruamel.yaml.clib` | 0.2.15 | MIT | Anthon van der Neut | ✅ Permissive |

### 2.3 Agent harness (Anthropic SDK)

| Package | Version | License | Copyright / Maintainer | Resale status |
|---|---|---|---|---|
| `anthropic` | 0.97.0 | MIT | Anthropic, PBC | ✅ Permissive |
| `claude-code-sdk` | 0.0.25 | MIT | Anthropic, PBC | ✅ Permissive |
| `mcp` | 1.27.0 | MIT | Anthropic, PBC (Model Context Protocol) | ✅ Permissive |

### 2.4 HTTP / networking (transitive via Anthropic SDK)

| Package | Version | License | Copyright / Maintainer | Resale status |
|---|---|---|---|---|
| `httpx` | 0.28.1 | BSD-3-Clause | Tom Christie | ✅ Permissive |
| `httpcore` | 1.0.9 | BSD-3-Clause | Tom Christie | ✅ Permissive |
| `httpx-sse` | 0.4.3 | MIT | Florimond Manca | ✅ Permissive |
| `h11` | 0.16.0 | MIT | Nathaniel J. Smith | ✅ Permissive |
| `anyio` | 4.13.0 | MIT | Alex Grönholm | ✅ Permissive |
| `sniffio` | 1.3.1 | MIT or Apache-2.0 | Nathaniel J. Smith | ✅ Permissive |
| `certifi` | 2026.4.22 | MPL-2.0 | Kenneth Reitz / certifi contributors | ✅ Permissive (MPL-2.0 data file) |
| `idna` | 3.13 | BSD-like (custom) | Kim Davies | ✅ Permissive |

### 2.5 MCP / ASGI server stack (transitive via mcp)

| Package | Version | License | Copyright / Maintainer | Resale status |
|---|---|---|---|---|
| `starlette` | 1.0.0 | BSD-3-Clause | Tom Christie | ✅ Permissive |
| `uvicorn` | 0.46.0 | BSD-3-Clause | Tom Christie / encode | ✅ Permissive |
| `sse-starlette` | 3.4.1 | BSD-2-Clause | Sion Jang | ✅ Permissive |
| `python-multipart` | 0.0.27 | Apache-2.0 | Andrew Dunstan, Jerome Leclanche | ✅ Permissive |

### 2.6 Data validation / typing (transitive)

| Package | Version | License | Copyright / Maintainer | Resale status |
|---|---|---|---|---|
| `annotated-types` | 0.7.0 | MIT | Adrian Garcia Badaracco | ✅ Permissive |
| `typing_extensions` | 4.15.0 | PSF-2.0 | Python Software Foundation | ✅ Permissive |
| `typing-inspection` | 0.4.2 | MIT | Pydantic contributors | ✅ Permissive |
| `attrs` | 26.1.0 | MIT | Hynek Schlawack | ✅ Permissive |
| `jsonschema` | 4.26.0 | MIT | Julian Berman | ✅ Permissive |
| `jsonschema-specifications` | 2025.9.1 | MIT | Julian Berman | ✅ Permissive |
| `referencing` | 0.37.0 | MIT | Julian Berman | ✅ Permissive |
| `rpds-py` | 0.30.0 | MIT | Julian Berman (Rust bindings) | ✅ Permissive |
| `docstring_parser` | 0.18.0 | MIT | Robbert van der Helm | ✅ Permissive |

### 2.7 Cryptography / auth (transitive via mcp / Anthropic)

| Package | Version | License | Copyright / Maintainer | Resale status |
|---|---|---|---|---|
| `cryptography` | 47.0.0 | Apache-2.0 and BSD | Python Cryptographic Authority | ✅ Permissive |
| `cffi` | 2.0.0 | MIT | Armin Rigo / Maciej Fijalkowski | ✅ Permissive |
| `pycparser` | 3.0 | BSD-3-Clause | Eli Bendersky | ✅ Permissive |
| `PyJWT` | 2.12.1 | MIT | Jose Padilla | ✅ Permissive |

### 2.8 Utilities (transitive)

| Package | Version | License | Copyright / Maintainer | Resale status |
|---|---|---|---|---|
| `click` | 8.3.3 | BSD-3-Clause | Pallets / Armin Ronacher | ✅ Permissive |
| `distro` | 1.9.0 | Apache-2.0 | Nir Cohen | ✅ Permissive |
| `jiter` | 0.14.0 | MIT | Samuel Colvin | ✅ Permissive |
| `packaging` | 26.2 | Apache-2.0 and BSD | Python Packaging Authority | ✅ Permissive |
| `python-dotenv` | 1.2.2 | BSD-3-Clause | Saurabh Kumar | ✅ Permissive |
| `pybind11` | 3.0.4 | BSD-3-Clause | Wenzel Jakob | ✅ Permissive |
| `Pygments` | 2.20.0 | BSD-2-Clause | Georg Brandl and contributors | ✅ Permissive |

---

## 3. Development-only dependencies (NOT shipped to end-users)

These packages are used only during development and testing. They are listed for completeness
but do not affect end-user licensing.

| Package | Version | License | Notes |
|---|---|---|---|
| `pytest` | 9.0.3 | MIT | Test runner |
| `pytest-cov` | 7.1.0 | MIT | Coverage plugin |
| `coverage` | 7.13.5 | Apache-2.0 | Coverage measurement |
| `pluggy` | 1.6.0 | MIT | pytest plugin system |
| `iniconfig` | 2.3.0 | MIT | pytest config parsing |
| `pydantic-settings` | 2.14.0 | MIT | Config management |

---

## 4. Build tools (NOT shipped to end-users)

| Package | Version | License | Notes |
|---|---|---|---|
| `setuptools` | 82.0.1 | MIT | Build backend |
| `wheel` | 0.47.0 | MIT | Wheel builder |
| `pip` | 26.0.1 | MIT | Package installer |

---

## 5. System-level dependencies

### 5.1 FFmpeg

| Field | Value |
|---|---|
| Binary | `ffmpeg` (system/conda installation) |
| Build configuration | LGPL-only — compiled without `--enable-gpl`, without libx264, without libx265 |
| License | GNU Lesser General Public License v2.1 or later (LGPLv2.1+) |
| FFmpeg copyright | Copyright © 2000–2024 FFmpeg developers |
| Linking | Dynamic linking only; parallax-engine invokes FFmpeg as an external subprocess |
| LGPL compliance | ✅ Dynamic link + user-replaceable binary + LGPL text reproduced in EULA |
| Commercial resale | ✅ PERMITTED under LGPL dynamic linking; attribution required (see EULA.md) |

FFmpeg is invoked via `subprocess.run()` — never imported or statically linked.
Users may replace the FFmpeg binary with their own LGPL build.

### 5.2 OpenH264 (H.264 video codec)

| Field | Value |
|---|---|
| Library | `libopenh264` (Cisco precompiled binary) |
| License | BSD-2-Clause |
| Copyright | Copyright © 2013 Cisco Systems, Inc. |
| Patent coverage | Cisco holds MPEG-LA H.264 patent license; Cisco's binary is royalty-free for all users |
| Commercial resale | ✅ PERMITTED; attribution required (see EULA.md) |

The precompiled Cisco binary is the only H.264 encoder used. This ensures:
- No GPL contamination from libx264/libx265
- No separate H.264 patent royalty obligation for parallax-engine customers

---

## 6. License compatibility matrix (resale-critical check)

| License type | Packages | Resale compatible? |
|---|---|---|
| MIT | Most deps | ✅ Yes, with attribution |
| BSD-2-Clause | Pygments, sse-starlette, OpenH264 | ✅ Yes, with attribution |
| BSD-3-Clause | numpy, scipy, httpx, httpcore, starlette, uvicorn, skia-python, click, python-dotenv, pycparser, pybind11 | ✅ Yes, with attribution |
| Apache-2.0 | opencv-python-headless, cryptography, coverage, distro, python-multipart, packaging | ✅ Yes, with attribution |
| HPND (Pillow) | Pillow | ✅ Yes, MIT-equivalent |
| PSF-2.0 | typing_extensions | ✅ Yes |
| MPL-2.0 | certifi (CA bundle data) | ✅ Yes (file-level copyleft, does not affect linked code) |
| LGPL-2.1+ | FFmpeg (dynamically linked) | ✅ Yes, with attribution + user-replaceable binary |
| BSD-2-Clause | libopenh264 / OpenH264 | ✅ Yes, with required attribution |
| **GPL (any version)** | **(none)** | **🚫 FORBIDDEN — none present** |
| **AGPL (any version)** | **(none)** | **🚫 FORBIDDEN — none present** |

**Result: CLEAN. Zero GPL or AGPL dependencies, direct or transitive.**

---

## 7. Audit methodology

1. `pip list --format=json` — enumerate all installed distributions in the runtime environment
2. `importlib.metadata.distributions()` — verify license classifiers and metadata
3. `python tools/validate_licensing.py` — automated gate checking:
   - Declared dependencies in `pyproject.toml`
   - Installed distributions (transitive catch)
   - AST scan of `parallax_engine/` and `tests/` for forbidden imports
   - Text scan of build artifacts for forbidden strings
   - FFmpeg binary encoder list (`ffmpeg -encoders`) checked for libx264/libx265 absence
4. Manual review of this document against the SPEC.md §5 requirements

---

## 8. Required attributions

The following attribution notices must appear in product documentation and in the EULA
(see `EULA.md`):

### FFmpeg (LGPL)

> This software uses code of [FFmpeg](http://ffmpeg.org) licensed under the
> [LGPLv2.1](http://www.gnu.org/licenses/old-licenses/lgpl-2.1.html) and its
> source can be downloaded [here](https://ffmpeg.org/download.html).

### OpenH264 (Cisco)

> OpenH264 Video Codec provided by Cisco Systems, Inc.

### Pillow (HPND)

> This software uses Pillow (PIL Fork), Copyright © 2010–2024 by Jeffrey A. Clark and
> contributors. Used under the Historical Permission Notice and Disclaimer (HPND) license.

### Skia / skia-python (BSD-3-Clause)

> This software uses Skia (Google) and the skia-python bindings (BSD-3-Clause).
> Skia is Copyright © Google LLC.

---

*This document was generated by the parallax-engine build harness on 2026-05-01.
Verify by running `python tools/validate_licensing.py` from the project root.*
