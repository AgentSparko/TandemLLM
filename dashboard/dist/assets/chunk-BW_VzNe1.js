import{_ as e,d as t,f as n,h as r,i,l as a,m as o,n as s,o as c,p as l,r as u,u as d,y as f}from"./chunk-DggE2y7K.js";import{C as p,D as m,E as h,N as g,P as _,S as ee,T as v,a as te,b as ne,c as re,d as ie,f as ae,g as oe,h as se,i as y,j as b,l as ce,m as le,n as x,p as ue,r as S,s as C,t as de,u as fe,v as pe,w as me,y as he}from"./chunk-ChNOK3qG.js";import{a as ge,n as _e}from"./chunk-BXmDkkzu.js";import{i as w,t as T}from"./chunk-CI4nmvus.js";import{n as ve}from"./chunk-C139jKKa.js";import{t as ye}from"./chunk-D26K_GyM.js";import{S as E,b as D,kt as be,u as xe,v as O,x as Se,y as k}from"./app-CYzeKYJB.js";import{_ as Ce,a as we,c as Te,d as Ee,f as De,g as Oe,h as A,i as ke,l as Ae,m as je,n as Me,o as j,p as Ne,r as Pe,s as Fe,t as Ie,u as Le,v as Re}from"./chunk-x6NBVcxA.js";import{n as ze,t as Be}from"./chunk-C9q5Vlnk.js";import{a as Ve,c as He,d as Ue,f as We,i as Ge,l as Ke,n as qe,o as Je,r as M,s as Ye,t as Xe,u as Ze}from"./chunk-BxJia3HY.js";var N=[`1`,`2`,`4`,`8`],P=[`2048`,`8192`,`32768`];function Qe(e){let t=e.parallel?.rows??[],n=t=>{let n=e.profiles.findIndex(e=>e.id===t.profile);return n<0?e.profiles.length:n};return[...t].sort((e,t)=>n(e)-n(t)||e.profile.localeCompare(t.profile))}function $e(e,t){return e?.parallel?.rows.find(e=>e.profile===t)??null}function et(e,t){let n=e.profiles.find(e=>e.id===t.profile);return n?n.label:(e.community??[]).find(e=>e.profile===t.profile||`community/${e.id}`===t.profile)?.name??t.profile}function F(e,t){let n=e.levels[t];return n&&n.at_once===Number(t)?n.wall_tok_s:null}function tt(e){let t=e.setup,n=t.sampling;return O.parallelSetup(t.lanes,t.window,t.max_tokens,`T ${n.temperature}, top-k ${n.top_k}, top-p ${n.top_p}`,t.suffix_store,t.response_cache,e.rounds,t.prompts.set)}var I=e=>e==null?`–`:e.toFixed(1),nt=e=>e==null?`–`:e.toFixed(2),L=class extends d{constructor(...e){super(...e),this.model=null,this.selected=null}render(){let t=this.model,n=t?Qe(t):[];return!t||!n.length?o:e`<div class="mt pt">
      <h3 class="pt-title">${O.parallelTitle}</h3>
      <div class="table-scroll mt-scroll">
        <table class="table mt-table pt-table">
          <thead>
            <tr>
              <th scope="col" class="mt-name" rowspan="2">${O.colWeights}</th>
              <th scope="colgroup" colspan="2">${O.parallelDecode}<span class="mt-unit">${O.tokPerS}</span></th>
              <th scope="colgroup" colspan=${P.length}>${O.parallelPrefill}<span class="mt-unit">${O.seconds}</span></th>
              <th scope="colgroup" colspan=${N.length}>${O.parallelAtOnce}<span class="mt-unit">${O.tokPerS}</span></th>
            </tr>
            <tr>
              <th scope="col" class="num-col">${O.colCode}</th>
              <th scope="col" class="num-col">${O.colProse}</th>
              ${P.map(t=>e`<th scope="col" class="num-col">${Number(t)/1024}k</th>`)}
              ${N.map(t=>e`<th scope="col" class="num-col">${t}</th>`)}
            </tr>
          </thead>
          <tbody>${n.map(e=>this.row(t,e))}</tbody>
        </table>
      </div>
      <div class="mt-foot muted small">${O.parallelFoot}
        <details class="mt-method"><summary>${O.howMeasured}</summary><p>${t.parallel?.method??``}</p>
          <ul class="pt-setups">${n.map(n=>e`<li data-setup=${n.profile}><strong>${et(t,n)}</strong>: ${O.parallelBuild(n.build,n.commit,n.date)} ${tt(n)}${n.note?e` ${n.note}`:o}</li>`)}</ul>
        </details>
      </div>
    </div>`}row(t,n){let r=n.profile===this.selected;return e`<tr class="mt-row ${r?`is-selected`:``}" data-parallel=${n.profile} aria-current=${r?`true`:o}>
      <th scope="row" class="mt-name"><span class="mt-label">${et(t,n)}</span>${r?e`<span class="mt-mine">${O.selectedRow}</span>`:o}</th>
      <td class="num-col num">${I(n.decode?.code)}</td>
      <td class="num-col num">${I(n.decode?.prose)}</td>
      ${P.map(t=>{let r=n.prefill?.[t];return r?e`<td class="num-col num" title=${O.parallelPrefillTitle(r.prompt_tokens,r.tok_s)}>${nt(r.ttft_s)}</td>`:e`<td class="num-col mt-na">–</td>`})}
      ${N.map(t=>{let r=n.levels[t],i=F(n,t);return i==null||!r?e`<td class="num-col mt-na">–</td>`:e`<td class="num-col num" title=${O.parallelLevelTitle(Number(t),r.decode_tok_s,r.ttft_s)}>${I(i)}</td>`})}
    </tr>`}};a([n({attribute:!1})],L.prototype,`model`,void 0),a([n({attribute:!1})],L.prototype,`selected`,void 0),L=a([l(`qse-parallel-table`)],L);function R(e,t){let n=$e(e,t);if(!n)return null;let r=F(n,`1`),i=[`8`,`4`,`2`].find(e=>F(n,e)!=null);return i?O.parallelCard(r,Number(i),F(n,i)):r==null?null:O.parallelCard(r,1,r)}var z=O.community,rt={text:f`<path d="M4 5.5h12M4 9.5h12M4 13.5h7" fill="none" stroke="currentColor" stroke-width="1.7" stroke-linecap="round"/>`,mic:f`<rect x="7.25" y="3" width="5.5" height="9" rx="2.75" fill="none" stroke="currentColor" stroke-width="1.6"/><path d="M4.5 10a5.5 5.5 0 0 0 11 0M10 15.5v2M7.5 17.5h5" fill="none" stroke="currentColor" stroke-width="1.6" stroke-linecap="round"/>`,video:f`<rect x="2.5" y="5" width="10.5" height="10" rx="1.5" fill="none" stroke="currentColor" stroke-width="1.6"/><path d="m13 9 4.5-2.5v7L13 11z" fill="none" stroke="currentColor" stroke-width="1.6" stroke-linejoin="round"/>`,drafters:f`<circle cx="4.5" cy="10" r="1.8" fill="currentColor"/><circle cx="10" cy="10" r="1.8" fill="currentColor" opacity=".7"/><circle cx="15.5" cy="10" r="1.8" fill="currentColor" opacity=".45"/><path d="M6.3 10h1.9M11.8 10h1.9" stroke="currentColor" stroke-width="1.4"/>`,x:f`<path d="M4 3.5h3.6l8.4 13H12.4z" fill="none" stroke="currentColor" stroke-width="1.6" stroke-linejoin="round"/><path d="M15.5 3.5 4.5 16.5" stroke="currentColor" stroke-width="1.6" stroke-linecap="round"/>`,huggingface:f`<circle cx="10" cy="10.5" r="6.5" fill="none" stroke="currentColor" stroke-width="1.6"/><circle cx="7.6" cy="9" r="1" fill="currentColor"/><circle cx="12.4" cy="9" r="1" fill="currentColor"/><path d="M7 12.2c.8 1.2 1.8 1.8 3 1.8s2.2-.6 3-1.8" fill="none" stroke="currentColor" stroke-width="1.6" stroke-linecap="round"/><path d="M4.2 7.2 3 6.2M15.8 7.2 17 6.2" stroke="currentColor" stroke-width="1.6" stroke-linecap="round"/>`,bluesky:f`<path d="M10 8.6C9 6.8 6.8 4.2 4.6 3.7 3.3 3.5 2.8 4.3 2.8 5.4c0 1.6.6 4.3 1.9 5 .9.4 2 .2 2.7 0-2 .5-2.9 1.4-2.1 2.9.9 1.8 3.2 2.5 4.7-.4 1.5 2.9 3.8 2.2 4.7.4.8-1.5-.1-2.4-2.1-2.9.7.2 1.8.4 2.7 0 1.3-.7 1.9-3.4 1.9-5 0-1.1-.5-1.9-1.8-1.7C13.2 4.2 11 6.8 10 8.6Z" fill="currentColor"/>`,mastodon:f`<path d="M4 15.5c-.9-1-1.5-2.6-1.5-5V8.3C2.5 5 4.6 3.5 10 3.5s7.5 1.5 7.5 4.8v1.5c0 2.6-1.4 3.9-4.2 4.1-1.9.2-3.5 0-4.3-.3v.9c0 1.4 2.3 1.5 5 1" fill="none" stroke="currentColor" stroke-width="1.6" stroke-linecap="round" stroke-linejoin="round"/><path d="M6.3 11.8V8.4c0-1.1.6-1.7 1.6-1.7s1.6.6 1.6 1.7v1.9M13.7 11.8V8.4c0-1.1-.6-1.7-1.6-1.7s-1.6.6-1.6 1.7v1.9" fill="none" stroke="currentColor" stroke-width="1.6" stroke-linecap="round"/>`,website:f`<circle cx="10" cy="10" r="7" fill="none" stroke="currentColor" stroke-width="1.6"/><path d="M3 10h14M10 3c2.2 2.1 3.3 4.4 3.3 7s-1.1 4.9-3.3 7c-2.2-2.1-3.3-4.4-3.3-7S7.8 5.1 10 3Z" fill="none" stroke="currentColor" stroke-width="1.4"/>`},B=(t,n=16)=>e`<svg class="icon" viewBox="0 0 20 20" aria-hidden="true" width=${n} height=${n}>${rt[t]}</svg>`,it=e=>e===`text`?B(`text`):e===`image`?i(`image`):B(e===`audio`?`mic`:`video`),at=e=>e.toUpperCase(),V=class extends d{constructor(...e){super(...e),this.model=null,this.sync=null,this.selected=null,this.device=null,this.syncing=!1,this.onUse=()=>{},this.onRefresh=()=>{},this.query=``,this.sort=`newest`,this.droppedOpen=!1,this.noAvatar=new Set}render(){let t=this.model;if(!t)return o;let n=ue(t),r=this.sync;if(!n.length&&!r)return o;let a=p(ee(n,this.query),this.sort),s=ce,c=oe(r?.add_url)??s;return e`<section class="cw" aria-labelledby="cw-h" data-community data-syncing=${this.syncing?``:o}>
      <header class="cw-head">
        <div class="cw-title">
          <h2 class="cw-h" id="cw-h">${z.heading}</h2>
          ${this.renderSource()}
        </div>
        <p class="cw-intro">${z.intro} <a href=${s} target="_blank" rel="noopener noreferrer">${z.addYoursHow} ${i(`external`)}</a></p>
        ${n.length?e`<div class="cw-tools">
              <label class="cw-search">
                ${i(`search`)}
                <input type="search" placeholder=${z.search} aria-label=${z.searchLabel} .value=${this.query} autocomplete="off" autocapitalize="off" spellcheck="false" @input=${e=>this.query=e.target.value} />
              </label>
              <label class="cw-sort"><span class="muted small">Sort</span>
                <select class="sel" aria-label=${z.sortLabel} @change=${e=>this.setSort(e.target.value)}>${fe.map(t=>e`<option value=${t} ?selected=${this.sort===t}>${ie[t]}</option>`)}</select>
              </label>
              <span class="cw-count num muted small" data-count>${se(a.length,n.length)}</span>
              <a class="btn btn-sm cw-add" href=${c} target="_blank" rel="noopener noreferrer">${i(`plus`)} ${z.addYours}</a>
            </div>`:o}
      </header>
      ${r?.dropped.length?this.renderDropped(r.dropped):o}
      ${n.length?a.length?e`<div class="cw-grid" role="list">${a.map(e=>this.renderCard(e))}</div>`:e`<div class="cw-empty" data-community-nomatch role="status"><p class="cw-empty-t">${z.noMatch(this.query.trim())}</p><p><button class="btn btn-sm" @click=${()=>this.query=``}>${i(`x`)} ${z.clearSearch}</button></p></div>`:e`<div class="cw-empty" data-community-empty>
            <p class="cw-empty-t">${z.empty}</p>
            <p class="muted">${z.emptyBody}</p>
            <p class="cw-empty-acts"><a class="btn btn-primary btn-sm" href=${c} target="_blank" rel="noopener noreferrer">${i(`plus`)} ${z.addYours}</a> <a class="btn btn-sm" href=${s} target="_blank" rel="noopener noreferrer">${z.addYoursHow} ${i(`external`)}</a></p>
          </div>`}
    </section>`}setSort(e){pe(e)&&(this.sort=e)}renderSource(){let t=this.sync,n=t?.source??`bundled`,r=e=>e&&Date.now()-Date.parse(e)<1e4?`just now`:_e(e),a=this.syncing?z.syncing:n===`github`?z.fromGithub(r(t?.fetched_at)):n===`cache`?z.fromCache(r(t?.fetched_at)):z.bundled;return e`<div class="cw-src" data-source=${this.syncing?`syncing`:n}>
      <span class="cw-src-dot" aria-hidden="true"></span>
      <span class="cw-src-text small" role="status">${a}</span>
      <button class="btn btn-ghost btn-sm cw-refresh" ?disabled=${this.syncing} aria-busy=${this.syncing} @click=${()=>this.onRefresh()}>${i(`refresh`)} ${z.refresh}</button>
      ${t?.error&&!this.syncing?e`<span class="cw-src-err small" role="alert">${i(`warn`)} ${z.syncFailed(t.error.replace(/\.?$/,`.`))}</span>`:o}
      ${t?.note&&!t.error&&!this.syncing?e`<span class="cw-src-note small muted">${t.note}</span>`:o}
    </div>`}renderDropped(t){return e`<div class="cw-dropped small" data-dropped>
      <p>${i(`info`)} <span>${z.dropped(t.length)}</span> <button class="linkish" aria-expanded=${this.droppedOpen} @click=${()=>this.droppedOpen=!this.droppedOpen}>${this.droppedOpen?z.droppedHide:z.droppedDetails}</button></p>
      ${this.droppedOpen?e`<ul>${t.map(t=>e`<li><code>${t.path}</code> <span class="muted">${t.reason}</span></li>`)}</ul>`:o}
    </div>`}renderCard(t){let n=this.selected===t.id,r=ae(t.profile,this.device),a=t.status===`broken`,s=t.author,c=s.avatar&&!this.noAvatar.has(s.login);return e`<article class="ccard ${n?`is-selected`:``} ${a?`is-broken`:``}" role="listitem" data-community-card=${t.slug} data-field=${n?`weights.profile`:o} aria-labelledby=${`cc-${t.slug}`}>
      <header class="ccard-maker">
        <span class="ccard-avatar" aria-hidden="true">
          ${c?e`<img src=${s.avatar} alt="" width="40" height="40" loading="lazy" decoding="async" referrerpolicy="no-referrer" @error=${()=>this.avatarFailed(s.login)} />`:e`<span class="ccard-initials">${s.initials}</span>`}
        </span>
        <span class="ccard-who">
          <span class="ccard-handles">
            ${s.url?e`<a class="ccard-login" href=${s.url} target="_blank" rel="noopener noreferrer" title=${z.github(s.login)}>@${s.login}</a>`:e`<span class="ccard-login">@${s.login}</span>`}
            ${s.social.length?e`<span class="ccard-social">${s.social.map(t=>e`<a class="ccard-net tip-host" href=${t.href} target="_blank" rel="noopener noreferrer" aria-label=${t.label} data-tip=${t.label} data-net=${t.kind}>${B(t.kind)}</a>`)}</span>`:o}
          </span>
          ${s.name&&s.name.toLowerCase()!==s.login.toLowerCase()?e`<span class="ccard-author">${s.name}</span>`:o}
        </span>
        <span class="ccard-badges">
          ${r===`running`?e`<span class="sbadge sbadge-now">${O.activeNow}</span>`:o}
          ${this.statusBadge(t)}
        </span>
      </header>
      <h3 class="ccard-name" id=${`cc-${t.slug}`}>${t.name}</h3>
      <p class="ccard-base">${t.finetuneOf?e`${z.finetuneOf} ${t.finetuneUrl?e`<a href=${t.finetuneUrl} target="_blank" rel="noopener noreferrer">${t.finetuneOf}</a>`:e`<span>${t.finetuneOf}</span>`}`:z.stock}</p>
      ${t.description?e`<p class="ccard-desc">${t.description}</p>`:o}
      <dl class="ccard-readout num">
        <div><dt>format</dt><dd>${at(t.format)}</dd></div>
        <div><dt>${z.bpw}</dt><dd>${t.bpw==null?`–`:t.bpw.toFixed(2)}</dd></div>
        <div><dt>${z.download}</dt><dd>${r===`download`?`${t.sizeGb.toFixed(1)} GB`:O.onDisk}</dd></div>
      </dl>
      <ul class="ccard-modes" aria-label="Modalities">
        ${t.modalities.map(t=>e`<li class="ccard-mode tip-host ${t.runs?`is-on`:`is-off`}" tabindex="0" data-tip=${t.why} aria-label=${t.why} data-modality=${t.id}>${it(t.id)}<span>${t.label}</span></li>`)}
      </ul>
      <p class="ccard-drafters">${B(`drafters`)} <span>${t.drafters}</span></p>
      ${t.verified&&t.verified.speeds.length?e`<div class="ccard-speed" data-speed>
            ${t.verified.speeds.map(t=>e`<span class="ccard-sp"><span class="ccard-sp-n num">${t.tok_s.toFixed(1)}</span><span class="ccard-sp-k">${t.task}</span></span>`)}
            <span class="ccard-sp-u"><span class="num">tok/s</span><span class="ccard-sp-note">${z.speedNote(t.verified.device,t.verified.build,ge(t.verified.date))}</span></span>
          </div>`:e`<p class="ccard-speed-none muted small">${z.notMeasured}</p>`}
      ${R(this.model,t.id)?e`<p class="ccard-par muted small num" data-parallel-line>${O.weightsAtOnce}: ${R(this.model,t.id)}</p>`:o}
      <footer class="ccard-foot">
        <span class="ccard-links">
          ${t.licenseUrl?e`<a class="sbadge ccard-license" href=${t.licenseUrl} target="_blank" rel="noopener noreferrer" title=${`${z.license}: ${t.license}`}>${t.license}</a>`:e`<span class="sbadge ccard-license" title=${`${z.license}: ${t.license}`}>${t.license}</span>`}
          ${t.hfUrl?e`<a class="ccard-hf" href=${t.hfUrl} target="_blank" rel="noopener noreferrer" title=${z.onHf(t.hfRepo)} data-hf-repo>${B(`huggingface`)} <span class="ccard-repo">${t.hfRepo}</span> ${i(`external`)}</a>`:e`<span class="ccard-hf" data-hf-repo>${B(`huggingface`)} <span class="ccard-repo">${t.hfRepo}</span></span>`}
        </span>
        <button class="btn ${n?``:`btn-primary`} ccard-use" ?disabled=${a} aria-pressed=${n} title=${a?z.brokenWhy:o} @click=${()=>!n&&this.onUse(t.id)}>${n?e`${i(`check`)} ${z.selected}`:z.use}</button>
      </footer>
    </article>`}statusBadge(t){return t.status===`verified`&&t.verified?e`<span class="sbadge sbadge-ok tip-host" tabindex="0" data-tip=${z.verifiedWhy(t.verified.device,t.verified.build,ge(t.verified.date))}>${i(`check`)} ${z.verified}</span>`:t.status===`broken`?e`<span class="sbadge sbadge-err tip-host" tabindex="0" data-tip=${z.brokenWhy}>${i(`warn`)} ${z.broken}</span>`:e`<span class="sbadge tip-host" tabindex="0" data-tip=${z.submittedWhy}>${z.submitted}</span>`}avatarFailed(e){let t=new Set(this.noAvatar);t.add(e),this.noAvatar=t}};a([n({attribute:!1})],V.prototype,`model`,void 0),a([n({attribute:!1})],V.prototype,`sync`,void 0),a([n({attribute:!1})],V.prototype,`selected`,void 0),a([n({attribute:!1})],V.prototype,`device`,void 0),a([n({type:Boolean})],V.prototype,`syncing`,void 0),a([n({attribute:!1})],V.prototype,`onUse`,void 0),a([n({attribute:!1})],V.prototype,`onRefresh`,void 0),a([t()],V.prototype,`query`,void 0),a([t()],V.prototype,`sort`,void 0),a([t()],V.prototype,`droppedOpen`,void 0),a([t()],V.prototype,`noAvatar`,void 0),V=a([l(`qse-community`)],V);function ot(e){return{title:O.removeTitle(e.label,e.gb),body:e.shared_with.length?O.removeShared(e.shared_with):O.removeBody(e.model_name),again:`bash ops/use-profile.sh ${e.profile} --fetch-only`}}function st(e){if(!e)return[];let t=new Set,n=[];for(let r of[...e.weights].sort((e,t)=>Number(t.active)-Number(e.active)||t.gb-e.gb))t.has(r.removes)||(t.add(r.removes),n.push(r));return n}var H=class extends d{constructor(...e){super(...e),this.data=null,this.error=null,this.asking=null,this.busy=!1,this.done=null}connectedCallback(){super.connectedCallback(),this.load()}async load(){try{this.data=await b.weights(),this.error=null}catch(e){this.error=w(e).message}}async removeRow(e){this.busy=!0;try{let t=await b.remove([e.profile]);this.done=O.removed(e.label,t.freed_gb,t.log??`run/weights-removed.txt`),this.asking=null,this.dispatchEvent(new CustomEvent(`weights-removed`,{detail:t,bubbles:!0})),await this.load()}catch(e){this.error=w(e).message}finally{this.busy=!1}}render(){let t=st(this.data);return!this.data&&!this.error?o:e`<section class="iw" aria-labelledby="iw-h" data-installed-weights>
      <header class="iw-head">
        <h2 class="iw-title" id="iw-h">${O.installedTitle}</h2>
        ${this.data?.free_gb==null?o:e`<span class="muted small num">${O.diskFree(this.data.free_gb)}</span>`}
      </header>
      ${this.error?e`<p class="rv-warn" role="alert">${i(`warn`)} ${this.error}</p>`:o}
      ${this.done?e`<p class="iw-done" role="status" data-removed>${i(`check`)} ${this.done}</p>`:o}
      ${t.length===0&&this.data?e`<p class="muted small">${O.installedNone}</p>`:o}
      <ul class="iw-list">
        ${t.map(t=>{let n=ot(t);return e`<li class="iw-row ${t.active?`is-active`:``}" data-installed=${t.profile}>
            <div class="iw-main">
              <span class="iw-name">${t.label}</span>
              <span class="muted small">${t.model_name}${t.shared_with.length?` · ${O.sharedWith(t.shared_with)}`:``}</span>
            </div>
            <span class="iw-gb num">${t.gb.toFixed(1)} GB</span>
            ${t.active?e`<span class="sbadge sbadge-now" title=${O.runningNoRemove}>${O.activeNow}</span>`:e`<button class="btn btn-sm btn-ghost" data-remove=${t.profile} ?disabled=${!t.removable||this.busy} @click=${()=>this.asking=t.profile}>${i(`trash`)} ${O.removeGo}</button>`}
            ${this.asking===t.profile?e`<div class="iw-ask" role="alertdialog" aria-label=${n.title} data-remove-confirm>
                  <p class="iw-ask-t">${n.title}</p>
                  <p class="small">${n.body}</p>
                  <p class="small muted">${O.removeAgain} <code>${n.again}</code></p>
                  <div class="inst-actions">
                    <button class="btn btn-sm btn-danger" ?disabled=${this.busy} data-remove-yes @click=${()=>void this.removeRow(t)}>${this.busy?O.removing:O.removeYes}</button>
                    <button class="btn btn-sm" ?disabled=${this.busy} @click=${()=>this.asking=null}>${O.cancel}</button>
                  </div>
                </div>`:o}
          </li>`})}
      </ul>
    </section>`}};a([t()],H.prototype,`data`,void 0),a([t()],H.prototype,`error`,void 0),a([t()],H.prototype,`asking`,void 0),a([t()],H.prototype,`busy`,void 0),a([t()],H.prototype,`done`,void 0),H=a([l(`qse-installed-weights`)],H);function ct(e){return e.group===`long`?e.mode===`greedy`?`long_greedy`:`long_sampled`:`${e.mode}_${e.len}`}function lt(e){return e===`everyday`?[`profile`,`prose`,`chat`,`code`,`ttft8`,`kl`,`lossless`]:[`profile`,`long32k`,`long64k`,`ttft32`,`kl`]}var ut={profile:O.colWeights,prose:O.colProse,chat:O.colChat,code:O.colCode,long32k:O.colLong32,long64k:O.colLong64,ttft8:O.colTtft8,ttft32:O.colTtft32,kl:O.colKl,lossless:O.colLossless};function U(e,t,n,r){let i=t.cells[ct(r)]??{};switch(n){case`profile`:{let n=e.profiles.find(e=>e.id===t.profile);return n?.bpw??n?.weights_gib??null}case`ttft8`:return t.ttft_s[8192]??null;case`ttft32`:return t.ttft_s[32768]??null;case`kl`:return e.profiles.find(e=>e.id===t.profile)?.quality.kl_vs_bf16??null;case`lossless`:return t.lossless?t.lossless.identical:null;default:return i[n]?.tok_s??null}}function dt(e,t){let n=e.measurements.rows,r=n.filter(e=>e.comparable),i=n.filter(e=>!e.comparable),a=n=>U(e,n,t.sort,t);return r.sort((e,n)=>{let r=a(e),i=a(n);return r==null&&i==null?0:r==null?1:i==null?-1:t.dir===`asc`?r-i:i-r}),{main:r,apart:i}}var W=[`prose`,`chat`,`code`,`long32k`,`long64k`],G=class extends d{constructor(...e){super(...e),this.model=null,this.selected=null,this.view={mode:`greedy`,len:`512`,group:`everyday`,sort:`code`,dir:`desc`}}set(e){let t={...this.view,...e};e.group&&!lt(e.group).includes(t.sort)&&(t.sort=e.group===`long`?`long32k`:`code`),this.view=t}sortBy(e){this.view.sort===e?this.set({dir:this.view.dir===`asc`?`desc`:`asc`}):this.set({sort:e,dir:W.includes(e)||e===`lossless`?`desc`:`asc`})}render(){let t=this.model;if(!t)return o;let n=this.view,r=lt(n.group),{main:i,apart:a}=dt(t,n),s={};for(let e of W)s[e]=Math.max(0,...i.map(r=>U(t,r,e,n)??0));let c=(t,n,r,i)=>e`<div class="seg" role="radiogroup" aria-label=${i}>${n.map(([n,i])=>e`<button class="seg-btn ${t===n?`is-on`:``}" role="radio" aria-checked=${t===n} data-val=${n} @click=${()=>r(n)}>${i}</button>`)}</div>`;return e`<div class="mt">
      <div class="mt-controls">
        ${c(n.mode,[[`greedy`,O.modeGreedy],[`sampled`,O.modeSampled]],e=>this.set({mode:e}),`Decoding`)}
        ${n.group===`everyday`?c(n.len,[[`512`,O.tokens512],[`128`,O.tokens128]],e=>this.set({len:e}),`Answer length`):o}
        ${c(n.group,[[`everyday`,O.groupEveryday],[`long`,O.groupLong]],e=>this.set({group:e}),`Tasks`)}
      </div>
      <div class="table-scroll mt-scroll">
        <table class="table mt-table" data-mode=${ct(n)}>
          <thead>
            <tr>
              ${r.map(t=>{let r=n.sort===t,i=W.includes(t)?O.tokPerS:t===`ttft8`||t===`ttft32`?O.seconds:``;return e`<th scope="col" class=${t===`profile`?`mt-name`:`num-col`} aria-sort=${r?n.dir===`asc`?`ascending`:`descending`:`none`}>
                  <button class="mt-sort ${r?`is-on`:``}" data-col=${t} @click=${()=>this.sortBy(t)} aria-label=${O.sortBy(ut[t])}>
                    <span>${ut[t]}</span>${i?e`<span class="mt-unit">${i}</span>`:o}<span class="mt-arrow" aria-hidden="true">${r?n.dir===`asc`?`▴`:`▾`:``}</span>
                  </button>
                </th>`})}
            </tr>
          </thead>
          <tbody>
            ${i.map(e=>this.row(t,e,r,n,s))}
            ${a.length?e`<tr class="mt-apart-head"><td colspan=${r.length}>${O.notComparable}</td></tr>
                  ${a.map(e=>this.row(t,e,r,n,s,!0))}`:o}
          </tbody>
        </table>
      </div>
      <div class="mt-foot muted small">${O.measuredOn(t.measurements.build,t.measurements.date)} <details class="mt-method"><summary>${O.howMeasured}</summary><p>${t.measurements.method}</p></details></div>
    </div>`}row(t,n,r,i,a,s=!1){let c=t.profiles.find(e=>e.id===n.profile),l=n.profile===this.selected;return e`<tr class="mt-row ${l?`is-selected`:``} ${s?`is-apart`:``}" data-profile=${n.profile} aria-current=${l?`true`:o}>
      ${r.map(r=>{if(r===`profile`)return e`<th scope="row" class="mt-name">
            <span class="mt-label">${c?.label??n.profile}</span>
            ${l?e`<span class="mt-mine">${O.selectedRow}</span>`:o}
            ${s?e`<span class="mt-note" title=${n.note??``}>${O.notComparable}</span>`:o}
          </th>`;let u=U(t,n,r,i);if(u==null)return e`<td class="num-col mt-na">–</td>`;if(r===`kl`)return e`<td class="num-col num">${u.toFixed(4)}</td>`;if(r===`lossless`){let t=n.lossless;return e`<td class="num-col num" title=${O.losslessTitle(t.identical,t.near_tie)}>${t.identical} + ${t.near_tie}</td>`}if(r===`ttft8`||r===`ttft32`)return e`<td class="num-col num">${u.toFixed(1)}</td>`;let d=a[r]?u/a[r]*100:0;return e`<td class="num-col num mt-speed"><span class="mt-bar" style="--w:${d.toFixed(1)}" aria-hidden="true"></span><span class="mt-val">${u.toFixed(1)}</span></td>`})}
    </tr>`}};a([n({attribute:!1})],G.prototype,`model`,void 0),a([n({attribute:!1})],G.prototype,`selected`,void 0),a([t()],G.prototype,`view`,void 0),G=a([l(`qse-measure-table`)],G);var ft=[`model`,`helpers`,`caches`,`engine`,`system`,`chats`],K=class extends d{constructor(...e){super(...e),this.model=null,this.estimate=null,this.available=null,this.local=!1,this.overText=null,this.open=!1,this.focusPart=null}render(){let t=this.estimate,n=this.model;if(!t||!n)return e`<section class="membar is-empty" aria-label=${O.memTitle}><div class="membar-line"><span class="membar-title">${O.memTitle}</span></div><div class="membar-track"></div></section>`;let r=t.budget_gb??t.device_gb-t.floor_gb,i=je(t),a=t.parts.find(e=>e.kind===`kv`),s=Math.max(t.device_gb,t.total_gb),c=e=>`${(e/s*100).toFixed(3)}%`,l=e=>ft.indexOf(De(n,e)),u=[...t.parts].sort((e,t)=>l(e.id)-l(t.id)),d=0,f=u.map(e=>{let t={p:e,left:d,cat:De(n,e.id)};return d+=e.gb,t}),p=this.available==null?null:Math.max(0,t.device_gb-this.available),m=new Map;for(let e of f)m.set(e.cat,(m.get(e.cat)??0)+e.p.gb);let h=f.find(e=>e.p.id===this.focusPart),g=h?`${h.p.label}: ${h.p.gb.toFixed(2)} GiB${h.p.worst_gb!=null&&h.p.worst_gb!==h.p.gb?` (${O.memoryUpTo(h.p.worst_gb)})`:``}`:null,_=`${O.memTitle}: ${O.memFits(t.total_gb,r)}. ${i>0?this.overText??``:O.memLeft(r-t.total_gb)}`;return e`<section class="membar ${i>0?`is-over`:``} ${this.open?`is-open`:``}" aria-label=${O.memTitle} data-fits=${t.fits} data-total=${t.total_gb.toFixed(2)}>
      <div class="membar-line">
        <span class="membar-title">${O.memTitle}</span>
        <span class="membar-fig num" data-membar-total><b>${t.total_gb.toFixed(1)}</b><span class="membar-of"> / ${r.toFixed(1)} GiB</span></span>
        <span class="membar-say ${i>0?`is-over`:``}" data-membar-say role=${i>0?`alert`:o}>${i>0?this.overText??O.overSentence(i,null):O.memLeft(r-t.total_gb)}</span>
        <span class="membar-now num">${p==null?o:e`<i class="membar-now-dot" aria-hidden="true"></i>${O.memNow(p)}`}</span>
        <span class="membar-device num">${O.memDevice(t.device_gb)}</span>
        <button class="btn btn-ghost btn-sm membar-toggle" aria-expanded=${this.open} aria-controls="membar-more" aria-label=${this.open?O.memCollapse:O.memExpand} @click=${()=>this.open=!this.open}>
          <svg class="icon" viewBox="0 0 20 20" aria-hidden="true"><path d="m5 8 5 5 5-5" fill="none" stroke="currentColor" stroke-width="1.8" stroke-linecap="round" stroke-linejoin="round" /></svg>
        </button>
      </div>
      <div class="membar-track" role="img" aria-label=${_}>
        <span class="membar-free" style="left:${c(r)};width:${c(Math.max(0,t.device_gb-r))}" title=${`${O.memFloor}: ${t.floor_gb} GiB`}><span class="membar-floor-label">${O.memFloor} ${t.floor_gb} GiB</span></span>
        ${f.map(t=>e`<span
            class="membar-seg cat-${t.cat} ${t.p.kind===`kv`?`is-kv`:``} ${this.focusPart===t.p.id?`is-focus`:``}"
            style="left:${c(t.left)};width:${c(t.p.gb)}"
            data-part=${t.p.id}
            tabindex="0"
            aria-label=${`${t.p.label} ${t.p.gb.toFixed(1)} GiB`}
            @mouseenter=${()=>this.focusPart=t.p.id}
            @mouseleave=${()=>this.focusPart=null}
            @focus=${()=>this.focusPart=t.p.id}
            @blur=${()=>this.focusPart=null}
          ></span>`)}
        ${a&&a.worst_gb!=null&&a.worst_gb>a.gb?e`<span class="membar-worst" style="left:${c(t.total_gb)};width:${c(Math.min(a.worst_gb-a.gb,s-t.total_gb))}" title=${`${O.memWorst}: ${a.worst_gb.toFixed(1)} GiB`}></span>`:o}
        ${i>0?e`<span class="membar-over" style="left:${c(r)};width:${c(t.total_gb-r)}"></span>`:o}
        <span class="membar-floor" style="left:${c(r)}"></span>
        ${p==null?o:e`<span class="membar-nowtick" style="left:${c(Math.min(p,s))}" title=${O.memNow(p)}></span>`}
      </div>
      <div class="membar-more" id="membar-more">
        <ul class="membar-legend">
          ${ft.filter(e=>m.has(e)).map(t=>e`<li class="cat-${t}"><i class="membar-sw" aria-hidden="true"></i>${O.memCat[t]} <span class="num">${(m.get(t)??0).toFixed(1)}</span></li>`)}
          <li class="cat-free"><i class="membar-sw" aria-hidden="true"></i>${O.memFloor} <span class="num">${t.floor_gb}</span></li>
        </ul>
        <p class="membar-detail" aria-live="polite">${g??(this.local?O.memLocal:t.notes[0]??`\xA0`)}</p>
      </div>
    </section>`}};a([n({attribute:!1})],K.prototype,`model`,void 0),a([n({attribute:!1})],K.prototype,`estimate`,void 0),a([n({attribute:!1})],K.prototype,`available`,void 0),a([n({type:Boolean})],K.prototype,`local`,void 0),a([n({attribute:!1})],K.prototype,`overText`,void 0),a([t()],K.prototype,`open`,void 0),a([t()],K.prototype,`focusPart`,void 0),K=a([l(`qse-memory-bar`)],K);var pt=[`set_field`,`toggle`,`select_profile`,`go_to_step`,`next`,`previous`,`explain`,`read_device`],q=(e,t=!1)=>({ok:!1,error:e,...t?{stale:t}:{}});function J(e,t){return`${e}.${t}`}function mt(e){if(e==null)return{};if(typeof e==`string`){if(!e.trim())return{};try{let t=JSON.parse(e);return t&&typeof t==`object`&&!Array.isArray(t)?t:null}catch{return null}}return typeof e==`object`&&!Array.isArray(e)?e:null}function Y(e){if(typeof e==`number`&&Number.isInteger(e)&&e>=0&&e<x.length)return x[e];if(typeof e==`string`){if(/^\d+$/.test(e))return Y(Number(e));if(y(e))return e}return null}function ht(e,t){return(e===`int`||e===`number`)&&typeof t==`string`&&t.trim()!==``&&Number.isFinite(Number(t))?Number(t):e===`bool`&&(t===`true`||t===`false`)?t===`true`:t}function gt(e){return typeof e==`boolean`?e?`on`:`off`:typeof e==`number`?e.toLocaleString(`en-US`):e}function _t(e,t){let n=String(e.name??``);if(!pt.includes(n))return/finish|apply|install|reset|delete|unload|load|confirm|submit/i.test(n)?q(O.tool.unknown(n)+` `+O.tool.userOnly(n)):q(O.tool.unknown(n));let r=mt(e.args);if(!r)return q(O.tool.badArgs(`expected a JSON object`));if(n===`read_device`)return{ok:!0,kind:`read_device`};let i=t.model,a=t.selection,o=(e,n)=>{let r=t.touchedAt?.get(J(e,n));return r!=null&&t.turnStartedAt!=null&&r>t.turnStartedAt};switch(n){case`go_to_step`:{let e=r.n??r.step??r.index,n=Y(e);return n?n===`install`?q(O.tool.installStep):n!==`models`&&!i?q(O.tool.noModel):{ok:!0,kind:`action`,actions:[{type:n===`models`?`tiles`:`go`,step:n}],undo:[{type:`go`,step:t.step}],target:{step:n,field:null},summary:O.toolDone.step(E[n]),result:O.tool.ok}:q(O.tool.noStep(String(e)))}case`next`:{if(!i)return q(O.tool.noModel);let e=C(t.step);if(e>=C(`review`))return q(O.tool.atEnd);let n=x[e+1];return{ok:!0,kind:`action`,actions:[{type:`next`}],undo:[{type:`go`,step:t.step}],target:{step:n,field:null},summary:O.toolDone.step(E[n]),result:O.tool.ok}}case`previous`:{let e=C(t.step);if(e<=0)return q(O.tool.atStart);let n=x[e-1];return{ok:!0,kind:`action`,actions:[{type:`previous`}],undo:[{type:`go`,step:t.step}],target:{step:n,field:null},summary:O.toolDone.step(E[n]),result:O.tool.ok}}}if(!i||!a)return q(O.tool.noModel);if(n===`explain`){let e=String(r.field??r.id??``),t=i.components.find(t=>t.id===e);if(t)return{ok:!0,kind:`explain`,target:{step:`components`,field:t.id},result:`${t.label}: ${t.what}${t.note?` `+t.note:``}`};let n=te(i,e);if(n)return{ok:!0,kind:`explain`,target:{step:`methods`,field:n.field.id},result:`${n.field.label}: ${n.field.what}`};let a=i.profiles.find(t=>t.id===e);if(a)return{ok:!0,kind:`explain`,target:{step:`weights`,field:`profile`},result:`${a.label}: KL ${a.quality.kl_vs_bf16??`?`}, ${a.weights_gib} GiB in memory${a.note?`. `+a.note:``}`};let o={slots:O.slotsWhat,window:O.windowWhat,kv_pool:O.kvPoolWhat,floor_gb:O.floorWhat};return o[e]?{ok:!0,kind:`explain`,target:{step:`parallelism`,field:e},result:o[e]}:q(O.tool.noField(`any`,e))}if(n===`select_profile`){let e=String(r.id??r.profile??``),t=i.profiles.find(t=>t.id===e);return t?o(`weights`,`profile`)?q(O.tool.stale,!0):a.profile===e?{ok:!0,kind:`action`,actions:[],undo:[],target:{step:`weights`,field:`profile`},summary:O.toolDone.profile(t.label),result:O.tool.same}:{ok:!0,kind:`action`,actions:[{type:`profile`,id:e}],undo:[{type:`profile`,id:a.profile}],target:{step:`weights`,field:`profile`},summary:O.toolDone.profile(t.label),result:O.tool.ok}:q(O.tool.noProfile(e))}if(n===`toggle`){let e=String(r.component??r.id??``),t=ht(`bool`,r.on);return typeof t==`boolean`?vt(i,a,e,t,o(`components`,e)):q(O.tool.badArgs(`on must be true or false`))}let s=Y(r.step);if(!s)return q(O.tool.noStep(String(r.step)));let c=String(r.field??``),l=re(i).find(e=>e.id===s)?.fields.find(e=>e.field===c);if(!l)return q(O.tool.noField(s,c));if(de.has(c))return q(O.tool.secret(l.label));let u=ht(l.type,r.value),d=S(l,u);if(d)return q(O.tool.badArgs(`${c}: ${d}`));if(s===`components`)return vt(i,a,c,u,o(s,c));if(o(s,c))return q(O.tool.stale,!0);let f={step:s,field:c},p=O.toolDone.field(l.label,gt(u));if(s===`weights`)return _t({name:`select_profile`,args:{id:u}},t);if(s===`parallelism`){let e=c,t=a[e];return t===u?{ok:!0,kind:`action`,actions:[],undo:[],target:f,summary:p,result:O.tool.same}:{ok:!0,kind:`action`,actions:[{type:`parallel`,key:e,value:u}],undo:[{type:`parallel`,key:e,value:t}],target:f,summary:p,result:O.tool.ok}}if(s===`methods`){let e=te(i,c);if(!e)return q(O.tool.noField(s,c));let t=a[e.group][c]??e.field.recommended;return t===u?{ok:!0,kind:`action`,actions:[],undo:[],target:f,summary:p,result:O.tool.same}:{ok:!0,kind:`action`,actions:[{type:`field`,group:e.group,id:c,value:u}],undo:[{type:`field`,group:e.group,id:c,value:t}],target:f,summary:p,result:O.tool.ok}}return q(O.tool.noField(s,c))}function vt(e,t,n,r,i){let a=e.components.find(e=>e.id===n);if(!a)return q(O.tool.noComponent(n));if(a.required&&!r)return q(O.tool.required(a.label));let o=h(e,t.components)[n];if(r&&o.disabled&&!o.required)return q(o.reason??O.tool.noComponent(n));if(i)return q(O.tool.stale,!0);let s={step:`components`,field:n},c=r?O.toolDone.toggleOn(a.label):O.toolDone.toggleOff(a.label);return(a.required||(t.components[n]??a.default))===r?{ok:!0,kind:`action`,actions:[],undo:[],target:s,summary:c,result:O.tool.same}:{ok:!0,kind:`action`,actions:[{type:`component`,id:n,on:r}],undo:[{type:`components`,set:{...t.components}}],target:s,summary:c,result:O.tool.ok}}var X=new Set([`explain`,`read_device`]);function yt(e){if(!e.tools||!e.calls.length)return null;let t=e.round+1>=4;return e.calls.some(e=>!e.ok&&!X.has(e.name))?e.retried?{tools:!1,retried:!0}:{tools:!t,retried:!0}:e.calls.every(e=>X.has(e.name)&&e.name!==`read_device`)?e.text.trim()?null:{tools:!1,retried:e.retried}:{tools:!t,retried:e.retried}}function bt(e){let t=new Map,n=0,r=r=>{let i=new Map,a=null;for(let t=n;t<r;t++){let n=e[t];if(n.role===`assistant`)for(let e of n.tool_calls??[])i.set(e.id,e.name);if(n.role!==`tool`||X.has(i.get(n.tool_call_id??``)??``))continue;let r={};try{r=JSON.parse(n.content)}catch{}r.ok===!1?a=r.stale||a===`stale`?`stale`:`unchanged`:r.ok===!0&&(a=null)}a&&r>n&&t.set(r-1,a)};return e.forEach((e,t)=>{e.role===`user`&&t!==0&&(r(t),n=t)}),r(e.length),t}function xt(e,t){return t&&e.ok&&e.summary?e.summary:null}var St=0;function Ct(e){return e.charAt(0).toUpperCase()+e.slice(1)}var Z=class extends d{constructor(...e){super(...e),this.host=null,this.step=`models`,this.chat=[],this.finished=!1,this.phase=`checking`,this.status=null,this.figure=`sleeping`,this.progress=null,this.streaming=!1,this.partial=``,this.composer=``,this.error=null,this.notice=null,this.sources=[],this.open=!1,this.toast=null,this.chips=new Map,this.ctrl=null,this.loadPoll=null,this.ping=null,this.loadStartedAt=0,this.lastUserText=``,this.opener=null,this.toastTimer=null,this.onKey=e=>{if(!this.open||!this.sheetMode())return;if(e.key===`Escape`){e.preventDefault(),this.close();return}if(e.key!==`Tab`)return;let t=this.querySelector(`.wren-panel`);if(!t)return;let n=[...t.querySelectorAll(`button:not([disabled]), textarea:not([disabled]), a[href], input:not([disabled])`)].filter(e=>e.offsetParent!==null);if(!n.length)return;let r=n[0],i=n[n.length-1];e.shiftKey&&document.activeElement===r?(e.preventDefault(),i.focus()):!e.shiftKey&&document.activeElement===i?(e.preventDefault(),r.focus()):t.contains(document.activeElement)||(e.preventDefault(),r.focus())}}connectedCallback(){super.connectedCallback(),this.checkStatus(),this.ping=setInterval(()=>{this.phase===`ready`&&document.visibilityState===`visible`&&this.checkStatus(!0)},3e4),document.addEventListener(`keydown`,this.onKey)}disconnectedCallback(){this.ctrl?.abort(),this.loadPoll&&clearTimeout(this.loadPoll),this.ping&&clearInterval(this.ping),this.toastTimer&&clearTimeout(this.toastTimer),document.removeEventListener(`keydown`,this.onKey),this.lockScroll(!1),super.disconnectedCallback()}updated(e){e.has(`finished`)&&this.finished&&this.phase!==`finished`&&this.sleepForInstall()}emit(e){this.figure=Re(this.figure,e)}async checkStatus(e=!1){if(this.finished){this.phase=`finished`,this.figure=`sleeping`;return}try{let e=await b.assistant.status();this.applyStatus(e)}catch(t){if(e)return;this.phase=`failed`,this.error=w(t).status===0?O.wrenOffline:w(t).message,this.emit(`error`)}}applyStatus(e){switch(this.status=e,e.state){case`loaded`:this.phase!==`ready`&&(this.phase=`ready`,this.progress=null,this.figure=`loading`,this.emit(`loaded`));break;case`loading`:this.phase=`loading`,this.progress=this.timeProgress(e.loading_s??null),this.emit(`load-progress`),this.scheduleLoadPoll();break;case`refused`:this.phase=`refused`,this.emit(`error`);break;case`failed`:this.phase=`failed`,this.error=e.reason??O.wrenFailed,this.emit(`error`);break;default:this.phase=e.model==null?`refused`:`asleep`,this.figure=`sleeping`}}timeProgress(e){let t=e??(Date.now()-this.loadStartedAt)/1e3;return Math.min(.95,1-Math.exp(-t/8))}scheduleLoadPoll(){this.loadPoll&&clearTimeout(this.loadPoll),this.loadPoll=setTimeout(()=>void this.checkStatus(!0),500)}async wake(){this.error=null,this.loadStartedAt=Date.now(),this.phase=`loading`,this.progress=0,this.emit(`load-start`);try{this.applyStatus(await b.assistant.load())}catch(e){let t=w(e),n=e instanceof T?e.body:void 0;n?.assistant&&(this.status=n.assistant),t.status===409?(this.phase=`refused`,n?.assistant?.reason&&(this.status={...this.status,reason:n.assistant.reason})):(this.phase=`failed`,this.error=t.status===0?O.wrenOffline:t.message),this.emit(`error`)}}async sleep(){this.stop();try{this.applyStatus(await b.assistant.unload())}catch{}this.phase=`asleep`,this.emit(`unload`)}async sleepForInstall(){this.ctrl?.abort(),this.phase=`finished`,this.emit(`unload`),this.close();try{await b.assistant.unload()}catch{}}stop(){this.ctrl&&this.ctrl.abort()}async send(e){let t=e.trim();if(!t||this.streaming||!this.host)return;this.lastUserText=t,this.composer=``,this.error=null,this.notice=null,this.toast=null;let n=[...this.chat,{role:`user`,content:t}];this.setChat(n),await this.turn(n)}async retry(){if(!this.lastUserText)return;this.error=null;let e=this.chat[this.chat.length-1]?.role===`user`?this.chat:[...this.chat,{role:`user`,content:this.lastUserText}];this.setChat(e),await this.turn(e)}setChat(e){this.chat=e,this.host?.saveChat(e),this.updateComplete.then(()=>this.scrollLog())}async turn(e){let t=this.host,n=Date.now(),r=e;this.streaming=!0,this.emit(`send`),this.ctrl=new AbortController;let i=this.ctrl.signal,a=!0,o=!1,s=!1;try{for(let e=1;;e++){let c=``,l=[],u=[],d=[],f=!0,p=null;if(this.partial=``,await g({messages:r,page:t.pageState(),...a?{}:{tools:!1}},e=>{e.type===`state`?this.emit(e.state===`thinking`?`state-thinking`:e.state===`loading`?`load-progress`:`first-token`):e.type===`text`?(c+=e.delta,this.partial=c,f?(f=!1,this.emit(`first-token`)):this.emit(`delta`),this.fig?.blip(),this.fabFig?.blip(),this.scrollLog()):e.type===`tool_call`?l.push({id:e.id||`call_${++St}`,name:e.name,args:e.args}):e.type===`done`?(this.sources=e.sources??[],e.unchanged&&(s=!0)):e.type===`error`&&(p=e.message)},i),p&&/another tab/i.test(p)){this.notice=O.wrenBusy;break}if(p)throw new T(`/v1/setup/assistant/chat`,500,p,`stream`);for(let e of l){let r=await t.execute(e,n);r.ok&&(this.emit(`tool-call`),this.fig?.hop(),this.emit(`acted`)),u.push({role:`tool`,tool_call_id:e.id,content:JSON.stringify(r.ok?{ok:!0,result:r.result,...r.summary?{summary:r.summary}:{}}:{ok:!1,error:r.result,...r.stale?{stale:!0}:{}})}),this.chips=new Map(this.chips).set(e.id,{summary:r.summary,ok:r.ok,stale:r.stale,undo:r.undo,undone:!1}),d.push({name:e.name,ok:r.ok,changed:!!r.summary});let i=xt(r,this.open&&this.sheetMode());i&&this.showToast(e.id,i,r.reveal??null)}let m={role:`assistant`,content:c,...l.length?{tool_calls:l}:{}};r=[...r,m,...u],this.partial=``,this.setChat(r);let h=yt({round:e,tools:a,text:c,calls:d,retried:o});if(!h)break;({tools:a,retried:o}=h)}s&&!bt(r).has(r.length-1)&&(this.notice=O.wrenNotChanged),this.emit(`done`)}catch(e){if(e.name===`AbortError`)this.partial&&this.setChat([...r,{role:`assistant`,content:`${this.partial} ${O.wrenStopped}`}]),this.partial=``,this.emit(`stop`);else{let t=w(e);t.status===409?this.notice=O.wrenBusy:t.status===0?this.error=O.wrenOffline:t.status===503||/unload|asleep|not loaded/i.test(t.message)?(this.error=O.wrenWentToSleep,this.checkStatus(!0)):this.error=t.message||O.wrenError,this.partial&&this.setChat([...r,{role:`assistant`,content:this.partial}]),this.partial=``,this.emit(t.status===409?`done`:`error`)}}finally{this.streaming=!1,this.ctrl=null}}undoChip(e){let t=this.chips.get(e);t?.undo&&!t.undone&&(t.undo(),this.chips=new Map(this.chips).set(e,{...t,undone:!0}))}scrollLog(){let e=this.log;e&&(e.scrollTop=e.scrollHeight)}sheetMode(){return typeof matchMedia==`function`&&matchMedia(`(max-width: 1100px)`).matches}toggleOpen(){this.open?this.close():(this.opener=document.activeElement,this.open=!0,this.lockScroll(!0),this.updateComplete.then(()=>(this.querySelector(`.wren-panel .wren-input, .wren-panel .btn-primary, .wren-close`)??null)?.focus()))}close(){this.open&&(this.open=!1,this.lockScroll(!1),this.opener?.focus?.())}lockScroll(e){document.documentElement.classList.toggle(`wren-sheet-open`,e&&this.sheetMode())}showToast(e,t,n){this.toast={id:e,summary:t,reveal:n},this.toastTimer&&clearTimeout(this.toastTimer),this.toastTimer=setTimeout(()=>this.toast=null,12e3)}async showMe(){let e=this.toast?.reveal;this.toast=null,this.close(),await this.updateComplete,requestAnimationFrame(()=>e?.())}statusWord(){return this.phase===`finished`?O.wrenFinished:this.phase===`asleep`?O.wrenAsleep:this.phase===`refused`?O.wrenCannotLoad:this.phase===`failed`?O.wrenFailed:this.phase===`loading`?O.wrenWaking(this.progress):O.wrenStatus[this.figure]??``}render(){let t=this.figure;return e`
      <button class="wren-fab ${this.open?`is-open`:``}" aria-label=${this.open?O.wrenClose:O.wrenOpen} aria-expanded=${this.open} aria-controls="wren-panel" @click=${()=>this.toggleOpen()} data-figure=${t}>
        <qse-wren-figure class="wren-fab-fig" .state=${t} .size=${50} .progress=${this.progress}></qse-wren-figure>
      </button>
      <div class="wren-scrim ${this.open?`is-open`:``}" @click=${()=>this.close()}></div>
      <aside id="wren-panel" class="wren-panel ${this.open?`is-open`:``}" aria-label=${O.wrenName} data-phase=${this.phase} data-figure=${t} role="complementary">
        <div class="wren-handle" aria-hidden="true"></div>
        <header class="wren-head">
          <qse-wren-figure class="wren-main" .state=${t} .size=${76} .progress=${this.progress}></qse-wren-figure>
          <div class="wren-titles">
            <h2 class="wren-name">${O.wrenName}</h2>
            <p class="wren-status" role="status" aria-live="polite" data-wren-status>${this.statusWord()}</p>
            ${this.status?.model&&this.phase===`ready`?e`<p class="wren-model">${this.status.model}${this.status.fallback?e` <span title=${O.wrenFallback}>(${O.wrenFallback})</span>`:o}</p>`:o}
          </div>
          <div class="wren-head-tools">
            ${this.phase===`ready`&&!this.streaming?e`<button class="btn btn-ghost btn-icon" title=${O.wrenSleep} aria-label=${O.wrenSleep} @click=${()=>void this.sleep()}>${i(`moon`)}</button>`:o}
            <button class="btn btn-ghost btn-icon wren-close" aria-label=${O.wrenClose} title=${O.wrenClose} @click=${()=>this.close()}>${i(`x`)}</button>
          </div>
        </header>
        ${this.renderBody()}
      </aside>
    `}hero(){return e`<qse-wren-figure class="wren-hero" .state=${this.figure} .size=${148} .progress=${this.progress}></qse-wren-figure>`}renderBody(){switch(this.phase){case`checking`:return e`<div class="wren-body wren-center">${this.hero()}</div>`;case`finished`:return e`<div class="wren-body wren-center" data-wren-finished>${this.hero()}<p>${O.wrenFinished}</p></div>${this.renderComposer(!0)}`;case`asleep`:return e`<div class="wren-body wren-center">
            ${this.hero()}
            <p class="wren-lead">${O.wrenAsleep}</p>
            <p class="muted small">${O.wrenAsleepBody}</p>
            <button class="btn btn-primary wren-wake" @click=${()=>void this.wake()}>${O.wrenWake}</button>
          </div>`;case`loading`:return e`<div class="wren-body wren-center">
            ${this.hero()}
            <p class="wren-lead">${O.wrenWaking(this.progress)}</p>
            <div class="wren-loadbar" role="progressbar" aria-valuemin="0" aria-valuemax="100" aria-valuenow=${this.progress==null?o:Math.round(this.progress*100)} aria-label=${O.wrenWaking(null)}><span style="width:${Math.round((this.progress??0)*100)}%"></span></div>
          </div>`;case`refused`:{let t=this.status,n=t?.model==null&&t!=null?O.wrenNoModel:O.wrenCannotLoadMemory(t?.claim_gib??null,this.host?.memAvailable()??null);return e`<div class="wren-body wren-center" data-wren-refused>
            ${this.hero()}
            <p class="wren-lead">${O.wrenCannotLoad}</p>
            <p class="muted small">${t?.reason?Ct(t.reason):n}</p>
            ${t?.reason?e`<p class="muted small">${n}</p>`:o}
            ${t?.model==null?o:e`<button class="btn wren-wake" @click=${()=>void this.wake()}>${i(`refresh`)} ${O.wrenRetry}</button>`}
          </div>`}case`failed`:return e`<div class="wren-body wren-center" data-wren-failed>
            ${this.hero()}
            <p class="wren-lead">${O.wrenFailed}</p>
            <p class="muted small">${this.error??``}</p>
            <button class="btn wren-wake" @click=${()=>void this.wake()}>${i(`refresh`)} ${O.wrenRetry}</button>
          </div>`;case`ready`:return this.renderChat()}}renderChat(){let t=this.chat,n=bt(t);return e`
      <div class="wren-body wren-log" aria-live="off">
        ${t.length===0?e`<p class="wren-msg is-assistant wren-hello">${O.wrenHello}</p>`:o}
        ${be(t.map((e,t)=>({m:e,i:t})),({i:e})=>e,({m:r,i})=>e`${this.renderMessage(r,i===t.length-1)}${n.has(i)?e`<p class="wren-note" data-tool-rejected>${n.get(i)===`stale`?O.tool.stale:O.wrenNotChanged}</p>`:o}`)}
        ${this.partial?e`<div class="wren-msg is-assistant is-streaming">${ze(Be(this.partial))}</div>`:o}
        ${this.streaming&&!this.partial?e`<div class="wren-msg is-assistant wren-typing" aria-hidden="true"><span></span><span></span><span></span></div>`:o}
        ${this.notice?e`<p class="wren-note">${this.notice}</p>`:o}
        ${this.error?e`<div class="wren-error" role="alert" data-wren-error>
              <span>${this.error}</span>
              <button class="btn btn-sm" @click=${()=>void this.retry()}>${i(`refresh`)} ${O.wrenRetry}</button>
            </div>`:o}
      </div>
      ${this.toast?e`<div class="wren-toast" role="status" data-wren-toast>
            <span class="wren-chip-dot" aria-hidden="true"></span>
            <span class="wren-toast-text">${this.toast.summary}</span>
            ${this.toast.reveal?e`<button class="btn btn-sm" @click=${()=>void this.showMe()}>${O.wrenShowMe}</button>`:o}
          </div>`:o}
      ${this.renderChips()} ${this.renderComposer(!1)}
    `}renderMessage(t,n){if(t.role===`user`)return e`<div class="wren-msg is-user">${t.content}</div>`;if(t.role===`assistant`)return t.content.trim()?e`<div class="wren-msg is-assistant">${ze(Be(t.content))}
        ${n&&this.sources.length?e`<p class="wren-sources">${O.wrenSources} ${this.sources.map((t,n)=>e`${n?`, `:``}<span title=${t.path}>${t.title||t.path}</span>`)}</p>`:o}
      </div>`:o;let r={};try{r=JSON.parse(t.content)}catch{}let i=t.tool_call_id?this.chips.get(t.tool_call_id):void 0;if(r.ok===!1||i?.ok===!1)return o;let a=i?.summary??r.summary;return a?e`<div class="wren-chip ${i?.undone?`is-undone`:``} ${this.toast?.id===t.tool_call_id?`is-fresh`:``}" data-tool-chip>
      <span class="wren-chip-dot" aria-hidden="true"></span>
      <span>${a}</span>
      ${i?.undo?e`<button class="btn btn-ghost btn-sm" ?disabled=${i.undone} @click=${()=>this.undoChip(t.tool_call_id)}>${i.undone?O.wrenUndone:O.wrenUndo}</button>`:o}
    </div>`:o}renderChips(){if(this.streaming)return o;let t=O.wrenChips[this.step]??[];return e`<div class="wren-suggest">${t.map(t=>e`<button class="wren-suggestion" @click=${()=>void this.send(t)}>${t}</button>`)}</div>`}renderComposer(t){return e`<form
      class="wren-composer"
      @submit=${e=>{e.preventDefault(),this.send(this.composer)}}
    >
      <label class="sr-only" for="wren-input">${O.wrenPlaceholder}</label>
      <textarea
        id="wren-input"
        class="wren-input"
        rows="1"
        .value=${this.composer}
        ?disabled=${t}
        placeholder=${t?O.wrenFinished:O.wrenPlaceholder}
        @focus=${()=>this.emit(`focus`)}
        @blur=${()=>!this.composer&&this.emit(`blur`)}
        @input=${e=>{this.composer=e.target.value,this.emit(`input`)}}
        @keydown=${e=>{e.key===`Enter`&&!e.shiftKey&&!e.isComposing&&(e.preventDefault(),this.send(this.composer))}}
      ></textarea>
      ${this.streaming?e`<button type="button" class="btn wren-stop" @click=${()=>this.stop()} aria-label=${O.wrenStop}>${i(`stop`)}<span>${O.wrenStop}</span></button>`:e`<button type="submit" class="btn btn-primary wren-send" ?disabled=${t||!this.composer.trim()} aria-label=${O.wrenSend}>${i(`send`)}</button>`}
    </form>`}};a([n({attribute:!1})],Z.prototype,`host`,void 0),a([n({attribute:!1})],Z.prototype,`step`,void 0),a([n({attribute:!1})],Z.prototype,`chat`,void 0),a([n({type:Boolean})],Z.prototype,`finished`,void 0),a([t()],Z.prototype,`phase`,void 0),a([t()],Z.prototype,`status`,void 0),a([t()],Z.prototype,`figure`,void 0),a([t()],Z.prototype,`progress`,void 0),a([t()],Z.prototype,`streaming`,void 0),a([t()],Z.prototype,`partial`,void 0),a([t()],Z.prototype,`composer`,void 0),a([t()],Z.prototype,`error`,void 0),a([t()],Z.prototype,`notice`,void 0),a([t()],Z.prototype,`sources`,void 0),a([t()],Z.prototype,`open`,void 0),a([t()],Z.prototype,`toast`,void 0),a([t()],Z.prototype,`chips`,void 0),a([_(`qse-wren-figure.wren-main`)],Z.prototype,`fig`,void 0),a([_(`qse-wren-figure.wren-fab-fig`)],Z.prototype,`fabFig`,void 0),a([_(`.wren-log`)],Z.prototype,`log`,void 0),a([_(`.wren-input`)],Z.prototype,`input`,void 0),Z=a([l(`qse-wren`)],Z);var Q=ve(`setup`),$=class extends d{constructor(...e){super(...e),this.path=[],this.wiz=Je(),this.storageProblem=null,this.storageFailed=!1,this.list={state:`loading`},this.model=null,this.device=null,this.serverEst=null,this.estLocal=!1,this.plan=null,this.confirmOpen=!1,this.applying=!1,this.removeAfter=!1,this.applyError=null,this.jobInitial=null,this.previousProfile=null,this.resetOpen=!1,this.unauthorized=!1,this.winNarrow=!1,this.slotsDraft=null,this.winDraft={window:null},this.nextRefused=!1,this.communitySyncing=!1,this.store=null,this.devPoll=null,this.estTimer=null,this.estCtrl=null,this.planTimer=null,this.planCtrl=null,this.commCtrl=null,this.warnedDiff=!1,this.touchedAt=new Map,this.loadingModel=null,this.modelSeq=0,this.onUnauthorized=()=>{this.unauthorized=!0},this.onSignedIn=()=>{this.unauthorized=!1,this.list.state!==`ready`&&this.loadList();let e=this.path[0];e&&this.model?.state!==`ready`&&this.loadModel(e),this.loadDevice(),this.wiz.selection&&this.queueEstimate(),this.wiz.step===`review`&&this.queuePlan(),this.wiz.step===`weights`&&this.syncCommunity()},this.onReset=()=>{this.wiz=Je(),this.plan=null,this.jobInitial=null,location.hash!==Q&&(location.hash=Q)},this.host={pageState:()=>Ie(this.m,this.wiz.step,this.wiz.selection,this.currentEstimate(),{errors:this.visibleErrors()}),execute:(e,t)=>this.executeTool(e,t),saveChat:e=>this.dispatch({type:`chat`,chat:e}),memAvailable:()=>this.device?.mem_available_gb??null}}connectedCallback(){super.connectedCallback(),this.store=He();let e=Ye(this.store);this.wiz=e.state,this.storageProblem=e.problem,(e.problem===`corrupt`||e.problem===`version`)&&Ve(this.store),this.loadList(),this.devPoll=ye(()=>this.loadDevice(),5e3),this.devPoll.start(),window.addEventListener(`qse-setup-reset`,this.onReset),window.addEventListener(`qse-setup-unauthorized`,this.onUnauthorized),window.addEventListener(`qse-signed-in`,this.onSignedIn)}disconnectedCallback(){this.devPoll?.stop(),this.estTimer&&clearTimeout(this.estTimer),this.planTimer&&clearTimeout(this.planTimer),this.estCtrl?.abort(),this.planCtrl?.abort(),this.commCtrl?.abort(),window.removeEventListener(`qse-setup-reset`,this.onReset),window.removeEventListener(`qse-setup-unauthorized`,this.onUnauthorized),window.removeEventListener(`qse-signed-in`,this.onSignedIn),super.disconnectedCallback()}async loadList(){this.list={state:`loading`};try{let e=await b.models();this.list=e.models.length||e.errors.length?{state:`ready`,data:e}:{state:`empty`,note:O.modelsEmpty}}catch(e){let t=w(e);this.list={state:`error`,endpoint:t.endpoint,message:t.status===401?O.unauthorized:t.message}}}async loadModel(e){if(this.loadingModel===e)return;this.loadingModel=e;let t=++this.modelSeq;this.model={state:`loading`};try{let n=v(await b.model(e));if(t!==this.modelSeq)return;this.model={state:`ready`,data:n},await this.openModel(n,this.routeStep()??void 0)}catch(n){if(t!==this.modelSeq)return;let r=w(n);this.model={state:`error`,endpoint:r.endpoint,message:r.status===404?`${O.modelsError} (${e})`:r.message}}finally{t===this.modelSeq&&(this.loadingModel=null)}}async loadDevice(){try{this.device=await b.device()}catch{}}async openModel(e,t){let n=null;if(!(this.wiz.model===e.id&&this.wiz.selection))try{n=(await b.current(e.id)).selection}catch{n=null}this.model?.state===`ready`&&this.model.data.id===e.id&&(this.dispatch({type:`open`,model:e,step:t,seed:n},!1),this.wiz.step===`weights`&&this.syncCommunity())}get m(){return this.model?.state===`ready`?this.model.data:null}routeStep(){let e=this.path[1];return y(e)?e:null}willUpdate(e){e.has(`path`)&&this.syncFromRoute()}syncFromRoute(){let[e,t]=this.path;if(!e){this.wiz.step!==`models`&&(this.wiz={...this.wiz,step:`models`});return}let n=this.m;if(!n||n.id!==e){this.loadModel(e);return}if(this.wiz.model!==e||!this.wiz.selection){this.openModel(n,y(t)?t:void 0);return}let r=y(t)?t:`overview`;r===`install`&&!this.wiz.job&&(r=`review`),r===`models`&&(r=`overview`),r!==this.wiz.step&&this.dispatch({type:`go`,step:r},!1)}hashFor(e){return e.step===`models`||!e.model?Q:`${Q}/${e.model}/${e.step}`}dispatch(e,t=!0){let n=Ke(this.wiz,e,this.m);if(n===this.wiz)return;let r=n.step!==this.wiz.step;this.wiz=n,this.store&&(this.storageFailed=!Ue(this.store,n));let i=this.hashFor(n);location.hash!==i&&(t?location.hash=i:history.replaceState(null,``,i)),(r||e.type===`recommended`||e.type===`parallel`&&e.key===`slots`)&&(this.slotsDraft=null,this.nextRefused=!1),(r||e.type===`recommended`||e.type===`profile`)&&(this.winDraft={window:null}),r&&(t&&window.scrollTo({top:0,behavior:Ce()?`auto`:`smooth`}),n.step===`review`&&this.queuePlan(),n.step===`weights`&&this.syncCommunity()),e.type!==`go`&&e.type!==`next`&&e.type!==`previous`&&e.type!==`chat`&&e.type!==`dismiss`&&(this.queueEstimate(),n.step===`review`&&this.queuePlan())}user(e,t,n){this.touchedAt.set(J(e,t),Date.now()),this.dispatch(n)}selKey(e){return JSON.stringify(e)}queueEstimate(){this.estTimer&&clearTimeout(this.estTimer),this.estTimer=setTimeout(()=>void this.confirmEstimate(),150)}async confirmEstimate(){let e=this.wiz.selection;if(!e)return;this.estCtrl?.abort();let t=this.estCtrl=new AbortController,n=this.selKey(e);try{let r=await b.estimate(e,t.signal);if(t.signal.aborted||n!==this.selKey(this.wiz.selection))return;this.serverEst={key:n,est:r},this.estLocal=!1;let i=this.m?Ne(this.m,e):null;i&&Math.abs(i.total_gb-r.total_gb)>.05&&!this.warnedDiff&&(this.warnedDiff=!0,console.warn(`setup: the server's estimate (${r.total_gb} GiB) and the page's (${i.total_gb} GiB) differ; showing the server's`))}catch(e){if(e.name===`AbortError`||t.signal.aborted)return;this.estLocal=!0}}currentEstimate(){let e=this.m,t=this.wiz.selection;if(!e||!t)return null;let n=Ne(e,t,this.device?.mem_available_gb??null),r=this.serverEst;return r&&r.key===this.selKey(t)&&(!n||Math.abs(r.est.total_gb-n.total_gb)>.05)?{...r.est,available_gb:this.device?.mem_available_gb??r.est.available_gb}:n}queuePlan(){this.planTimer&&clearTimeout(this.planTimer),this.planTimer=setTimeout(()=>void this.loadPlan(),200)}async loadPlan(){let e=this.wiz.selection;if(!e)return;this.planCtrl?.abort();let t=this.planCtrl=new AbortController;(!this.plan||this.plan.state!==`ready`)&&(this.plan={state:`loading`});try{let n=await b.plan(e,t.signal);t.signal.aborted||(this.plan={state:`ready`,data:n})}catch(e){if(e.name===`AbortError`||t.signal.aborted)return;let n=w(e);this.plan={state:`error`,endpoint:n.endpoint,message:n.message}}}async syncCommunity(){let e=this.m;if(!e)return;this.commCtrl?.abort();let t=this.commCtrl=new AbortController;this.communitySyncing=!0;try{let n=await b.communitySync(t.signal);if(t.signal.aborted||this.m?.id!==e.id)return;let r=this.m,i={...n.sync,dropped:n.sync.dropped.filter(t=>t.model===e.id),add_url:r.community_sync?.add_url||n.sync.add_url},a=v({...r,community_sync:i},n.models[e.id]??[]);this.model={state:`ready`,data:a};let o=this.wiz.selection;o&&!a.profiles.some(e=>e.id===o.profile)?this.dispatch({type:`open`,model:a},!1):this.queueEstimate()}catch(e){if(e.name===`AbortError`||t.signal.aborted)return;let n=this.m;n?.community_sync&&!(e instanceof T&&e.status===404)&&(this.model={state:`ready`,data:{...n,community_sync:{...n.community_sync,error:w(e).message,checked_at:new Date().toISOString()}}})}finally{t.signal.aborted||(this.communitySyncing=!1)}}visibleErrors(){let e=this.issues()[this.wiz.step]??[];return this.wiz.step!==`review`||this.plan?.state!==`ready`?e:[...e,...this.plan.data.errors.filter(t=>!e.includes(t))]}async executeTool(e,t){let n=_t(e,{model:this.m,selection:this.wiz.selection,step:this.wiz.step,touchedAt:this.touchedAt,turnStartedAt:t});if(!n.ok)return{ok:!1,result:n.error,summary:null,undo:null,stale:n.stale};if(n.kind===`read_device`)try{let e=await b.device();this.device=e;let t={mode:e.mode,mem_total_gb:e.mem_total_gb,mem_available_gb:e.mem_available_gb,gpu:e.gpu,disk:e.disk,engine:e.engine,active_profile:e.active_profile,profiles_on_disk:e.profiles_on_disk,password_set:e.password_set};return{ok:!0,result:JSON.stringify(t),summary:null,undo:null}}catch(e){return{ok:!1,result:w(e).message,summary:null,undo:null}}if(n.kind===`explain`)return n.target.step===this.wiz.step&&this.highlight(n.target.step,n.target.field),{ok:!0,result:n.result,summary:null,undo:null};let{target:r}=n;r.field&&r.step!==this.wiz.step&&this.dispatch({type:`go`,step:r.step});for(let e of n.actions)this.dispatch(e);await this.updateComplete,r.field&&this.highlight(r.step,r.field);let i=n.undo.length?()=>{for(let e of n.undo)this.dispatch(e);r.field&&this.highlight(r.step,r.field)}:null,a=r.field?()=>{r.step!==this.wiz.step&&this.dispatch({type:`go`,step:r.step}),this.highlight(r.step,r.field)}:null;return{ok:!0,result:n.result,summary:n.actions.length?n.summary:null,undo:i,reveal:a}}async highlight(e,t){if(!t)return;await this.updateComplete;let n=this.querySelector(`[data-field="${CSS.escape(J(e,t))}"]`);if(!n)return;let i=n.getBoundingClientRect();(i.top<120||i.bottom>innerHeight-80)&&n.scrollIntoView({block:`center`,behavior:Ce()?`auto`:`smooth`}),n.setAttribute(`data-wren-acting`,``),n.querySelector(`:scope > .wren-mini`)?.remove();let a=document.createElement(`span`);a.className=`wren-mini`,a.setAttribute(`aria-hidden`,`true`),n.append(a),r(Oe,a),setTimeout(()=>{n.removeAttribute(`data-wren-acting`),a.remove()},1600)}issues(){let e=this.m,t=this.wiz.selection;return!e||!t?{}:we(e,t,this.device,this.currentEstimate())}finishBlockers(){let e=this.m,t=this.wiz.selection;if(!e||!t)return[];let n=[],r=this.currentEstimate();r&&!r.fits&&n.push(O.reviewOver(je(r))),j(e,t,this.device)&&n.push(O.blockedToken);let i=Object.keys(this.issues()).filter(e=>e!==`review`);return i.length&&n.push(O.blockedInvalid(i.map(e=>E[e].toLowerCase()))),this.plan?.state===`ready`&&!this.plan.data.ok&&!n.length&&n.push(...this.plan.data.errors),n}async finish(){let e=this.wiz.selection;if(!e||this.applying||this.finishBlockers().length)return;this.applying=!0,this.applyError=null,this.previousProfile=this.device?.active_profile??null,this.querySelector(`qse-wren`)?.sleepForInstall();let t=e;try{let e=this.device?.active_profile,n=await b.apply({selection:t,...this.removeAfter&&e&&e!==t.profile?{remove_after:[e]}:{}});if(this.confirmOpen=!1,!this.isConnected||this.path[0]!==t.model||this.wiz.step!==`review`){xe(n.job);return}this.jobInitial=n.job,this.dispatch({type:`job`,id:n.job.id})}catch(e){this.applyError=this.applyErrorText(e)}finally{this.applying=!1}}applyErrorText(e){let t=w(e);if(t.status===401)return null;let n=e instanceof T?e.body:void 0;return n?.plan?(this.plan={state:`ready`,data:n.plan},this.confirmOpen=!1,n.plan.errors.join(` `)||t.message):e instanceof T&&e.type===`job_running`?O.blockedBusy:e instanceof T&&e.type===`busy`?O.blockedRequests(n?.error?.running??1):`${O.installError} ${t.message}`}render(){let t=this.wiz.step,n=this.m,r=this.path.length>0;return e`<div class="view view-setup" data-step=${r?t:`models`}>
      <div class="setup">
        <div class="setup-main">
          ${this.renderBanners()}
          ${this.unauthorized?e`<qse-setup-signin></qse-setup-signin>`:r?this.renderModelArea():this.renderTiles()}
          <footer class="setup-foot">
            <button class="btn btn-ghost btn-sm" aria-expanded=${this.resetOpen} aria-controls="setup-reset" @click=${()=>this.resetOpen=!this.resetOpen}>${i(`trash`)} ${O.resetLink}</button>
          </footer>
          ${this.resetOpen?e`<section class="panel setup-reset" id="setup-reset"><header class="panel-head"><h2 class="panel-title">${O.resetHeading}</h2></header><qse-reset inSetup></qse-reset></section>`:o}
        </div>
        <qse-wren class="setup-wren" .host=${this.host} .step=${r?t:`models`} .chat=${this.wiz.chat} .finished=${t===`install`&&!!this.wiz.job}></qse-wren>
      </div>
      ${n?this.renderConfirm(n):o}
    </div>`}renderBanners(){let t=this.storageProblem,n=[];return(t===`unavailable`||this.storageFailed)&&n.push(O.storageOff),t===`corrupt`&&n.push(O.storageCorrupt),t===`version`&&n.push(O.storageOld),this.wiz.notice&&this.wiz.step!==`components`&&this.wiz.step!==`parallelism`&&n.push(this.wiz.notice),e`${n.map(t=>e`<div class="banner setup-banner" role="status" data-setup-notice>${i(`info`)} <span>${t}</span></div>`)}`}renderTiles(){let t=this.list,n=this.device,r=this.wiz.model&&this.wiz.selection?this.wiz:null;return e`<section class="setup-tiles">
      <header class="setup-hero">
        <h1 class="setup-h1">${E.models}</h1>
        <p class="setup-lead">${k.models}</p>
        ${n?.mem_total_gb==null?o:e`<p class="setup-device muted num" data-device-line>${O.deviceLine(n.mem_total_gb,n.mem_available_gb)}</p>`}
      </header>
      ${t.state===`loading`?c(4):o}
      ${t.state===`error`?u(t.endpoint,`${O.modelsError} ${t.message}`,()=>void this.loadList()):o}
      ${t.state===`empty`?s(O.modelsEmpty):o}
      ${t.state===`ready`?e`<div class="mtiles">
              ${t.data.models.map(t=>{let n=r?.model===t.id,i=n?`${Q}/${t.id}/${r.step===`models`?`overview`:r.step}`:`${Q}/${t.id}/overview`;return e`<a class="mtile ${t.recommended?`is-rec`:``}" href=${i} data-model=${t.id}>
                  <span class="mtile-top"><span class="mtile-family">${t.family}</span>${t.recommended?e`<span class="sbadge sbadge-rec">${O.tileRecommended}</span>`:o}</span>
                  <span class="mtile-name">${t.name}</span>
                  <span class="mtile-params">${O.tileParams(t.params)}</span>
                  <span class="mtile-pitch">${t.pitch}</span>
                  ${t.community_count?e`<span class="mtile-comm">${O.community.tileCount(t.community_count)}</span>`:o}
                  ${t.best_speed?e`<span class="mtile-speed" title=${O.tileSpeedTitle}><span class="mtile-speed-n num">${t.best_speed.tok_s.toFixed(1)}</span><span class="mtile-speed-u">tok/s</span><span class="mtile-speed-note">${t.best_speed.task}, ${t.best_speed.profile}</span></span>`:o}
                  <span class="mtile-go"><span class="btn btn-primary">${n?`${O.tileOpen}: ${D[r.step]}`:O.tileOpen}</span></span>
                </a>`})}
            </div>
            ${t.data.errors.length?e`<div class="setup-load-errors" role="status"><p>${O.modelLoadErrors(t.data.errors.length)}</p><ul>${t.data.errors.map(t=>e`<li><code>${t.path}</code> <span class="muted">${t.message}</span></li>`)}</ul></div>`:o}`:o}
      <section class="guide" aria-labelledby="guide-h">
        <h2 class="guide-h" id="guide-h">${O.guideSteps}</h2>
        <ol class="guide-list">${x.slice(1).map(t=>e`<li><span class="guide-t">${D[t]}</span><span class="guide-d">${Se[t]}</span></li>`)}</ol>
      </section>
    </section>`}renderModelArea(){let t=this.model;if(this.wiz.step===`install`&&this.wiz.job&&t?.state!==`ready`)return e`<section class="setup-step" data-step="install" aria-labelledby="setup-step-title">
        <header class="step-head"><h1 class="step-title" id="setup-step-title" tabindex="-1">${E.install}</h1></header>
        ${this.stepInstall()}
      </section>`;if(!t||t.state===`loading`)return c(8);if(t.state===`error`)return u(t.endpoint,t.message,()=>void this.loadModel(this.path[0]));if(t.state!==`ready`||!this.wiz.selection)return c(8);let n=t.data,r=this.wiz.selection,i=this.currentEstimate(),a=this.wiz.step,s=i&&!i.fits?Pe(n,r,i):null;return e`
      <div class="setup-sticky">
        <qse-memory-bar .model=${n} .estimate=${i} .available=${this.device?.mem_available_gb??null} .local=${this.estLocal} .overText=${s}></qse-memory-bar>
        ${this.estLocal?e`<p class="setup-local" role="status" data-local-estimate>${O.memLocal}</p>`:o}
      </div>
      ${this.renderRail(n)}
      <section class="setup-step" data-step=${a} aria-labelledby="setup-step-title">
        <header class="step-head">
          <p class="step-kicker muted num">${O.stepOf(C(a)+1,x.length)}</p>
          <h1 class="step-title" id="setup-step-title" tabindex="-1">${E[a]}</h1>
          <p class="step-intro">${k[a]}</p>
        </header>
        ${this.renderStep(n,r,i)}
      </section>
      ${this.renderNav()}
    `}renderRail(t){let n=this.wiz.step,r=this.issues(),a=C(n);return e`<nav class="rail-steps" aria-label="Setup steps">
      <div class="rail-phone" aria-hidden="true">
        <span class="num">${O.stepOf(a+1,x.length)}</span><span class="rail-phone-label">${E[n]}</span>
        <span class="rail-phone-bar"><span style="width:${(a+1)/x.length*100}%"></span></span>
      </div>
      <ol class="steps">
        ${x.map((s,c)=>{let l=this.wiz.visited.includes(s),u=l&&(r[s]?.length??0)>0,d=s===n,f=l&&!u&&c<a,p=s!==`install`||!!this.wiz.job,m=s===`models`?Q:`${Q}/${t.id}/${s}`;return e`<li class="steps-item ${d?`is-current`:``} ${f?`is-done`:``} ${u?`is-bad`:``} ${l?`is-visited`:``}" data-rail=${s}>
            ${p?e`<a href=${m} aria-current=${d?`step`:o} title=${u?r[s].join(` `):E[s]}>
                  <span class="steps-node num" aria-hidden="true">${u?`!`:f?i(`check`):c+1}</span>
                  <span class="steps-label">${D[s]}</span>
                  ${u?e`<span class="sr-only">(needs a fix)</span>`:o}
                </a>`:e`<span class="steps-link is-locked" aria-disabled="true"><span class="steps-node num" aria-hidden="true">${c+1}</span><span class="steps-label">${D[s]}</span></span>`}
          </li>`})}
      </ol>
    </nav>`}renderNav(){let t=this.wiz.step;if(t===`install`)return o;let n=C(t),r=this.nextRefused?this.nextBlocker():null;return e`<nav class="setup-nav ${r?`has-why`:``}" aria-label="Step navigation">
      ${r?e`<p class="setup-nav-why" id="setup-nav-why" role="alert" data-next-refused>${i(`info`)} <span>${O.nextRefused(r)}</span></p>`:o}
      <button class="btn setup-prev" @click=${()=>n<=1?this.dispatch({type:`tiles`}):this.dispatch({type:`previous`})}>${O.previous}</button>
      <span class="setup-nav-mid muted small num">${O.stepOf(n+1,x.length)}</span>
      ${t===`review`?e`<button class="btn btn-primary setup-finish" ?disabled=${this.finishBlockers().length>0||this.plan?.state!==`ready`} @click=${()=>this.confirmOpen=!0}>${O.finish}</button>`:e`<button class="btn btn-primary setup-next" aria-describedby=${r?`setup-nav-why`:o} @click=${()=>this.next()}><span>${O.next}<span class="setup-next-to">: ${D[x[n+1]]}</span></span></button>`}
    </nav>`}nextBlocker(){let e=this.m,t=this.wiz.selection;if(this.wiz.step!==`parallelism`||!e||!t)return null;let n=Te(Le(e,t),this.slotsParse(e));if(n)return n;let r=this.winParse(e,t,`window`);return r&&!r.ok?`${O.window}: ${r.why}`:null}winParse(e,t,n){let r=this.winDraft[n];return r==null?null:Xe(r,Ge(e,t,n))}slotsParse(e){return this.slotsDraft==null?null:Fe(this.slotsDraft,Ae(e))}next(){if(this.nextBlocker()){this.nextRefused=!0,this.querySelector(`[data-field^="parallelism."].is-bad input`)?.focus();return}this.nextRefused=!1,this.dispatch({type:`next`})}renderStep(t,n,r){switch(this.wiz.step){case`overview`:return this.stepOverview(t,n);case`weights`:return this.stepWeights(t,n);case`components`:return this.stepComponents(t,n);case`parallelism`:return this.stepParallel(t,n,r);case`methods`:return this.stepMethods(t,n);case`review`:return this.stepReview(t,n,r);case`install`:return this.stepInstall();default:return e``}}stepOverview(t,n){let r=t.profiles.find(e=>e.id===n.profile),a=Ze(t,n.profile);return e`<div class="ov">
      <div class="ov-facts">
        <p class="ov-arch">${t.architecture}</p>
        <dl class="ov-dl">
          <div><dt>${O.colWeights}</dt><dd>${t.profiles.length} sizes, ${t.profiles.map(e=>e.label.replace(/ bpw$/,``)).join(`, `)}</dd></div>
          <div><dt>${O.window}</dt><dd class="num">${r?.max_window===null?O.windowNone(r.label):`${a.toLocaleString(`en-US`)} ${O.tokensUnit}`}</dd></div>
          <div><dt>${O.license}</dt><dd>${t.license}</dd></div>
        </dl>
        <ul class="ov-links">${t.links.map(t=>e`<li><a href=${t.url} target="_blank" rel="noopener noreferrer">${t.label} ${i(`external`)}</a></li>`)}</ul>
      </div>
      <section class="panel ov-table" aria-label="Measured speed">
        <qse-measure-table .model=${t} .selected=${n.profile}></qse-measure-table>
      </section>
      ${t.parallel?.rows.length?e`<section class="panel ov-table" aria-label="Speed on this build"><qse-parallel-table .model=${t} .selected=${n.profile}></qse-parallel-table></section>`:o}
    </div>`}stepWeights(t,n){let r=this.device,a=j(t,n,r),s=t.measurements.rows,c=Math.max(...s.filter(e=>e.comparable).map(e=>e.cells.greedy_512?.code?.tok_s??0));return e`
      ${a?e`<div class="banner banner-offline" role="alert" data-token-blocked>${i(`warn`)} ${O.privateNoToken}</div>`:o}
      ${!a&&r?.hf_token_present&&t.profiles.some(e=>e.hf_private&&!r.profiles_on_disk?.[e.id])?e`<p class="wnote muted small">${i(`info`)} ${O.privateRepo}</p>`:o}
      <div class="wcards" role="radiogroup" aria-label=${E.weights}>
        ${he(t).map(a=>{let l=n.profile===a.id,u=s.find(e=>e.profile===a.id),d=u?.cells.greedy_512,f=r?.profiles_on_disk?.[a.id],p=r?.active_profile===a.id,m=a.quality.top1_pct;return e`<label class="wcard ${l?`is-selected`:``} ${a.recommended?`is-rec`:``}" data-profile-card=${a.id} data-field=${l?`weights.profile`:o}>
            <input class="sr-only" type="radio" name="profile" .checked=${l} value=${a.id} @change=${()=>this.user(`weights`,`profile`,{type:`profile`,id:a.id})} />
            <span class="wcard-head">
              <span class="wcard-radio" aria-hidden="true"></span>
              <span class="wcard-name">${a.label}</span>
              ${p?e`<span class="sbadge sbadge-now">${O.activeNow}</span>`:o}
            </span>
            ${a.badges.length?e`<span class="wcard-badges">${a.badges.map(t=>e`<span class="sbadge ${t===`recommended`?`sbadge-rec`:``}">${t===`recommended`?O.recommended:t}</span>`)}</span>`:o}
            <span class="wcard-meters">
              <span class="wmeter" title=${a.quality.source}>
                <span class="wmeter-k">${O.weightsTop1}</span>
                <span class="wmeter-v num">${m==null?`–`:`${m.toFixed(1)} %`}</span>
                <span class="wmeter-bar q"><span style="width:${m==null?0:Math.max(4,(m-80)/20*100)}%"></span></span>
              </span>
              <span class="wmeter">
                <span class="wmeter-k">${O.weightsSpeed}</span>
                <span class="wmeter-v num">${d?.prose&&d?.code?e`${d.prose.tok_s.toFixed(1)} / ${d.code.tok_s.toFixed(1)}`:e`<span class="muted">${u&&!u.comparable?O.notComparable:O.notMeasured}</span>`}</span>
                <span class="wmeter-bar s"><span style="width:${d?.code&&u?.comparable?d.code.tok_s/c*100:0}%"></span></span>
              </span>
            </span>
            <span class="wcard-facts num">
              <span><span class="wcard-k">${O.weightsKl}</span> ${a.quality.kl_vs_bf16==null?`–`:a.quality.kl_vs_bf16.toFixed(4)}</span>
              <span><span class="wcard-k">${O.weightsMemory}</span> ${a.weights_gib.toFixed(1)} GiB</span>
              <span><span class="wcard-k">${O.weightsDisk}</span> ${f?O.onDisk:O.toDownload(a.disk_gb)}</span>
              ${R(t,a.id)?e`<span data-parallel-line><span class="wcard-k">${O.weightsAtOnce}</span> ${R(t,a.id)}</span>`:o}
            </span>
            ${this.renderSource(a)}
            ${a.note?e`<span class="wcard-note">${a.note}</span>`:o}
            ${a.hf_private&&!f&&r?.hf_token_present===!1?e`<span class="wcard-private is-blocked" data-private>${i(`warn`)} ${O.privateNoTokenShort}</span>`:o}
          </label>`})}
      </div>
      ${le(t).length||t.community?e`<qse-community .model=${t} .sync=${t.community_sync??null} .selected=${n.profile} .device=${r} .syncing=${this.communitySyncing} .onUse=${e=>this.user(`weights`,`profile`,{type:`profile`,id:e})} .onRefresh=${()=>void this.syncCommunity()}></qse-community>`:o}
      <qse-installed-weights @weights-removed=${()=>void this.loadDevice()}></qse-installed-weights>`}renderSource(t){let n=me(t);return e`<span class="wcard-src" data-weights-src=${n.kind}>
      <span class="sbadge wsrc-kind wsrc-${n.kind}">${n.label}</span>
      <span class="wsrc-by">${O.madeBy} ${n.byUrl?e`<a href=${n.byUrl} target="_blank" rel="noopener noreferrer" title=${O.community.github(n.by.slice(1))}>${n.by}</a>`:n.by}</span>
      ${n.repoUrl?e`<a class="wsrc-repo" href=${n.repoUrl} target="_blank" rel="noopener noreferrer" title=${O.community.onHf(n.repo)}>${n.repo} ${i(`external`)}</a>`:e`<span class="wsrc-repo">${n.repo}</span>`}
    </span>`}stepComponents(t,n){let r=h(t,n.components);r.vision&&ne(t,n.profile)&&(r.vision={disabled:!0,reason:O.community.visionOff,required:!1});let a=[...new Set(t.components.map(e=>e.group))],s=A(t,n.profile);return e`
      <div class="step-tools">
        <button class="btn btn-sm" @click=${()=>this.dispatch({type:`recommended`,scope:`components`})}>${i(`refresh`)} ${O.resetToRecommended}</button>
      </div>
      <p class="comp-notice" role="status" aria-live="polite" data-cascade>${this.wiz.notice??``}</p>
      ${a.map(a=>e`<section class="comp-group" aria-labelledby=${`cg-${a}`}>
          <h2 class="comp-group-title" id=${`cg-${a}`}>${O.groups[a]??a}</h2>
          <ul class="comp-list">
            ${t.components.filter(e=>e.group===a).map(t=>{let a=m(t,n.components),c=r[t.id],l=t.id===`drafters`&&s.drafters_gib!=null?s.drafters_gib:t.memory_gib;return e`<li>
                  <label class="comp ${a?`is-on`:``} ${c.disabled?`is-disabled`:``} ${c.required?`is-required`:``}" data-field=${`components.${t.id}`} data-component=${t.id}>
                    <input type="checkbox" class="comp-input" .checked=${a} ?disabled=${c.disabled} aria-describedby=${`cw-${t.id}${c.reason?` cr-${t.id}`:``}`} @change=${e=>this.user(`components`,t.id,{type:`component`,id:t.id,on:e.target.checked})} />
                    <span class="comp-box" aria-hidden="true">${i(`check`)}</span>
                    <span class="comp-main">
                      <span class="comp-label">${t.label}${t.default&&!t.required?e` <span class="comp-rec">${O.recommendedValue}</span>`:o}</span>
                      <span class="comp-what" id=${`cw-${t.id}`}>${t.what}</span>
                      ${c.reason?e`<span class="comp-reason" id=${`cr-${t.id}`} data-reason>${c.reason}</span>`:o}
                    </span>
                    <span class="comp-cost num">
                      <span class="comp-mem">${O.memoryCost(l)}</span>
                      ${t.memory_max_gib==null?o:e`<span class="muted">${O.memoryUpTo(t.memory_max_gib)}</span>`}
                      ${t.disk_gb>0?e`<span class="muted">${O.diskCost(t.disk_gb)}</span>`:o}
                    </span>
                  </label>
                </li>`})}
          </ul>
        </section>`)}`}stepParallel(t,n,r){let a=t.parallelism,s=r?.context_tokens_fit??0,c=Math.floor(s/n.slots),l=(e,t)=>this.user(`parallelism`,e,{type:`parallel`,key:e,value:t});return e`<div class="spar">
      <div class="spar-fields">
        ${this.slotsField(t,n)}
        ${this.windowField(t,n,`window`)}
        <div class="pfield" data-field="parallelism.kv_pool">
          <div class="pfield-k"><span class="pfield-label" id="pf-pool">${O.kvPool}</span><span class="pfield-what">${O.kvPoolWhat}</span></div>
          <label class="switch"><input type="checkbox" role="switch" .checked=${n.kv_pool} aria-labelledby="pf-pool" @change=${e=>l(`kv_pool`,e.target.checked)} /><span>${n.kv_pool?`on`:`off`}</span></label>
        </div>
        <div class="pfield" data-field="parallelism.floor_gb">
          <div class="pfield-k"><span class="pfield-label" id="pf-floor">${O.floor}</span><span class="pfield-what">${O.floorWhat}</span></div>
          <div class="floor-ctl">
            <input type="range" min=${a.floor_gb.min} max=${a.floor_gb.max} step="1" .value=${String(n.floor_gb)} aria-labelledby="pf-floor" @input=${e=>l(`floor_gb`,Number(e.target.value))} />
            <output class="num">${n.floor_gb} GiB</output>
          </div>
        </div>
        <div class="step-tools"><button class="btn btn-sm" @click=${()=>this.dispatch({type:`recommended`,scope:`parallelism`})}>${i(`refresh`)} ${O.resetToRecommended}</button></div>
        ${this.wiz.notice?e`<p class="comp-notice" role="status">${this.wiz.notice}</p>`:o}
      </div>
      <aside class="spar-readout panel" aria-live="polite" data-fit-tokens=${s}>
        <p class="spar-big num">${s.toLocaleString(`en-US`)}</p>
        <p class="spar-big-u">${O.fitsUnit}</p>
        <div class="spar-slots" aria-hidden="true">
          ${[{n:n.slots>1?`1–${n.slots}`:`1`,w:n.window,on:!0}].map(t=>e`<div class="spar-slot ${t.on?``:`is-off`}">
              <span class="spar-slot-n num">${t.n}</span>
              <span class="spar-slot-track"><span class="spar-slot-win" style="width:${t.w/a.window.max*100}%"></span><span class="spar-slot-fit" style="width:${Math.min(t.w,c)/a.window.max*100}%"></span></span>
              <span class="spar-slot-k num">${qe(t.w)}</span>
            </div>`)}
        </div>
        <p class="small">${O.fitsPerSlot(c,n.slots)}</p>
        ${r&&n.slots>1?e`<p class="small" data-lanes-full=${r.lanes_full}>${O.lanesFull(r.lanes_full,n.slots,n.window,r.lane_full_gb,n.kv_pool)}</p>`:o}
        ${r?e`<p class="small muted">${O.worstCase(r.kv_worst_gb,r.fits_worst??!1)}</p>`:o}
      </aside>
    </div>`}slotsField(t,n){let r=Ae(t),i=Le(t,n),a=this.slotsParse(t),s=a&&!a.ok?a.why:null,c=s??(i.over?O.slotsOver(i.n,i.overGb,i.fitMax):null),l=e=>this.user(`parallelism`,`slots`,{type:`parallel`,key:`slots`,value:e}),u=e=>{let t=a?.ok?a.n:n.slots,i=Ee(t,e,r);i===n.slots?this.slotsDraft=null:l(i)},d=e=>{let t=e.target.value,i=Fe(t,r);i.ok?(this.slotsDraft=null,i.n!==n.slots&&l(i.n)):this.slotsDraft=t},f=e=>{(e.key===`ArrowUp`||e.key===`ArrowDown`)&&(e.preventDefault(),u(e.key===`ArrowUp`?1:-1))},p=this.slotsDraft??String(n.slots),m=n.slots>=r.max&&!s;return e`<div class="pfield pfield-slots ${c?`is-bad`:``}" data-field="parallelism.slots">
      <div class="pfield-k"><label class="pfield-label" id="pf-slots" for="pf-slots-in">${O.slots}</label><span class="pfield-what">${O.slotsWhat}</span></div>
      <div class="stepper ${c?`is-bad`:``}" role="group" aria-labelledby="pf-slots">
        <button class="stepper-btn" type="button" aria-label=${O.slotsFewer} aria-controls="pf-slots-in" ?disabled=${n.slots<=r.min&&!s} @click=${()=>u(-1)}>
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
          aria-valuemin=${r.min}
          aria-valuemax=${r.max}
          aria-valuenow=${a&&!a.ok?o:n.slots}
          aria-valuetext=${a&&!a.ok?o:`${n.slots} ${n.slots===1?`chat`:`chats`}`}
          aria-invalid=${c?`true`:`false`}
          aria-describedby="pf-slots-say"
          maxlength="3"
          .value=${p}
          @input=${d}
          @keydown=${f}
          @focus=${e=>e.target.select()}
        />
        <button class="stepper-btn" type="button" aria-label=${O.slotsMore} aria-controls="pf-slots-in" ?disabled=${m} title=${m?O.slotsAtMax(r.max):o} @click=${()=>u(1)}>
          <svg viewBox="0 0 16 16" aria-hidden="true"><path d="M3.5 8h9M8 3.5v9" /></svg>
        </button>
      </div>
      <div class="slots-say" id="pf-slots-say">
        ${c?e`<p class="slots-err" role="alert" data-slots-error>${c}</p>`:o}
        ${r.max>1?e`<p class="slots-cost" data-slots-cost>${O.slotsCost(i.cost.now,i.cost.full,i.cost.window)}</p>`:o}
        ${i.untested&&!s?e`<p class="slots-note" data-slots-untested>${O.slotsUntested(r.measured)}</p>`:o}
        ${i.passes>1&&!s?e`<p class="slots-note" data-slots-passes>${O.slotsPasses(i.passes)}</p>`:o}
        ${t.parallelism.slots.engine_max_reason?e`<p class="slots-note" data-slots-engine>${t.parallelism.slots.engine_max_reason}</p>`:o}
      </div>
    </div>`}windowField(t,n,r){let i=Ge(t,n,r),a=n.window,s=O.window,c=`pf-win`,l=this.winParse(t,n,r),u=l&&!l.ok?l.why:null,d=l?.ok?l.n:a,f=e=>this.user(`parallelism`,r,{type:`parallel`,key:r,value:e}),p=e=>{this.winDraft={...this.winDraft,[r]:null},e!==a&&f(e)},m=e=>{let t=e.target.value;this.winDraft={...this.winDraft,[r]:t};let n=Xe(t,i);n.ok&&n.n!==a&&f(n.n)},h=e=>{let t=e.key;if(t===`ArrowUp`||t===`ArrowDown`)p(M(d,t===`ArrowUp`?1:-1,i));else if(t===`PageUp`||t===`PageDown`){let e=t===`PageUp`?1:-1;p(M(M(d,e,i),e,i))}else if(t===`Home`)p(i.min);else if(t===`End`)p(i.max);else if(t===`Enter`)e.target.blur();else return;e.preventDefault()},g=()=>{l?.ok&&(this.winDraft={...this.winDraft,[r]:null})},_=this.winDraft[r]??a.toLocaleString(`en-US`),ee=d<=i.min&&!u,v=d>=i.max&&!u;return e`<div class="pfield pfield-win ${u?`is-bad`:``}" data-field=${`parallelism.${r}`}>
      <div class="pfield-k"><label class="pfield-label" id=${c} for=${`${c}-in`}>${s}</label><span class="pfield-what">${O.windowWhat}</span></div>
      <div class="win-ctl">
        <div class="stepper stepper-wide ${u?`is-bad`:``}" role="group" aria-labelledby=${c}>
          <button class="stepper-btn" type="button" aria-label=${O.windowShorter(s)} aria-controls=${`${c}-in`} ?disabled=${ee} @click=${()=>p(M(d,-1,i))}>
            <svg viewBox="0 0 16 16" aria-hidden="true"><path d="M3.5 8h9" /></svg>
          </button>
          <input
            id=${`${c}-in`}
            class="stepper-in num"
            type="text"
            inputmode="numeric"
            enterkeyhint="done"
            autocomplete="off"
            spellcheck="false"
            role="spinbutton"
            aria-valuemin=${i.min}
            aria-valuemax=${i.max}
            aria-valuenow=${u?o:d}
            aria-valuetext=${u?o:`${d.toLocaleString(`en-US`)} ${O.tokensUnit}`}
            aria-invalid=${u?`true`:`false`}
            aria-describedby=${`${c}-say`}
            maxlength="12"
            .value=${_}
            @input=${m}
            @keydown=${h}
            @blur=${g}
            @focus=${e=>e.target.select()}
          />
          <button class="stepper-btn" type="button" aria-label=${O.windowLonger(s)} aria-controls=${`${c}-in`} ?disabled=${v} title=${v?i.maxWhy:o} @click=${()=>p(M(d,1,i))}>
            <svg viewBox="0 0 16 16" aria-hidden="true"><path d="M3.5 8h9M8 3.5v9" /></svg>
          </button>
        </div>
        <span class="stepper-hint num" aria-hidden="true" data-win-short>${u?``:qe(d)}</span>
      </div>
      <div class="slots-say" id=${`${c}-say`}>
        ${u?e`<p class="slots-err" role="alert" data-win-error>${u}</p>`:o}
        ${!u&&v&&i.max<t.parallelism[r].max?e`<p class="slots-note" data-win-max>${i.maxWhy}</p>`:o}
      </div>
    </div>`}fieldControl(t,n,r){let i=e=>this.user(`methods`,n.id,{type:`field`,group:t,id:n.id,value:e}),a=`mf-${n.id}`;if(n.type===`enum`){let t=n.options??[];return t.length<=3?e`<div class="seg seg-wrap" role="radiogroup" aria-labelledby=${`${a}-l`}>${t.map(t=>e`<button class="seg-btn ${r===t.value?`is-on`:``}" role="radio" aria-checked=${r===t.value} @click=${()=>i(t.value)}>${t.label}</button>`)}</div>`:e`<select id=${a} class="sel" @change=${e=>i(e.target.value)}>${t.map(t=>e`<option value=${t.value} ?selected=${r===t.value}>${t.label}</option>`)}</select>`}return n.type===`bool`?e`<label class="switch"><input id=${a} type="checkbox" role="switch" .checked=${r===!0} @change=${e=>i(e.target.checked)} /><span>${r?`on`:`off`}</span></label>`:n.type===`int`||n.type===`number`?e`<input id=${a} class="num-input num" type="number" inputmode=${n.type===`int`?`numeric`:`decimal`} min=${n.min??o} max=${n.max??o} step=${n.type===`int`?1:.05} .value=${String(r)} @change=${e=>{let t=e.target.value;i(t===``?NaN:Number(t))}} />`:e`<input id=${a} class="text-input" type="text" .value=${String(r)} spellcheck="false" autocapitalize="off" @change=${e=>i(e.target.value)} />`}fieldRow(t,n,r){let i=r[t][n.id]??n.recommended,a=S(n,i),s=n.options?.find(e=>e.value===n.recommended)?.label??String(n.recommended);return e`<div class="mfield ${a?`is-bad`:``}" data-field=${`methods.${n.id}`}>
      <div class="pfield-k">
        <label class="pfield-label" id=${`mf-${n.id}-l`} for=${`mf-${n.id}`}>${n.label}</label>
        <span class="pfield-what">${n.what}</span>
        <span class="mfield-rec muted small">${O.recommendedValue}: <span class="num">${s}</span>${i===n.recommended?o:e` <button class="linkish" @click=${()=>this.user(`methods`,n.id,{type:`field`,group:t,id:n.id,value:n.recommended})}>${O.resetToRecommended.toLowerCase()}</button>`}</span>
      </div>
      <div class="mfield-ctl">${this.fieldControl(t,n,i)}${a?e`<p class="mfield-err" role="alert">${O.invalidValue(a)}</p>`:o}</div>
    </div>`}stepMethods(t,n){let r=t.advanced.some(e=>S(e,n.advanced[e.id]??e.recommended));return e`<div class="methods">
      ${t.methods.map(e=>this.fieldRow(`methods`,e,n))}
      <details class="adv" ?open=${r}>
        <summary><span class="adv-title">${O.advanced}</span> <span class="muted small">${O.advancedNote}</span></summary>
        ${t.advanced.map(e=>this.fieldRow(`advanced`,e,n))}
      </details>
      <div class="step-tools"><button class="btn btn-sm" @click=${()=>this.dispatch({type:`recommended`,scope:`methods`})}>${i(`refresh`)} ${O.resetToRecommended}</button></div>
    </div>`}stepReview(t,n,r){let a=this.plan,s=this.wiz.step,l=r&&!r.fits?Me(t,n):[],u=this.finishBlockers(),d=this.device,f=A(t,n.profile),p=a?.state===`ready`?a.data:null;return e`<div class="review">
      <section class="rv-card rv-memory ${r&&!r.fits?`is-over`:`is-ok`}" data-review-memory>
        <h2 class="rv-h">${O.reviewMemory}</h2>
        ${r?e`<p class="rv-say">${r.fits?O.reviewFits(r.total_gb,r.budget_gb??r.device_gb-r.floor_gb):Pe(t,n,r)}</p>`:o}
        ${l.length?e`<h3 class="rv-sub">${O.howToFix}</h3>
              <ul class="rv-fixes">${l.map(n=>e`<li><span>${n.text}</span><span class="muted small num">−${n.saves.toFixed(1)} GiB</span><a class="btn btn-sm" href=${`${Q}/${t.id}/${n.step}`}>${O.fixGo}: ${D[n.step]}</a></li>`)}</ul>`:o}
      </section>

      <section class="rv-card">
        <h2 class="rv-h">${O.reviewWeights}</h2>
        <p>${d?.active_profile===f.id?O.reviewWeightsSame(f.label):O.reviewWeightsSwitch(d?.active_profile?t.profiles.find(e=>e.id===d.active_profile)?.label??d.active_profile:null,f.label)}</p>
        ${p&&!p.profile.on_disk?e`<p class="muted">${O.reviewDownload(p.profile.download_gb)}</p>`:o}
        ${p?.downloads?.length?e`<ul class="rv-downloads" data-review-downloads>${p.downloads.map(t=>e`<li data-download=${t.id}><span>${t.label}</span> <span class="muted small">${t.for}</span> <span class="num">${t.gb.toFixed(2)} GB</span></li>`)}</ul>`:o}
        ${p?e`<p class="muted small">${p.restart?O.reviewRestart:O.reviewNoRestart}</p>`:o}
        ${d?.active_profile&&d.active_profile!==f.id&&d.profiles_on_disk?.[d.active_profile]?e`<label class="rv-remove-after" data-remove-after><input type="checkbox" .checked=${this.removeAfter} @change=${e=>this.removeAfter=e.target.checked} />
              <span>${O.removeAfter(t.profiles.find(e=>e.id===d.active_profile)?.label??d.active_profile)}</span></label>`:o}
      </section>

      <section class="rv-card rv-changes">
        <h2 class="rv-h">${O.reviewChanges}</h2>
        ${a==null||a.state===`loading`?c(4):o}
        ${a?.state===`error`?e`<p class="rv-warn" role="alert">${i(`warn`)} ${O.planError} <span class="muted">${a.message}</span> <button class="btn btn-sm" @click=${()=>void this.loadPlan()}>${O.retry}</button></p>`:o}
        ${p?p.changes.length?e`<div class="table-scroll"><table class="table rv-table">
                <thead><tr><th scope="col">${O.reviewKey}</th><th scope="col">${O.reviewNow}</th><th scope="col">${O.reviewAfter}</th><th scope="col">${O.reviewFrom}</th></tr></thead>
                <tbody>${p.changes.map(t=>e`<tr data-env=${t.key}><th scope="row"><code>${t.key}</code></th><td class="rv-old">${t.current??e`<span class="muted">${O.reviewUnset}</span>`}</td><td class="rv-new">${t.removed?e`<span class="rv-removed" data-removed>${We(t,O.reviewUnset).to}</span>`:t.next??e`<span class="muted">${O.reviewUnset}</span>`}</td><td class="muted small">${t.from}</td></tr>`)}</tbody>
              </table></div>`:e`<p class="muted">${O.reviewNoChanges}</p>`:o}
        ${p&&(p.errors.length||p.warnings.length)?e`<ul class="rv-notes">
              ${p.errors.map((n,r)=>{let a=p.error_codes?.[r],c=ke(a);return e`<li class="is-err" data-error-code=${a??o}>${i(`warn`)} <span>${n}</span>${c&&c!==s?e` <a class="btn btn-sm" href=${`${Q}/${t.id}/${c}`}>${O.planFix}: ${D[c]}</a>`:o}</li>`})}
              ${p.warnings.map(t=>e`<li>${i(`info`)} ${t}</li>`)}
            </ul>`:o}
      </section>

      ${p?.commands.length?e`<section class="rv-card">
            <h2 class="rv-h">${O.reviewCommands}</h2>
            <p class="muted small">${O.reviewCommandsNote}</p>
            <pre class="rv-cmds">${p.commands.join(`
`)}</pre>
          </section>`:o}

      ${d&&!d.password_set?this.securityCard():d?.password_set?e`<p class="rv-secure muted">${i(`check`)} ${O.securitySet}</p>`:o}

      ${u.length?e`<ul class="rv-blockers" role="status" data-finish-blocked>${u.map(t=>e`<li>${i(`warn`)} ${t}</li>`)}</ul>`:o}
      ${this.applyError?e`<p class="rv-warn" role="alert">${i(`warn`)} ${this.applyError}</p>`:o}
    </div>`}get sec(){return this.wiz.selection?.advanced.admin_password===`generate`?`generate`:`keep`}loginOff(){let e=this.m?.advanced.find(e=>e.id===`dashboard_login`);return!!e&&(this.wiz.selection?.advanced.dashboard_login??e.recommended)!==`on`}securityCard(){let t=(t,n,r)=>e`<label class="sec-opt ${this.sec===t?`is-on`:``}">
      <input type="radio" name="sec" .checked=${this.sec===t} @change=${()=>this.user(`methods`,`admin_password`,{type:`field`,group:`advanced`,id:`admin_password`,value:t})} />
      <span><span class="sec-opt-label">${n}</span>${r?e`<span class="muted small">${r}</span>`:o}</span>
    </label>`;return e`<section class="rv-card rv-security" data-security-warning role="note">
      <h2 class="rv-h">${i(`warn`)} ${O.securityTitle}</h2>
      <p>${O.securityBody}</p>
      <div class="sec-opts" role="radiogroup" aria-label=${O.securityChoice}>
        ${t(`keep`,O.securityKeep)} ${t(`generate`,O.securityGenerate,O.securityGenerateNote)}
      </div>
      ${this.sec===`generate`&&this.loginOff()?e`<p class="sec-login" data-login-off>${i(`info`)} <span>${O.securityLoginOff}</span>
            <button class="btn btn-sm" @click=${()=>this.user(`methods`,`dashboard_login`,{type:`field`,group:`advanced`,id:`dashboard_login`,value:`on`})}>${O.securityLoginOn}</button></p>`:o}
    </section>`}renderConfirm(t){let n=this.plan?.state!==`ready`||this.plan.data.restart;return e`<dialog
      class="setup-confirm"
      aria-labelledby="sc-t"
      aria-describedby="sc-b"
      @cancel=${e=>this.applying&&e.preventDefault()}
      @close=${()=>this.confirmOpen=!1}
    >
      ${this.confirmOpen?e`<h2 id="sc-t">${O.confirmTitle}</h2>
            <p id="sc-b">${O.confirmBody(n)}</p>
            <p class="muted small">${A(t,this.wiz.selection.profile).label}</p>
            ${this.applyError?e`<p class="rv-warn" role="alert">${this.applyError}</p>`:o}
            <div class="setup-confirm-foot">
              <button class="btn" ?disabled=${this.applying} @click=${()=>this.confirmOpen=!1}>${O.cancel}</button>
              <button class="btn btn-primary setup-confirm-go" autofocus ?disabled=${this.applying} @click=${()=>void this.finish()}>${this.applying?O.installStarting:O.confirmGo}</button>
            </div>`:o}
    </dialog>`}updated(){let e=this.querySelector(`dialog.setup-confirm`);e&&(this.confirmOpen&&!e.open?e.showModal():!this.confirmOpen&&e.open&&e.close())}stepInstall(){let t=this.wiz.job;return t?e`<qse-install-progress .jobId=${t} .initial=${this.jobInitial} .previousProfile=${this.previousProfile} .onBack=${()=>this.dispatch({type:`job`,id:null})} @job-retry=${e=>{this.jobInitial=e.detail,this.dispatch({type:`job`,id:e.detail.id})}}></qse-install-progress>`:e`<p class="muted">${O.installStarting}</p>`}};a([n({attribute:!1})],$.prototype,`path`,void 0),a([t()],$.prototype,`wiz`,void 0),a([t()],$.prototype,`storageProblem`,void 0),a([t()],$.prototype,`storageFailed`,void 0),a([t()],$.prototype,`list`,void 0),a([t()],$.prototype,`model`,void 0),a([t()],$.prototype,`device`,void 0),a([t()],$.prototype,`serverEst`,void 0),a([t()],$.prototype,`estLocal`,void 0),a([t()],$.prototype,`plan`,void 0),a([t()],$.prototype,`confirmOpen`,void 0),a([t()],$.prototype,`applying`,void 0),a([t()],$.prototype,`removeAfter`,void 0),a([t()],$.prototype,`applyError`,void 0),a([t()],$.prototype,`jobInitial`,void 0),a([t()],$.prototype,`previousProfile`,void 0),a([t()],$.prototype,`resetOpen`,void 0),a([t()],$.prototype,`unauthorized`,void 0),a([t()],$.prototype,`winNarrow`,void 0),a([t()],$.prototype,`slotsDraft`,void 0),a([t()],$.prototype,`winDraft`,void 0),a([t()],$.prototype,`nextRefused`,void 0),a([t()],$.prototype,`communitySyncing`,void 0),$=a([l(`qse-setup`)],$);export{$ as QseSetup};