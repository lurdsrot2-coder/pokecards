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
# 外部コマンド（curl・git・PowerShell）を呼ぶたびに黒い窓が前面に出て
# 操作が中断されるので、窓を作らないようにする（Windowsのみ）
NOWIN = 0x08000000 if os.name == 'nt' else 0


def notify(title, message):
    """Windowsの通知を出す。気づけないと意味がないので、異常時は必ず呼ぶ。"""
    try:
        subprocess.run(
            ['powershell', '-ExecutionPolicy', 'Bypass', '-File', os.path.join(HERE, 'notify.ps1'),
             '-Title', title, '-Message', message],
            timeout=60, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL, creationflags=NOWIN)
    except Exception:
        pass


def fetch(url):
    req = urllib.request.Request(url, headers={'User-Agent': 'pokecards-backup'})
    with urllib.request.urlopen(req, timeout=180) as r:
        return r.read()


def log(msg):
    line = '%s  %s' % (datetime.datetime.now().strftime('%Y-%m-%d %H:%M:%S'), msg)
    # 自動実行は pythonw（黒い窓なし）なので、画面が無いことがある
    try:
        print(line)
    except Exception:
        pass
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

    # アプリに「いつバックアップできたか」を見せるための小さな状態ファイル。
    # アプリ側は端末のファイルを見られないので、リポジトリ経由で渡す
    write_status({
        'date': datetime.date.today().isoformat(),
        'createdAt': datetime.datetime.now().isoformat(timespec='seconds'),
        'file': name,
        'cards': len(out['cards']),
        'ownedKinds': kinds,
        'ownedTotal': total,
        'sizeMB': round(size / 1048576.0, 1),
    })
    return 0


def write_status(status):
    """状態ファイルをリポジトリに置いて push する。

    本体の作業ツリーは絶対に触らない（編集中の内容を壊さないため）。
    このファイル専用の小さなクローンを別に用意して、そこから push する。
    data.json は落とさないよう sparse-checkout で除いている。
    """
    work = os.path.join(os.environ.get('LOCALAPPDATA', os.path.expanduser('~')), 'PokecardStatus')
    url = 'https://github.com/%s/%s.git' % (OWNER, REPO)

    def git(args, cwd, timeout=180):
        return subprocess.run(['git'] + args, cwd=cwd, timeout=timeout,
                              stdout=subprocess.PIPE, stderr=subprocess.PIPE, creationflags=NOWIN)

    try:
        if not os.path.isdir(os.path.join(work, '.git')):
            os.makedirs(work, exist_ok=True)
            r = git(['clone', '--depth', '1', '--filter=blob:none', '--sparse', url, '.'], work, 600)
            if r.returncode != 0:
                raise RuntimeError(r.stderr.decode('utf-8', 'replace')[:200])
            git(['sparse-checkout', 'set', 'backup_status.json'], work)
        # コミットする人の名前。本体リポジトリの設定を引き継ぐ
        # （グローバル設定が無い環境だと、これが無いとcommitが黙って失敗する）
        who = git(['config', 'user.name'], work).stdout.decode('utf-8', 'replace').strip()
        if not who:
            for key, val in (('user.name', 'lurdsrot2-coder'), ('user.email', 'lurds.rot2@gmail.com')):
                src = git(['config', key], HERE).stdout.decode('utf-8', 'replace').strip()
                git(['config', key, src or val], work)
    except Exception as e:
        log('※ バックアップ日の記録用リポジトリを用意できませんでした: %s' % e)
        return

    path = os.path.join(work, 'backup_status.json')
    for attempt in range(5):
        try:
            git(['fetch', '-q', '--depth', '1', 'origin', 'main'], work)
            git(['reset', '--hard', '-q', 'FETCH_HEAD'], work)
            try:
                old = json.load(io.open(path, encoding='utf-8'))
                if old.get('date') == status['date'] and old.get('ownedTotal') == status['ownedTotal']:
                    return              # 同じ内容なら余計なコミットを作らない
            except Exception:
                pass
            io.open(path, 'w', encoding='utf-8', newline='').write(
                json.dumps(status, ensure_ascii=False))
            git(['add', 'backup_status.json'], work)
            c = git(['commit', '-q', '-m', 'chore: backup recorded (%s)' % status['date']], work)
            if c.returncode != 0:
                # コミットできていないのに push は「送るものが無い」で成功してしまうので、
                # ここで必ず止めて理由を残す
                raise RuntimeError('commit失敗: ' + c.stderr.decode('utf-8', 'replace')[:160])
            r = git(['push', '-q', 'origin', 'HEAD:main'], work)
            if r.returncode == 0:
                log('バックアップ日をアプリに反映しました (%s)' % status['date'])
                return
        except Exception as e:
            sys.stderr.write('status push 失敗(%d回目): %s\n' % (attempt + 1, e))
    log('※ バックアップ日の記録を送れませんでした（バックアップ自体は成功）')


if __name__ == '__main__':
    try:
        sys.exit(main())
    except Exception as e:
        log('失敗: %s' % e)
        notify('ポケカ バックアップ失敗',
               '%s\n保存先: %s' % (e, DEST))
        sys.exit(1)
