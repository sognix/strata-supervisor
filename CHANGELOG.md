# Changelog

All notable changes to this project. Versions follow [Semantic Versioning](https://semver.org/);
each release also names the Strata base image it was built on.

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
