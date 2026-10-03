// ==UserScript==
// @name         ポケカ買い得リスト：ホビステまとめてカート
// @namespace    https://lurdsrot2-coder.github.io/pokecards/
// @version      1.0
// @description  買い得リストの「まとめてカートへ」から開いたときだけ、ホビステの商品を1件ずつ自動でカートに入れる
// @match        https://www.hobbystation-single.jp/pk/product/detail/*
// @run-at       document-idle
// @grant        none
// @updateURL    https://lurdsrot2-coder.github.io/pokecards/docs/hobbystation-autocart.user.js
// @downloadURL  https://lurdsrot2-coder.github.io/pokecards/docs/hobbystation-autocart.user.js
// ==/UserScript==
(function(){
  'use strict';
  const KEY='pcAutoCart';
  const BASE='https://www.hobbystation-single.jp/pk/product/detail/';
  const here=(location.pathname.match(/detail\/(\d+)/)||[])[1];
  const load=()=>{ try{ return JSON.parse(sessionStorage.getItem(KEY)||'null'); }catch(_){ return null; } };
  const save=s=>sessionStorage.setItem(KEY, JSON.stringify(s));

  // 買い得リストからは #pcq=今のID,次のID,... で来る。
  // 2件目からはこのタブの sessionStorage だけで続ける
  let st;
  const m=location.hash.match(/pcq=([\d,]+)/);
  if(m){
    const q=m[1].split(',').filter(Boolean);
    st={q, total:q.length, ok:0, ng:[]};
    save(st);
    history.replaceState(null,'',location.pathname+location.search);
  }else{
    st=load();
  }
  // 普通に商品ページを見ているときは何もしない
  if(!st || !st.q.length || st.q[0]!==here) return;

  const bar=document.createElement('div');
  bar.style.cssText='position:fixed;left:0;right:0;top:0;z-index:99999;padding:10px 14px;'
    +'background:#1b1f3b;color:#fff;font:bold 15px sans-serif;display:flex;gap:12px;align-items:center';
  const msg=document.createElement('span');
  const stop=document.createElement('button');
  stop.textContent='止める';
  stop.style.cssText='margin-left:auto;padding:4px 12px;font:bold 13px sans-serif;cursor:pointer';
  stop.onclick=()=>{ sessionStorage.removeItem(KEY); finish(true); };
  bar.append(msg, stop);
  document.body.appendChild(bar);
  const no=st.total-st.q.length+1;
  msg.textContent='自動でカートに入れています '+no+' / '+st.total
    +'（入れた '+st.ok+'・飛ばした '+st.ng.length+'）';

  const name=(document.title.split('/').pop()||'').trim();
  const visible=()=>{ const md=document.querySelector('.ec-modal');
                      return md && getComputedStyle(md).display!=='none'; };

  function next(res){
    if(stopped) return;
    if(res===true) st.ok++; else st.ng.push({id:here, name, why:res});
    st.q.shift();
    if(!st.q.length){ sessionStorage.removeItem(KEY); finish(false); return; }
    save(st);
    // 店に負担をかけないよう少し間をあける
    setTimeout(()=>{ location.href=BASE+st.q[0]; }, 800);
  }

  let stopped=false;
  function finish(byUser){
    stopped=true;
    stop.remove();
    msg.innerHTML='';
    msg.append((byUser?'止めました。':'終わりました。')
      +'入れた '+st.ok+'件 ／ 飛ばした '+st.ng.length+'件 ');
    const a=document.createElement('a');
    a.href='/cart'; a.textContent='カートを見る';
    a.style.cssText='color:#7df;margin-left:8px';
    msg.append(a);
    if(st.ng.length){
      const d=document.createElement('div');
      d.style.cssText='position:fixed;left:0;right:0;top:44px;z-index:99999;padding:8px 14px;'
        +'background:#3b1b24;color:#fff;font:13px sans-serif;max-height:40vh;overflow:auto';
      d.innerHTML='飛ばしたもの（買い得リストの「入れた印」を外しました）<br>'
        + st.ng.map(x=>'・'+x.name.replace(/</g,'&lt;')+'（'+x.why+'）').join('<br>');
      document.body.appendChild(d);
    }
    // 買い得リストに、入らなかったものを知らせて印を外してもらう
    try{ if(window.opener) window.opener.postMessage({pcAutoCart:{ng:st.ng.map(x=>x.id)}}, '*'); }catch(_){}
  }

  const btn=document.querySelector('button.add-cart');
  if(!btn || btn.disabled || btn.offsetParent===null){ next('在庫なし'); return; }
  const qty=document.querySelector('input[name=quantity]');
  if(qty) qty.value='1';
  btn.click();
  // 追加できたかはモーダルの見出しで判断する（失敗すると理由に置き換わる）
  const t0=Date.now();
  const iv=setInterval(()=>{
    if(visible()){
      clearInterval(iv);
      const h=(document.getElementById('ec-modal-header')||{}).textContent||'';
      next(/カートに追加しました/.test(h) ? true : (h.trim().slice(0,40)||'追加できず'));
    }else if(Date.now()-t0>15000){
      clearInterval(iv);
      next('応答なし');
    }
  }, 250);
})();
