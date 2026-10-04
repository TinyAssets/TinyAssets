"""Execute the actual picker with synthetic DOM/transport; not live UI proof."""

import json
import shutil
import subprocess

import pytest

from tests.test_onboarding_app import _js_function
from tinyassets.onboarding import render_app_html


def ref(name):
    return {"provider_ref": "owned:future-source", "model_id": name}


def catalogue():
    return {
        "version": 1,
        "kind": "advisory_model_options",
        "universe_id": "home-a",
        "choice_authority": "accepted_manifest",
        "binding_state": "serving",
        "preferences": {"generation": 2, "policy": None},
        "sources": [],
        "source_failures": [],
        "unavailable": [],
        "order": [ref("first"), ref("second")],
        "options": [
            {
                "reference": ref(name),
                "in_candidate_catalog": True,
                "freshness": "fresh",
                "reasons": [],
            }
            for name in ("first", "second", "third")
        ],
    }


def test_declared_models_are_not_labelled_verified_available(tmp_path):
    doc = catalogue()
    doc["options"][0]["availability_basis"] = "owner_declared"
    doc["options"][1]["availability_basis"] = "executor_default"
    result = run_picker(tmp_path, "", doc=doc)
    rows = result["ui"]["model-inventory"]["children"]
    assert "availability not verified" in rows[0]["text"]
    assert "full model list not yet verified" in rows[1]["text"]


@pytest.mark.parametrize("reason,label", [
    ("catalogue_refresh_pending", "checking…"),
    ("native_catalogue_unavailable", "couldn't refresh"),
])
def test_catalogue_refresh_is_visible_without_disabling_known_choices(tmp_path, reason, label):
    doc = catalogue()
    doc["source_failures"] = [{"provider_ref": "owned:future-source",
                               "reasons": [{"reason": reason}]}]
    doc["options"][2].update(in_candidate_catalog=False, reasons=[{"reason": reason}])
    result = run_picker(tmp_path, "", doc)
    rows = result["ui"]["model-menu"]["children"]
    assert any(label in row["text"] for row in rows)
    assert not any("Needs access" in row["text"] for row in rows)
    assert any("first" in row["text"] and not row["disabled"] for row in rows)
    assert any("third" in row["text"] and row["disabled"] for row in rows)


def test_choosing_closes_the_dropdown_and_reuses_the_fresh_catalogue(tmp_path):
    """One click applies and closes; reopening does not re-read the catalogue."""
    result = run_picker(tmp_path, """
      await ModelPicker.menuOpen();
      await ModelPicker.choose(ModelPicker.key(""" + json.dumps(ref("first")) + """));
      if(!$("model-menu").hidden) throw new Error('the list stayed open after a choice');
      Owner.getModelOptions=async()=>{throw new Error('unnecessary refresh');};
      await ModelPicker.menuOpen();
    """)
    assert result["ui"]["model-menu"]["hidden"] is False
    assert not result["busy"]
    # The choice went to the SAVED default, not to a tab-local override.
    assert result["choice"] is None
    assert result["requests"][0]["body"]["policy"]["saved_default"] == ref("first")
    assert result["ui"]["btn-models"]["text"].startswith("Model: ")


def test_an_expired_catalogue_offers_nothing_to_pick(tmp_path):
    result = run_picker(tmp_path, """
      expire();Owner.getModelOptions=async()=>{throw new Error('offline');};
      await ModelPicker.menuOpen();
    """)
    assert result["stale"] and result["choice"] is None
    # Every row is inert and the list says why, rather than offering a choice it
    # cannot honour.
    rows = result["ui"]["model-menu"]["children"]
    choices = [row for row in rows if row["cls"] == "model-menu-item"]
    assert choices and all(row["disabled"] for row in choices)
    assert any("needs a refresh" in row["text"] for row in rows)
    assert result["requests"] == [] and result["writes"] == []


def test_saved_unavailable_choice_remains_visible_but_not_applicable(tmp_path):
    doc = catalogue()
    doc["options"][0]["reasons"] = [{"reason": "source_revoked"}]
    doc["preferences"]["policy"] = {
        "version": 1, "mode": "explicit", "saved_default": ref("first"), "fallbacks": [],
    }
    result = run_picker(tmp_path, "", doc)
    rows = result["ui"]["model-menu"]["children"]
    # It is still shown as the current choice, ticked, and it is not pickable.
    current = next(row for row in rows if row["checked"] == "true")
    assert "first" in current["text"], "the saved choice must still read as current"
    assert current["disabled"] is True, "...and must not be offered as a fresh pick"
    assert "unavailable" in current["text"]
    # ...and it also appears under "needs access" with its reason.
    assert any("Needs access" in row["text"] for row in rows)
    assert any("source revoked" in row["text"] for row in rows)


def test_one_choice_is_the_default_and_creates_no_tab_local_override(tmp_path):
    """The founder's whole ask: clicking a model IS setting it. Nothing else to do.

    "Use in this chat" is gone, so there is no way for the UI to leave a tab-local
    override behind -- `modelChoiceForNextTurn` stays null and every turn reads the
    saved preference, which is what the server already did for a null choice.
    """
    policy = {"version": 1, "mode": "explicit", "saved_default": ref("second"), "fallbacks": []}
    result = run_picker(
        tmp_path,
        "await ModelPicker.menuOpen();"
        + "await ModelPicker.choose(ModelPicker.key(" + json.dumps(ref("second")) + "));",
        response={"universe_id": "home-a", "generation": 3, "policy": policy, "updated_at": "now"},
    )
    assert result["choice"] is None, "a dropdown click must not create a tab-local override"
    assert result["snapshot"]["preferences"]["policy"] == policy
    assert "second" in result["ui"]["btn-models"]["text"]
    # Exactly one write, and it is the same preferences POST the old "Set as
    # default" button made -- the server contract is unchanged.
    assert len(result["requests"]) == 1
    assert result["requests"][0]["method"] == "POST"
    assert result["requests"][0]["body"]["policy"]["saved_default"] == ref("second")


