# -*- coding: utf-8 -*-
"""カードラッシュの「状態B」「状態C」を全件取り直して、買い得リストのデータを作る。

やっていること:
  1. カードラッシュの検索を全ページたどって、状態B/Cの商品を集める
  2. カード番号＋セット記号＋カード名で手元のDB（data.json＋delta.json）に突き合わせる
  3. 売り切れを落とし、同じカードの出品はいちばん安いものだけ残す
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


def log(msg):
    sys.stderr.write(msg + '\n')
    sys.stderr.flush()


# ── 1. 収集 ───────────────────────────────────────────
def fetch(url, tries=4):
    for i in range(tries):
        try:
            r = urllib.request.Request(url, headers={
                'User-Agent': UA, 'Accept': 'text/html', 'Accept-Language': 'ja,en;q=0.8'})
            return urllib.request.urlopen(r, timeout=90).read().decode('utf-8', 'replace')
        except Exception as e:
            log('  retry %d: %s' % (i + 1, e))
            time.sleep(2 + i * 3)
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


def scrape():
    """1ページ100件・最大100ページ（=1万件）で頭打ちになるので、
    価格の安い順と高い順の両方から取って重複を消す"""
    items = {}
    for kw in ('状態B', '状態C'):
        for order in ('asc', 'desc'):
            for page in range(1, 101):
                h = fetch(page_url(kw, order, page))
                if not h:
                    log('%s %s p%d 取得失敗' % (kw, order, page))
                    continue
                got = parse_page(h)
                for it in got:
                    items.setdefault(it['pid'], it)
                last = max([int(x) for x in re.findall(r'page=(\d+)"', h)] or [page])
                if page % 20 == 0 or page >= last:
                    log('  %s %s p%d/%d 累計%d' % (kw, order, page, last, len(items)))
                if page >= last or not got:
                    break
                time.sleep(.25)
    return list(items.values())


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
    return v


def db_variant(suffix):
    v = set()
    if 'マスターボールミラー' in suffix: v.add('master')
    elif 'モンスターボールミラー' in suffix: v.add('monster')
    elif 'ミラー' in suffix: v.add('mirror')
    if '1ED' in suffix or '初版' in suffix: v.add('1ed')
    if 'キラ' in suffix and 'ノンキラ' not in suffix: v.add('kira')
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


def match(items, cards):
    """迷ったら捨てる。間違った紐付けを1件出すほうが、取りこぼすより害が大きい"""
    by_num = collections.defaultdict(list)
    for c in cards.values():
        n = (c.get('cardNumber') or '').strip().upper()
        if n:
            by_num[n].append(c)

    out, stat = [], collections.Counter()
    for it in items:
        m = ALT.match(it['alt'])
        if not m:
            stat['状態表記なし'] += 1
            continue
        cond = m.group(1)
        if cond not in ('状態B', '状態C'):
            stat['B/C以外'] += 1
            continue
        raw, rarity, num = m.group(2), m.group(3), m.group(4).strip().upper()
        if not num or num == '/':
            stat['番号なし'] += 1
            continue
        cand = by_num.get(num, [])
        if not cand:
            stat['DBに番号なし'] += 1
            continue
        # セット記号（S8b 等）で絞る。DBのsetIdは s8b / s8b-m のように派生を持つ
        mo = (it.get('model') or '').strip().lower()
        if mo and mo != 'その他':
            nar = [c for c in cand if (c.get('setId') or '').lower() == mo
                   or (c.get('setId') or '').lower().startswith(mo + '-')]
            if nar:
                cand = nar
        base = norm(PAREN.sub('', raw))
        cand = [c for c in cand if norm((c.get('name') or '').split(':')[0]) == base]
        if not cand:
            stat['名前が合わない'] += 1
            continue
        want = variant_of(raw + rarity)
        # 「アンリミ」と書かれた商品を1ED版に当ててはいけない（値段が桁違いになる）
        unlim = 'アンリミ' in raw
        scored = []
        for c in cand:
            nm = c.get('name') or ''
            have = db_variant(nm.split(':', 1)[1] if ':' in nm else '')
            if unlim and '1ed' in have:
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
        stat['照合できた'] += 1
        out.append({'pid': it['pid'], 'cond': cond, 'crPrice': it['price'],
                    'stock': it['stock'], 'soldout': it['soldout'], 'num': num,
                    'id': c['id'], 'name': c.get('name') or '',
                    'setName': c.get('setName') or '', 'series': c.get('series') or '',
                    'image': c.get('customImage') or c.get('image') or '',
                    'price': c.get('price') or 0, 'owned': c.get('owned') or 0})
    return out, stat


# ── 3. まとめてJSONに ─────────────────────────────────
COLS = ['pid', 'cond', 'name', 'set', 'num', 'ser', 'img',
        'price', 'cr', 'stock', 'owned', 'cheap', 'new', 'sold', 'soldAt', 'hr']
KEEP_SOLD_DAYS = 14        # 売れたものを何日ぶん残して見せるか


def build(rows, prev):
    live = [r for r in rows if not r['soldout'] and r['price'] > 0 and r['crPrice'] > 0]
    # 「安い」の基準は状態ごとの実勢から決める。
    # 状態B/Cはそもそも相場より安いので、単純比較では全部が「安い」になってしまう
    th = {}
    for cond in ('状態B', '状態C'):
        rr = sorted(r['crPrice'] / r['price'] for r in live if r['cond'] == cond)
        th[cond[-1]] = {'med': round(statistics.median(rr), 4) if rr else 0,
                        'q25': round(rr[int(len(rr) * .25)], 4) if rr else 0}
    # 同じカードに複数の出品があるので、いちばん安いものだけ残す
    best = {}
    for r in live:
        k = (r['id'], r['cond'])
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
    today = datetime.date.today().isoformat()
    had_prev = bool(prev_live or prev_sold or prev.get('pids'))
    fresh = {r['pid'] for r in items if had_prev and r['pid'] not in prev_live
             and r['pid'] not in set(prev.get('pids') or [])}

    rowsout = []
    for r in items:
        handle = r['id'].split('-', 1)[1] if r['id'].startswith('hareruya2-') else ''
        rowsout.append([
            r['pid'], r['cond'][-1], r['name'], r['setName'], r['num'], r['series'],
            r['image'], r['price'], r['crPrice'],
            int(re.sub(r'\D', '', r['stock']) or 0), r['owned'],
            1 if r['crPrice'] / r['price'] <= th[r['cond'][-1]]['q25'] else 0,
            1 if r['pid'] in fresh else 0, 0, '', handle,
        ])

    # 売れて消えたものは、前回の行をそのまま持ち越して「売れた」印を付ける。
    # 買おうとしていたものが無くなったのは見えたほうがいい
    limit = (datetime.date.today() - datetime.timedelta(days=KEEP_SOLD_DAYS)).isoformat()
    sold_now = 0
    for pid, a in prev_live.items():
        if pid in live_pids:
            continue
        row = [pget(a, c, 0 if c in ('price', 'cr', 'stock', 'owned', 'cheap', 'new', 'sold') else '')
               for c in COLS]
        row[COLS.index('new')] = 0
        row[COLS.index('stock')] = 0
        row[COLS.index('sold')] = 1
        row[COLS.index('soldAt')] = today
        rowsout.append(row)
        sold_now += 1
    for a in prev_sold:
        when = pget(a, 'soldAt', '')
        if when and when >= limit:
            rowsout.append([pget(a, c, 0 if c in ('price', 'cr', 'stock', 'owned',
                                                  'cheap', 'new', 'sold') else '') for c in COLS])

    return {
        'createdAt': datetime.datetime.now().isoformat(timespec='seconds'),
        'cols': COLS,
        'th': th,
        'stats': {'listings': len(live), 'cards': len(items),
                  'sold': sold_now, 'added': len(fresh),
                  'soldKept': sum(1 for a in rowsout if a[COLS.index('sold')])},
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
    prev = {}
    if os.path.exists(OUT):
        try:
            prev = json.load(io.open(OUT, encoding='utf-8'))
        except Exception:
            pass
    log('カードラッシュを取得中…')
    items = scrape()
    log('取得 %d件' % len(items))
    cards = load_db()
    log('DB %d件' % len(cards))
    rows, stat = match(items, cards)
    for k, v in stat.most_common():
        log('  %-14s %d' % (k, v))
    data = build(rows, prev)
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
