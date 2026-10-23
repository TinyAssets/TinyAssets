// Capture and playback only. Turns are committed transcripts on the owner session.
const RealtimeCapture = {
  admitBeforeCapture(doc, media){
    const contract = doc && doc.contract && doc.contract.realtime;
    const spec = contract && contract[media];
    const allowed = spec && Array.isArray(spec.in) && spec.in.length > 0;
    if(!doc || doc.admitted === false || doc.gap || !allowed)
      return {ok:false, gap:(doc && (doc.gap || doc.reason)) || "unsupported"};
    return {ok:true, media:media};
  },
  stopTracks(tracks){
    const list = Array.isArray(tracks) ? tracks : [];
    for(const track of list){
      try{ if(track && typeof track.stop === "function") track.stop(); }catch(_error){}
    }
    return list.length;
  },
  inputHistory(history, transcript){
    const prior = Array.isArray(history) ? history.slice() : [];
    prior.push({text:String(transcript || "")});
    return prior;
  },
  disconnect(sent){
    return {side_effect_state: sent ? "unknown" : "none", resend:false};
  },
  bargeIn(activeTurn){
    return {cancel: activeTurn || null};
  }
};

async function commitRealtimeTurn(message){
  await ensureFreshToken();
  const options = {method:"POST", credentials:"same-origin",
    headers:Object.assign({"Content-Type":"application/json"}, authHeaders()),
    body:JSON.stringify({media:"audio", turn:message})};
  let response = await fetch("/app/session/realtime", options);
  if(response.status === 401 && await refreshAccessToken()){
    options.headers = Object.assign({"Content-Type":"application/json"}, authHeaders());
    response = await fetch("/app/session/realtime", options);
  }
  let doc = {};
  try{ doc = await response.json(); }catch(_error){}
  if(!response.ok) throw new Error(doc.error || doc.gap || "session_turn_failed");
  if(doc.outcome === "unknown") throw new Error("session_outcome_unknown");
  return typeof doc.reply === "string" ? doc.reply : "";
}
