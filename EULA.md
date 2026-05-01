# parallax-engine — End-User License Agreement (EULA) Template

**Version:** 1.0  
**Effective date:** [INSERT EFFECTIVE DATE]  
**Licensor:** [INSERT COMPANY NAME] ("Company")  
**Product:** parallax-engine

> **Note to licensors:** This is a template. Replace all bracketed placeholders
> with your company details before distributing to customers. Have legal counsel
> review before executing commercially.

---

## 1. Grant of License

Subject to the terms of this Agreement, Company grants Licensee a non-exclusive,
non-transferable, limited license to install and use parallax-engine solely for
Licensee's internal or commercial production purposes as specified in the applicable
order form or subscription agreement.

---

## 2. Restrictions

Licensee shall not:

(a) sublicense, sell, resell, transfer, assign, or otherwise commercialize or
make available to any third party the Software or any modified version thereof,
except as expressly permitted in writing by Company;

(b) modify, translate, adapt, or create derivative works based upon the Software,
except to the extent that applicable law expressly prohibits such restriction;

(c) reverse engineer, decompile, disassemble, or otherwise attempt to derive the
source code of the Software, except to the extent expressly permitted by applicable
law;

(d) use the Software to develop a competing product or service;

(e) remove or alter any proprietary notices or labels on the Software.

---

## 3. Open-Source Components and Required Attributions

parallax-engine incorporates or depends upon several open-source software components.
This section reproduces the attribution notices required by those components' licenses.
Company's use of these components does not alter the terms under which those components
are licensed to end users.

### 3.1 FFmpeg — LGPL Attribution

