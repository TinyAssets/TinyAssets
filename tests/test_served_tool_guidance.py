"""Long-form served-agent guidance is REACHABLE, not resident — and nothing was lost.

Measured 2026-09-25 (`tests/test_converse_turn_cost.py`): the engine
tool-definition block is re-sent on every model round-trip of every served
founder turn, it was 63,383 B, and `write_graph`'s manual was 38,513 chars of it
— 61% of the block for one handle. Production confirmed the loop shape on
2026-09-26 UTC: two recall turns on the free universe ran 3 rounds each.

So the manual moved into handbook chapters served by
``read_graph target="handbook"``. These tests hold the two halves of that claim
together: the description really got smaller, AND not one line of guidance is
gone — pinned against a digest taken before the split.

Change: `openspec/changes/engine-tool-manual-on-demand/`.
"""

from __future__ import annotations

import asyncio
import hashlib
import json
import pathlib
import re
import types
from collections import Counter

import pytest

from tinyassets import engine_mcp_server as engine
from tinyassets.served_tools import SERVED_ENGINE_MCP_TOOLS

#: Every word `write_graph`'s guidance carried BEFORE the 2026-09-26 split, with
#: its count, generated from the commit before the split. Committed rather than
#: recomputed so the proof needs no git history at test time.
#:
#: A WORD MULTISET, not a line digest, because the invariant is "no guidance was
#: lost" and that has to survive legitimate relocation and rewording: the split
#: adds a resident chapter index, and the PR #4000 review asked for the base64
#: rule to move BACK to the resident description. A line-sequence digest fails on
#: any such move and would have to be re-pinned each time, which is how a
#: preservation check quietly stops preserving anything. Deletion still fails.
PRE_SPLIT_WORDS = (
    pathlib.Path(__file__).parent
    / "fixtures"
    / "write_graph_guidance_pre_split_words.txt"
)
PRE_SPLIT_WORDS_SHA256 = (
    "37c5977f309b0395fc784d1ca0b5df348951598a9de120db2d5cdf988c5689a8"
)

#: How to put the chapters back where they were: the resident index sits exactly
#: where they used to start, and the tail resumes at this line. This pair IS the
#: documented reconstruction order — the test is the place that records it.
INDEX_ANCHOR = "THE HANDBOOK."
TAIL_ANCHOR = "A branch is a stored graph SHAPE"

#: Chapter order as the resident index names them. `delivering` was added
#: 2026-09-26 with open receivers: an agent asked to let other users send its
#: universe something reached for a public webhook because no chapter named the
#: cross-user delivery primitive. Appended last, so the reconstruction order the
#: tests below assert is unchanged. `interfaces` (user-authored app UIs) was
#: appended after it the same day, for the same reason. `systems` (2026-09-30):
#: asked for an always-on team of agents with its own screen, the universe built
#: an external service and asked for hosting, because nothing mapped that request
#: onto agent nodes, automations, files and an app UI.
#:
#: `branches` (2026-09-30) is FIRST, because it is the chapter for the call every
#: other chapter presupposes -- including `systems`, which points at it for the
#: concrete create syntax. It exists because a free account's naive first ask
#: ("something that runs every morning") spent 16 of 21 rounds failing
#: `operation="create"`, and `read_graph target="handbook" query="write_graph.branch"`
#: answered "no chapter 'branch'" -- there was no worked example anywhere the
#: model could reach.
SPLIT_CHAPTER_ORDER = (
    "branches", "connections", "code_nodes", "workspaces", "delivering",
    "interfaces", "systems",
)
# New handbook content is not part of the historical docstring relocation.
CHAPTER_ORDER = (*SPLIT_CHAPTER_ORDER, "connect", "share-after-publish", "capabilities")