def test_a_refused_save_leaves_the_saved_default_alone_and_says_so(tmp_path):
    result = run_picker(
        tmp_path,
        "await ModelPicker.menuOpen();"
        + "await ModelPicker.choose(ModelPicker.key(" + json.dumps(ref("first")) + "));",
        response={"error": "model_preferences_conflict"},
    )
    # The conflict did not become a silent success: the snapshot keeps the
    # generation it read, and the status line carries the reason.
    assert result["snapshot"]["preferences"]["generation"] == 2
    assert "changed elsewhere" in result["ui"]["model-status"]["text"]
    assert result["choice"] is None


def test_sign_in_hint_warns_without_disabling_manual_choice(tmp_path):
    doc = catalogue()
    doc["options"][0]["labels"] = ["recent_sign_in_failure"]
    result = run_picker(tmp_path, "", doc=doc)
    rows = result["ui"]["model-inventory"]["children"]
    assert "sign-in failure" in rows[0]["text"]
    assert "reconnect" in rows[0]["text"]
    # A sign-in warning is a note in the inventory, not a reason to withhold the
    # model from the list: it is still pickable so the owner can retry it.
    rows = result["ui"]["model-menu"]["children"]
    pickable = [row for row in rows if row["cls"] == "model-menu-item" and not row["disabled"]]
    assert any("first" in row["text"] for row in pickable)
    assert result["requests"] == [] and result["writes"] == []


def test_enumerated_models_and_provider_default_have_distinct_truthful_labels(tmp_path):
    doc = catalogue()
    doc["options"][0]["availability_basis"] = "executor_enumerated"
    doc["options"][1]["availability_basis"] = "executor_default"
    result = run_picker(tmp_path, "", doc=doc)
    rows = result["ui"]["model-inventory"]["children"]
    assert "listed by the connected provider" in rows[0]["text"]
    assert "provider chooses the model" in rows[1]["text"]
    assert "full model list not yet verified" not in rows[1]["text"]


def run_picker(tmp_path, steps, doc=None, response=None):
    html, _ = render_app_html()
    picker = html[html.index("  const ModelPicker={") : html.index("  function captureTurnOptions")]
    program = r"""
const elements=new Map();
class Element {
  constructor(){this.children=[];this.textContent="";this.value="";this.disabled=false;this.events={};
    this.open=false;this.hidden=false;this.className="";
    // The dropdown groups its rows by class and walks them for keyboard focus, so
    // the shim models classList rather than pretending it away.
    this.classList={contains:name=>String(this.className||"").split(" ").includes(name),
                    add:name=>{this.className=(this.className+" "+name).trim();},
                    remove:name=>{this.className=String(this.className||"").split(" ")
                      .filter(n=>n!==name).join(" ");}};}
  replaceChildren(){this.children=[];}
  appendChild(child){this.children.push(child);child.parent=this;return child;}
  setAttribute(key,value){this[key]=value;}
  getAttribute(key){return this[key];}
  addEventListener(key,fn){this.events[key]=fn;}
  // Real containment, because the outside-click close asks the question honestly.
  contains(node){
    for(let at=node;at;at=at.parent) if(at===this) return true;
    return false;
  }
  click(){if(this.events.click)this.events.click();}
  focus(){focused=this;}
  showModal(){this.open=true;}
  close(){this.open=false;if(this.events.close)this.events.close();}
}
let focused=null;
const $=id=>{if(!elements.has(id))elements.set(id,new Element());return elements.get(id);};
// A document-level listener is how a dropdown closes on an outside click, so the
// shim carries one instead of the picker avoiding it.
const documentEvents={};
const document={createElement:()=>new Element(),
  addEventListener:(key,fn)=>{documentEvents[key]=fn;},
  get activeElement(){return focused;}};
let modelChoiceForNextTurn=null,requests=[],expired=false,refreshed=0,connects=0;
let confirmed=true,confirmations=[],writes=[];
const confirm=text=>{confirmations.push(text);return confirmed;};
const setTimeout=fn=>{globalThis.expire=fn;return 1;},clearTimeout=()=>{};
const ensureFreshToken=async()=>{refreshed++;},authHeaders=()=>({Authorization:"test fixture"});
const sessionExpired=()=>{expired=true;ModelPicker.reset();};
const openConnectRequest=()=>{connects++;};
let doc=__DOC__,response=__RESPONSE__;
// The owner door (reads). This harness has ONE fake server, `MCP` below, so
// the owner door's reads are answered by it: a read the page makes is
// recorded and stubbed exactly where the scenario already records it.
const Owner={
  read(a){return MCP.callTool("read_graph",a,{idempotent:true});},
  status(a){return MCP.callTool("get_status",a||{},{idempotent:true});},
  getStatus(...x){return MCP.getStatus(...x);},
  getConversation(...x){return MCP.getConversation(...x);},
  readConversationChunk(...x){return MCP.readConversationChunk(...x);},
  getModelOptions(...x){return MCP.getModelOptions(...x);},
  listRequests(...x){return MCP.listRequests(...x);}};
const MCP={};
Owner.getModelOptions=async()=>JSON.parse(JSON.stringify(doc));
MCP.callTool=async(name,args)=>{
 writes.push({name,args});
 if(name==="read_graph")
  return {binding:{agent_binding_id:"binding-a",revision:3,status:"configured"}};
 return args.operation==="bind_serving_provider"
  ?{status:"ready",agent_binding:{agent_binding_id:"binding-a",revision:3}}
  :{status:"serving",agent_binding:{agent_binding_id:"binding-a",revision:4}};
};
let fetch=async(url,options)=>{
 const body=JSON.parse(options.body);
 requests.push({url,method:options.method,body});
 // With no fixed reply the fake server does what the real one does: saves the
 // policy it was sent and answers with it and the next generation.
 const reply=response||{universe_id:"home-a",generation:body.expected_generation+1,
   policy:body.policy,updated_at:"2026-09-27T00:00:00Z"};
 return {ok:!reply.error,status:reply.error?409:200,json:async()=>reply};
};
__FUNCTIONS__
(async()=>{
 ModelPicker.init();await ModelPicker.menuOpen();await ModelPicker.open();
 __STEPS__
 const ids=["btn-models","model-next","model-status","model-actual","model-menu",
   "model-fallbacks","model-inventory","model-saved"];
 const rowText=c=>c.children.length
   ? c.children.map(g=>g.textContent).join("").trim() : c.textContent;
 const ui=Object.fromEntries(ids.map(id=>[id,{text:$(id).textContent,disabled:$(id).disabled,
   hidden:$(id).hidden,
   children:$(id).children.map(c=>({text:rowText(c),value:c.value,disabled:c.disabled,
     cls:c.className,checked:c["aria-checked"]||""}))}]));
 console.log(JSON.stringify({choice:modelChoiceForNextTurn,draft:ModelPicker.draft,
   snapshot:ModelPicker.snapshot,stale:ModelPicker.stale,busy:ModelPicker.busy,requests,
   expired,refreshed,connects,writes,confirmations,recovery:ModelPicker.recovery,dialogOpen:$("model-dialog").open,
   focusReturned:focused===$("btn-models"),ui}));
})().catch(err=>{console.error(err);process.exitCode=1;});
"""
    program = (
        program.replace("__DOC__", json.dumps(catalogue() if doc is None else doc))
        .replace("__RESPONSE__", json.dumps(response))
        .replace("__FUNCTIONS__", _js_function(html, "copyModelChoice") + picker)
        .replace("__STEPS__", steps)
    )
    script = tmp_path / "picker.js"
    script.write_text(program, encoding="utf-8")
    node = shutil.which("node")
    assert node, "Node is required to execute the app picker"
    result = subprocess.run(
        [node, str(script)], capture_output=True, text=True, encoding="utf-8", timeout=20
    )
    assert result.returncode == 0, result.stderr
    return json.loads(result.stdout)


