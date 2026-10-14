  async function sendTurn(message, display, opts){
    opts=captureTurnOptions(opts);
    if(InlineConnection.waiting()) return queueTurn(message,display,opts);
    // A turn running on the server that this page did not start (another
    // window, an answered request) is just as busy as this page's own: a send
    // now steers or waits, never starts a second turn beside it (P1, live
    // 2026-10-02: such a send was lost).
    const otherTurn=!activeTurn && serverTurnLive();
    if($("btn-send").disabled || interruptPending || otherTurn)
      return steerOrQueue(message, display, opts);
    // `display` is what the founder sees in their bubble (compact when a file is
    // attached); `message` is the full text sent to the command center (file content
    // embedded verbatim). They differ only when an attachment rode along.
    // `opts.echoed` says the bubble is ALREADY on screen (a resend, or a message
    // restored after a reload) - drawing it twice would read as two sends.
    const sentAt=(typeof opts.sentAt==="number" && Number.isFinite(opts.sentAt))
      ? opts.sentAt : Date.now();
    opts.clientSendId=opts.clientSendId||crypto.randomUUID();
    const recoveryBubble=opts.recoveryBubble||(!opts.echoed
      ?appendMessage("founder", display || message, null, sentAt/1000):null);
    if(recoveryBubble) recoveryBubble.clientSendId=opts.clientSendId;
    // A line sent from a restore offer is a SIDE send: it must not take over
    // the in-flight record of an unconfirmed turn still on screen.
    if(!opts.keepInflight) rememberInflight(
      message, display, sentAt, opts.inputMethod, opts.modelChoice,opts.consumerRequest||null,
      opts.agentId,opts.clientSendId);
    // A relayed answer or a side send did not come from the composer: what
    // the founder is typing there stays (Codex round 3).
    const input=$("composer-input");
    if(!opts.keepComposer){ input.value=""; input.style.height="auto"; }
    const myTurn={}; activeTurn=myTurn;
    $("btn-send").disabled=true; turnStartedAt=Date.now();
    setStatusLine("Your agent is thinking...");
    renderWorking();
    let delivered=false;
    // An outcome the transport could not confirm: the turn may be running right
    // now. Nothing queued behind it is flushed automatically (see the finally).
    let unconfirmed=false;
    // Account AND home at the moment of sending. A reply or a failure that
    // lands after either changed belongs to a conversation that is no longer
    // on screen: it paints nothing, and nothing of it reaches the new
    // account's composer.
    const sentEpoch=MCP._loginEpoch, sentScope=queueScope, sentOwner=queueOwner;
    const stillHere=()=>sentEpoch===MCP._loginEpoch&&sentScope===queueScope&&
      sentOwner===queueOwner;
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
        const saved=readInflight();
        if(!saved||saved.message!==message||saved.ts!==sentAt)throw new Error('Your message could not be saved for connection.');
        InlineConnection.hold(answer.needs_connection);
        unconfirmed=true;
        return;
      }
      if(opts.returnReply&&(!answer||typeof answer.reply!=="string")){
        if(answer&&(answer.error||answer.turn_failure))throw servedFailureError(answer);
        throw new Error("voice_converse_invalid");
      }
      const beforeReply=new Set(Array.from($("thread").children));
      renderConverse(answer);
      for(const node of Array.from($("thread").children)){
        if(!beforeReply.has(node)&&String(node.className||"").includes("msg--universe"))
          node.replyToClientSendId=opts.clientSendId;
      }
      settleSteered((answer&&answer.steering)||{delivered:[],undelivered:[]});
      delivered=true;setStatusLine("");
      if(typeof AppUI!=="undefined") AppUI.turnSettled();
      if(typeof refreshAgentSwitcher==="function") refreshAgentSwitcher();
      refreshRail({foregroundAgent:opts.agentId});
      if(!opts.keepInflight) forgetInflight();
      if(opts.returnReply) return answer.reply;
    }catch(err){
      if(!stillHere()){ if(opts.returnReply) throw err; return; }
      // A turn that ended without a reply hands back every steered line. When
      // the answer never arrived at all, every steered line is queued again:
      // said twice is better than lost.
      settleSteered(err&&err.served ? (err.steering||{delivered:[],undelivered:[]}) : null);
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
        if(!opts.keepInflight)forgetInflight();
        const notice=appendFailureNotice(err.message,{speaker:"founder",text:message,display,truncated:false},undefined,err.failureCode,err.failure);
        if(notice) notice.replyToClientSendId=opts.clientSendId;
      }else{
        unconfirmed=!!(err&&err.transport)&&!err.notSent;
        offerResend(message, display, err, Object.assign({}, opts||{}, {sentAt,recoveryBubble}));
        // Unsaved or unconfirmed: the local recovery record is still needed.
      }
      if(opts.returnReply) throw err;
    }finally{
      if(stream)stream.close();
      // Cleanup is a CHANGE TO THE SCREEN and obeys the same rule as the
      // answer. Two separate questions: is this still MY composer (nothing
      // newer took it), and is this still the same account and home?
      const mine=activeTurn===myTurn;
      if(mine){
        activeTurn=null;
        // Nothing newer holds the composer, so THIS turn is what wedged it -
        // leaving it disabled would strand whoever is signed in now.
        $("btn-send").disabled=false;turnStartedAt=0;setStatusLine("");
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
        // An unconfirmed outcome is not a finished turn. Draining the queue
        // behind one sends the NEXT message into a command center that may still be
        // working on this one, on the strength of a guess the transport
        // explicitly refused to make. The lines stay queued, on disk, until
        // the user explicitly resumes them, even after another manual send.
        if(unconfirmed){ takeInterruptFlush(); if(!InlineConnection.waiting()) noteHeldQueue(); }
        else flushAfterTurn();
        Voice.conversationSettled(delivered,{message,ts:sentAt,
          consumerRequest:opts.consumerRequest||null,owner:sentOwner,scope:sentScope});
      }
    }
  }

  // Say what is being held, once, and only when there is something to hold.
  function noteHeldQueue(){
    if(!sendQueue.length) return;
    sendQueueHeld=true;
    const epoch=MCP._loginEpoch, owner=queueOwner, scope=queueScope;
    const resume=document.createElement("button");
    resume.className="btn btn--link";resume.textContent="Send queued messages";
    const note=appendMessage("system", sendQueue.length+" message"+(sendQueue.length===1?"":"s")+
      " you queued behind this one "+(sendQueue.length===1?"is":"are")+
      " being held, not sent: this turn's outcome is unknown. "+
      "Check progress before choosing to send them.",resume);
    resume.addEventListener("click",()=>{
      if(epoch!==MCP._loginEpoch||owner!==queueOwner||scope!==queueScope)return;
      if($("btn-send").disabled)return;
      note.remove();sendQueueHeld=false;flushSendQueue();
    });
  }

  async function sendVoiceTurn(message){
    // Speech must receive a reply or an explicit failure, never silently queue.
    if($("btn-send").disabled || interruptPending || serverTurnLive() || InlineConnection.waiting())
      throw new Error("voice_turn_busy");
    return sendTurn(message,message,{inputMethod:"spoken",keepComposer:true,returnReply:true});
  }