#: The passage a LATER change deliberately DELETED, verbatim.
#:
#: The ratchet below is "relocation, not deletion", and it is right to fail on a
#: deletion. So a deletion that is the POINT of a change is recorded here rather
#: than by re-pinning the baseline fixture — re-pinning is how a preservation
#: check quietly stops preserving anything, and it would also throw away the
#: split's provenance.
#:
#: Written as the PASSAGE rather than a hand-typed word list, because a word list
#: excuses a global deficit in those words wherever it happens (Codex
#: refute-review, P2 #9): every allowance below is derived from this string, and
#: `test_the_split_lost_no_guidance` additionally asserts the passage itself is
#: gone and its distinctive word with it. Unrelated additions and deletions
#: cannot balance out into a pass, because the anchor is the text.
#:
#: 2026-09-26, `openspec/changes/rotate-a-rejected-credential/`: it told the
#: agent to repair a credential the far side had rejected by REMOVING it and
#: depositing it again — the path the founder read as deletion and dismissed
#: three times, leaving a connection dead for ten days. `rotate_http` replaces it
#: in one card, so this had to go rather than sit beside its replacement
#: contradicting it.
#:
#: 2026-09-27: the founder's universe went looking for a "completion hook"
#: because nothing said automation runs never overlap -- which makes a
#: 300-second interval already "rerun when the last run ended". Saying so costs
#: resident text and the served descriptions sit at their 30,000-char ratchet;
#: this sentence paid for it. The create checks it describes still refuse by name.
#:
#: 2026-10-01: telling the agent an ask IS the notification (live: it searched
#: for a notification sender instead) costs resident text at the same ratchet.
#: Two sentences paid for it, each already said elsewhere in the docstring: the
#: run_graph step closes it ("Actually RUNNING it ... via run_graph"), and
#: "File delivery ... not supported here" sits one line above the other.
#:
#: 2026-10-03: publishing became a pending-request ask the person confirms.
#: The old browser-only denial now contradicts that route; only its exact
#: passage is retired, with the absence marker checked below.
#:
#: Each entry is ``(passage, marker)``: the verbatim passage the allowance is
#: derived from, and a word that occurs ONLY in it, so its absence is a cheap,
#: direct check that the passage went rather than merely being rephrased.
REMOVED_PASSAGES = (
    (
        "**If you are ROTATING a key rather than retiring it, carry both into the "
        "new ``connect_http`` ask.**",
        "ROTATING",
    ),
    (
        "It schedules your own workflow using existing creation checks and the "
        "universe's current serving provider.",
        "creation",
    ),
    (
        "The build half of build+run parity (run it afterward with run_graph).",
        "build+run",
    ),
    (
        "Exact file delivery is not implemented.",
        "implemented.",
    ),
    (
        "Publishing to the commons, changing visibility to public, and forking "
        "a foreign shape are NOT available here (they stay in the browser flow);",
        "forking",
    ),
)
DELIBERATELY_REMOVED: Counter = sum(
    (Counter(passage.split()) for passage, _marker in REMOVED_PASSAGES), Counter()
)


def _normalized(text: str) -> str:
    return "\n".join(line.strip() for line in text.splitlines()).rstrip("\n")


def _parameter_descriptions(handle: str) -> list[str]:
    """Every parameter description in the advertised schema, possibly empty."""
    async def _read() -> list[str]:
        for tool in await engine.mcp.list_tools():
            if tool.name == handle:
                schema = tool.parameters if isinstance(tool.parameters, dict) else {}
                return [
                    str(spec["description"])
                    for spec in (schema.get("properties") or {}).values()
                    if isinstance(spec, dict) and spec.get("description")
                ]
        raise AssertionError(f"no served handle named {handle!r}")

    return asyncio.run(_read())


def _resident(handle: str) -> str:
    """The advertised description plus every parameter description, flattened.

    What a served turn reads about ``handle`` without fetching a chapter. Both
    fields, because FastMCP 3.2.0 leaves a docstring's ``Args:`` block in the
    description while 3.4.x (CI, production) moves it into the parameter schema.
    """
    text = " ".join([_description(handle), *_parameter_descriptions(handle)])
    return " ".join(text.split())


def _description(handle: str) -> str:
    async def _read() -> str:
        for tool in await engine.mcp.list_tools():
            if tool.name == handle:
                return tool.description or ""
        raise AssertionError(f"no served handle named {handle!r}")

    return asyncio.run(_read())