def choose(name):
    return "ModelPicker.select(ModelPicker.key(" + json.dumps(ref(name)) + "));"


def add(name):
    return "await ModelPicker.addFallback(ModelPicker.key(" + json.dumps(ref(name)) + "));"


def pick(name):
    """Apply a model the way a user does: click its row in the dropdown."""
    return "await ModelPicker.choose(ModelPicker.key(" + json.dumps(ref(name)) + "));"


def test_selection_order_is_copied_and_actual_receipt_is_separate(tmp_path):
    result = run_picker(
        tmp_path,
        choose("first")
        + add("second")
        + add("third")
        + "await ModelPicker.move(1,-1);await ModelPicker.menuOpen();"
        + pick("first")
        + 'ModelPicker.draft.saved_default.model_id="changed after applying";'
        + 'ModelPicker.observe("Answered by another source, reported model");',
    )
    # What went to the server is a COPY: editing the draft afterwards cannot
    # rewrite the request that already left.
    body = result["requests"][-1]["body"]["policy"]
    assert body["saved_default"] == ref("first")
    assert body["fallbacks"] == [ref("third"), ref("second")]
    # The answering-model receipt is separate from the choice, and stays separate.
    assert "reported model" in result["ui"]["model-actual"]["text"]
    # That the label follows what the SERVER confirmed is pinned by
    # test_one_choice_is_the_default_and_creates_no_tab_local_override.
    assert result["ui"]["btn-models"]["text"].startswith("Model: ")


def test_configured_source_claims_are_visible_without_disabling_permitted_choice(tmp_path):
    doc = catalogue()
    doc["options"][0]["availability_basis"] = "owner_configured_contract"
    result = run_picker(tmp_path, choose("first"), doc)
    assert result["draft"]["saved_default"] == ref("first")
    text = result["ui"]["model-inventory"]["children"][0]["text"]
    assert "availability, privacy and charges" in text and "not independently verified" in text
    other = result["ui"]["model-inventory"]["children"][1]["text"]
    assert "not independently verified" not in other


def test_remove_last_fallback_preserves_explicit_empty_list(tmp_path):
    result = run_picker(
        tmp_path, choose("first") + add("second") + "await ModelPicker.move(0,0);"
    )
    # Removing the last fallback leaves an EXPLICIT choice with an empty list,
    # not a silent fall back to Automatic.
    assert result["draft"]["fallbacks"] == []
    assert result["draft"]["mode"] == "explicit"
    # ...and that is what was saved.
    assert result["requests"][-1]["body"]["policy"]["fallbacks"] == []
    assert result["requests"][-1]["body"]["policy"]["mode"] == "explicit"


def test_duplicates_primary_and_repeated_fallback_are_not_added(tmp_path):
    result = run_picker(tmp_path, choose("first") + add("first") + add("second") + add("second"))
    assert result["draft"]["fallbacks"] == [ref("second")]
    # Refused edits send nothing: one save, for the one real addition.
    assert len(result["requests"]) == 1


def test_automatic_replaces_the_whole_current_order(tmp_path):
    """Picking Automatic is not "clear the default" - it replaces the whole order.

    The "saved default clears the override" half of this test was DELETED: there is
    no tab-local override to clear now that one choice is the default.
    """
    result = run_picker(
        tmp_path, choose("first") + add("second") + 'ModelPicker.select("");'
    )
    assert result["draft"] == {
        "version": 2,
        "mode": "automatic",
        "saved_default": None,
        "fallbacks": [],
        # Going back to Automatic replaces the ORDER, not the saved per-model
        # effort levels; there were none set here, so the map is empty.
        "efforts": [],
    }
    # ...and choosing it from the dropdown saves exactly that.
    applied = run_picker(tmp_path, choose("first") + add("second")
                         + 'await ModelPicker.menuOpen();await ModelPicker.choose("");')
    assert applied["requests"][-1]["body"]["policy"]["mode"] == "automatic"
    assert applied["requests"][-1]["body"]["policy"]["fallbacks"] == []


@pytest.mark.parametrize(
    "failure", ["source_not_accepted", "outside_accepted_model_scope", "cost_exceeds_cap"]
)
def test_unavailable_model_visible_but_cannot_be_selected_or_authorize_cost(tmp_path, failure):
    doc = catalogue()
    doc["options"][0]["reasons"] = [{"reason": failure}]
    doc["options"][0]["in_candidate_catalog"] = False
    result = run_picker(tmp_path, choose("first"), doc)
    assert result["draft"]["mode"] == "automatic"
    # It is not among the rows a click can apply...
    rows = result["ui"]["model-menu"]["children"]
    pickable = [row for row in rows
                if row["cls"] == "model-menu-item" and not row["disabled"]]
    assert all("first" not in row["text"] for row in pickable)
    # ...it is listed under "needs access" with its reason, and the inventory
    # keeps the same reason.
    assert any(failure.replace("_", " ") in row["text"] for row in rows)
    assert failure.replace("_", " ") in result["ui"]["model-inventory"]["children"][0]["text"]
    assert result["requests"] == []


