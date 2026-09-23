# -*- coding: utf-8 -*-
"""カードラッシュ・トレカキャンプ・トレトク・福福トレカの在庫を取り直して、買い得リストを作る。

やっていること:
  1. カードラッシュ（状態B/C/Dの検索）とトレカキャンプ（コレクション別のJSON）から商品を集める
  2. カード番号＋セット記号＋カード名で手元のDB（data.json＋delta.json）に突き合わせる
  3. 売り切れを落とし、同じカード・同じ状態の出品はいちばん安いものだけ残す
  4. docs/buylist-rush-bc.json に書き出して push する（ページはこれを読む）

前回のJSONと比べて、消えた商品（＝売れた）と新しく出た商品を数える。
新着には印を付けるので、ページ側で「新着だけ」を見られる。

手動実行:  python buylist_rush.py                （全部取り直す。20〜30分）
           python buylist_rush.py --sync         （お店には行かず、相場と所持だけ合わせる。数秒）
           python buylist_rush.py --only TT      （店を選ぶ）
           python buylist_rush.py --no-push      （pushせず手元だけ更新）
"""
import urllib.request, urllib.parse, re, io, json, sys, os, time, subprocess
import unicodedata, statistics, datetime, collections, base64
import concurrent.futures as futures

OWNER, REPO = 'lurdsrot2-coder', 'pokecards'
HERE = os.path.dirname(os.path.abspath(__file__))
# 外部コマンド（curl・git・PowerShell）を呼ぶたびに黒い窓が前面に出て
# 操作が中断されるので、窓を作らないようにする（Windowsのみ）
NOWIN = 0x08000000 if os.name == 'nt' else 0
OUT = os.path.join(HERE, 'docs', 'buylist-rush-bc.json')
UA = ('Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 '
      '(KHTML, like Gecko) Chrome/128.0.0.0 Safari/537.36')

ITEM = re.compile(r'<li class="list_item_cell list_item_(\d+)\s*".*?(?=<li class="list_item_cell|</ul>)', re.S)
ALT_IMG = re.compile(r'<img[^>]+alt="([^"]*)"')
MODEL = re.compile(r'model_number_value">([^<]*)<')
PRICE = re.compile(r'class="figure">([\d,]+)円')
STOCK = re.compile(r'class="stock([^"]*)">([^<]*)<')     # 売切れは class="stock soldout"
ALT = re.compile(r'^〔([^〕]*)〕(.*?)【([^】]*)】\{([^}]*)\}\s*$')
PAREN = re.compile(r'[(（]([^)）]*)[)）]')
LV = re.compile(r'\s*LV\.?\s*\d+\s*$')     # 旧裏は「ワニノコ LV.13」と書かれる


LOG = os.path.join(HERE, 'buylist_rush.log')


def log(msg):
    line = '%s  %s' % (datetime.datetime.now().strftime('%m-%d %H:%M:%S'), msg)
    # 自動実行は pythonw（黒い窓なし）で動かすので、画面が無いことがある
    try:
        sys.stderr.write(line + '\n')
        sys.stderr.flush()
    except Exception:
        pass
    # 様子が画面に出ないので、ファイルにも残す
    try:
        with io.open(LOG, 'a', encoding='utf-8') as f:
            f.write(line + '\n')
    except Exception:
        pass


def notify(title, message):
    """自動実行（毎朝5時）で失敗したときに気づけるように通知を出す"""
    try:
        subprocess.run(
            ['powershell', '-ExecutionPolicy', 'Bypass', '-File', os.path.join(HERE, 'notify.ps1'),
             '-Title', title, '-Message', message],
            timeout=60, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL, creationflags=NOWIN)
    except Exception:
        pass


def _trim_log():
    try:
        if os.path.getsize(LOG) > 2 * 1024 * 1024:
            tail = io.open(LOG, encoding='utf-8').read()[-500000:]
            io.open(LOG, 'w', encoding='utf-8', newline='').write(tail)
    except Exception:
        pass


# ── 1. 収集 ───────────────────────────────────────────
def fetch(url, tries=4):
    for i in range(tries):
        try:
            r = urllib.request.Request(url, headers={
                'User-Agent': UA, 'Accept': 'text/html', 'Accept-Language': 'ja,en;q=0.8'})
            return urllib.request.urlopen(r, timeout=90).read().decode('utf-8', 'replace')
        except Exception as e:
            log('  retry %d: %s' % (i + 1, e))
            _backoff(e, i)
    return ''


def page_url(kw, order, page):
    return 'https://www.cardrush-pokemon.jp/product-list?' + urllib.parse.urlencode(
        {'num': 100, 'img': 160, 'order': order, 'keyword': kw, 'Submit': '検索', 'page': page})


def parse_page(html):
    out = []
    for m in ITEM.finditer(html):
        seg = m.group(0)
        alt = ALT_IMG.search(seg)
        if not alt:
            continue
        p, s, mo = PRICE.search(seg), STOCK.search(seg), MODEL.search(seg)
        out.append({'pid': m.group(1), 'alt': alt.group(1),
                    'model': mo.group(1).strip() if mo else '',
                    'price': int(p.group(1).replace(',', '')) if p else 0,
                    'stock': s.group(2).strip() if s else '',
                    'soldout': bool(s and 'soldout' in s.group(1))})
    return out


def cr_scrape():
    """カードラッシュ。1ページ100件・最大100ページ（=1万件）で頭打ちになるので、
    価格の安い順と高い順の両方から取って重複を消す。

    短時間に叩きすぎると 403 で弾かれる。連続で失敗したら早めに諦めて、
    前回ぶんを使い回す（失敗を「全部売り切れた」と誤解しないため）"""
    items = {}
    miss = 0
    for kw in ('状態B', '状態C', '状態D'):
        for order in ('asc', 'desc'):
            for page in range(1, 101):
                h = fetch(page_url(kw, order, page))
                if not h:
                    miss += 1
                    log('%s %s p%d 取得失敗（%d回目）' % (kw, order, page, miss))
                    if miss >= 3:
                        log('  カードラッシュに繋がらないので今回は見送ります')
                        return []
                    continue
                miss = 0
                got = parse_page(h)
                for it in got:
                    items.setdefault(it['pid'], it)
                last = max([int(x) for x in re.findall(r'page=(\d+)"', h)] or [page])
                if page % 20 == 0 or page >= last:
                    log('  %s %s p%d/%d 累計%d' % (kw, order, page, last, len(items)))
                if page >= last or not got:
                    break
                time.sleep(.5)
    out = []
    for it in items.values():
        m = ALT.match(it['alt'])
        if not m:
            continue
        cond = m.group(1)
        if cond not in ('状態B', '状態C', '状態D'):
            continue
        out.append({
            'shop': 'CR', 'pid': it['pid'], 'cond': cond[-1],
            'url': 'https://www.cardrush-pokemon.jp/product/' + it['pid'],
            'name': m.group(2), 'rarity': m.group(3),
            'num': m.group(4).strip().upper(), 'setcode': it.get('model') or '',
            'settitle': '', 'price': it['price'],
            'stock': int(re.sub(r'\D', '', it['stock']) or 0), 'soldout': it['soldout']})
    log('  カードラッシュ 状態B/C/D %d件' % len(out))
    return out


# ── トレカキャンプ（Shopify） ─────────────────────────
TC = 'https://torecacamp-pokemon.com'
# 「094/106」だけでなく、DP期の「DPBP#161」も番号として扱う（DB側も同じ書き方）
TC_NUM = re.compile(r'(?<![0-9A-Za-z/#])(?:[0-9A-Za-z]{1,4}/[0-9A-Za-z\-]{1,6}|[A-Za-z]{2,6}#\d{1,4})(?![0-9A-Za-z/])')
TC_SKIP = ('海外版', '英語版', '鑑定品', 'PSA', 'ARS', '韓国版', '北米版', '中国語')
TC_SKIP_TITLE = ('未開封', 'BOX', 'ボックス', 'デッキケース', 'スリーブ', 'プレイマット')
TC_CODE = re.compile(r'^[0-9A-Za-z][0-9A-Za-z\-_]{0,9}$')
# 商品名のうしろに付くレアリティ表記（「キテルグマ R」「ねがいのバトン UR (K)」）
TC_RARITY = {'C', 'U', 'R', 'RR', 'RRR', 'SR', 'HR', 'UR', 'AR', 'SAR', 'CHR', 'CSR',
             'K', 'PR', 'TR', 'ACE', 'SP', 'SSR', 'S', 'H', 'P', 'A', 'LEGEND', 'PROMO',
             '●', '◆', '★', '☆', '-'}


def _backoff(e, i):
    """429（叩きすぎ）は数十秒あける。短い間隔で粘ると余計に閉じられる"""
    code = getattr(e, 'code', 0)
    if code in (429, 403, 503):
        wait = 0
        try:
            wait = int((e.headers or {}).get('Retry-After') or 0)
        except Exception:
            wait = 0
        time.sleep(max(wait, 20 * (i + 1)))
        return True
    time.sleep(2 + i * 3)
    return False


def tc_json(path, tries=4):
    """トレカキャンプは curl で取る。

    PythonのurllibだとTLSの指紋で弾かれるらしく、同じURL・同じヘッダでも
    curlは200、urllibは429を返し続ける（時間をあけても変わらない）。
    Windows標準の curl.exe を使えば普通に取れる。
    """
    url = TC + path
    for i in range(tries):
        try:
            r = subprocess.run(
                ['curl', '-sS', '--compressed', '-m', '90', '-w', '\n%{http_code}',
                 '-A', UA, '-H', 'Accept: application/json', url],
                stdout=subprocess.PIPE, stderr=subprocess.PIPE, timeout=120, creationflags=NOWIN)
            out = r.stdout.decode('utf-8', 'replace')
            code = out.rsplit('\n', 1)[-1].strip()
            body = out.rsplit('\n', 1)[0]
            if code == '200' and body.strip():
                return json.loads(body)
            log('  retry %d: HTTP %s' % (i + 1, code or '?'))
            time.sleep(max(20 * (i + 1), 20) if code in ('429', '403', '503') else 3)
        except Exception as e:
            log('  retry %d: %s' % (i + 1, e))
            time.sleep(3 + i * 3)
    return {}