# ---------------------------------------------------------------------------
# Nothing was lost
# ---------------------------------------------------------------------------


def _pre_split_word_counts() -> Counter:
    raw = PRE_SPLIT_WORDS.read_bytes()
    digest = hashlib.sha256(raw).hexdigest()
    assert digest == PRE_SPLIT_WORDS_SHA256, (
        "the pre-split word fixture changed; it is the baseline this change is "
        "measured against, so regenerating it needs saying in the PR"
    )
    counts: Counter = Counter()
    for line in raw.decode("utf-8").splitlines():
        # A word from `.split()` can never contain a tab, so a line WITHOUT one is
        # the comment header. A `#` marker would have eaten the guidance's own
        # `#`-prefixed words — six of them, found the first time this ran.
        if "\t" not in line:
            continue
        count, _, word = line.partition("\t")
        counts[word] = int(count)
    return counts


def _source_docstring(name: str = "write_graph") -> str:
    """The RAW docstring from the shipped source, before any MCP layer sees it.

    The relocation this module guards is a SOURCE edit, so prove it against source.
    Reading the advertised description instead made this test a claim about FastMCP:
    on 3.4.x it extracts the `Args:` block into the parameter schema, consuming the
    `Args:` header and turning each parameter name into a schema KEY, so a word check
    over the advertised text reported 17 "lost" words in CI that the split had not
    touched. What FastMCP chooses to relocate is its business; what this change
    relocated is the question.
    """
    import ast

    source = pathlib.Path(engine.__file__).read_text(encoding="utf-8")
    node = next(
        item for item in ast.walk(ast.parse(source))
        if isinstance(item, ast.FunctionDef) and item.name == name
    )
    return ast.get_docstring(node, clean=False) or ""


#: A prose "universe" in the baseline is the same guidance renamed (founder,
#: 2026-10-01: the product is the person's command center;
#: `openspec/changes/rename-universe-to-command-center` C0). Only the bare prose
#: forms move; identifiers such as ``universe_id`` stay in the count untouched.
_RENAMED_PROSE = re.compile(r"^(\(?)universe((?:'s|s)?[).,;:]*)$")


#: Machine spellings C1 renamed in the advertised text (the public names).
_RENAMED_CODE = {"``tiny/<universe>/<slug>``": "``tiny/<command-center-id>/<slug>``"}


def _renamed(counts: Counter) -> Counter:
    out: Counter = Counter()
    for word, count in counts.items():
        if word in _RENAMED_CODE:
            out[_RENAMED_CODE[word]] += count
            continue
        match = _RENAMED_PROSE.match(word)
        if match:
            out[match.group(1) + "command"] += count
            out["center" + match.group(2)] += count
        else:
            out[word] += count
    return out


def test_the_split_lost_no_guidance():
    """The whole safety claim in one assertion: relocation, not deletion.

    Every word the docstring carried before the split still occurs at least as often
    across the docstring that remains plus every chapter. Relocation between them is
    allowed — that is the point — and so is added text; losing any of it is not.

    The one exception is a later change that MEANT to delete a passage. Its
    allowance is DERIVED from `REMOVED_PASSAGES` — so no deficit larger than those
    passages can hide behind it — and this test then anchors the exemption to the
    text: the passage is gone, and so is the word that occurred only in it. A word
    list alone would excuse a global deficit in those words wherever it happened.
    """
    baseline = _pre_split_word_counts()
    before = _renamed(baseline)
    text = _source_docstring() + "".join(
        engine.SERVED_TOOL_CHAPTERS["write_graph"][name] for name in SPLIT_CHAPTER_ORDER
    )
    after = Counter(text.split())
    unexplained = {
        word: (count, after[word])
        for word, count in before.items()
        if after[word] + DELIBERATELY_REMOVED[word] < count
    }
    assert not unexplained, (
        f"guidance words lost in relocation: {sorted(unexplained)[:20]}"
    )
    # The exemption is anchored to the TEXT, not to its words: it only covers a
    # passage that is actually gone.
    flat = " ".join(text.split())
    for passage, marker in REMOVED_PASSAGES:
        assert passage not in flat
        assert marker not in text
    assert sum(baseline.values()) == 4968  # provenance, stated in the fixture header


