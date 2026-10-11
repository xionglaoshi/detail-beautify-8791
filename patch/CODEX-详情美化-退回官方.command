#!/bin/zsh
#
#  CODEX「详情美化」补丁 · 一键退回官方（双击运行）
#
#  流程（与打补丁对称）：
#    1) 结束所有 CODEX 进程（含辅助进程），确认全部退出
#    2) 优先用「原版整包备份」整包还原（签名/哈希/资源全部回官方原状）
#       没有整包备份时，退化为「字节级反向补丁」
#    3) 校验签名与完整性
#    4) 重新拉起 CODEX
#
#  设计要点：**不依赖 App 能否打开**——纯文件操作 + 系统命令。
#
set -u
DIR="${0:A:h}"                      # 本脚本所在目录（补丁目录，可整体搬移）
PATCH_PY="$DIR/patch.py"
PRISTINE="$HOME/.codex/patches/_pristine"
GUARD_LABEL="com.xiong.deepseek-summary-proxy"
APP="${CHATGPT_APP:-/Applications/ChatGPT.app}"
PY="/usr/bin/python3"

echo "════════════════════════════════════════════"
echo "  CODEX · 详情美化补丁 —— 退回官方原版"
echo "════════════════════════════════════════════"
echo
echo "本操作将：结束正在运行的 CODEX（包括当前会话）→ 还原官方原版 → 重新打开。"
read -r "?按回车继续，Ctrl+C 取消…" _
echo

# 本机实测：① pgrep -f 读不到该 App 主进程；② 用 ps 的 command 列会把 awk/ps 自己也算进去
# → 统一用 ps 的 comm 列（可执行路径，无参数）匹配，天然不会 self-match
codex_pids() {
  /bin/ps -eo pid=,comm= | /usr/bin/awk -v p="$APP/Contents/" 'index($0,p)>0 {print $1}'
}

kill_codex() {
  osascript -e 'tell application "ChatGPT" to quit' >/dev/null 2>&1
  for i in {1..16}; do
    [[ -z "$(codex_pids)" ]] && return 0
    sleep 0.5
  done
  for p in $(codex_pids); do kill -9 "$p" 2>/dev/null; done
  for i in {1..10}; do
    [[ -z "$(codex_pids)" ]] && return 0
    sleep 0.5
  done
  return 1
}

if [[ ! -f "$PATCH_PY" ]]; then
  echo "✗ 找不到补丁工具：$PATCH_PY"
  read -r "?按回车键关闭…" _; exit 1
fi

# ── 1. 结束所有 CODEX 进程 ──────────────────────────────
echo "── 1/4 结束 CODEX 进程 ──"
echo "· 先请求优雅退出…"
if ! kill_codex; then
  echo "✗ 无法结束 CODEX 进程，已中止"
  /bin/ps -eo pid=,comm= | /usr/bin/awk -v p="$APP/Contents/" 'index($0,p)>0 {print "    " $0}'
  read -r "?按回车键关闭…" _; exit 1
fi
echo "· 已确认 CODEX 全部退出"

# ── 2. 还原官方 ─────────────────────────────────────────
echo
echo "── 2/4 还原官方原版 ──"
rc=1
if ls "$PRISTINE"/ChatGPT.app* >/dev/null 2>&1; then
  echo "· 使用原版整包备份整包还原…"
  "$PY" "$PATCH_PY" --app "$APP" --restore-bundle
  rc=$?
else
  echo "· 未找到整包备份，改用字节级反向补丁…"
  "$PY" "$PATCH_PY" --app "$APP" --revert --force
  rc=$?
fi

# ── 3. 校验 ─────────────────────────────────────────────
echo
echo "── 3/4 校验 ──"
"$PY" "$PATCH_PY" --app "$APP" --verify

# ── 4. 重新拉起 ─────────────────────────────────────────
echo
echo "── 4/4 重新打开 CODEX ──"
if [[ $rc -eq 0 ]]; then
  launchctl print "gui/$(id -u)/$GUARD_LABEL" >/dev/null 2>&1 && launchctl kickstart -k "gui/$(id -u)/$GUARD_LABEL" 2>/dev/null
  open "$APP" 2>/dev/null && echo "· 已重新拉起"
else
  echo "⚠ 还原未完全成功，未自动打开；请查看上面的输出"
fi
read -r "?按回车键关闭本窗口…" _
