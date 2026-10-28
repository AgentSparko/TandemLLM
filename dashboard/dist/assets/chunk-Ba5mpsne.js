import{c as e,d as t,f as n,g as r,l as i,p as a,u as o}from"./chunk-BlUUBWrt.js";import{i as s,n as c}from"./chunk-DPmNzEWF.js";import{c as l,r as u}from"./chunk-D_WZJPW6.js";import{n as d,o as f,r as p,t as m}from"./chunk-BkP-Mz-v.js";import{An as h,Cn as g,En as _,Nn as v,_n as y,jn as b,ln as x,on as S,xn as C}from"./app-Df17Dgo6.js";import{C as w,E as T,L as E,T as D,b as O,h as k,i as A,m as j,p as M}from"./chunk-DPgDSod7.js";import{n as N}from"./chunk-BDZrIVR2.js";var P=class extends i{constructor(...e){super(...e),this.fleetState=null,this.scope=m(),this.every=1e4,this.poller=null,this.unsub=[]}connectedCallback(){super.connectedCallback(),this.unsub.push(C(e=>{let t=!this.fleetState&&e;this.fleetState=e,t&&this.reload()}),d(e=>{this.scope=e,this.restart()})),this.restart()}disconnectedCallback(){this.unsub.forEach(e=>e()),this.unsub=[],this.poller?.stop(),this.poller=null,super.disconnectedCallback()}get resolved(){return this.fleetState?p(this.scope,this.fleetState):this.scope}get nodes(){return this.fleetState?y(this.fleetState,this.resolved):[]}restart(){this.poller?.stop(),this.poller=g(e=>this.load(e),this.every),this.scope.live?this.poller.start():this.poller.refresh()}reload(){this.poller?.refresh()}series(e){let t=this.fleetState;return(e?.series??[]).map(e=>{let n=t?.nodes.find(t=>t.id===e.node);return{id:e.node,alias:n?.alias??e.node,colour:n?.colour??1,values:e.values}})}};e([o()],P.prototype,`fleetState`,void 0),e([o()],P.prototype,`scope`,void 0);var F=e=>e.reduce((e,t)=>e+(t??0),0),I=e=>{let t=e.filter(e=>e!=null);return t.length?t.reduce((e,t)=>e+t,0)/t.length:null},L=class extends P{constructor(...e){super(...e),this.every=2e3,this.m={},this.marks=[],this.error=``}async load(e){if(!this.fleetState)return;let t=f(this.resolved);try{let[n,r,i]=await Promise.all([`tps`,`req`,`ttft`].map(e=>l.metrics({source:t,metric:e,range:`5m`}))),a=await N(n.t0).catch(()=>[]);if(!e())return;this.m={tps:n,req:r,ttft:i},this.marks=a,this.error=``}catch(t){e()&&(this.error=s(t).message)}}now(e){let t=this.m[e];return T(Object.fromEntries((t?.series??[]).map(e=>[e.node,D(e.values)])),this.fleetState?.nodes??[])}avg(e){return Object.fromEntries((this.m[e]?.series??[]).map(e=>[e.node,I(e.values)]))}render(){let e=this.fleetState;if(!e)return a;let t=this.nodes,n=this.m.tps,i=this.now(`tps`),o=this.now(`req`),s=O(E(t,i),`sum`),c=F(t.map(e=>o[e.id])),l=t.filter(e=>(i[e.id]??0)>.5).length,u=this.resolved,d=u.kind===`deployment`?e.deployments.find(e=>e.id===u.id)?.name??u.id:null;return r`<div class="fl-scope fl-live" data-fleet-live>
      <section class="panel fl-now" data-fleet-now>
        <div class="fl-now-num">
          <div class="eyebrow">Decode now · Σ ${t.length} Spark${t.length===1?``:`s`}${d?` · ${d}`:``}</div>
          <div class="fl-big num" data-fleet-total>${A(s)}<span class="u">tok/s</span></div>
          <p class="small muted fl-now-sub"><b class="num">${A(c)}</b> in flight · <b class="num">${l}</b> of ${t.length} decoding${this.error?r` · <span class="warn-ink">${this.error}</span>`:a}</p>
          <p class="xs faint">The fleet store's samples: every request each engine served, through this endpoint or not. A Split's tokens count once.</p>
        </div>
        <div class="fl-now-chart">
          ${n?r`<qse-fleet-chart chart-id="perf-now" .series=${this.series(n)} agg="sum" unit="tok/s" .t0=${n.t0} .step=${n.step_s} .events=${this.marks} mode="total" .height=${170} aria-label="Decode tok/s, last 5 minutes"></qse-fleet-chart>`:r`<div class="skeleton" style="height:170px"></div>`}
        </div>
      </section>
      <section class="panel fl-strips" data-fleet-strips>
        <header class="panel-head"><div class="panel-titles"><h2 class="panel-title">Per Spark</h2><p class="panel-sub">One strip per Spark in scope: what it serves, its decode over the last 5 minutes, its requests in flight</p></div></header>
        ${t.map(e=>this.strip(e,i[e.id]??null,o[e.id]??null))}
      </section>
      ${this.tiles(t)}
    </div>`}strip(e,t,n){let i=(this.fleetState?.deployments??[]).filter(t=>t.members.includes(e.id)),o=this.m.tps?.series.find(t=>t.node===e.id)?.values??[],s=Math.max(0,Math.round(n??0));return r`<div class="fl-strip ${e.status===`off`?`is-off`:``}" data-strip=${e.id}>
      <div class="fl-strip-who">
        ${x(e,{href:`#/sparks/${encodeURIComponent(e.id)}`})}
        <div class="fl-strip-deps xs">${i.length?i.map(e=>r`<span>${k(e.type)} ${e.name}</span>`):r`<span class="faint">serves nothing</span>`}</div>
      </div>
      <div class="fl-strip-chart">${M(o,e.colour,40,`${e.alias} decode, last 5 minutes`)}</div>
      <div class="fl-strip-lanes" aria-label=${`${s} in flight on ${e.alias}`}>
        <span class="fl-dots" aria-hidden="true">${Array.from({length:Math.min(s,12)},()=>r`<i style="--c:var(--n${(e.colour-1)%4+1})"></i>`)}${s>12?r`<b class="xs">+${s-12}</b>`:a}</span>
        <span class="xs muted num">${e.status===`off`?e.status_text:`${s} in flight`}</span>
      </div>
      <div class="fl-strip-now"><b class="num" data-strip-tps>${A(t)}</b><small>tok/s now</small></div>
    </div>`}tiles(e){let t=E(e,this.avg(`tps`)),n=E(e,this.avg(`ttft`)),i=E(e,this.avg(`req`));return r`<div class="mtiles is-3" data-fleet-tiles>
      ${j({id:`decode-5m`,label:`Decode, 5-min mean`,agg:`sum`,value:A(O(t,`sum`)),unit:`tok/s`,parts:t,sub:t.map(e=>`${e.alias} ${A(e.v)}`).join(` · `)})}
      ${j({id:`ttft-5m`,label:`TTFT p50`,agg:`mean`,value:A(O(n,`mean`)),unit:`ms`,parts:n,sub:n.map(e=>`${e.alias} ${e.v==null?`—`:v(e.v)}`).join(` · `)})}
      ${j({id:`req-5m`,label:`In flight, 5-min mean`,agg:`sum`,value:A(O(i,`sum`),1),parts:i,fmt:e=>A(e,1),sub:i.map(e=>`${e.alias} ${A(e.v,1)}`).join(` · `)})}
    </div>`}};e([o()],L.prototype,`m`,void 0),e([o()],L.prototype,`marks`,void 0),e([o()],L.prototype,`error`,void 0),L=e([n(`qse-fleet-live`)],L);var R=[{metric:`tps`,title:`Decode`,sub:`tok/s per Spark, Σ in Total`,mode:`stacked`,decimals:0},{metric:`ttft`,title:`Time to first token`,sub:`p50 ms per Spark, the mean of them in Total`,mode:`lines`,decimals:0},{metric:`req`,title:`Requests in flight`,sub:`per Spark, Σ in Total`,mode:`stacked`,decimals:1}],z=class extends P{constructor(...e){super(...e),this.every=3e4,this.m={},this.marks=[],this.error=``}async load(e){if(!this.fleetState)return;let t=this.resolved;try{let n=await Promise.all(R.map(e=>l.metrics({source:f(t),metric:e.metric,range:t.range}))),r=await N(n[0].t0).catch(()=>[]);if(!e())return;this.m=Object.fromEntries(n.map(e=>[e.metric,e])),this.marks=r,this.error=``}catch(t){e()&&(this.error=s(t).message)}}render(){if(!this.fleetState)return a;let e=this.resolved;return r`<section class="fl-scope fl-history" data-fleet-history>
      <div class="history-head"><div class="panel-titles"><h2 class="panel-title">By Spark</h2><p class="panel-sub">the fleet store over the scope's range (${e.range}); each chart in Total, Stacked, Lines, Split or Table · ◆ the fleet's events${this.error?r` · <span class="warn-ink">${this.error}</span>`:a}</p></div></div>
      <div class="grid-2">
        ${R.map(e=>{let t=this.m[e.metric];return r`<section class="panel" data-fleet-chart=${e.metric}>
            <header class="panel-head"><div class="panel-titles"><h2 class="panel-title">${e.title}</h2><p class="panel-sub">${e.sub}</p></div></header>
            ${t?r`<qse-fleet-chart chart-id=${`perf-hist-${e.metric}`} .series=${this.series(t)} agg=${u[e.metric].agg} unit=${u[e.metric].unit} .t0=${t.t0} .step=${t.step_s} .decimals=${e.decimals} .events=${this.marks} mode=${e.mode} .height=${200} aria-label=${`${e.title} per Spark over ${t.range}`}></qse-fleet-chart>`:r`<div class="skeleton" style="height:200px"></div>`}
          </section>`})}
      </div>
    </section>`}};e([o()],z.prototype,`m`,void 0),e([o()],z.prototype,`marks`,void 0),e([o()],z.prototype,`error`,void 0),z=e([n(`qse-fleet-history`)],z);var B=class extends P{constructor(...e){super(...e),this.every=6e4,this.window=null,this.rows=null,this.whole=null,this.energyFrom=null,this.error=``,this.lastKey=``}updated(e){if(e.has(`window`)&&this.window){let e=JSON.stringify(this.window);e!==this.lastKey&&(this.lastKey=e,this.rows=null,this.reload())}}async load(e){let t=this.window,n=this.fleetState;if(!t||!n)return;let r=this.resolved,i=this.nodes,a={from:t.from,to:t.to,bucket:`day`,tz:t.tz,model:t.model,client:t.client,user:t.user,internal:t.internal,ledger:`fleet`,deployment:r.kind===`deployment`?r.id:null};try{let[n,o,s]=await Promise.all([c.usage(a),Promise.all(i.map(e=>c.usage({...a,node:e.id}))),l.metrics({source:r.kind===`deployment`?f(r):`all`,metric:`power`,range:`30d`}).catch(()=>null)]);if(!e())return;let u=S(t.from,t.tz)/1e3,d=Math.min(Date.now()/1e3,S(t.to,t.tz)/1e3+86400),p=s?Math.max(u,s.t0):null;this.energyFrom=p,this.whole=n,this.rows=i.map((e,t)=>{let n=o[t],r=s?.series.find(t=>t.node===e.id),i=r&&p!=null?w(r.values,s.t0,s.step_s,p,d):{kwh:null,covered:0},a=p==null?0:F(n.buckets.filter(e=>Date.parse(e.start)/1e3+86400>p&&Date.parse(e.start)/1e3<d).map(e=>e.total_tokens));return{node:e,usage:n,kwh:i.kwh,covered:i.covered,tokensInEnergy:a,watts:i.kwh!=null&&d>p?i.kwh*36e5/((d-p)*i.covered||1):null}}),this.error=``}catch(t){e()&&(this.error=s(t).message)}}render(){let e=this.fleetState;if(!e)return a;let t=this.rows,n=r`<div class="history-head"><div class="panel-titles"><h2 class="panel-title">By Spark</h2><p class="panel-sub">TandemApp's controller ledger: the requests its gateway relayed, each with the Spark that served it; energy is each Spark's power samples added up${this.error?r` · <span class="warn-ink">${this.error}</span>`:a}</p></div></div>`;if(!t||!this.whole)return r`<section class="fl-scope fl-usage" data-usage-fleet>${n}<div class="skeleton" style="height:220px"></div></section>`;let i=F(t.map(e=>e.usage.totals.total_tokens)),o=F(t.map(e=>e.usage.totals.requests)),s=t.map(e=>({id:e.node.id,alias:e.node.alias,colour:e.node.colour,v:e.kwh})),c=t.some(e=>e.kwh!=null)?F(t.map(e=>e.kwh)):null,l=F(t.map(e=>e.kwh==null?0:e.tokensInEnergy)),u=Math.max(0,this.whole.totals.requests-o),d=this.whole.buckets.map(e=>Date.parse(e.start)/1e3),f=t.map(e=>({id:e.node.id,alias:e.node.alias,colour:e.node.colour,values:e.usage.buckets.map(e=>e.total_tokens)})),p=this.energyFrom==null?null:Math.max(0,Math.round((Date.now()/1e3-this.energyFrom)/86400)),m=this.energyFrom==null?`no power samples from the controller`:`kWh over the last ${p} day${p===1?``:`s`} of the period (the controller keeps 30 days of power)`;return r`<section class="fl-scope fl-usage" data-usage-fleet>
      ${n}
      <div class="mtiles is-4" data-usage-tiles>
        ${j({id:`u-req`,label:`Requests`,agg:`sum`,value:h(o),parts:t.map(e=>({id:e.node.id,alias:e.node.alias,colour:e.node.colour,v:e.usage.totals.requests})),fmt:e=>h(e),sub:u?`${h(u)} more on no Spark (a cloud backend)`:`every one on a Spark`})}
        ${j({id:`u-tok`,label:`Tokens`,agg:`sum`,value:_(i),parts:t.map(e=>({id:e.node.id,alias:e.node.alias,colour:e.node.colour,v:e.usage.totals.total_tokens})),fmt:e=>_(e),sub:`in and out`})}
        ${j({id:`u-kwh`,label:`Energy`,agg:`sum`,value:c==null?`—`:b(c,c<10?2:1),unit:`kWh`,parts:s,fmt:e=>b(e,2),sub:m})}
        ${j({id:`u-tpk`,label:`Tokens per kWh`,agg:null,value:c?_(l/c):`—`,sub:`Σ tokens / Σ kWh over the same days; higher is better`})}
      </div>
      <section class="panel" data-usage-stack>
        <header class="panel-head"><div class="panel-titles"><h2 class="panel-title">Tokens per day by Spark</h2><p class="panel-sub">stacked, from the controller's ledger; the mode switch shows them as lines, small multiples or a table</p></div></header>
        <qse-fleet-chart chart-id="usage-by-spark" .series=${f} agg="sum" unit="tok" .t0=${d[0]??0} .step=${86400} mode="stacked" .height=${220} aria-label="Tokens per day, stacked by Spark"></qse-fleet-chart>
      </section>
      <section class="panel" data-usage-by-node>
        <header class="panel-head"><div class="panel-titles"><h2 class="panel-title">Per Spark</h2><p class="panel-sub">${m}; tokens per kWh counts the tokens of those days only</p></div></header>
        <div class="table-scroll"><table class="table fl-node-table">
          <thead><tr><th>Spark</th><th>Serves</th><th class="num-col">Requests</th><th class="num-col">Tokens</th><th class="num-col">Share</th><th class="num-col">Decode p50</th><th class="num-col">TTFT p50</th><th class="num-col">Energy kWh</th><th class="num-col">Avg power W</th><th class="num-col">Tokens per kWh</th></tr></thead>
          <tbody>
            ${t.map(t=>r`<tr data-node=${t.node.id}>
                <td>${x(t.node,{href:`#/sparks/${encodeURIComponent(t.node.id)}`})}</td>
                <td class="cell-wrap small">${e.deployments.filter(e=>e.members.includes(t.node.id)).map(e=>e.name).join(`, `)||r`<span class="muted">nothing</span>`}</td>
                <td class="num-col num">${h(t.usage.totals.requests)}</td>
                <td class="num-col num" title=${h(t.usage.totals.total_tokens)}>${_(t.usage.totals.total_tokens)}</td>
                <td class="num-col num">${i?`${Math.round(100*t.usage.totals.total_tokens/i)} %`:`—`}</td>
                <td class="num-col num">${t.usage.totals.decode_tps_p50==null?`—`:`${b(t.usage.totals.decode_tps_p50,1)} tok/s`}</td>
                <td class="num-col num">${v(t.usage.totals.ttft_ms_p50)}</td>
                <td class="num-col num" data-kwh>${t.kwh==null?`—`:b(t.kwh,2)}${t.kwh!=null&&t.covered<.95?r` <span class="faint xs" title="the share of the days the power samples cover">${Math.round(t.covered*100)} %</span>`:a}</td>
                <td class="num-col num">${t.watts==null?`—`:A(t.watts)}</td>
                <td class="num-col num" data-tpk>${t.kwh?_(t.tokensInEnergy/t.kwh):`—`}</td>
              </tr>`)}
          </tbody>
          <tfoot><tr><td>Σ ${t.length} Spark${t.length===1?``:`s`}</td><td></td><td class="num-col num">${h(o)}</td><td class="num-col num">${_(i)}</td><td class="num-col num">100 %</td><td></td><td></td><td class="num-col num">${c==null?`—`:b(c,2)}</td><td></td><td class="num-col num">${c?_(l/c):`—`}</td></tr></tfoot>
        </table></div>
      </section>
    </section>`}};e([t({attribute:!1})],B.prototype,`window`,void 0),e([o()],B.prototype,`rows`,void 0),e([o()],B.prototype,`whole`,void 0),e([o()],B.prototype,`energyFrom`,void 0),e([o()],B.prototype,`error`,void 0),B=e([n(`qse-usage-fleet`)],B);export{z as QseFleetHistory,L as QseFleetLive,B as QseUsageFleet};