def test_complete_catalogue_and_opaque_labels_are_preserved(tmp_path):
    doc = catalogue()
    doc["options"] *= 24
    doc["options"][0] = {**doc["options"][0], "reference": ref('<img onerror="bad"> / モデル')}
    result = run_picker(tmp_path, "", doc)
    rows = result["ui"]["model-menu"]["children"]
    choices = [row for row in rows if row["cls"] == "model-menu-item"]
    # Automatic plus every one of the models: nothing is dropped to keep the list
    # short. Derived from the document, never a literal count.
    assert len(choices) == 1 + len(doc["options"])
    # An opaque label is carried as TEXT, never parsed.
    assert any('<img onerror="bad">' in row["text"] for row in choices)


@pytest.mark.parametrize("state", ["legacy_single_provider", "none"])
def test_unpowered_or_legacy_can_still_choose_automatic_but_not_a_named_model(tmp_path, state):
    """Without accepted-manifest authority Automatic saves and a named model does not."""
    doc = catalogue()
    doc["choice_authority"] = state
    saved = run_picker(tmp_path, 'await ModelPicker.menuOpen();await ModelPicker.choose("");',
                       doc=doc)
    assert saved["requests"][0]["body"]["policy"]["mode"] == "automatic"
    named = run_picker(
        tmp_path,
        "await ModelPicker.menuOpen();await ModelPicker.choose(ModelPicker.key("
        + json.dumps(ref("first")) + "));",
        doc=doc,
    )
    # Refused, and said where the founder is looking rather than swallowed.
    assert named["requests"] == []
    assert any("needs model access" in row["text"]
               for row in named["ui"]["model-menu"]["children"])


def test_save_exact_home_and_generation_never_grants_or_silently_switches(tmp_path):
    result = run_picker(tmp_path, choose("first") + "await ModelPicker.save();")
    assert result["requests"] == [
        {
            "url": "/app/models/preferences?universe_id=home-a",
            "method": "POST",
            "body": {
                "expected_generation": 2,
                "policy": {
                    "version": 2,
                    "mode": "explicit",
                    "saved_default": ref("first"),
                    "fallbacks": [],
                    "efforts": [],
                },
            },
        }
    ]
    assert result["choice"] is None
    assert result["snapshot"]["preferences"]["generation"] == 3
    # A confirmed save leaves the dialog editable against the new generation.
    assert not result["stale"] and not result["busy"]


@pytest.mark.parametrize("error", ["model_preferences_conflict", "model_preference_home_changed"])
def test_save_refusal_is_not_retried_and_settings_are_not_claimed_saved(tmp_path, error):
    result = run_picker(
        tmp_path, "await ModelPicker.save();await ModelPicker.save();", response={"error": error}
    )
    assert len(result["requests"]) == 1
    assert result["snapshot"]["preferences"]["generation"] == 2
    assert result["stale"]
    assert "saved." not in result["ui"]["model-status"]["text"]


def test_ambiguous_save_requires_read_not_automatic_replay(tmp_path):
    result = run_picker(
        tmp_path,
        """
      fetch=async()=>{requests.push({});throw new Error("network");};
      await ModelPicker.save();await ModelPicker.save();
    """,
    )
    assert len(result["requests"]) == 1
    assert "Could not confirm" in result["ui"]["model-status"]["text"]


def explicit(default, fallbacks=()):
    return {"version": 1, "mode": "explicit", "saved_default": ref(default),
            "fallbacks": [ref(name) for name in fallbacks]}


def test_adding_a_fallback_in_the_dialog_saves_it(tmp_path):
    """The live bug: the dialog showed a fallback the server never had.

    Adding one is the whole action - there is no Save button - so the add itself
    must POST the order, and the dialog must stay open afterwards.
    """
    doc = catalogue()
    doc["preferences"]["policy"] = explicit("first")
    result = run_picker(tmp_path, add("second"), doc)
    assert result["requests"] == [{
        "url": "/app/models/preferences?universe_id=home-a",
        "method": "POST",
        "body": {"expected_generation": 2, "policy": explicit("first", ["second"])},
    }]
    assert result["snapshot"]["preferences"]["policy"] == explicit("first", ["second"])
    assert result["dialogOpen"] is True, "a save from the dialog must not close it"
    shown = result["ui"]["model-fallbacks"]["children"]
    assert [row["text"].split("Move up")[0] for row in shown] == ["owned:future-source · second"]
    assert "saved" in result["ui"]["model-status"]["text"]


def test_two_dialog_edits_in_a_row_both_save(tmp_path):
    """The first save must not leave the dialog stale and swallow the second edit."""
    doc = catalogue()
    doc["preferences"]["policy"] = explicit("first")
    result = run_picker(tmp_path, add("second") + add("third")
                        + "await ModelPicker.move(0,1);await ModelPicker.move(1,0);", doc)
    sent = [(r["body"]["expected_generation"], r["body"]["policy"]["fallbacks"])
            for r in result["requests"]]
    assert sent == [
        (2, [ref("second")]),
        (3, [ref("second"), ref("third")]),
        (4, [ref("third"), ref("second")]),
        (5, [ref("third")]),
    ]
    assert result["snapshot"]["preferences"]["generation"] == 6
    assert result["snapshot"]["preferences"]["policy"] == explicit("first", ["third"])
    assert result["draft"] == explicit("first", ["third"])
    assert result["dialogOpen"] is True and not result["stale"]


@pytest.mark.parametrize("failure", ["conflict", "network"])
def test_an_unsaved_fallback_is_never_left_on_screen(tmp_path, failure):
    doc = catalogue()
    doc["preferences"]["policy"] = explicit("first")
    steps = add("second")
    if failure == "network":
        steps = 'fetch=async()=>{requests.push({});throw new Error("network");};' + steps
    result = run_picker(
        tmp_path, steps, doc,
        response={"error": "model_preferences_conflict"} if failure == "conflict" else None,
    )
    assert len(result["requests"]) == 1
    # Nothing the server may not have is shown, and the status says it did not save.
    assert result["ui"]["model-fallbacks"]["children"] == []
    assert result["stale"], "no further edit until a refresh reads what is saved"
    status = result["ui"]["model-status"]["text"]
    assert "Not saved" in status or "Could not confirm" in status
    assert result["snapshot"]["preferences"]["generation"] == 2


