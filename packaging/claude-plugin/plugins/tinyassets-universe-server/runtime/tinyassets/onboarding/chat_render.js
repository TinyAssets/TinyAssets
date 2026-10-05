// Reply data is never HTML or executable code. Keep source visible until a visual succeeds.
const ChatRender = (() => {
  const nonce = document.currentScript.nonce;
  let mermaidLoad, diagramId = 0;
  const text = (parent, value) => parent.appendChild(document.createTextNode(value));
  function httpURL(value){
    if(!/^https?:\/\//i.test(value) || /[\u0000-\u0020\u007f]/.test(value)) return null;
    try{const url=new URL(value); return ["http:","https:"].includes(url.protocol)?url.href:null;}
    catch(_){return null;}
  }
  function inline(parent, source){
    const tokens=/`([^`\n]+)`|\[([^\]\n]+)\]\(([^\s]+)\)|(https?:\/\/[^\s<>]+)/gi;
    let at=0;
    for(const match of source.matchAll(tokens)){
      text(parent,source.slice(at,match.index)); at=match.index+match[0].length;
      if(match[1]){const code=document.createElement("code");code.textContent=match[1];parent.appendChild(code);continue;}
      let url=match[3]||match[4], suffix="";
      if(match[4]){
        const trimmed=url.replace(/[.,!?;:]+$/,""); suffix=url.slice(trimmed.length);url=trimmed;
        while(url.endsWith(")") && (url.match(/\)/g)||[]).length>(url.match(/\(/g)||[]).length){url=url.slice(0,-1);suffix=")"+suffix;}
      }
      const href=httpURL(url);
      if(!href){text(parent,match[0]);continue;}
      const a=document.createElement("a"); a.href=href;a.target="_blank";a.rel="noopener noreferrer";
      a.addEventListener("click",event=>{
        if(typeof NATIVE!=="undefined" && NATIVE){event.preventDefault();void openExternal(href);}
      });
      a.textContent=match[2]||url;parent.appendChild(a);text(parent,suffix);
    }
    text(parent,source.slice(at));
  }
  function svgNode(name, attrs={}, label){
    const node=document.createElementNS("http://www.w3.org/2000/svg",name);
    for(const [key,value] of Object.entries(attrs)) node.setAttribute(key,String(value));
    if(label!==undefined) node.textContent=label;
    return node;
  }
  function chart(source){
    const spec=JSON.parse(source), labels=spec.labels, series=spec.series;
    if(!["bar","line","stacked"].includes(spec.type) || !Array.isArray(labels) || !labels.length || labels.length>100 ||
       !labels.every(v=>typeof v==="string" && v.length<=200) || !Array.isArray(series) || !series.length || series.length>12 ||
       !series.every(s=>s && typeof s.name==="string" && s.name.length<=200 && Array.isArray(s.values) &&
         s.values.length===labels.length && s.values.every(v=>typeof v==="number" && Number.isFinite(v) && Math.abs(v)<=1e12)))
      throw new Error("Invalid chart");
    const colors=["#2563eb","#d97706","#059669","#9333ea","#dc2626","#0891b2"];
    const totals=labels.map((_,i)=>series.reduce((v,s)=>v+Math.max(0,s.values[i]),0));
    const negatives=labels.map((_,i)=>series.reduce((v,s)=>v+Math.min(0,s.values[i]),0));
    const values=series.flatMap(s=>s.values);
    const lo=Math.min(0,...(spec.type==="stacked"?negatives:values));
    const hi=Math.max(0,...(spec.type==="stacked"?totals:values));
    const y=v=>230-(v-lo)/(hi-lo||1)*190, step=500/labels.length;
    const svg=svgNode("svg",{viewBox:`0 0 640 ${300+series.length*20}`,role:"img","aria-label":typeof spec.title==="string"?spec.title:"Chart"});
    svg.appendChild(svgNode("title",{},typeof spec.title==="string"?spec.title:"Chart"));
    svg.appendChild(svgNode("line",{x1:80,x2:580,y1:y(0),y2:y(0),stroke:"currentColor"}));
    for(let i=0;i<=4;i++){const v=lo+(hi-lo)*i/4;svg.appendChild(svgNode("text",{x:74,y:y(v)+4,"text-anchor":"end","font-size":12,fill:"currentColor"},Number(v.toPrecision(4))));}
    labels.forEach((label,i)=>svg.appendChild(svgNode("text",{x:80+(i+.5)*step,y:250,"text-anchor":"middle","font-size":12,fill:"currentColor"},label)));
    const pos=labels.map(()=>0), neg=labels.map(()=>0);
    series.forEach((s,j)=>{
      const color=colors[j%colors.length];
      if(spec.type==="line"){
        svg.appendChild(svgNode("polyline",{points:s.values.map((v,i)=>`${80+(i+.5)*step},${y(v)}`).join(" "),fill:"none",stroke:color,"stroke-width":3}));
      }
      s.values.forEach((v,i)=>{
        let shape;
        if(spec.type==="line") shape=svgNode("circle",{cx:80+(i+.5)*step,cy:y(v),r:4,fill:color});
        else{
          const stack=spec.type==="stacked", acc=v>=0?pos:neg, start=stack?acc[i]:0;
          const width=step*.8/(stack?1:series.length);
          shape=svgNode("rect",{x:80+i*step+step*.1+(stack?0:j*width),y:Math.min(y(start),y(start+v)),width:Math.max(.1,width-1),height:Math.abs(y(start+v)-y(start)),fill:color});
          if(stack)acc[i]+=v;
        }
        shape.appendChild(svgNode("title",{},`${s.name}: ${labels[i]} = ${v}`));svg.appendChild(shape);
      });
      svg.appendChild(svgNode("text",{x:80,y:280+j*20,fill:color,"font-size":14},s.name));
    });
    return svg;
  }
  function loadMermaid(){
    if(!mermaidLoad) mermaidLoad=new Promise((resolve,reject)=>{
      const script=document.createElement("script");script.nonce=nonce;
      // Vendored Mermaid 11.12.0 (MIT); the existing content-keyed module route.
      script.src="__TA_MERMAID_URL__";
      script.integrity="sha384-o+g/BxPwhi0C3RK7oQBxQuNimeafQ3GE/ST4iT2BxVI4Wzt60SH4pq9iXVYujjaS";
      script.crossOrigin="anonymous";script.referrerPolicy="no-referrer";
      script.onload=()=>resolve(window.mermaid);
      script.onerror=()=>{script.remove();mermaidLoad=null;reject(new Error("Diagram library unavailable"));};
      document.head.appendChild(script);
    });
    return mermaidLoad;
  }
  async function diagram(source, pre){
    try{
      // Per-diagram configuration cannot override our renderer or introduce HTML/CSS.
      if(source.length>50000 || /%%\s*\{|^\s*---|\b(?:click|href|classDef|style)\b/i.test(source)) return;
      const mermaid=await loadMermaid();
      mermaid.initialize({startOnLoad:false,securityLevel:"strict",htmlLabels:false,
        flowchart:{htmlLabels:false},suppressErrorRendering:true,maxTextSize:50000,maxEdges:500});
      const {svg}=await mermaid.render("chat_diagram_"+(++diagramId),source);
      const doc=new DOMParser().parseFromString(svg,"image/svg+xml");
      if(doc.querySelector("parsererror") || doc.documentElement.localName!=="svg") return;
      // Defense in depth: no active elements, foreign HTML, external references or handlers.
      doc.querySelectorAll("script,foreignObject,a,image,use,iframe,object,embed,animate,set,animateTransform").forEach(n=>n.remove());
      for(const node of doc.querySelectorAll("*")) for(const attr of Array.from(node.attributes)){
        if(/^on/i.test(attr.name) || /href/i.test(attr.name) || /(?:https?:|javascript:|data:|@import|url\(\s*[^#])/i.test(attr.value)) node.removeAttribute(attr.name);
      }
      for(const style of doc.querySelectorAll("style")){
        if(/@import|(?:https?:|javascript:|data:)|url\(\s*[^#]/i.test(style.textContent)) style.remove();
      }
      // SVG in an image has no scripting or external resource access, and needs no CSP relaxation.
      const img=document.createElement("img");img.alt="Mermaid diagram";img.className="chat-diagram";
      img.onload=()=>{if(pre.parentNode)pre.replaceWith(img);};
      img.src="data:image/svg+xml;charset=utf-8,"+encodeURIComponent(new XMLSerializer().serializeToString(doc));
    }catch(_){ /* Original code is the explicit fallback. */ }
  }
  function fileChip(source, download){
    const spec=JSON.parse(source);
    if(!spec || typeof spec.path!=="string" || !spec.path || spec.path.length>4096 ||
       /[\\\u0000-\u001f]/.test(spec.path) || spec.path.split("/").includes("..") ||
       (spec.path.startsWith("/")&&!spec.path.startsWith("/u/"))) throw new Error("Invalid file");
    const name=typeof spec.name==="string"?spec.name:spec.path.split("/").pop();
    const button=document.createElement("button");button.type="button";button.className="chat-file";
    button.textContent="Download "+name;
    button.addEventListener("click",async()=>{
      button.disabled=true;button.textContent="Downloading "+name+"…";
      try{await download(spec.path,name);button.textContent="Download "+name;}
      catch(_){button.textContent="Download unavailable — retry "+name;}
      finally{button.disabled=false;}
    });
    return button;
  }
  function render(parent, source, download){
    source=String(source||"");parent.textContent="";
    const fences=/^```([^\n`]*)\r?\n([\s\S]*?)^```[ \t]*(?:\r?\n|$)/gm;
    let at=0;
    for(const match of source.matchAll(fences)){
      inline(parent,source.slice(at,match.index));at=match.index+match[0].length;
      const pre=document.createElement("pre"), code=document.createElement("code");code.textContent=match[2];pre.appendChild(code);parent.appendChild(pre);
      try{
        const language=match[1].trim().toLowerCase();
        if(language==="chart") pre.replaceWith(chart(match[2]));
        else if(language==="mermaid") void diagram(match[2],pre);
        else if(language==="file" && download) pre.replaceWith(fileChip(match[2],download));
      }catch(_){ /* Invalid data remains a code block. */ }
    }
    inline(parent,source.slice(at));
  }
  return {render,httpURL,chart};
})();