def tc_parse(p, settitle):
    """「ニドラン♀ ● 1st2 002/048 【アンリミ】」のような商品名をほどく"""
    title = p.get('title') or ''
    tags = p.get('tags') or []
    if 'ポケモンカードシングル' not in tags and not settitle:
        return None
    # 海外版などはタグにも出るので両方見る。「BOX」は収録弾名（エクストラ
    # レギュレーションBOX）にも入っているので、商品名だけで判定する
    if any(w in title + ' ' + ' '.join(tags) for w in TC_SKIP):
        return None
    if any(w in title for w in TC_SKIP_TITLE):
        return None
    notes = re.findall(r'【([^】]*)】', title)
    core = re.sub(r'【[^】]*】', '', title).strip()
    ms = list(TC_NUM.finditer(core))
    if ms:
        m = ms[-1]
        num = m.group(0).upper()
        toks = core[:m.start()].split()
    else:
        # 番号が無い商品（「パチリス DP4」など）。セット記号＋名前で引く
        num = ''
        toks = core.split()
        if len(toks) < 2 or not TC_CODE.match(toks[-1]):
            return None
    if len(toks) < 2:
        return None
    setcode = toks[-1]
    head = toks[:-1]
    # 名前そのものがレアリティ表記と同じカード（トレーナーの「N」など）を
    # 消さないよう、2つ以上あるときだけ落とす
    while len(head) > 1 and head[-1].strip('()') in TC_RARITY:
        head = head[:-1]
    name = ' '.join(head)
    if not name:
        return None
    cond = 'A'
    for nt in notes:
        if nt.startswith('状態') and len(nt) > 2:
            # 状態A-・B+ などは頭文字に丸める（A/B/C/Dの4段だけ扱う）
            cond = nt[2]
    if cond not in ('A', 'B', 'C', 'D'):
        cond = 'A'
    v = (p.get('variants') or [{}])[0]
    try:
        price = int(float(v.get('price') or 0))
    except Exception:
        price = 0
    if not price:
        return None
    return {
        'shop': 'TC', 'pid': 'tc' + str(p.get('id')), 'cond': cond, 'vid': str(v.get('id') or ''),
        'url': TC + '/products/' + (p.get('handle') or ''),
        'name': name + (' (' + '/'.join(n for n in notes if not n.startswith('状態')) + ')' if
                        [n for n in notes if not n.startswith('状態')] else ''),
        'rarity': '', 'num': num, 'setcode': setcode,
        'settitle': settitle, 'price': price,
        'stock': 0, 'soldout': not v.get('available')}


def tc_scrape():
    """products.json は100ページ（2.5万件）で頭打ちになるので、
    収録弾ごとのコレクションを1つずつたどる"""
    cols = []
    for attempt in range(2):
        for page in range(1, 6):
            d = tc_json('/collections.json?limit=250&page=%d' % page)
            c = d.get('collections') or []
            if not c:
                break
            cols += c
            time.sleep(.5)
        if cols:
            break
        log('  トレカキャンプの一覧が取れないので60秒待ちます')
        time.sleep(60)
    log('  トレカキャンプ コレクション %d件' % len(cols))
    items, miss = {}, 0
    for i, col in enumerate(cols):
        # 「#neo1_金、銀、新世界へ…」のような弾名。旧裏の絞り込みに使う
        settitle = (col.get('title') or '').split('/')[0].strip()
        for page in range(1, 21):
            d = tc_json('/collections/%s/products.json?limit=250&page=%d'
                        % (urllib.parse.quote(col.get('handle') or '', safe=''), page))
            ps = d.get('products') or []
            if not d:
                # 429で弾かれ続けている。粘ると余計に閉じられるので早めに諦める
                miss += 1
                if miss >= 5:
                    log('  トレカキャンプに繋がらないので今回は見送ります')
                    return []
                break
            miss = 0
            if not ps:
                break
            for p in ps:
                if p.get('id') in items:
                    continue
                it = tc_parse(p, settitle)
                if it:
                    items[p['id']] = it
            if len(ps) < 250:
                break
            time.sleep(1.0)
        if (i + 1) % 100 == 0:
            log('  トレカキャンプ %d/%d コレクション 累計%d件' % (i + 1, len(cols), len(items)))
        time.sleep(.8)
    log('  トレカキャンプ %d件' % len(items))
    return list(items.values())


# ── トレトク ─────────────────────────────────────────
TT = 'https://www.toretoku.jp'
TT_LIST = re.compile(r'<ul class="resultList[^"]*">(.*?)</ul>', re.S)
TT_ITEM = re.compile(r'<li class="list">.*?(?=<li class="list">|\Z)', re.S)
TT_MODEL = re.compile(r'^(\S+)\s+(\d[0-9A-Za-z\-]*/[0-9A-Za-z\-]+)$')


def tt_get(url, tries=4):
    for i in range(tries):
        try:
            r = urllib.request.Request(url, headers={
                'User-Agent': UA, 'Accept': 'text/html', 'Accept-Language': 'ja'})
            return urllib.request.urlopen(r, timeout=90).read().decode('utf-8', 'replace')
        except Exception as e:
            log('  retry %d: %s' % (i + 1, e))
            _backoff(e, i)
    return ''


def tt_parse(seg, cattitle):
    def g(pat):
        m = re.search(pat, seg, re.S)
        return m.group(1) if m else ''
    def clean(x):
        return re.sub(r'\s+', ' ', re.sub(r'<[^>]+>', '', x)).strip()
    pid = g(r'/item/details/(\d+)')
    name = clean(g(r'<p class="name">(.*?)</p>'))
    if not pid or not name:
        return None
    if '日本語' not in clean(g(r'<p class="language">(.*?)</p>')):
        return None                      # 日本語版だけ
    model = clean(g(r'<p class="modelNumber">(.*?)</p>'))
    setcode, num = '', ''
    m = TT_MODEL.match(model)
    if m:
        setcode, num = m.group(1), m.group(2).upper()
    elif model:
        setcode = model.split()[0]       # 「旧1 No.025」= 旧裏。番号は使えない
    try:
        price = int(clean(g(r'<div class="price.*?</span>\s*([\d,]+)\s*<small>')).replace(',', ''))
    except Exception:
        price = 0
    if not price:
        return None
    stock = 0
    ms = re.search(r'在庫数[：:]\s*(\d+)', clean(g(r'<div class="number[^"]*">(.*?)</div>')))
    if ms:
        stock = int(ms.group(1))
    img = g(r'<img src="([^"]+)"')
    if img.startswith('/'):
        img = TT + img
    rank = g(r'rankIcon rank([A-Z])') or 'A'
    return {'shop': 'TT', 'pid': 'tt' + pid, 'cond': rank if rank in 'ABCD' else 'A',
            'url': TT + '/item/details/' + pid, 'name': name, 'rarity': '',
            'num': num, 'setcode': setcode, 'settitle': cattitle,
            'price': price, 'stock': stock, 'soldout': stock <= 0, 'img': img}


def tt_scrape():
    """トレトク。ポケモンのカテゴリを1つずつ、1ページ250件でたどる"""
    top = tt_get(TT + '/pokemon')
    cats, seen = [], set()
    for m in re.finditer(r'<a[^>]+href="(https://www\.toretoku\.jp/category/40/\d+/\d+)"[^>]*>(.*?)</a>',
                         top, re.S):
        url = m.group(1)
        title = re.sub(r'\s+', ' ', re.sub(r'<[^>]+>', '', m.group(2))).strip()
        if url in seen or not title:
            continue
        seen.add(url)
        cats.append((url, title))
    log('  トレトク カテゴリ %d件' % len(cats))
    if not cats:
        return []
    items, miss = {}, 0
    for i, (url, title) in enumerate(cats):
        for page in range(1, 21):
            h = tt_get('%s?number=250&page=%d' % (url, page))
            if not h:
                miss += 1
                if miss >= 3:
                    log('  トレトクに繋がらないので今回は見送ります')
                    return []
                break
            miss = 0
            body = TT_LIST.search(h)
            if not body:
                break
            got = TT_ITEM.findall(body.group(1))
            for seg in got:
                it = tt_parse(seg, title)
                if it and it['pid'] not in items:
                    items[it['pid']] = it
            if len(got) < 250:
                break
            time.sleep(.3)
        if (i + 1) % 50 == 0:
            log('  トレトク %d/%d カテゴリ 累計%d件' % (i + 1, len(cats), len(items)))
        time.sleep(.25)
    log('  トレトク %d件' % len(items))
    return list(items.values())


# ── 福福トレカ（カラーミーショップ） ──────────────────
FF = 'https://pokemon.fukufukutoreka.com'
FF_ITEM = re.compile(r'<li class="product-list__item">.*?(?=<li class="product-list__item">|</ul>)', re.S)
# 「いちげきウーラオスV(075/070)[SA]【S5I】」…名前・番号・レアリティ・収録弾が全部入っている
FF_TITLE = re.compile(r'^(.*?)[(（]([^)）]*)[)）]\s*[\[［]([^\]］]*)[\]］]\s*【([^】]*)】')
FF_SKIP = ('PSA', '鑑定', 'ARS', '未開封', 'BOX', '英語版', '海外版', '韓国版')


def ff_scrape():
    """福福トレカ。カテゴリ（収録弾）ごとに1回ずつ、1ページ210件で全部取れる。
    状態の区分は無いので、すべて通常品(A)として扱う"""
    top = fetch(FF + '/')
    if not top:
        log('  福福トレカに繋がりません')
        return []
    cats = sorted({int(x) for x in re.findall(r'/products/list\?category_id=(\d+)', top)})
    log('  福福トレカ カテゴリ %d件' % len(cats))
    items, miss = {}, 0
    for i, cid in enumerate(cats):
        h = fetch('%s/products/list?category_id=%d&disp_number=5' % (FF, cid))
        if not h:
            miss += 1
            if miss >= 3:
                log('  福福トレカに繋がらないので今回は見送ります')
                return []
            continue
        miss = 0
        for seg in FF_ITEM.findall(h):
            m = re.search(r'/products/detail/(\d+)', seg)
            t = re.search(r'title--name text-link"[^>]*>([^<]+)<', seg)
            if not m or not t:
                continue
            pid = m.group(1)
            if pid in items:
                continue
            title = t.group(1).strip()
            if any(w in title for w in FF_SKIP):
                continue
            mt = FF_TITLE.match(title)
            if not mt:
                continue
            p = re.search(r'--price">[￥¥]?([\d,]+)', seg)
            if not p:
                continue
            st = re.search(r'<span>/(\d+)</span>', seg)
            items[pid] = {
                'shop': 'FF', 'pid': 'ff' + pid, 'cond': 'A',
                'url': FF + '/products/detail/' + pid,
                'name': mt.group(1).strip(), 'rarity': mt.group(3).strip(),
                'num': mt.group(2).strip().upper(), 'setcode': mt.group(4).strip(),
                'settitle': '', 'price': int(p.group(1).replace(',', '')),
                'stock': int(st.group(1)) if st else 0,
                'soldout': 'add_cart' not in seg}
        if (i + 1) % 40 == 0:
            log('  福福トレカ %d/%d カテゴリ 累計%d件' % (i + 1, len(cats), len(items)))
        time.sleep(.4)
    log('  福福トレカ %d件' % len(items))
    return list(items.values())


# ── ここから下は追加の店 ──────────────────────────────
# どの店も、返すのは同じ形の辞書:
#   shop / pid / cond / url / name / rarity / num / setcode / settitle /
#   price / stock / soldout
# 画像は手元のDBのものを使うので、店から取る必要はない。

# カード以外（未開封・オリパ・鑑定品・海外版）は買い得リストの対象外。
# 「パック」は収録弾の名前にも出るので入れない
SKIP_WORDS = ('PSA', '鑑定', 'ARS', '未開封', 'BOX', 'ボックス', '英語版', '海外版',
              '韓国版', '中国語', '北米版', '福袋', 'オリパ', 'くじ',
              'スリーブ', 'デッキケース', 'プレイマット', 'サプライ')


