# Changelog

All notable changes to this project. Versions follow [Semantic Versioning](https://semver.org/);
each release also names the Strata base image it was built on.

## [Unreleased]

## [v0.1.0] - first public release

Built on Strata `0.2.9`, one image per flavor: `cuda13` (also the tags without suffix), `cuda12`,
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
- API port placeholder: `503 No model loaded in Strata.` while no model runs.
- Unraid template and Community Applications profile.
