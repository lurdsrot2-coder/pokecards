# -*- coding: utf-8 -*-
"""
ポケカコレクションのバックアップ。

GitHub から data.json と delta.json を取り出して1つに合成し、
OneDrive に日付つきで保存する。保存したファイルはアプリの「📂 復元」で
そのまま読み込める形式（cards 配列を持つJSON）。

- 直近30日ぶんと、毎月1日ぶんを残す
- 前回より所持枚数が減っていたら警告を記録する（気づけるようにするため）
- PCが壊れてもOneDrive側に残る。GitHubが使えなくなってもこちらが残る

手動実行:  python backup_to_onedrive.py
"""
import json
import os
import sys
import io
import glob
import datetime
import subprocess
import urllib.request

OWNER = 'lurdsrot2-coder'
REPO = 'pokecards'
DEST = os.path.join(os.environ.get('USERPROFILE', r'C:\Users\okudaira'), 'OneDrive', 'PokecardBackup')
KEEP_DAYS = 30
STALE_DAYS = 3          # 前回の成功からこれ以上空いたら知らせる
HERE = os.path.dirname(os.path.abspath(__file__))


def notify(title, message):
    """Windowsの通知を出す。気づけないと意味がないので、異常時は必ず呼ぶ。"""
    try:
        subprocess.run(
            ['powershell', '-ExecutionPolicy', 'Bypass', '-File', os.path.join(HERE, 'notify.ps1'),
             '-Title', title, '-Message', message],
            timeout=60, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
    except Exception:
        pass


def fetch(url):
    req = urllib.request.Request(url, headers={'User-Agent': 'pokecards-backup'})
    with urllib.request.urlopen(req, timeout=180) as r:
        return r.read()


def log(msg):
    line = '%s  %s' % (datetime.datetime.now().strftime('%Y-%m-%d %H:%M:%S'), msg)
    print(line)
    try:
        with io.open(os.path.join(DEST, 'backup.log'), 'a', encoding='utf-8') as f:
            f.write(line + '\n')
    except Exception:
        pass


def main():
    os.makedirs(DEST, exist_ok=True)

    # 最新コミットのSHA経由で取る（CDNの古いキャッシュを踏まないため）
    ref = json.loads(fetch('https://api.github.com/repos/%s/%s/git/ref/heads/main' % (OWNER, REPO)))
    sha = ref['object']['sha']
    raw = 'https://raw.githubusercontent.com/%s/%s/%s/' % (OWNER, REPO, sha)

    full = json.loads(fetch(raw + 'data.json'))
    try:
        delta = json.loads(fetch(raw + 'delta.json'))
    except Exception:
        delta = {'ops': {}}

    cards = {c['id']: c for c in full.get('cards', [])}
    for cid, card in (delta.get('ops') or {}).items():
        if card is None:
            cards.pop(cid, None)
        else:
            cards[cid] = card
    out = dict(full)
    out['cards'] = list(cards.values())
    out['backupSourceCommit'] = sha
    out['backupCreatedAt'] = datetime.datetime.now().isoformat()

    kinds = sum(1 for c in out['cards'] if (c.get('owned') or 0) > 0)
    total = sum((c.get('owned') or 0) for c in out['cards'])

    # 直前のバックアップと比べて減っていないか見る
    prev = sorted(glob.glob(os.path.join(DEST, 'pokecards_*.json')))
    warn = ''
    if prev:
        try:
            p = json.load(io.open(prev[-1], encoding='utf-8'))
            pt = sum((c.get('owned') or 0) for c in p.get('cards', []))
            if total < pt:
                warn = '  ※警告: 所持枚数が前回(%d)より %d 枚減っています' % (pt, pt - total)
                notify('ポケカ 所持枚数が減りました',
                       '前回 %d 枚 → 今回 %d 枚（%d 枚減）。誤操作や同期の事故かもしれません。'
                       % (pt, total, pt - total))
        except Exception:
            pass
        # 前回のバックアップから日が空いていたら知らせる（タスクが動いていない可能性）
        try:
            last = datetime.date.fromisoformat(os.path.basename(prev[-1])[10:20])
            gap = (datetime.date.today() - last).days
            if gap >= STALE_DAYS:
                notify('ポケカ バックアップが止まっていました',
                       '前回の保存は %d 日前（%s）です。今回は保存できました。'
                       % (gap, last.isoformat()))
        except Exception:
            pass

    name = 'pokecards_%s.json' % datetime.date.today().isoformat()
    path = os.path.join(DEST, name)
    with io.open(path, 'w', encoding='utf-8', newline='') as f:
        f.write(json.dumps(out, ensure_ascii=False, separators=(',', ':')))
    size = os.path.getsize(path)
    log('保存 %s  カード%d種類 / 所持%d種類 / 合計%d枚 / %.1fMB%s'
        % (name, len(out['cards']), kinds, total, size / 1048576.0, warn))

    # 古いものを間引く（直近30日ぶんと、毎月1日ぶんは残す）
    files = sorted(glob.glob(os.path.join(DEST, 'pokecards_*.json')))
    keep = set(files[-KEEP_DAYS:])
    for f in files:
        d = os.path.basename(f)[10:20]
        if d.endswith('-01'):
            keep.add(f)
    removed = 0
    for f in files:
        if f not in keep:
            try:
                os.remove(f)
                removed += 1
            except Exception:
                pass
    if removed:
        log('古いバックアップを %d件 削除（%d件を保持）' % (removed, len(keep)))
    return 0


if __name__ == '__main__':
    try:
        sys.exit(main())
    except Exception as e:
        log('失敗: %s' % e)
        notify('ポケカ バックアップ失敗',
               '%s\n保存先: %s' % (e, DEST))
        sys.exit(1)
