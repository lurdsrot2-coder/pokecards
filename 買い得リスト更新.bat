@echo off
rem カードラッシュの状態B/Cを取り直して、買い得リストを最新にする。
rem 10分ほどかかる。終わるとスマホ側も「⟳ 更新」で最新になる。
chcp 65001 > nul
cd /d "%~dp0"
python buylist_rush.py
echo.
echo 終わりました。ページ: https://lurdsrot2-coder.github.io/pokecards/docs/buylist-rush-bc.html
pause
