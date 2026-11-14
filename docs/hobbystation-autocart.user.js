// ==UserScript==
// @name         ポケカ買い得リスト：まとめてカート
// @namespace    https://lurdsrot2-coder.github.io/pokecards/
// @version      2.8
// @description  買い得リストの「まとめてカートへ」から開いたときだけ、店ごとの商品を1件ずつ自動でカートに入れる（ホビステ／フルアヘッド／カードラボ／トレトク／BIGWEB／福福トレカ／マイカ／トレコロ／オルタ／カードラッシュ／キャンプ）。メルカリは「メルカリで探す」から開いた検索結果を読んで買い得リストに返すだけ
// @match        https://www.hobbystation-single.jp/pk/product/detail/*
// @match        https://pokemon-card-fullahead.com/shopdetail/*
// @match        https://pokemon-card-fullahead.com/shop/basket.html*
// @match        https://www.c-labo-online.jp/product/*
// @match        https://www.cardrush-pokemon.jp/product/*
// @match        https://www.cardrush-pokemon.jp/cart*
// @match        https://www.c-labo-online.jp/cart*
// @match        https://www.toretoku.jp/item/details/*
// @match        https://www.bigweb.co.jp/ja/products/pokemon/cardViewer/*
// @match        https://pokemon.fukufukutoreka.com/products/detail/*
// @match        https://myca.dmm.com/pokemon-trading-card-game/items/single-card/*
// @match        https://www.torecolo.jp/shop/g/*
// @match        https://olta-tcg.com/pokemon/product/detail/*
// @match        https://torecacamp-pokemon.com/products/*
// @match        https://jp.mercari.com/search*
// @run-at       document-idle
// @grant        none
// @updateURL    https://lurdsrot2-coder.github.io/pokecards/docs/hobbystation-autocart.user.js
// @downloadURL  https://lurdsrot2-coder.github.io/pokecards/docs/hobbystation-autocart.user.js
// ==/UserScript==
// ---- メルカリ ----
// 買い得リストの「メルカリで探す」から開いた検索結果（#pcmc=<カードid>）だけを読む。
// 開いた画面に出ている出品を読むだけで、次の検索や別のページへの移動は一切しない
// （メルカリは自動での利用を規約で禁じているため。検索するのは毎回ユーザーの操作）
(function(){
  'use strict';
  if(location.hostname!=='jp.mercari.com') return;
  const m=location.hash.match(/pcmc=([^&]+)/);
  if(!m) return;
  let job=null;
  try{ job=JSON.parse(decodeURIComponent(m[1])); }catch(_){}
  if(!job || !job.id) return;
  history.replaceState(null,'',location.pathname+location.search);
  const sleep=ms=>new Promise(r=>setTimeout(r,ms));
  // 鑑定品・海外版・まとめ売りなどは、同じカード1枚の値段ではないので外す
  const NG=/psa|bgs|ars|cgc|鑑定|まとめ|セット売|枚セット|英語|海外|中国|韓国|簡体|繁体|オリパ|引退|スリーブ|ローダー|ファイル|空箱|未開封|box/i;
  // 番号の頭（032/080 → 32）。番号を書いていない出品は「未確認」として返す
  const head=String(job.num||'').match(/^0*(\d+)\s*\//);
  const numRe=head ? new RegExp('(^|[^0-9])0*'+head[1]+'\\s*[/／]') : null;
  const bar=document.createElement('div');
  bar.style.cssText='position:fixed;left:0;right:0;bottom:0;z-index:2147483647;padding:10px 14px;'
    +'background:#1b1f3b;color:#fff;font:bold 14px sans-serif';
  bar.textContent='買い得リスト：出品を読み取っています…';
  document.body.appendChild(bar);
  const cells=()=>[...document.querySelectorAll('li[data-testid="item-cell"]')];
  const priced=()=>cells().filter(c=>c.querySelector('[data-testid="item-tile-price"]'));
  (async()=>{
    // 表示を待つ。0件の検索は「見つかりません」が出る
    const t0=Date.now();
    while(Date.now()-t0<20000){
      if(priced().length) break;
      if(/該当する商品が見つかりません|見つかりませんでした/.test(document.body.innerText)) break;
      await sleep(400);
    }
    // 一覧は画面に入ったところから描かれる。裏のタブだと描かれないので、表に出るまで待つ
    if(document.hidden){
      bar.textContent='買い得リスト：このタブを表示すると読み取ります';
      await new Promise(r=>{ const f=()=>{ if(!document.hidden){ removeEventListener('visibilitychange',f); r(); } };
                             addEventListener('visibilitychange',f); });
      bar.textContent='買い得リスト：出品を読み取っています…';
    }
    // 少しずつ下まで流して描かせる（読み込み済みの結果を描くだけで、追加の検索はしない）。
    // 一気に一番下へ飛ぶと、途中の出品が描かれないまま残る
    for(let y=0, i=0; y<document.body.scrollHeight && i<60; y+=Math.max(300, innerHeight*0.7), i++){
      scrollTo(0,y);
      await sleep(300);
    }
    // 描き終わるのを待つ（数が2回続けて変わらなくなるまで、最大4秒）
    for(let i=0, last=-1, same=0; i<8 && same<2; i++){
      await sleep(500);
      const n=priced().length;
      same = n===last ? same+1 : 0; last=n;
    }
    scrollTo(0,0);
    const items=[], seen=new Set();
    let ng=0;
    for(const c of priced()){
      const a=c.querySelector('a[href*="/item/"]');
      const id=a && (a.getAttribute('href').match(/\/item\/(m\d+)/)||[])[1];
      if(!id || seen.has(id)) continue;
      seen.add(id);
      const n=(c.querySelector('[data-testid="thumbnail-item-name"]')||{}).innerText||'';
      const p=+((c.querySelector('[data-testid="item-tile-price"]')||{}).innerText||'').replace(/\D/g,'');
      if(!p) continue;
      if(NG.test(n)){ ng++; continue; }
      const sold=!!c.querySelector('[data-testid="item-tile-sticker"]');
      items.push({i:id, p:p, n:n.slice(0,80), s:sold?1:0, k:numRe?(numRe.test(n)?1:0):1});
    }
    const res={id:job.id, t:Date.now(), items:items.slice(0,80), ng:ng};
    const on=items.filter(x=>!x.s).length, so=items.length-on;
    let sent=false;
    try{ if(window.opener){ window.opener.postMessage({pcMercari:res}, '*'); sent=true; } }catch(_){}
    bar.textContent = sent
      ? '買い得リストに送りました：販売中 '+on+'件・売切 '+so+'件（鑑定品・まとめ売りなど '+ng+'件は除外）'
      : '買い得リストのタブが見つかりません。買い得リストの「メルカリ」ボタンから開き直してください';
    bar.style.background = sent ? '#0b5d3b' : '#7a1d1d';
  })();
})();
(function(){
  'use strict';
  const KEY='pcAutoCart';
  const SHOPS={'www.hobbystation-single.jp':'HB','pokemon-card-fullahead.com':'FA',
    'www.c-labo-online.jp':'CL','www.toretoku.jp':'TT','www.bigweb.co.jp':'BW',
    'www.cardrush-pokemon.jp':'CR',
    'pokemon.fukufukutoreka.com':'FF','myca.dmm.com':'MY','www.torecolo.jp':'TR','olta-tcg.com':'OL',
    'torecacamp-pokemon.com':'TC'};
  const shop=SHOPS[location.hostname];
  if(!shop) return;
  // フルアヘッド・カードラボ・カードラッシュは、押すとカート画面に移動する店。
  // カードラボとカードラッシュはどちらもカラーミーショップで、ボタンのidも同じ
  const NAV = shop==='FA' || shop==='CL' || shop==='CR';

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
    const cartUrl={HB:'/cart',FA:'/shop/basket.html',CL:'/cart',TT:'/cart',FF:'/cart',MY:'/cart',TR:'/shop/cart/cart.aspx',OL:'/cart',CR:'/cart',TC:'/cart'}[shop];
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
  // 動き出したことを先に知らせる。これが届かないと、買い得リスト側は
  // 「スクリプトが入っていない／古い」と判断して確認待ちを戻す
  tell({hello:1, ver:'2.7'});
  // 店で「在庫なし」と確認できたもの。買い得リスト側で「売切」にしてもらう。
  // 価格違い・店違い・押せなかった、は売り切れとは限らないので含めない
  const isSoldOut=res=>typeof res==='string' && /^(状態.の)?在庫なし$/.test(res);
  function next(res, name){
    if(stopped) return;
    // k は買い得リストの行の印。同じ商品ページに状態違いが複数あっても取り違えない
    const k=cur.k?[cur.k]:[];
    tell(res===true ? {ok:[cur.u], okK:k}
                    : {ng:[cur.u], ngK:k, soldK:isSoldOut(res)?k:[], sold:isSoldOut(res)?[cur.u]:[]});
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
  // キャンプ（Shopify）。商品データで在庫を確かめてから、cart/add.js で入れる。
  // 422 だけでは「売り切れ」か「カートに入れられる上限」か区別がつかないので、
  // 売り切れの判定は商品データの available で行う
  async function tc(){
    const vid=String(cur.v||'');
    if(!vid) return 'バリエーションが不明';
    const n=Math.max(1,+cur.n||1);
    try{
      const r=await fetch(location.pathname.replace(/\/+$/,'')+'.js',{credentials:'same-origin'});
      if(r.ok){
        const p=await r.json();
        const v=(p.variants||[]).find(x=>String(x.id)===vid);
        if(!v) return '商品が見つからない';
        if(v.available===false) return '在庫なし';
      }
    }catch(_){}
    let r2;
    try{
      r2=await fetch('/cart/add.js',{method:'POST',credentials:'same-origin',
        headers:{'Content-Type':'application/json','Accept':'application/json'},
        body:JSON.stringify({items:[{id:Number(vid),quantity:n}]})});
    }catch(e){ return 'カートに入らず（通信エラー）'; }
    if(r2.ok) return true;
    let j={}; try{ j=await r2.json(); }catch(_){}
    const why=String(j.description||j.message||r2.status).slice(0,40);
    return /sold out|売り切れ|在庫切れ/i.test(why) ? '在庫なし' : 'カートに入らず（'+why+'）';
  }
  async function ol(){
    // 状態ごとに行が分かれている。リストの状態と価格が同じ行だけ押す
    // （状態違いの行や、価格が変わった行を取り違えないため）
    // ボタンの文字は「カートに追加」だったが、今は「追加」になっている。どちらでも押す
    const btns=()=>[...document.querySelectorAll('button')].filter(b=>/^(カートに)?追加$/.test(b.innerText.trim()));
    if(!await waitFor(()=>btns().length, 10000)) return 'ボタンが見つからない';
    const rowOf=b=>{ let p=b;
      while(p.parentElement && btns().filter(x=>p.parentElement.contains(x)).length<2) p=p.parentElement;
      return p; };
    const want='¥'+Number(cur.p).toLocaleString('en-US');
    const rows=btns().map(b=>({b, t:rowOf(b).innerText.trim()}))
                     .filter(r=>r.t.includes(want) && (!cur.c || r.t.startsWith(cur.c)));
    if(!rows.length) return '価格が変わっています（'+want+'の行なし）';
    const hit=rows.find(r=>!r.b.disabled);
    if(!hit) return '在庫なし';
    // 押したあとの「カートに追加しました」の表示は当てにならない（出なくなった）。
    // カート本体の数量が増えたかで確かめる
    const qty=async()=>{
      try{
        const r=await fetch('/api',{method:'POST',headers:{'content-type':'application/json'},
          credentials:'same-origin',body:JSON.stringify({query:'query { cart { items { quantity } } }'})});
        const j=await r.json();
        return j.data.cart.items.reduce((s,i)=>s+(+i.quantity||0),0);
      }catch(_){ return -1; }
    };
    const before=await qty();
    if(before<0) return 'カートを確認できず';
    hit.b.click();
    for(let i=0;i<24;i++){            // 最大12秒
      await sleep(500);
      if((await qty())>before) return true;
    }
    return 'カートに入らず';
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
    // カラーミーの店は枚数を選べる。リストで×Nにしたぶんをそのまま入れる
    const q=document.querySelector('select[id^="cart_addquantity_"]');
    const want=Math.max(1, parseInt(cur.n,10)||1);
    if(q && want>1){
      const max=q.options.length;
      q.value=String(Math.min(want, max));
      q.dispatchEvent(new Event('change',{bubbles:true}));
    }
    st.wait=1; save(st);
    b.click();
    await sleep(15000);               // ここまで残っていたら移動できていない
    return 'カートに入らず';
  }

  const name=(document.title.split('/').pop()||document.title||'').trim().slice(0,40);

  // カート画面に着いた（直前の1件が入った）
  if(!onProduct){
    tell({ok:[cur.u], okK:cur.k?[cur.k]:[]});
    st.ok++; st.q.shift(); st.wait=0;
    advance();
    return;
  }
  // 商品ページ
  if(NAV) st.wait=0;
  const run={HB:hb,FA:nav,CL:nav,CR:nav,TT:tt,BW:bw,FF:ff,MY:my,TR:tr,OL:ol,TC:tc}[shop];
  (async()=>{
    let res;
    try{ res=await run(); }catch(e){ res='エラー '+(e&&e.message||e); }
    next(res, name);
  })();
})();
