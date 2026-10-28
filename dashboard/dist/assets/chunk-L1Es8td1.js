import{c as e,f as t,g as n,i as r,p as i,u as a}from"./chunk-BlUUBWrt.js";import{i as o,n as s}from"./chunk-CI4nmvus.js";import{c,i as l,r as u}from"./chunk-CafD3mDP.js";import{n as d,o as f,r as p,t as m}from"./chunk-Dfbf08Ll.js";import{l as h,p as g,s as _}from"./app-DhhvfC_Q.js";import{n as v}from"./chunk-CKIq6wSf.js";import{n as y,t as b}from"./chunk-DPDNpVPV.js";var x=`TandemApp’s controller does not run on this server yet (SRV-723), so the fleet API does not answer here.`,S=e=>new Date(e*1e3).toLocaleTimeString(`en-GB`,{hour:`2-digit`,minute:`2-digit`,second:`2-digit`});async function C(e){try{return await e()}catch(e){if(o(e).status===404)return`absent`;throw e}}var w=class extends y{constructor(...e){super(...e),this.fleetState=null,this.offFleet=null}connectedCallback(){super.connectedCallback(),this.offFleet=g(e=>this.fleetState=e)}disconnectedCallback(){this.offFleet?.(),this.offFleet=null,super.disconnectedCallback()}get nodes(){return this.fleetState?.nodes??[]}node(e){return this.nodes.find(t=>t.id===e)}};e([a()],w.prototype,`fleetState`,void 0);var T=[[`every`,`Every line`],[`debug`,`Debug and up`],[`info`,`Info and up`],[`warn`,`Warn and error`],[`error`,`Errors`]],E=e=>T.some(([t])=>t===e)?e:`info`,D=class extends w{constructor(...e){super(...e),this.lines=[],this.boxOnly=!1,this.error=null,this.loaded=!1,this.feed=new b(e=>this.load(e),2e3),this.asked=``}connectedCallback(){super.connectedCallback(),this.ref.paused||this.feed.start()}disconnectedCallback(){this.feed.stop(),super.disconnectedCallback()}updated(){let e=this.ref;e.paused?this.feed.stop():this.isConnected&&this.feed.start();let t=`${e.node}|${e.level}|${e.q}`;t!==this.asked&&(this.asked=t,this.loaded=!1,this.feed.refresh());let n=this.querySelector(`.wm-log`);n&&n.dataset.follow!==`0`&&(n.scrollTop=n.scrollHeight)}async load(e){let t=this.ref,n=typeof t.node==`string`?t.node:`all`,r=E(t.level),i=typeof t.q==`string`?t.q:``;try{let t=this.boxOnly?`absent`:await C(()=>c.logs({node:n,level:r===`every`?`debug`:r,access:r===`every`,q:i,limit:300}));if(t!==`absent`){e()&&([this.lines,this.error,this.loaded]=[t.lines,null,!0]);return}if(this.boxOnly=!0,n!==`all`){e()&&([this.lines,this.loaded]=[[],!0]);return}let a=await s.logsJson({level:r===`every`?`debug`:r===`warn`?`warning`:r,grep:i||null,backlog:300});e()&&([this.lines,this.error,this.loaded]=[a.lines.map(e=>({t:Date.parse(e.ts)/1e3,node:``,level:e.level===`warning`?`warn`:e.level,source:e.source,msg:e.msg})),null,!0])}catch(t){e()&&([this.error,this.loaded]=[o(t).message,!0])}}render(){let e=this.ref,t=typeof e.node==`string`?e.node:`all`,a=!!e.paused,o=this.boxOnly&&t!==`all`?this.node(t)?.alias??t:null;return n`<div class="wm-pane">
      <div class="wm-bar">
        <select class="wm-in" aria-label="Spark" @change=${e=>this.setRef({node:e.target.value})}>
          <option value="all" ?selected=${t===`all`}>${this.boxOnly?`This box`:`All Sparks`}</option>
          ${this.nodes.map(e=>n`<option value=${e.id} ?selected=${t===e.id}>${e.alias}</option>`)}
        </select>
        <select class="wm-in" aria-label="Level" @change=${e=>this.setRef({level:e.target.value})}>
          ${T.map(([t,r])=>n`<option value=${t} ?selected=${E(e.level)===t}>${r}</option>`)}
        </select>
        <input class="wm-in wm-filter" type="search" placeholder="Filter" aria-label="Filter" .value=${typeof e.q==`string`?e.q:``} @change=${e=>this.setRef({q:e.target.value})} />
        <span class="grow"></span>
        <button type="button" class="btn btn-ghost btn-sm" aria-pressed=${a} @click=${()=>this.setRef({paused:!a})}>${r(a?`play`:`pause`)} ${a?`Follow`:`Pause`}</button>
      </div>
      ${this.boxOnly?n`<p class="wm-note muted small">${o?`${o}’s log comes through its TandemNode agent and TandemApp’s controller; neither answers on this server yet.`:`The box’s own log. ${x}`}</p>`:i}
      ${this.error?n`<p class="wm-note err-ink small">${this.error}</p>`:i}
      <div class="wm-log num" role="log" aria-live="off" data-follow=${a?`0`:`1`}>
        ${this.loaded?!this.lines.length&&!o?n`<p class="muted small wm-empty">No lines match.</p>`:i:n`<p class="muted small wm-empty">loading</p>`}
        ${this.lines.map(e=>{let t=this.node(e.node);return n`<div class="wm-log-l is-${e.level}"><span class="t">${S(e.t)}</span><span class="n">${t?_(t,{status:!1}):n`<span class="muted">${e.node||`box`}</span>`}</span><span class="lv">${e.level}</span><span class="m" title=${e.msg}>${e.msg}</span></div>`})}
      </div>
    </div>`}};e([a()],D.prototype,`lines`,void 0),e([a()],D.prototype,`boxOnly`,void 0),e([a()],D.prototype,`error`,void 0),e([a()],D.prototype,`loaded`,void 0),D=e([t(`qse-win-logs`)],D);var O=class extends w{constructor(...e){super(...e),this.data=null,this.marks=[],this.error=null,this.scope=m(),this.feed=new b(e=>this.load(e),1e4),this.offScope=null,this.asked=``}connectedCallback(){super.connectedCallback(),this.offScope=d(e=>{this.scope=e,(this.ref.scope===`page`||!this.ref.scope)&&this.feed.refresh()}),this.feed.start()}disconnectedCallback(){this.offScope?.(),this.feed.stop(),super.disconnectedCallback()}updated(){let e=`${this.metric}|${this.ref.scope}`;e!==this.asked&&(this.asked=e,this.data=null,this.feed.refresh())}get metric(){return l.includes(this.ref.metric)?this.ref.metric:`tps`}source(){let e=typeof this.ref.scope==`string`?this.ref.scope:`page`;return e===`page`?f(this.fleetState?p(this.scope,this.fleetState):this.scope):e}async load(e){try{let t=await C(()=>c.metrics({source:this.source(),metric:this.metric,range:this.scope.range})),n=t===`absent`?[]:await v(t.t0).catch(()=>[]);e()&&([this.data,this.marks,this.error]=[t,n,null])}catch(t){e()&&(this.error=o(t).message)}}render(){let e=u[this.metric],t=typeof this.ref.scope==`string`?this.ref.scope:`page`,r=this.data,i;if(r===`absent`)i=n`<p class="wm-note muted small">${x}</p>`;else if(this.error)i=n`<p class="wm-note err-ink small">${this.error}</p>`;else if(!r)i=n`<p class="muted small wm-empty">loading</p>`;else{let t=r.series.map(e=>{let t=this.node(e.node);return{id:e.node,alias:t?.alias??e.node,colour:t?.colour??1,values:e.values}});i=n`<qse-fleet-chart chart-id=${`win-${this.win?.id}-${this.metric}`} .series=${t} agg=${r.agg} unit=${r.unit} .t0=${r.t0} .step=${r.step_s} .decimals=${e.decimals} .cap=${r.cap} .max=${e.max??null} .events=${this.marks} mode=${r.agg===`sum`?`stacked`:`lines`} aria-label=${`${e.label} over ${r.range}`}></qse-fleet-chart>`}return n`<div class="wm-pane">
      <div class="wm-bar">
        <select class="wm-in" aria-label="Metric" @change=${e=>this.setRef({metric:e.target.value})}>
          ${l.map(e=>n`<option value=${e} ?selected=${e===this.metric}>${u[e].label} (${u[e].unit})</option>`)}
        </select>
        <select class="wm-in" aria-label="Scope" @change=${e=>this.setRef({scope:e.target.value})}>
          <option value="page" ?selected=${t===`page`}>Follow the scope bar</option>
          <option value="all" ?selected=${t===`all`}>All Sparks</option>
          ${this.nodes.map(e=>n`<option value=${`node:${e.id}`} ?selected=${t===`node:${e.id}`}>${e.alias}</option>`)}
        </select>
      </div>
      <div class="wm-chart">${i}</div>
    </div>`}};e([a()],O.prototype,`data`,void 0),e([a()],O.prototype,`marks`,void 0),e([a()],O.prototype,`error`,void 0),e([a()],O.prototype,`scope`,void 0),O=e([t(`qse-win-chart`)],O);var k=[[`tps`,`tok/s`],[`temp`,`°C`],[`power`,`W`],[`gpu`,`% GPU`]],A=class extends w{constructor(...e){super(...e),this.now={},this.memory=null,this.absent=!1,this.feed=new b(e=>this.load(e),1e4),this.asked=``}connectedCallback(){super.connectedCallback(),this.feed.start()}disconnectedCallback(){this.feed.stop(),super.disconnectedCallback()}updated(){let e=this.nodeId;e&&e!==this.asked&&(this.asked=e,this.now={},this.memory=null,this.feed.refresh())}get nodeId(){return typeof this.ref.node==`string`?this.ref.node:this.nodes[0]?.id??null}async load(e){let t=this.nodeId;if(!t)return;let n={};for(let[r]of k){let i=await C(()=>c.metrics({source:`node:${t}`,metric:r,range:`5m`}));if(i===`absent`){e()&&(this.absent=!0);return}n[r]=[...i.series[0]?.values??[]].reverse().find(e=>e!=null)??null}let r=await C(()=>c.memory());e()&&t===this.nodeId&&(this.now=n,this.memory=r===`absent`?null:r.nodes.find(e=>e.node===t)??null)}render(){let e=this.nodeId,t=this.node(e),a=n`<div class="wm-bar">
      <select class="wm-in" aria-label="Spark" @change=${e=>this.setRef({node:e.target.value})}>
        ${this.nodes.map(t=>n`<option value=${t.id} ?selected=${t.id===e}>${t.alias}</option>`)}
      </select>
      <span class="grow"></span>
      ${e?n`<a class="btn btn-ghost btn-sm" href=${`#/sparks/${encodeURIComponent(e)}`}>Open page</a>`:i}
    </div>`;if(this.absent||!this.fleetState)return n`<div class="wm-pane">${a}<p class="wm-note muted small">${this.fleetState?x:`The node inspector shows a Spark of the fleet. ${x}`}</p></div>`;if(!t)return n`<div class="wm-pane">${a}<p class="wm-note muted small">No Spark ${e??``} in this fleet.</p></div>`;let o=this.fleetState.deployments.filter(e=>e.members.includes(t.id)),s=this.memory,c=s?s.weights+s.kv+s.cache+s.other:null,l=t.lease_end?`${Math.max(0,Math.round((t.lease_end-Date.now()/1e3)/86400))} days left`:t.owned?`owned`:`none`;return n`<div class="wm-pane">
      ${a}
      <div class="wm-insp">
        <div class="wm-insp-h">${_(t,{lg:!0})}<span class="small muted">${h(t)}</span></div>
        <div class="wm-insp-figs">
          ${k.map(([e,t])=>n`<div><span class="muted small">${u[e].label}</span><b class="num">${this.now[e]==null?`—`:Math.round(this.now[e])}</b><span class="muted small">${t}</span></div>`)}
        </div>
        ${s&&c!=null?n`<div class="wm-insp-mem"><div class="small muted">Unified memory <span class="num">${c.toFixed(1)} of ${s.usable} GiB</span></div><div class="meter"><span class="meter-fill" style=${`width:${Math.min(100,c/s.usable*100)}%`}></span></div></div>`:i}
        <dl class="kv">
          <dt>serves</dt><dd>${o.length?o.map(e=>e.name).join(`, `):`nothing`}</dd>
          <dt>reach</dt><dd>${t.transport===`tunnel`?`SSH tunnel`:`LAN`} · <span class="num">${t.address}</span></dd>
          <dt>hardware</dt><dd>${t.hw}</dd>
          <dt>version</dt><dd>${t.version}</dd>
          <dt>lease</dt><dd>${l}</dd>
        </dl>
        ${t.status===`alert`?n`<p class="wm-note warn-ink small">⚠ ${t.status_text}</p>`:i}
        <div class="wm-insp-acts">
          <button type="button" class="btn btn-sm" @click=${()=>this.openWindow(`terminal`,{node:t.id})}>${r(`terminal`)} Terminal</button>
          <button type="button" class="btn btn-sm" @click=${()=>this.openWindow(`logs`,{node:t.id})}>${r(`doc`)} Logs</button>
          <button type="button" class="btn btn-ghost btn-sm" @click=${()=>this.openWindow(`chart`,{metric:`tps`,scope:`node:${t.id}`})}>${r(`performance`)} Chart</button>
        </div>
      </div>
    </div>`}};e([a()],A.prototype,`now`,void 0),e([a()],A.prototype,`memory`,void 0),e([a()],A.prototype,`absent`,void 0),A=e([t(`qse-win-inspector`)],A);export{O as QseWinChart,A as QseWinInspector,D as QseWinLogs};