def test_refresh_failure_retains_labelled_stale_rows_and_blocks_changes(tmp_path):
    result = run_picker(
        tmp_path,
        choose("first")
        + """
      Owner.getModelOptions=async()=>{throw new Error("offline");};
      await ModelPicker.refresh();await ModelPicker.menuOpen();
    """
        + pick("first"),
    )
    assert result["choice"] is None and result["requests"] == []
    assert "stale" in result["ui"]["model-inventory"]["children"][0]["text"]
    choices = [row for row in result["ui"]["model-menu"]["children"]
               if row["cls"] == "model-menu-item"]
    assert choices and all(row["disabled"] for row in choices), (
        "a stale list must offer nothing to apply")


# test_expiry_disables_apply_without_altering_captured_choice was DELETED rather
# than converted: its whole subject was the tab-local "Use in this chat" choice,
# which no longer exists. Expiry blocking an apply is covered by
# test_an_expired_catalogue_offers_nothing_to_pick above.


def test_late_refresh_after_signout_cannot_repopulate_dialog(tmp_path):
    result = run_picker(
        tmp_path,
        """
      let finish;Owner.getModelOptions=()=>new Promise(resolve=>{finish=resolve;});
      const pending=ModelPicker.refresh();ModelPicker.reset();finish(doc);await pending;
    """,
    )
    assert result["snapshot"] is None and result["choice"] is None
    assert not result["dialogOpen"] and not result["busy"]


def test_refresh_home_change_clears_old_current_choice(tmp_path):
    result = run_picker(
        tmp_path,
        choose("first") + "doc.universe_id='home-b';await ModelPicker.refresh();",
    )
    # The previous home's draft does not carry into the new one.
    assert result["choice"] is None
    assert result["draft"]["mode"] == "automatic"
    assert result["snapshot"]["universe_id"] == "home-b"


def test_native_dialog_close_returns_focus_and_connection_is_real_action(tmp_path):
    result = run_picker(
        tmp_path, '$("btn-model-close").events.click();$("btn-model-connect").events.click();'
    )
    # Document's capture close listener owns the command-center handoff;
    # the isolated picker must no longer restore its invoking button.
    assert not result["focusReturned"]
    assert not result["dialogOpen"] and result["connects"] == 1
    html, _ = render_app_html()
    # The access sheet survives as a sheet; the BAR BUTTON now controls the
    # dropdown, which is the founder's "just a list dropdown menu".
    assert '<dialog id="model-dialog"' in html
    assert 'aria-controls="model-menu"' in html
    assert 'aria-controls="model-dialog"' not in html


def test_delayed_save_json_cannot_touch_another_login_snapshot(tmp_path):
    result = run_picker(
        tmp_path,
        """
      let finish;
      fetch=async()=>({ok:true,status:200,json:()=>new Promise(resolve=>{finish=resolve;})});
      const pending=ModelPicker.save();
      while(!finish) await Promise.resolve();
      ModelPicker.reset();doc.universe_id="home-b";await ModelPicker.refresh();
      finish(response);await pending;
    """,
    )
    assert result["snapshot"]["universe_id"] == "home-b"
    assert result["snapshot"]["preferences"]["generation"] == 2
    assert not result["stale"] and not result["busy"]


def test_failed_first_read_does_not_invent_saved_automatic_default(tmp_path):
    result = run_picker(tmp_path, "", doc={"error": "model_options_unavailable"})
    assert result["snapshot"] is None
    assert result["ui"]["model-saved"]["text"] == "Saved default: Not loaded"
    # The list offers nothing rather than an invented "Automatic" to apply.
    choices = [row for row in result["ui"]["model-menu"]["children"]
               if row["cls"] == "model-menu-item"]
    assert all(row["disabled"] for row in choices)
    assert result["requests"] == []


def test_successful_read_without_saved_policy_truthfully_shows_automatic(tmp_path):
    result = run_picker(tmp_path, "")
    assert result["snapshot"]["preferences"]["policy"] is None
    assert result["ui"]["model-saved"]["text"] == "Saved default: Automatic"


def access_catalogue(*, legacy=False, native=False):
    doc = catalogue()
    doc["binding"] = {"id": "binding-a", "revision": 2}
    doc["accepted_model_access"] = {}
    source = {
        "provider_ref": ref("first")["provider_ref"],
        "bind_key": "server-key",
        "access_method": "subscription_cli" if native else "api_key_http",
        "accepted": False,
    }
    doc["sources"] = [source]
    doc["legacy_source"] = {**source, "model_id": "" if native else "old-fixed"} if legacy else None
    if legacy:
        doc["choice_authority"] = "legacy_single_provider"
    for row in doc["options"]:
        row["reasons"] = [{"reason": "source_not_accepted"}]
        row["in_candidate_catalog"] = False
    if native:
        doc["options"] = [doc["options"][0]]
        doc["options"][0]["reference"]["model_id"] = ""
    return doc


def test_access_cancel_has_no_write_or_preference_change(tmp_path):
    result = run_picker(
        tmp_path,
        "confirmed=false;await ModelPicker.allowAccess(doc.sources[0]);",
        access_catalogue(),
    )
    assert result["writes"] == [] and result["requests"] == [] and result["choice"] is None
    assert "3 compatible" in result["confirmations"][0]


@pytest.mark.parametrize("scope,ids", [("explicit", ["future-model"]), ("discovered", [])])
def test_native_setup_uses_named_or_discovered_scope_without_changing_preference(
    tmp_path, scope, ids
):
    doc = access_catalogue(legacy=True, native=True)
    doc["sources"][0]["enumeration"] = "supported"
    steps = (
        "await ModelPicker.allowNativeAccess(doc.sources[0],"
        + json.dumps(scope)
        + ","
        + json.dumps(ids)
        + ");"
    )
    result = run_picker(tmp_path, steps, doc)
    assert len(result["writes"]) == 2
    payload = json.loads(result["writes"][0]["args"]["payload_json"])
    assert payload["model_access"]["server-key"] == {
        "model_scope": scope,
        "model_ids": ["", *ids] if scope == "explicit" else [],
        "cost_caps": None,
    }
    assert result["requests"] == [] and result["choice"] is None
    assert "unverified" in result["confirmations"][0]