#: The two shapes a FastMCP version can hand us for the same docstring: 3.2.0 leaves
#: the `Args:` block in `description`, 3.4.x extracts it into the parameter schema
#: (and drops the `<param>:` labels). Both are built from the SAME source docstring
#: and both are asserted, so neither host is the one this module happens to run on.
@pytest.mark.parametrize("placement", ["in_description", "in_schema"])
def test_the_reader_finds_guidance_under_either_fastmcp_placement(monkeypatch, placement):
    """`served_tool_guidance` reads the schema too, not only the description.

    A reader that looks at one field answers "is the agent told this?" differently
    per host — which is how two of these tests went RED in Linux CI while passing on
    Windows. Constructed rather than skipped: the first version of this test
    `pytest.skip`ped on the extracting version, i.e. it went quiet on exactly the
    host (and the production runtime) whose behaviour it was written to cover.
    """
    doc = _source_docstring()
    head, _, args_block = doc.partition("Args:")
    assert args_block, "write_graph lost its Args block"
    if placement == "in_description":
        tool = types.SimpleNamespace(
            name="write_graph", description=doc, parameters={"properties": {}},
        )
    else:
        tool = types.SimpleNamespace(
            name="write_graph",
            description=head,
            parameters={"properties": {
                # 3.4 keys each parameter and puts its prose in `description`,
                # without the `<param>:` label the docstring carried.
                "payload_json": {"description": "Args: " + args_block},
            }},
        )
    others = [
        other for other in asyncio.run(engine.mcp.list_tools()) if other.name != "write_graph"
    ]

    async def _list_tools():
        return [tool, *others]

    monkeypatch.setattr(engine.mcp, "list_tools", _list_tools)
    reachable = engine.served_tool_guidance("write_graph")
    # Wherever the Args block landed, the reader has it.
    assert "Args:" in reachable
    for parameter in ("payload_json", "expected_revision", "idempotency_key"):
        assert parameter in reachable
    # And the chapters are still appended under both placements.
    for name in CHAPTER_ORDER:
        probe = max(
            (line.strip() for line in engine.SERVED_TOOL_CHAPTERS["write_graph"][name]
             .splitlines()), key=len,
        )
        assert probe in reachable


def test_the_chapters_are_still_where_the_index_says_they_were():
    """Relocation is allowed, arbitrary reshuffling is not: order still holds.

    Structural, with no line count: how much of the docstring lands in the
    description versus the parameter schema depends on the FastMCP version
    (3.2.0 keeps `Args:` in the description, 3.4.x extracts it), so a line total
    asserts a different thing on each host — which is how this first went red in
    CI while passing locally.
    """
    description = _description("write_graph")
    at = description.index(INDEX_ANCHOR)
    head, rest = description[:at], description[at:]
    tail = TAIL_ANCHOR + rest.split(TAIL_ANCHOR, 1)[1]
    chapters = engine.SERVED_TOOL_CHAPTERS["write_graph"]
    recomposed = head + "".join(chapters[name] for name in SPLIT_CHAPTER_ORDER) + tail
    # The reconstruction reads in the original order: the operation catalogue
    # before the chapters, the delete/parity tail after them.
    assert recomposed.index('operation="create"') < recomposed.index("CODE NODES")
    assert recomposed.index("CODE NODES") < recomposed.index(TAIL_ANCHOR)
    assert recomposed.index("CODE NODES") < recomposed.index("WORKSPACES.")


def test_served_tool_guidance_answers_for_every_served_handle():
    """One call answers "is the agent told this?" — for all 14, not just the split one."""
    for handle in SERVED_ENGINE_MCP_TOOLS:
        guidance = engine.served_tool_guidance(handle)
        assert _description(handle) in guidance
    with_chapters = engine.served_tool_guidance("write_graph")
    for name in CHAPTER_ORDER:
        assert engine.SERVED_TOOL_CHAPTERS["write_graph"][name] in with_chapters


