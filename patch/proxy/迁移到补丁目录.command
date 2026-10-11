#!/bin/zsh
#
#  把「思考详情中转」彻底迁入补丁目录（需要管理员密码）
#
#  背景：中转服务由**开机级 LaunchDaemon** 启动，其 plist 归 root 所有，
#        里面写死了 4 个路径（程序 + 3 个日志）。程序本体已搬进补丁目录、
#
#        旧路径留软链；本脚本负责把最后两件事做完：
#          1) 把 3 个日志文件搬进补丁目录
#          2) 把 plist 里的路径改到补丁目录并重启守护
#        做完后 ~/.codex/tools/deepseek-summary-proxy/ 即可删除。
#
set -u
OLD="$HOME/.codex/tools/deepseek-summary-proxy"
NEW="$HOME/.codex/patches/detail-beautify/proxy"
PLIST="/Library/LaunchDaemons/com.xiong.deepseek-summary-proxy.plist"
LABEL="com.xiong.deepseek-summary-proxy"

echo "════════════════════════════════════════════"
echo "  中转服务 · 彻底迁入补丁目录（需要管理员密码）"
echo "════════════════════════════════════════════"
echo
echo "将执行：搬日志 → 改 Daemon 路径 → 重启守护 → 校验端口"
echo "若校验失败会自动回滚到原配置。"
read -r "?按回车继续，Ctrl+C 取消…" _
echo

BACKUP="/tmp/com.xiong.deepseek-summary-proxy.plist.bak-$(date +%Y%m%d-%H%M%S)"
cp "$PLIST" "$BACKUP" 2>/dev/null || { echo "✗ 无法读取 $PLIST（需要管理员权限）"; read -r "?按回车键关闭…" _; exit 1; }
echo "· 已备份 plist → $BACKUP"

sudo /bin/zsh -c "
set -e
mkdir -p '$NEW'
for f in proxy.log stdout.log stderr.log; do
  [ -e '$OLD/'\"\$f\" ] && mv '$OLD/'\"\$f\" '$NEW/'\"\$f\" || true
done
/usr/bin/sed -i '' 's|$OLD/|$NEW/|g' '$PLIST'
launchctl bootout system '$PLIST' 2>/dev/null || true
sleep 1
launchctl bootstrap system '$PLIST'
" || { echo "✗ 迁移命令执行失败"; read -r "?按回车键关闭…" _; exit 1; }

echo "· 已改路径并重启守护，等待端口…"
ok=0
for i in {1..20}; do
  if lsof -nP -iTCP:8791 -sTCP:LISTEN >/dev/null 2>&1; then ok=1; break; fi
  sleep 0.5
done

if [[ $ok -eq 1 ]]; then
  echo "✅ 迁移完成："
  echo "   守护：$LABEL（system）"
  echo "   程序：$NEW/proxy.py"
  echo "   日志：$NEW/proxy.log"
  echo
  echo "旧目录现在只剩软链（保证兼容），可安全删除："
  echo "   rm -rf $OLD"
else
  echo "❌ 端口未监听，自动回滚 plist…"
  sudo /bin/zsh -c "cp '$BACKUP' '$PLIST'; launchctl bootout system '$PLIST' 2>/dev/null || true; sleep 1; launchctl bootstrap system '$PLIST'" || true
  echo "· 已回滚，请把输出发给我排查"
fi
read -r "?按回车键关闭本窗口…" _
