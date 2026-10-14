// Generated credentials stay in this card, never in railCache or chat state.
const CredentialRequests = {
  cards: new Map(),
  management: 0,
  clear(){
    this.management++;
    $('api-keys-list')?.replaceChildren();
    if($('api-keys-status'))$('api-keys-status').textContent='';
    for(const dispose of this.cards.values())dispose();
    this.cards.clear();
  },
  async list(){
    const generation=++this.management,epoch=MCP._loginEpoch,owner=queueOwner,view=viewGeneration;
    const live=()=>generation===this.management&&epoch===MCP._loginEpoch&&owner===queueOwner&&view===viewGeneration;
    const host=$('api-keys-list'),status=$('api-keys-status');host.replaceChildren();
    status.textContent='Loading API keys.';
    const send=async body=>{
      const response=await fetch('/app/api-keys',{method:'POST',credentials:'same-origin',cache:'no-store',
        redirect:'error',referrerPolicy:'no-referrer',headers:{...authHeaders(),'Content-Type':'application/json'},
        body:JSON.stringify(body)});
      if(!response.ok)throw new Error('refused');return response.json();
    };
    try{
      const doc=await send({operation:'owner_list'});if(!live())return;
      status.textContent=doc.keys.length?'Secrets cannot be displayed again.':'No API keys yet.';
      for(const key of doc.keys){
        const row=document.createElement('div'),label=document.createElement('p');
        const scope=key.scopes.map(s=>s.universe+' / '+s.agent+' / '+s.capability).join('; ');
        label.textContent=key.name+' — '+(key.revoked_at!==null?'Revoked':'Active')+' — '+scope+
          (key.last_used_at?' — Last used '+new Date(key.last_used_at*1000).toLocaleString():' — Never used');
        row.appendChild(label);host.appendChild(row);
        if(key.revoked_at!==null)continue;
        let busy=false;
        const reveal=document.createElement('button');reveal.className='btn';reveal.type='button';reveal.textContent='Revoke '+key.name;
        const confirm=document.createElement('div');confirm.hidden=true;confirm.textContent='Revoke this key and remove its API access?';
        const yes=document.createElement('button'),no=document.createElement('button');
        for(const b of [yes,no]){b.className='btn';b.type='button';}
        yes.textContent='Confirm revoke';no.textContent='Keep key';
        reveal.onclick=()=>{if(live()){confirm.hidden=false;yes.focus();}};
        no.onclick=()=>{confirm.hidden=true;};
        yes.onclick=async()=>{
          if(busy||!live())return;busy=true;yes.disabled=no.disabled=reveal.disabled=true;
          try{await send({operation:'owner_revoke',key_id:key.key_id});if(live())await this.list();}
          catch(_e){if(live())status.textContent='Revocation is not confirmed. Review API keys again before taking another action.';}
        };
        confirm.append(yes,no);row.append(reveal,confirm);
      }
    }catch(_e){if(live()){
      status.textContent='Could not load keys. Sign in with the same account, then review again. ';
      const link=document.createElement('a');link.href='/app/owner-sign-in';link.target='_blank';link.rel='noopener noreferrer';
      link.textContent='Sign in';status.appendChild(link);
    }}
  },
  build(req){
    const box=document.createElement('div');box.className='rtab-body';
    const epoch=MCP._loginEpoch,owner=queueOwner,home=queueScope,view=viewGeneration;
    let active=true,busy=false,preview=null,created=false;
    const current=()=>active&&epoch===MCP._loginEpoch&&owner===queueOwner&&home===queueScope&&
      view===viewGeneration&&box.isConnected&&RequestSheets.selected===req.request_id&&
      $('request-rail').open;
    const add=(tag,text)=>{const el=document.createElement(tag);el.textContent=text;box.appendChild(el);return el;};
    add('p','Connect your external agent');
    const summary=add('p','Loading the protected request.');
    add('p','This key works with the REST API. MCP uses OAuth. Creating a key does not set up background updates or delivery.');
    const setup=add('pre','');setup.style.whiteSpace='pre-wrap';setup.style.overflowWrap='anywhere';
    const consent=add('label','');consent.className='rtab-label';
    const check=document.createElement('input');check.type='checkbox';
    consent.append(check,document.createTextNode(' I approve this agent and the access shown above.'));
    const secretLabel=add('label','One-time API key');secretLabel.className='rtab-label';secretLabel.hidden=true;
    const secret=document.createElement('input');secret.type='password';secret.readOnly=true;
    secret.autocomplete='off';secret.spellcheck=false;secret.setAttribute('aria-label','One-time API key');
    secretLabel.appendChild(secret);
    const note=add('p','');note.setAttribute('role','status');
    const signIn=add('a','Sign in to review this request');signIn.href='/app/owner-sign-in';
    signIn.target='_blank';signIn.rel='noopener noreferrer';signIn.hidden=true;
    const controls=add('div','');controls.className='rtab-row';
    const buttons={};
    const button=(id,text,fn)=>{const b=document.createElement('button');b.type='button';b.className='btn';
      b.textContent=text;b.onclick=fn;controls.appendChild(b);buttons[id]=b;return b;};
    function paint(){
      const existing=preview?.existing;
      buttons.create.disabled=busy||!preview||preview.status!=='pending'||!!existing||created||!check.checked;
      buttons.refresh.disabled=busy;check.disabled=busy||!!existing||created||!preview;
      consent.hidden=!!existing||created;
      buttons.copy.hidden=buttons.show.hidden=!secret.value;
      buttons.copy.disabled=buttons.show.disabled=busy;
      buttons.finish.hidden=!existing&&!created;buttons.finish.disabled=busy;
      buttons.revoke.hidden=!existing||existing.revoked_at!==null;buttons.revoke.disabled=busy;
      buttons.decline.hidden=!!existing||created;buttons.decline.disabled=busy||!preview;
      buttons.cancel.disabled=busy;
      buttons.cancel.textContent=existing?.revoked_at!=null?'Close':existing||created?'Close (key stays active)':'Not now';
    }
    async function post(operation){
      const response=await fetch('/app/api-keys',{method:'POST',credentials:'same-origin',
        cache:'no-store',referrerPolicy:'no-referrer',redirect:'error',
        headers:{...authHeaders(),'Content-Type':'application/json'},
        body:JSON.stringify({operation:'request_'+operation,universe_id:req.universe_id||home,request_id:req.request_id,
          ...(operation==='create'?{expected_digest:preview.digest}:{})})});
      if(!response.ok){const error=new Error('refused');error.signIn=response.status===401||response.status===403;throw error;}
      const doc=await response.json();if(doc.error)throw new Error('refused');return doc;
    }
    function describe(doc){
      const a=doc.action,caps=new Set(a.scopes.map(s=>s.capability)),center=a.scopes[0].universe;
      const operations=[];
      if(caps.has('read_graph'))operations.push('read the command center, status, connections and runs');
      if(caps.has('converse'))operations.push('send messages to its primary agent');
      if(caps.has('run_graph'))operations.push('start and cancel runs (which may use paid connected services)');
      summary.textContent=a.name+' will be able to '+operations.join('; ')+'. '+
        (caps.size===3?'Full supported REST access. ':'Selected REST access. ')+
        'Command center: '+center+'. Target agent: main (your primary in-app agent).';
      const base=new URL('/api',location.origin).href;
      setup.textContent='API base: '+base+'\nCommand center: '+center+
        '\nAuthentication: Authorization: Bearer <your API key>\n'+
        (caps.has('read_graph')?'Read status: GET '+base+'/command-centers/'+encodeURIComponent(center)+'/status\n':'')+
        (caps.has('converse')?'Send message: POST '+base+'/command-centers/'+encodeURIComponent(center)+
          '/messages\nJSON body: {"message":"Hello"}\n':'')+
        (caps.has('run_graph')?'Start a run: POST '+base+'/command-centers/'+encodeURIComponent(center)+
          '/runs\nJSON body: {"branch_def_id":"<existing branch id>","inputs":{}}\n':'')+
        'Save the key in your external agent’s secret settings. Never paste it into chat.';
    }
    async function perform(operation){
      if(busy||!current())return;
      if(operation==='create'&&buttons.create.disabled)return;
      busy=true;signIn.hidden=true;paint();
      if(operation==='create')note.textContent='Creating once. If interrupted, review this same request again.';
      try{
        const doc=await post(operation);
        if(!current())return;
        if(operation==='preview'){
          preview=doc;check.checked=false;describe(doc);
          if(doc.existing?.revoked_at!=null){secret.value='';secretLabel.hidden=true;}
          note.textContent=secret.value?'Your key is still shown in this open card. Copy it before closing.':doc.existing?(doc.existing.revoked_at!==null?'This key has been revoked. Finish this request; ask your agent for a new one if needed.':
            'This key already exists. Its secret cannot be shown again. If you did not save it, revoke it and ask your agent for a new key.'):
            'No key has been created. Review the access and confirm to create it.';
        }else if(operation==='create'){
          created=true;check.checked=false;
          preview.existing={key_id:doc.key_id,revoked_at:null};
          if(typeof doc.key==='string'&&doc.key){
            secret.value=doc.key;secretLabel.hidden=false;
            note.textContent='Key created. Copy it now: closing this card clears it permanently. Your agent cannot see it.';
          }else note.textContent='This request already created a key. The secret cannot be recovered; revoke it if you did not save it.';
        }else if(operation==='revoke'){
          secret.value='';secretLabel.hidden=true;preview.existing.revoked_at=1;
          note.textContent='Key revoked. It can no longer authenticate new API requests.';
        }else if(operation==='finish'||operation==='decline'){
          CredentialRequests.clear();$('request-rail').close();railOpen=null;await refreshRail();
        }
      }catch(error){
        if(!current())return;
        preview=null;check.checked=false;
        note.textContent=operation==='create'?'Creation is not confirmed. Do not create a replacement. Use Check status to find this request’s key.':
          'Could not confirm the result. Sign in with the same account if needed, then Check status. No automatic retry was sent.';
        signIn.hidden=!error.signIn;
      }finally{busy=false;if(current())paint();}
    }
    button('create','Create API key',()=>perform('create')).classList.add('btn--primary');
    button('refresh','Check status',()=>perform('preview'));
    button('copy','Copy key',async()=>{
      if(!current()||!secret.value)return;
      try{await navigator.clipboard.writeText(secret.value);if(current())note.textContent='Copied. Store it securely, then select Done.';}
      catch(_e){if(current()){secret.type='text';secret.focus();secret.select();note.textContent='Clipboard unavailable. Select and copy the key here.';}}
    });
    button('show','Show key',()=>{secret.type=secret.type==='password'?'text':'password';buttons.show.textContent=secret.type==='password'?'Show key':'Hide key';});
    button('revoke','Revoke key',()=>{
      if(busy||!current())return;
      revokeConfirm.hidden=false;revokeApprove.focus();
    });
    const revokeConfirm=add('div','Revoke this key? The external agent will lose this API access.');revokeConfirm.hidden=true;
    const revokeApprove=document.createElement('button');revokeApprove.type='button';revokeApprove.className='btn';
    revokeApprove.textContent='Confirm revoke';revokeApprove.onclick=()=>{revokeConfirm.hidden=true;perform('revoke');};
    const revokeCancel=document.createElement('button');revokeCancel.type='button';revokeCancel.className='btn';
    revokeCancel.textContent='Keep key';revokeCancel.onclick=()=>{revokeConfirm.hidden=true;};
    revokeConfirm.append(revokeApprove,revokeCancel);
    button('finish','Done',()=>perform('finish'));
    button('decline','Decline request',()=>perform('decline'));
    button('cancel','Not now',()=>{$('request-rail').close();railOpen=null;CredentialRequests.clear();});
    check.onchange=paint;
    this.cards.set(req.request_id,()=>{
      active=false;secret.value='';box.remove();
      const entry=railNodes.get(req.request_id);if(entry)entry.body=false;
    });
    paint();queueMicrotask(()=>perform('preview'));
    return box;
  }
};
window.addEventListener('pagehide',()=>CredentialRequests.clear());
$('request-rail').addEventListener('close',()=>CredentialRequests.clear());
if($('api-keys-refresh'))$('api-keys-refresh').onclick=()=>CredentialRequests.list();
