## [1.2.4](https://github.com/mini-app-polis/wiki-curator-cog/compare/v1.2.3...v1.2.4) (2026-05-18)


### Bug Fixes

* backfill mode bypasses curator_version skip ([43adf27](https://github.com/mini-app-polis/wiki-curator-cog/commit/43adf279e8a04e6bbf1f02a30efe79823381268d))

## [1.2.3](https://github.com/mini-app-polis/wiki-curator-cog/compare/v1.2.2...v1.2.3) (2026-05-18)


### Bug Fixes

* apply instructor alias map to references[] codepath ([54689ad](https://github.com/mini-app-polis/wiki-curator-cog/commit/54689ad84875a188ee80eceaf8e074502fcef818))

## [1.2.2](https://github.com/mini-app-polis/wiki-curator-cog/compare/v1.2.1...v1.2.2) (2026-05-18)


### Bug Fixes

* apply instructor alias map to references[] codepath ([6414e9c](https://github.com/mini-app-polis/wiki-curator-cog/commit/6414e9c257c1fc195f0b5aa6f07a3e1c2c0f35bc))

## [1.2.1](https://github.com/mini-app-polis/wiki-curator-cog/compare/v1.2.0...v1.2.1) (2026-05-18)


### Bug Fixes

* force HTTP/1.1 + retry push to absorb transient stream errors ([8e3efd9](https://github.com/mini-app-polis/wiki-curator-cog/commit/8e3efd959287f92805d4f1423b78f6fec4634dd4))

# [1.2.0](https://github.com/mini-app-polis/wiki-curator-cog/compare/v1.1.0...v1.2.0) (2026-05-18)


### Features

* populate instructor pages, regenerate index, tighten filters ([ad72055](https://github.com/mini-app-polis/wiki-curator-cog/commit/ad72055f13cd7b127026793bfcf06f89b689406b))

# [1.1.0](https://github.com/mini-app-polis/wiki-curator-cog/compare/v1.0.8...v1.1.0) (2026-05-18)


### Features

* collapse concept/technique vocab variants via alias maps and plural stemming ([6ea65ea](https://github.com/mini-app-polis/wiki-curator-cog/commit/6ea65ea1be35bc7e6a187c2f2946456aac5eedfc))

## [1.0.8](https://github.com/mini-app-polis/wiki-curator-cog/compare/v1.0.7...v1.0.8) (2026-05-18)


### Bug Fixes

* build ([f34ac1b](https://github.com/mini-app-polis/wiki-curator-cog/commit/f34ac1b89fe080bd76659ab9bfdc4981010fa97c))

## [1.0.7](https://github.com/mini-app-polis/wiki-curator-cog/compare/v1.0.6...v1.0.7) (2026-05-18)


### Bug Fixes

* build ([a4098bd](https://github.com/mini-app-polis/wiki-curator-cog/commit/a4098bdfadfa09834e030b333bab89c830510986))

## [1.0.6](https://github.com/mini-app-polis/wiki-curator-cog/compare/v1.0.5...v1.0.6) (2026-05-18)


### Bug Fixes

* build ([1856506](https://github.com/mini-app-polis/wiki-curator-cog/commit/18565069db0176d134268494e5a030f30b8ab642))

## [1.0.5](https://github.com/mini-app-polis/wiki-curator-cog/compare/v1.0.4...v1.0.5) (2026-05-18)


### Bug Fixes

* build ([06d794f](https://github.com/mini-app-polis/wiki-curator-cog/commit/06d794f010d236a87b524aa31f8090fca82f0f65))
* pin NIXPACKS_UV_VERSION=0.7.0 so build understands revision=3 lockfile ([f5222b7](https://github.com/mini-app-polis/wiki-curator-cog/commit/f5222b7737b1d33078c0ce5675c49709e0ef565e))
* tests ([b46b333](https://github.com/mini-app-polis/wiki-curator-cog/commit/b46b333296553a8e9e014204d6008572babd365b))

## [1.0.4](https://github.com/mini-app-polis/wiki-curator-cog/compare/v1.0.3...v1.0.4) (2026-05-17)


### Bug Fixes

* align release+build setup with evaluator-cog pattern ([ac33e8b](https://github.com/mini-app-polis/wiki-curator-cog/commit/ac33e8b6f8f61560010c08e17676f3e01a5f86df))

## [1.0.3](https://github.com/mini-app-polis/wiki-curator-cog/compare/v1.0.2...v1.0.3) (2026-05-17)


### Bug Fixes

* pin NIXPACKS_UV_VERSION via nixpacks.toml so Railway build resolves uv ([3f19a50](https://github.com/mini-app-polis/wiki-curator-cog/commit/3f19a50887be4fbecff3c16729addda43c3952a2))

## [1.0.2](https://github.com/mini-app-polis/wiki-curator-cog/compare/v1.0.1...v1.0.2) (2026-05-17)


### Bug Fixes

* add .python-version so Nixpacks picks the right Python (matches transcription-cog) ([ba23841](https://github.com/mini-app-polis/wiki-curator-cog/commit/ba2384118def1d160c6a59dafff26bf22cc8ac49))

## [1.0.1](https://github.com/mini-app-polis/wiki-curator-cog/compare/v1.0.0...v1.0.1) (2026-05-17)


### Bug Fixes

* CuratorState.curator_version_at_last_run is str, not int ([31f39c8](https://github.com/mini-app-polis/wiki-curator-cog/commit/31f39c86908fe946c331d1c98105dc5b6bf50e06))

# 1.0.0 (2026-05-17)


### Bug Fixes

* cap slugify length to survive long upstream concept names ([7e778a4](https://github.com/mini-app-polis/wiki-curator-cog/commit/7e778a4299a5a193a80a37fc2934e99bf064c75a))
* clean up partial source page on derived-fanout failure + cap slug length ([c73808a](https://github.com/mini-app-polis/wiki-curator-cog/commit/c73808a0b039f996e452fdda2764c42c56a4a61f))
* release pattern ([8d1cfe5](https://github.com/mini-app-polis/wiki-curator-cog/commit/8d1cfe59ea50d9b3e2a7f1f9cc4e4c980c6a5a9e))
* release pattern ([520f04a](https://github.com/mini-app-polis/wiki-curator-cog/commit/520f04a54f3c22dea87ab8a9aa1728bc276fae8e))
* rev 2 ([6d83a77](https://github.com/mini-app-polis/wiki-curator-cog/commit/6d83a775f16158e45a032820d871c7ab1afd70b7))
* rev 3 - concept based ([85e9e88](https://github.com/mini-app-polis/wiki-curator-cog/commit/85e9e889f7a66ba818890a04b63672ff2d171f99))
* updates for running on railway ([ed52971](https://github.com/mini-app-polis/wiki-curator-cog/commit/ed5297191d5bf55ae13f93306c6e6a1c53e32b90))
* updates to the process ([6aa8561](https://github.com/mini-app-polis/wiki-curator-cog/commit/6aa8561c38336a43736cf7864ec5fd171c3d1c07))


### Features

* curator v4 quality filters for concept/instructor fanout ([1bc1047](https://github.com/mini-app-polis/wiki-curator-cog/commit/1bc1047b424e6ea018f7e37f9ef8505b543bddcf))
* Inital checkin ([fdb10ee](https://github.com/mini-app-polis/wiki-curator-cog/commit/fdb10ee682dae2e815d14fedf2e735fc0e0e5eed))
* phase one implementation, untested ([22aa08d](https://github.com/mini-app-polis/wiki-curator-cog/commit/22aa08d35147665500443068aa5b79918bfec0ff))

# Changelog

All notable changes to this project will be documented in this file. The format follows [semantic-release](https://github.com/semantic-release/semantic-release) conventions; entries are generated from commit messages.

## [0.1.0] - Initial commit

- Repo scaffolding: pyproject.toml, evaluator.yaml, pre-commit, CI workflow.
- `wiki_curator_cog` package skeleton: config, API client wrapper, models, main entry point.
- Backfill and incremental flow stubs.
- No production functionality yet — Phase 1 implementation pending.