def test_an_unknown_handle_raises_instead_of_returning_empty():
    """A silent "" would let a test assert reachability for a name that is absent."""
    with pytest.raises(KeyError, match="no served handle"):
        engine.served_tool_guidance("write_graphh")


# ---------------------------------------------------------------------------
# The description really got smaller, and the split is a partition
# ---------------------------------------------------------------------------


def test_moved_chapters_are_gone_from_the_per_round_description():
    """The saving is real: chapter text is not also resident."""
    description = _description("write_graph")
    chapters = engine.SERVED_TOOL_CHAPTERS["write_graph"]
    for name, text in chapters.items():
        # Compare on a distinctive interior line, so this cannot pass merely
        # because indentation differs.
        probe = max(
            (line.strip() for line in text.splitlines()), key=len,
        )
        assert len(probe) > 40
        assert probe not in description, f"{name} is still resident"
    assert len(description) < 12_000, len(description)


def test_the_resident_index_names_every_chapter_and_how_to_fetch_it():
    """A chapter the description does not point at is a chapter nobody fetches."""
    description = _description("write_graph")
    assert INDEX_ANCHOR in description
    for name in engine.SERVED_TOOL_CHAPTERS["write_graph"]:
        assert name in description
    assert 'read_graph target="handbook"' in description


def test_guidance_that_prevents_a_wrong_first_call_stays_resident():
    """The split rule, asserted: absence must cost a fetch, never a bad call."""
    description = _description("write_graph")
    # The manifest key shape: a wrong key is only refused AFTER the attempt.
    assert "io_manifest" in description
    assert "file_bundle" in description
    # The operation catalogue and the no-effect guarantee.
    assert 'operation="create"' in description
    assert "fires NO effect" in description or "NO effect" in description
    # Parameter documentation. Where it LIVES is FastMCP-version dependent (3.2.0
    # leaves `Args:` in the description, 3.4.x extracts it into the schema), so
    # assert it reaches the agent rather than which field carries it.
    reachable = engine.served_tool_guidance("write_graph")
    assert "Args:" in reachable or _parameter_descriptions("write_graph")
    for parameter in ("payload_json", "expected_revision"):
        assert parameter in reachable
    # PR #4000 review: skipping this produces a WRONG effectful call -- a
    # corrupted file written to somebody's repository -- not an absent one, so it
    # came back out of the `connections` chapter.
    assert "NEVER generate base64" in description
    assert "NEVER re-type a file" in description
    assert "422 not valid Base64" in description
    # And it is not ALSO in the chapter: one definition, not two that can diverge.
    chapters = engine.SERVED_TOOL_CHAPTERS["write_graph"]
    assert "NEVER generate base64" not in chapters["connections"]


# ---------------------------------------------------------------------------
# The handbook read contract
# ---------------------------------------------------------------------------


def test_the_index_lists_every_chapter_that_exists():
    payload = json.loads(engine._handbook_read(""))
    assert payload["handbook"] == {
        handle: sorted(chapters)
        for handle, chapters in engine.SERVED_TOOL_CHAPTERS.items()
    }
    assert 'query="<handle>.<chapter>"' in payload["read_one"]


def test_a_chapter_comes_back_verbatim_and_untruncated():
    for name in CHAPTER_ORDER:
        payload = json.loads(engine._handbook_read(f"write_graph.{name}"))
        assert payload["handle"] == "write_graph"
        assert payload["chapter"] == name
        assert payload["text"] == engine.SERVED_TOOL_CHAPTERS["write_graph"][name]


def test_the_bound_read_handle_actually_serves_the_handbook(monkeypatch):
    """END TO END through the real `read_graph`, not the helper behind it.

    PR #4000 review, mutation finding: disabling the `if normalized == "handbook"`
    branch left the whole suite green (210 passed), because every handbook test
    called `_handbook_read` directly. The agent would have been told to fetch
    chapters it could not reach. This test drives the handle the agent drives.
    """
    monkeypatch.setattr(engine, "_binding_error", lambda: None)
    monkeypatch.setattr(engine, "_GRAPH_ID", "u-handbook", raising=False)

    index = json.loads(engine.read_graph(target="handbook"))
    assert sorted(index["handbook"]["write_graph"]) == sorted(CHAPTER_ORDER)

    for name in CHAPTER_ORDER:
        chapter = json.loads(
            engine.read_graph(target="handbook", query=f"write_graph.{name}")
        )
        assert chapter["text"] == engine.SERVED_TOOL_CHAPTERS["write_graph"][name]

    refused = json.loads(engine.read_graph(target="handbook", query="write_graph.nope"))
    assert "no chapter 'nope'" in refused["error"]


