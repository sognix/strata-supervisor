# Third-party notices

Strata Supervisor is a web UI that drives [Strata](https://github.com/Niko1221/Strata). Its image is built
`FROM` the official Strata image (`ghcr.io/niko1221/strata`, tag `<STRATA_VERSION>-<STRATA_FLAVOR>` from the build
arguments in the `Dockerfile`, the label `strata.base` of every image names it). This repository contains only the web UI's own
source code; Strata and everything in its image are not copied into this repository and are not modified.

## Strata

- **License:** MIT License
- **Source:** https://github.com/Niko1221/Strata (the release tag matching `STRATA_VERSION`)
- **Copyright:** (c) 2026 Niko1221 and the Strata contributors

```text
MIT License

Copyright (c) 2026 Niko1221 and the Strata contributors

Permission is hereby granted, free of charge, to any person obtaining a copy
of this software and associated documentation files (the "Software"), to deal
in the Software without restriction, including without limitation the rights
to use, copy, modify, merge, publish, distribute, sublicense, and/or sell
copies of the Software, and to permit persons to whom the Software is
furnished to do so, subject to the following conditions:

The above copyright notice and this permission notice shall be included in all
copies or substantial portions of the Software.

THE SOFTWARE IS PROVIDED "AS IS", WITHOUT WARRANTY OF ANY KIND, EXPRESS OR
IMPLIED, INCLUDING BUT NOT LIMITED TO THE WARRANTIES OF MERCHANTABILITY,
FITNESS FOR A PARTICULAR PURPOSE AND NONINFRINGEMENT. IN NO EVENT SHALL THE
AUTHORS OR COPYRIGHT HOLDERS BE LIABLE FOR ANY CLAIM, DAMAGES OR OTHER
LIABILITY, WHETHER IN AN ACTION OF CONTRACT, TORT OR OTHERWISE, ARISING FROM,
OUT OF OR IN CONNECTION WITH THE SOFTWARE OR THE USE OR OTHER DEALINGS IN THE
SOFTWARE.
```

## Components inside the Strata base image

These ship unchanged in the official Strata image this image is built on. They are listed here for
information; their licenses and notices are those of the Strata image and of the projects themselves
(see Strata's repository, including `third_party/`, and its `docker/Dockerfile`):

| Component | License | Source |
|---|---|---|
| Strata Terminal and engine package | MIT | https://github.com/Niko1221/Strata |
| ggml / llama.cpp parts (model preparation tools) | MIT | https://github.com/ggml-org/llama.cpp |
| Python 3.12 (bundled with the Strata Terminal) | Python Software Foundation License | https://www.python.org/ |
| tini | MIT | https://github.com/krallin/tini |
| Ubuntu 24.04 base image and packages | various (per package) | https://ubuntu.com/ |
| NVIDIA CUDA runtime libraries (`cuda` flavors) | NVIDIA CUDA Toolkit EULA (redistributable parts) | https://docs.nvidia.com/cuda/eula/ |
| AMD ROCm runtime libraries (`rocm` flavors) | various open-source licenses (mostly MIT) | https://github.com/ROCm |

The NVIDIA driver itself is not in the image; it comes from the host through the NVIDIA Container Toolkit
(on Unraid: the Nvidia-Driver plugin).

## Models

Strata downloads model files on the first load of a model. They are not part of this project or its image,
and each model comes with its own license; check the model's page before using it.

## Logo

`app/frontend/logo.svg` and `unraid/strata-supervisor.png` are original work for this project (MIT): a power
symbol above three bars. The shape of the bars follows the Strata logo to show that this is a front end for
Strata, not to suggest that it is an official part of it. Strata's logo file is not used.

## Distributing the built image

A published image contains the Strata base image and everything listed above. Keep this notice with it.
The corresponding sources stay available upstream at the links above.
