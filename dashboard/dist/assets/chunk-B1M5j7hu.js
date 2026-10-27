import{_ as e,d as t,f as n,l as r,m as i,p as a,y as o}from"./chunk-BhvuB6f9.js";import{d as s,f as c,o as l,r as u,s as d,u as f}from"./chunk-qnF8mJGs.js";var p=class extends f{constructor(...e){super(...e),this.values=[],this.mode=`ribbon`,this.color=`cobalt`,this.format=e=>String(Math.round(e)),this.unit=``,this.step=1,this.ariaLabelText=``,this.hover=null,this.glowId=c(`sp`),this.onKey=e=>{let t=this.values.length;if(t){if(e.key===`ArrowLeft`||e.key===`ArrowRight`){e.preventDefault();let n=this.hover??t-1;this.hover=Math.max(0,Math.min(t-1,n+(e.key===`ArrowLeft`?-1:1)))}else e.key===`Home`?this.hover=0:e.key===`End`?this.hover=t-1:e.key===`Escape`&&(this.hover=null)}}}setHover(e){let t=this.getBoundingClientRect(),n=`touches`in e?e.touches[0]?.clientX:e.clientX;n!=null&&(this.hover=d(n-t.left,0,t.width,this.values.length))}render(){let t=this.width||300,n=this.height||44,r=this.values.length,a=this.values.filter(e=>e!=null&&Number.isFinite(e)),c=a.length?Math.max(...a):1,d=u([0,c>0?c*1.1:1],[n-3,4]),f=u([0,Math.max(1,r-1)],[3,t-3]),p=this.values.map((e,t)=>e==null||!Number.isFinite(e)?null:{x:f(t),y:d(e)}),m=s(this.color),h=-1;for(let e=r-1;e>=0;e--)if(p[e]){h=e;break}let g=this.hover!=null&&this.hover<r?this.hover:null,_=g==null?null:this.values[g],v=g==null?0:(r-1-g)*this.step;return e`<div
      class="spark spark-${this.mode}"
      tabindex="0"
      role="img"
      aria-label=${this.ariaLabelText||`last ${Math.round(r*this.step)} seconds`}
      @mousemove=${this.setHover}
      @mouseleave=${()=>this.hover=null}
      @touchstart=${this.setHover}
      @touchmove=${this.setHover}
      @keydown=${this.onKey}
    >
      <svg width=${t} height=${n} viewBox="0 0 ${t} ${n}" preserveAspectRatio="none">
        <defs>
          <filter id="${this.glowId}-glow" x="-5%" y="-60%" width="110%" height="220%"><feGaussianBlur stdDeviation="3.5" /></filter>
        </defs>
        ${a.length?i:o`<line class="spark-base" x1="3" x2=${t-3} y1=${n-3} y2=${n-3} />`}
        ${this.mode===`ribbon`&&a.length?o`
            <path d=${l(p)} fill="none" stroke=${m} stroke-width="7" stroke-linecap="round" stroke-linejoin="round" opacity="0.26" filter="url(#${this.glowId}-glow)" />
            <path d=${l(p)} fill="none" stroke=${m} stroke-width="1.6" stroke-linecap="round" stroke-linejoin="round" />`:i}
        ${this.mode===`dots`?p.map(e=>e?o`<circle cx=${e.x} cy=${e.y} r="2.6" fill=${m} opacity="0.85" />`:i):i}
        ${h>=0?o`<circle class="spark-now" cx=${p[h].x} cy=${p[h].y} r=${this.mode===`dots`?3.4:3} />`:i}
        ${g!=null&&p[g]?o`<line class="crosshair" x1=${p[g].x} x2=${p[g].x} y1="2" y2=${n-2} /><circle class="ring" cx=${p[g].x} cy=${p[g].y} r="4" fill=${m} />`:i}
      </svg>
      ${g==null?i:e`<div class="tip tip-spark" style="left:${Math.min(Math.max(f(g),70),t-70)}px" role="status">
            <span class="num">${_==null?`no sample`:this.format(_)+(this.unit?` `+this.unit:``)}</span><span class="muted">${v===0?`now`:`${v} s ago`}</span>
          </div>`}
    </div>`}};r([n({attribute:!1})],p.prototype,`values`,void 0),r([n({type:String})],p.prototype,`mode`,void 0),r([n({type:String})],p.prototype,`color`,void 0),r([n({attribute:!1})],p.prototype,`format`,void 0),r([n({type:String})],p.prototype,`unit`,void 0),r([n({type:Number})],p.prototype,`step`,void 0),r([n({type:String,attribute:`aria-label`})],p.prototype,`ariaLabelText`,void 0),r([t()],p.prototype,`hover`,void 0),p=r([a(`qse-spark`)],p);var m=(function(){let e=typeof document<`u`&&document.createElement(`link`).relList;return e&&e.supports&&e.supports(`modulepreload`)?`modulepreload`:`preload`})(),h=function(e,t){return new URL(e,t).href},g={},_=function(e){return e.pathname.endsWith(`.css`)},v=function(e,t,n){let r=Promise.resolve();if(t&&t.length>0){let e,i=document.querySelector(`meta[property=csp-nonce]`),a=i?.nonce||i?.getAttribute(`nonce`);function o(e){return Promise.all(e.map(e=>Promise.resolve(e).then(e=>({status:`fulfilled`,value:e}),e=>({status:`rejected`,reason:e}))))}function s(e){return import.meta.resolve?new URL(import.meta.resolve(e)):new URL(e,import.meta.url)}r=o(t.map(t=>{t=h(t,n);let r=s(t);if(r.href in g)return;g[r.href]=!0;let i=_(r);if(e===void 0){e={all:new Set,styles:new Set};let t=document.getElementsByTagName(`link`);for(let n=t.length-1;n>=0;n--){let r=t[n];e.all.add(r.href),r.rel===`stylesheet`&&e.styles.add(r.href)}}if((i?e.styles:e.all).has(r.href))return;let o=document.createElement(`link`);if(o.rel=i?`stylesheet`:m,i||(o.as=`script`),o.crossOrigin=``,o.href=r.href,a&&o.setAttribute(`nonce`,a),document.head.appendChild(o),i)return new Promise((e,t)=>{o.addEventListener(`load`,e),o.addEventListener(`error`,()=>t(Error(`Unable to preload CSS for ${r}`)))})}).filter(e=>e!==void 0))}function i(e){let t=new Event(`vite:preloadError`,{cancelable:!0});if(t.payload=e,window.dispatchEvent(t),!t.defaultPrevented)throw e}return r.then(t=>{for(let e of t||[])e.status===`rejected`&&i(e.reason);return e().catch(i)})};export{v as t};