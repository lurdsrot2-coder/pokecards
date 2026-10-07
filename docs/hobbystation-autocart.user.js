// ==UserScript==
// @name         ポケカ買い得リスト：まとめてカート
// @namespace    https://lurdsrot2-coder.github.io/pokecards/
// @version      2.3
// @description  買い得リストの「まとめてカートへ」から開いたときだけ、店ごとの商品を1件ずつ自動でカートに入れる（ホビステ／フルアヘッド／カードラボ／トレトク／BIGWEB／福福トレカ／マイカ／トレコロ／オルタ）
// @match        https://www.hobbystation-single.jp/pk/product/detail/*
// @match        https://pokemon-card-fullahead.com/shopdetail/*
// @match        https://pokemon-card-fullahead.com/shop/basket.html*
// @match        https://www.c-labo-online.jp/product/*
// @match        https://www.c-labo-online.jp/cart*
// @match        https://www.toretoku.jp/item/details/*
// @match        https://www.bigweb.co.jp/ja/products/pokemon/cardViewer/*
// @match        https://pokemon.fukufukutoreka.com/products/detail/*
// @match        https://myca.dmm.com/pokemon-trading-card-game/items/single-card/*
// @match        https://www.torecolo.jp/shop/g/*
// @match        https://olta-tcg.com/pokemon/product/detail/*
// @run-at       document-idle
// @grant        none
// @updateURL    https://lurdsrot2-coder.github.io/pokecards/docs/hobbystation-autocart.user.js
// @downloadURL  https://lurdsrot2-coder.github.io/pokecards/docs/hobbystation-autocart.user.js
// ==/UserScript==
(function(){
  'use strict';
  const KEY='pcAutoCart';
  const SHOPS={'www.hobbystation-single.jp':'HB','pokemon-card-fullahead.com':'FA',
    'www.c-labo-online.jp':'CL','www.toretoku.jp':'TT','www.bigweb.co.jp':'BW',
    'pokemon.fukufukutoreka.com':'FF','myca.dmm.com':'MY','www.torecolo.jp':'TR','olta-tcg.com':'OL'};
  const shop=SHOPS[location.hostname];
  if(!shop) return;
  // フルアヘッドとカードラボは、押すとカート画面に移動する店
  const NAV = shop==='FA' || shop==='CL';

  const sleep=ms=>new Promise(r=>setTimeout(r,ms));
  const norm=u=>String(u).replace(/[?#].*$/,'').replace(/\/+$/,'');
  const load=()=>{ try{ return JSON.parse(sessionStorage.getItem(KEY)||'null'); }catch(_){ return null; } };
  const save=s=>sessionStorage.setItem(KEY, JSON.stringify(s));
  async function waitFor(fn, ms){
    const t0=Date.now();
    for(;;){
      let v=null; try{ v=fn(); }catch(_){}
      if(v) return v;
      if(Date.now()-t0>ms) return null;
      await sleep(250);
    }
  }
  const waitText=(re,ms)=>waitFor(()=>re.test(document.body.innerText),ms)
                          .then(v=>v?true:'カートに入らず');

  // 買い得リストからは #pcq=<JSON> で来る。2件目からはこのタブの sessionStorage で続ける
  let st=null;
  const m=location.hash.match(/pcq=([^&]+)/);
  if(m){
    try{
      const j=JSON.parse(decodeURIComponent(m[1]));
      if(j && Array.isArray(j.q) && j.q.length)
        st={shop:j.shop||shop, q:j.q, total:j.q.length, ok:0, ng:[], wait:0};
    }catch(_){}
    if(st){ save(st); history.replaceState(null,'',location.pathname+location.search); }
  }else{
    st=load();
  }
  // 普通に見ているときは何もしない（別の店のキューが残っていても動かさない）
  if(!st || !st.q.length || st.shop!==shop) return;

  const cur=st.q[0];
  const onProduct = norm(location.pathname)===norm(cur.u);
  if(!onProduct && !(NAV && st.wait)) return;

  // ---- 画面上部の帯 ----
  const bar=document.createElement('div');
  bar.style.cssText='position:fixed;left:0;right:0;top:0;z-index:2147483647;padding:10px 14px;'
    +'background:#1b1f3b;color:#fff;font:bold 15px sans-serif;display:flex;gap:12px;align-items:center';
  const msg=document.createElement('span');
  const stop=document.createElement('button');
  stop.textContent='止める';
  stop.style.cssText='margin-left:auto;padding:4px 12px;font:bold 13px sans-serif;cursor:pointer';
  bar.append(msg, stop);
  document.body.appendChild(bar);
  let stopped=false;
  stop.onclick=()=>{ stopped=true; sessionStorage.removeItem(KEY); finish(true); };
  const show=()=>{
    const no=Math.min(st.total, st.total-st.q.length+1);
    msg.textContent='自動でカートに入れています '+no+' / '+st.total
      +'（入れた '+st.ok+'・飛ばした '+st.ng.length+'）';
  };
  show();

  function finish(byUser){
    stop.remove();
    msg.innerHTML='';
    msg.append((byUser?'止めました。':'終わりました。')
      +'入れた '+st.ok+'件 ／ 飛ばした '+st.ng.length+'件 ');
    const cartUrl={HB:'/cart',FA:'/shop/basket.html',CL:'/cart',TT:'/cart',FF:'/cart',MY:'/cart',TR:'/shop/cart/cart.aspx',OL:'/cart'}[shop];
    if(cartUrl){
      const a=document.createElement('a');
      a.href=cartUrl; a.textContent='カートを見る';
      a.style.cssText='color:#7df;margin-left:8px';
      msg.append(a);
    }else{
      // BIGWEBのカートはこのタブの中にある。閉じずに、右上のカートから買う
      msg.append('（カートはこのタブの右上。このタブを閉じないでください）');
    }
    if(st.ng.length){
      const d=document.createElement('div');
      d.style.cssText='position:fixed;left:0;right:0;top:44px;z-index:2147483647;padding:8px 14px;'
        +'background:#3b1b24;color:#fff;font:13px sans-serif;max-height:40vh;overflow:auto';
      d.innerHTML='飛ばしたもの（買い得リストでは「入れた」になっていません）<br>'
        + st.ng.map(x=>'・'+String(x.name).replace(/</g,'&lt;')+'（'+String(x.why).replace(/</g,'&lt;')+'）').join('<br>');
      document.body.appendChild(d);
    }
  }

  function advance(){
    if(stopped) return;
    if(!st.q.length){ sessionStorage.removeItem(KEY); finish(false); return; }
    save(st); show();
    // 店に負担をかけないよう少し間をあける
    setTimeout(()=>{ if(!stopped) location.href=location.origin+st.q[0].u; }, 800);
  }
  // 買い得リストへ1件ごとに知らせる。「入った」と届いたものだけ、リスト側で「入れた」になる
  function tell(msg){
    try{ if(window.opener) window.opener.postMessage({pcAutoCart:Object.assign({shop:shop}, msg)}, '*'); }catch(_){}
  }
  function next(res, name){
    if(stopped) return;
    tell(res===true ? {ok:[cur.u]} : {ng:[cur.u]});
    if(res===true) st.ok++; else st.ng.push({u:cur.u, name:name, why:res});
    st.q.shift(); st.wait=0;
    advance();
  }

  // ---- 店ごとの「カートに入れる」----
  async function hb(){
    const b=document.querySelector('button.add-cart');
    if(!b || b.disabled || b.offsetParent===null) return '在庫なし';
    const q=document.querySelector('input[name=quantity]'); if(q) q.value='1';
    b.click();
    return waitText(/カートに追加しました/,15000);
  }
  async function ff(){
    const b=document.querySelector('button.add-cart');
    if(!b || b.disabled || b.offsetParent===null) return '在庫なし';
    b.click();
    return waitText(/カートに追加しました/,15000);
  }
  async function tr(){
    const b=await waitFor(()=>document.querySelector('button.block-add-cart--btn'), 8000);
    if(!b || b.disabled || b.offsetParent===null) return '在庫なし';
    b.click();
    return waitText(/カゴに入れました/,15000);
  }
  async function ol(){
    // 状態ごとに行が分かれている。リストの状態と価格が同じ行だけ押す
    // （状態違いの行や、価格が変わった行を取り違えないため）
    const btns=()=>[...document.querySelectorAll('button')].filter(b=>b.innerText.trim()==='カートに追加');
    if(!await waitFor(()=>btns().length, 10000)) return '在庫なし';
    const rowOf=b=>{ let p=b;
      while(p.parentElement && btns().filter(x=>p.parentElement.contains(x)).length<2) p=p.parentElement;
      return p; };
    const want='¥'+Number(cur.p).toLocaleString('en-US');
    const rows=btns().map(b=>({b, t:rowOf(b).innerText.trim()}))
                     .filter(r=>r.t.includes(want) && (!cur.c || r.t.startsWith(cur.c)));
    if(!rows.length) return '価格が変わっています（'+want+'の行なし）';
    const hit=rows.find(r=>!r.b.disabled);
    if(!hit) return '在庫なし';
    hit.b.click();
    return waitText(/カートに追加しました/,15000);
  }
  async function my(){
    // 店を取り違えないよう、ボタンの近くに店名が出ているものだけ押す
    const b=await waitFor(()=>[...document.querySelectorAll('button')].find(e=>{
      if(!/カートに追加/.test(e.innerText) || e.offsetParent===null || e.disabled) return false;
      if(!cur.m) return true;
      let p=e; for(let i=0;i<8&&p;i++,p=p.parentElement) if(p.innerText && p.innerText.includes(cur.m)) return true;
      return false;
    }), 10000);
    if(!b) return '在庫なし／店違い';
    b.click();
    return waitText(/カートに追加しました/,15000);
  }
  async function tt(){
    // 状態（A／B）ごとに枚数を選ぶ。リストの状態と同じ欄で1枚にする
    const a=await waitFor(()=>[...document.querySelectorAll('.priceArea')].find(x=>
      x.querySelector('select') && x.innerText.split('\n')[0].trim()===cur.c), 8000);
    if(!a) return '状態'+cur.c+'の在庫なし';
    const sel=a.querySelector('select');
    if(sel.options.length<2) return '在庫なし';
    sel.value='1'; sel.dispatchEvent(new Event('change',{bubbles:true}));
    const b=document.querySelector('.js_inCartBtn'); if(!b) return 'ボタンなし';
    b.click();
    return waitText(/カートに追加しました/,15000);
  }
  async function bw(){
    // BIGWEBのカートはこのタブの sessionStorage にある。必ず同じタブで続けること
    const id=(location.pathname.match(/cardViewer\/(\d+)/)||[])[1];
    const c=await waitFor(()=>{ const x=document.querySelector('.one-image-item-container');
      return x && x.querySelector('button.cart-button') && x; }, 15000);
    if(!c) return '画面が出ず／在庫なし';
    await sleep(600);
    const plus=[...c.querySelectorAll('button.cart-button')].find(b=>b.innerText.trim()==='add');
    if(!plus || plus.disabled) return '在庫なし';
    plus.click();
    // 数量を変えるとボタンが描き換わるので、押したあとで探し直す
    const put=await waitFor(()=>[...c.querySelectorAll('button')].find(b=>
      /カートに(追加|入れる)/.test(b.innerText) && !b.disabled), 5000);
    if(!put) return '追加ボタンなし';
    await sleep(300);
    put.click();
    // 反映が遅いことがあるので、カートに載るのを最大6秒待つ
    const inCart=()=>{ try{ return JSON.parse(sessionStorage.getItem('cartItems')||'[]')
                                        .some(x=>String(x.id)===id); }catch(_){ return false; } };
    return (await waitFor(inCart, 6000)) ? true : 'カートに入らず';
  }
  // 押すとカート画面に移動する店。移動できたら、次の画面でこの続きをやる
  async function nav(){
    const b = shop==='FA' ? document.querySelector('a.add_cart')
                          : document.getElementById('submit_cart_input_btn');
    if(!b || b.disabled || b.offsetParent===null) return '在庫なし';
    st.wait=1; save(st);
    b.click();
    await sleep(15000);               // ここまで残っていたら移動できていない
    return 'カートに入らず';
  }

  const name=(document.title.split('/').pop()||document.title||'').trim().slice(0,40);

  // カート画面に着いた（直前の1件が入った）
  if(!onProduct){
    tell({ok:[cur.u]});
    st.ok++; st.q.shift(); st.wait=0;
    advance();
    return;
  }
  // 商品ページ
  if(NAV) st.wait=0;
  const run={HB:hb,FA:nav,CL:nav,TT:tt,BW:bw,FF:ff,MY:my,TR:tr,OL:ol}[shop];
  (async()=>{
    let res;
    try{ res=await run(); }catch(e){ res='エラー '+(e&&e.message||e); }
    next(res, name);
  })();
})();
