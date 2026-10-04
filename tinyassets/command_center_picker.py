"""The blank command center and its public, health-checked package offers."""

from __future__ import annotations

import json
import logging

logger = logging.getLogger(__name__)

BUILD_PROMPT = (
    "Help me design my own command center: ask me what I want it to do, then build it."
)

PLATFORM_DEFAULT_UI = {
    "kind": "tinyassets.app-ui.v1",
    "version": 1,
    "ui_id": "platform:blank",
    "name": "Blank command center",
    "markup": """<main id="offer">
<h1>Your command center is empty</h1>
<p>Talk to your agent to build or change this space, or browse a design someone has shared.
Browsing and visual previews do not send a message or use a model.</p>
<button id="build">Build your own</button>
<button id="try-one">Try someone else's</button>
<section id="packages" hidden aria-label="Published command centers"></section>
<p id="message" role="status"></p>
</main>""",
    "style": """*{box-sizing:border-box}html,body{margin:0;min-height:100%;}
body{min-height:100vh;display:grid;place-items:center;background:#101419;
color:#edf0f3;font:16px system-ui,sans-serif;padding:32px}
main{width:min(760px,100%);text-align:center}h1{font-size:clamp(24px,4vw,36px)}
p{line-height:1.6;color:#b9c3ce}button{font:inherit;cursor:pointer;border:1px solid
#667789;border-radius:8px;padding:12px 18px;margin:6px;background:#233344;color:inherit}
button:disabled{opacity:.6;cursor:wait}a{color:#b9c3ce}section{text-align:left;
margin:24px 0}article{border:1px solid #43505f;border-radius:12px;padding:16px;
margin:12px 0}h2{margin:0;font-size:20px}[hidden]{display:none!important}""",
    "script": """(async()=>{
const el=id=>document.getElementById(id),message=el('message');
const say=error=>{message.textContent=error.message||String(error);};
el('try-one').onclick=()=>{el('packages').hidden=false;};
el('build').onclick=async()=>{
  try{await tinyassets.call('chat.prefill',{text:BUILD_PROMPT});}catch(error){say(error);}
};
try{
  const doc=await tinyassets.call('packages.list_tryable',{});
  if(!doc.packages.length&&!(doc.systems||[]).length)
    el('packages').textContent='No shared command centers are available yet. '+
      'You can build your own.';
  if(doc.can_try)for(const p of doc.packages){
    const card=document.createElement('article'),name=document.createElement('h2');
    name.textContent=p.name;card.appendChild(name);
    const description=document.createElement('p');description.textContent=p.description;
    card.appendChild(description);
    const detail=document.createElement('p');
    detail.textContent=p.author_id+' · Version '+p.version+' · '+p.size+
      ' · Model: '+(p.needs.model||'None specified')+
      ' · Connections: '+(p.needs.connections.join(', ')||'None');
    card.appendChild(detail);
    const button=document.createElement('button');button.textContent='Preview';
    button.onclick=async()=>{
      button.disabled=true;
      try{await tinyassets.call('packages.preview',{agent_definition_id:p.agent_definition_id});
        message.textContent='Visual preview is open. '+
          'Nothing installs before you choose Copy and confirm.';
      }catch(error){say(error);}finally{button.disabled=false;}
    };
    card.appendChild(button);el('packages').appendChild(card);
  }
  for(const p of doc.systems||[]){
    const card=document.createElement('article'),name=document.createElement('h2');
    name.textContent=p.name;card.appendChild(name);
    const description=document.createElement('p');description.textContent=p.description;
    card.appendChild(description);
    const detail=document.createElement('p');
    detail.textContent=p.author_id+' · Public system · Components only; no files · '+
      p.workflow_count+' workflows · '+p.automation_count+' paused automations · '+
      (p.agent_template_count||0)+' public agent templates';
    card.appendChild(detail);
    const button=document.createElement('button');button.textContent='Preview copy';
    button.disabled=!p.available;
    button.onclick=async()=>{
      button.disabled=true;
      try{await tinyassets.call('packages.preview',{agent_definition_id:p.agent_definition_id});
        message.textContent='Visual preview is open. '+
          'Choose Copy to review and confirm; nothing installs yet.';
      }catch(error){say(error);}finally{button.disabled=!p.available;}
    };
    card.appendChild(button);
    if(!p.available){const why=document.createElement('p');
      why.textContent=p.unavailable_reason;card.appendChild(why);}
    el('packages').appendChild(card);
  }
}catch(error){say(error);}
})();""".replace("BUILD_PROMPT", json.dumps(BUILD_PROMPT)),
}


def working_packages() -> list[dict]:
    """Latest per author/name, newest first; unhealthy publications never offer a Try."""
    from tinyassets.api import package_requests
    from tinyassets.api.helpers import _base_path
    from tinyassets.api.publish_requests import BRANCH_REF_KIND
    from tinyassets.branch_versions import branch_version_is_public, get_branch_version
    from tinyassets.command_center_agent_templates import templates

    try:
        rows = package_requests.list_packages(limit=100)
        rows.sort(key=lambda row: (row.get("created_at") or "",
                                   row["agent_definition_id"]), reverse=True)
        base = _base_path()
    except Exception:
        logger.warning("Could not list command-center packages", exc_info=True)
        return []
    latest = {}
    for row in rows:
        try:
            key = (row["author_id"], row["name"])
            if key not in latest or row["version"] > latest[key]["version"]:
                latest[key] = row
        except Exception:
            logger.warning("Skipping invalid package listing", exc_info=True)
    result = []
    for row in rows:
        try:
            if latest.get((row["author_id"], row["name"])) is not row:
                continue
            definition, _, _, _ = package_requests._load(row)
            for component in definition["components"].values():
                if component.get("kind") != BRANCH_REF_KIND:
                    continue
                version = component.get("published_version_id")
                if (not version or not branch_version_is_public(base, version)
                        or get_branch_version(base, version) is None):
                    raise ValueError("package workflow version is missing or not public")
            agent_template_count = len(templates(base, definition["components"]))
            result.append({key: row[key] for key in (
                "agent_definition_id", "name", "description", "author_id", "version",
                "size", "file_count", "needs",
            )})
            result[-1]["agent_template_count"] = agent_template_count
            if len(result) == 12:
                break
        except Exception:
            logger.warning("Skipping unhealthy command-center package %s",
                           row.get("agent_definition_id"), exc_info=True)
    return result


def read_packages(*, universe_id: str = "") -> dict:
    from tinyassets.api.pending_requests import _owner_gate

    _, _, denial = _owner_gate(universe_id)
    if denial is not None:
        return denial
    packages = working_packages()
    systems = working_systems()
    return {"packages": packages, "systems": systems, "build_prompt": BUILD_PROMPT,
            "can_try": bool(packages or any(row["available"] for row in systems))}


def working_systems() -> list[dict]:
    from tinyassets.api.system_copy_requests import list_systems

    return list_systems()