def test_native_setup_refuses_unknown_discovery_and_cancelled_access(tmp_path):
    doc = access_catalogue(legacy=True, native=True)
    result = run_picker(
        tmp_path, "await ModelPicker.allowNativeAccess(doc.sources[0],'discovered',[]);", doc
    )
    assert result["writes"] == [] and result["confirmations"] == []
    result = run_picker(
        tmp_path,
        "confirmed=false;"
        "await ModelPicker.allowNativeAccess(doc.sources[0],'explicit',['future']);",
        doc,
    )
    assert result["writes"] == [] and result["requests"] == []


def test_native_scope_change_preserves_other_sources_and_cost_caps(tmp_path):
    doc = access_catalogue(native=True)
    doc["sources"][0]["enumeration"] = "supported"
    old = {
        "server-key": {"model_scope": "explicit", "model_ids": ["", "old"], "cost_caps": None},
        "other": {
            "model_scope": "explicit",
            "model_ids": ["keep"],
            "cost_caps": {"input_million_tokens_usd": 20},
        },
    }
    doc["accepted_model_access"] = old
    result = run_picker(
        tmp_path, "await ModelPicker.allowNativeAccess(doc.sources[0],'discovered',[]);", doc
    )
    access = json.loads(result["writes"][0]["args"]["payload_json"])["model_access"]
    assert access["other"] == old["other"]
    assert access["server-key"] == {"model_scope": "discovered", "model_ids": [], "cost_caps": None}
    assert "replaces" in result["confirmations"][0]


def test_native_input_survives_refresh_and_clears_on_home_change(tmp_path):
    doc = access_catalogue(legacy=True, native=True)
    doc["sources"][0]["enumeration"] = "supported"
    result = run_picker(tmp_path, """
      let controls=$('model-access-sources').children[0].children;
      const mode=controls[1],input=controls[2];
      mode.value='named';mode.events.change();
      if(input.hidden) throw new Error('named input hidden');
      input.value='model-a\\nmodel-b';input.events.input();
      await ModelPicker.refresh();
      controls=$('model-access-sources').children[0].children;
      if(controls[1].value!=='named'||controls[2].value!==input.value)
        throw new Error('draft lost on refresh');
      doc.universe_id='home-b';await ModelPicker.refresh();
      controls=$('model-access-sources').children[0].children;
      if(controls[2].value) throw new Error('draft crossed universe');
    """, doc)
    assert result["writes"] == []


@pytest.mark.parametrize("ids", [[""], ["bad\nname"], [" leading"], ["x" * 201], ["same", "same"]])
def test_native_setup_rejects_invalid_or_duplicate_ids_before_write(tmp_path, ids):
    result = run_picker(
        tmp_path,
        "await ModelPicker.allowNativeAccess(doc.sources[0],'explicit'," + json.dumps(ids) + ");",
        access_catalogue(legacy=True, native=True),
    )
    assert result["writes"] == [] and result["confirmations"] == []


@pytest.mark.parametrize("native", [False, True])
def test_confirmed_access_uses_server_discriminator_and_returned_revision(tmp_path, native):
    result = run_picker(
        tmp_path,
        "await ModelPicker.allowAccess(doc.sources[0]);",
        access_catalogue(legacy=True, native=native),
    )
    bind, enable = result["writes"]
    args = bind["args"]
    assert args["target"] == "agent_binding" and args["graph_id"] == "home-a"
    assert args["expected_revision"] == 2 and args["agent_binding_id"] == "binding-a"
    assert json.loads(args["payload_json"]) == {
        "provider": "server-key",
        "model_access": {
            "server-key": {
                "model_scope": "explicit" if native else "discovered",
                "model_ids": [""] if native else [],
                "cost_caps": None,
            }
        },
    }
    assert enable["args"]["operation"] == "set_serving"
    assert enable["args"]["expected_revision"] == 3
    assert json.loads(enable["args"]["payload_json"]) == {"enabled": True}
    assert result["requests"] == [] and result["choice"] is None


def test_expansion_preserves_existing_spending_and_all_other_members(tmp_path):
    doc = access_catalogue()
    old = {
        "server-key": {
            "model_scope": "explicit",
            "model_ids": ["old"],
            "cost_caps": {"input_million_tokens_usd": 1250000},
        },
        "another-key": {"model_scope": "explicit", "model_ids": ["keep"], "cost_caps": None},
    }
    doc["accepted_model_access"] = old
    result = run_picker(tmp_path, "await ModelPicker.allowAccess(doc.sources[0]);", doc)
    changed = json.loads(result["writes"][0]["args"]["payload_json"])["model_access"]
    assert changed["another-key"] == old["another-key"]
    assert changed["server-key"]["cost_caps"] == old["server-key"]["cost_caps"]
    assert changed["server-key"]["model_scope"] == "discovered"


@pytest.mark.parametrize(
    "reason", ["cost_exceeds_cap", "source_revoked", "engine_tools_unavailable"]
)
def test_zero_eligible_models_cannot_begin_access_conversion(tmp_path, reason):
    doc = access_catalogue(legacy=True)
    for row in doc["options"]:
        row["reasons"].append({"reason": reason})
    result = run_picker(tmp_path, "await ModelPicker.allowAccess(doc.sources[0]);", doc)
    assert result["writes"] == [] and result["confirmations"] == []


def test_legacy_conversion_cannot_silently_replace_another_source(tmp_path):
    doc = access_catalogue(legacy=True)
    doc["legacy_source"]["provider_ref"] = "a-different-current-source"
    result = run_picker(tmp_path, "await ModelPicker.allowAccess(doc.sources[0]);", doc)
    assert result["writes"] == []


