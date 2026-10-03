  // ---- Custom command center UI: isolated renderer + closed bridge ----------------
  // A command center can hold executable UI bundles (`tinyassets.app-ui.v1`) its own
  // agent writes, and this renders them. A bundle is somebody's arbitrary code —
  // usually somebody the viewer has never met, because bundles are shared by
  // publish/remix — so nothing here sanitizes it. It runs in the sandboxed,
  // opaque-origin document `/app/ui-frame` serves (see ui_frame.py for the
  // policy), which owns no storage, no cookies and no network of its own.
  //
  // Everything the bundle can do is in ACTIONS below and nowhere else. Each
  // handler builds its own tool arguments and pins the command center to the VIEWING
  // user's current home, so a bundle cannot name a command center: cross-user reach is
  // not refused by a check, it is unrepresentable. Replies are assembled from
  // picked fields, never spread from a server payload, so a field added upstream
  // later cannot ride out to untrusted code.
  //
  // Storage is the viewer's own `app_ui` row (read_graph/write_graph
  // target="app_ui"): the bundles in `ui_library` and the choice in
  // `ui_selection`, one row per person and command center, keyed server-side by who is
  // signed in. It is not an agent binding and never appears to a binding reader.
  // Every write is compare-and-set on the row's revision.
  const AppUI={
    KIND:"tinyassets.app-ui.v1",VERSION:1,PROTOCOL:1,
    SHELL_KIND:"tinyassets.app-experience-shell.v1",
    FRAME_SRC:"/app/ui-frame",
    // No `allow-same-origin`: that single word is the whole isolation boundary.
    // With it the frame would share this page's origin and could read
    // `sessionStorage` (the access token), `localStorage` and the parent DOM.
    // The frame's own response header sandboxes it too, so this is the second of
    // two independent locks, not the only one.
    // `allow-forms` lets a bundle's <form> fire its submit event, which a bundle
    // handles in script. Without it the browser drops the submit silently and the
    // button does nothing. The frame's `form-action 'none'` still refuses every
    // real submission, so no form can navigate or send anything anywhere.
    SANDBOX:"allow-scripts allow-forms",
    // Per-UI bounds only, the same numbers the server enforces
    // (custom_agents.APP_UI_MAX_*). There is NO bound on the library as a whole --
    // neither a count of UIs nor a byte total: those bytes are the command center's
    // tier storage, one of the two limits an account has (founder 2026-09-30).
    // The 49,152-byte bundle bound these replace made a game impossible
    // (founder's village, 2026-10-02). Sizes are UTF-8 BYTES, because that is
    // what the server validates (Codex, 2026-09-26).
    MAX_TEXT_BYTES:1048576,MAX_ASSET_FILES:500,MAX_ASSET_BYTES:16777216,MAX_UI_ASSET_BYTES:134217728,
    MAX_NAME:120,MAX_MESSAGE:8192,MAX_READ_TURNS:50,
    MAX_LIST_RUNS:50,MAX_OUTPUT_CHUNK:8192,MAX_ID:200,MAX_PATH:1024,MAX_FILE_CHUNK:65536,
    // The first page of a whole-list read; see readWhole.
    PAGE:100,
    // The conversation design: which published conversation component handles
    // this owner's future messages. It is the receiver's own non-serving agent
    // binding (`configuration.role` = ROLE, choice in `turn_consumer`), which the
    // server reads at turn admission (consumer_selection.py). A UI may ASK to
    // change it; the person approves in this page's own chrome, never in the UI.
    ROLE:"app_experience",TURN_KIND:"tinyassets.turn-graph.v1",
    ID_RE:/^[a-z0-9][a-z0-9-]{0,63}$/,
    // The blank command center the platform ships. Its ui_id carries a colon,
    // which ID_RE forbids, so no UI a person can author or install may claim
    // it -- that is what makes it an identity rather than a convention.
    PLATFORM_UI_ID:"platform:blank",
    // Actions only that bundle may ask for. Installing software and composing a
    // message as the owner are the app's offer to them, not a third-party
    // bundle's capability. `packages.list_tryable` is absent on purpose: it
    // only reads what is already published.
    PLATFORM_ONLY:["packages.try","chat.prefill"],
    FIELDS:["kind","markup","name","script","style","ui_id","version"],
    // Carried verbatim when present: the asset manifest the server checked,
    // shared libraries by name, and whether `script` is a module.
    OPTIONAL:["assets","libraries","script_type","workflow_refs"],
    SHA256_RE:/^[0-9a-f]{64}$/,
    ASSET_PATH_RE:/^[A-Za-z0-9][A-Za-z0-9._-]*(\/[A-Za-z0-9][A-Za-z0-9._-]*)*$/,
    ASSET_FETCH:"/app/api/ui-asset",
    // The vendored libraries and their SHA-384 pins, the same table as
    // tinyassets/onboarding/ui_libraries.json (a test holds them equal). A
    // library whose bytes do not match is refused, never posted to the frame.
    LIBRARIES:Object.freeze({"howler":{format:"global",sha384:"sha384-SSf4pKRrGaeWL8bdA89QvGkhZo5WvIVCGwKVzfX7z+9sSaIHtE/AkOLehofI4JVY",requires:[]},"phaser":{format:"global",sha384:"sha384-AvQiDMZAVLda3VtAoU5MCfBz8pzXhteb2CiUJeKBmPlWzpXj1uJ96Km11+YuFNu/",requires:[]},"pixi.js":{format:"global",sha384:"sha384-sQhAUuZTvdanRcBHbVCTasnEWH28GGEK87FJGPj/JaqwL/15KWQwKe7whTHdCkwm",requires:[]},"three":{format:"module",sha384:"sha384-IDC7sAMAIMB/TZ6dgKKPPAKZ2bXXXP8+FBMBC8cU319eBhKITx+PaalhfDkDNH28",requires:[]},"three/addons/controls/OrbitControls.js":{format:"module",sha384:"sha384-aJoe4qqS/DgF2jh9njAuvA6QIveJYoCuYOfYjdFY8P3eawzmc9bEQ40jq2TvOyuP",requires:["three"]},"three/addons/loaders/GLTFLoader.js":{format:"module",sha384:"sha384-x79xjCNsFlRByL5E+VmRg4w6ppPmAVfP11fg7GzEVJ0wT+wuVfARjjZNLsq7iUmq",requires:["three", "three/addons/utils/BufferGeometryUtils.js"]},"three/addons/utils/BufferGeometryUtils.js":{format:"module",sha384:"sha384-wOjwauvHlJO7K6APr7FmMGH2nupQa3Ndzas9bJZhsAMOK055efJud8ns4aYmASKv",requires:["three"]}}),
    libCache:new Map(),

    epoch:0,home:"",principal:"",enabled:false,busy:false,
    // `library` is the bundles this app can render; `broken` is one record per
    // stored entry it cannot, each keeping the component VERBATIM so an install
    // puts it back untouched. `unreadable` is reserved for a library that is not
    // a list at all -- the only failure that really is library-wide.
    library:[],broken:[],unreadable:"",selection:null,active:null,frame:null,listener:null,platformDefault:null,
    // The conversation installation as last read (null: none, so default), the
    // reason it could not be read, and the selection it replaced this visit.
    conversation:null,conversationNote:"",previousTurn:null,selecting:false,ambiguous:false,
    // The stored row's revision as last read; 0 means no row exists yet.
    revision:0,
    // Bumped on every mount AND unmount. A request captures it, so a reply owed
    // to the bundle that was on screen a moment ago cannot settle a promise in
    // the one that replaced it -- both bootstraps number requests from r1, so the
    // ids collide by construction (Codex, 2026-09-26).
    frameGen:0,ready:false,sending:false,emitting:false,trying:false,pending:0,
    // True only while mountDefault's own bundle is on screen (isPlatformDefault).
    defaultMounted:false,

    bytes(value){ return new TextEncoder().encode(String(value)).length; },

    // ---- component reader (pure; no DOM, no network, no sanitizing) ---------
    unsupported(reason){ return {ok:false,reason}; },
    text(value,limit){ return typeof value==="string"&&value.length<=limit; },
    parseBundle(component){
      if(!component||typeof component!=="object"||Array.isArray(component))
        return this.unsupported("UI component is not an object");
      const keys=Object.keys(component).sort();
      const extra=keys.filter(k=>!this.FIELDS.includes(k)&&!this.OPTIONAL.includes(k));
      if(extra.length) return this.unsupported("UI component carries fields this app does not render: "+extra.join(", "));
      const missing=this.FIELDS.filter(k=>!keys.includes(k));
      if(missing.length) return this.unsupported("UI component is missing "+missing.join(", "));
      if(component.kind!==this.KIND) return this.unsupported("not a "+this.KIND+" component");
      if(component.version!==this.VERSION)
        return this.unsupported("UI version "+String(component.version)+" is not supported; this app renders version 1");
      if(!this.text(component.ui_id,64)||(!this.ID_RE.test(component.ui_id)&&component.ui_id!=="platform:blank"))
        return this.unsupported("ui_id must be lowercase letters, digits or dashes");
      if(!this.text(component.name,this.MAX_NAME)||!component.name.trim())
        return this.unsupported("name must be a non-empty string of at most "+this.MAX_NAME+" characters");
      for(const field of ["markup","style","script"])
        if(typeof component[field]!=="string") return this.unsupported(field+" must be a string");
      const size=this.bytes(JSON.stringify(component));
      if(size>this.MAX_TEXT_BYTES)
        return this.unsupported("this UI is "+size+" bytes of text; the limit is "+this.MAX_TEXT_BYTES);
      if("script_type" in component&&component.script_type!=="classic"&&component.script_type!=="module")
        return this.unsupported("script_type must be classic or module");
      if("workflow_refs" in component){
        const refs=component.workflow_refs;
        if(!refs||typeof refs!=="object"||Array.isArray(refs)||Object.keys(refs).length>100)
          return this.unsupported("workflow_refs must be an object of at most 100 references");
        for(const [alias,id] of Object.entries(refs))
          if(!/^[A-Za-z][A-Za-z0-9_-]{0,63}$/.test(alias)||
             typeof id!=="string"||!id||id.length>200)
            return this.unsupported("workflow_refs contains an invalid alias or workflow id");
      }
      if("libraries" in component){
        const libs=component.libraries;
        if(!Array.isArray(libs)) return this.unsupported("libraries must be a list");
        for(const name of libs)
          if(typeof name!=="string"||!Object.prototype.hasOwnProperty.call(this.LIBRARIES,name))
            return this.unsupported("library "+String(name)+" is not one this app provides");
        if(new Set(libs).size!==libs.length) return this.unsupported("a library is listed twice");
      }
      if("assets" in component){
        const assets=component.assets;
        if(!assets||typeof assets!=="object"||Array.isArray(assets)) return this.unsupported("assets must be an object");
        const paths=Object.keys(assets);
        if(paths.length>this.MAX_ASSET_FILES) return this.unsupported("this UI has more than "+this.MAX_ASSET_FILES+" assets");
        let total=0;
        for(const path of paths){
          const ref=assets[path];
          if(path.length>200||!this.ASSET_PATH_RE.test(path)) return this.unsupported("asset path "+path+" is not a bundle path");
          if(!ref||typeof ref!=="object"||!this.SHA256_RE.test(String(ref.sha256))||!Number.isInteger(ref.size)||
             ref.size<0||ref.size>this.MAX_ASSET_BYTES||typeof ref.media_type!=="string"||!ref.media_type)
            return this.unsupported("asset "+path+" is not a stored blob");
          total+=ref.size;
        }
        if(total>this.MAX_UI_ASSET_BYTES) return this.unsupported("this UI's assets exceed "+this.MAX_UI_ASSET_BYTES+" bytes");
      }
      const bundle={kind:this.KIND,version:this.VERSION,ui_id:component.ui_id,
        name:component.name.trim(),markup:component.markup,style:component.style,script:component.script};
      // Optional fields pass through as stored, so a library rebuilt from parsed
      // entries (install) never strips another UI's assets.
      for(const field of this.OPTIONAL)
        if(field in component) bundle[field]=JSON.parse(JSON.stringify(component[field]));
      return {ok:true,bundle};
    },
    // A name for an entry this app could not read, for the person to recognise
    // it by. Its own `name`, else its `ui_id`, else where it sits in the list --
    // all three are untrusted text, so they are bounded here and only ever
    // reach the screen through textContent.
    brokenLabel(component,index){
      const pick=field=>{
        const value=component&&typeof component==="object"?component[field]:null;
        return typeof value==="string"&&value.trim()?value.trim().slice(0,this.MAX_NAME):"";
      };
      return pick("name")||pick("ui_id")||("the UI in position "+(index+1));
    },
    // The library is a LIST of any length, ordered as stored, with no
    // user-chosen keys; each entry names itself by `ui_id`.
    //
    // One entry this app cannot render does NOT make the library unreadable.
    // It used to: the first failure was returned for the whole list, so a
    // single component with a bad `version` hid every UI the person had built
    // and disabled installing (founder, P1, 2026-10-03). Each entry is read on
    // its own now; the ones that parse are usable, and the ones that do not are
    // kept verbatim in `broken` so nothing is lost and each can be named with
    // its reason. Only a non-list `ui_library` is still a library-wide refusal.
    readLibrary(configuration){
      const raw=configuration&&configuration.ui_library;
      if(raw===undefined||raw===null) return {ok:true,entries:[],broken:[]};
      if(!Array.isArray(raw)) return this.unsupported("ui_library is not a list");
      // Of several entries sharing a ui_id, the LAST is used. `install` appends,
      // so the later entry is the more recently written one; taking the first
      // would let a stale copy win silently (Codex, 2026-10-03). A write refuses
      // duplicates, so this only arises in a row that already has them.
      const read=raw.map((component,index)=>({component,index,parsed:this.parseBundle(component)}));
      const newest=new Map();
      for(const item of read) if(item.parsed.ok) newest.set(item.parsed.bundle.ui_id,item.index);
      const entries=[],broken=[];
      read.forEach(({component,index,parsed})=>{
        const superseded=parsed.ok&&newest.get(parsed.bundle.ui_id)!==index;
        const reason=!parsed.ok?parsed.reason
          :superseded?"ui_id "+parsed.bundle.ui_id+" is listed twice; the later entry is the one in use":"";
        if(reason){
          // The id is recorded only when it is a well-formed one, so a saved
          // choice can still be matched to the entry that cannot render.
          const id=component&&typeof component==="object"&&this.text(component.ui_id,64)
            &&this.ID_RE.test(component.ui_id)?component.ui_id:"";
          broken.push({ui_id:id,label:this.brokenLabel(component,index),reason,component});
          return;
        }
        entries.push(parsed.bundle);
      });
      return {ok:true,entries,broken};
    },
    readSelection(configuration){
      const raw=configuration&&configuration.ui_selection;
      if(raw===undefined||raw===null) return {ok:true,selection:null};
      if(!raw||typeof raw!=="object"||Array.isArray(raw)) return this.unsupported("ui_selection is not an object");
      const keys=Object.keys(raw).sort();
      if(raw.version!==1) return this.unsupported("ui_selection version is not supported");
      if(raw.state==="default"){
        if(JSON.stringify(keys)!==JSON.stringify(["state","version"]))
          return this.unsupported("a default ui_selection carries unexpected fields");
        return {ok:true,selection:{version:1,state:"default"}};
      }
      if(raw.state!=="active") return this.unsupported("unknown ui_selection state");
      if(JSON.stringify(keys)!==JSON.stringify(["state","ui_id","version"]))
        return this.unsupported("an active ui_selection carries unexpected fields");
      if(!this.text(raw.ui_id,64)||!this.ID_RE.test(raw.ui_id)) return this.unsupported("ui_selection names an invalid ui_id");
      return {ok:true,selection:{version:1,state:"active",ui_id:raw.ui_id}};
    },
    // For inspecting a PUBLIC design: exactly one UI component, or nothing.
    readDefinition(agent){
      if(!agent||typeof agent!=="object"||!agent.components||typeof agent.components!=="object"||Array.isArray(agent.components))
        return this.unsupported("definition has no components");
      const keys=[];
      for(const [key,component] of Object.entries(agent.components))
        if(component&&typeof component==="object"&&component.kind===this.KIND) keys.push(key);
      if(keys.length!==1) return this.unsupported(keys.length
        ? "definition has "+keys.length+" UI components; this app renders exactly one"
        : "definition has no "+this.KIND+" component");
      const parsed=this.parseBundle(agent.components[keys[0]]);
      if(!parsed.ok) return parsed;
      return {ok:true,key:keys[0],bundle:parsed.bundle};
    },

    // ---- lifecycle and fencing ---------------------------------------------
    fence(epoch,home){ return this.enabled&&epoch===this.epoch&&home===this.home; },
    reset(){
      this.epoch++; this.unmount();
      this.enabled=false; this.home=""; this.principal="";
      this.library=[]; this.broken=[]; this.unreadable=""; this.selection=null; this.busy=false;
      this.platformDefault=null; this.defaultMounted=false;
      this.revision=0;
      this.conversation=null; this.conversationNote=""; this.previousTurn=null; this.selecting=false; this.ambiguous=false;
      $("btn-ui-switch").hidden=true;
      this.status(""); this.paint();
    },
    // The SAME account can move to another home mid-session (the status poll
    // observes it and calls setQueueScope). A bundle mounted for the old home
    // would otherwise keep a live bridge and be served the NEW home's
    // conversation, because `converse`/`get_status` resolve the caller's current
    // home rather than the one the bundle was granted (Codex, 2026-09-26).
    // Sign-out already tore the bridge down; this closes the same-login path.
    homeChanged(home){
      const id=String(home||"").trim();
      if(!this.enabled||!id||id===this.home) return;
      this.reset();
      this.status("Your home command center changed; the custom UI was closed and its access ended.");
    },
    enable(home,principal){
      if(this.enabled&&this.home===home&&this.principal===principal) return;
      this.reset();
      const id=String(home||"").trim();
      if(!id||!principal) return;
      this.epoch++; this.home=id; this.principal=principal; this.enabled=true;
      $("btn-ui-switch").hidden=false;
      this.paint();
      this.load();
    },
    // One read of the viewer's own row. The server keys it by the signed-in
    // caller, so there is nothing here to name and no owner to check.
    async fetchRow(){
      const doc=await Owner.read({target:"app_ui",graph_id:this.home});
      const row=doc&&doc.app_ui;
      if(!row||doc.error||!Number.isInteger(row.revision)||row.revision<0||row.universe_id!==this.home)
        throw Error((doc&&(doc.detail||doc.error))||"unexpected app UI reply");
      return row;
    },
    // Refresh and first load. Fenced like every other request here: a reply for
    // a home this controller has left is dropped.
    async load(){
      if(!this.enabled||this.busy) return;
      const epoch=this.epoch,home=this.home;
      this.busy=true; this.paint();
      try{
        const row=await this.fetchRow();
        if(!this.fence(epoch,home)) return;
        this.adopt(row);
        await this.readConversationDesign();
      }catch(err){
        if(!this.fence(epoch,home)) return;
        if(err&&err.authRequired){ sessionExpired(); return; }
        this.status("Could not read your installed UIs ("+(err&&err.message||"unknown error")+"). Default chat is in use.");
      }finally{ if(this.fence(epoch,home)){ this.busy=false; this.paint(); } }
    },
    // After each turn: the command center can switch or edit this row by talking
    // (write_graph target="app_ui" operation="activate" ...). A bodiless index
    // read says whether the row moved; only then is it read whole and adopted,
    // so an unchanged row never remounts a UI mid-use.
    async turnSettled(){
      if(!this.enabled||this.busy) return;
      const epoch=this.epoch,home=this.home;
      try{
        const doc=await Owner.read({target:"app_ui",graph_id:home,query:"index"});
        const row=doc&&doc.app_ui;
        if(!this.fence(epoch,home)||!row||doc.error||!Number.isInteger(row.revision)) return;
        if(row.revision!==this.revision) await this.load();
      }catch(_err){ /* the next turn asks again; the current screen stays */ }
    },
    // Appended to the status when some entries could not be read, so the person
    // is told without the working UIs being hidden. Switch command center names
    // each one and its reason.
    brokenNote(){
      if(!this.broken.length) return "";
      return " "+this.broken.length+(this.broken.length===1?" installed UI cannot be shown":
        " installed UIs cannot be shown")+"; open Switch command center to see why.";
    },
    adopt(row){
      if(!this.enabled) return;
      this.revision=row.revision;
      this.platformDefault=row.platform_default||null;
      const library=this.readLibrary(row),selection=this.readSelection(row);
      this.unmount();
      if(!library.ok){
        // An unreadable library is remembered as unreadable, NOT as empty. An
        // empty cache here is what let a later install rewrite `ui_library` from
        // nothing and drop the bundles it could not parse (Codex, 2026-09-26).
        this.library=[]; this.broken=[]; this.unreadable=library.reason; this.selection=null;
        // "Default chat is in use" has to BE true: every branch that says it
        // mounts the platform's blank command center, or the stage is left
        // empty with the explanation inside a closed dialog and no way back
        // (gpt-6-astra on #4358, reproduced).
        this.mountDefault();
        this.status("Installed UIs unreadable: "+library.reason+". Default chat is in use. Installing would overwrite them, so it is disabled."); this.paint(); return;
      }
      this.library=library.entries; this.broken=library.broken; this.unreadable="";
      if(!selection.ok){
        this.selection=null;
        this.mountDefault();
        this.status("Saved UI choice unreadable: "+selection.reason+". Default chat is in use."); this.paint(); return;
      }
      this.selection=selection.selection;
      if(this.selection&&this.selection.state==="active"){
        const entry=this.library.find(b=>b.ui_id===this.selection.ui_id);
        const spoiled=this.broken.find(b=>b.ui_id&&b.ui_id===this.selection.ui_id);
        if(entry){ this.mount(entry); this.status("Using "+entry.name+"."+this.brokenNote()); }
        // The chosen UI is still installed; it is the one that cannot render,
        // so say which and why rather than "no longer installed".
        else if(spoiled){
          this.mountDefault();
          this.status(spoiled.label+" cannot be shown: "+spoiled.reason
            +". Default chat is in use; your other UIs still work.");
        }
        else{
          this.mountDefault();
          this.status("Your saved UI ("+this.selection.ui_id+") is no longer installed. Default chat is in use."+this.brokenNote());
        }
      }else{
        this.mountDefault();
        this.status((this.library.length?"Default chat is in use.":"")+this.brokenNote());
      }
      this.paint();
    },

    // ---- rendering: the bundle never enters this document ------------------
    // Is the bundle on screen RIGHT NOW the platform's own blank command
    // center? Read off the mounted entry, so a UI cannot become the platform's
    // by naming itself that: parseBundle is the only way into `active`, and the
    // only bundle whose ui_id may contain a colon is the one the server sends
    // as `platform_default`.
    isPlatformDefault(){
      return !!(this.active && this.active.ui_id===this.PLATFORM_UI_ID && this.defaultMounted);
    },
    // Returns whether the stage now holds it. It does NOT throw: it is the
    // fallback several branches above fall back TO, and a throw there would
    // replace their explanation with a blank stage. A malformed platform
    // bundle is ours, not the owner's, so it is reported loudly and the caller
    // keeps its own message.
    mountDefault(){
      if(!this.enabled||!this.platformDefault) return false;
      const parsed=this.parseBundle(this.platformDefault);
      if(!parsed.ok){
        console.error("the platform's blank command center did not parse: "+parsed.reason);
        return false;
      }
      this.mount(parsed.bundle);
      // Set AFTER mount: mount() clears it, so this is only ever true for the
      // bundle this call put on screen.
      this.defaultMounted=true;
      return true;
    },
    mount(entry){
      this.unmount();
      const host=$("ui-frame-host"),frame=document.createElement("iframe");
      frame.id="ui-frame"; frame.className="ui-frame"; frame.title=entry.name;
      frame.setAttribute("tabindex","0");
      frame.setAttribute("sandbox",this.SANDBOX);
      frame.setAttribute("referrerpolicy","no-referrer");
      frame.setAttribute("src",this.FRAME_SRC);
      this.frame=frame; this.active=entry; this.ready=false;
      this.frameGen++; this.pending=0; this.sending=false; this.emitting=false; this.trying=false;
      this.defaultMounted=false;   // mountDefault sets it again after this call
      this.listener=event=>this.receive(event);
      window.addEventListener("message",this.listener);
      host.replaceChildren(frame);
      host.hidden=false;
      $("view-chat").classList.add("ui-custom-active");
      // Keep the command center visible and hand keyboard input back to it.
      if(typeof refreshChatCloud==="function") refreshChatCloud();
      if(typeof focusCommandCenter === "function" &&
         !(typeof isTypingTarget === "function" && isTypingTarget(document.activeElement)) &&
         !document.activeElement.closest("dialog[open], #cloud-menu:not([hidden])")) focusCommandCenter();
      this.paintHeader();
    },
    unmount(){
      if(this.listener){ window.removeEventListener("message",this.listener); this.listener=null; }
      const host=$("ui-frame-host");
      host.replaceChildren(); host.hidden=true;
      $("view-chat").classList.remove("ui-custom-active");
      // Keep the command center visible and hand keyboard input back to it.
      if(typeof refreshChatCloud==="function") refreshChatCloud();
      if(typeof focusCommandCenter === "function" &&
         !(typeof isTypingTarget === "function" && isTypingTarget(document.activeElement)) &&
         !document.activeElement.closest("dialog[open], #cloud-menu:not([hidden])")) focusCommandCenter();
      this.frame=null; this.active=null; this.ready=false; this.sending=false; this.emitting=false; this.pending=0;
      this.trying=false; this.defaultMounted=false;
      this.frameGen++;
      this.paintHeader();
    },

    // ---- the bridge: one frame, one allowlist, one command center ----------------
    // A frozen map. An action absent from it does not exist — the refusal names
    // what was asked and nothing is guessed from a near-match.
    ACTIONS:Object.freeze({
      whoami:"whoami",list_agents:"listAgents",
      send_message:"sendMessage",open_chat:"openChat",read_conversation:"readConversation",
      list_automations:"listAutomations",list_runs:"listRuns",read_live:"readLive",
      read_run:"readRun",read_run_output:"readRunOutput",
      list_files:"listFiles",read_file:"readFile",emit:"emit",
      "packages.list_tryable":"listTryablePackages","packages.try":"tryPackage","chat.prefill":"prefillChat",
      conversation_design:"conversationDesign",set_conversation_design:"setConversationDesign"}),
    receive(event){
      // Only THIS frame's window is heard. Another frame, a popup, or the page
      // itself cannot speak for the bundle, and the check is on the window
      // object rather than on an origin string, which an opaque origin makes "null"
      // for every sandboxed document on the page.
      if(!this.frame||event.source!==this.frame.contentWindow) return;
      const message=event.data;
      if(!message||typeof message!=="object"||message.ta_ui!==this.PROTOCOL) return;
      if(message.type==="ready"){ this.deliver(); return; }
      // The reserved key, handed back by a frame that would otherwise swallow
      // it. The frame can only ask for THIS: focus moves to the composer, and
      // nothing about the bundle, the turn or the account changes.
      if(message.type==="reserved_key"){
        if(typeof focusChatComposer==="function") focusChatComposer();
        return;
      }
      if(message.type!=="call"||typeof message.id!=="string"||typeof message.action!=="string") return;
      this.serve(message.id,message.action,message.params);
    },
    // The frame owns no network, so the bytes a UI loads are fetched HERE, with
    // the viewer's bearer, checked against their pins, and posted in; the frame
    // turns them into blob: URLs of its own. Fenced to the frame that asked: a
    // remount while bytes download drops them.
    deliver(){
      if(!this.frame||!this.active||this.ready) return;
      this.ready=true;
      if(typeof focusCommandCenter === "function" &&
         !(typeof isTypingTarget === "function" && isTypingTarget(document.activeElement)) &&
         !document.activeElement.closest("dialog[open], #cloud-menu:not([hidden])")) focusCommandCenter();
      const entry=this.active;
      const bundle={markup:entry.markup,style:entry.style,script:entry.script};
      if(entry.script_type==="module") bundle.script_type="module";
      // Nothing to fetch: handed over at once, exactly as before files existed.
      if(!(entry.libraries||[]).length&&!Object.keys(entry.assets||{}).length){
        this.post({ta_ui:this.PROTOCOL,type:"bundle",bundle});
        return;
      }
      return this.deliverWithFiles(entry,bundle);
    },
    async deliverWithFiles(entry,bundle){
      const gen=this.frameGen,epoch=this.epoch,home=this.home;
      let files=[],libraries=[];
      try{
        libraries=await this.libraryBytes(entry.libraries||[]);
        files=await this.assetBytes(entry.assets||{},home);
      }catch(err){
        if(gen!==this.frameGen||!this.fence(epoch,home)) return;
        if(err&&err.authRequired){ sessionExpired(); return; }
        this.status(entry.name+" could not load its files ("+(err&&err.message||"unknown error")+").");
        this.paint();
        return;
      }
      if(gen!==this.frameGen||!this.fence(epoch,home)||!this.frame) return;
      this.post({ta_ui:this.PROTOCOL,type:"bundle",bundle:Object.assign(bundle,{files,libraries})});
    },
    async fetchBytes(body){
      try{ await ensureFreshToken(); }catch(_err){ /* the request reports it */ }
      const headers={"Content-Type":"application/json"};
      const tk=token(); if(tk) headers["Authorization"]="Bearer "+tk;
      const resp=await fetch(this.ASSET_FETCH,{method:"POST",headers,credentials:"same-origin",
        cache:"no-store",body:JSON.stringify(body)});
      if(resp.status===401){ const e=new Error("authentication_required"); e.authRequired=true; throw e; }
      if(!resp.ok){
        let doc=null; try{ doc=await resp.json(); }catch(_e){ doc=null; }
        throw new Error((doc&&typeof doc.error==="string"&&doc.error)||("answered "+resp.status));
      }
      return await resp.arrayBuffer();
    },
    async digest(algorithm,bytes){
      return new Uint8Array(await crypto.subtle.digest(algorithm,bytes));
    },
    // Every library named, after what it requires, each verified against its
    // SHA-384 pin. Libraries are public code, so a verified copy is kept for
    // the page's life.
    async libraryBytes(names){
      const ordered=[],visit=name=>{
        if(ordered.includes(name)) return;
        const pin=this.LIBRARIES[name];
        if(!pin) throw new Error("library "+name+" is not one this app provides");
        for(const dep of pin.requires) visit(dep);
        ordered.push(name);
      };
      for(const name of names) visit(name);
      const out=[];
      for(const name of ordered){
        let bytes=this.libCache.get(name);
        if(!bytes){
          bytes=await this.fetchBytes({library:name});
          const got="sha384-"+btoa(String.fromCharCode(...await this.digest("SHA-384",bytes)));
          if(got!==this.LIBRARIES[name].sha384) throw new Error("library "+name+" failed its integrity check");
          this.libCache.set(name,bytes);
        }
        out.push({name,format:this.LIBRARIES[name].format,bytes});
      }
      return out;
    },
    // The viewer's own blobs, a few at a time, each checked against the hash
    // its manifest names before the frame sees it.
    async assetBytes(assets,home){
      const paths=Object.keys(assets),out=new Array(paths.length);
      let next=0;
      const hex=bytes=>Array.from(bytes,b=>b.toString(16).padStart(2,"0")).join("");
      const worker=async()=>{
        while(next<paths.length){
          const i=next++,path=paths[i],ref=assets[path];
          const bytes=await this.fetchBytes({graph_id:home,sha256:ref.sha256});
          if(hex(await this.digest("SHA-256",bytes))!==ref.sha256) throw new Error("asset "+path+" failed its integrity check");
          out[i]={path,media_type:ref.media_type,bytes};
        }
      };
      await Promise.all(Array.from({length:Math.min(4,paths.length)},worker));
      return out;
    },
    post(payload){
      const frame=this.frame&&this.frame.contentWindow;
      // The frame's origin is opaque, so it cannot be named; "*" still reaches
      // only this window. What crosses is the bundle's own source and results the
      // bundle asked for as the viewer -- which includes this viewer's
      // conversation text, so it IS sensitive; see the residual-risk note in
      // openspec/changes/composable-ui-experiences/design.md.
      if(frame) frame.postMessage(payload,"*");
    },
    refuse(id,error){ this.post({ta_ui:this.PROTOCOL,type:"result",id,ok:false,error:String(error)}); },
    // Re-checks, against the server, that the signed-in identity and home are
    // still the ones this bundle was granted. `converse` and `get_status` resolve
    // the CALLER's current home, so a bundle mounted before a home change would
    // otherwise be served the new home's data.
    async verify(){
      const epoch=this.epoch,home=this.home,principal=this.principal;
      const me=await fetchMe();
      if(!this.fence(epoch,home)) throw new Error("your session changed");
      if(!me||me.principal_id!==principal||me.universe_id!==home||me.setup!=="connected"){
        const err=new Error("your signed-in identity or home changed; this UI's access ended");
        err.revoke=true; throw err;
      }
    },
    async serve(id,action,params){
      // A reply is owed to the frame that ASKED. Without this, bundle A's answer
      // reaches bundle B, and since both bootstraps number requests from r1 it
      // settles B's own r1 promise with A's data (Codex, 2026-09-26).
      const gen=this.frameGen,asker={gen,name:this.active?this.active.name:"This UI"};
      const method=Object.prototype.hasOwnProperty.call(this.ACTIONS,action)?this.ACTIONS[action]:null;
      if(!method){ this.refuse(id,"action not available: "+action); return; }
      // PLATFORM-ONLY actions. Installing a package and putting words in the
      // owner's composer are the app's own offer to them, not a capability a
      // UI someone else wrote gets to reach for: a third-party bundle could
      // otherwise install software or compose a message as the owner. Only the
      // blank command center the platform ships (PLATFORM_UI_ID) may ask, and
      // the check is on the bundle MOUNTED NOW, not on anything the frame says
      // about itself.
      if(this.PLATFORM_ONLY.indexOf(action)>=0 && !this.isPlatformDefault()){
        this.refuse(id,"action not available: "+action); return;
      }
      if(this.pending>=8){ this.refuse(id,"too many requests in flight"); return; }
      const epoch=this.epoch,home=this.home,args=(params&&typeof params==="object"&&!Array.isArray(params))?params:{};
      this.pending++;
      try{
        await this.verify();
        // AGAIN, after the await. verify() is a server round-trip, and the
        // owner can replace the bundle while it is in flight: the checks below
        // used to run only on the way OUT, which discarded the reply but had
        // already DONE the work -- gpt-6-astra reproduced chat.prefill running
        // with a third-party bundle on screen. The effect, not just the
        // answer, belongs to the bundle that asked.
        if(!this.fence(epoch,home)||gen!==this.frameGen||!this.frame) return;
        if(this.PLATFORM_ONLY.indexOf(action)>=0 && !this.isPlatformDefault()){
          this.refuse(id,"action not available: "+action); return;
        }
        const result=await this[method](args,asker);
        if(!this.fence(epoch,home)||gen!==this.frameGen||!this.frame) return;
        this.post({ta_ui:this.PROTOCOL,type:"result",id,ok:true,result});
      }catch(err){
        if(err&&err.revoke){ this.refuse(id,err.message); this.reset(); return; }
        if(!this.fence(epoch,home)||gen!==this.frameGen||!this.frame) return;
        if(err&&err.authRequired){ this.refuse(id,"your session ended"); sessionExpired(); return; }
        this.refuse(id,(err&&err.message)||"unavailable");
      }finally{ if(gen===this.frameGen) this.pending--; }
    },
    // The viewer's identity, reduced to what a UI needs to greet them. No
    // principal id, no token, no provider or credential material.
    async whoami(){
      return {protocol:this.PROTOCOL,command_center_id:this.home,
        command_center_name:String(($("universe-name")&&$("universe-name").textContent)||"").trim(),
        workflow_refs:Object.assign({},this.active&&this.active.workflow_refs||{})};
    },
    // The viewer's OWN agents. `graph_id` is this.home, never an argument, so a
    // bundle cannot enumerate anybody else's command center.
    // Every row of one of the viewer's OWN lists. The read takes a page size
    // and no offset, so ask for a page and, while the server fills it exactly,
    // ask for a bigger one: a bundle
    // sees the whole list, never a first page passed off as all of it. A fixed
    // page here was an owner-volume cliff -- the 101st agent read as "no agent
    // of yours" (Codex on owner-door-complete-reads, 2026-09-30).
    async readWhole(args,key){
      for(let page=this.PAGE;;page*=4){
        const doc=await Owner.read(Object.assign({},args,{limit:page}));
        if(!doc||doc.error||!Array.isArray(doc[key])||doc[key].length<page) return doc;
      }
    },
    // The viewer's agents a person can talk to: the main agent, then every agent
    // they bound into THIS command center (harness §4.18). A conversation-design
    // installation answers the main thread, so it is not one of them.
    // `selected` is the agent the app's chat is talking to right now.
    conversable(b){
      return !!(b&&typeof b==="object"&&b.created_by===this.principal&&b.universe_id===this.home&&
        b.status==="configured"&&b.agent_binding_id&&b.configuration&&typeof b.configuration==="object"&&
        b.configuration.role!==this.ROLE&&
        !Object.prototype.hasOwnProperty.call(b.configuration,"turn_consumer"));
    },
    async listAgents(){
      const doc=await this.readWhole({target:"agent_bindings",graph_id:this.home},"bindings");
      if(!doc||doc.error||!Array.isArray(doc.bindings)) throw new Error("your agents are unavailable");
      const current=typeof addressedAgentId==="function"?addressedAgentId():"main";
      const agents=[{agent_id:"main",name:"Your agent",selected:current==="main"}];
      for(const b of doc.bindings){
        if(!this.conversable(b)) continue;
        // Picked fields only. A configuration is private operational data and
        // never crosses into a bundle, so only its NAME does.
        const id=String(b.agent_binding_id);
        agents.push({agent_id:id,name:String(b.configuration.name||"Unnamed agent"),selected:current===id});
      }
      return {agents};
    },
    // One of the viewer's own agents, by id or by name. Anything else is refused
    // by name: never quietly answered by a different agent.
    async agentNamed(wanted){
      const agents=(await this.listAgents()).agents;
      const match=agents.find(a=>a.agent_id===wanted)||agents.find(a=>a.name===wanted);
      if(!match) throw new Error("no agent of yours is named "+wanted);
      return match;
    },
    // Opens the app's chat talking to that agent: its own thread, its name on
    // the chat. A room-per-agent screen calls this when the person picks a room.
    async openChat(args){
      const wanted=typeof args.agent==="string"?args.agent.trim():"";
      if(!wanted) throw new Error("agent is required");
      const match=await this.agentNamed(wanted);
      await addressAgent({agent_id:match.agent_id,name:match.name});
      return {opened:true,agent_id:match.agent_id,name:match.name};
    },
    // Sends through the app's ordinary turn path, so a bundle's message gets the
    // same recovery, queueing and thread record a typed one gets. Naming an agent
    // opens the chat with that agent first, so the message and its reply appear
    // on that agent's own thread; no agent means the one the chat is talking to.
    async sendMessage(args){
      const text=typeof args.text==="string"?args.text.trim():"";
      if(!text) throw new Error("text is required");
      if(text.length>this.MAX_MESSAGE) throw new Error("text exceeds "+this.MAX_MESSAGE+" characters");
      const wanted=typeof args.agent==="string"?args.agent.trim():"";
      if(this.sending) throw new Error("a message from this UI is already in flight");
      this.sending=true;
      try{
        let agentId=typeof addressedAgentId==="function"?addressedAgentId():"main";
        if(wanted){
          const match=await this.agentNamed(wanted);
          agentId=match.agent_id;
          await addressAgent({agent_id:agentId,name:match.name});
        }
        if(typeof addressedAgentId==="function"&&addressedAgentId()!==agentId)
          throw new Error("the chat switched to another agent; nothing was sent");
        await sendTurn(text,text,{inputMethod:"app_action",agentId});
      }finally{ this.sending=false; }
      return {sent:true};
    },
    // The viewer's own saved conversation, field by field.
    //
    // Pinned to `this.home` and the ANSWER is checked against it. `get_status`
    // with no command center defaults to the caller's ACTIVE command center, so an unpinned
    // read hands a bundle whichever home the account moved to rather than the one
    // it was granted (Codex, 2026-09-26). `verify()` closes the window; this
    // closes the read itself, so neither depends on the other being right.
    //
    // One PAGE of it, newest first, and the page says whether older turns exist:
    // `has_more` and `next_before`, which the bundle passes back as `before` to
    // read the page before. An unreadable thread is an error, never `turns:[]`.
    async readConversation(args){
      const limit=Number.isInteger(args.limit)&&args.limit>0?Math.min(args.limit,this.MAX_READ_TURNS):this.MAX_READ_TURNS;
      const call={universe_id:this.home,include_conversation:true,conversation_limit:limit};
      if(Number.isSafeInteger(args.before)&&args.before>=0) call.conversation_before=args.before;
      // Whose thread: a named agent of the viewer's, else the one the chat is
      // talking to. The server re-checks that the agent is the viewer's own.
      const wanted=typeof args.agent==="string"?args.agent.trim():"";
      const agent=wanted?(await this.agentNamed(wanted)).agent_id:
        (typeof addressedAgentId==="function"?addressedAgentId():"main");
      if(agent!=="main") call.conversation_agent=agent;
      const doc=await Owner.status(call);
      if(!doc||doc.error) throw new Error("your conversation is unavailable");
      if(String(doc.universe_id||"")!==this.home)
        throw new Error("that conversation belongs to another command center; this UI's access ended");
      const conversation=doc.recent_conversation;
      if(!conversation||typeof conversation.error==="string"||!Array.isArray(conversation.turns))
        throw new Error("your conversation could not be read");
      const turns=[];
      for(const turn of conversation.turns){
        if(!turn||typeof turn.text!=="string") continue;
        turns.push({speaker:String(turn.speaker||"unknown"),text:turn.text,
          at:typeof turn.ts==="number"?turn.ts:null,truncated:!!turn.truncated});
      }
      const more=conversation.has_more===true&&Number.isSafeInteger(conversation.next_before);
      return {turns,has_more:more,next_before:more?conversation.next_before:null};
    },

    // ---- live state: the viewer's own automations and runs, read-only -------
    // What a screen needs to show agents WORKING rather than a picture of them.
    // Same rules as the reads above: `graph_id` is always this.home, the answer
    // is checked against it where it names a command center, and every reply is built
    // from picked fields. Automation `inputs` and a run's `actor` never cross:
    // the first is the owner's private configuration, the second a principal id.
    //
    // The server scopes each of these to the named command center, so a run id from
    // anywhere else reads as not found rather than being returned.
    async listTryablePackages(){
      const doc=await Owner.read({target:"command_center_packages",graph_id:this.home});
      if(!doc||doc.error||!Array.isArray(doc.packages)||doc.packages.length>12||
        typeof doc.build_prompt!=="string"||doc.build_prompt.length>this.MAX_MESSAGE||
        doc.can_try!==(doc.packages.length>=1)) throw new Error("command-center packages are unavailable");
      const packages=doc.packages.map(p=>{
        if(!p||!this.text(p.agent_definition_id,this.MAX_ID)||!p.agent_definition_id||
          typeof p.name!=="string"||typeof p.description!=="string"||typeof p.author_id!=="string"||
          !Number.isInteger(p.version)||p.version<1||typeof p.size!=="string"||
          !Number.isInteger(p.file_count)||p.file_count<0||!p.needs||
          typeof p.needs.model!=="string"||!Array.isArray(p.needs.connections)||
          !p.needs.connections.every(c=>typeof c==="string"))
          throw new Error("invalid command-center package");
        return {agent_definition_id:p.agent_definition_id,name:p.name,description:p.description,
          author_id:p.author_id,version:p.version,size:p.size,file_count:p.file_count,
          needs:{model:p.needs.model,connections:p.needs.connections.slice()}};
      });
      return {packages,build_prompt:doc.build_prompt,can_try:doc.can_try};
    },
    async tryPackage(args){
      const id=args.agent_definition_id;
      if(!this.text(id,this.MAX_ID)||!id.trim()) throw new Error("agent_definition_id is required");
      if(this.trying) throw new Error("a package request from this UI is already in flight");
      const gen=this.frameGen;
      this.trying=true;
      try{
        const doc=await MCP.callTool("write_graph",{target:"connection",operation:"try_package",
          graph_id:this.home,payload_json:JSON.stringify({agent_definition_id:id})});
        if(!doc||doc.error||!this.text(doc.request_id,this.MAX_ID)||!doc.request_id)
          throw new Error((doc&&(doc.detail||doc.error))||"the install could not be requested");
        return {request_id:doc.request_id};
      }finally{ if(gen===this.frameGen) this.trying=false; }
    },
    prefillChat(args){
      const text=args.text;
      if(!this.text(text,this.MAX_MESSAGE)) throw new Error("chat text is too long or missing");
      if(typeof chatCloudPrefill==="function") chatCloudPrefill(text);
      else throw new Error("the chat is not available");
      return {prefilled:true};
    },
    // Each agent's live state, for a screen that animates agents as they work
    // (a village whose villagers walk to what they are doing). Which agent,
    // working or idle, since when, and its latest steps -- each a tool name, a
    // platform-made safe summary and done/running/failed, never a command, an
    // argument or a result (harness S4). Read-only, pinned to this.home, keyed
    // by agent. Poll it; every call is one owner-door status read.
    async readLive(){
      const doc=await Owner.status({universe_id:this.home});
      if(!doc||doc.error) throw new Error("your agents' live state is unavailable");
      if(String(doc.universe_id||"")!==this.home)
        throw new Error("that state belongs to another command center; this UI's access ended");
      const turn=(doc.active_turn&&typeof doc.active_turn==="object")?doc.active_turn:null;
      const working=!!turn&&turn.state!=="unreadable"&&turn.stale!==true;
      const steps=[];
      if(working&&Array.isArray(turn.tools)){
        for(const step of turn.tools.slice(0,5)){
          if(!step||typeof step!=="object") continue;
          steps.push({tool:String(step.tool||""),summary:String(step.summary||""),
            state:["running","done","failed"].includes(step.state)?step.state:"",
            age_s:Number.isFinite(step.age_s)?step.age_s:null});
        }
      }
      // The live turn is the selected conversation agent's; every other agent
      // is idle until per-agent turns arrive (design §4.18).
      const roster=await this.listAgents();
      // With no installed agent selected, the conversation is the universe's own
      // agent: it gets the seeded id "main" (design §4.18), named as the app
      // names the universe.
      const listed=roster.agents.some(a=>a.selected) ? roster.agents
        : [{agent_id:"main",name:(await this.whoami()).universe_name||"Your agent",selected:true}]
            .concat(roster.agents);
      const agents=listed.map(a=>a.selected&&working
        ? {agent_id:a.agent_id,name:a.name,state:"working",
           since:typeof turn.started_at==="string"?turn.started_at:null,steps}
        : {agent_id:a.agent_id,name:a.name,state:"idle",since:null,steps:[]});
      return {as_of:new Date().toISOString(),agents};
    },
    async listAutomations(){
      const doc=await this.readWhole({target:"automations",graph_id:this.home},"automations");
      if(!doc||doc.error||!Array.isArray(doc.automations)) throw new Error("your automations are unavailable");
      if(String(doc.universe_id||"")!==this.home)
        throw new Error("those automations belong to another command center; this UI's access ended");
      const automations=[];
      for(const a of doc.automations){
        // A retired fleet-era row names no command center and runs nothing: skip it.
        if(!a||typeof a!=="object"||a.universe_id!==this.home) continue;
        const t=(a.trigger&&typeof a.trigger==="object")?a.trigger:{};
        automations.push({automation_id:String(a.automation_id||""),name:String(a.name||""),
          branch_id:String(a.branch_def_id||""),
          trigger:{kind:String(t.kind||""),interval_seconds:Number.isFinite(t.interval_seconds)?t.interval_seconds:null,
            cron:String(t.cron_expr||""),event:String(t.event_type||"")},
          state:String(a.desired_state||""),paused_because:String(a.pause_reason||""),
          last_run_id:String(a.last_run_id||""),last_result:String(a.last_reason||""),
          last_finished_at:a.last_finished_at||null,next_due_at:a.next_due_at||null,
          consecutive_failures:Number.isInteger(a.consecutive_failures)?a.consecutive_failures:0});
      }
      return {automations};
    },
    // Newest first, at most MAX_LIST_RUNS: a bundle is untrusted code and cannot
    // raise the bound of one request. The page SAYS whether older runs exist
    // (one look-ahead row is read to know), so a cut is never silent -- a fixed
    // 50 with no signal is what Codex round 2 flagged.
    async listRuns(args){
      const limit=Number.isSafeInteger(args.limit)&&args.limit>0?Math.min(args.limit,this.MAX_LIST_RUNS):this.MAX_LIST_RUNS;
      const call={target:"runs",graph_id:this.home,limit:limit+1};
      if(typeof args.status==="string"&&args.status.trim()) call.run_status=args.status.trim();
      const doc=await Owner.read(call);
      if(!doc||doc.error||!Array.isArray(doc.runs)) throw new Error("your runs are unavailable");
      const runs=[];
      for(const r of doc.runs.slice(0,limit)){
        if(!r||typeof r!=="object") continue;
        runs.push(this.runSummary(r));
      }
      return {runs,has_more:doc.runs.length>limit};
    },
    runSummary(r){
      return {run_id:String(r.run_id||""),branch_id:String(r.branch_def_id||""),
        name:String(r.run_name||""),status:String(r.status||""),
        started_at:r.started_at||null,finished_at:r.finished_at||null,
        last_node_id:String(r.last_node_id||"")};
    },
    runId(args){
      const id=typeof args.run_id==="string"?args.run_id.trim():"";
      if(!id||id.length>this.MAX_ID) throw new Error("run_id is required");
      return id;
    },
    async readRun(args){
      const id=this.runId(args);
      const doc=await Owner.read(
        {target:"run",graph_id:this.home,run_id:id});
      if(!doc||doc.error||String(doc.run_id||"")!==id) throw new Error("that run is not one of yours");
      const nodes=[];
      for(const n of Array.isArray(doc.node_statuses)?doc.node_statuses:[])
        if(n&&typeof n==="object") nodes.push({node_id:String(n.node_id||""),status:String(n.status||"")});
      const catalog=doc.output_catalog&&Array.isArray(doc.output_catalog.fields)?doc.output_catalog.fields:[];
      return Object.assign(this.runSummary(doc),{error:String(doc.error||""),nodes,
        output_fields:catalog.filter(f=>f&&typeof f.name==="string").map(f=>f.name)});
    },
    // One output field of one of the viewer's runs, in bounded chunks: what an
    // agent node wrote is how a screen shows what that agent said.
    async readRunOutput(args){
      const id=this.runId(args);
      const field=typeof args.field==="string"?args.field:"";
      if(!field||field.length>this.MAX_ID) throw new Error("field is required");
      const offset=Number.isInteger(args.offset)&&args.offset>0?args.offset:0;
      const doc=await Owner.read({target:"run_output",graph_id:this.home,run_id:id,
        field_name:field,output_offset:offset,output_max_chars:this.MAX_OUTPUT_CHUNK});
      if(!doc||doc.error||typeof doc.chunk!=="string") throw new Error("that output is not available");
      return {field:String(doc.field_name||field),encoding:doc.encoding==="json"?"json":"text",
        text:doc.chunk,offset:Number.isInteger(doc.offset)?doc.offset:offset,
        total_chars:Number.isInteger(doc.total_chars)?doc.total_chars:null,
        next_offset:Number.isInteger(doc.next_offset)?doc.next_offset:null};
    },

    // ---- the shared folder and the wake -------------------------------------
    // Agents coordinate through files in the command center folder, so a screen of
    // them reads those files. Owner-only on the server (an admin grant on this
    // home), pinned to this.home here, picked fields back.
    filePath(value,required){
      const path=typeof value==="string"?value.trim():"";
      if(required&&!path) throw new Error("path is required");
      if(path.length>this.MAX_PATH) throw new Error("path is too long");
      return path;
    },
    async listFiles(args){
      const path=this.filePath(args.path,false);
      const doc=await Owner.read(
        {target:"command_center_files",graph_id:this.home,query:path});
      if(!doc||doc.error||!Array.isArray(doc.entries)) throw new Error("that folder is not available");
      if(String(doc.universe_id||"")!==this.home)
        throw new Error("that folder belongs to another command center; this UI's access ended");
      const entries=[];
      for(const e of doc.entries){
        if(!e||typeof e.name!=="string") continue;
        const kind=e.kind==="dir"?"dir":"file";
        entries.push(kind==="dir"?{name:e.name,kind}:{name:e.name,kind,
          size_bytes:Number.isInteger(e.size_bytes)?e.size_bytes:null});
      }
      return {path:String(doc.path||""),entries,truncated:!!doc.truncated};
    },
    async readFile(args){
      const path=this.filePath(args.path,true);
      const offset=Number.isInteger(args.offset)&&args.offset>0?args.offset:0;
      const doc=await Owner.read({target:"command_center_file",graph_id:this.home,
        query:path,file_offset:offset,file_max_bytes:this.MAX_FILE_CHUNK});
      if(!doc||doc.error) throw new Error("that file is not available");
      if(String(doc.universe_id||"")!==this.home)
        throw new Error("that file belongs to another command center; this UI's access ended");
      const text=doc.encoding==="text"&&typeof doc.text==="string";
      return {path:String(doc.path||path),encoding:text?"text":"base64",
        content:text?doc.text:String(doc.base64||""),size_bytes:Number.isInteger(doc.size_bytes)?doc.size_bytes:null,
        offset:Number.isInteger(doc.offset)?doc.offset:offset,
        next_offset:Number.isInteger(doc.next_offset)?doc.next_offset:null};
    },
    // Wakes the viewer's OWN agent subscribed to `name` (an app_event
    // automation). It cannot run anything by id: what listens to a name is the
    // owner's decision, made when the subscription was created.
    async emit(args){
      const name=typeof args.name==="string"?args.name.trim():"";
      if(!name) throw new Error("name is required");
      const data=args.data===undefined||args.data===null?{}:args.data;
      if(typeof data!=="object"||Array.isArray(data)) throw new Error("data must be an object");
      if(this.emitting) throw new Error("an event from this UI is already in flight");
      this.emitting=true;
      try{
        const doc=await MCP.callTool("run_graph",{operation:"emit_event",graph_id:this.home,
          inputs_json:JSON.stringify({name,data})});
        if(!doc||doc.error) throw new Error((doc&&(doc.detail||doc.error))||"the event was not sent");
        return {emitted:doc.emitted===true,woke:Number.isInteger(doc.woke)?doc.woke:0};
      }finally{ this.emitting=false; }
    },

    // ---- the conversation design: read, ask to change, trusted recovery ----
    // Which published conversation component answers this owner's future
    // messages. It used to be chosen in a fixed "App design" dialog; a UI can
    // now read it and ASK for a change, so a screen of agents can route the
    // conversation to the one the person picked. The ask is never the
    // approval: the person confirms in this page's own prompt, drawn outside
    // the bundle, naming what changes. The write is the receiver's own CAS
    // update, read back before it is reported; the server still checks source
    // access, the immutable Branch pin, model access and every effect when a
    // turn is admitted, so a selection is data, never a grant.
    eligible(b){
      return !!(b&&b.created_by===this.principal&&b.universe_id===this.home&&
        b.status==="configured"&&b.agent_binding_id&&b.agent_definition_id&&
        Number.isInteger(b.revision)&&b.configuration&&typeof b.configuration==="object"&&
        b.configuration.role===this.ROLE&&
        !Object.prototype.hasOwnProperty.call(b.configuration,"provider_ref"));
    },
    // Field names only, exactly what the server adapter accepts.
    turnComponent(c){
      const fields=["kind","version","branch_version_id","content_hash","input_map","reply_key"].sort();
      const ident=v=>typeof v==="string"&&v.length>0&&v.length<=this.MAX_ID&&v.trim()===v&&!/[\x00-\x1f\x7f]/.test(v);
      if(!c||typeof c!=="object"||Array.isArray(c)||JSON.stringify(Object.keys(c).sort())!==JSON.stringify(fields)||
         c.kind!==this.TURN_KIND||c.version!==1||!ident(c.branch_version_id)||
         !/^[a-f0-9]{64}$/.test(c.content_hash)||!ident(c.reply_key))return this.unsupported("unsupported conversation adapter or source pin");
      const m=c.input_map;
      if(!m||typeof m!=="object"||Array.isArray(m)||!Object.hasOwn(m,"message")||
         Object.keys(m).some(k=>!["message","history"].includes(k))||
         Object.values(m).some(v=>!ident(v))||new Set(Object.values(m)).size!==Object.values(m).length)
        return this.unsupported("unsupported conversation input mapping");
      return {ok:true};
    },
    // Picked fields only: never the installation's configuration.
    describe(b){
      const t=b&&b.configuration&&b.configuration.turn_consumer;
      if(!t||t.state==="disabled") return {state:"default"};
      if(t.state==="active"&&typeof t.component_key==="string")
        return {state:"active",agent_definition_id:String(b.agent_definition_id),component_key:t.component_key};
      return {state:"unsupported"};
    },
    async installations(){
      const doc=await this.readWhole({target:"agent_bindings",graph_id:this.home},"bindings");
      if(!doc||doc.error||!Array.isArray(doc.bindings)) throw new Error("your conversation design is unavailable");
      return doc.bindings.filter(b=>this.eligible(b));
    },
    async readConversationDesign(){
      const epoch=this.epoch,home=this.home;
      try{
        const rows=await this.installations();
        if(!this.fence(epoch,home)) return;
        this.conversation=rows.length===1?rows[0]:null;
        this.ambiguous=rows.length>1;
        this.conversationNote=this.ambiguous?"More than one conversation installation exists, so none answers you. Restore the default conversation to clear them.":"";
      }catch(err){
        if(!this.fence(epoch,home)) return;
        if(err&&err.authRequired) throw err;
        this.conversation=null; this.ambiguous=false;
        this.conversationNote="Conversation design unreadable ("+(err&&err.message||"unknown error")+").";
      }
      this.paint();
    },
    async conversationDesign(){
      const rows=await this.installations();
      if(rows.length>1) return {state:"ambiguous"};
      return this.describe(rows[0]||null);
    },
    async setConversationDesign(args,asker){
      const want=args.state==="default"?null:{
        definition_id:typeof args.agent_definition_id==="string"?args.agent_definition_id.trim():"",
        component_key:typeof args.component_key==="string"?args.component_key:""};
      if(want&&(!want.definition_id||want.definition_id.length>this.MAX_ID||!want.component_key||want.component_key.length>this.MAX_ID))
        throw new Error("agent_definition_id and component_key are required, or state \"default\"");
      if(this.selecting) throw new Error("a conversation change is already in flight");
      return this.changeConversation(want,(agent,target)=>{
        // The prompt names the UI that ASKED, captured before any await, and
        // is never shown once that UI has left the screen.
        this.stillAsking(asker);
        return confirm(target
          ? "“"+asker.name+"” asks to send your future messages to “"+String(agent.name||"unnamed")+
            "” (its conversation component "+target.component_key+", workflow version "+
            String(agent.components[target.component_key].branch_version_id)+"). "+
            "Your model choice and access still govern every call, and work already started is not changed. Allow?"
          : "“"+asker.name+"” asks to restore the default conversation for your future messages. Allow?");
      },asker);
    },
    // A UI's request belongs to the frame that sent it. Another UI swapped in
    // while the request awaited is a different author, so the request ends.
    stillAsking(asker){
      if(asker&&asker.gen!==this.frameGen)
        throw new Error("the UI that asked is no longer on screen; nothing was changed");
    },
    // The ONE conversation write path. `approve` and `asker` are null only for
    // this page's own recovery buttons, which are themselves the person's click.
    async changeConversation(target,approve,asker){
      if(!this.enabled) throw new Error("not ready");
      const epoch=this.epoch,home=this.home;
      this.selecting=true; this.paint();
      try{
        const rows=await this.installations();
        if(!this.fence(epoch,home)) throw new Error("your session changed");
        if(rows.length>1){
          if(target||approve) throw new Error("more than one conversation installation exists; restore the default conversation in Switch command center first. Nothing was changed");
          return await this.clearAmbiguity(rows,epoch,home);
        }
        const b=rows[0]||null;
        if(b&&b.updated_by!==this.principal) throw new Error("the conversation installation is not owner-controlled; nothing was changed");
        let definitionId,selection,agent=null;
        if(target){
          const doc=await Owner.read({target:"agent",agent_definition_id:target.definition_id});
          if(!this.fence(epoch,home)) throw new Error("your session changed");
          agent=doc&&doc.agent;
          if(!agent||doc.error||agent.agent_definition_id!==target.definition_id) throw new Error("that design is unavailable");
          const c=agent.components&&typeof agent.components==="object"&&
            Object.prototype.hasOwnProperty.call(agent.components,target.component_key)?agent.components[target.component_key]:null;
          const fit=this.turnComponent(c);
          if(!fit.ok) throw new Error("that design has no supported conversation component "+target.component_key+": "+fit.reason);
          if(!/^[a-f0-9]{64}$/.test(agent.content_fingerprint)) throw new Error("that design has no content fingerprint");
          definitionId=target.definition_id;
          selection={version:1,state:"active",component_key:target.component_key,definition_fingerprint:agent.content_fingerprint};
        }else{
          if(!b||this.describe(b).state==="default"){ this.conversation=b; return {state:"default"}; }
          definitionId=String(b.agent_definition_id);
          selection={version:1,state:"disabled"};
        }
        if(approve&&!approve(agent,target)) throw new Error("the person did not approve the change; nothing was changed");
        // The prompt waited on a person: the session, and the UI on screen,
        // may have moved meanwhile.
        await this.verify();
        this.stillAsking(asker);
        const config=b?JSON.parse(JSON.stringify(b.configuration)):{schema_version:1,name:"App experience",role:this.ROLE};
        const previous=b?{definition_id:String(b.agent_definition_id),
          selection:JSON.parse(JSON.stringify(config.turn_consumer||{version:1,state:"disabled"}))}:null;
        config.turn_consumer=selection;
        const check=await this.writeInstallation(b,definitionId,config,epoch,home);
        this.previousTurn=previous; this.conversation=check; this.ambiguous=false; this.conversationNote="";
        this.status(selection.state==="disabled"
          ?"Default conversation restored for future messages. Work already started is not changed."
          :"Conversation design changed for future messages. Your model choice and private data are unchanged.");
        return this.describe(check);
      }finally{ if(this.fence(epoch,home)){ this.selecting=false; this.paint(); } }
    },
    // One revision-guarded write of the receiver's own installation, read back
    // and compared by VALUE: the store keeps canonical JSON with sorted keys.
    // With no `b` it creates one; the server refuses a second installation of
    // the role, so two tabs that both saw none cannot both create one.
    async writeInstallation(b,definitionId,config,epoch,home){
      const result=await MCP.callTool("write_graph",{target:"agent_binding",operation:b?"update":"bind",
        graph_id:home,agent_definition_id:definitionId,
        ...(b?{agent_binding_id:b.agent_binding_id,expected_revision:b.revision}:{}),
        payload_json:JSON.stringify(config)});
      if(!this.fence(epoch,home)) throw new Error("your session changed");
      const written=result&&result.binding;
      const kept=config.role===this.ROLE;
      // A retired row has left the role, so `eligible` no longer fits it; it
      // is still this viewer's own row in this home.
      const mine=v=>!!(v&&v.created_by===this.principal&&v.universe_id===this.home&&
        v.status==="configured"&&(!kept||this.eligible(v)));
      if(!result||result.error||result.status!=="configured"||!mine(written)||
         written.updated_by!==this.principal||String(written.agent_definition_id)!==definitionId||
         (b&&written.agent_binding_id!==b.agent_binding_id))
        throw new Error("the change was not confirmed"+(result&&result.error?" ("+String(result.error)+")":"")+"; it was not retried");
      const doc=await Owner.read({target:"agent_binding",graph_id:home,agent_binding_id:written.agent_binding_id});
      if(!this.fence(epoch,home)) throw new Error("your session changed");
      const check=doc&&doc.binding;
      if(!mine(check)||check.agent_binding_id!==written.agent_binding_id||check.updated_by!==this.principal||
         String(check.agent_definition_id)!==definitionId||check.revision!==written.revision||
         this.canonical(check.configuration)!==this.canonical(config))
        throw new Error("the change could not be confirmed by read-back; it was not retried");
      return check;
    },
    // Trusted recovery from more than one installation (left by an older
    // client, before the server refused a second one): the first, by id, is
    // kept with the default conversation; every other one is retired out of
    // the role, so exactly one installation remains and none is active.
    async clearAmbiguity(rows,epoch,home){
      const ordered=[...rows].sort((x,y)=>String(x.agent_binding_id)<String(y.agent_binding_id)?-1:1);
      let kept=null;
      for(const [i,b] of ordered.entries()){
        const config=JSON.parse(JSON.stringify(b.configuration));
        config.turn_consumer={version:1,state:"disabled"};
        if(i>0) config.role=this.ROLE+"_retired";
        else if(this.describe(b).state==="default"&&b.updated_by===this.principal){ kept=b; continue; }
        const check=await this.writeInstallation(b,String(b.agent_definition_id),config,epoch,home);
        if(i===0) kept=check;
      }
      this.previousTurn=null; this.conversation=kept; this.ambiguous=false; this.conversationNote="";
      this.status("Default conversation restored; "+(ordered.length-1)+" extra installation(s) retired.");
      return {state:"default"};
    },
    async recover(target){
      if(!this.enabled||this.selecting) return;
      try{ await this.changeConversation(target,null,null); }
      catch(err){
        if(err&&err.authRequired){ sessionExpired(); return; }
        this.status("Conversation not changed: "+(err&&err.message||"unavailable")+".");
        await this.readConversationDesign();
      }
    },
    restoreDefaultConversation(){ return this.recover(null); },
    restorePreviousConversation(){
      const p=this.previousTurn;
      if(!p) return;
      return this.recover(p.selection.state==="active"
        ?{definition_id:p.definition_id,component_key:p.selection.component_key}:null);
    },

    // ---- switching: explicit, persisted through ONE write path -------------
    async choose(uiId){
      if(!this.enabled||this.busy) return;
      const entry=this.library.find(b=>b.ui_id===uiId);
      if(!entry){ this.status("That UI is not installed. Refresh."); this.paint(); return; }
      // Apply first so the switch is immediate; persistence is what makes it
      // survive a sign-in, and a failed write says so rather than reverting the
      // view the user just asked for.
      this.mount(entry);
      await this.remember({version:1,state:"active",ui_id:entry.ui_id},
        "Now using "+entry.name+".","Now using "+entry.name+" for this visit only");
    },
    async chooseDefault(){
      if(!this.enabled||this.busy) return;
      this.unmount();
      this.mountDefault();
      await this.remember({version:1,state:"default"},
        "Default chat restored.","Default chat restored for this visit only");
    },
    // The ONE write path. Re-reads the row so `mutate` works on what is stored
    // now rather than on this controller's cache, then saves only the fields
    // `mutate` returns, compare-and-set on the revision just read. A save that
    // loses a race is refused by the server and reported; nothing is retried.
    // A fresh account needs no setup step: its first save (revision 0) creates
    // the row, and nothing about it is published.
    async save(noun,mutate){
      if(!this.enabled||this.busy) return {ok:false,reason:"not ready"};
      const epoch=this.epoch,home=this.home;
      this.busy=true; this.paint();
      try{
        const row=await this.fetchRow();
        if(!this.fence(epoch,home)) return {ok:false,reason:"stale"};
        const changes=mutate(JSON.parse(JSON.stringify(row)));
        const result=await MCP.callTool("write_graph",{target:"app_ui",operation:"save",
          graph_id:home,expected_revision:row.revision,payload_json:JSON.stringify(changes)});
        if(!this.fence(epoch,home)) return {ok:false,reason:"stale"};
        const saved=result&&result.app_ui;
        if(!result||result.error||result.status!=="saved"||!saved||saved.universe_id!==home||
           saved.revision!==row.revision+1)
          throw Error((result&&(result.detail||result.error))||noun+" save was not confirmed");
        // Compared as VALUES, not as text: the store keeps canonical JSON with
        // sorted keys, so {version,state,ui_id} comes back {state,ui_id,version}.
        // A text compare called every saved choice a mismatch (live 2026-10-01).
        for(const key of Object.keys(changes))
          if(this.canonical(saved[key])!==this.canonical(changes[key])) throw Error(noun+" save did not match");
        this.revision=saved.revision;
        return {ok:true,row:saved};
      }catch(err){
        if(!this.fence(epoch,home)) return {ok:false,reason:"stale"};
        if(err&&err.authRequired){ sessionExpired(); return {ok:false,reason:"auth"}; }
        return {ok:false,reason:"failed",error:err};
      }finally{ if(this.fence(epoch,home)){ this.busy=false; this.paint(); } }
    },
    // JSON text with every object's keys sorted: one spelling per value.
    canonical(value){
      const sort=v=>Array.isArray(v)?v.map(sort):(v&&typeof v==="object")?
        Object.fromEntries(Object.keys(v).sort().map(k=>[k,sort(v[k])])):v;
      return JSON.stringify(sort(value));
    },
    async remember(selection,saved,unsaved){
      const outcome=await this.save("UI choice",()=>({ui_selection:JSON.parse(JSON.stringify(selection))}));
      if(!outcome.ok){
        const why=outcome.error&&outcome.error.message||outcome.reason||"unavailable";
        this.status(unsaved+" — the choice was not saved ("+why+").");
      }else{
        this.selection=selection; this.status(saved);
      }
      this.paint();
    },
    // Install a bundle into the viewer's own library: a remix installs the
    // COMPONENT, so it runs against this viewer's bridge and this viewer's
    // command center. The author's command center is never addressed by an installed copy.
    async install(component){
      if(!this.enabled||this.busy) return {ok:false,reason:"not ready"};
      const parsed=this.parseBundle(component);
      if(!parsed.ok){ this.status("Cannot install: "+parsed.reason); this.paint(); return parsed; }
      // Refuse rather than overwrite what could not be read. An install used to
      // rebuild `ui_library` from this controller's cache, and `adopt` empties that
      // cache when ANY stored entry is unsupported -- so installing next to a
      // future-version bundle silently deleted it, and CAS could not notice
      // because the revision was current (Codex, 2026-09-26).
      if(this.unreadable){
        this.status("Your installed UIs cannot be read ("+this.unreadable+"), so installing would overwrite them. Nothing was changed.");
        this.paint(); return this.unsupported("library unreadable");
      }
      let next=null,keptBroken=null;
      const outcome=await this.save("UI install",row=>{
        // Built from the row the save actually read, not from the cache -- so a
        // library that changed since the last read is re-checked here instead
        // of being replaced by a stale view.
        const observed=this.readLibrary(row);
        if(!observed.ok) throw Error("Your installed UIs cannot be read ("+observed.reason+"); nothing was overwritten");
        next=observed.entries.filter(b=>b.ui_id!==parsed.bundle.ui_id).concat([parsed.bundle]);
        // Entries this app cannot render are written back as they were read.
        // This write replaces the whole list, so anything left out is destroyed:
        // carrying them is what lets an install proceed beside a component with
        // a bad version instead of being refused (founder, P1, 2026-10-03). An
        // entry whose ui_id this install replaces is the one case that drops.
        //
        // NOT byte-exact, and it cannot be from here: `fetchRow` has already
        // parsed the row as JSON, so an integer outside JavaScript's exact
        // range was rounded before this code saw it (Codex, 2026-10-03:
        // 9007199254740993 -> ...92 inside a field the app does not render).
        // Reachable only through an extra field on an already-unrenderable
        // entry. The fix is for the client to stop rewriting entries it did not
        // author -- splice server-side with add_ui/replace_ui --
        // docs/concerns/2026-10-03-whole-library-write-rounds-carried-numbers.md
        keptBroken=observed.broken.filter(b=>!b.ui_id||b.ui_id!==parsed.bundle.ui_id);
        // No library-wide limit, so no install is ever turned away for the size
        // of what is already there. The bundle itself was validated above, and
        // its bytes are the command center's storage.
        return {ui_library:JSON.parse(JSON.stringify(
          next.concat(keptBroken.map(b=>b.component))))};
      });
      if(!outcome.ok){
        const why=outcome.error&&outcome.error.message||outcome.reason||"unavailable";
        this.status("The UI was not installed ("+why+")."); this.paint(); return outcome;
      }
      this.library=next; this.broken=keptBroken;
      this.status("Installed "+parsed.bundle.name+". Switch to it whenever you like."+this.brokenNote());
      this.paint();
      return {ok:true,bundle:parsed.bundle};
    },
    // Sharing is the existing path: a UI component inside a public definition.
    // Publishing is a separate, explicit act — an installed UI stays private.
    publishPayload(bundle,description){
      const parsed=this.parseBundle(bundle);
      if(!parsed.ok) return parsed;
      // Its files live in this viewer's private UI storage; a published copy
      // could not load them (the server's publish refuses them for the same reason).
      if(parsed.bundle.assets&&Object.keys(parsed.bundle.assets).length)
        return this.unsupported("a UI that loads its own files cannot be published yet");
      return {ok:true,payload:{schema_version:1,name:parsed.bundle.name,
        description:String(description||""),tags:[this.KIND],
        components:{ui:JSON.parse(JSON.stringify(parsed.bundle))}}};
    },

    // ---- fixed chrome: textContent only, never markup ----------------------
    status(text){ const node=$("ui-status"); if(node) node.textContent=text||""; },
    button(text,onClick,disabled){
      const b=document.createElement("button"); b.type="button"; b.className="btn btn--link";
      b.textContent=text; b.disabled=!!disabled; b.addEventListener("click",onClick); return b;
    },
    line(parent,text,cls){ const p=document.createElement("p"); if(cls) p.className=cls; p.textContent=text; parent.appendChild(p); return p; },
    paintHeader(){
      const label=$("ui-mode");
      if(!label) return;
      label.hidden=!this.active;
      label.textContent=this.active?"UI: "+this.active.name:"";
    },
    paint(){
      this.paintHeader();
      const list=$("ui-list");
      if(!list) return;
      list.replaceChildren();
      const row=document.createElement("li");
      // Disabled only while a save is in flight. It used to also require
      // something to BE active, which disabled the way back at exactly the
      // moment it is needed -- nothing mounted (gpt-6-astra on #4358).
      // chooseDefault works from no bundle: it unmounts, then mounts the
      // platform's blank command center.
      row.appendChild(this.button("Default chat",()=>this.chooseDefault(),this.busy));
      list.appendChild(row);
      for(const bundle of this.library){
        const item=document.createElement("li"),current=!!(this.active&&this.active.ui_id===bundle.ui_id);
        item.appendChild(this.button((current?"Using: ":"Use ")+bundle.name,
          ()=>this.choose(bundle.ui_id),this.busy||current));
        list.appendChild(item);
      }
      // Each entry this app cannot render, named with its reason, BELOW the ones
      // that work. The reason is the parser's own sentence, so "version must be
      // 1" reaches the person and their agent rather than a blanket "unreadable".
      for(const entry of this.broken){
        const item=document.createElement("li");
        this.line(item,entry.label+" cannot be shown: "+entry.reason,"muted");
        list.appendChild(item);
      }
      if(this.broken.length)
        this.line(list,"Ask your agent to fix the ones above; your other UIs and installing are unaffected.","muted");
      if(!this.library.length&&!this.broken.length)
        this.line(list,"No custom UI installed. Ask your agent to build one.","muted");
      $("btn-ui-refresh").disabled=this.busy;
      this.paintConversation();
    },
    // Trusted recovery, outside any custom UI: what answers this person's
    // messages, and the way back to the default without the UI's help.
    paintConversation(){
      const panel=$("ui-conversation");
      if(!panel) return;
      panel.replaceChildren();
      const now=this.describe(this.conversation);
      this.line(panel,now.state==="active"
        ?"Conversation design: "+now.agent_definition_id+" / "+now.component_key+" (revision "+this.conversation.revision+")"
        :now.state==="unsupported"?"Conversation selection is not supported by this app. Restore the default.":"Conversation design: default");
      if(this.conversationNote) this.line(panel,this.conversationNote,"muted");
      this.line(panel,"Your chosen model and existing access still govern every call. A change applies to future messages, not work already started.","muted");
      panel.appendChild(this.button("Restore default conversation",()=>this.restoreDefaultConversation(),
        this.selecting||(now.state==="default"&&!this.ambiguous)));
      if(this.previousTurn) panel.appendChild(this.button("Restore previous conversation",
        ()=>this.restorePreviousConversation(),this.selecting));
    },
    open(){
      if(!this.enabled) return;
      this.paint();
      const dialog=$("ui-dialog");
      if(!dialog.open) dialog.showModal();
    },
    init(){
      $("btn-ui-switch").addEventListener("click",()=>this.open());
      $("btn-ui-refresh").addEventListener("click",()=>this.load());
      $("btn-ui-close").addEventListener("click",()=>$("ui-dialog").close());
      this.paintHeader();
    }
  };
