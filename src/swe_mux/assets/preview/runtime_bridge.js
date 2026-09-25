(function(){
// swe-mux preview runtime bridge. Injected into every proxied preview document by
// `preview_transport.rewrite_preview_html`, which replaces the two placeholders.
// Contract (design/features/processes-and-previews.md, "Preview contract"): every
// rewrite is idempotent, the page's own origin is never a Project service, and no
// element attribute is rewritten without bound inside one task.
const prefix=__MUX_PREVIEW_PREFIX__;
const projectRoutes=__MUX_PROJECT_ROUTES__;
// A client-side router reads location.pathname directly and Location is not
// patchable, so the mount point cannot be hidden from it the way asset URLs are.
// Advertise it instead: an app passes this to its router's basename (React Router,
// vue-router, SvelteKit) and falls back to "/" when it is not inside a preview.
window.__MUX_PREVIEW_BASE__=prefix;
const canonicalOrigin=function(url){
  let protocol=url.protocol;
  if(protocol==="ws:")protocol="http:";
  if(protocol==="wss:")protocol="https:";
  let hostname=url.hostname.toLowerCase();
  if(hostname==="localhost"||hostname==="0.0.0.0")hostname="127.0.0.1";
  if(hostname==="[::]"||hostname==="::")hostname="[::1]";
  if(hostname.includes(":")&&!hostname.startsWith("["))hostname="["+hostname+"]";
  const defaultPort=(protocol==="http:"&&url.port==="80")||(protocol==="https:"&&url.port==="443");
  return protocol+"//"+hostname+(url.port&&!defaultPort?":"+url.port:"");
};
// This document is served by swe-mux, so its own origin is swe-mux itself. A route
// naming it would make every same-origin URL look like a Project service, and
// re-prefixing an already routed URL never converges: that pair froze the desktop
// app on 2026-09-24.
const pageOrigin=canonicalOrigin(new URL(location.href));
const alreadyRouted=function(url){
  return url.host===location.host&&url.pathname.startsWith("/preview/");
};
const serviceRoute=function(url){
  if(alreadyRouted(url))return undefined;
  const origin=canonicalOrigin(url);
  if(origin===pageOrigin)return undefined;
  return projectRoutes[origin];
};
const route=function(value){
  try {
    const url=new URL(String(value),location.href);
    if(alreadyRouted(url))return url.toString();
    const projectPrefix=serviceRoute(url);
    if(projectPrefix){
      url.protocol=location.protocol==="https:"?(url.protocol.startsWith("ws")?"wss:":"https:"):(url.protocol.startsWith("ws")?"ws:":"http:");
      url.host=location.host;
      url.pathname=projectPrefix+url.pathname.replace(/^\/+/,"");
    } else if(url.host===location.host){
      url.pathname=prefix+url.pathname.replace(/^\/+/,"");
    }
    return url.toString();
  } catch (_) { return value; }
};
const urlAttributes=new Set(["src","href","action"]);
const routeAttribute=function(value){
  const raw=String(value);
  if(raw.startsWith("/")&&!raw.startsWith("//")){
    return raw.startsWith("/preview/")?value:route(raw);
  }
  try {
    if(serviceRoute(new URL(raw,location.href)))return route(raw);
  } catch (_) {}
  return value;
};
// Rewrites per element attribute are bounded inside one task. Every rewrite above
// is idempotent, so a correct page never comes near this; it exists so a page that
// fights the bridge (or a future non-idempotent rule) degrades to one unrouted URL
// and a console warning instead of a renderer that never yields again.
const REWRITE_BUDGET=16;
let rewriteCounts=null;
let budgetWarned=false;
const spendRewrite=function(element,name){
  if(rewriteCounts===null){
    rewriteCounts=new WeakMap();
    setTimeout(function(){rewriteCounts=null;},0);
  }
  let counts=rewriteCounts.get(element);
  if(counts===undefined){counts=new Map();rewriteCounts.set(element,counts);}
  const spent=(counts.get(name)||0)+1;
  counts.set(name,spent);
  if(spent<=REWRITE_BUDGET)return true;
  if(!budgetWarned){
    budgetWarned=true;
    try { console.warn("swe-mux preview bridge: stopped rewriting "+String(element.tagName).toLowerCase()+" "+name+" after "+REWRITE_BUDGET+" rewrites in one task"); } catch (_) {}
  }
  return false;
};
const rewriteMarkup=function(value){
  const source=String(value);
  return source.replace(/(\b(?:src|href|action)\s*=\s*["'])([^"']+)/gi,
    function(_,start,target){return start+routeAttribute(target);});
};
const nativeSetAttribute=Element.prototype.setAttribute;
Element.prototype.setAttribute=function(name,value){
  const lowered=String(name).toLowerCase();
  const next=urlAttributes.has(lowered)&&spendRewrite(this,lowered)?routeAttribute(value):value;
  return nativeSetAttribute.call(this,name,next);
};
const patchMarkupProperty=function(name){
  const descriptor=Object.getOwnPropertyDescriptor(Element.prototype,name);
  if(!descriptor||typeof descriptor.set!=="function")return;
  try {
    Object.defineProperty(Element.prototype,name,{
      configurable:descriptor.configurable,
      enumerable:descriptor.enumerable,
      get:descriptor.get,
      set:function(value){descriptor.set.call(this,rewriteMarkup(value));}
    });
  } catch (_) {}
};
patchMarkupProperty("innerHTML");
patchMarkupProperty("outerHTML");
const nativeInsertAdjacentHTML=Element.prototype.insertAdjacentHTML;
Element.prototype.insertAdjacentHTML=function(position,value){
  return nativeInsertAdjacentHTML.call(this,position,rewriteMarkup(value));
};
const patchUrlProperty=function(constructorName,name){
  const constructor=window[constructorName];
  if(!constructor)return;
  const descriptor=Object.getOwnPropertyDescriptor(constructor.prototype,name);
  if(!descriptor||typeof descriptor.set!=="function")return;
  try {
    Object.defineProperty(constructor.prototype,name,{
      configurable:descriptor.configurable,
      enumerable:descriptor.enumerable,
      get:descriptor.get,
      set:function(value){descriptor.set.call(this,spendRewrite(this,name)?routeAttribute(value):value);}
    });
  } catch (_) {}
};
[
  ["HTMLImageElement","src"],
  ["HTMLScriptElement","src"],
  ["HTMLIFrameElement","src"],
  ["HTMLSourceElement","src"],
  ["HTMLMediaElement","src"],
  ["HTMLLinkElement","href"],
  ["HTMLAnchorElement","href"],
  ["HTMLAreaElement","href"],
  ["HTMLFormElement","action"]
].forEach(function(entry){patchUrlProperty(entry[0],entry[1]);});
const rerouteOwnAttributes=function(element){
  urlAttributes.forEach(function(name){
    if(!element.hasAttribute(name))return;
    const current=element.getAttribute(name);
    const next=routeAttribute(current);
    if(next!==current&&spendRewrite(element,name))nativeSetAttribute.call(element,name,next);
  });
};
const rerouteTree=function(node){
  if(!(node instanceof Element))return;
  rerouteOwnAttributes(node);
  node.querySelectorAll("[src],[href],[action]").forEach(rerouteOwnAttributes);
};
new MutationObserver(function(records){
  records.forEach(function(record){
    if(record.type==="attributes")rerouteOwnAttributes(record.target);
    else record.addedNodes.forEach(rerouteTree);
  });
}).observe(document,{subtree:true,childList:true,attributes:true,
  attributeFilter:["src","href","action"]});
const NativeWebSocket=window.WebSocket;
window.WebSocket=class extends NativeWebSocket{
  constructor(url,protocols){super(route(url),protocols);}
};
const nativeFetch=window.fetch.bind(window);
window.fetch=function(input,init){
  if(input instanceof Request) input=new Request(route(input.url),input);
  else input=route(input);
  return nativeFetch(input,init);
};
const nativeOpen=XMLHttpRequest.prototype.open;
XMLHttpRequest.prototype.open=function(_method,url){
  const args=Array.prototype.slice.call(arguments);args[1]=route(url);
  return nativeOpen.apply(this,args);
};
if(window.EventSource){
  const NativeEventSource=window.EventSource;
  window.EventSource=class extends NativeEventSource{
    constructor(url,init){super(route(url),init);}
  };
}
// Tell the pane which page this is and what it loaded. The pane cannot read a
// sandboxed document's location, so without this its refresh returned to the
// preview's root instead of reloading the page on screen, and it had nothing to
// watch for a file server that cannot push a reload. Only paths under this
// preview's own route are reported, relative to it; the pane validates them again.
const ownPath=function(value){
  try {
    const url=new URL(String(value),location.href);
    if(url.host!==location.host||!url.pathname.startsWith(prefix))return undefined;
    return url.pathname.slice(prefix.length)+url.search+url.hash;
  } catch (_) { return undefined; }
};
const RESOURCE_LIMIT=23;
const report=function(){
  if(window.parent===window)return;
  const path=ownPath(location.href);
  if(path===undefined)return;
  const resources=[];
  try {
    performance.getEntriesByType("resource").forEach(function(entry){
      const initiator=String(entry.initiatorType||"");
      if(initiator==="fetch"||initiator==="xmlhttprequest"||initiator==="beacon")return;
      const own=ownPath(entry.name);
      if(own!==undefined&&own!==path&&resources.indexOf(own)<0&&resources.length<RESOURCE_LIMIT)resources.push(own);
    });
  } catch (_) {}
  try { window.parent.postMessage({source:"swe-mux-preview",type:"location",path:path,resources:resources},"*"); } catch (_) {}
};
report();
window.addEventListener("load",report);
window.addEventListener("popstate",report);
window.addEventListener("hashchange",report);
["pushState","replaceState"].forEach(function(name){
  const native=history[name];
  if(typeof native!=="function")return;
  history[name]=function(){
    const result=native.apply(this,arguments);
    report();
    return result;
  };
});
})();
