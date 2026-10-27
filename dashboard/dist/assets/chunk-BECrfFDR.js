import{_ as e,d as t,i as n,l as r,m as i,p as a}from"./chunk-BhvuB6f9.js";import{i as o,n as s}from"./chunk-CI4nmvus.js";import{n as c,o as l,t as u}from"./chunk-CUrI0gct.js";import{C as d,S as f,i as p,n as m,s as h,w as g,x as _}from"./app-Dr2GQXJe.js";import"./chunk-YaSI1I2G.js";import{n as v,t as y}from"./chunk-CLP-vGrM.js";var b=`TandemApp’s controller does not run on this server yet (SRV-723), so the fleet API does not answer here.`,x=e=>new Date(e*1e3).toLocaleTimeString(`en-GB`,{hour:`2-digit`,minute:`2-digit`,second:`2-digit`});async function S(e){try{return await e()}catch(e){if(o(e).status===404)return`absent`;throw e}}var C=class extends v{constructor(...e){super(...e),this.fleetState=null,this.offFleet=null}connectedCallback(){super.connectedCallback(),this.offFleet=h(e=>this.fleetState=e)}disconnectedCallback(){this.offFleet?.(),this.offFleet=null,super.disconnectedCallback()}get nodes(){return this.fleetState?.nodes??[]}node(e){return this.nodes.find(t=>t.id===e)}};r([t()],C.prototype,`fleetState`,void 0);var w=[[`all`,`All levels`],[`debug`,`Debug and up`],[`info`,`Info and up`],[`warn`,`Warn and error`],[`error`,`Errors`]],T=class extends C{constructor(...e){super(...e),this.lines=[],this.boxOnly=!1,this.error=null,this.loaded=!1,this.feed=new y(e=>this.load(e),2e3),this.asked=``}connectedCallback(){super.connectedCallback(),this.ref.paused||this.feed.start()}disconnectedCallback(){this.feed.stop(),super.disconnectedCallback()}updated(){let e=this.ref;e.paused?this.feed.stop():this.isConnected&&this.feed.start();let t=`${e.node}|${e.level}|${e.q}`;t!==this.asked&&(this.asked=t,this.loaded=!1,this.feed.refresh());let n=this.querySelector(`.wm-log`);n&&n.dataset.follow!==`0`&&(n.scrollTop=n.scrollHeight)}async load(e){let t=this.ref,n=typeof t.node==`string`?t.node:`all`,r=t.level||`all`,i=typeof t.q==`string`?t.q:``;try{let t=this.boxOnly?`absent`:await S(()=>l.logs({node:n,level:r,q:i,limit:300}));if(t!==`absent`){e()&&([this.lines,this.error,this.loaded]=[t.lines,null,!0]);return}if(this.boxOnly=!0,n!==`all`){e()&&([this.lines,this.loaded]=[[],!0]);return}let a=await s.logsJson({level:r===`all`?void 0:r===`warn`?`warning`:r,grep:i||null,backlog:300});e()&&([this.lines,this.error,this.loaded]=[a.lines.map(e=>({t:Date.parse(e.ts)/1e3,node:``,level:e.level===`warning`?`warn`:e.level,source:e.source,msg:e.msg})),null,!0])}catch(t){e()&&([this.error,this.loaded]=[o(t).message,!0])}}render(){let t=this.ref,r=typeof t.node==`string`?t.node:`all`,a=!!t.paused,o=this.boxOnly&&r!==`all`?this.node(r)?.alias??r:null;return e`<div class="wm-pane">
      <div class="wm-bar">
        <select class="wm-in" aria-label="Spark" @change=${e=>this.setRef({node:e.target.value})}>
          <option value="all" ?selected=${r===`all`}>${this.boxOnly?`This box`:`All Sparks`}</option>
          ${this.nodes.map(t=>e`<option value=${t.id} ?selected=${r===t.id}>${t.alias}</option>`)}
        </select>
        <select class="wm-in" aria-label="Level" @change=${e=>this.setRef({level:e.target.value})}>
          ${w.map(([n,r])=>e`<option value=${n} ?selected=${(t.level||`all`)===n}>${r}</option>`)}
        </select>
        <input class="wm-in wm-filter" type="search" placeholder="Filter" aria-label="Filter" .value=${typeof t.q==`string`?t.q:``} @change=${e=>this.setRef({q:e.target.value})} />
        <span class="grow"></span>
        <button type="button" class="btn btn-ghost btn-sm" aria-pressed=${a} @click=${()=>this.setRef({paused:!a})}>${n(a?`play`:`pause`)} ${a?`Follow`:`Pause`}</button>
      </div>
      ${this.boxOnly?e`<p class="wm-note muted small">${o?`${o}’s log comes through its TandemNode agent and TandemApp’s controller; neither answers on this server yet.`:`The box’s own log. ${b}`}</p>`:i}
      ${this.error?e`<p class="wm-note err-ink small">${this.error}</p>`:i}
      <div class="wm-log num" role="log" aria-live="off" data-follow=${a?`0`:`1`}>
        ${this.loaded?!this.lines.length&&!o?e`<p class="muted small wm-empty">No lines match.</p>`:i:e`<p class="muted small wm-empty">loading</p>`}
        ${this.lines.map(t=>{let n=this.node(t.node);return e`<div class="wm-log-l is-${t.level}"><span class="t">${x(t.t)}</span><span class="n">${n?m(n,{status:!1}):e`<span class="muted">${t.node||`box`}</span>`}</span><span class="lv">${t.level}</span><span class="m" title=${t.msg}>${t.msg}</span></div>`})}
      </div>
    </div>`}};r([t()],T.prototype,`lines`,void 0),r([t()],T.prototype,`boxOnly`,void 0),r([t()],T.prototype,`error`,void 0),r([t()],T.prototype,`loaded`,void 0),T=r([a(`qse-win-logs`)],T);var E=class extends C{constructor(...e){super(...e),this.data=null,this.error=null,this.scope=_(),this.feed=new y(e=>this.load(e),1e4),this.offScope=null,this.asked=``}connectedCallback(){super.connectedCallback(),this.offScope=f(e=>{this.scope=e,(this.ref.scope===`page`||!this.ref.scope)&&this.feed.refresh()}),this.feed.start()}disconnectedCallback(){this.offScope?.(),this.feed.stop(),super.disconnectedCallback()}updated(){let e=`${this.metric}|${this.ref.scope}`;e!==this.asked&&(this.asked=e,this.data=null,this.feed.refresh())}get metric(){return c.includes(this.ref.metric)?this.ref.metric:`tps`}source(){let e=typeof this.ref.scope==`string`?this.ref.scope:`page`;return e===`page`?g(this.fleetState?d(this.scope,this.fleetState):this.scope):e}async load(e){try{let t=await S(()=>l.metrics({source:this.source(),metric:this.metric,range:this.scope.range}));e()&&([this.data,this.error]=[t,null])}catch(t){e()&&(this.error=o(t).message)}}render(){let t=u[this.metric],n=typeof this.ref.scope==`string`?this.ref.scope:`page`,r=this.data,i;if(r===`absent`)i=e`<p class="wm-note muted small">${b}</p>`;else if(this.error)i=e`<p class="wm-note err-ink small">${this.error}</p>`;else if(!r)i=e`<p class="muted small wm-empty">loading</p>`;else{let n=r.series.map(e=>{let t=this.node(e.node);return{id:e.node,alias:t?.alias??e.node,colour:t?.colour??1,values:e.values}});i=e`<qse-fleet-chart chart-id=${`win-${this.win?.id}-${this.metric}`} .series=${n} agg=${r.agg} unit=${r.unit} .t0=${r.t0} .step=${r.step_s} .decimals=${t.decimals} .cap=${r.cap} .max=${t.max??null} mode=${r.agg===`sum`?`stacked`:`lines`} aria-label=${`${t.label} over ${r.range}`}></qse-fleet-chart>`}return e`<div class="wm-pane">
      <div class="wm-bar">
        <select class="wm-in" aria-label="Metric" @change=${e=>this.setRef({metric:e.target.value})}>
          ${c.map(t=>e`<option value=${t} ?selected=${t===this.metric}>${u[t].label} (${u[t].unit})</option>`)}
        </select>
        <select class="wm-in" aria-label="Scope" @change=${e=>this.setRef({scope:e.target.value})}>
          <option value="page" ?selected=${n===`page`}>Follow the scope bar</option>
          <option value="all" ?selected=${n===`all`}>All Sparks</option>
          ${this.nodes.map(t=>e`<option value=${`node:${t.id}`} ?selected=${n===`node:${t.id}`}>${t.alias}</option>`)}
        </select>
      </div>
      <div class="wm-chart">${i}</div>
    </div>`}};r([t()],E.prototype,`data`,void 0),r([t()],E.prototype,`error`,void 0),r([t()],E.prototype,`scope`,void 0),E=r([a(`qse-win-chart`)],E);var D=[[`tps`,`tok/s`],[`temp`,`°C`],[`power`,`W`],[`gpu`,`% GPU`]],O=class extends C{constructor(...e){super(...e),this.now={},this.memory=null,this.absent=!1,this.feed=new y(e=>this.load(e),1e4),this.asked=``}connectedCallback(){super.connectedCallback(),this.feed.start()}disconnectedCallback(){this.feed.stop(),super.disconnectedCallback()}updated(){let e=this.nodeId;e&&e!==this.asked&&(this.asked=e,this.now={},this.memory=null,this.feed.refresh())}get nodeId(){return typeof this.ref.node==`string`?this.ref.node:this.nodes[0]?.id??null}async load(e){let t=this.nodeId;if(!t)return;let n={};for(let[r]of D){let i=await S(()=>l.metrics({source:`node:${t}`,metric:r,range:`5m`}));if(i===`absent`){e()&&(this.absent=!0);return}n[r]=[...i.series[0]?.values??[]].reverse().find(e=>e!=null)??null}let r=await S(()=>l.memory());e()&&t===this.nodeId&&(this.now=n,this.memory=r===`absent`?null:r.nodes.find(e=>e.node===t)??null)}render(){let t=this.nodeId,r=this.node(t),a=e`<div class="wm-bar">
      <select class="wm-in" aria-label="Spark" @change=${e=>this.setRef({node:e.target.value})}>
        ${this.nodes.map(n=>e`<option value=${n.id} ?selected=${n.id===t}>${n.alias}</option>`)}
      </select>
      <span class="grow"></span>
      ${t?e`<a class="btn btn-ghost btn-sm" href=${`#/sparks/${encodeURIComponent(t)}`}>Open page</a>`:i}
    </div>`;if(this.absent||!this.fleetState)return e`<div class="wm-pane">${a}<p class="wm-note muted small">${this.fleetState?b:`The node inspector shows a Spark of the fleet. ${b}`}</p></div>`;if(!r)return e`<div class="wm-pane">${a}<p class="wm-note muted small">No Spark ${t??``} in this fleet.</p></div>`;let o=this.fleetState.deployments.filter(e=>e.members.includes(r.id)),s=this.memory,c=s?s.weights+s.kv+s.cache+s.other:null,l=r.lease_end?`${Math.max(0,Math.round((r.lease_end-Date.now()/1e3)/86400))} days left`:r.owned?`owned`:`none`;return e`<div class="wm-pane">
      ${a}
      <div class="wm-insp">
        <div class="wm-insp-h">${m(r,{lg:!0})}<span class="small muted">${p(r)}</span></div>
        <div class="wm-insp-figs">
          ${D.map(([t,n])=>e`<div><span class="muted small">${u[t].label}</span><b class="num">${this.now[t]==null?`—`:Math.round(this.now[t])}</b><span class="muted small">${n}</span></div>`)}
        </div>
        ${s&&c!=null?e`<div class="wm-insp-mem"><div class="small muted">Unified memory <span class="num">${c.toFixed(1)} of ${s.usable} GiB</span></div><div class="meter"><span class="meter-fill" style=${`width:${Math.min(100,c/s.usable*100)}%`}></span></div></div>`:i}
        <dl class="kv">
          <dt>serves</dt><dd>${o.length?o.map(e=>e.name).join(`, `):`nothing`}</dd>
          <dt>reach</dt><dd>${r.transport===`tunnel`?`SSH tunnel`:`LAN`} · <span class="num">${r.address}</span></dd>
          <dt>hardware</dt><dd>${r.hw}</dd>
          <dt>version</dt><dd>${r.version}</dd>
          <dt>lease</dt><dd>${l}</dd>
        </dl>
        ${r.status===`alert`?e`<p class="wm-note warn-ink small">⚠ ${r.status_text}</p>`:i}
        <div class="wm-insp-acts">
          <button type="button" class="btn btn-sm" @click=${()=>this.openWindow(`terminal`,{node:r.id})}>${n(`terminal`)} Terminal</button>
          <button type="button" class="btn btn-sm" @click=${()=>this.openWindow(`logs`,{node:r.id})}>${n(`doc`)} Logs</button>
          <button type="button" class="btn btn-ghost btn-sm" @click=${()=>this.openWindow(`chart`,{metric:`tps`,scope:`node:${r.id}`})}>${n(`performance`)} Chart</button>
        </div>
      </div>
    </div>`}};r([t()],O.prototype,`now`,void 0),r([t()],O.prototype,`memory`,void 0),r([t()],O.prototype,`absent`,void 0),O=r([a(`qse-win-inspector`)],O);export{E as QseWinChart,O as QseWinInspector,T as QseWinLogs};