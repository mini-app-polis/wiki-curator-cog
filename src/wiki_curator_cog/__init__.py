"""wiki-curator-cog — WCS wiki maintenance pipeline.

Reads structured notes from api-kaianolevine-com and maintains the
wcs-wiki markdown knowledge base. Operates according to the schema
defined in wcs-wiki/CLAUDE.md.

Install name:  wiki-curator-cog
Import name:   wiki_curator_cog
"""

import os

# GitPython refuses to import without a git binary, and the Lambda runtime
# has none. Only the Railway `export` run uses git (git_ops), but every
# module imports it on the way to flow, so the wiki-jobs worker could not
# import at all. Quiet makes the import succeed; a git command, if one were
# called there, would still fail loudly. Set before any submodule imports git.
os.environ.setdefault("GIT_PYTHON_REFRESH", "quiet")

from ._version import __version__ as __version__  # noqa: E402
