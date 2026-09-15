# -*- coding: utf-8 -*-
"""カードラッシュとトレカキャンプの在庫を取り直して、買い得リストのデータを作る。

やっていること:
  1. カードラッシュ（状態B/C/Dの検索）とトレカキャンプ（コレクション別のJSON）から商品を集める
  2. カード番号＋セット記号＋カード名で手元のDB（data.json＋delta.json）に突き合わせる
  3. 売り切れを落とし、同じカード・同じ状態の出品はいちばん安いものだけ残す
  4. docs/buylist-rush-bc.json に書き出して push する（ページはこれを読む）

前回のJSONと比べて、消えた商品（＝売れた）と新しく出た商品を数える。
新着には印を付けるので、ページ側で「新着だけ」を見られる。

手動実行:  python buylist_rush.py
           python buylist_rush.py --no-push    （pushせず手元だけ更新）
"""
import urllib.request, urllib.parse, re, io, json, sys, os, time, subprocess
import unicodedata, statistics, datetime, collections

OWNER, REPO = 'lurdsrot2-coder', 'pokecards'
HERE = os.path.dirname(os.path.abspath(__file__))
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
    sys.stderr.write(line + '\n')
    sys.stderr.flush()
    # 自動実行（毎朝5時）の様子は画面に出ないので、ファイルにも残す
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
            timeout=60, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
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
    for i in range(tries):
        try:
            r = urllib.request.Request(TC + path, headers={'User-Agent': UA, 'Accept': 'application/json'})
            return json.loads(urllib.request.urlopen(r, timeout=90).read())
        except Exception as e:
            log('  retry %d: %s' % (i + 1, e))
            _backoff(e, i)
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
    for page in range(1, 6):
        d = tc_json('/collections.json?limit=250&page=%d' % page)
        c = d.get('collections') or []
        if not c:
            break
        cols += c
        time.sleep(.2)
    log('  トレカキャンプ コレクション %d件' % len(cols))
    items = {}
    for i, col in enumerate(cols):
        # 「#neo1_金、銀、新世界へ…」のような弾名。旧裏の絞り込みに使う
        settitle = (col.get('title') or '').split('/')[0].strip()
        for page in range(1, 21):
            d = tc_json('/collections/%s/products.json?limit=250&page=%d'
                        % (urllib.parse.quote(col.get('handle') or '', safe=''), page))
            ps = d.get('products') or []
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
            time.sleep(.5)
        if (i + 1) % 100 == 0:
            log('  トレカキャンプ %d/%d コレクション 累計%d件' % (i + 1, len(cols), len(items)))
        time.sleep(.4)
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


def scrape():
    """店ごとに集めて、ちゃんと取れた店の集合も返す。
    取れなかった店のぶんは前回の内容をそのまま残す（売り切れ扱いにしない）"""
    only = None
    if '--only' in sys.argv:
        only = {x.upper() for x in sys.argv[sys.argv.index('--only') + 1].split(',')}
    items, ok = [], set()
    for shop, fn, least in (('CR', cr_scrape, 500), ('TC', tc_scrape, 500), ('TT', tt_scrape, 500)):
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


def load_db():
    """data.json に delta.json を重ねた、いま画面に出ているのと同じ状態"""
    full = json.load(io.open(os.path.join(HERE, 'data.json'), encoding='utf-8'))
    cards = {c['id']: c for c in full.get('cards', [])}
    try:
        delta = json.load(io.open(os.path.join(HERE, 'delta.json'), encoding='utf-8'))
        for cid, card in (delta.get('ops') or {}).items():
            if card is None:
                cards.pop(cid, None)
            else:
                cards[cid] = card
    except Exception as e:
        log('delta.json を読めませんでした（data.json だけで続けます）: %s' % e)
    return cards


# 店が「旧裏」と言っているかどうか。カードラッシュは番号が {旧裏}、
# キャンプは 1st2 / neo1 / 1stGYM1、トレトクは 旧1 のような記号を使う
OLD_CODE = re.compile(r'^(1st|neo|旧|gym|opg|pmcg|vending)', re.I)


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
            if cand and mo and mo != 'その他' and not _is_old_back(it):
                keep = [c for c in cand if _code_ok(mo, c.get('setId'))]
                if not keep:
                    stat['収録弾が合わない'] += 1
                    continue
                cand = keep
        if not cand and _is_old_back(it):
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
        sure = 1 if (code and code != 'その他' and _code_ok(code, c.get('setId'))) else 0
        out.append({'shop': it['shop'], 'pid': it['pid'], 'url': it['url'], 'sure': sure,
                    'vid': it.get('vid', ''),
                    'cond': it['cond'], 'crPrice': it['price'],
                    'stock': it['stock'], 'soldout': it['soldout'], 'num': num,
                    'id': c['id'], 'name': c.get('name') or '',
                    'setName': c.get('setName') or '', 'series': c.get('series') or '',
                    'image': c.get('customImage') or c.get('image') or '',
                    'price': c.get('price') or 0, 'owned': c.get('owned') or 0})
    return out, stat


# ── 3. まとめてJSONに ─────────────────────────────────
COLS = ['pid', 'cond', 'name', 'set', 'num', 'ser', 'img',
        'price', 'cr', 'stock', 'owned', 'cheap', 'new', 'sold', 'soldAt', 'hr', 'id',
        'shop', 'url', 'vid', 'sure']
# 画像URLと商品URLは同じ頭が延々と続くので、共通部分を外に出して行から削る
# （スマホで毎回落とすファイルなので、数MB減るのは効く）
IMG_BASE = 'https://cdn.shopify.com/s/files/1/0763/0536/7360/'
TC_PROD = TC + '/products/'
CR_PROD = 'https://www.cardrush-pokemon.jp/product/'
TT_PROD = TT + '/item/details/'


def shrink(row):
    i = COLS.index
    img = row[i('img')] or ''
    if img.startswith(IMG_BASE):
        row[i('img')] = img[len(IMG_BASE):]
    url = row[i('url')] or ''
    if url.startswith(TC_PROD):
        row[i('url')] = url[len(TC_PROD):]
    elif url.startswith(TT_PROD):
        row[i('url')] = url[len(TT_PROD):]
    elif url.startswith(CR_PROD):
        row[i('url')] = ''
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
    prev_shops = {'TC' if p.startswith('tc') else 'CR' for p in list(prev_live) + [pget(a, 'pid', '') for a in prev_sold]}
    fresh = {r['pid'] for r in items
             if r['shop'] in prev_shops and r['pid'] not in prev_live
             and r['pid'] not in set(prev.get('pids') or [])}

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
        'createdAt': datetime.datetime.now().isoformat(timespec='seconds'),
        'cols': COLS,
        'base': {'img': IMG_BASE, 'tc': TC_PROD, 'cr': CR_PROD, 'tt': TT_PROD,
                 'id': 'hareruya2-', 'cart': TC + '/cart/'},
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
                              stdout=subprocess.PIPE, stderr=subprocess.PIPE)

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


def main():
    _trim_log()
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
