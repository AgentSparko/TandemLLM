import{c as e,d as t,f as n,g as r,i,l as a,m as o,n as s,o as c,p as l,r as u,u as d,v as f}from"./chunk-BlUUBWrt.js";import{t as p}from"./chunk-Blyyi2VJ.js";import{i as m,t as h}from"./chunk-DPmNzEWF.js";import{a as g,n as _}from"./chunk-C139jKKa.js";import{$t as ee,Cn as v,Dn as te,G as ne,H as y,K as b,P as re,U as ie,W as x,bn as S,ln as ae,wn as oe}from"./app-oKdPFUIA.js";import{n as se,r as ce,t as le}from"./chunk-Bw_CIVqS.js";import{C as ue,D as de,E as fe,M as pe,N as me,P as C,R as he,S as ge,T as _e,a as ve,b as ye,c as be,d as xe,f as Se,g as Ce,h as we,i as w,j as T,l as Te,m as Ee,n as E,p as De,r as D,s as O,t as Oe,u as ke,v as Ae,w as je,y as Me}from"./chunk-BXw0wEbV.js";import{a as Ne,c as Pe,d as Fe,f as Ie,h as k,i as Le,l as Re,m as ze,n as Be,o as Ve,p as He,r as Ue,s as We,t as Ge,u as Ke}from"./chunk-CGajD5w9.js";import{n as qe,t as Je}from"./chunk-BPJ9KAj1.js";import{a as Ye,c as Xe,d as Ze,f as Qe,i as $e,l as et,n as tt,o as nt,p as rt,r as A,s as it,t as at,u as ot}from"./chunk-yzU3VTTW.js";var j=class extends a{constructor(...e){super(...e),this.alias=``,this.plan=null,this.answer=null}get dialog(){return this.querySelector(`dialog`)}ask(e){return this.answer?.(!1),this.plan=e,this.updateComplete.then(()=>{this.dialog&&!this.dialog.open&&this.dialog.showModal()}),new Promise(e=>this.answer=e)}done(e){let t=this.answer;this.answer=null,this.dialog?.open&&this.dialog.close(),t?.(e)}list(e,t,n){return t.length?r`<section class="act-sec ${n}"><h3 class="eyebrow">${e}</h3><ul>${t.map(e=>r`<li>${e}</li>`)}</ul></section>`:l}render(){let e=this.plan;return r`<dialog class="act-dialog ${e?.allowed?`is-danger`:``}" aria-labelledby="sc-title" data-setup-confirm=${e?.route??l} @close=${()=>this.done(!1)} @cancel=${()=>this.done(!1)}>
      ${e?r`<form method="dialog" class="act-body" @submit=${t=>(t.preventDefault(),e.allowed&&this.done(!0))}>
            <header class="act-head"><h2 id="sc-title">${e.title}</h2><span class="small muted">through TandemApp’s controller</span></header>
            ${e.blocked?r`<p class="act-blocked warn-ink" data-act-blocked><span aria-hidden="true">⚠</span> ${e.blocked}</p>`:l}
            ${this.list(`What happens`,e.impact,`is-impact`)}
            ${this.list(`Stays`,e.keeps,`is-keeps`)}
            <footer class="act-foot">
              <button type="button" class="btn btn-ghost" data-act-cancel @click=${()=>this.done(!1)}>${e.allowed?`Cancel`:`Close`}</button>
              ${e.allowed?r`<button type="submit" class="btn btn-danger-solid" data-act-go>Go ahead on ${this.alias}</button>`:l}
            </footer>
          </form>`:l}
    </dialog>`}};e([t()],j.prototype,`alias`,void 0),e([d()],j.prototype,`plan`,void 0),j=e([n(`qse-setup-confirm`)],j);var M=[`1`,`2`,`4`,`8`],N=[`2048`,`8192`,`32768`];function st(e){let t=e.parallel?.rows??[],n=t=>{let n=e.profiles.findIndex(e=>e.id===t.profile);return n<0?e.profiles.length:n};return[...t].sort((e,t)=>n(e)-n(t)||e.profile.localeCompare(t.profile))}function ct(e,t){return e?.parallel?.rows.find(e=>e.profile===t)??null}function lt(e,t){let n=e.profiles.find(e=>e.id===t.profile);return n?n.label:(e.community??[]).find(e=>e.profile===t.profile||`community/${e.id}`===t.profile)?.name??t.profile}function P(e,t){let n=e.levels[t];return n&&n.at_once===Number(t)?n.wall_tok_s:null}function ut(e){let t=e.setup,n=t.sampling;return y.parallelSetup(t.lanes,t.window,t.max_tokens,`T ${n.temperature}, top-k ${n.top_k}, top-p ${n.top_p}`,t.suffix_store,t.response_cache,e.rounds,t.prompts.set)}var F=e=>e==null?`–`:e.toFixed(1),dt=e=>e==null?`–`:e.toFixed(2),I=class extends a{constructor(...e){super(...e),this.model=null,this.selected=null}render(){let e=this.model,t=e?st(e):[];return!e||!t.length?l:r`<div class="mt pt">
      <h3 class="pt-title">${y.parallelTitle}</h3>
      <div class="table-scroll mt-scroll">
        <table class="table mt-table pt-table">
          <thead>
            <tr>
              <th scope="col" class="mt-name" rowspan="2">${y.colWeights}</th>
              <th scope="colgroup" colspan="2">${y.parallelDecode}<span class="mt-unit">${y.tokPerS}</span></th>
              <th scope="colgroup" colspan=${N.length}>${y.parallelPrefill}<span class="mt-unit">${y.seconds}</span></th>
              <th scope="colgroup" colspan=${M.length}>${y.parallelAtOnce}<span class="mt-unit">${y.tokPerS}</span></th>
            </tr>
            <tr>
              <th scope="col" class="num-col">${y.colCode}</th>
              <th scope="col" class="num-col">${y.colProse}</th>
              ${N.map(e=>r`<th scope="col" class="num-col">${Number(e)/1024}k</th>`)}
              ${M.map(e=>r`<th scope="col" class="num-col">${e}</th>`)}
            </tr>
          </thead>
          <tbody>${t.map(t=>this.row(e,t))}</tbody>
        </table>
      </div>
      <div class="mt-foot muted small">${y.parallelFoot}
        <details class="mt-method"><summary>${y.howMeasured}</summary><p>${e.parallel?.method??``}</p>
          <ul class="pt-setups">${t.map(t=>r`<li data-setup=${t.profile}><strong>${lt(e,t)}</strong>: ${y.parallelBuild(t.build,t.commit,t.date)} ${ut(t)}${t.note?r` ${t.note}`:l}</li>`)}</ul>
        </details>
      </div>
    </div>`}row(e,t){let n=t.profile===this.selected;return r`<tr class="mt-row ${n?`is-selected`:``}" data-parallel=${t.profile} aria-current=${n?`true`:l}>
      <th scope="row" class="mt-name"><span class="mt-label">${lt(e,t)}</span>${n?r`<span class="mt-mine">${y.selectedRow}</span>`:l}</th>
      <td class="num-col num">${F(t.decode?.code)}</td>
      <td class="num-col num">${F(t.decode?.prose)}</td>
      ${N.map(e=>{let n=t.prefill?.[e];return n?r`<td class="num-col num" title=${y.parallelPrefillTitle(n.prompt_tokens,n.tok_s)}>${dt(n.ttft_s)}</td>`:r`<td class="num-col mt-na">–</td>`})}
      ${M.map(e=>{let n=t.levels[e],i=P(t,e);return i==null||!n?r`<td class="num-col mt-na">–</td>`:r`<td class="num-col num" title=${y.parallelLevelTitle(Number(e),n.decode_tok_s,n.ttft_s)}>${F(i)}</td>`})}
    </tr>`}};e([t({attribute:!1})],I.prototype,`model`,void 0),e([t({attribute:!1})],I.prototype,`selected`,void 0),I=e([n(`qse-parallel-table`)],I);function L(e,t){let n=ct(e,t);if(!n)return null;let r=P(n,`1`),i=[`8`,`4`,`2`].find(e=>P(n,e)!=null);return i?y.parallelCard(r,Number(i),P(n,i)):r==null?null:y.parallelCard(r,1,r)}var R=y.community,ft={text:f`<path d="M4 5.5h12M4 9.5h12M4 13.5h7" fill="none" stroke="currentColor" stroke-width="1.7" stroke-linecap="round"/>`,mic:f`<rect x="7.25" y="3" width="5.5" height="9" rx="2.75" fill="none" stroke="currentColor" stroke-width="1.6"/><path d="M4.5 10a5.5 5.5 0 0 0 11 0M10 15.5v2M7.5 17.5h5" fill="none" stroke="currentColor" stroke-width="1.6" stroke-linecap="round"/>`,video:f`<rect x="2.5" y="5" width="10.5" height="10" rx="1.5" fill="none" stroke="currentColor" stroke-width="1.6"/><path d="m13 9 4.5-2.5v7L13 11z" fill="none" stroke="currentColor" stroke-width="1.6" stroke-linejoin="round"/>`,drafters:f`<circle cx="4.5" cy="10" r="1.8" fill="currentColor"/><circle cx="10" cy="10" r="1.8" fill="currentColor" opacity=".7"/><circle cx="15.5" cy="10" r="1.8" fill="currentColor" opacity=".45"/><path d="M6.3 10h1.9M11.8 10h1.9" stroke="currentColor" stroke-width="1.4"/>`,x:f`<path d="M4 3.5h3.6l8.4 13H12.4z" fill="none" stroke="currentColor" stroke-width="1.6" stroke-linejoin="round"/><path d="M15.5 3.5 4.5 16.5" stroke="currentColor" stroke-width="1.6" stroke-linecap="round"/>`,huggingface:f`<circle cx="10" cy="10.5" r="6.5" fill="none" stroke="currentColor" stroke-width="1.6"/><circle cx="7.6" cy="9" r="1" fill="currentColor"/><circle cx="12.4" cy="9" r="1" fill="currentColor"/><path d="M7 12.2c.8 1.2 1.8 1.8 3 1.8s2.2-.6 3-1.8" fill="none" stroke="currentColor" stroke-width="1.6" stroke-linecap="round"/><path d="M4.2 7.2 3 6.2M15.8 7.2 17 6.2" stroke="currentColor" stroke-width="1.6" stroke-linecap="round"/>`,bluesky:f`<path d="M10 8.6C9 6.8 6.8 4.2 4.6 3.7 3.3 3.5 2.8 4.3 2.8 5.4c0 1.6.6 4.3 1.9 5 .9.4 2 .2 2.7 0-2 .5-2.9 1.4-2.1 2.9.9 1.8 3.2 2.5 4.7-.4 1.5 2.9 3.8 2.2 4.7.4.8-1.5-.1-2.4-2.1-2.9.7.2 1.8.4 2.7 0 1.3-.7 1.9-3.4 1.9-5 0-1.1-.5-1.9-1.8-1.7C13.2 4.2 11 6.8 10 8.6Z" fill="currentColor"/>`,mastodon:f`<path d="M4 15.5c-.9-1-1.5-2.6-1.5-5V8.3C2.5 5 4.6 3.5 10 3.5s7.5 1.5 7.5 4.8v1.5c0 2.6-1.4 3.9-4.2 4.1-1.9.2-3.5 0-4.3-.3v.9c0 1.4 2.3 1.5 5 1" fill="none" stroke="currentColor" stroke-width="1.6" stroke-linecap="round" stroke-linejoin="round"/><path d="M6.3 11.8V8.4c0-1.1.6-1.7 1.6-1.7s1.6.6 1.6 1.7v1.9M13.7 11.8V8.4c0-1.1-.6-1.7-1.6-1.7s-1.6.6-1.6 1.7v1.9" fill="none" stroke="currentColor" stroke-width="1.6" stroke-linecap="round"/>`,website:f`<circle cx="10" cy="10" r="7" fill="none" stroke="currentColor" stroke-width="1.6"/><path d="M3 10h14M10 3c2.2 2.1 3.3 4.4 3.3 7s-1.1 4.9-3.3 7c-2.2-2.1-3.3-4.4-3.3-7S7.8 5.1 10 3Z" fill="none" stroke="currentColor" stroke-width="1.4"/>`},z=(e,t=16)=>r`<svg class="icon" viewBox="0 0 20 20" aria-hidden="true" width=${t} height=${t}>${ft[e]}</svg>`,pt=e=>e===`text`?z(`text`):e===`image`?i(`image`):z(e===`audio`?`mic`:`video`),mt=e=>e.toUpperCase(),B=class extends a{constructor(...e){super(...e),this.model=null,this.sync=null,this.selected=null,this.device=null,this.syncing=!1,this.onUse=()=>{},this.onRefresh=()=>{},this.query=``,this.sort=`newest`,this.droppedOpen=!1,this.noAvatar=new Set}render(){let e=this.model;if(!e)return l;let t=De(e),n=this.sync;if(!t.length&&!n)return l;let a=ue(ge(t,this.query),this.sort),o=Te,s=Ce(n?.add_url)??o;return r`<section class="cw" aria-labelledby="cw-h" data-community data-syncing=${this.syncing?``:l}>
      <header class="cw-head">
        <div class="cw-title">
          <h2 class="cw-h" id="cw-h">${R.heading}</h2>
          ${this.renderSource()}
        </div>
        <p class="cw-intro">${R.intro} <a href=${o} target="_blank" rel="noopener noreferrer">${R.addYoursHow} ${i(`external`)}</a></p>
        ${t.length?r`<div class="cw-tools">
              <label class="cw-search">
                ${i(`search`)}
                <input type="search" placeholder=${R.search} aria-label=${R.searchLabel} .value=${this.query} autocomplete="off" autocapitalize="off" spellcheck="false" @input=${e=>this.query=e.target.value} />
              </label>
              <label class="cw-sort"><span class="muted small">Sort</span>
                <select class="sel" aria-label=${R.sortLabel} @change=${e=>this.setSort(e.target.value)}>${ke.map(e=>r`<option value=${e} ?selected=${this.sort===e}>${xe[e]}</option>`)}</select>
              </label>
              <span class="cw-count num muted small" data-count>${we(a.length,t.length)}</span>
              <a class="btn btn-sm cw-add" href=${s} target="_blank" rel="noopener noreferrer">${i(`plus`)} ${R.addYours}</a>
            </div>`:l}
      </header>
      ${n?.dropped.length?this.renderDropped(n.dropped):l}
      ${t.length?a.length?r`<div class="cw-grid" role="list">${a.map(e=>this.renderCard(e))}</div>`:r`<div class="cw-empty" data-community-nomatch role="status"><p class="cw-empty-t">${R.noMatch(this.query.trim())}</p><p><button class="btn btn-sm" @click=${()=>this.query=``}>${i(`x`)} ${R.clearSearch}</button></p></div>`:r`<div class="cw-empty" data-community-empty>
            <p class="cw-empty-t">${R.empty}</p>
            <p class="muted">${R.emptyBody}</p>
            <p class="cw-empty-acts"><a class="btn btn-primary btn-sm" href=${s} target="_blank" rel="noopener noreferrer">${i(`plus`)} ${R.addYours}</a> <a class="btn btn-sm" href=${o} target="_blank" rel="noopener noreferrer">${R.addYoursHow} ${i(`external`)}</a></p>
          </div>`}
    </section>`}setSort(e){Ae(e)&&(this.sort=e)}renderSource(){let e=this.sync,t=e?.source??`bundled`,n=e=>e&&Date.now()-Date.parse(e)<1e4?`just now`:oe(e),a=this.syncing?R.syncing:t===`github`?R.fromGithub(n(e?.fetched_at)):t===`cache`?R.fromCache(n(e?.fetched_at)):R.bundled;return r`<div class="cw-src" data-source=${this.syncing?`syncing`:t}>
      <span class="cw-src-dot" aria-hidden="true"></span>
      <span class="cw-src-text small" role="status">${a}</span>
      <button class="btn btn-ghost btn-sm cw-refresh" ?disabled=${this.syncing} aria-busy=${this.syncing} @click=${()=>this.onRefresh()}>${i(`refresh`)} ${R.refresh}</button>
      ${e?.error&&!this.syncing?r`<span class="cw-src-err small" role="alert">${i(`warn`)} ${R.syncFailed(e.error.replace(/\.?$/,`.`))}</span>`:l}
      ${e?.note&&!e.error&&!this.syncing?r`<span class="cw-src-note small muted">${e.note}</span>`:l}
    </div>`}renderDropped(e){return r`<div class="cw-dropped small" data-dropped>
      <p>${i(`info`)} <span>${R.dropped(e.length)}</span> <button class="linkish" aria-expanded=${this.droppedOpen} @click=${()=>this.droppedOpen=!this.droppedOpen}>${this.droppedOpen?R.droppedHide:R.droppedDetails}</button></p>
      ${this.droppedOpen?r`<ul>${e.map(e=>r`<li><code>${e.path}</code> <span class="muted">${e.reason}</span></li>`)}</ul>`:l}
    </div>`}renderCard(e){let t=this.selected===e.id,n=Se(e.profile,this.device),a=e.status===`broken`,o=e.author,s=o.avatar&&!this.noAvatar.has(o.login);return r`<article class="ccard ${t?`is-selected`:``} ${a?`is-broken`:``}" role="listitem" data-community-card=${e.slug} data-field=${t?`weights.profile`:l} aria-labelledby=${`cc-${e.slug}`}>
      <header class="ccard-maker">
        <span class="ccard-avatar" aria-hidden="true">
          ${s?r`<img src=${o.avatar} alt="" width="40" height="40" loading="lazy" decoding="async" referrerpolicy="no-referrer" @error=${()=>this.avatarFailed(o.login)} />`:r`<span class="ccard-initials">${o.initials}</span>`}
        </span>
        <span class="ccard-who">
          <span class="ccard-handles">
            ${o.url?r`<a class="ccard-login" href=${o.url} target="_blank" rel="noopener noreferrer" title=${R.github(o.login)}>@${o.login}</a>`:r`<span class="ccard-login">@${o.login}</span>`}
            ${o.social.length?r`<span class="ccard-social">${o.social.map(e=>r`<a class="ccard-net tip-host" href=${e.href} target="_blank" rel="noopener noreferrer" aria-label=${e.label} data-tip=${e.label} data-net=${e.kind}>${z(e.kind)}</a>`)}</span>`:l}
          </span>
          ${o.name&&o.name.toLowerCase()!==o.login.toLowerCase()?r`<span class="ccard-author">${o.name}</span>`:l}
        </span>
        <span class="ccard-badges">
          ${n===`running`?r`<span class="sbadge sbadge-now">${y.activeNow}</span>`:l}
          ${this.statusBadge(e)}
        </span>
      </header>
      <h3 class="ccard-name" id=${`cc-${e.slug}`}>${e.name}</h3>
      <p class="ccard-base">${e.finetuneOf?r`${R.finetuneOf} ${e.finetuneUrl?r`<a href=${e.finetuneUrl} target="_blank" rel="noopener noreferrer">${e.finetuneOf}</a>`:r`<span>${e.finetuneOf}</span>`}`:R.stock}</p>
      ${e.description?r`<p class="ccard-desc">${e.description}</p>`:l}
      <dl class="ccard-readout num">
        <div><dt>format</dt><dd>${mt(e.format)}</dd></div>
        <div><dt>${R.bpw}</dt><dd>${e.bpw==null?`–`:e.bpw.toFixed(2)}</dd></div>
        <div><dt>${R.download}</dt><dd>${n===`download`?`${e.sizeGb.toFixed(1)} GB`:y.onDisk}</dd></div>
      </dl>
      <ul class="ccard-modes" aria-label="Modalities">
        ${e.modalities.map(e=>r`<li class="ccard-mode tip-host ${e.runs?`is-on`:`is-off`}" tabindex="0" data-tip=${e.why} aria-label=${e.why} data-modality=${e.id}>${pt(e.id)}<span>${e.label}</span></li>`)}
      </ul>
      <p class="ccard-drafters">${z(`drafters`)} <span>${e.drafters}</span></p>
      ${e.verified&&e.verified.speeds.length?r`<div class="ccard-speed" data-speed>
            ${e.verified.speeds.map(e=>r`<span class="ccard-sp"><span class="ccard-sp-n num">${e.tok_s.toFixed(1)}</span><span class="ccard-sp-k">${e.task}</span></span>`)}
            <span class="ccard-sp-u"><span class="num">tok/s</span><span class="ccard-sp-note">${R.speedNote(e.verified.device,e.verified.build,te(e.verified.date))}</span></span>
          </div>`:r`<p class="ccard-speed-none muted small">${R.notMeasured}</p>`}
      ${L(this.model,e.id)?r`<p class="ccard-par muted small num" data-parallel-line>${y.weightsAtOnce}: ${L(this.model,e.id)}</p>`:l}
      <footer class="ccard-foot">
        <span class="ccard-links">
          ${e.licenseUrl?r`<a class="sbadge ccard-license" href=${e.licenseUrl} target="_blank" rel="noopener noreferrer" title=${`${R.license}: ${e.license}`}>${e.license}</a>`:r`<span class="sbadge ccard-license" title=${`${R.license}: ${e.license}`}>${e.license}</span>`}
          ${e.hfUrl?r`<a class="ccard-hf" href=${e.hfUrl} target="_blank" rel="noopener noreferrer" title=${R.onHf(e.hfRepo)} data-hf-repo>${z(`huggingface`)} <span class="ccard-repo">${e.hfRepo}</span> ${i(`external`)}</a>`:r`<span class="ccard-hf" data-hf-repo>${z(`huggingface`)} <span class="ccard-repo">${e.hfRepo}</span></span>`}
        </span>
        <button class="btn ${t?``:`btn-primary`} ccard-use" ?disabled=${a} aria-pressed=${t} title=${a?R.brokenWhy:l} @click=${()=>!t&&this.onUse(e.id)}>${t?r`${i(`check`)} ${R.selected}`:R.use}</button>
      </footer>
    </article>`}statusBadge(e){return e.status===`verified`&&e.verified?r`<span class="sbadge sbadge-ok tip-host" tabindex="0" data-tip=${R.verifiedWhy(e.verified.device,e.verified.build,te(e.verified.date))}>${i(`check`)} ${R.verified}</span>`:e.status===`broken`?r`<span class="sbadge sbadge-err tip-host" tabindex="0" data-tip=${R.brokenWhy}>${i(`warn`)} ${R.broken}</span>`:r`<span class="sbadge tip-host" tabindex="0" data-tip=${R.submittedWhy}>${R.submitted}</span>`}avatarFailed(e){let t=new Set(this.noAvatar);t.add(e),this.noAvatar=t}};e([t({attribute:!1})],B.prototype,`model`,void 0),e([t({attribute:!1})],B.prototype,`sync`,void 0),e([t({attribute:!1})],B.prototype,`selected`,void 0),e([t({attribute:!1})],B.prototype,`device`,void 0),e([t({type:Boolean})],B.prototype,`syncing`,void 0),e([t({attribute:!1})],B.prototype,`onUse`,void 0),e([t({attribute:!1})],B.prototype,`onRefresh`,void 0),e([d()],B.prototype,`query`,void 0),e([d()],B.prototype,`sort`,void 0),e([d()],B.prototype,`droppedOpen`,void 0),e([d()],B.prototype,`noAvatar`,void 0),B=e([n(`qse-community`)],B);function ht(e){return{title:y.removeTitle(e.label,e.gb),body:e.shared_with.length?y.removeShared(e.shared_with):y.removeBody(e.model_name),again:`bash ops/use-profile.sh ${e.profile} --fetch-only`}}function gt(e){if(!e)return[];let t=new Set,n=[];for(let r of[...e.weights].sort((e,t)=>Number(t.active)-Number(e.active)||t.gb-e.gb))t.has(r.removes)||(t.add(r.removes),n.push(r));return n}var V=class extends a{constructor(...e){super(...e),this.data=null,this.error=null,this.asking=null,this.busy=!1,this.done=null}connectedCallback(){super.connectedCallback(),this.load()}async load(){try{this.data=await C.weights(),this.error=null}catch(e){this.error=m(e).message}}async removeRow(e){this.busy=!0;try{let t=await C.remove([e.profile]);this.done=y.removed(e.label,t.freed_gb,t.log??`run/weights-removed.txt`),this.asking=null,this.dispatchEvent(new CustomEvent(`weights-removed`,{detail:t,bubbles:!0})),await this.load()}catch(e){T(e)||(this.error=m(e).message)}finally{this.busy=!1}}render(){let e=gt(this.data);return!this.data&&!this.error?l:r`<section class="iw" aria-labelledby="iw-h" data-installed-weights>
      <header class="iw-head">
        <h2 class="iw-title" id="iw-h">${y.installedTitle}</h2>
        ${this.data?.free_gb==null?l:r`<span class="muted small num">${y.diskFree(this.data.free_gb)}</span>`}
      </header>
      ${this.error?r`<p class="rv-warn" role="alert">${i(`warn`)} ${this.error}</p>`:l}
      ${this.done?r`<p class="iw-done" role="status" data-removed>${i(`check`)} ${this.done}</p>`:l}
      ${e.length===0&&this.data?r`<p class="muted small">${y.installedNone}</p>`:l}
      <ul class="iw-list">
        ${e.map(e=>{let t=ht(e);return r`<li class="iw-row ${e.active?`is-active`:``}" data-installed=${e.profile}>
            <div class="iw-main">
              <span class="iw-name">${e.label}</span>
              <span class="muted small">${e.model_name}${e.shared_with.length?` · ${y.sharedWith(e.shared_with)}`:``}</span>
            </div>
            <span class="iw-gb num">${e.gb.toFixed(1)} GB</span>
            ${e.active?r`<span class="sbadge sbadge-now" title=${y.runningNoRemove}>${y.activeNow}</span>`:r`<button class="btn btn-sm btn-ghost" data-remove=${e.profile} ?disabled=${!e.removable||this.busy} @click=${()=>this.asking=e.profile}>${i(`trash`)} ${y.removeGo}</button>`}
            ${this.asking===e.profile?r`<div class="iw-ask" role="alertdialog" aria-label=${t.title} data-remove-confirm>
                  <p class="iw-ask-t">${t.title}</p>
                  <p class="small">${t.body}</p>
                  <p class="small muted">${y.removeAgain} <code>${t.again}</code></p>
                  <div class="inst-actions">
                    <button class="btn btn-sm btn-danger" ?disabled=${this.busy} data-remove-yes @click=${()=>void this.removeRow(e)}>${this.busy?y.removing:y.removeYes}</button>
                    <button class="btn btn-sm" ?disabled=${this.busy} @click=${()=>this.asking=null}>${y.cancel}</button>
                  </div>
                </div>`:l}
          </li>`})}
      </ul>
    </section>`}};e([d()],V.prototype,`data`,void 0),e([d()],V.prototype,`error`,void 0),e([d()],V.prototype,`asking`,void 0),e([d()],V.prototype,`busy`,void 0),e([d()],V.prototype,`done`,void 0),V=e([n(`qse-installed-weights`)],V);function _t(e){return e.group===`long`?e.mode===`greedy`?`long_greedy`:`long_sampled`:`${e.mode}_${e.len}`}function vt(e){return e===`everyday`?[`profile`,`prose`,`chat`,`code`,`ttft8`,`kl`,`lossless`]:[`profile`,`long32k`,`long64k`,`ttft32`,`kl`]}var yt={profile:y.colWeights,prose:y.colProse,chat:y.colChat,code:y.colCode,long32k:y.colLong32,long64k:y.colLong64,ttft8:y.colTtft8,ttft32:y.colTtft32,kl:y.colKl,lossless:y.colLossless};function H(e,t,n,r){let i=t.cells[_t(r)]??{};switch(n){case`profile`:{let n=e.profiles.find(e=>e.id===t.profile);return n?.bpw??n?.weights_gib??null}case`ttft8`:return t.ttft_s[8192]??null;case`ttft32`:return t.ttft_s[32768]??null;case`kl`:return e.profiles.find(e=>e.id===t.profile)?.quality.kl_vs_bf16??null;case`lossless`:return t.lossless?t.lossless.identical:null;default:return i[n]?.tok_s??null}}function bt(e,t){let n=e.measurements.rows,r=n.filter(e=>e.comparable),i=n.filter(e=>!e.comparable),a=n=>H(e,n,t.sort,t);return r.sort((e,n)=>{let r=a(e),i=a(n);return r==null&&i==null?0:r==null?1:i==null?-1:t.dir===`asc`?r-i:i-r}),{main:r,apart:i}}var U=[`prose`,`chat`,`code`,`long32k`,`long64k`],W=class extends a{constructor(...e){super(...e),this.model=null,this.selected=null,this.view={mode:`greedy`,len:`512`,group:`everyday`,sort:`code`,dir:`desc`}}set(e){let t={...this.view,...e};e.group&&!vt(e.group).includes(t.sort)&&(t.sort=e.group===`long`?`long32k`:`code`),this.view=t}sortBy(e){this.view.sort===e?this.set({dir:this.view.dir===`asc`?`desc`:`asc`}):this.set({sort:e,dir:U.includes(e)||e===`lossless`?`desc`:`asc`})}render(){let e=this.model;if(!e)return l;let t=this.view,n=vt(t.group),{main:i,apart:a}=bt(e,t),o={};for(let n of U)o[n]=Math.max(0,...i.map(r=>H(e,r,n,t)??0));let s=(e,t,n,i)=>r`<div class="seg" role="radiogroup" aria-label=${i}>${t.map(([t,i])=>r`<button class="seg-btn ${e===t?`is-on`:``}" role="radio" aria-checked=${e===t} data-val=${t} @click=${()=>n(t)}>${i}</button>`)}</div>`;return r`<div class="mt">
      <div class="mt-controls">
        ${s(t.mode,[[`greedy`,y.modeGreedy],[`sampled`,y.modeSampled]],e=>this.set({mode:e}),`Decoding`)}
        ${t.group===`everyday`?s(t.len,[[`512`,y.tokens512],[`128`,y.tokens128]],e=>this.set({len:e}),`Answer length`):l}
        ${s(t.group,[[`everyday`,y.groupEveryday],[`long`,y.groupLong]],e=>this.set({group:e}),`Tasks`)}
      </div>
      <div class="table-scroll mt-scroll">
        <table class="table mt-table" data-mode=${_t(t)}>
          <thead>
            <tr>
              ${n.map(e=>{let n=t.sort===e,i=U.includes(e)?y.tokPerS:e===`ttft8`||e===`ttft32`?y.seconds:``;return r`<th scope="col" class=${e===`profile`?`mt-name`:`num-col`} aria-sort=${n?t.dir===`asc`?`ascending`:`descending`:`none`}>
                  <button class="mt-sort ${n?`is-on`:``}" data-col=${e} @click=${()=>this.sortBy(e)} aria-label=${y.sortBy(yt[e])}>
                    <span>${yt[e]}</span>${i?r`<span class="mt-unit">${i}</span>`:l}<span class="mt-arrow" aria-hidden="true">${n?t.dir===`asc`?`▴`:`▾`:``}</span>
                  </button>
                </th>`})}
            </tr>
          </thead>
          <tbody>
            ${i.map(r=>this.row(e,r,n,t,o))}
            ${a.length?r`<tr class="mt-apart-head"><td colspan=${n.length}>${y.notComparable}</td></tr>
                  ${a.map(r=>this.row(e,r,n,t,o,!0))}`:l}
          </tbody>
        </table>
      </div>
      <div class="mt-foot muted small">${y.measuredOn(e.measurements.build,e.measurements.date)} <details class="mt-method"><summary>${y.howMeasured}</summary><p>${e.measurements.method}</p></details></div>
    </div>`}row(e,t,n,i,a,o=!1){let s=e.profiles.find(e=>e.id===t.profile),c=t.profile===this.selected;return r`<tr class="mt-row ${c?`is-selected`:``} ${o?`is-apart`:``}" data-profile=${t.profile} aria-current=${c?`true`:l}>
      ${n.map(n=>{if(n===`profile`)return r`<th scope="row" class="mt-name">
            <span class="mt-label">${s?.label??t.profile}</span>
            ${c?r`<span class="mt-mine">${y.selectedRow}</span>`:l}
            ${o?r`<span class="mt-note" title=${t.note??``}>${y.notComparable}</span>`:l}
          </th>`;let u=H(e,t,n,i);if(u==null)return r`<td class="num-col mt-na">–</td>`;if(n===`kl`)return r`<td class="num-col num">${u.toFixed(4)}</td>`;if(n===`lossless`){let e=t.lossless;return r`<td class="num-col num" title=${y.losslessTitle(e.identical,e.near_tie)}>${e.identical} + ${e.near_tie}</td>`}if(n===`ttft8`||n===`ttft32`)return r`<td class="num-col num">${u.toFixed(1)}</td>`;let d=a[n]?u/a[n]*100:0;return r`<td class="num-col num mt-speed"><span class="mt-bar" style="--w:${d.toFixed(1)}" aria-hidden="true"></span><span class="mt-val">${u.toFixed(1)}</span></td>`})}
    </tr>`}};e([t({attribute:!1})],W.prototype,`model`,void 0),e([t({attribute:!1})],W.prototype,`selected`,void 0),e([d()],W.prototype,`view`,void 0),W=e([n(`qse-measure-table`)],W);var xt=[`model`,`helpers`,`caches`,`engine`,`system`,`chats`],G=class extends a{constructor(...e){super(...e),this.model=null,this.estimate=null,this.available=null,this.local=!1,this.overText=null,this.open=!1,this.focusPart=null}render(){let e=this.estimate,t=this.model;if(!e||!t)return r`<section class="membar is-empty" aria-label=${y.memTitle}><div class="membar-line"><span class="membar-title">${y.memTitle}</span></div><div class="membar-track"></div></section>`;let n=e.budget_gb??e.device_gb-e.floor_gb,i=ze(e),a=e.parts.find(e=>e.kind===`kv`),o=Math.max(e.device_gb,e.total_gb),s=e=>`${(e/o*100).toFixed(3)}%`,c=e=>xt.indexOf(Ie(t,e)),u=[...e.parts].sort((e,t)=>c(e.id)-c(t.id)),d=0,f=u.map(e=>{let n={p:e,left:d,cat:Ie(t,e.id)};return d+=e.gb,n}),p=this.available==null?null:Math.max(0,e.device_gb-this.available),m=new Map;for(let e of f)m.set(e.cat,(m.get(e.cat)??0)+e.p.gb);let h=f.find(e=>e.p.id===this.focusPart),g=h?`${h.p.label}: ${h.p.gb.toFixed(2)} GiB${h.p.worst_gb!=null&&h.p.worst_gb!==h.p.gb?` (${y.memoryUpTo(h.p.worst_gb)})`:``}`:null,_=`${y.memTitle}: ${y.memFits(e.total_gb,n)}. ${i>0?this.overText??``:y.memLeft(n-e.total_gb)}`;return r`<section class="membar ${i>0?`is-over`:``} ${this.open?`is-open`:``}" aria-label=${y.memTitle} data-fits=${e.fits} data-total=${e.total_gb.toFixed(2)}>
      <div class="membar-line">
        <span class="membar-title">${y.memTitle}</span>
        <span class="membar-fig num" data-membar-total><b>${e.total_gb.toFixed(1)}</b><span class="membar-of"> / ${n.toFixed(1)} GiB</span></span>
        <span class="membar-say ${i>0?`is-over`:``}" data-membar-say role=${i>0?`alert`:l}>${i>0?this.overText??y.overSentence(i,null):y.memLeft(n-e.total_gb)}</span>
        <span class="membar-now num">${p==null?l:r`<i class="membar-now-dot" aria-hidden="true"></i>${y.memNow(p)}`}</span>
        <span class="membar-device num">${y.memDevice(e.device_gb)}</span>
        <button class="btn btn-ghost btn-sm membar-toggle" aria-expanded=${this.open} aria-controls="membar-more" aria-label=${this.open?y.memCollapse:y.memExpand} @click=${()=>this.open=!this.open}>
          <svg class="icon" viewBox="0 0 20 20" aria-hidden="true"><path d="m5 8 5 5 5-5" fill="none" stroke="currentColor" stroke-width="1.8" stroke-linecap="round" stroke-linejoin="round" /></svg>
        </button>
      </div>
      <div class="membar-track" role="img" aria-label=${_}>
        <span class="membar-free" style="left:${s(n)};width:${s(Math.max(0,e.device_gb-n))}" title=${`${y.memFloor}: ${e.floor_gb} GiB`}><span class="membar-floor-label">${y.memFloor} ${e.floor_gb} GiB</span></span>
        ${f.map(e=>r`<span
            class="membar-seg cat-${e.cat} ${e.p.kind===`kv`?`is-kv`:``} ${this.focusPart===e.p.id?`is-focus`:``}"
            style="left:${s(e.left)};width:${s(e.p.gb)}"
            data-part=${e.p.id}
            tabindex="0"
            aria-label=${`${e.p.label} ${e.p.gb.toFixed(1)} GiB`}
            @mouseenter=${()=>this.focusPart=e.p.id}
            @mouseleave=${()=>this.focusPart=null}
            @focus=${()=>this.focusPart=e.p.id}
            @blur=${()=>this.focusPart=null}
          ></span>`)}
        ${a&&a.worst_gb!=null&&a.worst_gb>a.gb?r`<span class="membar-worst" style="left:${s(e.total_gb)};width:${s(Math.min(a.worst_gb-a.gb,o-e.total_gb))}" title=${`${y.memWorst}: ${a.worst_gb.toFixed(1)} GiB`}></span>`:l}
        ${i>0?r`<span class="membar-over" style="left:${s(n)};width:${s(e.total_gb-n)}"></span>`:l}
        <span class="membar-floor" style="left:${s(n)}"></span>
        ${p==null?l:r`<span class="membar-nowtick" style="left:${s(Math.min(p,o))}" title=${y.memNow(p)}></span>`}
      </div>
      <div class="membar-more" id="membar-more">
        <ul class="membar-legend">
          ${xt.filter(e=>m.has(e)).map(e=>r`<li class="cat-${e}"><i class="membar-sw" aria-hidden="true"></i>${y.memCat[e]} <span class="num">${(m.get(e)??0).toFixed(1)}</span></li>`)}
          <li class="cat-free"><i class="membar-sw" aria-hidden="true"></i>${y.memFloor} <span class="num">${e.floor_gb}</span></li>
        </ul>
        <p class="membar-detail" aria-live="polite">${g??(this.local?y.memLocal:e.notes[0]??`\xA0`)}</p>
      </div>
    </section>`}};e([t({attribute:!1})],G.prototype,`model`,void 0),e([t({attribute:!1})],G.prototype,`estimate`,void 0),e([t({attribute:!1})],G.prototype,`available`,void 0),e([t({type:Boolean})],G.prototype,`local`,void 0),e([t({attribute:!1})],G.prototype,`overText`,void 0),e([d()],G.prototype,`open`,void 0),e([d()],G.prototype,`focusPart`,void 0),G=e([n(`qse-memory-bar`)],G);var St=[`set_field`,`toggle`,`select_profile`,`go_to_step`,`next`,`previous`,`explain`,`read_device`],K=(e,t=!1)=>({ok:!1,error:e,...t?{stale:t}:{}});function q(e,t){return`${e}.${t}`}function Ct(e){if(e==null)return{};if(typeof e==`string`){if(!e.trim())return{};try{let t=JSON.parse(e);return t&&typeof t==`object`&&!Array.isArray(t)?t:null}catch{return null}}return typeof e==`object`&&!Array.isArray(e)?e:null}function J(e){if(typeof e==`number`&&Number.isInteger(e)&&e>=0&&e<E.length)return E[e];if(typeof e==`string`){if(/^\d+$/.test(e))return J(Number(e));if(w(e))return e}return null}function wt(e,t){return(e===`int`||e===`number`)&&typeof t==`string`&&t.trim()!==``&&Number.isFinite(Number(t))?Number(t):e===`bool`&&(t===`true`||t===`false`)?t===`true`:t}function Tt(e){return typeof e==`boolean`?e?`on`:`off`:typeof e==`number`?e.toLocaleString(`en-US`):e}function Et(e,t){let n=String(e.name??``);if(!St.includes(n))return/finish|apply|install|reset|delete|unload|load|confirm|submit/i.test(n)?K(y.tool.unknown(n)+` `+y.tool.userOnly(n)):K(y.tool.unknown(n));let r=Ct(e.args);if(!r)return K(y.tool.badArgs(`expected a JSON object`));if(n===`read_device`)return{ok:!0,kind:`read_device`};let i=t.model,a=t.selection,o=(e,n)=>{let r=t.touchedAt?.get(q(e,n));return r!=null&&t.turnStartedAt!=null&&r>t.turnStartedAt};switch(n){case`go_to_step`:{let e=r.n??r.step??r.index,n=J(e);return n?n===`install`?K(y.tool.installStep):n!==`models`&&!i?K(y.tool.noModel):{ok:!0,kind:`action`,actions:[{type:n===`models`?`tiles`:`go`,step:n}],undo:[{type:`go`,step:t.step}],target:{step:n,field:null},summary:y.toolDone.step(b[n]),result:y.tool.ok}:K(y.tool.noStep(String(e)))}case`next`:{if(!i)return K(y.tool.noModel);let e=O(t.step);if(e>=O(`review`))return K(y.tool.atEnd);let n=E[e+1];return{ok:!0,kind:`action`,actions:[{type:`next`}],undo:[{type:`go`,step:t.step}],target:{step:n,field:null},summary:y.toolDone.step(b[n]),result:y.tool.ok}}case`previous`:{let e=O(t.step);if(e<=0)return K(y.tool.atStart);let n=E[e-1];return{ok:!0,kind:`action`,actions:[{type:`previous`}],undo:[{type:`go`,step:t.step}],target:{step:n,field:null},summary:y.toolDone.step(b[n]),result:y.tool.ok}}}if(!i||!a)return K(y.tool.noModel);if(n===`explain`){let e=String(r.field??r.id??``),t=i.components.find(t=>t.id===e);if(t)return{ok:!0,kind:`explain`,target:{step:`components`,field:t.id},result:`${t.label}: ${t.what}${t.note?` `+t.note:``}`};let n=ve(i,e);if(n)return{ok:!0,kind:`explain`,target:{step:`methods`,field:n.field.id},result:`${n.field.label}: ${n.field.what}`};let a=i.profiles.find(t=>t.id===e);if(a)return{ok:!0,kind:`explain`,target:{step:`weights`,field:`profile`},result:`${a.label}: KL ${a.quality.kl_vs_bf16??`?`}, ${a.weights_gib} GiB in memory${a.note?`. `+a.note:``}`};let o={slots:y.slotsWhat,window:y.windowWhat,kv_pool:y.kvPoolWhat,floor_gb:y.floorWhat};return o[e]?{ok:!0,kind:`explain`,target:{step:`parallelism`,field:e},result:o[e]}:K(y.tool.noField(`any`,e))}if(n===`select_profile`){let e=String(r.id??r.profile??``),t=i.profiles.find(t=>t.id===e);return t?o(`weights`,`profile`)?K(y.tool.stale,!0):a.profile===e?{ok:!0,kind:`action`,actions:[],undo:[],target:{step:`weights`,field:`profile`},summary:y.toolDone.profile(t.label),result:y.tool.same}:{ok:!0,kind:`action`,actions:[{type:`profile`,id:e}],undo:[{type:`profile`,id:a.profile}],target:{step:`weights`,field:`profile`},summary:y.toolDone.profile(t.label),result:y.tool.ok}:K(y.tool.noProfile(e))}if(n===`toggle`){let e=String(r.component??r.id??``),t=wt(`bool`,r.on);return typeof t==`boolean`?Dt(i,a,e,t,o(`components`,e)):K(y.tool.badArgs(`on must be true or false`))}let s=J(r.step);if(!s)return K(y.tool.noStep(String(r.step)));let c=String(r.field??``),l=be(i).find(e=>e.id===s)?.fields.find(e=>e.field===c);if(!l)return K(y.tool.noField(s,c));if(Oe.has(c))return K(y.tool.secret(l.label));let u=wt(l.type,r.value),d=D(l,u);if(d)return K(y.tool.badArgs(`${c}: ${d}`));if(s===`components`)return Dt(i,a,c,u,o(s,c));if(o(s,c))return K(y.tool.stale,!0);let f={step:s,field:c},p=y.toolDone.field(l.label,Tt(u));if(s===`weights`)return Et({name:`select_profile`,args:{id:u}},t);if(s===`parallelism`){let e=c,t=a[e];return t===u?{ok:!0,kind:`action`,actions:[],undo:[],target:f,summary:p,result:y.tool.same}:{ok:!0,kind:`action`,actions:[{type:`parallel`,key:e,value:u}],undo:[{type:`parallel`,key:e,value:t}],target:f,summary:p,result:y.tool.ok}}if(s===`methods`){let e=ve(i,c);if(!e)return K(y.tool.noField(s,c));let t=a[e.group][c]??e.field.recommended;return t===u?{ok:!0,kind:`action`,actions:[],undo:[],target:f,summary:p,result:y.tool.same}:{ok:!0,kind:`action`,actions:[{type:`field`,group:e.group,id:c,value:u}],undo:[{type:`field`,group:e.group,id:c,value:t}],target:f,summary:p,result:y.tool.ok}}return K(y.tool.noField(s,c))}function Dt(e,t,n,r,i){let a=e.components.find(e=>e.id===n);if(!a)return K(y.tool.noComponent(n));if(a.required&&!r)return K(y.tool.required(a.label));let o=fe(e,t.components)[n];if(r&&o.disabled&&!o.required)return K(o.reason??y.tool.noComponent(n));if(i)return K(y.tool.stale,!0);let s={step:`components`,field:n},c=r?y.toolDone.toggleOn(a.label):y.toolDone.toggleOff(a.label);return(a.required||(t.components[n]??a.default))===r?{ok:!0,kind:`action`,actions:[],undo:[],target:s,summary:c,result:y.tool.same}:{ok:!0,kind:`action`,actions:[{type:`component`,id:n,on:r}],undo:[{type:`components`,set:{...t.components}}],target:s,summary:c,result:y.tool.ok}}var Y=new Set([`explain`,`read_device`]);function Ot(e){if(!e.tools||!e.calls.length)return null;let t=e.round+1>=4;return e.calls.some(e=>!e.ok&&!Y.has(e.name))?e.retried?{tools:!1,retried:!0}:{tools:!t,retried:!0}:e.calls.every(e=>Y.has(e.name)&&e.name!==`read_device`)?e.text.trim()?null:{tools:!1,retried:e.retried}:{tools:!t,retried:e.retried}}function X(e){let t=new Map,n=0,r=r=>{let i=new Map,a=null;for(let t=n;t<r;t++){let n=e[t];if(n.role===`assistant`)for(let e of n.tool_calls??[])i.set(e.id,e.name);if(n.role!==`tool`||Y.has(i.get(n.tool_call_id??``)??``))continue;let r={};try{r=JSON.parse(n.content)}catch{}r.ok===!1?a=r.stale||a===`stale`?`stale`:`unchanged`:r.ok===!0&&(a=null)}a&&r>n&&t.set(r-1,a)};return e.forEach((e,t)=>{e.role===`user`&&t!==0&&(r(t),n=t)}),r(e.length),t}function kt(e,t){return t&&e.ok&&e.summary?e.summary:null}var At=0;function jt(e){return e.charAt(0).toUpperCase()+e.slice(1)}var Z=class extends a{constructor(...e){super(...e),this.host=null,this.step=`models`,this.chat=[],this.finished=!1,this.phase=`checking`,this.status=null,this.figure=`sleeping`,this.progress=null,this.streaming=!1,this.partial=``,this.composer=``,this.error=null,this.notice=null,this.sources=[],this.open=!1,this.toast=null,this.chips=new Map,this.ctrl=null,this.loadPoll=null,this.ping=null,this.loadStartedAt=0,this.lastUserText=``,this.opener=null,this.toastTimer=null,this.onKey=e=>{if(!this.open||!this.sheetMode())return;if(e.key===`Escape`){e.preventDefault(),this.close();return}if(e.key!==`Tab`)return;let t=this.querySelector(`.wren-panel`);if(!t)return;let n=[...t.querySelectorAll(`button:not([disabled]), textarea:not([disabled]), a[href], input:not([disabled])`)].filter(e=>e.offsetParent!==null);if(!n.length)return;let r=n[0],i=n[n.length-1];e.shiftKey&&document.activeElement===r?(e.preventDefault(),i.focus()):!e.shiftKey&&document.activeElement===i?(e.preventDefault(),r.focus()):t.contains(document.activeElement)||(e.preventDefault(),r.focus())}}connectedCallback(){super.connectedCallback(),this.checkStatus(),this.ping=setInterval(()=>{this.phase===`ready`&&document.visibilityState===`visible`&&this.checkStatus(!0)},3e4),document.addEventListener(`keydown`,this.onKey)}disconnectedCallback(){this.ctrl?.abort(),this.loadPoll&&clearTimeout(this.loadPoll),this.ping&&clearInterval(this.ping),this.toastTimer&&clearTimeout(this.toastTimer),document.removeEventListener(`keydown`,this.onKey),this.lockScroll(!1),super.disconnectedCallback()}updated(e){e.has(`finished`)&&this.finished&&this.phase!==`finished`&&this.sleepForInstall()}emit(e){this.figure=ce(this.figure,e)}async checkStatus(e=!1){if(this.finished){this.phase=`finished`,this.figure=`sleeping`;return}try{let e=await C.assistant.status();this.applyStatus(e)}catch(t){if(e)return;this.phase=`failed`,this.error=m(t).status===0?y.wrenOffline:m(t).message,this.emit(`error`)}}applyStatus(e){switch(this.status=e,e.state){case`loaded`:this.phase!==`ready`&&(this.phase=`ready`,this.progress=null,this.figure=`loading`,this.emit(`loaded`));break;case`loading`:this.phase=`loading`,this.progress=this.timeProgress(e.loading_s??null),this.emit(`load-progress`),this.scheduleLoadPoll();break;case`refused`:this.phase=`refused`,this.emit(`error`);break;case`failed`:this.phase=`failed`,this.error=e.reason??y.wrenFailed,this.emit(`error`);break;default:this.phase=e.model==null?`refused`:`asleep`,this.figure=`sleeping`}}timeProgress(e){let t=e??(Date.now()-this.loadStartedAt)/1e3;return Math.min(.95,1-Math.exp(-t/8))}scheduleLoadPoll(){this.loadPoll&&clearTimeout(this.loadPoll),this.loadPoll=setTimeout(()=>void this.checkStatus(!0),500)}async wake(){this.error=null,this.loadStartedAt=Date.now(),this.phase=`loading`,this.progress=0,this.emit(`load-start`);try{this.applyStatus(await C.assistant.load())}catch(e){if(T(e)){this.phase=`asleep`,this.progress=null,this.emit(`unload`);return}let t=m(e),n=e instanceof h?e.body:void 0;n?.assistant&&(this.status=n.assistant),t.status===409?(this.phase=`refused`,n?.assistant?.reason&&(this.status={...this.status,reason:n.assistant.reason})):(this.phase=`failed`,this.error=t.status===0?y.wrenOffline:t.message),this.emit(`error`)}}async sleep(){this.stop();try{this.applyStatus(await C.assistant.unload())}catch{}this.phase=`asleep`,this.emit(`unload`)}async sleepForInstall(){this.ctrl?.abort(),this.phase=`finished`,this.emit(`unload`),this.close();try{await C.assistant.unload()}catch{}}stop(){this.ctrl&&this.ctrl.abort()}async send(e){let t=e.trim();if(!t||this.streaming||!this.host)return;this.lastUserText=t,this.composer=``,this.error=null,this.notice=null,this.toast=null;let n=[...this.chat,{role:`user`,content:t}];this.setChat(n),await this.turn(n)}async retry(){if(!this.lastUserText)return;this.error=null;let e=this.chat[this.chat.length-1]?.role===`user`?this.chat:[...this.chat,{role:`user`,content:this.lastUserText}];this.setChat(e),await this.turn(e)}setChat(e){this.chat=e,this.host?.saveChat(e),this.updateComplete.then(()=>this.scrollLog())}async turn(e){let t=this.host,n=Date.now(),r=e;this.streaming=!0,this.emit(`send`),this.ctrl=new AbortController;let i=this.ctrl.signal,a=!0,o=!1,s=!1;try{for(let e=1;;e++){let c=``,l=[],u=[],d=[],f=!0,p=null;if(this.partial=``,await he({messages:r,page:t.pageState(),...a?{}:{tools:!1}},e=>{e.type===`state`?this.emit(e.state===`thinking`?`state-thinking`:e.state===`loading`?`load-progress`:`first-token`):e.type===`text`?(c+=e.delta,this.partial=c,f?(f=!1,this.emit(`first-token`)):this.emit(`delta`),this.fig?.blip(),this.fabFig?.blip(),this.scrollLog()):e.type===`tool_call`?l.push({id:e.id||`call_${++At}`,name:e.name,args:e.args}):e.type===`done`?(this.sources=e.sources??[],e.unchanged&&(s=!0)):e.type===`error`&&(p=e.message)},i),p&&/another tab/i.test(p)){this.notice=y.wrenBusy;break}if(p)throw new h(`/v1/setup/assistant/chat`,500,p,`stream`);for(let e of l){let r=await t.execute(e,n);r.ok&&(this.emit(`tool-call`),this.fig?.hop(),this.emit(`acted`)),u.push({role:`tool`,tool_call_id:e.id,content:JSON.stringify(r.ok?{ok:!0,result:r.result,...r.summary?{summary:r.summary}:{}}:{ok:!1,error:r.result,...r.stale?{stale:!0}:{}})}),this.chips=new Map(this.chips).set(e.id,{summary:r.summary,ok:r.ok,stale:r.stale,undo:r.undo,undone:!1}),d.push({name:e.name,ok:r.ok,changed:!!r.summary});let i=kt(r,this.open&&this.sheetMode());i&&this.showToast(e.id,i,r.reveal??null)}let m={role:`assistant`,content:c,...l.length?{tool_calls:l}:{}};r=[...r,m,...u],this.partial=``,this.setChat(r);let g=Ot({round:e,tools:a,text:c,calls:d,retried:o});if(!g)break;({tools:a,retried:o}=g)}s&&!X(r).has(r.length-1)&&(this.notice=y.wrenNotChanged),this.emit(`done`)}catch(e){if(e.name===`AbortError`)this.partial&&this.setChat([...r,{role:`assistant`,content:`${this.partial} ${y.wrenStopped}`}]),this.partial=``,this.emit(`stop`);else{let t=m(e);t.status===409?this.notice=y.wrenBusy:t.status===0?this.error=y.wrenOffline:t.status===503||/unload|asleep|not loaded/i.test(t.message)?(this.error=y.wrenWentToSleep,this.checkStatus(!0)):this.error=t.message||y.wrenError,this.partial&&this.setChat([...r,{role:`assistant`,content:this.partial}]),this.partial=``,this.emit(t.status===409?`done`:`error`)}}finally{this.streaming=!1,this.ctrl=null}}undoChip(e){let t=this.chips.get(e);t?.undo&&!t.undone&&(t.undo(),this.chips=new Map(this.chips).set(e,{...t,undone:!0}))}scrollLog(){let e=this.log;e&&(e.scrollTop=e.scrollHeight)}sheetMode(){return typeof matchMedia==`function`&&matchMedia(`(max-width: 1100px)`).matches}toggleOpen(){this.open?this.close():(this.opener=document.activeElement,this.open=!0,this.lockScroll(!0),this.updateComplete.then(()=>(this.querySelector(`.wren-panel .wren-input, .wren-panel .btn-primary, .wren-close`)??null)?.focus()))}close(){this.open&&(this.open=!1,this.lockScroll(!1),this.opener?.focus?.())}lockScroll(e){document.documentElement.classList.toggle(`wren-sheet-open`,e&&this.sheetMode())}showToast(e,t,n){this.toast={id:e,summary:t,reveal:n},this.toastTimer&&clearTimeout(this.toastTimer),this.toastTimer=setTimeout(()=>this.toast=null,12e3)}async showMe(){let e=this.toast?.reveal;this.toast=null,this.close(),await this.updateComplete,requestAnimationFrame(()=>e?.())}statusWord(){return this.phase===`finished`?y.wrenFinished:this.phase===`asleep`?y.wrenAsleep:this.phase===`refused`?y.wrenCannotLoad:this.phase===`failed`?y.wrenFailed:this.phase===`loading`?y.wrenWaking(this.progress):y.wrenStatus[this.figure]??``}render(){let e=this.figure;return r`
      <button class="wren-fab ${this.open?`is-open`:``}" aria-label=${this.open?y.wrenClose:y.wrenOpen} aria-expanded=${this.open} aria-controls="wren-panel" @click=${()=>this.toggleOpen()} data-figure=${e}>
        <qse-wren-figure class="wren-fab-fig" .state=${e} .size=${50} .progress=${this.progress}></qse-wren-figure>
      </button>
      <div class="wren-scrim ${this.open?`is-open`:``}" @click=${()=>this.close()}></div>
      <aside id="wren-panel" class="wren-panel ${this.open?`is-open`:``}" aria-label=${y.wrenName} data-phase=${this.phase} data-figure=${e} role="complementary">
        <div class="wren-handle" aria-hidden="true"></div>
        <header class="wren-head">
          <qse-wren-figure class="wren-main" .state=${e} .size=${76} .progress=${this.progress}></qse-wren-figure>
          <div class="wren-titles">
            <h2 class="wren-name">${y.wrenName}</h2>
            <p class="wren-status" role="status" aria-live="polite" data-wren-status>${this.statusWord()}</p>
            ${this.status?.model&&this.phase===`ready`?r`<p class="wren-model">${this.status.model}${this.status.fallback?r` <span title=${y.wrenFallback}>(${y.wrenFallback})</span>`:l}</p>`:l}
          </div>
          <div class="wren-head-tools">
            ${this.phase===`ready`&&!this.streaming?r`<button class="btn btn-ghost btn-icon" title=${y.wrenSleep} aria-label=${y.wrenSleep} @click=${()=>void this.sleep()}>${i(`moon`)}</button>`:l}
            <button class="btn btn-ghost btn-icon wren-close" aria-label=${y.wrenClose} title=${y.wrenClose} @click=${()=>this.close()}>${i(`x`)}</button>
          </div>
        </header>
        ${this.renderBody()}
      </aside>
    `}hero(){return r`<qse-wren-figure class="wren-hero" .state=${this.figure} .size=${148} .progress=${this.progress}></qse-wren-figure>`}renderBody(){switch(this.phase){case`checking`:return r`<div class="wren-body wren-center">${this.hero()}</div>`;case`finished`:return r`<div class="wren-body wren-center" data-wren-finished>${this.hero()}<p>${y.wrenFinished}</p></div>${this.renderComposer(!0)}`;case`asleep`:return r`<div class="wren-body wren-center">
            ${this.hero()}
            <p class="wren-lead">${y.wrenAsleep}</p>
            <p class="muted small">${y.wrenAsleepBody}</p>
            <button class="btn btn-primary wren-wake" @click=${()=>void this.wake()}>${y.wrenWake}</button>
          </div>`;case`loading`:return r`<div class="wren-body wren-center">
            ${this.hero()}
            <p class="wren-lead">${y.wrenWaking(this.progress)}</p>
            <div class="wren-loadbar" role="progressbar" aria-valuemin="0" aria-valuemax="100" aria-valuenow=${this.progress==null?l:Math.round(this.progress*100)} aria-label=${y.wrenWaking(null)}><span style="width:${Math.round((this.progress??0)*100)}%"></span></div>
          </div>`;case`refused`:{let e=this.status,t=e?.model==null&&e!=null?y.wrenNoModel:y.wrenCannotLoadMemory(e?.claim_gib??null,this.host?.memAvailable()??null);return r`<div class="wren-body wren-center" data-wren-refused>
            ${this.hero()}
            <p class="wren-lead">${y.wrenCannotLoad}</p>
            <p class="muted small">${e?.reason?jt(e.reason):t}</p>
            ${e?.reason?r`<p class="muted small">${t}</p>`:l}
            ${e?.model==null?l:r`<button class="btn wren-wake" @click=${()=>void this.wake()}>${i(`refresh`)} ${y.wrenRetry}</button>`}
          </div>`}case`failed`:return r`<div class="wren-body wren-center" data-wren-failed>
            ${this.hero()}
            <p class="wren-lead">${y.wrenFailed}</p>
            <p class="muted small">${this.error??``}</p>
            <button class="btn wren-wake" @click=${()=>void this.wake()}>${i(`refresh`)} ${y.wrenRetry}</button>
          </div>`;case`ready`:return this.renderChat()}}renderChat(){let e=this.chat,t=X(e);return r`
      <div class="wren-body wren-log" aria-live="off">
        ${e.length===0?r`<p class="wren-msg is-assistant wren-hello">${y.wrenHello}</p>`:l}
        ${ee(e.map((e,t)=>({m:e,i:t})),({i:e})=>e,({m:n,i})=>r`${this.renderMessage(n,i===e.length-1)}${t.has(i)?r`<p class="wren-note" data-tool-rejected>${t.get(i)===`stale`?y.tool.stale:y.wrenNotChanged}</p>`:l}`)}
        ${this.partial?r`<div class="wren-msg is-assistant is-streaming">${qe(Je(this.partial))}</div>`:l}
        ${this.streaming&&!this.partial?r`<div class="wren-msg is-assistant wren-typing" aria-hidden="true"><span></span><span></span><span></span></div>`:l}
        ${this.notice?r`<p class="wren-note">${this.notice}</p>`:l}
        ${this.error?r`<div class="wren-error" role="alert" data-wren-error>
              <span>${this.error}</span>
              <button class="btn btn-sm" @click=${()=>void this.retry()}>${i(`refresh`)} ${y.wrenRetry}</button>
            </div>`:l}
      </div>
      ${this.toast?r`<div class="wren-toast" role="status" data-wren-toast>
            <span class="wren-chip-dot" aria-hidden="true"></span>
            <span class="wren-toast-text">${this.toast.summary}</span>
            ${this.toast.reveal?r`<button class="btn btn-sm" @click=${()=>void this.showMe()}>${y.wrenShowMe}</button>`:l}
          </div>`:l}
      ${this.renderChips()} ${this.renderComposer(!1)}
    `}renderMessage(e,t){if(e.role===`user`)return r`<div class="wren-msg is-user">${e.content}</div>`;if(e.role===`assistant`)return e.content.trim()?r`<div class="wren-msg is-assistant">${qe(Je(e.content))}
        ${t&&this.sources.length?r`<p class="wren-sources">${y.wrenSources} ${this.sources.map((e,t)=>r`${t?`, `:``}<span title=${e.path}>${e.title||e.path}</span>`)}</p>`:l}
      </div>`:l;let n={};try{n=JSON.parse(e.content)}catch{}let i=e.tool_call_id?this.chips.get(e.tool_call_id):void 0;if(n.ok===!1||i?.ok===!1)return l;let a=i?.summary??n.summary;return a?r`<div class="wren-chip ${i?.undone?`is-undone`:``} ${this.toast?.id===e.tool_call_id?`is-fresh`:``}" data-tool-chip>
      <span class="wren-chip-dot" aria-hidden="true"></span>
      <span>${a}</span>
      ${i?.undo?r`<button class="btn btn-ghost btn-sm" ?disabled=${i.undone} @click=${()=>this.undoChip(e.tool_call_id)}>${i.undone?y.wrenUndone:y.wrenUndo}</button>`:l}
    </div>`:l}renderChips(){if(this.streaming)return l;let e=y.wrenChips[this.step]??[];return r`<div class="wren-suggest">${e.map(e=>r`<button class="wren-suggestion" @click=${()=>void this.send(e)}>${e}</button>`)}</div>`}renderComposer(e){return r`<form
      class="wren-composer"
      @submit=${e=>{e.preventDefault(),this.send(this.composer)}}
    >
      <label class="sr-only" for="wren-input">${y.wrenPlaceholder}</label>
      <textarea
        id="wren-input"
        class="wren-input"
        rows="1"
        .value=${this.composer}
        ?disabled=${e}
        placeholder=${e?y.wrenFinished:y.wrenPlaceholder}
        @focus=${()=>this.emit(`focus`)}
        @blur=${()=>!this.composer&&this.emit(`blur`)}
        @input=${e=>{this.composer=e.target.value,this.emit(`input`)}}
        @keydown=${e=>{e.key===`Enter`&&!e.shiftKey&&!e.isComposing&&(e.preventDefault(),this.send(this.composer))}}
      ></textarea>
      ${this.streaming?r`<button type="button" class="btn wren-stop" @click=${()=>this.stop()} aria-label=${y.wrenStop}>${i(`stop`)}<span>${y.wrenStop}</span></button>`:r`<button type="submit" class="btn btn-primary wren-send" ?disabled=${e||!this.composer.trim()} aria-label=${y.wrenSend}>${i(`send`)}</button>`}
    </form>`}};e([t({attribute:!1})],Z.prototype,`host`,void 0),e([t({attribute:!1})],Z.prototype,`step`,void 0),e([t({attribute:!1})],Z.prototype,`chat`,void 0),e([t({type:Boolean})],Z.prototype,`finished`,void 0),e([d()],Z.prototype,`phase`,void 0),e([d()],Z.prototype,`status`,void 0),e([d()],Z.prototype,`figure`,void 0),e([d()],Z.prototype,`progress`,void 0),e([d()],Z.prototype,`streaming`,void 0),e([d()],Z.prototype,`partial`,void 0),e([d()],Z.prototype,`composer`,void 0),e([d()],Z.prototype,`error`,void 0),e([d()],Z.prototype,`notice`,void 0),e([d()],Z.prototype,`sources`,void 0),e([d()],Z.prototype,`open`,void 0),e([d()],Z.prototype,`toast`,void 0),e([d()],Z.prototype,`chips`,void 0),e([p(`qse-wren-figure.wren-main`)],Z.prototype,`fig`,void 0),e([p(`qse-wren-figure.wren-fab-fig`)],Z.prototype,`fabFig`,void 0),e([p(`.wren-log`)],Z.prototype,`log`,void 0),e([p(`.wren-input`)],Z.prototype,`input`,void 0),Z=e([n(`qse-wren`)],Z);var Q=_(`setup`),Mt={title:`Sign in to TandemApp’s controller`,body:`This Spark’s Setup Guide runs through TandemApp’s controller, which has an admin password: the TANDEM_ADMIN_TOKEN it runs with.`,kept:`The session lasts 12 hours in this browser; your progress stays in it too.`},$=class extends a{constructor(...e){super(...e),this.path=[],this.node=null,this.wiz=nt(),this.storageProblem=null,this.storageFailed=!1,this.list={state:`loading`},this.model=null,this.device=null,this.serverEst=null,this.estLocal=!1,this.plan=null,this.confirmOpen=!1,this.applying=!1,this.removeAfter=!1,this.applyError=null,this.jobInitial=null,this.previousProfile=null,this.resetOpen=!1,this.unauthorized=!1,this.winNarrow=!1,this.slotsDraft=null,this.winDraft={window:null},this.nextRefused=!1,this.communitySyncing=!1,this.store=null,this.devPoll=null,this.estTimer=null,this.estCtrl=null,this.planTimer=null,this.planCtrl=null,this.commCtrl=null,this.warnedDiff=!1,this.touchedAt=new Map,this.loadingModel=null,this.modelSeq=0,this.onUnauthorized=()=>{this.unauthorized=!0},this.onSignedIn=()=>{this.unauthorized=!1,this.list.state!==`ready`&&this.loadList();let e=this.path[0];e&&this.model?.state!==`ready`&&this.loadModel(e),this.loadDevice(),this.wiz.selection&&this.queueEstimate(),this.wiz.step===`review`&&this.queuePlan(),this.wiz.step===`weights`&&this.syncCommunity()},this.onReset=()=>{this.wiz=nt(),this.plan=null,this.jobInitial=null,location.hash!==this.at()&&(location.hash=this.at())},this.host={pageState:()=>Ge(this.m,this.wiz.step,this.wiz.selection,this.currentEstimate(),{errors:this.visibleErrors()}),execute:(e,t)=>this.executeTool(e,t),saveChat:e=>this.dispatch({type:`chat`,chat:e}),memAvailable:()=>this.device?.mem_available_gb??null}}connectedCallback(){super.connectedCallback(),this.node??=g().params.get(`node`);let e=this.node?S()?.nodes.find(e=>e.id===this.node):null;me(this.node?{id:this.node,alias:e?.alias??this.node}:null),pe(this.node?e=>this.querySelector(`qse-setup-confirm`).ask(e):null),this.store=Xe();let t=it(this.store,this.key);this.wiz=t.state,this.storageProblem=t.problem,(t.problem===`corrupt`||t.problem===`version`)&&Ye(this.store,this.key),this.loadList(),this.devPoll=v(()=>this.loadDevice(),5e3),this.devPoll.start(),window.addEventListener(`qse-setup-reset`,this.onReset),window.addEventListener(`qse-setup-unauthorized`,this.onUnauthorized),window.addEventListener(`qse-signed-in`,this.onSignedIn)}disconnectedCallback(){this.devPoll?.stop(),this.estTimer&&clearTimeout(this.estTimer),this.planTimer&&clearTimeout(this.planTimer),this.estCtrl?.abort(),this.planCtrl?.abort(),this.commCtrl?.abort(),window.removeEventListener(`qse-setup-reset`,this.onReset),window.removeEventListener(`qse-setup-unauthorized`,this.onUnauthorized),window.removeEventListener(`qse-signed-in`,this.onSignedIn),me(null),pe(null),super.disconnectedCallback()}get key(){return ot(this.node)}at(e=``){return`${Q}${e}${this.node?`?node=${encodeURIComponent(this.node)}`:``}`}async loadList(){this.list={state:`loading`};try{let e=await C.models();this.list=e.models.length||e.errors.length?{state:`ready`,data:e}:{state:`empty`,note:y.modelsEmpty}}catch(e){let t=m(e),n=!this.node&&t.status===404?S()?.nodes.find(e=>e.status!==`off`):null;if(n){location.replace(`${Q}${this.path.length?`/${this.path.map(encodeURIComponent).join(`/`)}`:``}?node=${encodeURIComponent(n.id)}`);return}this.list={state:`error`,endpoint:t.endpoint,message:t.status===401?y.unauthorized:t.message}}}async loadModel(e){if(this.loadingModel===e)return;this.loadingModel=e;let t=++this.modelSeq;this.model={state:`loading`};try{let n=_e(await C.model(e));if(t!==this.modelSeq)return;this.model={state:`ready`,data:n},await this.openModel(n,this.routeStep()??void 0)}catch(n){if(t!==this.modelSeq)return;let r=m(n);this.model={state:`error`,endpoint:r.endpoint,message:r.status===404?`${y.modelsError} (${e})`:r.message}}finally{t===this.modelSeq&&(this.loadingModel=null)}}async loadDevice(){try{this.device=await C.device()}catch{}}async openModel(e,t){let n=null;if(!(this.wiz.model===e.id&&this.wiz.selection))try{n=(await C.current(e.id)).selection}catch{n=null}this.model?.state===`ready`&&this.model.data.id===e.id&&(this.dispatch({type:`open`,model:e,step:t,seed:n},!1),this.wiz.step===`weights`&&this.syncCommunity())}get m(){return this.model?.state===`ready`?this.model.data:null}routeStep(){let e=this.path[1];return w(e)?e:null}willUpdate(e){e.has(`path`)&&this.syncFromRoute()}syncFromRoute(){let[e,t]=this.path;if(!e){this.wiz.step!==`models`&&(this.wiz={...this.wiz,step:`models`});return}let n=this.m;if(!n||n.id!==e){this.loadModel(e);return}if(this.wiz.model!==e||!this.wiz.selection){this.openModel(n,w(t)?t:void 0);return}let r=w(t)?t:`overview`;r===`install`&&!this.wiz.job&&(r=`review`),r===`models`&&(r=`overview`),r!==this.wiz.step&&this.dispatch({type:`go`,step:r},!1)}hashFor(e){return e.step===`models`||!e.model?this.at():this.at(`/${e.model}/${e.step}`)}dispatch(e,t=!0){let n=et(this.wiz,e,this.m);if(n===this.wiz)return;let r=n.step!==this.wiz.step;this.wiz=n,this.store&&(this.storageFailed=!Qe(this.store,n,this.key));let i=this.hashFor(n);location.hash!==i&&(t?location.hash=i:history.replaceState(null,``,i)),(r||e.type===`recommended`||e.type===`parallel`&&e.key===`slots`)&&(this.slotsDraft=null,this.nextRefused=!1),(r||e.type===`recommended`||e.type===`profile`)&&(this.winDraft={window:null}),r&&(t&&window.scrollTo({top:0,behavior:se()?`auto`:`smooth`}),n.step===`review`&&this.queuePlan(),n.step===`weights`&&this.syncCommunity()),e.type!==`go`&&e.type!==`next`&&e.type!==`previous`&&e.type!==`chat`&&e.type!==`dismiss`&&(this.queueEstimate(),n.step===`review`&&this.queuePlan())}user(e,t,n){this.touchedAt.set(q(e,t),Date.now()),this.dispatch(n)}selKey(e){return JSON.stringify(e)}queueEstimate(){this.estTimer&&clearTimeout(this.estTimer),this.estTimer=setTimeout(()=>void this.confirmEstimate(),150)}async confirmEstimate(){let e=this.wiz.selection;if(!e)return;this.estCtrl?.abort();let t=this.estCtrl=new AbortController,n=this.selKey(e);try{let r=await C.estimate(e,t.signal);if(t.signal.aborted||n!==this.selKey(this.wiz.selection))return;this.serverEst={key:n,est:r},this.estLocal=!1;let i=this.m?He(this.m,e):null;i&&Math.abs(i.total_gb-r.total_gb)>.05&&!this.warnedDiff&&(this.warnedDiff=!0,console.warn(`setup: the server's estimate (${r.total_gb} GiB) and the page's (${i.total_gb} GiB) differ; showing the server's`))}catch(e){if(e.name===`AbortError`||t.signal.aborted)return;this.estLocal=!0}}currentEstimate(){let e=this.m,t=this.wiz.selection;if(!e||!t)return null;let n=He(e,t,this.device?.mem_available_gb??null),r=this.serverEst;return r&&r.key===this.selKey(t)&&(!n||Math.abs(r.est.total_gb-n.total_gb)>.05)?{...r.est,available_gb:this.device?.mem_available_gb??r.est.available_gb}:n}queuePlan(){this.planTimer&&clearTimeout(this.planTimer),this.planTimer=setTimeout(()=>void this.loadPlan(),200)}async loadPlan(){let e=this.wiz.selection;if(!e)return;this.planCtrl?.abort();let t=this.planCtrl=new AbortController;(!this.plan||this.plan.state!==`ready`)&&(this.plan={state:`loading`});try{let n=await C.plan(e,t.signal);t.signal.aborted||(this.plan={state:`ready`,data:n})}catch(e){if(e.name===`AbortError`||t.signal.aborted)return;let n=m(e);this.plan={state:`error`,endpoint:n.endpoint,message:n.message}}}async syncCommunity(){let e=this.m;if(!e)return;this.commCtrl?.abort();let t=this.commCtrl=new AbortController;this.communitySyncing=!0;try{let n=await C.communitySync(t.signal);if(t.signal.aborted||this.m?.id!==e.id)return;let r=this.m,i={...n.sync,dropped:n.sync.dropped.filter(t=>t.model===e.id),add_url:r.community_sync?.add_url||n.sync.add_url},a=_e({...r,community_sync:i},n.models[e.id]??[]);this.model={state:`ready`,data:a};let o=this.wiz.selection;o&&!a.profiles.some(e=>e.id===o.profile)?this.dispatch({type:`open`,model:a},!1):this.queueEstimate()}catch(e){if(e.name===`AbortError`||t.signal.aborted)return;let n=this.m;n?.community_sync&&!(e instanceof h&&e.status===404)&&(this.model={state:`ready`,data:{...n,community_sync:{...n.community_sync,error:m(e).message,checked_at:new Date().toISOString()}}})}finally{t.signal.aborted||(this.communitySyncing=!1)}}visibleErrors(){let e=this.issues()[this.wiz.step]??[];return this.wiz.step!==`review`||this.plan?.state!==`ready`?e:[...e,...this.plan.data.errors.filter(t=>!e.includes(t))]}async executeTool(e,t){let n=Et(e,{model:this.m,selection:this.wiz.selection,step:this.wiz.step,touchedAt:this.touchedAt,turnStartedAt:t});if(!n.ok)return{ok:!1,result:n.error,summary:null,undo:null,stale:n.stale};if(n.kind===`read_device`)try{let e=await C.device();this.device=e;let t={mode:e.mode,mem_total_gb:e.mem_total_gb,mem_available_gb:e.mem_available_gb,gpu:e.gpu,disk:e.disk,engine:e.engine,active_profile:e.active_profile,profiles_on_disk:e.profiles_on_disk,password_set:e.password_set};return{ok:!0,result:JSON.stringify(t),summary:null,undo:null}}catch(e){return{ok:!1,result:m(e).message,summary:null,undo:null}}if(n.kind===`explain`)return n.target.step===this.wiz.step&&this.highlight(n.target.step,n.target.field),{ok:!0,result:n.result,summary:null,undo:null};let{target:r}=n;r.field&&r.step!==this.wiz.step&&this.dispatch({type:`go`,step:r.step});for(let e of n.actions)this.dispatch(e);await this.updateComplete,r.field&&this.highlight(r.step,r.field);let i=n.undo.length?()=>{for(let e of n.undo)this.dispatch(e);r.field&&this.highlight(r.step,r.field)}:null,a=r.field?()=>{r.step!==this.wiz.step&&this.dispatch({type:`go`,step:r.step}),this.highlight(r.step,r.field)}:null;return{ok:!0,result:n.result,summary:n.actions.length?n.summary:null,undo:i,reveal:a}}async highlight(e,t){if(!t)return;await this.updateComplete;let n=this.querySelector(`[data-field="${CSS.escape(q(e,t))}"]`);if(!n)return;let r=n.getBoundingClientRect();(r.top<120||r.bottom>innerHeight-80)&&n.scrollIntoView({block:`center`,behavior:se()?`auto`:`smooth`}),n.setAttribute(`data-wren-acting`,``),n.querySelector(`:scope > .wren-mini`)?.remove();let i=document.createElement(`span`);i.className=`wren-mini`,i.setAttribute(`aria-hidden`,`true`),n.append(i),o(le,i),setTimeout(()=>{n.removeAttribute(`data-wren-acting`),i.remove()},1600)}issues(){let e=this.m,t=this.wiz.selection;return!e||!t?{}:Ne(e,t,this.device,this.currentEstimate())}finishBlockers(){let e=this.m,t=this.wiz.selection;if(!e||!t)return[];let n=[],r=this.currentEstimate();r&&!r.fits&&n.push(y.reviewOver(ze(r))),Ve(e,t,this.device)&&n.push(y.blockedToken);let i=Object.keys(this.issues()).filter(e=>e!==`review`);return i.length&&n.push(y.blockedInvalid(i.map(e=>b[e].toLowerCase()))),this.plan?.state===`ready`&&!this.plan.data.ok&&!n.length&&n.push(...this.plan.data.errors),n}async finish(){let e=this.wiz.selection;if(!e||this.applying||this.finishBlockers().length)return;this.applying=!0,this.applyError=null,this.previousProfile=this.device?.active_profile??null,this.querySelector(`qse-wren`)?.sleepForInstall();let t=e;try{let e=this.device?.active_profile,n=await C.apply({selection:t,...this.removeAfter&&e&&e!==t.profile?{remove_after:[e]}:{}});if(this.confirmOpen=!1,!this.isConnected||this.path[0]!==t.model||this.wiz.step!==`review`){this.node||re(n.job);return}this.jobInitial=n.job,this.dispatch({type:`job`,id:n.job.id})}catch(e){this.applyError=this.applyErrorText(e)}finally{this.applying=!1}}applyErrorText(e){let t=m(e);if(t.status===401||T(e))return null;let n=e instanceof h?e.body:void 0;return n?.plan?(this.plan={state:`ready`,data:n.plan},this.confirmOpen=!1,n.plan.errors.join(` `)||t.message):e instanceof h&&e.type===`job_running`?y.blockedBusy:e instanceof h&&e.type===`busy`?y.blockedRequests(n?.error?.running??1):`${y.installError} ${t.message}`}render(){let e=this.wiz.step,t=this.m,n=this.path.length>0;return r`<div class="view view-setup" data-step=${n?e:`models`}>
      ${this.node?this.renderNodeBar():l}
      <div class="setup">
        <div class="setup-main">
          ${this.renderBanners()}
          ${this.unauthorized?r`<qse-setup-signin .texts=${this.node?Mt:null}></qse-setup-signin>`:n?this.renderModelArea():this.renderTiles()}
          <footer class="setup-foot">
            <button class="btn btn-ghost btn-sm" aria-expanded=${this.resetOpen} aria-controls="setup-reset" @click=${()=>this.resetOpen=!this.resetOpen}>${i(`trash`)} ${y.resetLink}</button>
          </footer>
          ${this.resetOpen?r`<section class="panel setup-reset" id="setup-reset"><header class="panel-head"><h2 class="panel-title">${y.resetHeading}</h2></header><qse-reset inSetup></qse-reset></section>`:l}
        </div>
        <qse-wren class="setup-wren" .host=${this.host} .step=${n?e:`models`} .chat=${this.wiz.chat} .finished=${e===`install`&&!!this.wiz.job}></qse-wren>
      </div>
      ${t?this.renderConfirm(t):l}
    </div>`}renderNodeBar(){let e=S(),t=e?.nodes.find(e=>e.id===this.node)??null,n=e=>location.hash=`${Q}${this.path.length?`/${this.path.map(encodeURIComponent).join(`/`)}`:``}?node=${encodeURIComponent(e)}`;return r`<div class="setup-node-bar" data-setup-node=${this.node}>
        <span class="eyebrow">Setup Guide on</span>
        ${t?ae(t,{lg:!0}):r`<b>${this.node}</b>`}
        ${t&&t.status===`off`?r`<span class="small warn-ink">${t.status_text}</span>`:r`<span class="xs muted">through TandemApp’s controller; each change is said before it goes</span>`}
        <span class="setup-node-sp"></span>
        ${e&&e.nodes.length>1?r`<label class="small setup-node-pick"><span class="muted">Spark</span><select data-setup-node-pick aria-label="Set up another Spark" @change=${e=>n(e.target.value)}>${e.nodes.map(e=>r`<option value=${e.id} ?selected=${e.id===this.node}>${e.alias}</option>`)}</select></label>`:l}
        <a class="btn btn-sm btn-ghost" href="#/models/place" data-back-placement>← Serve a model</a>
      </div>
      <qse-setup-confirm .alias=${t?.alias??this.node??``}></qse-setup-confirm>`}renderBanners(){let e=this.storageProblem,t=[];return(e===`unavailable`||this.storageFailed)&&t.push(y.storageOff),e===`corrupt`&&t.push(y.storageCorrupt),e===`version`&&t.push(y.storageOld),this.wiz.notice&&this.wiz.step!==`components`&&this.wiz.step!==`parallelism`&&t.push(this.wiz.notice),r`${t.map(e=>r`<div class="banner setup-banner" role="status" data-setup-notice>${i(`info`)} <span>${e}</span></div>`)}`}renderTiles(){let e=this.list,t=this.device,n=this.wiz.model&&this.wiz.selection?this.wiz:null;return r`<section class="setup-tiles">
      <header class="setup-hero">
        <h1 class="setup-h1">${b.models}</h1>
        <p class="setup-lead">${ie.models}</p>
        ${t?.mem_total_gb==null?l:r`<p class="setup-device muted num" data-device-line>${y.deviceLine(t.mem_total_gb,t.mem_available_gb)}</p>`}
      </header>
      ${e.state===`loading`?c(4):l}
      ${e.state===`error`?u(e.endpoint,`${y.modelsError} ${e.message}`,()=>void this.loadList()):l}
      ${e.state===`empty`?s(y.modelsEmpty):l}
      ${e.state===`ready`?r`<div class="mtiles">
              ${e.data.models.map(e=>{let t=n?.model===e.id,i=t?this.at(`/${e.id}/${n.step===`models`?`overview`:n.step}`):this.at(`/${e.id}/overview`);return r`<a class="mtile ${e.recommended?`is-rec`:``}" href=${i} data-model=${e.id}>
                  <span class="mtile-top"><span class="mtile-family">${e.family}</span>${e.recommended?r`<span class="sbadge sbadge-rec">${y.tileRecommended}</span>`:l}</span>
                  <span class="mtile-name">${e.name}</span>
                  <span class="mtile-params">${y.tileParams(e.params)}</span>
                  <span class="mtile-pitch">${e.pitch}</span>
                  ${e.community_count?r`<span class="mtile-comm">${y.community.tileCount(e.community_count)}</span>`:l}
                  ${e.best_speed?r`<span class="mtile-speed" title=${y.tileSpeedTitle}><span class="mtile-speed-n num">${e.best_speed.tok_s.toFixed(1)}</span><span class="mtile-speed-u">tok/s</span><span class="mtile-speed-note">${e.best_speed.task}, ${e.best_speed.profile}</span></span>`:l}
                  <span class="mtile-go"><span class="btn btn-primary">${t?`${y.tileOpen}: ${x[n.step]}`:y.tileOpen}</span></span>
                </a>`})}
            </div>
            ${e.data.errors.length?r`<div class="setup-load-errors" role="status"><p>${y.modelLoadErrors(e.data.errors.length)}</p><ul>${e.data.errors.map(e=>r`<li><code>${e.path}</code> <span class="muted">${e.message}</span></li>`)}</ul></div>`:l}`:l}
      <section class="guide" aria-labelledby="guide-h">
        <h2 class="guide-h" id="guide-h">${y.guideSteps}</h2>
        <ol class="guide-list">${E.slice(1).map(e=>r`<li><span class="guide-t">${x[e]}</span><span class="guide-d">${ne[e]}</span></li>`)}</ol>
      </section>
    </section>`}renderModelArea(){let e=this.model;if(this.wiz.step===`install`&&this.wiz.job&&e?.state!==`ready`)return r`<section class="setup-step" data-step="install" aria-labelledby="setup-step-title">
        <header class="step-head"><h1 class="step-title" id="setup-step-title" tabindex="-1">${b.install}</h1></header>
        ${this.stepInstall()}
      </section>`;if(!e||e.state===`loading`)return c(8);if(e.state===`error`)return u(e.endpoint,e.message,()=>void this.loadModel(this.path[0]));if(e.state!==`ready`||!this.wiz.selection)return c(8);let t=e.data,n=this.wiz.selection,i=this.currentEstimate(),a=this.wiz.step,o=i&&!i.fits?Ue(t,n,i):null;return r`
      <div class="setup-sticky">
        <qse-memory-bar .model=${t} .estimate=${i} .available=${this.device?.mem_available_gb??null} .local=${this.estLocal} .overText=${o}></qse-memory-bar>
        ${this.estLocal?r`<p class="setup-local" role="status" data-local-estimate>${y.memLocal}</p>`:l}
      </div>
      ${this.renderRail(t)}
      <section class="setup-step" data-step=${a} aria-labelledby="setup-step-title">
        <header class="step-head">
          <p class="step-kicker muted num">${y.stepOf(O(a)+1,E.length)}</p>
          <h1 class="step-title" id="setup-step-title" tabindex="-1">${b[a]}</h1>
          <p class="step-intro">${ie[a]}</p>
        </header>
        ${this.renderStep(t,n,i)}
      </section>
      ${this.renderNav()}
    `}renderRail(e){let t=this.wiz.step,n=this.issues(),a=O(t);return r`<nav class="rail-steps" aria-label="Setup steps">
      <div class="rail-phone" aria-hidden="true">
        <span class="num">${y.stepOf(a+1,E.length)}</span><span class="rail-phone-label">${b[t]}</span>
        <span class="rail-phone-bar"><span style="width:${(a+1)/E.length*100}%"></span></span>
      </div>
      <ol class="steps">
        ${E.map((o,s)=>{let c=this.wiz.visited.includes(o),u=c&&(n[o]?.length??0)>0,d=o===t,f=c&&!u&&s<a,p=o!==`install`||!!this.wiz.job,m=o===`models`?this.at():this.at(`/${e.id}/${o}`);return r`<li class="steps-item ${d?`is-current`:``} ${f?`is-done`:``} ${u?`is-bad`:``} ${c?`is-visited`:``}" data-rail=${o}>
            ${p?r`<a href=${m} aria-current=${d?`step`:l} title=${u?n[o].join(` `):b[o]}>
                  <span class="steps-node num" aria-hidden="true">${u?`!`:f?i(`check`):s+1}</span>
                  <span class="steps-label">${x[o]}</span>
                  ${u?r`<span class="sr-only">(needs a fix)</span>`:l}
                </a>`:r`<span class="steps-link is-locked" aria-disabled="true"><span class="steps-node num" aria-hidden="true">${s+1}</span><span class="steps-label">${x[o]}</span></span>`}
          </li>`})}
      </ol>
    </nav>`}renderNav(){let e=this.wiz.step;if(e===`install`)return l;let t=O(e),n=this.nextRefused?this.nextBlocker():null;return r`<nav class="setup-nav ${n?`has-why`:``}" aria-label="Step navigation">
      ${n?r`<p class="setup-nav-why" id="setup-nav-why" role="alert" data-next-refused>${i(`info`)} <span>${y.nextRefused(n)}</span></p>`:l}
      <button class="btn setup-prev" @click=${()=>t<=1?this.dispatch({type:`tiles`}):this.dispatch({type:`previous`})}>${y.previous}</button>
      <span class="setup-nav-mid muted small num">${y.stepOf(t+1,E.length)}</span>
      ${e===`review`?r`<button class="btn btn-primary setup-finish" ?disabled=${this.finishBlockers().length>0||this.plan?.state!==`ready`} @click=${()=>this.confirmOpen=!0}>${y.finish}</button>`:r`<button class="btn btn-primary setup-next" aria-describedby=${n?`setup-nav-why`:l} @click=${()=>this.next()}><span>${y.next}<span class="setup-next-to">: ${x[E[t+1]]}</span></span></button>`}
    </nav>`}nextBlocker(){let e=this.m,t=this.wiz.selection;if(this.wiz.step!==`parallelism`||!e||!t)return null;let n=Pe(Ke(e,t),this.slotsParse(e));if(n)return n;let r=this.winParse(e,t,`window`);return r&&!r.ok?`${y.window}: ${r.why}`:null}winParse(e,t,n){let r=this.winDraft[n];return r==null?null:at(r,$e(e,t,n))}slotsParse(e){return this.slotsDraft==null?null:We(this.slotsDraft,Re(e))}next(){if(this.nextBlocker()){this.nextRefused=!0,this.querySelector(`[data-field^="parallelism."].is-bad input`)?.focus();return}this.nextRefused=!1,this.dispatch({type:`next`})}renderStep(e,t,n){switch(this.wiz.step){case`overview`:return this.stepOverview(e,t);case`weights`:return this.stepWeights(e,t);case`components`:return this.stepComponents(e,t);case`parallelism`:return this.stepParallel(e,t,n);case`methods`:return this.stepMethods(e,t);case`review`:return this.stepReview(e,t,n);case`install`:return this.stepInstall();default:return r``}}stepOverview(e,t){let n=e.profiles.find(e=>e.id===t.profile),a=Ze(e,t.profile);return r`<div class="ov">
      <div class="ov-facts">
        <p class="ov-arch">${e.architecture}</p>
        <dl class="ov-dl">
          <div><dt>${y.colWeights}</dt><dd>${e.profiles.length} sizes, ${e.profiles.map(e=>e.label.replace(/ bpw$/,``)).join(`, `)}</dd></div>
          <div><dt>${y.window}</dt><dd class="num">${n?.max_window===null?y.windowNone(n.label):`${a.toLocaleString(`en-US`)} ${y.tokensUnit}`}</dd></div>
          <div><dt>${y.license}</dt><dd>${e.license}</dd></div>
        </dl>
        <ul class="ov-links">${e.links.map(e=>r`<li><a href=${e.url} target="_blank" rel="noopener noreferrer">${e.label} ${i(`external`)}</a></li>`)}</ul>
      </div>
      <section class="panel ov-table" aria-label="Measured speed">
        <qse-measure-table .model=${e} .selected=${t.profile}></qse-measure-table>
      </section>
      ${e.parallel?.rows.length?r`<section class="panel ov-table" aria-label="Speed on this build"><qse-parallel-table .model=${e} .selected=${t.profile}></qse-parallel-table></section>`:l}
    </div>`}stepWeights(e,t){let n=this.device,a=Ve(e,t,n),o=e.measurements.rows,s=Math.max(...o.filter(e=>e.comparable).map(e=>e.cells.greedy_512?.code?.tok_s??0));return r`
      ${a?r`<div class="banner banner-offline" role="alert" data-token-blocked>${i(`warn`)} ${y.privateNoToken}</div>`:l}
      ${!a&&n?.hf_token_present&&e.profiles.some(e=>e.hf_private&&!n.profiles_on_disk?.[e.id])?r`<p class="wnote muted small">${i(`info`)} ${y.privateRepo}</p>`:l}
      <div class="wcards" role="radiogroup" aria-label=${b.weights}>
        ${Me(e).map(a=>{let c=t.profile===a.id,u=o.find(e=>e.profile===a.id),d=u?.cells.greedy_512,f=n?.profiles_on_disk?.[a.id],p=n?.active_profile===a.id,m=a.quality.top1_pct;return r`<label class="wcard ${c?`is-selected`:``} ${a.recommended?`is-rec`:``}" data-profile-card=${a.id} data-field=${c?`weights.profile`:l}>
            <input class="sr-only" type="radio" name="profile" .checked=${c} value=${a.id} @change=${()=>this.user(`weights`,`profile`,{type:`profile`,id:a.id})} />
            <span class="wcard-head">
              <span class="wcard-radio" aria-hidden="true"></span>
              <span class="wcard-name">${a.label}</span>
              ${p?r`<span class="sbadge sbadge-now">${y.activeNow}</span>`:l}
            </span>
            ${a.badges.length?r`<span class="wcard-badges">${a.badges.map(e=>r`<span class="sbadge ${e===`recommended`?`sbadge-rec`:``}">${e===`recommended`?y.recommended:e}</span>`)}</span>`:l}
            <span class="wcard-meters">
              <span class="wmeter" title=${a.quality.source}>
                <span class="wmeter-k">${y.weightsTop1}</span>
                <span class="wmeter-v num">${m==null?`–`:`${m.toFixed(1)} %`}</span>
                <span class="wmeter-bar q"><span style="width:${m==null?0:Math.max(4,(m-80)/20*100)}%"></span></span>
              </span>
              <span class="wmeter">
                <span class="wmeter-k">${y.weightsSpeed}</span>
                <span class="wmeter-v num">${d?.prose&&d?.code?r`${d.prose.tok_s.toFixed(1)} / ${d.code.tok_s.toFixed(1)}`:r`<span class="muted">${u&&!u.comparable?y.notComparable:y.notMeasured}</span>`}</span>
                <span class="wmeter-bar s"><span style="width:${d?.code&&u?.comparable?d.code.tok_s/s*100:0}%"></span></span>
              </span>
            </span>
            <span class="wcard-facts num">
              <span><span class="wcard-k">${y.weightsKl}</span> ${a.quality.kl_vs_bf16==null?`–`:a.quality.kl_vs_bf16.toFixed(4)}</span>
              <span><span class="wcard-k">${y.weightsMemory}</span> ${a.weights_gib.toFixed(1)} GiB</span>
              <span><span class="wcard-k">${y.weightsDisk}</span> ${f?y.onDisk:y.toDownload(a.disk_gb)}</span>
              ${L(e,a.id)?r`<span data-parallel-line><span class="wcard-k">${y.weightsAtOnce}</span> ${L(e,a.id)}</span>`:l}
            </span>
            ${this.renderSource(a)}
            ${a.note?r`<span class="wcard-note">${a.note}</span>`:l}
            ${a.hf_private&&!f&&n?.hf_token_present===!1?r`<span class="wcard-private is-blocked" data-private>${i(`warn`)} ${y.privateNoTokenShort}</span>`:l}
          </label>`})}
      </div>
      ${Ee(e).length||e.community?r`<qse-community .model=${e} .sync=${e.community_sync??null} .selected=${t.profile} .device=${n} .syncing=${this.communitySyncing} .onUse=${e=>this.user(`weights`,`profile`,{type:`profile`,id:e})} .onRefresh=${()=>void this.syncCommunity()}></qse-community>`:l}
      <qse-installed-weights @weights-removed=${()=>void this.loadDevice()}></qse-installed-weights>`}renderSource(e){let t=je(e);return r`<span class="wcard-src" data-weights-src=${t.kind}>
      <span class="sbadge wsrc-kind wsrc-${t.kind}">${t.label}</span>
      <span class="wsrc-by">${y.madeBy} ${t.byUrl?r`<a href=${t.byUrl} target="_blank" rel="noopener noreferrer" title=${y.community.github(t.by.slice(1))}>${t.by}</a>`:t.by}</span>
      ${t.repoUrl?r`<a class="wsrc-repo" href=${t.repoUrl} target="_blank" rel="noopener noreferrer" title=${y.community.onHf(t.repo)}>${t.repo} ${i(`external`)}</a>`:r`<span class="wsrc-repo">${t.repo}</span>`}
    </span>`}stepComponents(e,t){let n=fe(e,t.components);n.vision&&ye(e,t.profile)&&(n.vision={disabled:!0,reason:y.community.visionOff,required:!1});let a=[...new Set(e.components.map(e=>e.group))],o=k(e,t.profile);return r`
      <div class="step-tools">
        <button class="btn btn-sm" @click=${()=>this.dispatch({type:`recommended`,scope:`components`})}>${i(`refresh`)} ${y.resetToRecommended}</button>
      </div>
      <p class="comp-notice" role="status" aria-live="polite" data-cascade>${this.wiz.notice??``}</p>
      ${a.map(a=>r`<section class="comp-group" aria-labelledby=${`cg-${a}`}>
          <h2 class="comp-group-title" id=${`cg-${a}`}>${y.groups[a]??a}</h2>
          <ul class="comp-list">
            ${e.components.filter(e=>e.group===a).map(e=>{let a=de(e,t.components),s=n[e.id],c=e.id===`drafters`&&o.drafters_gib!=null?o.drafters_gib:e.memory_gib;return r`<li>
                  <label class="comp ${a?`is-on`:``} ${s.disabled?`is-disabled`:``} ${s.required?`is-required`:``}" data-field=${`components.${e.id}`} data-component=${e.id}>
                    <input type="checkbox" class="comp-input" .checked=${a} ?disabled=${s.disabled} aria-describedby=${`cw-${e.id}${s.reason?` cr-${e.id}`:``}`} @change=${t=>this.user(`components`,e.id,{type:`component`,id:e.id,on:t.target.checked})} />
                    <span class="comp-box" aria-hidden="true">${i(`check`)}</span>
                    <span class="comp-main">
                      <span class="comp-label">${e.label}${e.default&&!e.required?r` <span class="comp-rec">${y.recommendedValue}</span>`:l}</span>
                      <span class="comp-what" id=${`cw-${e.id}`}>${e.what}</span>
                      ${s.reason?r`<span class="comp-reason" id=${`cr-${e.id}`} data-reason>${s.reason}</span>`:l}
                    </span>
                    <span class="comp-cost num">
                      <span class="comp-mem">${y.memoryCost(c)}</span>
                      ${e.memory_max_gib==null?l:r`<span class="muted">${y.memoryUpTo(e.memory_max_gib)}</span>`}
                      ${e.disk_gb>0?r`<span class="muted">${y.diskCost(e.disk_gb)}</span>`:l}
                    </span>
                  </label>
                </li>`})}
          </ul>
        </section>`)}`}stepParallel(e,t,n){let a=e.parallelism,o=n?.context_tokens_fit??0,s=Math.floor(o/t.slots),c=(e,t)=>this.user(`parallelism`,e,{type:`parallel`,key:e,value:t});return r`<div class="spar">
      <div class="spar-fields">
        ${this.slotsField(e,t)}
        ${this.windowField(e,t,`window`)}
        <div class="pfield" data-field="parallelism.kv_pool">
          <div class="pfield-k"><span class="pfield-label" id="pf-pool">${y.kvPool}</span><span class="pfield-what">${y.kvPoolWhat}</span></div>
          <label class="switch"><input type="checkbox" role="switch" .checked=${t.kv_pool} aria-labelledby="pf-pool" @change=${e=>c(`kv_pool`,e.target.checked)} /><span>${t.kv_pool?`on`:`off`}</span></label>
        </div>
        <div class="pfield" data-field="parallelism.floor_gb">
          <div class="pfield-k"><span class="pfield-label" id="pf-floor">${y.floor}</span><span class="pfield-what">${y.floorWhat}</span></div>
          <div class="floor-ctl">
            <input type="range" min=${a.floor_gb.min} max=${a.floor_gb.max} step="1" .value=${String(t.floor_gb)} aria-labelledby="pf-floor" @input=${e=>c(`floor_gb`,Number(e.target.value))} />
            <output class="num">${t.floor_gb} GiB</output>
          </div>
        </div>
        <div class="step-tools"><button class="btn btn-sm" @click=${()=>this.dispatch({type:`recommended`,scope:`parallelism`})}>${i(`refresh`)} ${y.resetToRecommended}</button></div>
        ${this.wiz.notice?r`<p class="comp-notice" role="status">${this.wiz.notice}</p>`:l}
      </div>
      <aside class="spar-readout panel" aria-live="polite" data-fit-tokens=${o}>
        <p class="spar-big num">${o.toLocaleString(`en-US`)}</p>
        <p class="spar-big-u">${y.fitsUnit}</p>
        <div class="spar-slots" aria-hidden="true">
          ${[{n:t.slots>1?`1–${t.slots}`:`1`,w:t.window,on:!0}].map(e=>r`<div class="spar-slot ${e.on?``:`is-off`}">
              <span class="spar-slot-n num">${e.n}</span>
              <span class="spar-slot-track"><span class="spar-slot-win" style="width:${e.w/a.window.max*100}%"></span><span class="spar-slot-fit" style="width:${Math.min(e.w,s)/a.window.max*100}%"></span></span>
              <span class="spar-slot-k num">${tt(e.w)}</span>
            </div>`)}
        </div>
        <p class="small">${y.fitsPerSlot(s,t.slots)}</p>
        ${n&&t.slots>1?r`<p class="small" data-lanes-full=${n.lanes_full}>${y.lanesFull(n.lanes_full,t.slots,t.window,n.lane_full_gb,t.kv_pool)}</p>`:l}
        ${n?r`<p class="small muted">${y.worstCase(n.kv_worst_gb,n.fits_worst??!1)}</p>`:l}
      </aside>
    </div>`}slotsField(e,t){let n=Re(e),i=Ke(e,t),a=this.slotsParse(e),o=a&&!a.ok?a.why:null,s=o??(i.over?y.slotsOver(i.n,i.overGb,i.fitMax):null),c=e=>this.user(`parallelism`,`slots`,{type:`parallel`,key:`slots`,value:e}),u=e=>{let r=a?.ok?a.n:t.slots,i=Fe(r,e,n);i===t.slots?this.slotsDraft=null:c(i)},d=e=>{let r=e.target.value,i=We(r,n);i.ok?(this.slotsDraft=null,i.n!==t.slots&&c(i.n)):this.slotsDraft=r},f=e=>{(e.key===`ArrowUp`||e.key===`ArrowDown`)&&(e.preventDefault(),u(e.key===`ArrowUp`?1:-1))},p=this.slotsDraft??String(t.slots),m=t.slots>=n.max&&!o;return r`<div class="pfield pfield-slots ${s?`is-bad`:``}" data-field="parallelism.slots">
      <div class="pfield-k"><label class="pfield-label" id="pf-slots" for="pf-slots-in">${y.slots}</label><span class="pfield-what">${y.slotsWhat}</span></div>
      <div class="stepper ${s?`is-bad`:``}" role="group" aria-labelledby="pf-slots">
        <button class="stepper-btn" type="button" aria-label=${y.slotsFewer} aria-controls="pf-slots-in" ?disabled=${t.slots<=n.min&&!o} @click=${()=>u(-1)}>
          <svg viewBox="0 0 16 16" aria-hidden="true"><path d="M3.5 8h9" /></svg>
        </button>
        <input
          id="pf-slots-in"
          class="stepper-in num"
          type="text"
          inputmode="numeric"
          enterkeyhint="done"
          autocomplete="off"
          spellcheck="false"
          role="spinbutton"
          aria-valuemin=${n.min}
          aria-valuemax=${n.max}
          aria-valuenow=${a&&!a.ok?l:t.slots}
          aria-valuetext=${a&&!a.ok?l:`${t.slots} ${t.slots===1?`chat`:`chats`}`}
          aria-invalid=${s?`true`:`false`}
          aria-describedby="pf-slots-say"
          maxlength="3"
          .value=${p}
          @input=${d}
          @keydown=${f}
          @focus=${e=>e.target.select()}
        />
        <button class="stepper-btn" type="button" aria-label=${y.slotsMore} aria-controls="pf-slots-in" ?disabled=${m} title=${m?y.slotsAtMax(n.max):l} @click=${()=>u(1)}>
          <svg viewBox="0 0 16 16" aria-hidden="true"><path d="M3.5 8h9M8 3.5v9" /></svg>
        </button>
      </div>
      <div class="slots-say" id="pf-slots-say">
        ${s?r`<p class="slots-err" role="alert" data-slots-error>${s}</p>`:l}
        ${n.max>1?r`<p class="slots-cost" data-slots-cost>${y.slotsCost(i.cost.now,i.cost.full,i.cost.window)}</p>`:l}
        ${i.untested&&!o?r`<p class="slots-note" data-slots-untested>${y.slotsUntested(n.measured)}</p>`:l}
        ${i.passes>1&&!o?r`<p class="slots-note" data-slots-passes>${y.slotsPasses(i.passes)}</p>`:l}
        ${e.parallelism.slots.engine_max_reason?r`<p class="slots-note" data-slots-engine>${e.parallelism.slots.engine_max_reason}</p>`:l}
      </div>
    </div>`}windowField(e,t,n){let i=$e(e,t,n),a=t.window,o=y.window,s=`pf-win`,c=this.winParse(e,t,n),u=c&&!c.ok?c.why:null,d=c?.ok?c.n:a,f=e=>this.user(`parallelism`,n,{type:`parallel`,key:n,value:e}),p=e=>{this.winDraft={...this.winDraft,[n]:null},e!==a&&f(e)},m=e=>{let t=e.target.value;this.winDraft={...this.winDraft,[n]:t};let r=at(t,i);r.ok&&r.n!==a&&f(r.n)},h=e=>{let t=e.key;if(t===`ArrowUp`||t===`ArrowDown`)p(A(d,t===`ArrowUp`?1:-1,i));else if(t===`PageUp`||t===`PageDown`){let e=t===`PageUp`?1:-1;p(A(A(d,e,i),e,i))}else if(t===`Home`)p(i.min);else if(t===`End`)p(i.max);else if(t===`Enter`)e.target.blur();else return;e.preventDefault()},g=()=>{c?.ok&&(this.winDraft={...this.winDraft,[n]:null})},_=this.winDraft[n]??a.toLocaleString(`en-US`),ee=d<=i.min&&!u,v=d>=i.max&&!u;return r`<div class="pfield pfield-win ${u?`is-bad`:``}" data-field=${`parallelism.${n}`}>
      <div class="pfield-k"><label class="pfield-label" id=${s} for=${`${s}-in`}>${o}</label><span class="pfield-what">${y.windowWhat}</span></div>
      <div class="win-ctl">
        <div class="stepper stepper-wide ${u?`is-bad`:``}" role="group" aria-labelledby=${s}>
          <button class="stepper-btn" type="button" aria-label=${y.windowShorter(o)} aria-controls=${`${s}-in`} ?disabled=${ee} @click=${()=>p(A(d,-1,i))}>
            <svg viewBox="0 0 16 16" aria-hidden="true"><path d="M3.5 8h9" /></svg>
          </button>
          <input
            id=${`${s}-in`}
            class="stepper-in num"
            type="text"
            inputmode="numeric"
            enterkeyhint="done"
            autocomplete="off"
            spellcheck="false"
            role="spinbutton"
            aria-valuemin=${i.min}
            aria-valuemax=${i.max}
            aria-valuenow=${u?l:d}
            aria-valuetext=${u?l:`${d.toLocaleString(`en-US`)} ${y.tokensUnit}`}
            aria-invalid=${u?`true`:`false`}
            aria-describedby=${`${s}-say`}
            maxlength="12"
            .value=${_}
            @input=${m}
            @keydown=${h}
            @blur=${g}
            @focus=${e=>e.target.select()}
          />
          <button class="stepper-btn" type="button" aria-label=${y.windowLonger(o)} aria-controls=${`${s}-in`} ?disabled=${v} title=${v?i.maxWhy:l} @click=${()=>p(A(d,1,i))}>
            <svg viewBox="0 0 16 16" aria-hidden="true"><path d="M3.5 8h9M8 3.5v9" /></svg>
          </button>
        </div>
        <span class="stepper-hint num" aria-hidden="true" data-win-short>${u?``:tt(d)}</span>
      </div>
      <div class="slots-say" id=${`${s}-say`}>
        ${u?r`<p class="slots-err" role="alert" data-win-error>${u}</p>`:l}
        ${!u&&v&&i.max<e.parallelism[n].max?r`<p class="slots-note" data-win-max>${i.maxWhy}</p>`:l}
      </div>
    </div>`}fieldControl(e,t,n){let i=n=>this.user(`methods`,t.id,{type:`field`,group:e,id:t.id,value:n}),a=`mf-${t.id}`;if(t.type===`enum`){let e=t.options??[];return e.length<=3?r`<div class="seg seg-wrap" role="radiogroup" aria-labelledby=${`${a}-l`}>${e.map(e=>r`<button class="seg-btn ${n===e.value?`is-on`:``}" role="radio" aria-checked=${n===e.value} @click=${()=>i(e.value)}>${e.label}</button>`)}</div>`:r`<select id=${a} class="sel" @change=${e=>i(e.target.value)}>${e.map(e=>r`<option value=${e.value} ?selected=${n===e.value}>${e.label}</option>`)}</select>`}return t.type===`bool`?r`<label class="switch"><input id=${a} type="checkbox" role="switch" .checked=${n===!0} @change=${e=>i(e.target.checked)} /><span>${n?`on`:`off`}</span></label>`:t.type===`int`||t.type===`number`?r`<input id=${a} class="num-input num" type="number" inputmode=${t.type===`int`?`numeric`:`decimal`} min=${t.min??l} max=${t.max??l} step=${t.type===`int`?1:.05} .value=${String(n)} @change=${e=>{let t=e.target.value;i(t===``?NaN:Number(t))}} />`:r`<input id=${a} class="text-input" type="text" .value=${String(n)} spellcheck="false" autocapitalize="off" @change=${e=>i(e.target.value)} />`}fieldRow(e,t,n){let i=n[e][t.id]??t.recommended,a=D(t,i),o=t.options?.find(e=>e.value===t.recommended)?.label??String(t.recommended);return r`<div class="mfield ${a?`is-bad`:``}" data-field=${`methods.${t.id}`}>
      <div class="pfield-k">
        <label class="pfield-label" id=${`mf-${t.id}-l`} for=${`mf-${t.id}`}>${t.label}</label>
        <span class="pfield-what">${t.what}</span>
        <span class="mfield-rec muted small">${y.recommendedValue}: <span class="num">${o}</span>${i===t.recommended?l:r` <button class="linkish" @click=${()=>this.user(`methods`,t.id,{type:`field`,group:e,id:t.id,value:t.recommended})}>${y.resetToRecommended.toLowerCase()}</button>`}</span>
      </div>
      <div class="mfield-ctl">${this.fieldControl(e,t,i)}${a?r`<p class="mfield-err" role="alert">${y.invalidValue(a)}</p>`:l}</div>
    </div>`}stepMethods(e,t){let n=e.advanced.some(e=>D(e,t.advanced[e.id]??e.recommended));return r`<div class="methods">
      ${e.methods.map(e=>this.fieldRow(`methods`,e,t))}
      <details class="adv" ?open=${n}>
        <summary><span class="adv-title">${y.advanced}</span> <span class="muted small">${y.advancedNote}</span></summary>
        ${e.advanced.map(e=>this.fieldRow(`advanced`,e,t))}
      </details>
      <div class="step-tools"><button class="btn btn-sm" @click=${()=>this.dispatch({type:`recommended`,scope:`methods`})}>${i(`refresh`)} ${y.resetToRecommended}</button></div>
    </div>`}stepReview(e,t,n){let a=this.plan,o=this.wiz.step,s=n&&!n.fits?Be(e,t):[],u=this.finishBlockers(),d=this.device,f=k(e,t.profile),p=a?.state===`ready`?a.data:null;return r`<div class="review">
      <section class="rv-card rv-memory ${n&&!n.fits?`is-over`:`is-ok`}" data-review-memory>
        <h2 class="rv-h">${y.reviewMemory}</h2>
        ${n?r`<p class="rv-say">${n.fits?y.reviewFits(n.total_gb,n.budget_gb??n.device_gb-n.floor_gb):Ue(e,t,n)}</p>`:l}
        ${s.length?r`<h3 class="rv-sub">${y.howToFix}</h3>
              <ul class="rv-fixes">${s.map(t=>r`<li><span>${t.text}</span><span class="muted small num">−${t.saves.toFixed(1)} GiB</span><a class="btn btn-sm" href=${this.at(`/${e.id}/${t.step}`)}>${y.fixGo}: ${x[t.step]}</a></li>`)}</ul>`:l}
      </section>

      <section class="rv-card">
        <h2 class="rv-h">${y.reviewWeights}</h2>
        <p>${d?.active_profile===f.id?y.reviewWeightsSame(f.label):y.reviewWeightsSwitch(d?.active_profile?e.profiles.find(e=>e.id===d.active_profile)?.label??d.active_profile:null,f.label)}</p>
        ${p&&!p.profile.on_disk?r`<p class="muted">${y.reviewDownload(p.profile.download_gb)}</p>`:l}
        ${p?.downloads?.length?r`<ul class="rv-downloads" data-review-downloads>${p.downloads.map(e=>r`<li data-download=${e.id}><span>${e.label}</span> <span class="muted small">${e.for}</span> <span class="num">${e.gb.toFixed(2)} GB</span></li>`)}</ul>`:l}
        ${p?r`<p class="muted small">${p.restart?y.reviewRestart:y.reviewNoRestart}</p>`:l}
        ${d?.active_profile&&d.active_profile!==f.id&&d.profiles_on_disk?.[d.active_profile]?r`<label class="rv-remove-after" data-remove-after><input type="checkbox" .checked=${this.removeAfter} @change=${e=>this.removeAfter=e.target.checked} />
              <span>${y.removeAfter(e.profiles.find(e=>e.id===d.active_profile)?.label??d.active_profile)}</span></label>`:l}
      </section>

      <section class="rv-card rv-changes">
        <h2 class="rv-h">${y.reviewChanges}</h2>
        ${a==null||a.state===`loading`?c(4):l}
        ${a?.state===`error`?r`<p class="rv-warn" role="alert">${i(`warn`)} ${y.planError} <span class="muted">${a.message}</span> <button class="btn btn-sm" @click=${()=>void this.loadPlan()}>${y.retry}</button></p>`:l}
        ${p?p.changes.length?r`<div class="table-scroll"><table class="table rv-table">
                <thead><tr><th scope="col">${y.reviewKey}</th><th scope="col">${y.reviewNow}</th><th scope="col">${y.reviewAfter}</th><th scope="col">${y.reviewFrom}</th></tr></thead>
                <tbody>${p.changes.map(e=>r`<tr data-env=${e.key}><th scope="row"><code>${e.key}</code></th><td class="rv-old">${e.current??r`<span class="muted">${y.reviewUnset}</span>`}</td><td class="rv-new">${e.removed?r`<span class="rv-removed" data-removed>${rt(e,y.reviewUnset).to}</span>`:e.next??r`<span class="muted">${y.reviewUnset}</span>`}</td><td class="muted small">${e.from}</td></tr>`)}</tbody>
              </table></div>`:r`<p class="muted">${y.reviewNoChanges}</p>`:l}
        ${p&&(p.errors.length||p.warnings.length)?r`<ul class="rv-notes">
              ${p.errors.map((t,n)=>{let a=p.error_codes?.[n],s=Le(a);return r`<li class="is-err" data-error-code=${a??l}>${i(`warn`)} <span>${t}</span>${s&&s!==o?r` <a class="btn btn-sm" href=${this.at(`/${e.id}/${s}`)}>${y.planFix}: ${x[s]}</a>`:l}</li>`})}
              ${p.warnings.map(e=>r`<li>${i(`info`)} ${e}</li>`)}
            </ul>`:l}
      </section>

      ${p?.commands.length?r`<section class="rv-card">
            <h2 class="rv-h">${y.reviewCommands}</h2>
            <p class="muted small">${y.reviewCommandsNote}</p>
            <pre class="rv-cmds">${p.commands.join(`
`)}</pre>
          </section>`:l}

      ${d&&!d.password_set?this.securityCard():d?.password_set?r`<p class="rv-secure muted">${i(`check`)} ${y.securitySet}</p>`:l}

      ${u.length?r`<ul class="rv-blockers" role="status" data-finish-blocked>${u.map(e=>r`<li>${i(`warn`)} ${e}</li>`)}</ul>`:l}
      ${this.applyError?r`<p class="rv-warn" role="alert">${i(`warn`)} ${this.applyError}</p>`:l}
    </div>`}get sec(){return this.wiz.selection?.advanced.admin_password===`generate`?`generate`:`keep`}loginOff(){let e=this.m?.advanced.find(e=>e.id===`dashboard_login`);return!!e&&(this.wiz.selection?.advanced.dashboard_login??e.recommended)!==`on`}securityCard(){let e=(e,t,n)=>r`<label class="sec-opt ${this.sec===e?`is-on`:``}">
      <input type="radio" name="sec" .checked=${this.sec===e} @change=${()=>this.user(`methods`,`admin_password`,{type:`field`,group:`advanced`,id:`admin_password`,value:e})} />
      <span><span class="sec-opt-label">${t}</span>${n?r`<span class="muted small">${n}</span>`:l}</span>
    </label>`;return r`<section class="rv-card rv-security" data-security-warning role="note">
      <h2 class="rv-h">${i(`warn`)} ${y.securityTitle}</h2>
      <p>${y.securityBody}</p>
      <div class="sec-opts" role="radiogroup" aria-label=${y.securityChoice}>
        ${e(`keep`,y.securityKeep)} ${e(`generate`,y.securityGenerate,y.securityGenerateNote)}
      </div>
      ${this.sec===`generate`&&this.loginOff()?r`<p class="sec-login" data-login-off>${i(`info`)} <span>${y.securityLoginOff}</span>
            <button class="btn btn-sm" @click=${()=>this.user(`methods`,`dashboard_login`,{type:`field`,group:`advanced`,id:`dashboard_login`,value:`on`})}>${y.securityLoginOn}</button></p>`:l}
    </section>`}renderConfirm(e){let t=this.plan?.state!==`ready`||this.plan.data.restart;return r`<dialog
      class="setup-confirm"
      aria-labelledby="sc-t"
      aria-describedby="sc-b"
      @cancel=${e=>this.applying&&e.preventDefault()}
      @close=${()=>this.confirmOpen=!1}
    >
      ${this.confirmOpen?r`<h2 id="sc-t">${y.confirmTitle}</h2>
            <p id="sc-b">${y.confirmBody(t)}</p>
            <p class="muted small">${k(e,this.wiz.selection.profile).label}</p>
            ${this.applyError?r`<p class="rv-warn" role="alert">${this.applyError}</p>`:l}
            <div class="setup-confirm-foot">
              <button class="btn" ?disabled=${this.applying} @click=${()=>this.confirmOpen=!1}>${y.cancel}</button>
              <button class="btn btn-primary setup-confirm-go" autofocus ?disabled=${this.applying} @click=${()=>void this.finish()}>${this.applying?y.installStarting:y.confirmGo}</button>
            </div>`:l}
    </dialog>`}updated(){let e=this.querySelector(`dialog.setup-confirm`);e&&(this.confirmOpen&&!e.open?e.showModal():!this.confirmOpen&&e.open&&e.close())}stepInstall(){let e=this.wiz.job;return e?r`<qse-install-progress .jobId=${e} .initial=${this.jobInitial} .previousProfile=${this.previousProfile} .onBack=${()=>this.dispatch({type:`job`,id:null})} @job-retry=${e=>{this.jobInitial=e.detail,this.dispatch({type:`job`,id:e.detail.id})}}></qse-install-progress>`:r`<p class="muted">${y.installStarting}</p>`}};e([t({attribute:!1})],$.prototype,`path`,void 0),e([t({attribute:!1})],$.prototype,`node`,void 0),e([d()],$.prototype,`wiz`,void 0),e([d()],$.prototype,`storageProblem`,void 0),e([d()],$.prototype,`storageFailed`,void 0),e([d()],$.prototype,`list`,void 0),e([d()],$.prototype,`model`,void 0),e([d()],$.prototype,`device`,void 0),e([d()],$.prototype,`serverEst`,void 0),e([d()],$.prototype,`estLocal`,void 0),e([d()],$.prototype,`plan`,void 0),e([d()],$.prototype,`confirmOpen`,void 0),e([d()],$.prototype,`applying`,void 0),e([d()],$.prototype,`removeAfter`,void 0),e([d()],$.prototype,`applyError`,void 0),e([d()],$.prototype,`jobInitial`,void 0),e([d()],$.prototype,`previousProfile`,void 0),e([d()],$.prototype,`resetOpen`,void 0),e([d()],$.prototype,`unauthorized`,void 0),e([d()],$.prototype,`winNarrow`,void 0),e([d()],$.prototype,`slotsDraft`,void 0),e([d()],$.prototype,`winDraft`,void 0),e([d()],$.prototype,`nextRefused`,void 0),e([d()],$.prototype,`communitySyncing`,void 0),$=e([n(`qse-setup`)],$);export{$ as QseSetup};