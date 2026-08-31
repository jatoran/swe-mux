let e=null;const n=new Set;function o(t){e=t;for(const i of n)i(e)}function r(t){return t(e),n.add(t),()=>{n.delete(t)}}export{o as reportContinuityFailure,r as subscribeContinuityFailure};
