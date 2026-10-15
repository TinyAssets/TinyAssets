"""Execute the packaged send lifecycle before composing the shared C12 shell."""
from pathlib import Path

import pytest

from tests.test_app_spoken_turn_account_fence import _run
from tinyassets.onboarding import app_modules


@pytest.fixture
def html(tmp_path):
    template = tmp_path / "shell.html"
    template.write_text("__TA_APP_MAIN__", encoding="utf-8")
    source = app_modules.shell_source(template)
    ui = (app_modules.MODULE_DIR.parent / "app_ui.js").read_text(encoding="utf-8")
    return source.replace("__TA_APP_UI__", ui)


def test_retry_keeps_committed_send_identity(tmp_path, html):
    result = _run(tmp_path, html, r'''
    setQueueOwner("p-1"); setQueueScope("u-1");
    await loadHistory(); await pollStatus();
    const first=sendTurn("do this once"); await settle();
    const original=snapshot().inflight.client_send_id;
    const error=new Error("connection lost");error.transport=true;gates[0].reject(error);
    await first;await settle();
    const retry=sendTurn("do this once",undefined,{clientSendId:original,echoed:true});
    await settle();
    const replay=snapshot().inflight.client_send_id;
    gates[1].resolve({already_received:true,history_saved:true,universe_id:"u-1"});
    await retry;
    console.log(JSON.stringify({original,replay}));
    ''')
    assert result["original"]
    assert result["replay"] == result["original"]


def test_spoken_adapter_preserves_composer_and_returns_reply(tmp_path, html):
    result = _run(tmp_path, html, r'''
    setQueueOwner("p-1"); setQueueScope("u-1");
    await loadHistory(); await pollStatus();
    els["composer-input"].value="unfinished typed draft";
    const turn=sendVoiceTurn("read the result"); await settle();
    gates[0].resolve({reply:"The result."});
    const reply=await turn;
    console.log(JSON.stringify({reply,after:fence()}));
    ''')
    assert result["reply"] == "The result."
    assert result["after"]["composer"] == "unfinished typed draft"
    assert result["after"]["settled"] == [True]


def test_reload_observes_the_saved_identity_without_resending(tmp_path, html):
    result = _run(tmp_path, html, r'''
    setQueueOwner("p-1"); setQueueScope("u-1");
    rememberInflight("finish this","finish this",Date.now(),
      "typed",null,null,"main","original-send");
    liveInflight=null;inflightRestored=false;
    await restoreInflight([]);
    const note=els.thread.children.find(n=>n.unconfirmed);
    await note.checkSavedConversation();await settle();
    const identity=snapshot().inflight.client_send_id;
    console.log(JSON.stringify({identity,calls:converseCalls,after:snapshot()}));
    ''')
    assert result["identity"] == "original-send"
    assert result["calls"] == []
    assert result["after"]["resendButtons"] == 0


def test_durable_acknowledgement_does_not_claim_a_completed_turn(tmp_path, html):
    result = _run(tmp_path, html, r'''
    setQueueOwner("p-1"); setQueueScope("u-1");
    await loadHistory(); await pollStatus();
    const turn=sendTurn("still working",undefined,{clientSendId:"received-send"});
    await settle();
    gates[0].resolve({already_received:true,history_saved:true,universe_id:"u-1"});
    await turn;await settle();
    console.log(JSON.stringify({after:fence()}));
    ''')
    assert result["after"]["inflight"]["client_send_id"] == "received-send"
    assert result["after"]["settled"] == [False]
    assert result["after"]["converseCalls"] == ["still working"]


def test_same_text_new_send_has_new_identity(tmp_path, html):
    result = _run(tmp_path, html, r'''
    setQueueOwner("p-1"); setQueueScope("u-1");
    await loadHistory(); await pollStatus();
    const first=sendTurn("again"); await settle();
    const original=snapshot().inflight.client_send_id;
    gates[0].resolve({reply:"done"});await first;
    const next=sendTurn("again");await settle();
    const nextId=snapshot().inflight.client_send_id;
    gates[1].resolve({reply:"done again"});await next;
    console.log(JSON.stringify({original,nextId}));
    ''')
    assert result["original"] != result["nextId"]


def test_shell_sources_are_split_without_exposing_request_values(tmp_path):
    template = tmp_path / "app.html"
    template.write_text('<script nonce="nonce">__TA_APP_MAIN__</script>', encoding="utf-8")
    source = app_modules.shell_source(template)
    assert "__TA_DURABLE_SEND__" not in source
    assert "__TA_SEND_JOURNAL__" not in source
    assert source.count("async function sendTurn(") == 1
    assert source.count("async function sendVoiceTurn(") == 1
    assert "__TA_ONBOARDING_CONFIG__" in source
    assert "AppRecovery.attach" in source
    assert Path(app_modules.MODULE_DIR / "durable_send.js").is_file()


def test_served_app_activates_one_packaged_shell():
    from tinyassets.onboarding import render_app_html

    html, _csp = render_app_html()
    assert "__TA_APP_MAIN__" not in html
    assert "__TA_DURABLE_SEND__" not in html
    assert html.count("async function sendTurn(") == 1
    assert html.count("async function sendVoiceTurn(") == 1
    assert "_meta:{progressToken:frame.id}" in html
    assert "function billingSurface()" in html