> This software uses code of [FFmpeg](http://ffmpeg.org) licensed under the
> [GNU Lesser General Public License v2.1 or later (LGPLv2.1+)](http://www.gnu.org/licenses/old-licenses/lgpl-2.1.html).
> The FFmpeg source code is available at [https://ffmpeg.org/download.html](https://ffmpeg.org/download.html).
>
> FFmpeg is dynamically linked and is not statically incorporated into
> parallax-engine. Licensee may replace the FFmpeg binary with their own
> LGPL-compatible build. The FFmpeg binary is the copyrighted work of the
> FFmpeg developers (Copyright © 2000–2024 FFmpeg developers and contributors).

**LGPL compliance notice:** parallax-engine invokes FFmpeg exclusively as an external
subprocess (no static or dynamic linking within the Python package). Users retain the
right to substitute their own LGPL-compliant FFmpeg binary. The FFmpeg LGPL license
text is available at: https://www.gnu.org/licenses/old-licenses/lgpl-2.1.txt

### 3.2 OpenH264 — Cisco Attribution

> **OpenH264 Video Codec provided by Cisco Systems, Inc.**
>
> This software uses the OpenH264 H.264 video encoder, a precompiled binary
> provided by Cisco Systems, Inc. under the BSD 2-Clause License. Cisco Systems, Inc.
> holds a sublicense from MPEG LA for the H.264 standard patents that covers
> end-users of the Cisco-provided binary at no additional royalty cost.
>
> Copyright © 2013, Cisco Systems, Inc. All rights reserved.
>
> Redistribution and use in source and binary forms, with or without modification,
> are permitted provided that the following conditions are met:
>
> 1. Redistributions of source code must retain the above copyright notice, this
>    list of conditions and the following disclaimer.
> 2. Redistributions in binary form must reproduce the above copyright notice,
>    this list of conditions and the following disclaimer in the documentation
>    and/or other materials provided with the distribution.
>
> THIS SOFTWARE IS PROVIDED BY CISCO SYSTEMS, INC. "AS IS" AND ANY EXPRESS OR
> IMPLIED WARRANTIES, INCLUDING, BUT NOT LIMITED TO, THE IMPLIED WARRANTIES OF
> MERCHANTABILITY AND FITNESS FOR A PARTICULAR PURPOSE ARE DISCLAIMED. IN NO EVENT
> SHALL CISCO SYSTEMS, INC. BE LIABLE FOR ANY DIRECT, INDIRECT, INCIDENTAL,
> SPECIAL, EXEMPLARY, OR CONSEQUENTIAL DAMAGES (INCLUDING, BUT NOT LIMITED TO,
> PROCUREMENT OF SUBSTITUTE GOODS OR SERVICES; LOSS OF USE, DATA, OR PROFITS;
> OR BUSINESS INTERRUPTION) HOWEVER CAUSED AND ON ANY THEORY OF LIABILITY,
> WHETHER IN CONTRACT, STRICT LIABILITY, OR TORT (INCLUDING NEGLIGENCE OR
> OTHERWISE) ARISING IN ANY WAY OUT OF THE USE OF THIS SOFTWARE, EVEN IF ADVISED
> OF THE POSSIBILITY OF SUCH DAMAGE.

**H.264 patent notice:** The OpenH264 binary is provided by Cisco Systems, Inc.
under its sublicense from MPEG LA, LLC. Cisco's H.264 patent sublicense covers
end-user use of the Cisco precompiled binary. For further information, see:
http://www.openh264.org/BINARY_LICENSE.txt

### 3.3 Pillow — HPND Attribution

> This software uses Pillow (PIL Fork), an imaging library.
>
> The Python Imaging Library (PIL) is Copyright © 1997–2011 by Secret Labs AB.
> Copyright © 1995–2011 by Fredrik Lundh.
> Pillow is Copyright © 2010–2024 by Jeffrey A. Clark (Alex) and contributors.
>
> Used under the Historical Permission Notice and Disclaimer (HPND) license,
> which permits use, copy, modification, and distribution for any purpose and
> without fee, subject to the inclusion of this copyright notice.

### 3.4 NumPy — BSD-3-Clause Attribution

> This software uses NumPy.
> Copyright © 2005–2024 NumPy Developers. All rights reserved.
> Used under the BSD 3-Clause License.

### 3.5 SciPy — BSD-3-Clause Attribution

> This software uses SciPy.
> Copyright © 2001–2002 Enthought, Inc., 2003–2024 SciPy Developers. All rights reserved.
> Used under the BSD 3-Clause License.

### 3.6 OpenCV (opencv-python-headless) — Apache-2.0 Attribution

> This software uses OpenCV (Open Source Computer Vision Library).
> Copyright © 2000–2024 Intel Corporation, 2009–2011 Willow Garage Inc.,
> 2009–2016 NVIDIA Corporation, 2010–2013 Advanced Micro Devices, Inc.,
> 2015–2016 OpenCV Foundation, 2015–2016 Itseez Inc., 2019–2024 OpenCV.org.
> Used under the Apache License, Version 2.0.

### 3.7 Skia / skia-python — BSD-3-Clause Attribution

> This software uses Skia (Google's 2D graphics library) via the skia-python
> Python bindings.
> Skia is Copyright © Google LLC.
> skia-python bindings are Copyright © kyamagu and contributors.
> Used under the BSD 3-Clause License.

### 3.8 Anthropic SDK (anthropic, claude-code-sdk, mcp) — MIT Attribution

> This software uses the Anthropic Python SDK, Claude Code SDK, and Model Context
> Protocol (MCP) SDK.
> Copyright © 2023–2024 Anthropic, PBC. All rights reserved.
> Used under the MIT License.

### 3.9 Pydantic — MIT Attribution

> This software uses Pydantic.
> Copyright © 2017–2024 Samuel Colvin and contributors.
> Used under the MIT License.

### 3.10 Other Open-Source Components

This software additionally incorporates the following open-source components,
all used under permissive licenses (MIT, BSD, Apache 2.0, or equivalent).
Complete license texts and copyright notices are available in the LICENSES.md
file distributed with the Software:

- httpx (BSD-3-Clause) — HTTP client library
- PyYAML (MIT) — YAML parsing
- click (BSD-3-Clause) — CLI framework
- cryptography (Apache-2.0 / BSD) — Cryptographic primitives
- anyio (MIT) — Async I/O
- attrs (MIT) — Class generation
- jsonschema (MIT) — JSON Schema validation
- typing_extensions (PSF-2.0) — Typing backports
- certifi (MPL-2.0) — CA certificate bundle

---

## 4. Intellectual Property

The Software and all copies thereof are proprietary to Company and title thereto
remains in Company. All rights in the Software not specifically granted in this
Agreement are reserved to Company. Licensee acknowledges that no title to the
intellectual property in the Software is transferred to Licensee.

---

## 5. Disclaimer of Warranties

THE SOFTWARE IS PROVIDED "AS IS" WITHOUT WARRANTY OF ANY KIND. TO THE MAXIMUM
EXTENT PERMITTED BY APPLICABLE LAW, COMPANY DISCLAIMS ALL WARRANTIES, EXPRESS
OR IMPLIED, INCLUDING BUT NOT LIMITED TO WARRANTIES OF MERCHANTABILITY, FITNESS
FOR A PARTICULAR PURPOSE, AND NON-INFRINGEMENT.

COMPANY DOES NOT WARRANT THAT THE SOFTWARE WILL BE ERROR-FREE OR THAT OPERATION
OF THE SOFTWARE WILL BE UNINTERRUPTED.

---

## 6. Limitation of Liability

IN NO EVENT SHALL COMPANY BE LIABLE FOR ANY INDIRECT, INCIDENTAL, SPECIAL,
CONSEQUENTIAL, OR PUNITIVE DAMAGES, OR DAMAGES FOR LOSS OF PROFITS, REVENUE,
DATA, BUSINESS, OR GOODWILL, EVEN IF COMPANY HAS BEEN ADVISED OF THE POSSIBILITY
OF SUCH DAMAGES. COMPANY'S TOTAL CUMULATIVE LIABILITY ARISING OUT OF OR RELATED
TO THIS AGREEMENT WILL NOT EXCEED THE AMOUNTS PAID BY LICENSEE IN THE TWELVE
MONTHS PRECEDING THE CLAIM.

---

## 7. Term and Termination

This Agreement is effective until terminated. Company may terminate this Agreement
immediately upon written notice if Licensee breaches any provision. Upon termination,
Licensee must cease use of the Software and destroy all copies.

---

## 8. Governing Law

This Agreement shall be governed by and construed in accordance with the laws of
[INSERT JURISDICTION], without regard to its conflict of laws provisions.

---

## 9. Entire Agreement

This Agreement constitutes the entire agreement between the parties with respect to
the Software and supersedes all prior agreements, understandings, negotiations, and
discussions.

---

## Appendix A: Open-Source License Texts

The complete texts of all open-source licenses applicable to components used by
parallax-engine are available at:

- **MIT License:** https://opensource.org/licenses/MIT
- **BSD 3-Clause License:** https://opensource.org/licenses/BSD-3-Clause
- **BSD 2-Clause License:** https://opensource.org/licenses/BSD-2-Clause
- **Apache License 2.0:** https://www.apache.org/licenses/LICENSE-2.0
- **LGPL v2.1:** https://www.gnu.org/licenses/old-licenses/lgpl-2.1.txt
- **MPL 2.0:** https://www.mozilla.org/en-US/MPL/2.0/
- **HPND:** https://opensource.org/licenses/HPND
- **OpenH264 Binary License:** http://www.openh264.org/BINARY_LICENSE.txt

A full license audit listing every dependency, its version, license type, and
copyright holder is available in `LICENSES.md` distributed with the Software.

---

*EULA template version 1.0 — parallax-engine commercial distribution*  
*Generated by the parallax-engine build harness on 2026-05-01*  
*Have qualified legal counsel review before commercial distribution.*
