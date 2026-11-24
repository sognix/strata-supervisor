# Changelog

All notable changes to this project. Versions follow [Semantic Versioning](https://semver.org/);
each release also names the Strata base image it was built on.

## [v1.0.6] - 2026-11-24

Built on Strata `0.2.21`.

- Inkling-2-Mini: the drafter now defaults to Strata's choice. Strata measures at the first start of a setup whether the drafter is faster on your card and keeps it only then; that first start takes a few minutes longer. When the model is split over several NVIDIA cards, it runs without the drafter. You can still switch it off.
- Inkling-2-Mini GGUF builds now download the drafter as `mtp.safetensors` (0.64 GB) instead of `dspark-Inkling-2-Mini-Q8_0.gguf`; download progress and the Models page count the right file.

## [v1.0.5] - 2026-11-23

Built on Strata `0.2.20`.

- Added a changelog dialog that shows what changed in the current release; it appears automatically after an update and can be opened anytime by clicking the Supervisor version in the sidebar.
- Updated the card selection warning for Hy4-Flash and Inkling-2-Mini to reflect the new multi-card limits (up to 4 cards for Hy4-Flash, up to 4 for Inkling-2-Mini on CUDA, and up to 2 for Inkling-2-Mini on other backends).
- Removed the temporary warning about a potential bug with Inkling-2-Mini on two NVIDIA cards, as it is no longer relevant in the updated Strata version.
- Updated the model file listing logic to correctly identify and manage shared files for native models, including the new DSpark drafter file for Inkling-2-Mini GGUF builds.
- Added links in the sidebar to the Strata GitHub repository and its specific release notes.

## [v1.0.4] - 2026-11-22

Built on Strata `0.2.19`.

- Added support for Tencent Hy4-Flash and Inkling-2-Mini models, which use their own engine programs and file structures.
- Hy4-Flash and Inkling-2-Mini now show specific memory placement details (VRAM, RAM, and SSD usage) provided by Strata.
- For Hy4-Flash and Inkling-2-Mini, the "KV format," "RAM mode," and "VRAM reserve" settings are hidden as they are not used by these engines.
- The "Pictures" setting for Hy4-Flash and Inkling-2-Mini is limited to "on" or "off," as these models use their own image readers.
- The "Draft layer" setting for Hy4-Flash and Inkling-2-Mini is renamed to "Drafter" and limited to "on" or "off," reflecting the built-in drafting mechanism.
- Parallel request limits are now model-specific: Hy4-Flash allows 1 request, Inkling-2-Mini allows up to 64, and other models remain at 8.
- A warning is displayed when configuring Inkling-2-Mini to use two NVIDIA cards, noting a potential bug in Strata 0.2.19 that may cause setup failures.
- The model files page now lists individual files for Hy4-Flash and Inkling-2-Mini folders, allowing for more granular management of shared and specific model components.
- Download progress for Hy4-Flash and Inkling-2-Mini now accurately reflects only the files required for the selected configuration, excluding unused components.
- If a model launch fails due to a known Strata issue, a plain-English explanation of the problem is now displayed in the UI.

## [v1.0.3] - 2026-11-20

Built on Strata `0.2.17`.

- Added support for the Qwen3.6-35B-A3B model, which runs on a dedicated engine path using only the main GPU.
- When configuring Qwen3.6-35B-A3B, the KV format, RAM mode, and VRAM reserve settings are disabled as they are not used by this model.
- The "Split over all cards" option is hidden for Qwen3.6-35B-A3B, and helper roles are disabled since the model runs on a single card.
- Updated memory estimates for Qwen3.6-35B-A3B to reflect that experts are kept in RAM if they do not fit on the main card, with no conversation cache in RAM.
- The model overview page now displays specific memory usage details for Qwen3.6-35B-A3B, including context cache on the main card and expert placement.
- Improved detection of Qwen3.6-35B-A3B model files by name to automatically select the correct family.

## [v1.0.2] - 2026-11-19

Built on Strata `0.2.16`.

- Strata 0.2.16 dropped DeepSeek-V4.1-Flash (0.2.13 was the last version that ran it),
  so it is gone from the list of prepared models and the fit markers too.
  A saved DeepSeek config no longer loads; downloaded DeepSeek files stay in place,
  delete them on the Models page if you no longer need them.

## [v1.0.1] - 2026-11-09

Built on Strata `0.2.13`.

- Added new model options to the catalog, including ISTA-DAS Coder and three NVFP4 variants.

## [v1.0.0] - 2026-11-07

First public release. Built on Strata `0.2.12`, one image per flavor: `cuda13` (also the tags without suffix), `cuda12`,
`rocm-gfx120x`, `rocm-gfx110x`, `rocm-gfx1151`.

- Several model configs, one TOML file each in `/config/models`; load and unload one at a time,
  only by hand. Nothing loads at container start.
- Config form with Strata's catalog (prepared models with fit marker) or your own GGUF folders,
  context, KV format, vision, draft layer, parallel requests, GPU roles, VRAM reserve and RAM mode.
- Plan and "Check download" ask Strata what a load would need without changing anything.
- State in the top bar, the list and the config page, including "loading (downloading)".
- Models page: Strata's downloads and your own GGUF folders with size and the configs that use
  them; delete with confirmation.
- Logs of the launch, the engine and the supervisor, with the API key hidden.
- Hardware page: start checks, cards with VRAM, RAM and swap of the container.
- Strata and Supervisor version at the bottom of the sidebar.
- API port placeholder: `503 No model loaded in Strata.` while no model runs.
- Unraid template and Community Applications profile.
