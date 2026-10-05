// Deliberately a separate classic script: a parse/boot error in the app must
// never remove the escape controls. Inspired by tiny's early listener (#4481).
window.AppRecovery=(()=>{
  "use strict";
  const cfg=__TA_ONBOARDING_CONFIG__, key="ta_app_recovery", draftKey="ta_recovery_draft";
  const node=id=>document.getElementById(id);
  let hooks=null, booted=false, failed=false, frameTried=false, timer=null;
  let badSince=0, goodSince=0, probing=false;
  const read=()=>{try{return JSON.parse(sessionStorage.getItem(key))||{attempts:0,next:0};}
    catch(_e){return {attempts:2,next:0};}};
  function notice(){node("app-recovery-failed").hidden=false;}
  function saveDraft(){
    const input=node("composer-input");
    if(!input||!input.value) return true;
    try{
      const scope=hooks&&hooks.scope();
      if(!scope||!scope.owner||!scope.home) return false;
      sessionStorage.setItem(draftKey,JSON.stringify({...scope,text:input.value}));
      return true;
    }catch(_e){return false;}
  }
  function restoreDraft(){
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
    const saved=saveDraft();
    if(!saved&&!manual){notice();return;}
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
    const state=health(),now=Date.now();
    if((!booted||state.expected)&&!state.healthy){
      goodSince=0;
      if(!badSince)badSince=now;
      if(now-badSince>=15000){badSince=now;fail();}
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
    if(!cfg.build||probing) return;
    probing=true;
    try{
      const response=await fetch("/app",{method:"HEAD",cache:"no-store",credentials:"omit",
        signal:AbortSignal.timeout(10000)});
      const live=response.headers.get("X-TinyAssets-Build");
      // A broken page never consults the turn/typing hold. The durable turn
      // record already belongs to the app; save only the unsent draft here.
      if(response.ok&&live&&live!==cfg.build){
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
  document.addEventListener("click",event=>{
    const target=event.target.closest&&event.target.closest("[data-app-recovery]");
    if(!target) return;
    event.preventDefault();event.stopImmediatePropagation();
    const action=target.dataset.appRecovery;
    if(action==="reload"){navigate(true);return;}
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
  return {attach(value){hooks=value;},started(){booted=true;restoreDraft();},
    fail,restoreDraft,checkBuild,broken:()=>failed};
})();
