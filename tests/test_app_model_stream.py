"""Progress previews use the existing request's SSE and never become final replies."""
import json
import shutil
import subprocess

import pytest

from tinyassets.onboarding.app_modules import MODULE_DIR


def _run(tmp_path, body):
    node = shutil.which("node")
    if node is None:
        pytest.fail("Node is required to verify the shipped model stream renderer")
    source = (MODULE_DIR / "model_stream.js").read_text()
    main = (MODULE_DIR / "main.js").read_text()
    transport = "// ---- MCP client" + main.split("// ---- MCP client", 1)[1].split(
        "// ---- UI ----", 1)[0]
    script = tmp_path / "stream.js"
    script.write_text(source + "\n" + transport + "\n(async()=>{\n" + body +
                      "\n})().catch(e=>{console.error(e);process.exit(1)});")
    result = subprocess.run([node, str(script)], capture_output=True, text=True, timeout=10)
    assert result.returncode == 0, result.stderr
    return json.loads(result.stdout)


def test_sse_progress_is_correlated_and_never_replaces_the_terminal_reply(tmp_path):
    result = _run(tmp_path, r'''
    const seen=[];
    const notification=(token,event)=>({jsonrpc:"2.0",method:"notifications/progress",
      params:{progressToken:token,progress:1,
        message:JSON.stringify({tinyassets_model_stream:1,...event})}});
    const start={type:"model_start",operation_id:"op-1"};
    const delta={type:"model_text_delta",operation_id:"op-1",text:"hello Ω"};
    const frames=[notification(8,{...delta,text:"foreign"}),notification(7,start),
      notification(7,delta),{jsonrpc:"2.0",id:7,result:{reply:"Canonical saved reply"}}];
    const bytes=new TextEncoder().encode(frames.map(f=>"data: "+JSON.stringify(f)+"\n\n").join(""));
    const response=new Response(new ReadableStream({start(controller){
      for(let i=0;i<bytes.length;i+=3)controller.enqueue(bytes.slice(i,i+3));
      controller.close();
    }}),{headers:{"Content-Type":"text/event-stream"}});
    const terminal=await MCP._readTerminal(response,7,event=>{seen.push(event);});
    console.log(JSON.stringify({seen,terminal}));
    ''')
    assert [event["type"] for event in result["seen"]] == ["model_start", "model_text_delta"]
    assert result["seen"][1]["text"] == "hello Ω"
    assert result["terminal"]["result"]["reply"] == "Canonical saved reply"


def test_preview_is_fenced_bounded_and_cleared_on_terminal_or_interruption(tmp_path):
    result = _run(tmp_path, r'''
    let current=true,created=0,removed=0,text="";
    const preview=ModelStream.open({current:()=>current,create:()=>{
      created++;return {write:value=>{text=value;},remove:()=>{removed++;text="";}};
    }});
    const emit=(type,operation_id,extra={})=>preview.event({type,operation_id,...extra});
    emit("model_text_delta","unknown",{text:"ignored"});
    emit("model_start","op-1");
    emit("model_text_delta","op-1",{text:"<script>untrusted</script>"});
    const literal=text;
    emit("model_end","op-1",{status:"interrupted"});
    emit("model_text_delta","op-1",{text:"late"});
    emit("model_start","op-2");
    emit("model_text_delta","op-2",{text:"x".repeat(140000)});
    const bounded=text.length;
    current=false;emit("model_text_delta","op-2",{text:"another account"});
    current=true;emit("model_start","op-3");
    preview.close();
    console.log(JSON.stringify({literal,bounded,text,created,removed}));
    ''')
    assert result == {"literal": "<script>untrusted</script>", "bounded": 131072,
                      "text": "", "created": 2, "removed": 2}


def test_render_failure_cannot_discard_the_saved_final_reply(tmp_path):
    result = _run(tmp_path, r'''
    const progress={jsonrpc:"2.0",method:"notifications/progress",params:{progressToken:7,
      message:JSON.stringify({tinyassets_model_stream:1,type:"model_start",operation_id:"op"})}};
    const terminal={jsonrpc:"2.0",id:7,result:{reply:"saved"}};
    const response=new Response("data: "+JSON.stringify(progress)+"\n\n"+
      "data: "+JSON.stringify(terminal)+"\n\n",{headers:{"Content-Type":"text/event-stream"}});
    const final=await MCP._readTerminal(response,7,()=>{throw new Error("renderer failed");});
    console.log(JSON.stringify(final));
    ''')
    assert result["result"]["reply"] == "saved"


def test_converse_requests_progress_on_the_existing_mcp_call(tmp_path):
    result = _run(tmp_path, r'''
    globalThis.token=()=>"test-token";
    globalThis.ensureFreshToken=async()=>{};
    const requests=[];
    globalThis.fetch=async(_url,init)=>{
      const frame=JSON.parse(init.body);requests.push(frame);
      return Response.json({jsonrpc:"2.0",id:frame.id,result:{structuredContent:{reply:"done"}}});
    };
    MCP.sessionId="test-session";
    await MCP.callTool("converse",{message:"hello"},{onModelEvent:()=>{}});
    await MCP.callTool("read_graph",{target:"status"});
    console.log(JSON.stringify(requests));
    ''')
    assert result[0]["method"] == "tools/call"
    assert result[0]["params"]["_meta"]["progressToken"] == result[0]["id"]
    assert result[0]["params"]["arguments"] == {"message": "hello"}
    assert "_meta" not in result[1]["params"]
