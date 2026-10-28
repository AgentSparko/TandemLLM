import{c as e,d as t,f as n,g as r,l as i,p as a,v as o}from"./chunk-BlUUBWrt.js";function s(e,t){if(t===`error`)return`error`;if(t===`unload`)return`sleeping`;if(t===`load-start`||t===`load-progress`)return`loading`;if(e===`sleeping`)return`sleeping`;if(e===`loading`)return t===`loaded`?`idle`:`loading`;switch(t){case`loaded`:case`done`:case`stop`:case`reset`:return`idle`;case`focus`:case`input`:return e===`idle`||e===`listening`||e===`error`?`listening`:e;case`blur`:return e===`listening`?`idle`:e;case`send`:case`state-thinking`:case`acted`:return`thinking`;case`first-token`:case`delta`:return e===`acting`?`acting`:`talking`;case`tool-call`:return`acting`}return e}var c={idle:{eyes:`dot`,mouth:`smile`,extra:`none`,led:`breathe`,motion:`breathe`},listening:{eyes:`wide`,mouth:`o`,extra:`none`,led:`steady`,motion:`lean`},thinking:{eyes:`up`,mouth:`flat`,extra:`question`,led:`amber`,motion:`wiggle`},loading:{eyes:`lid`,mouth:`flat`,extra:`none`,led:`steady`,motion:`slow`},talking:{eyes:`dot`,mouth:`open`,extra:`none`,led:`steady`,motion:`bounce`},acting:{eyes:`side`,mouth:`wide`,extra:`spark`,led:`flash`,motion:`hop`},error:{eyes:`small`,mouth:`wavy`,extra:`alarm`,led:`blink`,motion:`shake`},sleeping:{eyes:`shut`,mouth:`tiny`,extra:`z`,led:`off`,motion:`sleep`}};function l(e){return c[e]??c.idle}function u(e){return e==null||!Number.isFinite(e)?null:Math.max(.02,Math.min(1,e))}function d(e,t=0){let n=.55+.45*Math.max(0,Math.min(1,Number.isFinite(e)?e:0));return Math.abs(n-t)<.12?n>.78?n-.25:n+.2:n}function f(){return typeof matchMedia==`function`&&matchMedia(`(prefers-reduced-motion: reduce)`).matches}var p=`M16.2 29.4L83.6 29Q85.2 29 86 30.4L89.6 38.6Q90.4 40 90.3 43.4L89.8 79.2Q89.7 82.2 86.8 82.2L13.4 82.6Q10.4 82.6 10.4 79.6L10.2 43Q10.1 40.2 10.9 38.8L14.4 30.6Q15 29.4 16.2 29.4Z`,m=`M12.5 40L87.5 40.3Q90.3 40.4 90.3 43.4L89.8 79.2Q89.7 82.2 86.8 82.2L13.4 82.6Q10.4 82.6 10.4 79.6L10.2 43Q10.2 40 12.5 40Z`,h=[16.6,21.6,26.6,73.4,78.4,83.4],g=[47,55,63,71],_=h.flatMap(e=>g.map(t=>`M${e-1.3} ${t}a1.3 1.3 0 1 0 2.6 0a1.3 1.3 0 1 0-2.6 0`)).join(``),v=o`<g class="gb-lit">${h.flatMap((e,t)=>g.map(n=>o`<circle cx=${e} cy=${n} r="1.5" style=${`animation-delay:${(t*.12).toFixed(2)}s`} />`))}</g>`,y=(e,t)=>o`<g class=${`gb-eye gb-eye-${e}`}>
  <ellipse cx=${t} cy="56.4" rx="4.3" ry="5.5" />
  <circle class="gb-glint" cx=${t+1.5} cy="54.3" r="1.25" />
  <rect class="gb-lid" x=${t-4.8} y="50.4" width="9.6" height="5.8" />
</g>`,b=e=>o`
  <ellipse class="gb-ground" cx="50" cy="88.6" rx="36" ry="2.6" />
  <g class="gb-all">
    <g class="gb-feet"><rect x="21" y="79.6" width="10.5" height="8" rx="2.2" /><rect x="68.5" y="79.6" width="10.5" height="8" rx="2.2" /></g>
    <g class="gb-body">
      <path class="gb-cut" d=${p} transform="translate(1.4 1.8)" />
      <path class="gb-edge" d=${p} />
      <path class="gb-front" d=${m} />
      <path class="gb-chin" d="M10.4 77.6L89.9 77.2 89.8 79.2Q89.7 82.2 86.8 82.2L13.4 82.6Q10.4 82.6 10.4 79.6Z" />
      <path class="gb-top" d="M16.2 29.4L83.6 29Q85.2 29 86 30.4L89.8 39.4Q89.6 40.3 87.5 40.3L12.5 40Q10.6 40 10.9 39L14.4 30.6Q15 29.4 16.2 29.4Z" />
      <path class="gb-seam" d="M11.4 40L89.4 40.3" />
      <path class="gb-dots" d=${_} />
      ${e}
      <circle class="gb-led-halo" cx="86.4" cy="43.4" r="2.9" />
      <circle class="gb-led" cx="86.4" cy="43.4" r="1.25" />
      <g class="gb-face">
        <ellipse class="gb-blush" cx="28.4" cy="66" rx="4.6" ry="2.8" />
        <ellipse class="gb-blush" cx="71.6" cy="66" rx="4.6" ry="2.8" />
        <g class="gb-eyes">${y(`l`,38.6)}${y(`r`,61.4)}</g>
        <path class="gb-shut" d="M34.4 56.6Q38.6 60.8 42.8 56.6M57.2 56.6Q61.4 60.8 65.6 56.6" />
        <path class="gb-m gb-m-smile" d="M45.8 67.8L47.7 70.4H52.3L54.2 67.8" />
        <ellipse class="gb-m gb-m-o gb-fill" cx="50" cy="69.8" rx="1.8" ry="2.2" />
        <path class="gb-m gb-m-flat" d="M47 69.6H53" />
        <ellipse class="gb-m gb-m-open gb-fill" cx="50" cy="70.4" rx="3.3" ry="3.4" />
        <g class="gb-m gb-m-wide"><path class="gb-fill" d="M44.6 67.2H55.4Q55.4 73 50 73Q44.6 73 44.6 67.2Z" /><ellipse class="gb-tongue" cx="50" cy="71.4" rx="2.6" ry="1.3" /></g>
        <path class="gb-m gb-m-wavy" d="M45.4 70.4L47.7 68.4 50 70.4 52.3 68.4 54.6 70.4" />
        <path class="gb-m gb-m-tiny" d="M48.4 69.8H51.6" />
      </g>
    </g>
  </g>
  <g class="gb-question"><text x="68" y="23">?</text><text x="77" y="15">?</text><text x="86" y="7">?</text></g>
  <g class="gb-alarm"><text x="70" y="22">?!</text><path class="gb-sweat" d="M87 32.4Q84 36.6 84 38.6A3 3 0 0 0 90 38.6Q90 36.6 87 32.4Z" /></g>
  <g class="gb-z"><text x="70" y="22">z</text><text x="78" y="14">z</text><text x="86" y="6">z</text></g>
  <g class="gb-spark"><path d="M10 22l0-7M6.5 18.5l7 0" /><path d="M20 12l0-5M17.5 9.5l5 0" /></g>
`,x=56,S=0,C=class extends i{constructor(...e){super(...e),this.state=`idle`,this.size=72,this.progress=null,this.lastBlip=0}blip(){let e=performance.now();e-this.lastBlip<140||f()||(this.lastBlip=e,S=d(Math.random(),S),this.querySelector(`.gb-m-open`)?.animate([{transform:`scaleY(.5)`},{transform:`scaleY(${S})`,offset:.4},{transform:`scaleY(.5)`}],{duration:180,easing:`ease-out`}))}hop(){let e=this.querySelector(`.gb-all`);if(!e||f())return Promise.resolve();let t={duration:1100,easing:`cubic-bezier(.3,.7,.4,1)`},n=e.animate([{transform:`translate(0,0) scale(1,1)`},{transform:`translate(0,0) scale(1.1,.86)`,offset:.1},{transform:`translate(-5px,-16px) scale(.92,1.1)`,offset:.28},{transform:`translate(-9px,0) scale(1.12,.84)`,offset:.44},{transform:`translate(-12px,-10px) scale(.94,1.07)`,offset:.62},{transform:`translate(-14px,0) scale(1.08,.9)`,offset:.78},{transform:`translate(0,0) scale(1,1)`}],t);return this.querySelector(`.gb-ground`)?.animate([{transform:`translateX(0) scaleX(1)`},{transform:`translateX(-5px) scaleX(.6)`,offset:.28},{transform:`translateX(-9px) scaleX(1.06)`,offset:.44},{transform:`translateX(-12px) scaleX(.75)`,offset:.62},{transform:`translateX(-14px) scaleX(1)`,offset:.78},{transform:`translateX(0) scaleX(1)`}],t),this.querySelector(`.gb-spark`)?.animate([{opacity:0,transform:`scale(.4)`},{opacity:1,transform:`scale(1.1)`,offset:.3},{opacity:0,transform:`scale(1.3)`}],{duration:900,delay:300,easing:`ease-out`}),n.finished.then(()=>void 0).catch(()=>void 0)}render(){let e=l(this.state),t=this.state===`loading`,n=t?u(this.progress):null,i=this.size<=x?Math.round(this.size*108/84):this.size,s=(i-this.size)/2;return r`<svg
      class="goldbox"
      data-state=${this.state}
      data-eyes=${e.eyes}
      data-mouth=${e.mouth}
      data-extra=${e.extra}
      data-led=${e.led}
      data-motion=${e.motion}
      ?data-small=${s>0}
      viewBox="-4 -4 108 108"
      width=${i}
      height=${i}
      style=${s?`margin:${-s}px`:a}
      aria-hidden="true"
      focusable="false"
    >
      ${t?o`<g class="w-ring"><circle class="w-ring-track" cx="50" cy="58" r="48" pathLength="100" />${n==null?o`<circle class="w-ring-spin" cx="50" cy="58" r="48" pathLength="100" stroke-dasharray="18 82" />`:o`<circle class="w-ring-fill" cx="50" cy="58" r="48" pathLength="100" stroke-dasharray=${`${(n*100).toFixed(1)} 100`} />`}</g>`:a}
      ${b(t?v:a)}
    </svg>`}};e([t({reflect:!0})],C.prototype,`state`,void 0),e([t({type:Number})],C.prototype,`size`,void 0),e([t({attribute:!1})],C.prototype,`progress`,void 0),C=e([n(`qse-wren-figure`)],C);var w=o`<svg class="wren-mini-box" viewBox="0 0 30 22" width="32" height="24" aria-hidden="true" focusable="false">
  <rect class="mw-foot" x="6.4" y="16.4" width="3.4" height="4" rx="1" /><rect class="mw-foot" x="20.2" y="16.4" width="3.4" height="4" rx="1" />
  <path class="mw-edge" d="M6.2 2.6L23.8 2.4 27 6.6Q27.6 7.2 27.6 8.2V17.2Q27.6 18.4 26.4 18.4L3.6 18.6Q2.4 18.6 2.4 17.4V8.2Q2.4 7.2 3 6.6Z" />
  <path class="mw-front" d="M3.6 7L26.4 7.1Q27.6 7.1 27.6 8.3V17.2Q27.6 18.4 26.4 18.4L3.6 18.6Q2.4 18.6 2.4 17.4V8.2Q2.4 7 3.6 7Z" />
  <path class="mw-top" d="M6.2 2.6L23.8 2.4 27 7.1 3 7Z" />
  <ellipse class="mw-blush" cx="8.8" cy="14.4" rx="1.7" ry="1" /><ellipse class="mw-blush" cx="21.2" cy="14.4" rx="1.7" ry="1" />
  <g class="mw-eyes"><ellipse cx="12" cy="11.4" rx="1.25" ry="1.6" /><ellipse cx="18" cy="11.4" rx="1.25" ry="1.6" /></g>
  <ellipse class="mw-o" cx="15" cy="15" rx=".75" ry=".9" />
  <path class="mw-smile" d="M13.6 14.4L14.3 15.3H15.7L16.4 14.4" />
</svg>`;export{f as n,s as r,w as t};