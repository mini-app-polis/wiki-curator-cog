## [3.3.1](https://github.com/mini-app-polis/wiki-curator-cog/compare/v3.3.0...v3.3.1) (2026-10-18)


### Bug Fixes

* rebuild every page in one job, and load secrets before Sentry starts (MINI-105, CD-038) ([5ccdc2e](https://github.com/mini-app-polis/wiki-curator-cog/commit/5ccdc2ee345d18d466e95032aac12a672b47c292))

# [3.3.0](https://github.com/mini-app-polis/wiki-curator-cog/compare/v3.2.0...v3.3.0) (2026-10-18)


### Features

* materialise with no entities rebuilds every page, in follow-on batches (MINI-103) ([dce2291](https://github.com/mini-app-polis/wiki-curator-cog/commit/dce2291648b44b5f0a9a93018c987b9a5f45a023))

# [3.2.0](https://github.com/mini-app-polis/wiki-curator-cog/compare/v3.1.0...v3.2.0) (2026-10-18)


### Bug Fixes

* **deps:** bump miniapppolis-common-utils to 6.6.0 ([1baa0d7](https://github.com/mini-app-polis/wiki-curator-cog/commit/1baa0d7dbf7144d19fd15ee107a3b846c3f05eba))


### Features

* page documents list the slugs folded into them (MINI-100) ([9b41a2a](https://github.com/mini-app-polis/wiki-curator-cog/commit/9b41a2ad38852dabb60d586692bb1ff4fcc24434))

# [3.1.0](https://github.com/mini-app-polis/wiki-curator-cog/compare/v3.0.1...v3.1.0) (2026-10-18)


### Features

* a run report per wiki job — counts, cost, and a WARN for work left undone ([80d9a00](https://github.com/mini-app-polis/wiki-curator-cog/commit/80d9a00ab19c089d3e43c1321f1356ef3c916c5b))

## [3.0.1](https://github.com/mini-app-polis/wiki-curator-cog/compare/v3.0.0...v3.0.1) (2026-10-18)


### Bug Fixes

* drop rendered_at from page frontmatter so an unchanged corpus renders unchanged (MINI-99) ([1327572](https://github.com/mini-app-polis/wiki-curator-cog/commit/1327572569f17f2b18e8a3e054e8b8540097c703))

# [3.0.0](https://github.com/mini-app-polis/wiki-curator-cog/compare/v2.4.1...v3.0.0) (2026-10-18)


### Code Refactoring

* retire the Railway export run; the curator is the wiki-jobs worker (MINI-95) ([359beb2](https://github.com/mini-app-polis/wiki-curator-cog/commit/359beb2945de98a0e1d4984991e8f05bf96a5a46))


### BREAKING CHANGES

* the export mode and the bare invocation of python -m wiki_curator_cog.main are removed, along with boot.py, git_ops.py, config.py and the GitPython dependency. The deployed curator is the wiki-jobs Lambda worker; the CLI keeps only the local commands (render, link, consolidate, generate, materialise, job, state-import, state-verify) and now requires one.

## [2.4.1](https://github.com/mini-app-polis/wiki-curator-cog/compare/v2.4.0...v2.4.1) (2026-10-18)


### Bug Fixes

* materialise summary separates names gone from pages deleted (MINI-96) ([0effc41](https://github.com/mini-app-polis/wiki-curator-cog/commit/0effc418a7eac8026062b1e526ed9a08d37484e8))

# [2.4.0](https://github.com/mini-app-polis/wiki-curator-cog/compare/v2.3.3...v2.4.0) (2026-10-17)


### Bug Fixes

* **deps:** bump miniapppolis-common-utils to 6.5.0 ([0e8af65](https://github.com/mini-app-polis/wiki-curator-cog/commit/0e8af656676435fd89115e60574ef13f4d31a48f))


### Features

* materialise page documents into the API; render markdown from them (MINI-96) ([a5be955](https://github.com/mini-app-polis/wiki-curator-cog/commit/a5be955df3220c607fa648ae58f6d11b136f9a10))

## [2.3.3](https://github.com/mini-app-polis/wiki-curator-cog/compare/v2.3.2...v2.3.3) (2026-10-17)


### Bug Fixes

* add docstrings to public methods and verify mocks in render_local git test ([bdc5b26](https://github.com/mini-app-polis/wiki-curator-cog/commit/bdc5b26e72bae85febd11543b3c86162c2905ebc))

## [2.3.2](https://github.com/mini-app-polis/wiki-curator-cog/compare/v2.3.1...v2.3.2) (2026-10-17)


### Bug Fixes

* import without a git binary, so the Lambda worker loads (MINI-95) ([94086c5](https://github.com/mini-app-polis/wiki-curator-cog/commit/94086c511421273ffe11d11ffba0ad87f2f2a84c))

## [2.3.1](https://github.com/mini-app-polis/wiki-curator-cog/compare/v2.3.0...v2.3.1) (2026-10-17)


### Bug Fixes

* **ci:** deploy the release tag when a re-run finds it on main's tip (MINI-95) ([ebcfd86](https://github.com/mini-app-polis/wiki-curator-cog/commit/ebcfd86a9c41c859909b070025db0cd5210289e0))

# [2.3.0](https://github.com/mini-app-polis/wiki-curator-cog/compare/v2.2.0...v2.3.0) (2026-10-17)


### Bug Fixes

* **deps:** bump miniapppolis-common-utils to 6.4.0 ([882ea35](https://github.com/mini-app-polis/wiki-curator-cog/commit/882ea35c9b296e76ec1d5ab8bc075d4d335f6f32))
* **deps:** bump miniapppolis-common-utils to 6.4.0 ([eded63c](https://github.com/mini-app-polis/wiki-curator-cog/commit/eded63c65d550f4733ce1c2115f50d7f6c14a07a))
* follow-on jobs only for new links, generate in chunks (MINI-94) ([1f270fd](https://github.com/mini-app-polis/wiki-curator-cog/commit/1f270fd0e99a3fa479eba7b177fe4c1ca5f071b0))
* one state folder for every command, never the current directory ([0eae5cc](https://github.com/mini-app-polis/wiki-curator-cog/commit/0eae5cc6771bc9ebb104394968ebb5c03d338f55))


### Features

* one unit of work per job — link, consolidate, generate (MINI-94) ([96248e3](https://github.com/mini-app-polis/wiki-curator-cog/commit/96248e3eeb171cf8a56e808b5c85c6de3b03eaf3))
* the API is the curator's state store (MINI-94) ([d8985a1](https://github.com/mini-app-polis/wiki-curator-cog/commit/d8985a1fa5887d05d979fd3b5741b4c4d4f30afa))
* wiki-jobs Lambda worker and its deploy (MINI-95) ([8e42b36](https://github.com/mini-app-polis/wiki-curator-cog/commit/8e42b365ed84cb3c46a9249f055cf2787bc3230f))

# [2.2.0](https://github.com/mini-app-polis/wiki-curator-cog/compare/v2.1.0...v2.2.0) (2026-10-17)


### Features

* state-import and state-verify — move curator state to the API (MINI-89) ([042dbc6](https://github.com/mini-app-polis/wiki-curator-cog/commit/042dbc631c41d4c66e19a80e6533d42279c44cd5))

# [2.1.0](https://github.com/mini-app-polis/wiki-curator-cog/compare/v2.0.22...v2.1.0) (2026-10-17)


### Bug Fixes

* accept stray fields in model responses instead of rejecting the batch ([96b9587](https://github.com/mini-app-polis/wiki-curator-cog/commit/96b958766e28d634a74cdcbef2cba80afc646c9a))
* consolidate — explicit fold decision; nest facets in Details ([3539cbb](https://github.com/mini-app-polis/wiki-curator-cog/commit/3539cbb5b090b52fb5809c897007d5b65140f2e1))
* correct or unlink model-written links that point at no page ([f64c597](https://github.com/mini-app-polis/wiki-curator-cog/commit/f64c5975f3ce6e69e7a2eaeb66739cd9b335ea89))
* **deps:** bump miniapppolis-common-utils to 6.2.0 ([a573506](https://github.com/mini-app-polis/wiki-curator-cog/commit/a5735060788b1f33c1903f9c66b158cf2f3c552a))
* hubs converge in one run — cover all pending mentions per call ([054a1de](https://github.com/mini-app-polis/wiki-curator-cog/commit/054a1de5209214b42757cae20c7e60c94763b59a))
* hubs converge in one run; parse the first JSON object in model responses ([1200a48](https://github.com/mini-app-polis/wiki-curator-cog/commit/1200a48ba01dc9db9884f6d615eed0124ebc16c5))
* refuse to render an empty export over the wiki ([2c22473](https://github.com/mini-app-polis/wiki-curator-cog/commit/2c22473f122ed94b92164056d74ba448ab25eb1c))


### Features

* add local generation (generate mode) and render --prose ([362d443](https://github.com/mini-app-polis/wiki-curator-cog/commit/362d4434ec311c08db873ba088594a5bc5cab330))
* add local linking pass (link mode) writing references to links.json ([85dd57f](https://github.com/mini-app-polis/wiki-curator-cog/commit/85dd57ffda67075e4afb0a91cf5aee9b2c9f4ebe))
* add local linking pass (link mode) writing references to links.json ([c99ed7e](https://github.com/mini-app-polis/wiki-curator-cog/commit/c99ed7edc888ac98c9225b6488b56f31f06fce45))
* add local render mode with link check and corpus quality summary ([66482bc](https://github.com/mini-app-polis/wiki-curator-cog/commit/66482bc7bdf3b44592b42d49fb5c9428aad6fa4c))
* consolidate — private thin-entity review ([0813361](https://github.com/mini-app-polis/wiki-curator-cog/commit/0813361b436fb720e018d3284ab5c0df8a592b0c))
* consolidate — weigh support; never fold into a thinner page ([3768571](https://github.com/mini-app-polis/wiki-curator-cog/commit/376857183ca9935cb9b4a8f8cd09c6afc07f5f8c))
* facet consolidation — fold narrow entities into their broader concept ([136d39b](https://github.com/mini-app-polis/wiki-curator-cog/commit/136d39bfe2b22030b4d9f3185dfde593a15af40d))
* fixed-shape prose — rewrite, never grow; condense over-shape pages ([8a6ae73](https://github.com/mini-app-polis/wiki-curator-cog/commit/8a6ae738ab4d6b7ad2cc23e821ecdf4d9bb401ff))
* fold drills and patterns; dry runs and cost reporting ([204ddfc](https://github.com/mini-app-polis/wiki-curator-cog/commit/204ddfce03bc0689540879798af87a9f49eb106d))
* generate a short definition plus an In practice section ([6d05f66](https://github.com/mini-app-polis/wiki-curator-cog/commit/6d05f66ee5a41c1133dacdc10f34d278fb4d3b50))
* merge entities a note declares the same (alias_of), at build time ([cb5fc0e](https://github.com/mini-app-polis/wiki-curator-cog/commit/cb5fc0e6f1d38fb203a944e6157e5a514835a12b))
* overlay linker references on the local render ([bf876c9](https://github.com/mini-app-polis/wiki-curator-cog/commit/bf876c93c3282d5be0b643298237c812409dc5e2))
* run link and generate calls in parallel (--workers) ([c556d29](https://github.com/mini-app-polis/wiki-curator-cog/commit/c556d29fb9b0fad0873985eff8bb187af5ce7043))
* show only a count when more than 25 pages reference an entity ([4ed3f5a](https://github.com/mini-app-polis/wiki-curator-cog/commit/4ed3f5a9bde7bedc7c8269e1ddb66248b26cbcf9))

## [2.0.22](https://github.com/mini-app-polis/wiki-curator-cog/compare/v2.0.21...v2.0.22) (2026-10-16)


### Bug Fixes

* **deps:** bump miniapppolis-common-utils to 6.1.0 ([18f927e](https://github.com/mini-app-polis/wiki-curator-cog/commit/18f927e0ff89be88b55f245c9ab42452c73eebae))
* **deps:** bump miniapppolis-common-utils to 6.1.1 ([3171b87](https://github.com/mini-app-polis/wiki-curator-cog/commit/3171b87a608e726f2362d4045e31eb54f3dc5461))

## [2.0.21](https://github.com/mini-app-polis/wiki-curator-cog/compare/v2.0.20...v2.0.21) (2026-10-16)


### Bug Fixes

* **deps:** require miniapppolis-common-utils 6 (>=6.0.1) ([d725553](https://github.com/mini-app-polis/wiki-curator-cog/commit/d725553838614a868d1476cfe31999b011edc0b1))

## [2.0.20](https://github.com/mini-app-polis/wiki-curator-cog/compare/v2.0.19...v2.0.20) (2026-10-16)


### Bug Fixes

* **deps:** bump miniapppolis-common-utils to 5.25.5 ([5c9827c](https://github.com/mini-app-polis/wiki-curator-cog/commit/5c9827c91aba7f134b63404ccf9de3fc988c24cb))

## [2.0.19](https://github.com/mini-app-polis/wiki-curator-cog/compare/v2.0.18...v2.0.19) (2026-10-15)


### Bug Fixes

* **deps:** bump miniapppolis-common-utils to 5.25.4 ([9a03096](https://github.com/mini-app-polis/wiki-curator-cog/commit/9a0309657290f4f1bc640e3ab773d17b0add018f))

## [2.0.18](https://github.com/mini-app-polis/wiki-curator-cog/compare/v2.0.17...v2.0.18) (2026-10-12)


### Bug Fixes

* **deps:** bump miniapppolis-common-utils to 5.25.3 ([c48869b](https://github.com/mini-app-polis/wiki-curator-cog/commit/c48869b6d96291cf30bc12dcbe53348a6d00acc3))

## [2.0.17](https://github.com/mini-app-polis/wiki-curator-cog/compare/v2.0.16...v2.0.17) (2026-10-11)


### Bug Fixes

* **deps:** bump miniapppolis-common-utils to 5.25.1 ([d59d7a0](https://github.com/mini-app-polis/wiki-curator-cog/commit/d59d7a00039a6612a10aefde88da473abfb7eb86))
* **deps:** bump miniapppolis-common-utils to 5.25.2 ([2263c9a](https://github.com/mini-app-polis/wiki-curator-cog/commit/2263c9ac8437b342c43a561f56ed818d2162abce))

## [2.0.16](https://github.com/mini-app-polis/wiki-curator-cog/compare/v2.0.15...v2.0.16) (2026-10-09)


### Bug Fixes

* **deps:** bump miniapppolis-common-utils to 5.25.0 ([554abbe](https://github.com/mini-app-polis/wiki-curator-cog/commit/554abbe5e2364d5b83c327203318a54e3863675c))

## [2.0.15](https://github.com/mini-app-polis/wiki-curator-cog/compare/v2.0.14...v2.0.15) (2026-10-09)


### Bug Fixes

* **deps:** bump miniapppolis-common-utils to 5.24.1 ([896f791](https://github.com/mini-app-polis/wiki-curator-cog/commit/896f7914a39fdbcdc74380b5231153eb4d501384))

## [2.0.14](https://github.com/mini-app-polis/wiki-curator-cog/compare/v2.0.13...v2.0.14) (2026-10-08)


### Bug Fixes

* **observability:** move to shared SENTRY_DSN_COGS and tag service ([e6b81e6](https://github.com/mini-app-polis/wiki-curator-cog/commit/e6b81e603b89627a399d57dfc4424d3009aaa091))

## [2.0.13](https://github.com/mini-app-polis/wiki-curator-cog/compare/v2.0.12...v2.0.13) (2026-10-08)


### Bug Fixes

* **deps:** bump miniapppolis-common-utils ([18def66](https://github.com/mini-app-polis/wiki-curator-cog/commit/18def667c4f4069165ffaf4fd092254c04f98579))

## [2.0.12](https://github.com/mini-app-polis/wiki-curator-cog/compare/v2.0.11...v2.0.12) (2026-10-08)


### Bug Fixes

* **deps:** bump miniapppolis-common-utils ([383331d](https://github.com/mini-app-polis/wiki-curator-cog/commit/383331dd5ee837804d25595bb6877050b90cd0c0))
* **deps:** bump python-dotenv ([b95d48f](https://github.com/mini-app-polis/wiki-curator-cog/commit/b95d48f3d85726a0266791b1a48473896445ba8b))

## [2.0.11](https://github.com/mini-app-polis/wiki-curator-cog/compare/v2.0.10...v2.0.11) (2026-10-07)


### Bug Fixes

* **deps:** bump gitpython ([4c45aa2](https://github.com/mini-app-polis/wiki-curator-cog/commit/4c45aa27dd8ba77aa8f1f8193bc02d2e4d164647))
* **deps:** bump miniapppolis-common-utils ([366c386](https://github.com/mini-app-polis/wiki-curator-cog/commit/366c3867ba3218eea9a3f91020148365e241a152))

## [2.0.10](https://github.com/mini-app-polis/wiki-curator-cog/compare/v2.0.9...v2.0.10) (2026-10-06)


### Bug Fixes

* **deps:** bump miniapppolis-common-utils ([0643af4](https://github.com/mini-app-polis/wiki-curator-cog/commit/0643af4894c27f570ee67f42c19d5baab8fb6003))
* **deps:** bump miniapppolis-common-utils ([370ad9a](https://github.com/mini-app-polis/wiki-curator-cog/commit/370ad9af7eff46223cab031c9f1b8d3aecd79b35))
* **deps:** bump sentry-sdk ([7c66694](https://github.com/mini-app-polis/wiki-curator-cog/commit/7c66694cab6e86bd71aad30de5cb9d4426900864))

## [2.0.9](https://github.com/mini-app-polis/wiki-curator-cog/compare/v2.0.8...v2.0.9) (2026-10-01)


### Bug Fixes

* **deps:** bump miniapppolis-common-utils ([bff36d9](https://github.com/mini-app-polis/wiki-curator-cog/commit/bff36d9ad941b9e74e3ce45f3d137b38f7d81947))
* **deps:** bump miniapppolis-common-utils ([f1dfbb9](https://github.com/mini-app-polis/wiki-curator-cog/commit/f1dfbb9ec82283656d06673a0c62fb294f25a5c4))

## [2.0.8](https://github.com/mini-app-polis/wiki-curator-cog/compare/v2.0.7...v2.0.8) (2026-09-30)


### Bug Fixes

* **deps:** patch vulnerable dependencies (urllib3, virtualenv) ([6eb015c](https://github.com/mini-app-polis/wiki-curator-cog/commit/6eb015cbc61786469f7d1946b4d633275f58b9ad))

## [2.0.7](https://github.com/mini-app-polis/wiki-curator-cog/compare/v2.0.6...v2.0.7) (2026-09-30)


### Bug Fixes

* **api:** render from common's contract models and drop the local copies ([3a6330f](https://github.com/mini-app-polis/wiki-curator-cog/commit/3a6330f2aa8a52824b7f629eb29223d5b7fdf0cc))
* uv lock ([0c10b8a](https://github.com/mini-app-polis/wiki-curator-cog/commit/0c10b8add6024d96130a47488d133a1841b64971))

## [2.0.6](https://github.com/mini-app-polis/wiki-curator-cog/compare/v2.0.5...v2.0.6) (2026-09-29)


### Bug Fixes

* **deps:** bump oauthlib to 4.0.0 for CVE-2026-49265 ([ea2c436](https://github.com/mini-app-polis/wiki-curator-cog/commit/ea2c436c8c7b8681dc3ea1b8c87f40b871dc4a12))

## [2.0.5](https://github.com/mini-app-polis/wiki-curator-cog/compare/v2.0.4...v2.0.5) (2026-09-28)


### Bug Fixes

* **deps:** bump miniapppolis-common-utils in the commons group ([83575e0](https://github.com/mini-app-polis/wiki-curator-cog/commit/83575e06de105d7d83e304717f88dfde37b59127))

## [2.0.4](https://github.com/mini-app-polis/wiki-curator-cog/compare/v2.0.3...v2.0.4) (2026-09-28)


### Bug Fixes

* **deps:** bump the python-minor-and-patch group with 2 updates ([db77407](https://github.com/mini-app-polis/wiki-curator-cog/commit/db7740757be66a78c64d8d9c8123fb3a58d430ce))

## [2.0.3](https://github.com/mini-app-polis/wiki-curator-cog/compare/v2.0.2...v2.0.3) (2026-09-24)


### Bug Fixes

* **deps:** require common-utils 5.13.1 ([9a5b66d](https://github.com/mini-app-polis/wiki-curator-cog/commit/9a5b66d53c191d53fd32861a8bfbff86984276b0))

## [2.0.2](https://github.com/mini-app-polis/wiki-curator-cog/compare/v2.0.1...v2.0.2) (2026-09-23)


### Bug Fixes

* **report:** report what the commit changed, not what was rewritten ([0666f2e](https://github.com/mini-app-polis/wiki-curator-cog/commit/0666f2e0d59a7fdea5930bce6afe5aa42750a7ac))

## [2.0.1](https://github.com/mini-app-polis/wiki-curator-cog/compare/v2.0.0...v2.0.1) (2026-09-23)


### Bug Fixes

* addressing findings from evaluator ([1c9a44a](https://github.com/mini-app-polis/wiki-curator-cog/commit/1c9a44a8dbd332a6b6e66500a17bfca7764a70d2))
* **config:** drop env keys for settings that no longer exist ([90215c5](https://github.com/mini-app-polis/wiki-curator-cog/commit/90215c5e2339775c449acda584c14f9cb0fdf966))

# [2.0.0](https://github.com/mini-app-polis/wiki-curator-cog/compare/v1.8.2...v2.0.0) (2026-09-23)


* feat!: run as a one-shot Railway process, drop Prefect ([e01e094](https://github.com/mini-app-polis/wiki-curator-cog/commit/e01e0941f4829d0ad72f51d9cb6ff85e28b973bd))


### Features

* gate auto-run outside production, check push access before the render ([742f3fc](https://github.com/mini-app-polis/wiki-curator-cog/commit/742f3fcc595526f1b5bad9acc0b0cce1aca47ae0))


### BREAKING CHANGES

* PREFECT_API_KEY and PREFECT_API_URL are no longer read.

## [1.8.2](https://github.com/mini-app-polis/wiki-curator-cog/compare/v1.8.1...v1.8.2) (2026-09-21)


### Bug Fixes

* **deps:** bump anyio to 4.15.1 for CVE-2026-63374, CVE-2026-64847 ([f9664c0](https://github.com/mini-app-polis/wiki-curator-cog/commit/f9664c055b5e602ae13b7f9ea1ce482adb250852))
* **deps:** bump the python-minor-and-patch group with 4 updates ([5338167](https://github.com/mini-app-polis/wiki-curator-cog/commit/53381674c717c1df8230ed1e6322017727c7cc81))

## [1.8.1](https://github.com/mini-app-polis/wiki-curator-cog/compare/v1.8.0...v1.8.1) (2026-09-17)


### Bug Fixes

* **deps:** bump the python-minor-and-patch group with 3 updates ([dfb06d7](https://github.com/mini-app-polis/wiki-curator-cog/commit/dfb06d7306c86f74ae47d71c6c83262ccfb7be51))

# [1.8.0](https://github.com/mini-app-polis/wiki-curator-cog/compare/v1.7.1...v1.8.0) (2026-09-11)


### Features

* verb status and duration added ([d6da876](https://github.com/mini-app-polis/wiki-curator-cog/commit/d6da8762bb99e6c0492785c51b4a78da2fdb5895))

## [1.7.1](https://github.com/mini-app-polis/wiki-curator-cog/compare/v1.7.0...v1.7.1) (2026-09-11)


### Bug Fixes

* stop serving prefect deployments outside production ([ef57175](https://github.com/mini-app-polis/wiki-curator-cog/commit/ef57175041173355d9fc1d3ab657bb160ffe6056))
* test setup ([46ee696](https://github.com/mini-app-polis/wiki-curator-cog/commit/46ee696a21be9d645b09c33ea11c13dc552bf477))

# [1.7.0](https://github.com/mini-app-polis/wiki-curator-cog/compare/v1.6.4...v1.7.0) (2026-09-10)


### Bug Fixes

* moving to common 5.7.1 ([d2b5f4c](https://github.com/mini-app-polis/wiki-curator-cog/commit/d2b5f4c806af160f9a20c6c80a80bcb4806216ab))


### Features

* **environment:** gate the Healthchecks ping outside production ([f9c5a46](https://github.com/mini-app-polis/wiki-curator-cog/commit/f9c5a46e8d8ad6681260d5c74e5d771a92fc5c7f))

## [1.6.4](https://github.com/mini-app-polis/wiki-curator-cog/compare/v1.6.3...v1.6.4) (2026-09-10)


### Bug Fixes

* **deps:** bump the python-minor-and-patch group with 2 updates ([325bac5](https://github.com/mini-app-polis/wiki-curator-cog/commit/325bac5a666741805a3b71fa1c8ec6bbe66cabcc))
* **deps:** upgrade cryptography, httplib2, h2 and pyasn1 past known advisories ([d2b1227](https://github.com/mini-app-polis/wiki-curator-cog/commit/d2b1227d66601c5233db5e5ff879983e0aa85fd3))

## [1.6.3](https://github.com/mini-app-polis/wiki-curator-cog/compare/v1.6.2...v1.6.3) (2026-09-09)


### Bug Fixes

* **export:** report dangling references, slug collisions and unresolved instructors ([d0910e9](https://github.com/mini-app-polis/wiki-curator-cog/commit/d0910e93f00bb51c0c325ab66e5784ced972fb90))

## [1.6.2](https://github.com/mini-app-polis/wiki-curator-cog/compare/v1.6.1...v1.6.2) (2026-09-08)


### Bug Fixes

* ci verification ([6a04b49](https://github.com/mini-app-polis/wiki-curator-cog/commit/6a04b499678f27964d6ed62eff9e2e50da444b4a))

## [1.6.1](https://github.com/mini-app-polis/wiki-curator-cog/compare/v1.6.0...v1.6.1) (2026-09-08)


### Bug Fixes

* dependency ([b12580c](https://github.com/mini-app-polis/wiki-curator-cog/commit/b12580c96640190652251cc2a1246a8692aee897))

# [1.6.0](https://github.com/mini-app-polis/wiki-curator-cog/compare/v1.5.5...v1.6.0) (2026-09-08)


### Features

* utilizing common and notification at the cog level ([a5ade10](https://github.com/mini-app-polis/wiki-curator-cog/commit/a5ade103d65a92ee18a0577ca6ca5438281581ee))

## [1.5.5](https://github.com/mini-app-polis/wiki-curator-cog/compare/v1.5.4...v1.5.5) (2026-09-08)


### Bug Fixes

* **deps:** bump the python-minor-and-patch group with 2 updates ([666738d](https://github.com/mini-app-polis/wiki-curator-cog/commit/666738dcee2dbd5f3c90c0dd949337e1f6e7b751))

## [1.5.4](https://github.com/mini-app-polis/wiki-curator-cog/compare/v1.5.3...v1.5.4) (2026-09-06)


### Bug Fixes

* **release:** source the version from a committed file instead of pyproject.toml ([69001ad](https://github.com/mini-app-polis/wiki-curator-cog/commit/69001ad0d4f88d30554458b6a7f12b96e71e4775))

## [1.5.3](https://github.com/mini-app-polis/wiki-curator-cog/compare/v1.5.2...v1.5.3) (2026-09-05)


### Bug Fixes

* **deps:** consume miniapppolis-common-utils from PyPI ([233220d](https://github.com/mini-app-polis/wiki-curator-cog/commit/233220d157a23948d46bb4fcf2e5a8b342c00bdf))

## [1.5.2](https://github.com/mini-app-polis/wiki-curator-cog/compare/v1.5.1...v1.5.2) (2026-09-05)


### Bug Fixes

* **git:** never three-way merge a lockfile ([ca91b4b](https://github.com/mini-app-polis/wiki-curator-cog/commit/ca91b4be1b03af1e76753725283c1c624d396349))

## [1.5.1](https://github.com/mini-app-polis/wiki-curator-cog/compare/v1.5.0...v1.5.1) (2026-09-04)


### Bug Fixes

* **deps:** bump anthropic from 0.102.0 to 1.3.0 ([d61810b](https://github.com/mini-app-polis/wiki-curator-cog/commit/d61810b37d15df036375ec447f3c6f197d3fed04))
* **deps:** bump the python-minor-and-patch group with 11 updates ([32ab703](https://github.com/mini-app-polis/wiki-curator-cog/commit/32ab7037460e4f46cb127cd6e226e6328ee50574))

# [1.5.0](https://github.com/mini-app-polis/wiki-curator-cog/compare/v1.4.1...v1.5.0) (2026-09-04)


### Features

* **deps:** automate dependency updates ([1d4e940](https://github.com/mini-app-polis/wiki-curator-cog/commit/1d4e940d9c274c29f893ad8b0d0036036405aa51))

## [1.4.1](https://github.com/mini-app-polis/wiki-curator-cog/compare/v1.4.0...v1.4.1) (2026-09-02)


### Bug Fixes

* **deps:** pin common-python-utils v4.0.0 ([c9f1f18](https://github.com/mini-app-polis/wiki-curator-cog/commit/c9f1f184a5755dd1d6535ed3a4f1138a57055d9b))

# [1.4.0](https://github.com/mini-app-polis/wiki-curator-cog/compare/v1.3.0...v1.4.0) (2026-09-02)


### Bug Fixes

* **api-client:** drop the shared Clerk machine secret fallback ([036ff14](https://github.com/mini-app-polis/wiki-curator-cog/commit/036ff1454af1ac534b85248f796de5896d0ee526))
* **deps:** pin common-python-utils v3.2.0 exactly ([2e6a024](https://github.com/mini-app-polis/wiki-curator-cog/commit/2e6a024e5e5ec227ed7194db36c4f2322b354da1))


### Features

* **api:** authenticate with this cog's own named API key ([8d3ea29](https://github.com/mini-app-polis/wiki-curator-cog/commit/8d3ea29e12fcaf833043c3ee8beb228a1db4d3ca))

# [1.3.0](https://github.com/mini-app-polis/wiki-curator-cog/compare/v1.2.6...v1.3.0) (2026-05-28)


### Features

* rewrite curator as stateless renderer over the entity substrate ([f8884c4](https://github.com/mini-app-polis/wiki-curator-cog/commit/f8884c40e5f803a8f4030bff29a45ee23b3834d5))

## [1.2.6](https://github.com/mini-app-polis/wiki-curator-cog/compare/v1.2.5...v1.2.6) (2026-05-18)


### Bug Fixes

* updating documentation to current state ([2a6a75d](https://github.com/mini-app-polis/wiki-curator-cog/commit/2a6a75dfbae4323e76c45936902c9e96768c5851))

## [1.2.5](https://github.com/mini-app-polis/wiki-curator-cog/compare/v1.2.4...v1.2.5) (2026-05-18)


### Bug Fixes

* always regenerate Overview, Across sources, and status ([20d69e3](https://github.com/mini-app-polis/wiki-curator-cog/commit/20d69e33429f49e809eaf577559c2dbcbfbcb927))

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
