"""Authoring documents ``read_page`` returns instead of a wiki lookup.

``branch_design_guide`` and ``app_ui`` are named by the tool descriptions.
They are not wiki pages. A model that calls ``read_page`` with either name
gets the document.
"""

from __future__ import annotations

import json


def _canonical_page(page: str) -> str:
    key = str(page or "").strip().strip("/").removesuffix(".md")
    for prefix in ("pages/", "drafts/"):
        if key.startswith(prefix):
            key = key[len(prefix):]
    return key.strip("/").casefold()


def authoring_page_json(page: str) -> str:
    """JSON ``{"page", "content"}`` for an authoring document, else empty."""
    key = _canonical_page(page)
    if key == "branch_design_guide":
        from tinyassets.api.branches import _branch_design_guide_prompt

        content = _branch_design_guide_prompt()
    elif key == "app_ui":
        from tinyassets.api.app_ui import APP_UI_AUTHORING_PAGE

        content = APP_UI_AUTHORING_PAGE
    else:
        return ""
    return json.dumps({"page": key, "content": content}, ensure_ascii=False)
