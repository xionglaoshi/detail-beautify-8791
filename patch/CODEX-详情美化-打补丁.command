#!/bin/zsh
#
#  CODEX「详情美化」补丁 · 一键打补丁（双击运行）
#
#  流程（按用户 2026-09-16 要求）：
#    1) 结束所有 CODEX 进程（含辅助进程），确认全部退出
#    2) 打补丁（等长字节替换 + ad-hoc 重签 + 校验）
#    3) 查看中转服务状态（v2 起不依赖中转：补丁直接读模型原生思考）
#    4) 重新拉起 CODEX
#
#  退回入口：同目录 CODEX-详情美化-退回官方.command
#
set -u
DIR="${0:A:h}"                      # 本脚本所在目录（补丁目录，可整体搬移）
PATCH_PY="$DIR/patch.py"
GUARD_LABEL="com.xiong.deepseek-summary-proxy"
APP="${CHATGPT_APP:-/Applications/ChatGPT.app}"
PY="/usr/bin/python3"

echo "════════════════════════════════════════════"
echo "  CODEX · 详情美化补丁（思考过程默认展开显示）"
echo "════════════════════════════════════════════"
echo
echo "本操作将：结束正在运行的 CODEX（包括当前会话）→ 打补丁 → 重新打开。"
read -r "?按回车继续，Ctrl+C 取消…" _
echo

# 本机实测：① pgrep -f 读不到该 App 主进程；② 用 ps 的 command 列会把 awk/ps 自己也算进去
# （命令行里含 App 路径）→ 统一用 ps 的 comm 列（可执行路径，无参数）匹配，天然不会 self-match
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
  echo "✗ 无法结束 CODEX 进程，已中止（未做任何修改）"
  echo "  仍在运行的进程："
  /bin/ps -eo pid=,comm= | /usr/bin/awk -v p="$APP/Contents/" 'index($0,p)>0 {print "    " $0}'
  read -r "?按回车键关闭…" _; exit 1
fi
echo "· 已确认 CODEX 全部退出"

# ── 2. 打补丁 ───────────────────────────────────────────
echo
echo "── 2/4 打补丁 ──"
"$PY" "$PATCH_PY" --app "$APP" --check
"$PY" "$PATCH_PY" --app "$APP" --apply --force
rc=$?
if [[ $rc -ne 0 ]]; then
  echo
  echo "✗ 打补丁未成功（退出码 $rc）。"
  echo "  如需恢复官方：双击 CODEX-详情美化-退回官方.command"
  read -r "?按回车键关闭…" _; exit $rc
fi
echo
"$PY" "$PATCH_PY" --app "$APP" --verify

# ── 3. 中转服务状态（v2 起不依赖，仅提示）─────────────────
echo
echo "── 3/4 中转服务（v2 已不依赖，仅备用）──"
if lsof -nP -iTCP:8791 -sTCP:LISTEN >/dev/null 2>&1; then
  echo "· 中转服务在运行（127.0.0.1:8791）——当前补丁不需要它，留着备用即可"
else
  echo "· 中转服务未运行（正常：补丁已改为直接读模型原生思考，不走中转）"
  echo "  需要恢复回溯档案时再开：sudo launchctl bootstrap system /Library/LaunchDaemons/$GUARD_LABEL.plist"
fi

# ── 4. 重新拉起 CODEX ───────────────────────────────────
echo
echo "── 4/4 重新打开 CODEX ──"
open "$APP" 2>/dev/null && echo "· 已重新拉起"
echo
echo "✅ 完成：补丁已生效于新启动的 CODEX（思考内容直读模型原生思考；思考中展开显示、回答完自动折叠）。"
echo "不想要了：双击 CODEX-详情美化-退回官方.command 即可回到官方原版。"
read -r "?按回车键关闭本窗口…" _
