"""Build the export's contract models in tests, naming only what matters.

The renderer reads the API's own models (``mini_app_polis.api.contract``),
which require every field the API always sends. A test about slugs should
not have to spell out an instructor's background or a reference's timestamp,
so these builders — named after the models the renderer knows them by —
fill the rest with neutral values and return the real contract model.
"""

from __future__ import annotations

import datetime as dt
from typing import Any

from mini_app_polis.api import contract

#: A fixed moment for the timestamps a test never looks at.
_EPOCH = dt.datetime(2026, 1, 1, tzinfo=dt.UTC)


def WcsEntity(**fields: Any) -> contract.WcsEntityItem:  # noqa: N802
    return contract.WcsEntityItem(
        **{"overview_md": "", "status": "", "external_origin": {}, **fields}
    )


def WcsInstructor(**fields: Any) -> contract.WcsInstructorItem:  # noqa: N802
    return contract.WcsInstructorItem(
        **{
            "background_md": "",
            "teaching_themes_md": "",
            "notable_framings_md": "",
            **fields,
        }
    )


def WcsSource(**fields: Any) -> contract.WcsSourceItem:  # noqa: N802
    return contract.WcsSourceItem(
        **{
            "title": None,
            "session_date": None,
            "instructors_raw": [],
            "students_raw": [],
            "organization": "",
            **fields,
        }
    )


def WcsAttribution(**fields: Any) -> contract.WcsSourceAttributionItem:  # noqa: N802
    return contract.WcsSourceAttributionItem(
        **{
            "instructor_id": None,
            "prose": "",
            "raw_term": "",
            "position": 0,
            "origin": "",
            **fields,
        }
    )


def WcsDefinition(**fields: Any) -> contract.WcsEntityDefinitionItem:  # noqa: N802
    return contract.WcsEntityDefinitionItem(
        **{
            "instructor_id": None,
            "term": "",
            "definition": "",
            "position": 0,
            "origin": "",
            **fields,
        }
    )


def WcsRelation(**fields: Any) -> contract.WcsEntityRelationItem:  # noqa: N802
    return contract.WcsEntityRelationItem(
        **{"source_id": None, "prose": "", "origin": "", **fields}
    )


def WcsDrillPurpose(**fields: Any) -> contract.WcsDrillPurposeItem:  # noqa: N802
    return contract.WcsDrillPurposeItem(
        **{
            "source_id": None,
            "skill_name": "",
            "skill_slug": "",
            "prose": "",
            "focus_context": "",
            "origin": "",
            **fields,
        }
    )


def WcsTechniqueRequirement(  # noqa: N802
    **fields: Any,
) -> contract.WcsTechniqueRequirementItem:
    return contract.WcsTechniqueRequirementItem(
        **{
            "source_id": None,
            "skill_name": "",
            "skill_slug": "",
            "prose": "",
            "origin": "",
            **fields,
        }
    )


def WcsReference(**fields: Any) -> contract.WcsSourceReferenceItem:  # noqa: N802
    return contract.WcsSourceReferenceItem(
        **{
            "referenced_name": "",
            "context": "",
            "ref_type": "",
            "origin": "",
            "created_at": _EPOCH,
            **fields,
        }
    )


def WcsWikiExport(**fields: Any) -> contract.WcsWikiExportItem:  # noqa: N802
    return contract.WcsWikiExportItem(
        **{
            "entities": [],
            "instructors": [],
            "sources": [],
            "attributions": [],
            "definitions": [],
            "relations": [],
            "drill_purposes": [],
            "technique_requirements": [],
            "references": [],
            "exported_at": _EPOCH,
            **fields,
        }
    )
