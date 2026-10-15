  function captureTurnOptions(opts){
    opts=opts||{};
    return Object.assign({},opts,{
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
                            agent=null,clientSendId=""){
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
       owner:queueOwner,
       scope:queueScope,
       ts:(typeof sentAt==="number" && Number.isFinite(sentAt))?sentAt:Date.now()};
    try{ localStorage.setItem(INFLIGHT_KEY, JSON.stringify(record)); liveInflight=record; }catch(_e){}
  }
  function forgetInflight(){ try{ localStorage.removeItem(INFLIGHT_KEY); }catch(_e){} liveInflight=null; }
  // One record is the same record by what its writer could not have shared
  // with another turn: send identity and, for legacy records, the exact
  // text, send stamp and chosen-conversation request.
  function sameInflight(a,b){
    return !!a&&!!b&&(a.client_send_id||"")===(b.client_send_id||"")&&a.message===b.message&&(a.ts||0)===(b.ts||0)&&
      JSON.stringify(a.consumerRequest||null)===JSON.stringify(b.consumerRequest||null);
  }
  // Forget ONE record: the one a restore pass observed. A newer turn on this
  // page may have replaced it on disk while that pass awaited the connector
  // (or a click); an older turn's outcome must not erase the newer turn's
  // only way back after a reload.
  function forgetInflightIf(record){ if(sameInflight(readInflight(),record)) forgetInflight(); }
  function readInflight(){
    try{ const raw=localStorage.getItem(INFLIGHT_KEY); return raw?JSON.parse(raw):null; }
    catch(_e){ return null; }
  }

  async function sendConversationRequest(message,display,sentAt,opts){
    const epoch=MCP._loginEpoch;
    const checkLogin=()=>{if(epoch!==MCP._loginEpoch)throw new Error("Conversation account changed.");};
    let payload=await MCP.converse(message,opts.inputMethod,opts.modelChoice,opts.consumerRequest||null,opts.agentId,opts.clientSendId,opts.onModelEvent);
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
      if(opts.keepInflight)throw new Error("Resolve the earlier unconfirmed conversation first.");
      opts.consumerRequest={version:1,request_key:crypto.randomUUID(),
        binding_id:s.binding_id,binding_revision:s.binding_revision};
      rememberInflight(message,display,sentAt,opts.inputMethod,opts.modelChoice,opts.consumerRequest,
        opts.agentId,opts.clientSendId);
      const saved=readInflight();
      if(!saved||JSON.stringify(saved.consumerRequest)!==JSON.stringify(opts.consumerRequest))
        throw new Error("Could not save safe conversation recovery. No workflow was started.");
      payload=await MCP.converse(message,opts.inputMethod,opts.modelChoice,opts.consumerRequest,opts.agentId,opts.clientSendId,opts.onModelEvent);
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
        payload=await MCP.converse(message,opts.inputMethod,opts.modelChoice,opts.consumerRequest,opts.agentId,opts.clientSendId,opts.onModelEvent);
        checkLogin();
      }
    }
    if(payload&&payload.consumer_turn&&["pending","held","expired"].includes(payload.consumer_turn.state))
      throw new Error(payload.notice||"This conversation is held; it will not be run again.");
    return payload;
    }
  }