def curl_text(url, accept='text/html', data=None, tries=4):
    """外の店は curl に任せる。

    Pythonのurllibだと店によってはTLSの指紋で弾かれる（トレカキャンプで実際にあった）。
    data を渡すと JSON の POST になる（オルタのGraphQL用）。
    """
    args = ['curl', '-sS', '-L', '--compressed', '-m', '90', '-w', '\n%{http_code}',
            '-A', UA, '-H', 'Accept: ' + accept, '-H', 'Accept-Language: ja,en;q=0.8']
    tmp = ''
    if data is not None:
        # 日本語を含むので、コマンドラインに直接書かずファイル経由で渡す
        tmp = os.path.join(HERE, '_post_%d.json' % os.getpid())
        with io.open(tmp, 'w', encoding='utf-8') as f:
            f.write(json.dumps(data, ensure_ascii=False))
        args += ['-H', 'Content-Type: application/json', '--data-binary', '@' + tmp]
    args.append(url)
    try:
        for i in range(tries):
            try:
                r = subprocess.run(args, stdout=subprocess.PIPE, stderr=subprocess.PIPE,
                                   timeout=120, creationflags=NOWIN)
                out = r.stdout.decode('utf-8', 'replace')
                code = out.rsplit('\n', 1)[-1].strip()
                body = out.rsplit('\n', 1)[0]
                if code == '200' and body.strip():
                    return body
                if code == '404':
                    return ''        # 存在しないページ。待っても現れない
                log('  retry %d: HTTP %s %s' % (i + 1, code or '?', url[:70]))
                time.sleep(max(20 * (i + 1), 20) if code in ('429', '403', '503') else 3)
            except Exception as e:
                log('  retry %d: %s' % (i + 1, e))
                time.sleep(3 + i * 3)
    finally:
        if tmp and os.path.exists(tmp):
            try:
                os.remove(tmp)
            except Exception:
                pass
    return ''


def curl_json(url, data=None, tries=4):
    body = curl_text(url, 'application/json', data, tries)
    if not body:
        return {}
    try:
        return json.loads(body)
    except Exception:
        log('  JSONとして読めません: %s' % url[:70])
        return {}


def zero_strip(code):
    """店によって収録弾の記号がゼロ埋めされている（m06a）。
    手元のDBは公式表記（M6a）なので、英字の直後のゼロだけを落とす。
    『SPROMO-100』のような数字は触らない"""
    return re.sub(r'(?<=[A-Z])0+(?=\d)', '', (code or '').upper())


# ── BIGWEB（公開APIをそのまま読む） ───────────────────
BW_API = 'https://api.bigweb.co.jp/products'
BW_VIEW = 'https://www.bigweb.co.jp/ja/products/pokemon/cardViewer/'
BW_GAME = 170                     # ポケモンカードゲーム
BW_SET = re.compile(r'^【([^】]+)】\s*(.*)$')
BW_NUM = re.compile(r'[(（]\s*([0-9A-Za-z]{1,4}/[0-9A-Za-z\-]{1,8})\s*[)）]')
BW_STATE = re.compile(r'【状態\s*([A-D])')
JP_CHAR = re.compile(r'[ぁ-んァ-ヶ一-龥]')


def bw_cond(it):
    """『プレイ用』＝そのまま使える、『特価[傷含む]』＝キズあり。
    旧裏の高額品だけは【状態B+】のように別途書かれるので、そちらを優先する"""
    txt = ' '.join(str(it.get(k) or '') for k in ('name', 'comment', 'sale_words', 'description'))
    m = BW_STATE.search(txt)
    if m:
        return m.group(1)
    w = ((it.get('condition') or {}).get('web') or '')
    return 'C' if ('傷' in w or '特価' in w) else 'A'


def bw_scrape():
    """BIGWEB。1ページ100件のJSONを最後まで読むだけ。
    旧裏・e・ADV まで在庫があり、収録弾の記号が商品側に入っているのが強み"""
    first = curl_json('%s?game_id=%d&page=1' % (BW_API, BW_GAME))
    pg = first.get('pagenate') or {}
    pages = pg.get('pageCount') or 0
    if not pages:
        log('  BIGWEBのAPIが読めません')
        return []
    log('  BIGWEB %dページ（在庫切れ含め%d件）' % (pages, pg.get('count') or 0))
    items = {}
    for p in range(1, pages + 1):
        d = first if p == 1 else curl_json('%s?game_id=%d&page=%d' % (BW_API, BW_GAME, p))
        got = d.get('items') or []
        if not got and p > 1:
            log('  BIGWEB %dページ目が空。ここで打ち切ります' % p)
            break
        for it in got:
            if it.get('is_sold_out') or (it.get('stock_count') or 0) <= 0:
                continue
            if it.get('is_box') or it.get('is_supply') or it.get('is_preorder_item'):
                continue
            price = int(it.get('price') or 0)
            if price <= 0:
                continue
            name = (it.get('name') or '').strip()
            if not name or any(w in name for w in SKIP_WORDS):
                continue
            # 日本語版のコレクションなので、英語名だけの商品（海外版）は入れない
            if not JP_CHAR.search(name):
                continue
            slip = ((it.get('cardset') or {}).get('slip') or '').strip()
            if any(w in slip for w in SKIP_WORDS):
                continue
            m = BW_SET.match(slip)
            code, title = (m.group(1), m.group(2)) if m else ('', slip)
            mn = BW_NUM.search(it.get('comment') or '') or BW_NUM.search(name)
            pid = 'bw%s' % it.get('id')
            items[pid] = {
                'shop': 'BW', 'pid': pid, 'cond': bw_cond(it),
                'url': BW_VIEW + str(it.get('id')),
                'name': BW_NUM.sub('', name).strip(),
                'rarity': ((it.get('rarity') or {}).get('web') or '').strip(),
                'num': mn.group(1).upper() if mn else '',
                # 【MBG/MBD】のように2弾ぶん書かれることがあるので先頭だけ見る
                'setcode': zero_strip(code.split('/')[0].strip()),
                'settitle': title, 'price': price,
                'stock': int(it.get('stock_count') or 0), 'soldout': False}
        if p % 50 == 0:
            log('  BIGWEB %d/%dページ 在庫あり%d件' % (p, pages, len(items)))
        time.sleep(.2)
    log('  BIGWEB %d件' % len(items))
    return list(items.values())


# ── 遊々亭（収録弾ごとの静的ページ） ───────────────────
YY = 'https://yuyu-tei.jp'
YY_VERS = re.compile(r'name="vers\[\]"[^>]*value="([a-z0-9_\-]+)"')
YY_BLOCK = re.compile(r'<div\s+class="(card-product[^"]*)"(.*?)'
                      r'(?=<div\s+class="card-product|<footer|</body)', re.S)
YY_IMG = re.compile(r'<img\s+src="https://card\.yuyu-tei\.jp/[^"]*"\s+alt="([^"]*)"')
YY_PRICE = re.compile(r'([\d,]+)\s*円')
YY_HID = re.compile(r'value="([^"]*)"\s+class="cart_(cid|ver|kizu)"')
# 在庫は「◯」（潤沢）か「2 点」（残りわずか）か「×」（売切）の3通り
YY_ZAIKO = re.compile(r'在庫\s*:\s*([^<]*)<')
# alt は「134/103 FUR ミュウツーex」の形（番号・レアリティ・カード名）
YY_ALT = re.compile(r'^(\S+/\S+)\s+(\S+)\s+(.+)$')


def yy_scrape():
    """遊々亭。収録弾ごとに1ページで全部載っているので、弾の数だけ取る。
    キズありは cart_kizu=1 で区別されている"""
    top = fetch(YY + '/sell/poc/s/m06a')
    if not top:
        log('  遊々亭に繋がりません')
        return []
    vers = []
    for v in YY_VERS.findall(top):
        if v not in vers:
            vers.append(v)
    if not vers:
        log('  遊々亭の収録弾リストが読めません')
        return []
    # キズあり品は弾のページには出ず、この一覧にだけ載る
    vers.append('damage')
    log('  遊々亭 収録弾 %d件（＋キズあり一覧）' % (len(vers) - 1))
    items, miss = {}, 0
    for i, v in enumerate(vers):
        h = top if v == 'm06a' else fetch('%s/sell/poc/s/%s' % (YY, v))
        if not h:
            miss += 1
            if miss >= 5:
                log('  遊々亭に繋がらないので今回は見送ります')
                return []
            continue
        miss = 0
        code = zero_strip(v)
        for cls, seg in YY_BLOCK.findall(h):
            if 'sold-out' in cls:
                continue
            alt = YY_IMG.search(seg)
            if not alt:
                continue
            ma = YY_ALT.match(alt.group(1).strip())
            if not ma:
                continue
            hid = dict((k, val) for val, k in YY_HID.findall(seg))
            cid = hid.get('cid') or ''
            if not cid:
                continue
            p = YY_PRICE.search(seg)
            if not p:
                continue
            z = YY_ZAIKO.search(seg)
            zt = (z.group(1).strip() if z else '')
            if not zt or zt[0] in '×xX':
                continue
            digits = re.sub(r'\D', '', zt)
            kizu = (hid.get('kizu') or '0') != '0'
            # キズありの一覧（/s/damage）は弾がばらばらなので、
            # ページではなく商品じたいが持っている弾を見る
            ver = hid.get('ver') or v
            pid = 'yy%s-%s%s' % (ver, cid, 'k' if kizu else '')
            name = ma.group(3).strip()
            if any(w in name for w in SKIP_WORDS):
                continue
            items[pid] = {
                'shop': 'YY', 'pid': pid, 'cond': 'C' if kizu else 'A',
                'url': '%s/sell/poc/card/%s/%s' % (YY, ver, cid),
                'name': name, 'rarity': ma.group(2).strip(),
                'num': ma.group(1).strip().upper(), 'setcode': zero_strip(ver) or code,
                'settitle': '', 'price': int(p.group(1).replace(',', '')),
                'stock': int(digits) if digits else 9, 'soldout': False}
        if (i + 1) % 50 == 0:
            log('  遊々亭 %d/%d弾 累計%d件' % (i + 1, len(vers), len(items)))
        time.sleep(.3)
    log('  遊々亭 %d件' % len(items))
    return list(items.values())


# ── トレコロ（カードボックス／ecbeing） ───────────────
TR = 'https://www.torecolo.jp'
TR_MENU = TR + '/side_menu/pc_side_menu.html'
TR_CAT = re.compile(r'/shop/c/(c1074\d{2,6})/')
TR_CODE = re.compile(r'/shop/g/g([^/"]+)/')
TR_NAME = re.compile(r'js-enhanced-ecommerce-goods-name"[^>]*>([^<]+)<')
TR_TITLE = re.compile(r'data-category="([^"(]*)')
TR_RAR = re.compile(r'goods-category ellipsis line\d">([^<]*)<')
TR_PRICE = re.compile(r'goods-price">\s*([\d,]+)\s*円')
TR_STOCK = re.compile(r'product-stock">在庫\s*<span>(\d+)</span>')
TR_VAR = re.compile(r'variation-name[^>]*>([^<]*)<')
# 商品コードは「135-103-M6A-B-K-SALE」＝ 番号・分母・収録弾・おまけの記号。
# 収録弾は「S8A-P」のようにハイフンを含むことがあるので、
# うしろから記号を落とすのではなく、前から記号に当たるまでを弾として読む
TR_FLAG = re.compile(r'^(?:B|K|N|A|C|D|KIZU|SALE\d*)$')


