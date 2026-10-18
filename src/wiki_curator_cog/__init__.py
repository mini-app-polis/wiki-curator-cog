"""wiki-curator-cog — WCS wiki maintenance pipeline.

Builds the WCS wiki from the canonical corpus in api-kaianolevine-com: links
statements to concepts, folds narrow entities into broader ones, writes each
page's prose, and stores each page as a document in the API (ADR-005,
ADR-006). The wcs-wiki markdown repo is a local export of the same pages.

Install name:  wiki-curator-cog
Import name:   wiki_curator_cog
"""

from ._version import __version__ as __version__
