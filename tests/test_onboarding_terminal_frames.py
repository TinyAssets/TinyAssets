"""Independent wire-format coverage for the app's actual response reader."""

from __future__ import annotations

import json
import shutil
import subprocess
from pathlib import Path

import pytest

import tinyassets.onboarding as onboarding


@pytest.mark.parametrize("case", ["json", "json_error", "sse_error", "utf8", "multiline"])
def test_real_fetch_response_formats(tmp_path, case):
    node = shutil.which("node")
    assert node, "Node is required to verify the deployed JavaScript"
    from tinyassets.onboarding.app_modules import shell_source

    html = shell_source(Path(onboarding.__file__).parent / "app.html")
    source = "// ---- MCP client" + html.split("// ---- MCP client", 1)[1].split(
        "// ---- UI ----", 1)[0]
    program = r"""
const assert=require('node:assert/strict');
const wireCase=__CASE__;
let calls=0;
function token(){return 'local-test';}
async function ensureFreshToken(){}
async function refreshAccessToken(){throw new Error('unexpected auth retry');}
async function fetch(_url,init){
  calls++;
  const id=JSON.parse(init.body).id;
  const document={jsonrpc:'2.0',id};
  if(wireCase.endsWith('error'))document.error={code:-32602,message:'test rejection'};
  else document.result={structuredContent:{reply:'café ☀ finished'}};
  if(wireCase.startsWith('json'))return new Response(JSON.stringify(document),
    {headers:{'content-type':'application/json'}});
  let frame='data: '+JSON.stringify(document)+'\r\n\r\n';
  if(wireCase==='multiline')frame=': keepalive\r\nevent: message\r\n'+
    JSON.stringify(document,null,2).split('\n').map(line=>'data: '+line).join('\r\n')+
    '\r\n\r\n';
  const bytes=new TextEncoder().encode(frame);
  return new Response(new ReadableStream({start(controller){
    // Every byte is a chunk: this splits UTF-8 sequences and CRLF pairs.
    for(const byte of bytes)controller.enqueue(new Uint8Array([byte]));
    controller.close();
  }}),{headers:{'content-type':'text/event-stream'}});
}
__SOURCE__
MCP.sessionId='current';
(async()=>{
  let guard;
  try{
    const result=Promise.race([MCP.converse('read only'),new Promise((_,reject)=>{
      guard=setTimeout(()=>reject(new Error('unbounded response reader')),1000);
    })]);
    if(wireCase.endsWith('error'))await assert.rejects(result,
      error=>error.rpc && error.rpc.code===-32602 && error.message==='test rejection');
    else assert.deepEqual(await result,{reply:'café ☀ finished'});
    assert.equal(calls,1);
    assert.equal(MCP.sessionId,'current','valid response must not retire the session');
  }finally{clearTimeout(guard);}
})().catch(error=>{console.error(error);process.exitCode=1;});
"""
    script = tmp_path / "terminal_wire.js"
    script.write_text(program.replace("__CASE__", json.dumps(case)).replace(
        "__SOURCE__", source), encoding="utf-8")
    proc = subprocess.run([node, str(script)], capture_output=True, text=True,
                          encoding="utf-8", timeout=10)
    assert proc.returncode == 0, proc.stderr
