// ==UserScript==
// @name         ポケカ買い得リスト：メルカリの♡
// @namespace    https://lurdsrot2-coder.github.io/pokecards/
// @version      1.0
// @description  買い得リストのタイルの♡を押したときだけ、その商品のメルカリのページを開いて♡（いいね！）を1回押し、結果を買い得リストに返してタブを閉じる
// @match        https://lurdsrot2-coder.github.io/pokecards/docs/buylist-rush-bc.html*
// @match        https://jp.mercari.com/item/*
// @match        https://jp.mercari.com/shops/product/*
// @run-at       document-idle
// @grant        GM_setValue
// @grant        GM_getValue
// @grant        GM_deleteValue
// @grant        GM_addValueChangeListener
// @grant        GM_openInTab
// @updateURL    https://lurdsrot2-coder.github.io/pokecards/docs/mercari-like.user.js
// @downloadURL  https://lurdsrot2-coder.github.io/pokecards/docs/mercari-like.user.js
// ==/UserScript==
// メルカリの商品ページは、外のサイトから開くと開いた元のタブとのつながり（window.opener）と
// URLの印を落とすので、頼みごとと結果は Tampermonkey の保存場所で受け渡す。
(function(){
  'use strict';
  const REQ='pcLikeReq', RES='pcLikeRes';

  // ---- 買い得リスト側：♡の頼みを受けて、メルカリのタブを開く ----
  if(location.hostname==='lurdsrot2-coder.github.io'){
    document.documentElement.dataset.pcLikeBridge='1';
    let tab=null;
    addEventListener('message', e=>{
      if(e.origin!==location.origin) return;
      const r=e.data && e.data.pcLikeReq;
      if(!r) return;
      const id=String(r.i||'').replace(/[^\w]/g,'');
      if(!id) return;
      const url=id[0]==='s' ? 'https://jp.mercari.com/shops/product/'+id.slice(1) : 'https://jp.mercari.com/item/'+id;
      GM_setValue(REQ, {i:id, on:r.on?1:0, t:Date.now()});
      try{ if(tab && !tab.closed) tab.close(); }catch(_){}
      tab=GM_openInTab(url, {active:true, setParent:true});
    });
    GM_addValueChangeListener(RES, (k, o, n)=>{
      if(!n || !n.i) return;
      // うまくいったらタブを閉じて買い得リストに戻る。だめなときは開いたまま（理由を見られるように）
      if(n.ok){ try{ if(tab) tab.close(); }catch(_){} tab=null; }
      window.postMessage({pcLikeRes:{i:n.i, on:n.on, ok:n.ok}}, location.origin);
    });
    return;
  }

  // ---- メルカリ側：頼まれた商品のページでだけ、♡を1回押す ----
  if(location.hostname!=='jp.mercari.com') return;
  const a=location.pathname.match(/^\/item\/(m\d+)/), b=location.pathname.match(/^\/shops\/product\/(\w+)/);
  const id=a?a[1]:b?'s'+b[1]:'';
  if(!id) return;
  const sleep=ms=>new Promise(r=>setTimeout(r,ms));
  const btn=()=>document.querySelector('[data-testid="icon-heart-button"]');
  // 塗りつぶしの♡＝いいね！済み、線だけの♡＝まだ
  const state=()=>{ const h=btn(); if(!h) return null;
    if(h.querySelector('[data-testid="icon-like-filled"]')) return true;
    if(h.querySelector('[data-testid="icon-like-outlined"]')) return false;
    const p=h.getAttribute('aria-pressed'); return p==null?null:p==='true'; };
  (async()=>{
    // 2分以内の、この商品への頼みだけ。ふつうに開いたページでは何もしない
    const req=GM_getValue(REQ, null);
    if(!req || req.i!==id || Date.now()-req.t>120000) return;
    GM_deleteValue(REQ);
    const want=!!req.on;
    const bar=document.createElement('div');
    bar.style.cssText='position:fixed;left:0;right:0;bottom:0;z-index:2147483647;padding:10px 14px;'
      +'background:#1b1f3b;color:#fff;font:bold 14px sans-serif';
    bar.textContent='買い得リスト：'+(want?'いいね！しています…':'いいね！を外しています…');
    document.body.appendChild(bar);
    const t0=Date.now();
    while(Date.now()-t0<15000 && state()===null) await sleep(300);
    let ok=false;
    if(state()!==null){
      if(state()!==want){
        btn().click();
        for(let i=0; i<30 && state()!==want; i++) await sleep(250);
      }
      ok = state()===want;
    }
    bar.textContent = ok ? (want?'いいね！しました':'いいね！を外しました')+'。買い得リストに戻ります'
      : '買い得リスト：♡を押せませんでした（売り切れ・削除・ログアウトの可能性）';
    bar.style.background = ok ? '#0b5d3b' : '#7a1d1d';
    GM_setValue(RES, {i:id, on:want?1:0, ok:ok?1:0, t:Date.now()});
  })();
})();
