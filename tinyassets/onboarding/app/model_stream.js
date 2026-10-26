// A progress notification is a temporary view, never a durable reply or authority.
const ModelStream=Object.freeze({
  decode(document,token){
    if(!document||document.jsonrpc!=="2.0"||document.method!=="notifications/progress"||
       Object.prototype.hasOwnProperty.call(document,"id"))return null;
    const params=document.params;
    if(!params||params.progressToken!==token||typeof params.message!=="string"||
       params.message.length>131072)return null;
    let event;try{event=JSON.parse(params.message);}catch(_error){return null;}
    if(!event||event.tinyassets_model_stream!==1||typeof event.operation_id!=="string"||
       !event.operation_id||event.operation_id.length>256)return null;
    if(event.type==="model_start")return event;
    if(event.type==="model_text_delta"&&typeof event.text==="string")return event;
    if(event.type==="model_end"&&["completed","interrupted"].includes(event.status))return event;
    return null;
  },
  open({current,create}){
    let operation=null,text="",preview=null,closed=false,ended=false;
    const clear=()=>{if(preview)preview.remove();preview=null;text="";};
    return {
      event(event){
        if(closed)return;
        if(!current()){closed=true;clear();return;}
        if(event.type==="model_start"){
          clear();operation=event.operation_id;ended=false;return;
        }
        if(!operation||event.operation_id!==operation||ended)return;
        if(event.type==="model_end"){
          ended=true;
          if(event.status==="interrupted")clear();
          return;
        }
        if(event.type!=="model_text_delta"||!event.text)return;
        // A bounded preview never affects the complete journaled response.
        text=(text+event.text).slice(-131072);
        if(!preview)preview=create();
        preview.write(text);
      },
      close(){closed=true;clear();}
    };
  }
});