def test_ambiguous_binding_is_not_retried_or_followed_by_enable(tmp_path):
    result = run_picker(
        tmp_path,
        """
      MCP.callTool=async(name,args)=>{writes.push({name,args});throw new Error("network");};
      await ModelPicker.allowAccess(doc.sources[0]);await ModelPicker.allowAccess(doc.sources[0]);
    """,
        access_catalogue(legacy=True),
    )
    assert len(result["writes"]) == 1 and result["stale"]
    assert result["recovery"] is None


def test_failed_reconnect_has_explicit_fenced_legacy_restore(tmp_path):
    result = run_picker(
        tmp_path,
        """
      const original=MCP.callTool;
      MCP.callTool=async(name,args)=>{
        if(args.operation==="set_serving"&&writes.length===1){
          writes.push({name,args});return {error:"held"};
        }
        return original(name,args);
      };
      await ModelPicker.allowAccess(doc.sources[0]);
      if(!ModelPicker.recovery) throw new Error("missing recovery");
      await ModelPicker.restoreAccess();
    """,
        access_catalogue(legacy=True),
    )
    assert len(result["confirmations"]) == 2
    assert [w["args"].get("operation", "read") for w in result["writes"]] == [
        "bind_serving_provider",
        "set_serving",
        "read",
        "bind_serving_provider",
        "set_serving",
    ]
    restore = result["writes"][3]["args"]
    assert restore["expected_revision"] == 3
    assert json.loads(restore["payload_json"]) == {"provider": "server-key"}
    assert result["recovery"] is None and result["choice"] is None


def test_signout_after_bind_prevents_followup_enable(tmp_path):
    result = run_picker(
        tmp_path,
        """
      const original=MCP.callTool;
      MCP.callTool=async(name,args)=>{
        const result=await original(name,args);ModelPicker.reset();return result;
      };
      await ModelPicker.allowAccess(doc.sources[0]);
    """,
        access_catalogue(legacy=True),
    )
    assert len(result["writes"]) == 1 and result["snapshot"] is None


@pytest.mark.parametrize("changed", ["home", "revision", "serving"])
def test_restore_does_not_overwrite_changed_or_working_state(tmp_path, changed):
    mutation = {
        "home": 'doc.universe_id="new-home";',
        "revision": 'MCP.callTool=async()=>({binding:{revision:99,status:"configured"}});',
        "serving": 'MCP.callTool=async()=>({binding:{revision:3,status:"serving"}});',
    }[changed]
    result = run_picker(
        tmp_path,
        """
      const original=MCP.callTool;
      MCP.callTool=async(name,args)=>{
        if(args.operation==="set_serving") {writes.push({name,args});return {error:"held"};}
        return original(name,args);
      };
      await ModelPicker.allowAccess(doc.sources[0]);
    """
        + mutation
        + "await ModelPicker.restoreAccess();",
        access_catalogue(legacy=True),
    )
    assert len(result["writes"]) == 2
    assert "no restore was attempted" in result["ui"]["model-status"]["text"]


def test_the_menu_is_the_route_to_connecting_another_model_source(tmp_path):
    """"Optional extras live in the model picker" (founder, 2026-09-30).

    A standing offer to add a second source used to be a permanent card in
    "Waiting on you" with no Accept, Deny or Clear on it, because a derived
    entry has nothing to resolve. It is off the rail now, so the picker has to
    reach it -- one tap from "Change model", not two through the dialog.
    """
    result = run_picker(tmp_path, "")
    rows = result["ui"]["model-menu"]["children"]
    connect = [row for row in rows if "Connect another model source" in row["text"]]
    assert len(connect) == 1, "the picker has no route to adding a source"
    # Classed apart from the model choices, because it is not one of them: a
    # keyboard user tabbing the list must not land on it as a pick.
    assert "model-menu-item--more" in connect[0]["cls"]
    assert connect[0]["disabled"] is False


def test_the_connect_row_opens_the_connect_request_and_closes_the_menu(tmp_path):
    result = run_picker(tmp_path, """
      const label=c=>c.children.map(g=>g.textContent).join("");
      const row=$("model-menu").children
        .find(c=>label(c).includes("Connect another model source"));
      if(!row) throw new Error("no connect row in the menu");
      row.click();
    """)
    assert result["connects"] == 1, "the menu row did not open the connect request"
    assert result["ui"]["model-menu"]["hidden"] is True, "the menu stayed open over the card"


# --------------------------------------------------------------------------
# Effort, driven through the real picker code.
# --------------------------------------------------------------------------


def effort_catalogue(levels=("low", "medium", "high", "xhigh", "max")):
    """A catalogue where `first` advertises effort levels and `second` does not."""
    doc = catalogue()
    for row in doc["options"]:
        row["effort_levels"] = list(levels) if row["reference"]["model_id"] == "first" else []
        row["effort"] = ""
    doc["preferences"]["policy"] = {
        "version": 2, "mode": "explicit", "saved_default": ref("first"),
        "fallbacks": [], "efforts": [],
    }
    return doc


def menu_rows(result):
    return result["ui"]["model-menu"]["children"]


def row_label(row):
    """A checked row carries the tick glyph in its text; compare the label."""
    return row["text"].replace("✓", "").strip()


def test_effort_offers_exactly_the_levels_the_source_advertised(tmp_path):
    """Per model, from the provider -- never a fixed list this app carries."""
    result = run_picker(tmp_path, "await ModelPicker.menuOpen();", effort_catalogue())
    rows = menu_rows(result)
    assert any("Effort" in row["text"] for row in rows), "no effort group was rendered"
    after = rows[next(i for i, r in enumerate(rows) if "Effort" in r["text"]) + 1:]
    offered = [row_label(r) for r in after if r["cls"] == "model-menu-item"]
    assert offered[:6] == ["Provider default", "low", "medium", "high", "xhigh", "max"]
    # "Provider default" is ticked while nothing is saved: the app has not
    # chosen a level on the owner's behalf.
    default_row = next(r for r in after if row_label(r) == "Provider default")
    assert default_row["checked"] == "true"


def test_a_model_without_advertised_levels_shows_no_effort_control(tmp_path):
    """The founder's constraint: unsupported means absent, not greyed out."""
    doc = effort_catalogue()
    doc["preferences"]["policy"]["saved_default"] = ref("second")
    result = run_picker(tmp_path, "await ModelPicker.menuOpen();", doc)
    assert all("Effort" not in row["text"] for row in menu_rows(result))


