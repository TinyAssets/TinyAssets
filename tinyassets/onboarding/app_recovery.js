// Deliberately a separate classic script: a parse/boot error in the app must
// never remove the escape controls. Inspired by tiny's early listener (#4481).
window.AppRecovery=(()=>{
  "use strict";
  const cfg=__TA_ONBOARDING_CONFIG__, key="ta_app_recovery", draftKey="ta_recovery_draft";
  const node=id=>document.getElementById(id);
  let hooks=null, booted=false, failed=false, frameTried=false, timer=null, leaving=false;
  let badSince=0, goodSince=0, probing=false;
  const read=()=>{try{return JSON.parse(sessionStorage.getItem(key))||{attempts:0,next:0};}
    catch(_e){return {attempts:2,next:0};}};
  function notice(){node("app-recovery-failed").hidden=false;}
  function saveDraft(){
    const input=node("composer-input");
    if(!input||(!input.value&&!leaving)) return true;
    try{
      const scope=hooks&&hooks.scope();
      if(!scope||!scope.owner||!scope.home) return false;
      if(!input.value){
        const saved=JSON.parse(sessionStorage.getItem(draftKey));
        if(saved&&saved.owner===scope.owner&&saved.home===scope.home&&saved.agent===scope.agent)
          sessionStorage.removeItem(draftKey);
        return true;
      }
      sessionStorage.setItem(draftKey,JSON.stringify({...scope,text:input.value}));
      return true;
    }catch(_e){return false;}
  }
  function restoreDraft(){
    // Navigation is asynchronous. The departing page must not consume the
    // record it just saved for its successor while waiting for the response.
    if(leaving) return;
    try{
      const saved=JSON.parse(sessionStorage.getItem(draftKey)),scope=hooks&&hooks.scope();
      if(!saved||!scope||!scope.owner||!scope.home) return;
      if(saved.owner!==scope.owner||saved.home!==scope.home||saved.agent!==scope.agent) return;
      const input=node("composer-input");
      if(!input.value){input.value=saved.text;input.dispatchEvent(new Event("input",{bubbles:true}));}
      // A newer draft wins. Never send anything automatically.
      sessionStorage.removeItem(draftKey);
    }catch(_e){}
  }
  function navigate(manual=false){
    if(leaving) return;
    const saved=saveDraft();
    if(!saved&&!manual){notice();return;}
    leaving=true;
    if(manual){try{sessionStorage.removeItem(key);}catch(_e){}}
    const url=new URL(location.href);
    url.searchParams.set("_ta_recover",String(Date.now()));
    location.replace(url.href);
  }
  function pageRetry(){
    if(timer) return;
    const state=read();
    if(state.attempts>=2){notice();return;}
    const delay=Math.max(2000*Math.pow(4,state.attempts),state.next-Date.now());
    try{sessionStorage.setItem(key,JSON.stringify({attempts:state.attempts+1,next:Date.now()+delay}));}
    catch(_e){notice();return;}
    timer=setTimeout(()=>{timer=null;navigate();},delay);
  }
  function fail(error){
    if(error) console.error("Command center recovery",error);
    failed=true;goodSince=0;notice();
    if(!frameTried&&hooks){
      frameTried=true;badSince=Date.now();
      try{if(hooks.retryFrame()) return;}catch(err){console.error("Frame retry failed",err);}
    }
    pageRetry();
  }
  function health(){
    try{return hooks?hooks.health():{expected:false,healthy:false};}
    catch(_e){return {expected:true,healthy:false};}
  }
  function tick(){
    // A reload starts at the main agent. Keep a different agent's draft private
    // until the owner selects that agent again, then offer it without sending.
    if(booted)restoreDraft();
    const state=health(),now=Date.now();
    if((!booted||state.expected)&&!state.healthy){
      goodSince=0;
      if(!badSince)badSince=now;
      if(now-badSince>=(state.timeout||15000)){badSince=now;fail();}
    }else{
      badSince=0;
      if(!goodSince)goodSince=now;
      if(now-goodSince>=30000){
        failed=false;frameTried=false;node("app-recovery-failed").hidden=true;
        if(timer){clearTimeout(timer);timer=null;}
        try{sessionStorage.removeItem(key);}catch(_e){}
      }
    }
  }
  async function checkBuild(){
    if((!cfg.build&&!cfg.shell_version)||probing) return;
    probing=true;
    try{
      const response=await fetch("/app",{method:"HEAD",cache:"no-store",credentials:"omit",
        signal:AbortSignal.timeout(10000)});
      const live=response.headers.get("X-TinyAssets-Build");
      const shell=response.headers.get("X-TinyAssets-Shell");
      // A broken page never consults the turn/typing hold. The durable turn
      // record already belongs to the app; save only the unsent draft here.
      if(response.ok&&((live&&live!==cfg.build)||
         (cfg.shell_version&&shell&&shell!==cfg.shell_version))){
        const state=health();
        if(booted&&!failed&&(!state.expected||state.healthy)&&hooks&&hooks.holdUpdate())return;
        notice();
        if(!frameTried&&hooks){
          frameTried=true;
          try{hooks.retryFrame();}catch(err){console.error("Frame update failed",err);}
        }
        pageRetry();
      }
    }catch(_e){/* offline: the next probe retries */}
    finally{probing=false;}
  }
  // Sign-out must work in every state, including an app that never booted, so
  // it needs nothing from the main script. The same logout grant the app uses
  // drops the renewable session; if it cannot be reached, the pending mark
  // makes the next start finish the sign-out before renewing anything.
  async function logout(){
    if(leaving) return;
    leaving=true;
    // Before any wait: a native sign-in queued behind the renewal lock re-reads
    // these and stands down instead of minting a new session.
    try{localStorage.setItem("ta_logout_pending","1");}catch(_e){}
    try{sessionStorage.removeItem("ta_pkce");localStorage.removeItem("ta_pkce");}catch(_e){}
    let ok=false;
    const end=async()=>{
      try{
        let ref="";try{ref=localStorage.getItem("ta_sref")||"";}catch(_e){}
        const response=await fetch("/app/token",{method:"POST",credentials:"same-origin",
          headers:{"Content-Type":"application/json"},
          body:JSON.stringify({grant_type:"logout",session_ref:ref||undefined}),
          signal:AbortSignal.timeout(10000)});
        ok=response.ok;
      }catch(_e){}
    };
    // The app's renewal lock (main.js withRefreshLock): a renewal already running
    // in another tab finishes first, so it cannot re-issue the session afterwards.
    try{
      if(navigator.locks&&navigator.locks.request)
        await Promise.race([navigator.locks.request("ta-session-refresh",end),
                            new Promise(resolve=>setTimeout(resolve,15000))]);
      else await end();
    }catch(_e){}
    try{
      sessionStorage.removeItem("ta_access_token");sessionStorage.removeItem("ta_access_exp");
      sessionStorage.removeItem(key);sessionStorage.removeItem(draftKey);
      if(ok){localStorage.removeItem("ta_sref");localStorage.removeItem("ta_logout_pending");}
    }catch(_e){}
    // Bounded: a plugin or service worker that never answers must not strand
    // the page, since `leaving` already swallows every other recovery control.
    await Promise.race([silenceNotifications().catch(()=>{}),
                        new Promise(resolve=>setTimeout(resolve,3000))]);
    location.replace("/app");
  }
  // The same teardown as the app's sign-out (main.js enterSignedOut): this
  // device stops receiving and showing the previous owner's notifications.
  async function silenceNotifications(){
    const plugins=window.Capacitor&&window.Capacitor.Plugins;
    const native=!!(window.Capacitor&&typeof window.Capacitor.isNativePlatform==="function"
      &&window.Capacitor.isNativePlatform());
    if(native&&plugins){
      try{["app.push.fcm","app.push.owner","app.push.recipient"].forEach(k=>localStorage.removeItem(k));}
      catch(_e){}
      const reply=plugins.NotificationReply,push=plugins.PushNotifications;
      try{if(reply){await reply.setActive({active:false,recipient:""});await reply.consume();}}catch(_e){}
      try{if(push){await push.unregister();await push.removeAllDeliveredNotifications();}}catch(_e){}
      return;
    }
    try{
      if(!navigator.serviceWorker) return;
      const registration=await navigator.serviceWorker.getRegistration("/app");
      if(!registration) return;
      const subscription=await registration.pushManager.getSubscription();
      if(subscription) await subscription.unsubscribe();
      (await registration.getNotifications()).forEach(n=>n.close());
    }catch(_e){}
  }
  document.addEventListener("click",event=>{
    const target=event.target.closest&&event.target.closest("[data-app-recovery]");
    if(!target) return;
    event.preventDefault();event.stopImmediatePropagation();
    const action=target.dataset.appRecovery;
    if(action==="reload"){navigate(true);return;}
    if(action==="logout"){logout();return;}
    try{
      if(action==="chat"&&hooks){hooks.openChat();return;}
      if(action==="browse"&&hooks&&hooks.browse())return;
      if(action==="refresh"&&hooks&&hooks.retryFrame()){frameTried=true;badSince=Date.now();return;}
    }catch(err){console.error("Recovery control",err);}
    navigate(true);
  },true);
  // Script load/parse failures occur outside boot()'s promise.
  window.addEventListener("error",event=>{
    if(event.target?.tagName==="SCRIPT"||!booted)fail(event.error||"App script failed");
  },true);
  window.addEventListener("unhandledrejection",event=>{if(!booted)fail(event.reason);});
  setInterval(tick,1000);
  setInterval(checkBuild,60000);
  window.addEventListener("online",checkBuild);
  window.addEventListener("focus",checkBuild);
  // The old document stays editable until navigation commits. Capture its
  // latest text before the main page's pagehide handlers retire voice/input.
  window.addEventListener("pagehide",()=>{if(leaving)saveDraft();});
  return {attach(value){hooks=value;},started(){booted=true;restoreDraft();},
    fail,restoreDraft,checkBuild,upgrade:()=>navigate(),broken:()=>failed};
})();
