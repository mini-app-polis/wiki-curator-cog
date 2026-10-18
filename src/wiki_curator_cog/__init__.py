"""wiki-curator-cog — WCS wiki maintenance pipeline.

Builds the WCS wiki from the canonical corpus in api-kaianolevine-com: links
statements to concepts, folds narrow entities into broader ones, writes each
page's prose, and stores each page as a document in the API (ADR-005,
ADR-006). The wcs-wiki markdown repo is a local export of the same pages.

Install name:  wiki-curator-cog
Import name:   wiki_curator_cog
"""

from mini_app_polis import load_secrets

from ._version import __version__ as __version__

# Before any module of the package is imported: the worker starts Sentry at
# import, from SENTRY_DSN_COGS, and a secret that arrives after that is too
# late (mini_app_polis.ssm_secrets). Does nothing outside Lambda.
load_secrets()
