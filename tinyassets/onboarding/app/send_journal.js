  function captureTurnOptions(opts){
    opts=opts||{};
    return Object.assign({},opts,{
      owner:queueOwner,scope:queueScope,
      agentId:typeof opts.agentId==="string"&&opts.agentId?opts.agentId:(typeof addressedAgentId==="function"?addressedAgentId():"main"),
      inputMethod:turnInputMethod(opts.inputMethod),
      modelChoice:copyModelChoice(Object.prototype.hasOwnProperty.call(opts,"modelChoice")
        ? opts.modelChoice : modelChoiceForNextTurn)});
  }
  // The record carries its command center for the same reason a saved queue line
  // does: another founder signing in on this browser must never be shown, or
  // offered to resend, a message this one held - nor the filenames it names
  // (Fable 21633 on the app byte-intake shape, 2026-09-20).
  function rememberInflight(message, display, sentAt, inputMethod, modelChoice, consumerRequest=null,
                            agent=null,clientSendId="",owner=queueOwner,scope=queueScope){
    const record={message, display: display||message, client_send_id:clientSendId,
       inputMethod:turnInputMethod(inputMethod),
       modelChoice:copyModelChoice(modelChoice),
       consumerRequest,
       // The agent this turn was SENT to. A pinned line (a claimed held line)
       // names it, because the owner may have switched agents since: the
       // record has to describe the turn on the wire, not the screen, or the
       // recovery offer reappears under the wrong agent.
       agent:String((typeof agent==="string"&&agent)||
         (typeof addressedAgentId==="function"?addressedAgentId():"main")),
       owner,
       scope,
       ts:(typeof sentAt==="number" && Number.isFinite(sentAt))?sentAt:Date.now()};
    writeInflight(record); liveInflight=record;
  }
  function writeInflight(record){
    if(!record.client_send_id)record.client_send_id=crypto.randomUUID();
    localStorage.setItem(INFLIGHT_KEY+":"+record.client_send_id,JSON.stringify(record));
  }
  function readInflights(){
    const old=localStorage.getItem(INFLIGHT_KEY);
    if(old){
      const record=JSON.parse(old);
      if(!record.client_send_id)record.legacy=true;
      writeInflight(record);
      localStorage.removeItem(INFLIGHT_KEY);
    }
    const records=[];
    for(let i=0;i<localStorage.length;i++){
      const key=localStorage.key(i);
      if(key&&key.startsWith(INFLIGHT_KEY+":"))records.push(JSON.parse(localStorage.getItem(key)));
    }
    return records.sort((a,b)=>a.ts-b.ts);
  }
  function readInflight(id){
    return readInflights().find(r=>id?r.client_send_id===id:
      ownsSavedRow(r)&&(r.agent||"main")===addressedAgentId())||null;
  }
  function sameInflight(a,b){return !!a&&!!b&&a.client_send_id===b.client_send_id;}
  function forgetInflight(id){
    if(!id)return;
    localStorage.removeItem(INFLIGHT_KEY+":"+id);
    if(liveInflight&&liveInflight.client_send_id===id)liveInflight=null;
  }
  function forgetInflightIf(record){if(record)forgetInflight(record.client_send_id);}
  async function importSavedQueue(){
    const items=JSON.parse(localStorage.getItem(QUEUE_KEY)||"[]"),remaining=[];
    for(const item of items){
      if(!ownsSavedRow(item)){remaining.push(item);continue;}
      // Deterministic migration: two tabs import the same legacy draft with one identity.
      const bytes=new TextEncoder().encode(JSON.stringify([item.owner,item.scope,item.agent||"main",item.ts,item.message]));
      const digest=await crypto.subtle.digest("SHA-256",bytes);
      const id="legacy-"+Array.from(new Uint8Array(digest),n=>n.toString(16).padStart(2,"0")).join("");
      writeInflight(Object.assign({},item,{client_send_id:id,legacy:true}));
    }
    if(remaining.length)localStorage.setItem(QUEUE_KEY,JSON.stringify(remaining));
    else localStorage.removeItem(QUEUE_KEY);
  }

  async function sendConversationRequest(message,display,sentAt,opts){
    const epoch=MCP._loginEpoch;
    const checkLogin=()=>{if(epoch!==MCP._loginEpoch)throw new Error("Conversation account changed.");};
    const submit=()=>MCP.converse(message,opts.inputMethod,opts.modelChoice,opts.consumerRequest||null,opts.agentId,opts.clientSendId,opts.onModelEvent,opts.scope);
    let payload=await submit();
    if(payload&&payload.already_received&&opts.consumerRequest){
      const observed=await Owner.read({target:"conversation_turn",request_key:opts.consumerRequest.request_key});
      if(observed&&observed.consumer_turn)payload=observed;
    }
    // From here on the server has ANSWERED a converse for this message, and a
    // chosen-conversation turn may be accepted and running. A later failure (a
    // poll whose handshake fails, a 401 whose renewal is down) is not sent for
    // THAT request only - it proves nothing about the turn. So `notSent` never
    // leaves this function: the page keeps "Check this conversation" and holds
    // whatever is queued behind a turn that may still be working.
    try{ return await continueConversationRequest(payload); }
    catch(err){ if(err&&typeof err==="object"&&err.notSent) err.notSent=false; throw err; }
    async function continueConversationRequest(payload){
    checkLogin();
    if(payload&&payload.error==="consumer_request_required"&&!opts.consumerRequest){
      const s=payload.consumer_selection;
      if(!s||s.version!==1||typeof s.binding_id!=="string"||!Number.isInteger(s.binding_revision))
        throw new Error("The selected conversation is unavailable.");

      opts.consumerRequest={version:1,request_key:crypto.randomUUID(),
        binding_id:s.binding_id,binding_revision:s.binding_revision};
      rememberInflight(message,display,sentAt,opts.inputMethod,opts.modelChoice,opts.consumerRequest,
        opts.agentId,opts.clientSendId,opts.owner,opts.scope);
      const saved=readInflight(opts.clientSendId);
      if(!saved||JSON.stringify(saved.consumerRequest)!==JSON.stringify(opts.consumerRequest))
        throw new Error("Could not save safe conversation recovery. No workflow was started.");
      payload=await submit();
      checkLogin();
    }
    const started=Date.now();
    if(payload&&payload.consumer_turn){
      const id=payload.consumer_turn.turn_id,key="consumer:"+id+":founder";
      const nodes=Array.from($("thread").children),own=nodes.find(n=>n.clientSendId===opts.clientSendId);
      if(own){
        nodes.filter(n=>n!==own&&n.savedTurnKey===key).forEach(n=>n.remove());
        own.savedTurnKey=key;
      }
      renderedConsumerFounders.add(id);
    }
    while(payload&&payload.consumer_turn&&payload.consumer_turn.state==="pending"){
      if(Date.now()-started>30*60*1000)throw new Error("Still working. Check this same conversation again.");
      await new Promise(resolve=>setTimeout(resolve,1500));
      checkLogin();
      payload=await Owner.read({target:"conversation_turn",
        request_key:opts.consumerRequest.request_key});
      checkLogin();
      if(payload&&payload.consumer_turn&&payload.consumer_turn.state==="held"&&
         ["completed","failed","cancelled","interrupted"].includes(payload.consumer_turn.run_status)){
        // Exact replay repairs only the already-terminal pair after a crash gap.
        payload=await submit();
        checkLogin();
      }
    }
    if(payload&&payload.consumer_turn&&["pending","held","expired"].includes(payload.consumer_turn.state))
      throw new Error(payload.notice||"This conversation is held; it will not be run again.");
    return payload;
    }
  }
