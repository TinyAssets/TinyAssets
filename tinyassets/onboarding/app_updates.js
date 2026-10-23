// Public version policy is injected with the page. Native versions come from the shell.
const AppUpdates = (()=>{
  const $=id=>document.getElementById(id);
  const parts=value=>/^[0-9]+(?:\.[0-9]+){1,3}$/.test(value||'')
    ? value.split('.').map(Number) : null;
  function compare(a,b){
    const x=parts(a),y=parts(b);
    if(!x||!y)return null;
    for(let i=0;i<Math.max(x.length,y.length);i++){
      if((x[i]||0)!==(y[i]||0))return (x[i]||0)<(y[i]||0)?-1:1;
    }
    return 0;
  }
  function state(version,policy){
    if(!policy||!parts(policy.minimum)||!parts(policy.latest)||
       compare(policy.minimum,policy.latest)>0)return 'none';
    if(!parts(version)||compare(version,policy.minimum)<0)return 'required';
    return compare(version,policy.latest)<0?'available':'none';
  }
  let platform='',policy=null;
  async function open(){
    if(!policy)return;
    const url=policy.url;
    try{
      if(platform==='desktop'){
        if(window.tinyassetsDesktop?.openExternal)await window.tinyassetsDesktop.openExternal(url);
        else window.open(url,'_blank','noopener'); // old Electron shell's popup handoff
      }else if(platform==='android'){
        const launcher=window.Capacitor?.Plugins?.AppLauncher;
        if(launcher){
          try{
            const result=await launcher.openUrl({url:'market://details?id=io.tinyassets.app'});
            if(result?.completed)return;
          }catch(_e){}
        }
        const browser=window.Capacitor?.Plugins?.Browser;
        if(!browser)throw new Error('Browser plugin unavailable');
        await browser.open({url});
      }else if(platform==='ios'){
        const browser=window.Capacitor?.Plugins?.Browser;
        if(!browser)throw new Error('Browser plugin unavailable');
        await browser.open({url});
      }
    }catch(_e){
      $('app-update-error').textContent='Could not open the update page. Try again.';
    }
  }
  function required(){
    $('app-update-banner').hidden=true;
    $('app-update-required').hidden=false;
    $('app').inert=true;
    $('app-update-required-open').onclick=open;
    $('app-update-required-open').focus();
  }
  function requireDesktopUpdate(versions){
    platform='desktop';policy=versions?.desktop;
    if(policy)required();
  }
  async function init(versions){
    const native=window.Capacitor?.isNativePlatform?.();
    const desktop=!!window.tinyassetsDesktop||/(?:Electron\/|tinyassets-desktop\/)/.test(navigator.userAgent||'');
    if(!native&&!desktop)return true;
    platform=native?window.Capacitor.getPlatform():'desktop';
    policy=versions?.[platform];
    if(!policy)return true;
    let version='';
    if(native){
      try{ version=(await window.Capacitor.Plugins.App.getInfo()).version; }catch(_e){}
    }else{
      try{ version=await window.tinyassetsDesktop?.getVersion?.(); }catch(_e){}
      version=version||(/tinyassets-desktop\/([0-9.]+)/.exec(navigator.userAgent||'')||[])[1]||'';
      // The 0.1.1 preload has openExternal but predates getVersion and the UA stamp.
      if(!version&&window.tinyassetsDesktop?.openExternal)version='0.1.1';
    }
    const prompt=!native&&!window.tinyassetsDesktop?'required':state(version,policy);
    if(prompt==='required'){required();return false;}
    if(prompt==='available'){
      const key=`ta_update_dismissed_${platform}`;
      if(localStorage.getItem(key)!==policy.latest){
        $('app-update-banner').hidden=false;
        $('app-update-banner-open').onclick=open;
        $('app-update-dismiss').onclick=()=>{
          localStorage.setItem(key,policy.latest);$('app-update-banner').hidden=true;
        };
      }
    }
    return true;
  }
  return {compare,state,init,requireDesktopUpdate};
})();