def test_the_handbook_route_still_refuses_an_unbound_caller(monkeypatch):
    """`_binding_error()` runs BEFORE the handbook branch; keep it that way."""
    monkeypatch.setattr(engine, "_binding_error", lambda: json.dumps({"error": "unbound"}))
    assert json.loads(engine.read_graph(target="handbook"))["error"] == "unbound"


def test_an_unknown_name_is_refused_and_names_what_is_available():
    unknown_handle = json.loads(engine._handbook_read("read_graph.connections"))
    assert "no handbook for 'read_graph'" in unknown_handle["error"]
    assert "write_graph" in unknown_handle["handbook"]
    unknown_chapter = json.loads(engine._handbook_read("write_graph.secrets"))
    assert "no chapter 'secrets'" in unknown_chapter["error"]
    assert sorted(CHAPTER_ORDER) == unknown_chapter["chapters"]


def test_the_handbook_carries_no_universe_state_and_no_secret():
    """Static text: it cannot leak, and there is nothing to write."""
    for query in ("", "write_graph.connections", "write_graph.code_nodes"):
        payload = engine._handbook_read(query)
        assert "vault://" not in payload
        assert "Bearer " not in payload
    assert "handbook" in engine._PINNED_READ_TARGETS
    # Read-only by construction: the write handle has no handbook target, so the
    # refusal names the targets it does support rather than writing anything.
    answer = engine.write_graph(target="handbook", operation="create")
    assert json.loads(answer)["error"]
    for text in engine.SERVED_TOOL_CHAPTERS["write_graph"].values():
        probe = max((line.strip() for line in text.splitlines()), key=len)
        assert probe not in answer


# ---------------------------------------------------------------------------
# One path for every account, and the public surface is untouched
# ---------------------------------------------------------------------------


def test_the_resident_block_does_not_vary_by_anything():
    """Founder rule: all accounts behave the same. Assembly reads no context."""
    first = [_description(name) for name in SERVED_ENGINE_MCP_TOOLS]
    second = [_description(name) for name in SERVED_ENGINE_MCP_TOOLS]
    assert first == second
    assert engine._handbook_read("") == engine._handbook_read("")


def test_the_public_connector_description_is_untouched_and_uncoupled():
    """Chatbot clients read the public surface; an engine split must not reach it."""
    from tinyassets import universe_server

    public = universe_server.write_graph.__doc__ or ""
    served = engine.write_graph.__doc__ or ""
    # Independent docstrings on independent functions, not derived from each other.
    assert public != served
    assert INDEX_ANCHOR not in public
    assert "handbook" not in public
    # The public manual is still whole: it never carried the engine's chapters,
    # and the relocation did not shrink it (measured 16,623 chars on 2026-09-25).
    assert len(public) > 16_000


def test_the_agent_is_told_a_request_is_the_notification():
    """Live 2026-10-01: asked to send the morning note as a notification, the
    universe searched for a notification sender, found none, and raised a request
    for one. Requests ARE the notification channel; that fact must stay resident."""
    assert "An ask IS the only notification" in _resident("write_graph")


def test_the_agent_is_told_an_ask_is_only_for_what_the_owner_can_grant():
    """Live 2026-10-01: refused a trigger type the engine lacked, the universe
    asked its owner to approve "Enable new-message wakes" -- an approval that
    could not create the missing capability, so the owner approved and nothing
    happened. A platform gap goes to the patch request, never to the owner."""
    resident = _resident("write_graph")
    assert "what the owner can grant or decide" in resident
    assert "a platform gap is a patch request" in resident