def test_automatic_mode_shows_no_effort_control(tmp_path):
    """Automatic has no single model, so there is nothing to set a level on."""
    doc = effort_catalogue()
    doc["preferences"]["policy"] = {
        "version": 2, "mode": "automatic", "saved_default": None,
        "fallbacks": [], "efforts": [],
    }
    result = run_picker(tmp_path, "await ModelPicker.menuOpen();", doc)
    assert all("Effort" not in row["text"] for row in menu_rows(result))


def test_choosing_a_level_saves_it_against_that_model(tmp_path):
    result = run_picker(tmp_path, """
      await ModelPicker.menuOpen();
      await ModelPicker.chooseEffort(""" + json.dumps(ref("first")) + ""","xhigh");
    """, effort_catalogue())
    assert result["requests"], "choosing a level sent no save"
    policy = result["requests"][-1]["body"]["policy"]
    assert policy["version"] == 2
    assert policy["efforts"] == [
        {"provider_ref": ref("first")["provider_ref"], "model_id": "first", "level": "xhigh"},
    ]
    # Unlike a model pick, the menu stays open so levels can be compared.
    assert result["ui"]["model-menu"]["hidden"] is False
    rows = menu_rows(result)
    picked = next(r for r in rows if row_label(r) == "xhigh")
    assert picked["checked"] == "true"


def test_switching_model_does_not_erase_a_saved_effort_level(tmp_path):
    """A save replaces the WHOLE policy, so a rebuilt draft would wipe levels.

    Switching model is not a decision about effort. Pinned because the client
    owns the document it posts: dropping `efforts` here would silently reset
    every level the owner had set, with the server doing exactly as told.
    """
    doc = effort_catalogue()
    doc["preferences"]["policy"]["efforts"] = [
        {"provider_ref": ref("first")["provider_ref"], "model_id": "first", "level": "max"},
    ]
    result = run_picker(tmp_path, """
      await ModelPicker.menuOpen();
      await ModelPicker.choose(ModelPicker.key(""" + json.dumps(ref("second")) + """));
    """, doc)
    policy = result["requests"][-1]["body"]["policy"]
    assert policy["saved_default"] == ref("second")
    assert policy["efforts"] == [
        {"provider_ref": ref("first")["provider_ref"], "model_id": "first", "level": "max"},
    ], "the other model's saved level was dropped by a model switch"


def test_going_back_to_automatic_keeps_saved_effort_levels(tmp_path):
    doc = effort_catalogue()
    doc["preferences"]["policy"]["efforts"] = [
        {"provider_ref": ref("first")["provider_ref"], "model_id": "first", "level": "high"},
    ]
    result = run_picker(tmp_path, """
      await ModelPicker.menuOpen();
      await ModelPicker.choose("");
    """, doc)
    policy = result["requests"][-1]["body"]["policy"]
    assert policy["mode"] == "automatic"
    assert policy["efforts"] == [
        {"provider_ref": ref("first")["provider_ref"], "model_id": "first", "level": "high"},
    ]


def test_clearing_a_level_returns_to_the_provider_default(tmp_path):
    doc = effort_catalogue()
    doc["preferences"]["policy"]["efforts"] = [
        {"provider_ref": ref("first")["provider_ref"], "model_id": "first", "level": "low"},
    ]
    result = run_picker(tmp_path, """
      await ModelPicker.menuOpen();
      await ModelPicker.chooseEffort(""" + json.dumps(ref("first")) + ""","");
    """, doc)
    assert result["requests"][-1]["body"]["policy"]["efforts"] == []


def test_a_refused_effort_save_does_not_show_a_level_that_is_not_stored(tmp_path):
    """The menu must not report a setting the server refused."""
    result = run_picker(tmp_path, """
      await ModelPicker.menuOpen();
      await ModelPicker.chooseEffort(""" + json.dumps(ref("first")) + ""","max");
    """, effort_catalogue(), response={"error": "model_preferences_conflict"})
    rows = menu_rows(result)
    assert all(r["checked"] != "true" or row_label(r) != "max" for r in rows), (
        "a refused level was left ticked"
    )


def test_an_expired_catalogue_offers_no_effort_either(tmp_path):
    result = run_picker(tmp_path, """
      expire();Owner.getModelOptions=async()=>{throw new Error('offline');};
      await ModelPicker.menuOpen();
      await ModelPicker.chooseEffort(""" + json.dumps(ref("first")) + ""","high");
    """, effort_catalogue())
    assert result["requests"] == [], "a stale catalogue must not save a level"


def test_a_provider_advertised_model_renders_as_a_normal_choice(tmp_path):
    """The founder's symptom, closed in the UI layer.

    An executor-enumerated row must be pickable and must NOT appear under
    "Needs access". The same id arriving only from the reviewed public list is
    an offer to grant and belongs under that divider with its reason -- so this
    asserts both halves, because the broken build also SHOWED the model, just
    in the wrong group.
    """
    doc = catalogue()
    advertised, offered = doc["options"][0], doc["options"][1]
    advertised["availability_basis"] = "executor_enumerated"
    advertised["in_candidate_catalog"] = True
    advertised["reasons"] = []
    offered["availability_basis"] = "publicly_listed"
    offered["in_candidate_catalog"] = False
    offered["reasons"] = [{"reason": "model_access_optin_required"}]
    result = run_picker(tmp_path, "await ModelPicker.menuOpen();", doc)

    rows = menu_rows(result)
    divider = next((i for i, r in enumerate(rows) if "Needs access" in r["text"]), len(rows))
    above = [row_label(r) for r in rows[:divider] if r["cls"] == "model-menu-item"]
    below = [row_label(r) for r in rows[divider:] if r["cls"] == "model-menu-item"]

    assert "first" in " ".join(above), "the advertised model was not offered as a choice"
    advertised_row = next(r for r in rows if row_label(r).endswith("first"))
    assert advertised_row["disabled"] is False, "the advertised model was not pickable"
    assert "opt in" not in advertised_row["text"].replace("_", " ")
    # ...and the grant-only row is still gated, with its reason said out loud.
    assert "second" in " ".join(below), "a grant-only row escaped the Needs access group"
    assert any("model access optin required" in r["text"] for r in rows[divider:])
