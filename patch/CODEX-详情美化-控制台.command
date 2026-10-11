#!/bin/zsh
#
#  CODEX「详情美化」控制台（双击运行）
#  一个入口管理两层：
#    渲染层 —— app.asar 补丁（思考块默认展开 + 显示更多行）
#    接入层 —— 思考详情中转（不改源码的应急兜底）
#
set -u
DIR="${0:A:h}"                      # 本脚本所在目录（补丁目录，可整体搬移）
PATCH_PY="$DIR/patch.py"
APPLY="$DIR/CODEX-详情美化-打补丁.command"
REVERT="$DIR/CODEX-详情美化-退回官方.command"
PY="/usr/bin/python3"

while true; do
  clear
  echo "════════════════════════════════════════════"
  echo "  CODEX · 详情美化 控制台"
  echo "════════════════════════════════════════════"
  echo
  "$PY" "$PATCH_PY" --status
  echo
  echo "────────────────────────────────────────────"
  echo " 1) 打渲染层补丁（思考块默认展开 + 更多行；需重启 CODEX）"
  echo " 2) 退回官方原版（渲染层）"
  echo " 3) 开启接入层兜底（中转，使思考内容可见）"
  echo " 4) 关闭接入层兜底（模型链路改回官方）"
  echo " 5) 刷新状态"
  echo " 0) 退出"
  echo "────────────────────────────────────────────"
  read -r "?请选择（0-5）：" choice
  case "$choice" in
    1) "$APPLY" ;;
    2) "$REVERT" ;;
    3) "$PY" "$PATCH_PY" --proxy-on;  echo; read -r "?按回车继续…" _ ;;
    4) "$PY" "$PATCH_PY" --proxy-off; echo; read -r "?按回车继续…" _ ;;
    5) ;;
    0|"") exit 0 ;;
    *) echo "无效选择"; sleep 1 ;;
  esac
done