def tr_code(code):
    parts = (code or '').split('-')
    if len(parts) < 2:
        return '', ''
    if parts[1].isdigit():
        num, rest = '%s/%s' % (parts[0], parts[1]), parts[2:]
    else:
        num, rest = '', parts[1:]
    ver = []
    for seg in rest:
        if TR_FLAG.match(seg):
            break
        ver.append(seg)
    return num.upper(), zero_strip('-'.join(ver))


def tr_cond(var):
    """状態は「（商品状態・中古良品）」の形で書かれている"""
    if 'キズ' in var or '傷' in var or 'プレイ用' in var:
        return 'C'
    if '良品' in var:
        return 'B'
    return 'A'


def tr_category(cat):
    """1カテゴリぶんをめくる。取れなかったら False を返して呼び元に知らせる"""
    out = {}
    for page in range(1, 41):        # 1カテゴリ2000件で頭打ち
        url = '%s/shop/c/%s/%s' % (TR, cat, '?page=%d' % page if page > 1 else '')
        h = fetch(url, tries=2)
        if not h:
            return out, False
        shown = 0                    # そのページに商品が並んでいたか（ページ送りの判断用）
        for seg in h.split('<dl class="block-thumbnail-t--goods')[1:]:
            mc = TR_CODE.search(seg)
            mn = TR_NAME.search(seg)
            mp = TR_PRICE.search(seg)
            if not (mc and mn and mp):
                continue
            code = mc.group(1)
            var = (TR_VAR.search(seg).group(1) if TR_VAR.search(seg) else '')
            pid = 'tr' + code
            if pid in out:
                continue
            shown += 1
            name = unicodedata.normalize('NFKC', mn.group(1)).strip()
            if not name or any(w in name for w in SKIP_WORDS):
                continue
            # 売り切れも「在庫 0」で一覧に出てくるのでここで落とす。
            # 数が書かれていないのは作りが変わったときなので、そのときも出さない
            ms = TR_STOCK.search(seg)
            stock = int(ms.group(1)) if ms else 0
            if stock <= 0:
                continue
            # キズあり在庫は一覧に「有／無」としか出ず、値段が分からないので扱わない
            num, setcode = tr_code(code)
            mr = TR_RAR.search(seg)
            mtt = TR_TITLE.search(seg)
            out[pid] = {
                'shop': 'TR', 'pid': pid, 'cond': tr_cond(var),
                'url': '%s/shop/g/g%s/' % (TR, code),
                'name': name,
                'rarity': unicodedata.normalize('NFKC', mr.group(1)).strip() if mr else '',
                'num': num.upper(), 'setcode': setcode,
                'settitle': (mtt.group(1).strip() if mtt else ''),
                'price': int(mp.group(1).replace(',', '')),
                'stock': stock, 'soldout': False}
        if shown == 0:
            break
        time.sleep(.2)
    return out, True


def tr_scrape():
    """トレコロ。収録弾ごとのカテゴリを1ページ50件でめくる。
    収録弾の記号が商品コードに入っているので、弾の取り違えが起きにくい。
    カテゴリが400近くあるので、何本か並行して読む"""
    menu = fetch(TR_MENU)
    cats = sorted(set(TR_CAT.findall(menu or '')))
    if not cats:
        log('  トレコロのカテゴリ一覧が読めません')
        return []
    log('  トレコロ カテゴリ %d件' % len(cats))
    items, bad, done = {}, 0, 0
    with futures.ThreadPoolExecutor(max_workers=5) as ex:
        for f in futures.as_completed([ex.submit(tr_category, c) for c in cats]):
            try:
                got, ok = f.result()
            except Exception:
                got, ok = {}, False
            done += 1
            items.update(got)
            if not ok:
                bad += 1
            if done % 100 == 0:
                log('  トレコロ %d/%dカテゴリ 累計%d件' % (done, len(cats), len(items)))
    if bad:
        log('  トレコロ %dカテゴリは取れませんでした' % bad)
    log('  トレコロ %d件' % len(items))
    return list(items.values())


# ── PRICE BASE（futureshop） ──────────────────────────
PB = 'https://shop.price-base.com'
PB_CAT = re.compile(r'href="(/c/pokemon/[a-z0-9\-]+)"')
PB_LINK = re.compile(r'href="(/c/pokemon/[a-z0-9\-]+/[a-z0-9\-]+)"')
PB_NAME = re.compile(r'fs-c-productName__name">([^<]{1,90})<')
PB_PRICE = re.compile(r'fs-c-price__value">([\d,]+)<')
# 「ドガース（001/055）［C］【ADVシリーズ】」＝ 名前・番号・レアリティ・シリーズ
PB_TITLE = re.compile(r'^(.*?)[（(]([0-9A-Za-z]{1,4}/[0-9A-Za-z\-]{1,8})[）)]\s*'
                      r'[［\[]([^］\]]*)[］\]]\s*(?:【([^】]*)】)?')


PB_LD = re.compile(r'<script[^>]*application/ld\+json[^>]*>(.*?)</script>', re.S)
PB_COND = re.compile(r'状態\s*([A-D])')


def pb_product(url):
    """商品ページの構造化データから、状態ごとの値段と在庫を読む。
    一覧の値段は売り切れの状態まで含めた最安値なので当てにならない"""
    h = fetch(url, tries=2)
    if not h:
        return None
    for blk in PB_LD.findall(h):
        try:
            d = json.loads(blk)
        except Exception:
            continue
        if d.get('@type') != 'ProductGroup':
            continue
        out = []
        for v in (d.get('hasVariant') or []):
            of = v.get('offers') or {}
            if 'InStock' not in str(of.get('availability') or ''):
                continue          # 売り切れの状態は出さない
            price = int(float(of.get('price') or 0))
            if price <= 0:
                continue
            mc = PB_COND.search(v.get('name') or '')
            out.append((mc.group(1) if mc else 'A', price))
        return out
    return None


def pb_links():
    """一覧をめくって、商品の場所（URL）と商品名だけを集める"""
    top = fetch(PB + '/c/pokemon')
    cats = sorted({c for c in PB_CAT.findall(top or '') if c.count('/') == 3})
    if not cats:
        log('  PRICE BASE のカテゴリ一覧が読めません')
        return {}
    log('  PRICE BASE カテゴリ %d件' % len(cats))
    found, miss = {}, 0
    for i, c in enumerate(cats):
        for page in range(1, 21):
            url = PB + c + ('?page=%d' % page if page > 1 else '')
            h = fetch(url)
            if not h:
                miss += 1
                if miss >= 5:
                    log('  PRICE BASE に繋がらないので今回は見送ります')
                    return {}
                break
            miss = 0
            added = 0
            for seg in h.split('fs-c-productListItem__image')[1:]:
                mn = PB_NAME.search(seg)
                ml = PB_LINK.search(seg)
                if not (mn and ml):
                    continue
                slug = ml.group(1)
                if slug in found:
                    continue
                added += 1
                # 一覧の「在庫切れ」は商品ごと。付いていれば状態A〜Dすべて在庫なし
                if '在庫切れ' in seg:
                    continue
                title = unicodedata.normalize('NFKC', mn.group(1)).strip()
                title = re.sub(r'^【[^】]*】\s*', '', title)
                if any(w in title for w in SKIP_WORDS):
                    continue
                mt = PB_TITLE.match(title)
                if not mt:
                    continue      # 番号が書かれていない商品は当てられないので捨てる
                found[slug] = (mt.group(1).strip(), mt.group(2).upper(),
                               (mt.group(3) or '').strip(), (mt.group(4) or '').strip())
            if added == 0:
                break
            time.sleep(.25)
        if (i + 1) % 50 == 0:
            log('  PRICE BASE %d/%dカテゴリ 商品%d件' % (i + 1, len(cats), len(found)))
    return found


def pb_scrape():
    """PRICE BASE。1商品に状態A〜Dがぶら下がっていて、一覧に出ている値段は
    売り切れの状態まで含めた最安値なので当てにならない。
    商品ページを見て、在庫のある状態だけを出す"""
    found = pb_links()
    if not found:
        return []
    log('  PRICE BASE 商品 %d件。状態ごとの在庫を見にいきます' % len(found))
    items, done = {}, 0
    with futures.ThreadPoolExecutor(max_workers=5) as ex:
        jobs = {ex.submit(pb_product, PB + slug): slug for slug in found}
        for f in futures.as_completed(jobs):
            slug = jobs[f]
            done += 1
            try:
                conds = f.result()
            except Exception:
                conds = None
            if not conds:
                continue          # 全部売り切れ、または読めなかった
            name, num, rar, settitle = found[slug]
            for cond, price in conds:
                pid = 'pb' + slug.rsplit('/', 1)[-1] + '-' + cond.lower()
                items[pid] = {
                    'shop': 'PB', 'pid': pid, 'cond': cond,
                    'url': PB + slug,
                    'name': name, 'rarity': rar, 'num': num,
                    'setcode': '', 'settitle': settitle,
                    'price': price, 'stock': 1, 'soldout': False}
            if done % 500 == 0:
                log('  PRICE BASE %d/%d商品 累計%d件' % (done, len(found), len(items)))
    log('  PRICE BASE %d件' % len(items))
    return list(items.values())


# ── カードショップオルタ（GraphQL） ───────────────────
OL = 'https://olta-tcg.com'
OL_Q = """query F($page:Int,$perPage:Int,$where:ProductFaceWhereInput){
  productFaces(page:$page,perPage:$perPage,where:$where){
    count pageCount
    items{ name code
      productSkus{ skuCode price stock }
      productTags{ name }
    }
  }
}"""
# 商品名は「ナゾノクサ[タネばくだん][M2/001/080]」＝ 最後の[]に 弾/番号/総数
OL_NAME = re.compile(r'^(.*?)\s*\[([0-9A-Za-z\-]{1,10})/([0-9A-Za-z]{1,4})/([0-9A-Za-z]{1,6})\]\s*$')
OL_SKU = re.compile(r'^condition-([a-e])-')
OL_COND = {'a': 'A', 'b': 'B', 'c': 'C', 'd': 'D', 'e': 'D'}


