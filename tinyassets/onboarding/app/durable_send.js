  async function sendTurn(message, display, opts){
    opts=captureTurnOptions(opts);
    // `display` is what the founder sees in their bubble (compact when a file is
    // attached); `message` is the full text sent to the command center (file content
    // embedded verbatim). They differ only when an attachment rode along.
    // `opts.echoed` says the bubble is ALREADY on screen (a resend, or a message
    // restored after a reload) - drawing it twice would read as two sends.
    const sentAt=(typeof opts.sentAt==="number" && Number.isFinite(opts.sentAt))
      ? opts.sentAt : Date.now();
    opts.clientSendId=opts.clientSendId||crypto.randomUUID();
    if(activeSends.has(opts.clientSendId))return;
    // Persist before clearing the composer or publishing its bubble. Storage failure
    // leaves the draft in place and no request goes on the wire.
    try{rememberInflight(message,display,sentAt,opts.inputMethod,opts.modelChoice,
      opts.consumerRequest||null,opts.agentId,opts.clientSendId,opts.owner,opts.scope);}
    catch(err){setStatusLine("Could not save your message on this device. Please try again.");throw err;}
    if(opts.consumeUploads)Uploads.clear();
    const recoveryBubble=opts.recoveryBubble||(!opts.echoed&&!opts.background
      ?appendMessage("founder", display || message, null, sentAt/1000):null);
    if(recoveryBubble) recoveryBubble.clientSendId=opts.clientSendId;
    // A relayed answer or a side send did not come from the composer: what
    // the founder is typing there stays (Codex round 3).
    const input=$("composer-input");
    if(!opts.keepComposer){ input.value=""; input.style.height="auto"; }
    const myTurn={startedAt:Date.now()}; activeSends.set(opts.clientSendId,myTurn); activeTurn=myTurn;
    $("btn-send").disabled=false; turnStartedAt=Date.now();
    setStatusLine("Your agent is thinking...");
    renderWorking();
    let delivered=false;
    // Account AND home at the moment of sending. A reply or a failure that
    // lands after either changed belongs to a conversation that is no longer
    // on screen: it paints nothing, and nothing of it reaches the new
    // account's composer.
    const sentEpoch=MCP._loginEpoch, sentScope=queueScope, sentOwner=queueOwner;
    const stillHere=()=>sentEpoch===MCP._loginEpoch&&sentScope===queueScope&&
      sentOwner===queueOwner&&opts.agentId===addressedAgentId();
    const stream=typeof ModelStream==="undefined"?null:ModelStream.open({
      current:()=>stillHere()&&opts.agentId===addressedAgentId(),
      create:()=>{
        const node=appendMessage("system","Response in progress (not yet saved).");
        const body=document.createElement("div");body.className="msg-body";
        node.appendChild(body);
        return {write:text=>{body.textContent=text;},remove:()=>node.remove()};
      }
    });
    if(stream)opts.onModelEvent=event=>stream.event(event);
    // Same account, same home, new request: a retry sentence left over from an
    // earlier turn stops speaking for this one (see Voice.turnStarted).
    Voice.turnStarted(sentOwner,sentScope);
    try{
      const answer=await sendConversationRequest(message,display,sentAt,opts);
      if(stream)stream.close();
      if(answer&&answer.history_saved&&!answer.already_received&&!answer.retryable_setup)
        forgetInflight(opts.clientSendId);
      if(!stillHere()){
        if(opts.returnReply) throw new Error("voice_turn_retired");
        return;
      }
      if(answer&&answer.already_received){
        // Admission is durable, but is not proof the agent has finished.
        // Recover by the same identity; never unlock queued work on a guess.
        const error=new Error("Your message was received. Checking its saved result.");
        error.transport=true;error.received=true;throw error;
      }
      if(answer&&answer.status==='held'&&answer.reason==='setup_required'&&answer.needs_connection){
        const saved=readInflight(opts.clientSendId);
        if(!saved||saved.message!==message||saved.ts!==sentAt)throw new Error('Your message could not be saved for connection.');
        InlineConnection.hold(answer.needs_connection,saved);
        return;
      }
      if(answer&&answer.retryable_setup){
        const error=new Error(answer.error||"Connection setup is incomplete. Retry this message after connecting.");
        error.transport=true;throw error;
      }
      if(answer&&answer.error&&!answer.history_saved){
        const error=new Error(answer.error);error.transport=true;throw error;
      }
      if(opts.returnReply&&(!answer||typeof answer.reply!=="string")){
        if(answer&&(answer.error||answer.turn_failure))throw servedFailureError(answer);
        throw new Error("voice_converse_invalid");
      }
      if(answer&&answer.history_saved)forgetInflight(opts.clientSendId);
      const beforeReply=new Set(Array.from($("thread").children));
      if(opts.restored){
        const history=await Owner.getConversation();
        drawHistoryTurns((history.recent_conversation||{}).turns||[]);
      }else renderConverse(answer);
      for(const node of Array.from($("thread").children)){
        if(!beforeReply.has(node)&&String(node.className||"").includes("msg--universe"))
          node.replyToClientSendId=opts.clientSendId;
      }
      delivered=true;setStatusLine("");
      if(typeof AppUI!=="undefined") AppUI.turnSettled();
      if(typeof refreshAgentSwitcher==="function") refreshAgentSwitcher();
      refreshRail({foregroundAgent:opts.agentId});
      forgetInflight(opts.clientSendId);
      if(opts.returnReply) return answer.reply;
    }catch(err){
      if(!stillHere()){ if(opts.returnReply) throw err; return; }
      // Signing out clears the composer (it is account-scoped), so the text
      // cannot be "left in the box". What survives is the in-flight record on
      // disk, which the same account is offered again after signing in.
      if(err.authRequired){
        setStatusLine(""); sessionExpired(!opts.keepInflight);
        if(opts.returnReply) throw err;
        return;
      }
      setStatusLine("");
      // A transport failure is not a dead end: the session has already been
      // dropped, so the next attempt rebuilds it. Say what actually went wrong
      // and put the retry one click away, instead of leaving the user staring
      // at a parser string with nothing to do (founder, 2026-08-28).
      if(err.historySaved){
        forgetInflight(opts.clientSendId);
        const notice=appendFailureNotice(err.message,{speaker:"founder",text:message,display,truncated:false},undefined,err.failureCode,err.failure);
        if(notice) notice.replyToClientSendId=opts.clientSendId;
      }else{
        offerResend(message, display, err, Object.assign({}, opts||{}, {sentAt,recoveryBubble}));
        // Unsaved or unconfirmed: the local recovery record is still needed.
      }
      if(opts.returnReply) throw err;
    }finally{
      if(stream)stream.close();
      // Cleanup is a CHANGE TO THE SCREEN and obeys the same rule as the
      // answer. Two separate questions: is this still MY composer (nothing
      // newer took it), and is this still the same account and home?
      activeSends.delete(opts.clientSendId);
      const mine=activeTurn===myTurn;
      if(mine){
        activeTurn=Array.from(activeSends.values()).pop()||null;
        // Nothing newer holds the composer, so THIS turn is what wedged it -
        // leaving it disabled would strand whoever is signed in now.
        $("btn-send").disabled=false;turnStartedAt=activeTurn?activeTurn.startedAt:0;setStatusLine("");
        // This page's own turn is over. The indicator may still be up on the
        // server's word (another window's turn, or the queued line going out
        // next); renderWorking re-asks rather than assuming this was the only one.
        renderWorking();
      }
      // Lines queued behind this turn belong to the account and home that
      // queued them; they are not flushed into a different one. Neither the
      // voice session nor a status line of the account now on screen is
      // touched by a turn that outlived its own login or home.
      if(stillHere()&&mine){
        takeInterruptFlush();
        Voice.conversationSettled(delivered,{message,ts:sentAt,
          consumerRequest:opts.consumerRequest||null,owner:sentOwner,scope:sentScope});
      }
    }
  }

  async function sendVoiceTurn(message){
    // Speech must receive a reply or an explicit failure, never silently queue.
    if(activeTurn || interruptPending || serverTurnLive() || InlineConnection.waiting())
      throw new Error("voice_turn_busy");
    return sendTurn(message,message,{inputMethod:"spoken",keepComposer:true,returnReply:true});
  }
