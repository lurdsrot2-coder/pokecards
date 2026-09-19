@echo off
rem 4店の在庫を取り直して、買い得リストを最新にする。30分ほどかかる。
rem 手で実行するときは進み具合が見たいので、あえて窓を出す python で動かす。
rem 自動実行（5時・13時・21時半）は窓の出ない pythonw なので邪魔にならない。
chcp 65001 > nul
cd /d "%~dp0"
python buylist_rush.py
echo.
echo 終わりました。ページ: https://lurdsrot2-coder.github.io/pokecards/docs/buylist-rush-bc.html
pause