def ol_scrape():
    """カードショップオルタ。状態A〜Eが1商品にぶら下がっていて、
    それぞれ別の値段が付いている。安い状態だけを拾える"""
    items = {}
    page, pages = 1, 1
    while page <= pages:
        d = curl_json(OL + '/api', {'query': OL_Q, 'variables': {
            'page': page, 'perPage': 100,
            'where': {'cardTitle': {'is': {'code': {'equals': 'pokemon'}}}}}})
        pf = ((d.get('data') or {}).get('productFaces') or {})
        got = pf.get('items') or []
        if page == 1:
            pages = pf.get('pageCount') or 0
            if not pages:
                log('  オルタのAPIが読めません')
                return []
            log('  オルタ %dページ（在庫切れ含め%d件）' % (pages, pf.get('count') or 0))
        if not got:
            break
        for it in got:
            mt = OL_NAME.match((it.get('name') or '').strip())
            if not mt:
                continue
            name = re.sub(r'\[[^\]]*\]', '', mt.group(1)).strip()
            if not name or any(w in name for w in SKIP_WORDS):
                continue
            tags = [(t.get('name') or '').strip() for t in (it.get('productTags') or [])]
            rar = next((t for t in tags if t in TC_RARITY), '')
            for sk in (it.get('productSkus') or []):
                if (sk.get('stock') or 0) <= 0 or (sk.get('price') or 0) <= 0:
                    continue
                ms = OL_SKU.match(sk.get('skuCode') or '')
                if not ms:
                    continue
                pid = 'ol%s-%s' % (it.get('code'), ms.group(1))
                items[pid] = {
                    'shop': 'OL', 'pid': pid, 'cond': OL_COND[ms.group(1)],
                    'url': '%s/pokemon/product/detail/%s' % (OL, it.get('code')),
                    'name': name, 'rarity': rar,
                    'num': '%s/%s' % (mt.group(3), mt.group(4)),
                    'setcode': zero_strip(mt.group(2)), 'settitle': '',
                    'price': int(sk['price']), 'stock': int(sk['stock']), 'soldout': False}
        if page % 30 == 0:
            log('  オルタ %d/%dページ 累計%d件' % (page, pages, len(items)))
        page += 1
        time.sleep(.25)
    log('  オルタ %d件' % len(items))
    return list(items.values())


# ── DMMマイカ（全国の店が集まるモール） ───────────────
MY = 'https://myca.dmm.com'
MY_LIST = MY + '/pokemon-trading-card-game/list'
MY_SPLIT = 'href="/pokemon-trading-card-game/items/single-card/'
MY_ID = re.compile(r'^(\d+)[?"]')
MY_ANCHOR = re.compile(r'^\d+[^>]*>(.*?)</a>', re.S)
MY_COND = re.compile(r'状態([A-Z])')
MY_YEN = re.compile(r'¥(?:<!--\s*-->)?([\d,]+)')
MY_META = re.compile(r'text-muted-foreground[^>]*>([^<]{1,24})<')
MY_NUM = re.compile(r'([0-9A-Za-z]{1,4}/[0-9A-Za-z\-]{1,8})\s*$')
MY_SHOP = re.compile(r'/other/shops/([A-Za-z0-9]+)"[^>]*>([^<]{1,40})')
MY_SEND = re.compile(r'送料について\s*(.{0,60})', re.S)


def my_shops():
    """店の一覧（番号と名前）。どのポケカ商品ページにも全店のリンクが入っている"""
    h = curl_text('%s/pokemon-trading-card-game/list' % MY)
    ids = sorted(set(re.findall(r'/other/shops/([A-Za-z0-9]+)', h or '')))
    if not ids:
        return {}
    names = {}
    for m in MY_SHOP.finditer(h):
        names.setdefault(m.group(1), m.group(2).strip())
    out = {}
    for b in ids:
        try:
            num = base64.b64decode(b + '=' * (-len(b) % 4)).decode()
        except Exception:
            continue
        if num.isdigit():
            out[num] = (b, names.get(b, '店' + num))
    return out


def my_shop_page(b64):
    """店のページから、店名と送料を取る。
    一覧の下に並ぶリンクからは名前が取れない店があるので、ここで拾い直す"""
    h = curl_text('%s/other/shops/%s' % (MY, b64), tries=2)
    if not h:
        return '', ''
    mt = re.search(r'<title>([^<|]+)', h)
    name = mt.group(1).strip() if mt else ''
    t = re.sub(r'<[^>]+>', ' ', h)
    m = MY_SEND.search(t)
    ship = re.sub(r'\s+', ' ', m.group(1)).strip() if m else ''
    # ページの中に埋め込まれたJSONを拾ってしまうことがあるので、その時は書き直す
    if '\"' in ship or '":"' in ship:
        m2 = re.search(r'shippingPolicy\\?":\\?"([^"\\]{4,60})', h)
        ship = re.sub(r'\\s+', ' ', m2.group(1)).strip() if m2 else ''
    return name, ship.split('■')[0].strip()[:40]   # キャンセルポリシー以降は要らない


def my_parse(h, shop):
    """一覧のHTMLから商品を取り出す"""
    out = {}
    for seg in h.split(MY_SPLIT)[1:]:
        mi = MY_ID.match(seg)
        ma = MY_ANCHOR.match(seg)
        if not (mi and ma):
            continue
        pid = 'my' + mi.group(1)
        head = re.sub(r'<!--.*?-->', '', ma.group(1))
        text = unicodedata.normalize('NFKC', re.sub(r'<[^>]+>', ' ', head)).strip()
        mn = MY_NUM.search(text)
        name = (text[:mn.start()] if mn else text).strip()
        if not name or any(w in name for w in SKIP_WORDS):
            continue
        body = seg[:3000]
        mp = MY_YEN.search(body)
        if not mp:
            continue
        mc = MY_COND.search(body)
        rar, code = '', ''
        # 「AR/M6a」のようにレアリティと収録弾が並ぶ。弾だけのこともある
        for v in MY_META.findall(body):
            if '/' in v:
                rar, code = v.split('/', 1)
                break
            if not code:
                code = v
        out[pid] = {
            'shop': 'MY', 'pid': pid,
            'cond': mc.group(1) if mc and mc.group(1) in 'ABCD' else 'A',
            'url': '%s/pokemon-trading-card-game/items/single-card/%s' % (MY, mi.group(1)),
            'name': name, 'rarity': rar.strip(),
            'num': mn.group(1).upper() if mn else '',
            'setcode': zero_strip(code.strip()), 'settitle': '',
            'price': int(mp.group(1).replace(',', '')),
            'stock': 1, 'soldout': False, 'mall': shop}
    return out


def my_one(num, b64, shop):
    """1店ぶん。店名と送料も一緒に持ち帰る"""
    page_name, ship = my_shop_page(b64)
    if page_name:
        shop = page_name
    got, blank = {}, 0
    for page in range(1, 61):
        h = curl_text('%s?storeIds=%s&page=%d' % (MY_LIST, num, page), tries=2)
        if not h:
            blank += 1
            if blank >= 3:
                break
            continue
        add = my_parse(h, shop)
        new = {k: v for k, v in add.items() if k not in got}
        if not new:
            blank += 1
            if blank >= 3:
                break
        else:
            blank = 0
            got.update(new)
        time.sleep(.2)
    return got, shop, ship


MY_SHIP = {}          # 店名 → 送料の書き方（ページに出す）


def my_scrape():
    """DMMマイカ。店ごとに取る。同じ注文でも店が違えば送料が別にかかるので、
    どの店の出品かが分からないと買い方を決められない"""
    shops = my_shops()
    if not shops:
        log('  DMMマイカの店一覧が読めません')
        return []
    log('  DMMマイカ 店 %d件' % len(shops))
    items, done = {}, 0
    MY_SHIP.clear()
    with futures.ThreadPoolExecutor(max_workers=5) as ex:
        jobs = {ex.submit(my_one, num, b64, name): name
                for num, (b64, name) in shops.items()}
        for f in futures.as_completed(jobs):
            name = jobs[f]
            done += 1
            try:
                got, name, ship = f.result()
            except Exception:
                got, ship = {}, ''
            items.update(got)
            if ship:
                MY_SHIP[name] = ship
            if done % 25 == 0:
                log('  DMMマイカ %d/%d店 累計%d件' % (done, len(shops), len(items)))
    log('  DMMマイカ %d件（%d店）' % (len(items), len(MY_SHIP)))
    return list(items.values())


# ── 駿河屋 ───────────────────────────────────────────
SG = 'https://www.suruga-ya.jp'
# シングルカードの一覧。旧裏面は別カテゴリになっている。
# 現行は7.7万件（3,227ページ）あるうえ、在庫があるのは1ページに1〜3件しかない。
# しかも現行は他の10店でほぼ拾えるので、新着順で上の方だけ見る。
# 旧裏面は2,205件（92ページ）と小さく、ここにしか無い在庫なので全部見る。
SG_CATS = [('501080047', '旧裏面', 92, ''),
           ('501080033', '現行', 300, '&rankBy=modificationTime%3Adescending')]
SG_LIST = (SG + '/search?category=%s&search_word=&boxpacksingle='
           '%%E3%%82%%B7%%E3%%83%%B3%%E3%%82%%B0%%E3%%83%%AB&inStock=On&page=%d%s')
SG_ITEM = re.compile(r'<div class="item_box.*?(?=<div class="item_box|<div class="pagination|</body)', re.S)
SG_URL = re.compile(r'href="(/product/detail/[^"?]+)')
SG_NAME = re.compile(r'<h3 class="product-name">([^<]+)</h3>')
# 在庫があるときは「中古：￥1,580 税込」、無いときは「品切れ」。
# 店舗によって値段が違うと「￥470 ～ ￥780」と幅で出るので、安いほうを採る
SG_PRICE = re.compile(r'<p class="price[^"]*"[^>]*>(.{0,400}?)</p>', re.S)
SG_YEN = re.compile(r'￥\s*([\d,]+)')
SG_SET = re.compile(r'class="condition background-kishu"[^>]*>\s*([^<]+?)\s*</p>', re.S)
# 「129/103[SAR]：(キラ)ミュウex」＝ 番号・レアリティ・名前
SG_TITLE = re.compile(r'^([0-9A-Za-z][0-9A-Za-z\-#]*(?:/[0-9A-Za-z\-]+)?)'
                      r'(?:\[([^\]]*)\])?\s*[:：]\s*(.+)$')
SG_HURT = ('キズ', '傷', '状態難', 'ジャンク', 'プレイ用')
# 駿河屋は「(Bランク)」のように状態を名前に書いてくる。そのまま状態として使える
SG_RANK = re.compile(r'[(（]\s*([A-D])\s*ランク[^)）]*[)）]')


def sg_page(cat, page, extra=''):
    h = fetch(SG_LIST % (cat, page, extra), tries=2)
    if not h:
        return None
    out = {}
    pos = list(SG_NAME.finditer(h))
    for i, m in enumerate(pos):
        before = h[(pos[i - 1].end() if i else 0):m.start()]
        after = h[m.end():(pos[i + 1].start() if i + 1 < len(pos) else len(h))]
        us = SG_URL.findall(before)
        mp = SG_PRICE.search(after)
        if not (us and mp):
            continue
        if '品切' in mp.group(1):
            continue                   # 売り切れは出さない
        yens = SG_YEN.findall(mp.group(1))
        if not yens:
            continue
        price = min(int(y.replace(',', '')) for y in yens)
        title = unicodedata.normalize('NFKC', m.group(1)).strip()
        head, sep, rest = title.partition(':')
        if not sep:
            continue                   # 番号とカード名が分かれていないものは捨てる
        mrank = SG_RANK.search(rest)
        name = SG_RANK.sub('', rest)
        name = re.sub(r'^\([^)]*\)', '', name).strip()
        if not name or any(w in title for w in SKIP_WORDS):
            continue
        mnum = re.search(r'([0-9A-Za-z][0-9A-Za-z\-]*/[0-9A-Za-z\-]+)', head)
        mrar = re.search(r'\[([^\]]*)\]', head)
        ms = SG_SET.search(after)
        settitle = unicodedata.normalize('NFKC', ms.group(1)).strip() if ms else ''
        # 旧裏は番号が振られていないので、名前で引く印を立てる（DB側も番号を持っていない）
        num = mnum.group(1).upper() if mnum else ('旧裏' if '旧裏' in settitle else '')
        pid = 'sg' + us[-1].rsplit('/', 1)[-1]
        out[pid] = {
            'shop': 'SG', 'pid': pid,
            'cond': (mrank.group(1) if mrank else
                     ('C' if any(w in title for w in SG_HURT) else 'A')),
            'url': SG + us[-1],
            'name': name, 'rarity': (mrar.group(1) if mrar else '').strip(),
            'num': num,
            'setcode': '',
            'settitle': settitle,
            'price': price, 'stock': 1, 'soldout': False}
    return out


def sg_scrape():
    """駿河屋。1ページ24件と細かいので、何本か並行してめくる。
    旧弾・旧裏面の在庫が厚いのがここの強み"""
    items, bad = {}, 0
    for cat, label, cap, extra in SG_CATS:
        h = fetch(SG_LIST % (cat, 1, extra))
        if not h:
            log('  駿河屋（%s）に繋がりません' % label)
            continue
        m = re.search(r'([\d,]{2,12})\s*件', h)
        total = int(m.group(1).replace(',', '')) if m else 0
        pages = min((total + 23) // 24, cap) if total else min(40, cap)
        log('  駿河屋（%s） %s件 → %dページ見ます' % (label, format(total, ','), pages))
        # 駿河屋は同時に叩くと中身の無いページを返してくる（4本並行で取りこぼした）。
        # 1本ずつ、少し間を置いて読む
        for n in range(1, pages + 1):
            got = sg_page(cat, n, extra)
            if got is None:
                bad += 1
                continue
            items.update(got)
            if n % 100 == 0:
                log('  駿河屋（%s） %d/%dページ 累計%d件' % (label, n, pages, len(items)))
            time.sleep(.3)
    if bad:
        log('  駿河屋 %dページは取れませんでした' % bad)
    log('  駿河屋 %d件' % len(items))
    return list(items.values())


def scrape():
    """店ごとに集めて、ちゃんと取れた店の集合も返す。
    取れなかった店のぶんは前回の内容をそのまま残す（売り切れ扱いにしない）"""
    only = None
    if '--only' in sys.argv:
        only = {x.upper() for x in sys.argv[sys.argv.index('--only') + 1].split(',')}
    items, ok = [], set()
    for shop, fn, least in (('CR', cr_scrape, 500), ('TC', tc_scrape, 500),
                            ('TT', tt_scrape, 500), ('FF', ff_scrape, 300),
                            ('BW', bw_scrape, 300), ('YY', yy_scrape, 300),
                            ('TR', tr_scrape, 300), ('PB', pb_scrape, 200),
                            ('OL', ol_scrape, 200), ('MY', my_scrape, 300),
                            ('SG', sg_scrape, 150)):     # 駿河屋は在庫のある物が少ない
        if only and shop not in only:
            log('  %s は今回スキップ（前回のぶんを残します）' % shop)
            continue
        try:
            got = fn()
        except Exception as e:
            log('%s の取得に失敗: %s' % (shop, e))
            got = []
        if len(got) >= least:
            items += got
            ok.add(shop)
        else:
            log('※ %s は %d件しか取れませんでした。前回のぶんをそのまま残します' % (shop, len(got)))
    return items, ok


# ── 2. 突き合わせ ─────────────────────────────────────
def norm(s):
    # ＆と&、全角英数と半角などの表記ゆれを吸収してから比べる
    return re.sub(r'[\s　]+', '', unicodedata.normalize('NFKC', s or ''))


def variant_of(text):
    """商品名から「どの版か」を読む。DBの名前の「:○○」と突き合わせるため"""
    v = set()
    if 'マスターボール' in text: v.add('master')
    elif 'モンスターボール' in text: v.add('monster')
    elif 'ミラー' in text: v.add('mirror')
    if '1ED' in text or '初版' in text: v.add('1ed')
    if 'キラ' in text and 'ノンキラ' not in text: v.add('kira')
    if 'マーク無' in text or 'マークな' in text: v.add('nomark')
    return v


def db_variant(suffix):
    v = set()
    if 'マスターボールミラー' in suffix: v.add('master')
    elif 'モンスターボールミラー' in suffix: v.add('monster')
    elif 'ミラー' in suffix: v.add('mirror')
    if '1ED' in suffix or '初版' in suffix: v.add('1ed')
    if 'キラ' in suffix and 'ノンキラ' not in suffix: v.add('kira')
    if 'マーク無' in suffix: v.add('nomark')
    return v


def _fetch_raw(sha, name):
    url = 'https://raw.githubusercontent.com/%s/%s/%s/%s' % (OWNER, REPO, sha, name)
    r = urllib.request.Request(url, headers={'User-Agent': UA})
    return json.loads(urllib.request.urlopen(r, timeout=180).read())


def load_db():
    """data.json に delta.json を重ねた、アプリに出ているのと同じ状態。

    GitHub から取る。手元の作業ツリーのファイルは git pull したときしか
    新しくならないので、そこを見ていると時価更新や＋1が何日も反映されない
    （相場が古いままだったのはこれが原因）。取れないときだけ手元を使う。
    """
    full = delta = None
    try:
        ref = json.loads(urllib.request.urlopen(urllib.request.Request(
            'https://api.github.com/repos/%s/%s/git/ref/heads/main' % (OWNER, REPO),
            headers={'User-Agent': UA}), timeout=60).read())
        sha = ref['object']['sha']
        full = _fetch_raw(sha, 'data.json')
        try:
            delta = _fetch_raw(sha, 'delta.json')
        except Exception:
            delta = None
        log('  DBはGitHubの %s から読みました' % sha[:7])
    except Exception as e:
        log('  GitHubからDBを取れませんでした（手元のファイルを使います）: %s' % e)
    if full is None:
        full = json.load(io.open(os.path.join(HERE, 'data.json'), encoding='utf-8'))
        try:
            delta = json.load(io.open(os.path.join(HERE, 'delta.json'), encoding='utf-8'))
        except Exception as e:
            log('delta.json を読めませんでした（data.json だけで続けます）: %s' % e)
            delta = None
    cards = {c['id']: c for c in full.get('cards', [])}
    for cid, card in ((delta or {}).get('ops') or {}).items():
        if card is None:
            cards.pop(cid, None)
        else:
            cards[cid] = card
    return cards


# 店が「旧裏」と言っているかどうか。カードラッシュは番号が {旧裏}、
# キャンプは 1st2 / neo1 / 1stGYM1、トレトクは 旧1 のような記号を使う
OLD_CODE = re.compile(r'^(1st|neo|旧|gym|opg|pmcg|vending)', re.I)


# 「PROMO」「その他」のように、どの弾か分からない書き方。収録弾の照合には使えない
GENERIC_CODE = {'promo', 'pr', 'p', 'その他', 'other', 'sp', 'etc', ''}


def _code_usable(code, num):
    code = (code or '').strip().lower()
    if code in GENERIC_CODE:
        return False
    # 「112/BW-P」のように番号の分母が弾名そのものなら、番号だけで十分決まる
    tail = (num or '').split('/')[-1]
    return tail.isdigit()


def _code_ok(code, setid):
    """店の収録記号とDBのsetIdが同じ弾を指していそうか。
    ADV1↔ad1 のように書き方が違うだけのことが多いので、頭2文字まで見る"""
    a = re.sub(r'[^a-z0-9]', '', (code or '').lower())
    b = re.sub(r'[^a-z0-9]', '', (setid or '').lower())
    if not a or not b:
        return True
    if b == a or b.startswith(a) or a.startswith(b):
        return True
    n = 0
    for x, y in zip(a, b):
        if x != y:
            break
        n += 1
    return n >= 2


def _is_old_back(it):
    if it.get('num') == '旧裏':
        return True
    if OLD_CODE.match((it.get('setcode') or '').strip()):
        return True
    t = it.get('settitle') or ''
    return ('旧裏' in t) or bool(OLD_CODE.match(t.strip()))


def match(items, cards):
    """迷ったら捨てる。間違った紐付けを1件出すほうが、取りこぼすより害が大きい"""
    by_num = collections.defaultdict(list)
    by_old = collections.defaultdict(list)     # 旧裏はDBに番号が無いので名前で引く
    by_set = collections.defaultdict(list)     # 番号が書かれていない商品は 収録記号＋名前 で引く
    for c in cards.values():
        n = (c.get('cardNumber') or '').strip().upper()
        if n and n != '-':
            by_num[n].append(c)
        if '旧裏' in (c.get('quickTags') or []):
            by_old[norm((c.get('name') or '').split(':')[0])].append(c)
        sid = (c.get('setId') or '').lower()
        if sid:
            by_set[(sid, norm((c.get('name') or '').split(':')[0]))].append(c)

    out, stat = [], collections.Counter()
    for it in items:
        raw, num = it['name'], it['num']
        if num == '/':
            num = ''
        base = norm(LV.sub('', PAREN.sub('', raw)))
        cand = []
        if not num:
            # 番号が書かれていない商品。収録記号（DP4 等）と名前が両方合う
            # ものが1枚だけのときに限って採用する
            mo = (it.get('setcode') or '').strip().lower()
            cand = list(by_set.get((mo, base), [])) if mo else []
            if not cand:
                stat['番号なし'] += 1
                continue
        elif num != '旧裏':
            cand = by_num.get(num, [])
            # セット記号（S8b 等）で絞る。DBのsetIdは s8b / s8b-m のように派生を持つ
            mo = (it.get('setcode') or '').strip().lower()
            if mo and mo != 'その他':
                nar = [c for c in cand if (c.get('setId') or '').lower() == mo
                       or (c.get('setId') or '').lower().startswith(mo + '-')]
                if nar:
                    cand = nar
            cand = [c for c in cand if norm((c.get('name') or '').split(':')[0]) == base]
            # 店が弾を名乗っているのに、DB側がまるで別の弾しか持っていないときは捨てる。
            # 番号と名前だけで当てると、同じ番号を使う別の弾のカードに化ける
            # （エンテイ PRE2 002/009 が「ポケパーク」の5万円に化けるたぐい）
            if cand and _code_usable(mo, num) and not _is_old_back(it):
                keep = [c for c in cand if _code_ok(mo, c.get('setId'))]
                if not keep:
                    stat['収録弾が合わない'] += 1
                    continue
                cand = keep
        old_hit = False
        if not cand and _is_old_back(it):
            old_hit = True
            # 旧裏はDB側に番号が無い（店は {旧裏} や「1st2 002/048」と書く）ので名前で引く。
            # 旧裏だと分かっている商品にだけ使う。番号が合わなかっただけの現行カードに
            # これを使うと、同名の旧裏カードに化ける（エンテイ PRE2 002/009 が
            # 「めざめる伝説」の4万円に紐づいていた）
            # 同じ名前が複数の弾にあるとき（リザードンLV.76は40万と65万）は
            # 取り違えの害が大きいので、下の同点判定で捨てる
            cand = by_old.get(base, [])
            # 店が弾名を持っているなら、それで候補を絞れる
            st = norm(re.sub(r'[（(].*', '', it.get('settitle') or '')).replace('…', '')
            if cand and st and len(st) >= 3:
                nar = [c for c in cand if st in norm(c.get('setName') or '')]
                if nar:
                    cand = nar
            if not cand:
                stat['旧裏でDBに無い'] += 1
                continue
        if not cand:
            stat['DBに無い'] += 1
            continue
        # 版（ミラー・1ED・キラ等）の判定は、かっこ書きとレアリティだけを見る。
        # カード名まで見ると「ドーミラー」の“ミラー”を拾って、ミラー版に化ける
        want = variant_of(' '.join(PAREN.findall(raw)) + ' ' + it.get('rarity', ''))
        # 「アンリミ」と書かれた商品を1ED版に当ててはいけない（値段が桁違いになる）
        notes = ' '.join(PAREN.findall(raw))
        unlim = 'アンリミ' in notes
        marked = 'マークあり' in notes or 'マーク有' in notes
        scored = []
        for c in cand:
            nm = c.get('name') or ''
            have = db_variant(nm.split(':', 1)[1] if ':' in nm else '')
            if unlim and '1ed' in have:
                continue
            if marked and 'nomark' in have:
                continue
            scored.append((3 * len(want & have) - 2 * len(want ^ have), c))
        scored.sort(key=lambda x: -x[0])
        if not scored or scored[0][0] < 0:
            stat['版が合わない'] += 1
            continue
        if len(scored) > 1 and scored[0][0] == scored[1][0]:
            stat['版の判断がつかない'] += 1
            continue
        c = scored[0][1]
        stat['照合できた(' + it['shop'] + ')'] += 1
        # 店が収録弾を書いていない商品（カードラッシュの「その他」）は、
        # 同じ番号・同じ名前の別の弾かもしれない。あとで印を出すために残す
        code = (it.get('setcode') or '').strip()
        # 番号の分母が弾名（112/BW-P）なら、記号が無くても弾は確定している
        sure = 1 if ((_code_usable(code, num) and _code_ok(code, c.get('setId')))
                     or not (num or '').split('/')[-1].isdigit()) else 0
        # 旧裏は番号が無く名前で当てているので、1ED・マークなし等の版まではわからない。
        # 値段が桁違いに見えるのはたいていこれなので、確実でない印を付けておく
        if old_hit:
            sure = 0
        out.append({'shop': it['shop'], 'pid': it['pid'], 'url': it['url'], 'sure': sure,
                    'vid': it.get('vid', ''), 'mall': it.get('mall', ''),
                    'cond': it['cond'], 'crPrice': it['price'],
                    'stock': it['stock'], 'soldout': it['soldout'], 'num': num,
                    'id': c['id'], 'name': c.get('name') or '',
                    'setName': c.get('setName') or '', 'series': c.get('series') or '',
                    'code': (c.get('setCode') or c.get('setId') or '').upper(),
                    'rar': c.get('rarityLabel') or c.get('rarity') or '',
                    'tags': '|'.join(c.get('quickTags') or []),
                    'image': c.get('customImage') or c.get('image') or '',
                    'price': c.get('price') or 0, 'owned': c.get('owned') or 0})
    return out, stat


# ── 3. まとめてJSONに ─────────────────────────────────
COLS = ['pid', 'cond', 'name', 'set', 'num', 'ser', 'img',
        'price', 'cr', 'stock', 'owned', 'cheap', 'new', 'sold', 'soldAt', 'hr', 'id',
        'shop', 'url', 'vid', 'sure', 'code', 'rar', 'tags', 'fst',
        'mall']       # マイカだけ。モールの中のどの店か（送料が店ごとにかかる）
# 画像URLと商品URLは同じ頭が延々と続くので、共通部分を外に出して行から削る
# （スマホで毎回落とすファイルなので、数MB減るのは効く）
IMG_BASE = 'https://cdn.shopify.com/s/files/1/0763/0536/7360/'
TC_PROD = TC + '/products/'
CR_PROD = 'https://www.cardrush-pokemon.jp/product/'
TT_PROD = TT + '/item/details/'
FF_PROD = FF + '/products/detail/'
BW_PROD = BW_VIEW
YY_PROD = YY + '/sell/poc/card/'
TR_PROD = TR + '/shop/g/g'
PB_PROD = PB
OL_PROD = OL + '/pokemon/product/detail/'
SG_PROD = SG + '/product/detail/'
MY_PROD = MY + '/pokemon-trading-card-game/items/single-card/'
# 店ごとの「商品URLの頭」。行からはこの部分を削って、ページ側で戻す
PROD_BASE = [('tc', TC_PROD), ('tt', TT_PROD), ('ff', FF_PROD), ('bw', BW_PROD),
             ('yy', YY_PROD), ('tr', TR_PROD), ('pb', PB_PROD), ('ol', OL_PROD),
             ('my', MY_PROD), ('sg', SG_PROD)]


def shrink(row):
    i = COLS.index
    img = row[i('img')] or ''
    if img.startswith(IMG_BASE):
        row[i('img')] = img[len(IMG_BASE):]
    url = row[i('url')] or ''
    if url.startswith(CR_PROD):
        row[i('url')] = ''            # カードラッシュは商品番号から組み立て直せる
    else:
        for _, b in PROD_BASE:
            if url.startswith(b):
                row[i('url')] = url[len(b):]
                break
    cid, hr = row[i('id')] or '', row[i('hr')] or ''
    if hr and cid == 'hareruya2-' + hr:
        row[i('id')] = ''
    return row
KEEP_SOLD_DAYS = 14        # 売れたものを何日ぶん残して見せるか
MAX_RATIO = 1.3            # 相場よりこれ以上高いものは買い得リストに載せない


def build(rows, prev, ok_shops=None):
    ok_shops = ok_shops if ok_shops is not None else {'CR', 'TC'}
    live = [r for r in rows if not r['soldout'] and r['price'] > 0 and r['crPrice'] > 0]

    # 「安い」の基準は店と状態ごとに決める。
    # 状態B/Cはそもそも相場より安いので、店をまたいだ一律の線では意味がない
    th, small = {}, []
    for key in sorted({r['shop'] + r['cond'] for r in live}):
        rr = sorted(r['crPrice'] / r['price'] for r in live
                    if r['shop'] + r['cond'] == key)
        if len(rr) < 30:
            small.append(key)          # 母数が足りないものは後で同じ店の代表値を借りる
            continue
        th[key] = {'med': round(statistics.median(rr), 4),
                   'q25': round(rr[int(len(rr) * .25)], 4)}
    for key in (prev.get('th') or {}):
        if key[:2] not in ok_shops and key not in th:
            th[key] = prev['th'][key]
    for key in small:
        shop = key[:2]
        src = th.get(shop + 'C') or th.get(shop + 'A') or th.get(shop + 'B')
        if src:
            th[key] = dict(src, borrowed=1)
        else:
            th[key] = {'med': 1, 'q25': 0, 'borrowed': 1}

    # 同じカード・同じ店・同じ状態の出品は、いちばん安いものだけ残す。
    # 相場より明らかに高いものは買う対象にならないので載せない（ファイルも軽くなる）
    best = {}
    for r in live:
        if r['crPrice'] > r['price'] * MAX_RATIO:
            continue
        k = (r['id'], r['shop'], r['cond'])
        if k not in best or r['crPrice'] < best[k]['crPrice']:
            best[k] = r
    items = sorted(best.values(), key=lambda r: -(r['price'] - r['crPrice']))

    # 前回の一覧を、列名で引ける形に戻す（列が増えても読めるように名前で対応づける）
    pcol = {c: i for i, c in enumerate(prev.get('cols') or [])}
    def pget(a, key, dflt=''):
        i = pcol.get(key)
        return a[i] if i is not None and i < len(a) else dflt
    prev_live, prev_sold = {}, []
    for a in (prev.get('items') or []):
        if pget(a, 'sold', 0):
            prev_sold.append(a)
        else:
            prev_live[pget(a, 'pid')] = a

    # その商品を初めて見かけた日。新着順に並べるために持っておく
    first_seen = {}
    for a in (prev.get('items') or []):
        v = pget(a, 'fst', '')
        if v:
            first_seen[pget(a, 'pid')] = v
    today_s = datetime.date.today().isoformat()

    live_pids = {r['pid'] for r in live}
    # 取得できなかった店のぶんは、前回の行をそのまま残す。
    # ここで落とすと「まとめて売り切れた」ように見えてしまう
    carried = []
    for pid, a in list(prev_live.items()):
        shop = pget(a, 'shop', '') or ('TC' if str(pid).startswith('tc') else 'CR')
        if shop not in ok_shops:
            row = [pget(a, c, 0 if c in ('price', 'cr', 'stock', 'owned', 'cheap', 'new', 'sold')
                        else '') for c in COLS]
            row[COLS.index('new')] = 0
            row[COLS.index('shop')] = shop
            carried.append(row)
            live_pids.add(pid)
            del prev_live[pid]
    if carried:
        log('  取得できなかった店の %d件は前回のまま残しました' % len(carried))
    today = datetime.date.today().isoformat()
    # 前回そもそも扱っていなかった店は、全部「新着」になってしまうので印を付けない
    def _shop_of(a):
        return pget(a, 'shop', '') or ({'tc': 'TC', 'tt': 'TT', 'ff': 'FF'}
                                       .get(str(pget(a, 'pid', ''))[:2], 'CR'))
    prev_shops = {_shop_of(a) for a in list(prev_live.values()) + prev_sold}
    had_prev = bool(prev_shops)
    prev_pids = set(prev.get('pids') or [])
    fresh = {r['pid'] for r in items
             if r['shop'] in prev_shops and r['pid'] not in prev_live
             and r['pid'] not in prev_pids}

    rowsout = list(carried)
    for r in items:
        handle = r['id'].split('-', 1)[1] if r['id'].startswith('hareruya2-') else ''
        key = r['shop'] + r['cond']
        rowsout.append([
            r['pid'], r['cond'], r['name'], r['setName'], r['num'], r['series'],
            r['image'], r['price'], r['crPrice'], r['stock'], r['owned'],
            1 if r['crPrice'] / r['price'] <= th[key]['q25'] else 0,
            1 if r['pid'] in fresh else 0, 0, '', handle, r['id'],
            r['shop'], r['url'], r.get('vid', ''), r.get('sure', 1),
            r.get('code', ''), r.get('rar', ''), r.get('tags', ''),
            first_seen.get(r['pid'], today_s if had_prev else ''),
            r.get('mall', ''),
        ])
        shrink(rowsout[-1])

    # 売れて消えたものは、前回の行をそのまま持ち越して「売れた」印を付ける。
    # 買おうとしていたものが無くなったのは見えたほうがいい
    limit = (datetime.date.today() - datetime.timedelta(days=KEEP_SOLD_DAYS)).isoformat()
    sold_now = 0
    blank = lambda c: (1 if c == 'sure' else
                       0 if c in ('price', 'cr', 'stock', 'owned', 'cheap', 'new', 'sold') else '')
    for pid, a in prev_live.items():
        if pid in live_pids:
            continue
        row = [pget(a, c, blank(c)) for c in COLS]
        row[COLS.index('new')] = 0
        row[COLS.index('stock')] = 0
        row[COLS.index('sold')] = 1
        row[COLS.index('soldAt')] = today
        if not row[COLS.index('shop')]:
            row[COLS.index('shop')] = 'TC' if str(pid).startswith('tc') else 'CR'
        if not row[COLS.index('url')]:
            row[COLS.index('url')] = 'https://www.cardrush-pokemon.jp/product/' + str(pid)
        rowsout.append(row)
        sold_now += 1
    for a in prev_sold:
        when = pget(a, 'soldAt', '')
        if when and when >= limit:
            rowsout.append([pget(a, c, blank(c)) for c in COLS])

    byshop = collections.Counter(r['shop'] for r in items)
    for a in carried:
        byshop[a[COLS.index('shop')]] += 1
    return {
        'createdAt': datetime.datetime.now().astimezone().isoformat(timespec='seconds'),
        'cols': COLS,
        'base': dict(PROD_BASE, img=IMG_BASE, cr=CR_PROD,
                     id='hareruya2-', cart=TC + '/cart/'),
        # マイカの店ごとの送料。店をまたぐと送料が別にかかるので、選ぶときの材料になる
        'ships': dict(MY_SHIP) or (prev.get('ships') or {}),
        'th': th,
        'stats': {'listings': len(live), 'cards': len(items) + len(carried),
                  'sold': sold_now, 'added': len(fresh),
                  'soldKept': sum(1 for a in rowsout if a[COLS.index('sold')]),
                  'byShop': dict(byshop)},
        'pids': sorted(live_pids),
        'items': rowsout,
    }


def push(path):
    """本体の作業ツリーは触らずに、このファイルだけを別クローンから push する
    （バックアップの状態ファイルと同じやり方。編集中の内容を壊さないため）"""
    work = os.path.join(os.environ.get('LOCALAPPDATA', os.path.expanduser('~')), 'PokecardStatus')
    rel = 'docs/buylist-rush-bc.json'

    def git(args, cwd=work, timeout=300):
        return subprocess.run(['git'] + args, cwd=cwd, timeout=timeout,
                              stdout=subprocess.PIPE, stderr=subprocess.PIPE, creationflags=NOWIN)

    if not os.path.isdir(os.path.join(work, '.git')):
        os.makedirs(work, exist_ok=True)
        r = git(['clone', '--depth', '1', '--filter=blob:none', '--sparse',
                 'https://github.com/%s/%s.git' % (OWNER, REPO), '.'], work, 600)
        if r.returncode != 0:
            raise RuntimeError(r.stderr.decode('utf-8', 'replace')[:200])
    # cone方式なので指定するのはディレクトリ。ルート直下のファイルは常に入る。
    # ファイル名を渡すと docs/ が展開されず、git add が黙って何もしない
    git(['sparse-checkout', 'set', 'docs'])
    # グローバル設定が無い環境だと、これが無いと commit が黙って失敗する
    if not git(['config', 'user.name']).stdout.strip():
        for key, val in (('user.name', OWNER), ('user.email', 'lurds.rot2@gmail.com')):
            src = git(['config', key], HERE).stdout.decode('utf-8', 'replace').strip()
            git(['config', key, src or val])

    for attempt in range(5):
        git(['fetch', '-q', '--depth', '1', 'origin', 'main'])
        git(['reset', '--hard', '-q', 'FETCH_HEAD'])
        dst = os.path.join(work, 'docs', 'buylist-rush-bc.json')
        os.makedirs(os.path.dirname(dst), exist_ok=True)
        io.open(dst, 'w', encoding='utf-8', newline='').write(
            io.open(path, encoding='utf-8').read())
        git(['add', rel])
        # sparse-checkout の外だと git add が黙って素通りする。
        # 「変更なし」と区別がつかず気づけないので、中身のハッシュで確かめる
        out = lambda r: r.stdout.decode('utf-8', 'replace').strip()
        want = out(git(['hash-object', dst]))
        if want != out(git(['rev-parse', ':' + rel])):
            raise RuntimeError('%s を index に入れられませんでした'
                               '（sparse-checkout の設定を確認）' % rel)
        head = git(['rev-parse', 'HEAD:' + rel])
        if head.returncode == 0 and out(head) == want:
            log('変更なし（pushしません）')
            return True
        c = git(['commit', '-q', '-m', 'chore: 買い得リストのデータ更新'])
        if c.returncode != 0:
            # commit できていないのに push は「送るものが無い」で成功してしまう
            raise RuntimeError('commit失敗: ' + c.stderr.decode('utf-8', 'replace')[:160])
        if git(['push', '-q', 'origin', 'HEAD:main']).returncode == 0:
            return True
        log('push 失敗（%d回目）。取り直して再試行' % (attempt + 1))
    return False


def sync_only():
    """お店には行かず、手元のDB（data.json＋delta.json）の相場と所持枚数だけを
    いまの一覧に反映し直す。数秒で終わるので短い間隔で回せる。
    （お店の在庫の増減は1日1回の全体取得の担当）"""
    if not os.path.exists(OUT):
        log('一覧がまだありません。先に通常の取得を実行してください')
        return 1
    data = json.load(io.open(OUT, encoding='utf-8'))
    cols = data.get('cols') or []
    for extra in ('code', 'rar', 'tags', 'fst', 'mall'):
        if extra not in cols:
            cols.append(extra)
    data['cols'] = cols
    ix = {c: i for i, c in enumerate(cols)}
    if 'id' not in ix:
        log('列の形が古いので同期できません')
        return 1
    cards = load_db()
    base_id = (data.get('base') or {}).get('id') or 'hareruya2-'
    padded = any(len(a) < len(cols) for a in (data.get('items') or []))
    changed_p = changed_o = 0
    for a in data.get('items') or []:
        while len(a) < len(cols):      # 列が増えたぶんを埋める
            a.append('')
        cid = a[ix['id']] or ((base_id + a[ix['hr']]) if a[ix['hr']] else '')
        c = cards.get(cid)
        if not c:
            continue
        # パック記号・レアリティ・タグは、取り直さなくてもDBから埋められる
        for key, val in (('code', (c.get('setCode') or c.get('setId') or '').upper()),
                         ('rar', c.get('rarityLabel') or c.get('rarity') or ''),
                         ('tags', '|'.join(c.get('quickTags') or []))):
            i = ix.get(key)
            if i is None:
                continue
            if a[i] != val:
                a[i] = val
                changed_o += 1
        pr, ow = c.get('price') or 0, c.get('owned') or 0
        if pr and a[ix['price']] != pr:
            a[ix['price']] = pr
            changed_p += 1
        if a[ix['owned']] != ow:
            a[ix['owned']] = ow
            changed_o += 1
    # 相場が動いたので「割安」の線も引き直す
    groups = collections.defaultdict(list)
    for a in data['items']:
        if a[ix['sold']] or not a[ix['price']] or not a[ix['cr']]:
            continue
        groups[(a[ix['shop']] or 'CR') + a[ix['cond']]].append(a[ix['cr']] / a[ix['price']])
    th, small = {}, []
    for k, v in groups.items():
        v.sort()
        if len(v) < 30:
            small.append(k)
            continue
        th[k] = {'med': round(statistics.median(v), 4), 'q25': round(v[int(len(v) * .25)], 4)}
    for k in small:
        src = th.get(k[:2] + 'C') or th.get(k[:2] + 'A') or th.get(k[:2] + 'B')
        th[k] = dict(src, borrowed=1) if src else {'med': 1, 'q25': 0, 'borrowed': 1}
    cheap = 0
    for a in data['items']:
        if a[ix['sold']] or not a[ix['price']] or not a[ix['cr']]:
            continue
        t = th.get((a[ix['shop']] or 'CR') + a[ix['cond']])
        a[ix['cheap']] = 1 if (t and a[ix['cr']] / a[ix['price']] <= t['q25']) else 0
        cheap += a[ix['cheap']]
    if not (changed_p or changed_o) and not padded:
        log('DB同期: 変わりなし（割安%d件）' % cheap)
        return 0
    data['th'] = th
    data['syncedAt'] = datetime.datetime.now().astimezone().isoformat(timespec='seconds')
    io.open(OUT, 'w', encoding='utf-8', newline='').write(
        json.dumps(data, ensure_ascii=False, separators=(',', ':')))
    log('DB同期: 相場%d件・所持%d件を更新／割安%d件' % (changed_p, changed_o, cheap))
    if '--no-push' not in sys.argv:
        log('push: ' + ('OK' if push(OUT) else '失敗'))
    return 0


LOCK = os.path.join(HERE, 'buylist_rush.lock')
LOCK_STALE = 6 * 3600      # これより古い鍵は、前回が落ちた残骸とみなす


def _lock():
    """同時に2つ走らせない。走っているなら False"""
    try:
        if os.path.exists(LOCK):
            age = time.time() - os.path.getmtime(LOCK)
            if age < LOCK_STALE:
                who = ''
                try:
                    who = io.open(LOCK, encoding='utf-8').read().strip()
                except Exception:
                    pass
                log('別の取得が動いています（%s・%d分前に開始）。今回は何もしません'
                    % (who or '?', age / 60))
                return False
            log('古い鍵が残っていたので外します（%.1f時間前）' % (age / 3600))
        io.open(LOCK, 'w', encoding='utf-8').write('pid %d %s' % (
            os.getpid(), datetime.datetime.now().strftime('%m-%d %H:%M')))
        return True
    except Exception:
        return True            # 鍵が作れないくらいで止めない


def _unlock():
    try:
        os.remove(LOCK)
    except Exception:
        pass


def main():
    _trim_log()
    if '--sync' in sys.argv:
        return sync_only()
    if not _lock():
        return 0
    try:
        return _run()
    finally:
        _unlock()


def _run():
    prev = {}
    if os.path.exists(OUT):
        try:
            prev = json.load(io.open(OUT, encoding='utf-8'))
        except Exception:
            pass
    log('お店の在庫を取得中…')
    items, ok_shops = scrape()
    log('取得 %d件（%s）' % (len(items), '／'.join(sorted(ok_shops)) or 'なし'))
    if not ok_shops:
        raise RuntimeError('どの店からも取得できませんでした')
    cards = load_db()
    log('DB %d件' % len(cards))
    rows, stat = match(items, cards)
    for k, v in stat.most_common():
        log('  %-14s %d' % (k, v))
    data = build(rows, prev, ok_shops)
    os.makedirs(os.path.dirname(OUT), exist_ok=True)
    io.open(OUT, 'w', encoding='utf-8', newline='').write(
        json.dumps(data, ensure_ascii=False, separators=(',', ':')))
    s = data['stats']
    log('在庫あり %d件 → カード %d件 ／ 売れた %d件 ・ 新着 %d件 ／ %.1fMB'
        % (s['listings'], s['cards'], s['sold'], s['added'], os.path.getsize(OUT) / 1048576))
    if '--no-push' not in sys.argv:
        log('push: ' + ('OK' if push(OUT) else '失敗'))
    return 0


if __name__ == '__main__':
    try:
        sys.exit(main())
    except Exception as e:
        log('失敗: %s' % e)
        notify('ポケカ 買い得リストの更新に失敗', str(e)[:200])
        sys.exit(1)
