> 历史说明（2026-09）：描述归档时的运行环境；当前仓库入口与边界以根目录 README 为准。

# 可选组件：接入层兜底（思考详情中转服务）

> 2026-09-16 并入「详情美化」补丁，作为**不改 App 源码时的应急方案**。

## 一句话

**渲染层补丁**（本补丁主线）＝ 让界面"愿意显示"思考；**接入层兜底**（本组件）＝ 让界面"有内容可显示"。
两者互补：只有中转没有补丁 → 思考能看到内容，但块是折叠的、需要手动展开；只有补丁没有中转 → 块展开了但里面是空的。

## 它是什么

| 项 | 值 |
|---|---|
| 程序 | **本目录 `proxy.py`**（2026-09-16 已从 `~/.codex/tools/deepseek-summary-proxy/` 迁入；旧路径留软链以兼容 root 级 Daemon 的写死路径） |
| 服务 | `/Library/LaunchDaemons/com.xiong.deepseek-summary-proxy.plist`（开机级 LaunchDaemon，不登录也起） |
| 监听 | `127.0.0.1:8791` |
| 日志 | 迁移前在旧路径；双击本目录 `迁移到补丁目录.command`（需管理员密码）后落到本目录 `proxy.log` |
| 留痕 | `~/.codex/thinking-log/`（每回合思考/工具/回答，可回看全文） |

## 目录内容

```
proxy/
├── proxy.py                    # 中转程序（正主）
├── state/                      # 运行状态与历史（guard 日志、配置备份、旧版本备份…）
└── 迁移到补丁目录.command        # 需要管理员密码：搬日志 + 改 Daemon 路径 + 重启守护（含失败自动回滚）
```

## 原理

把 DeepSeek 的 `reasoning_text` 流**镜像成界面要读的 `reasoning_summary_text` 流**（按 40 字一行、每行单独成段），其余事件原样透传；同时把每回合内容留痕。

> 为什么必须这样做：Codex 桌面端的思考区只读"摘要"通道，而 DeepSeek 不产出摘要 → 界面必然空白。中转把思考"翻译"成界面认的格式。

## 开关命令

```bash
PY=~/.codex/patches/detail-beautify/patch.py
python3 $PY --proxy-status   # 看状态
python3 $PY --proxy-on       # 启用（把模型接入切到中转）
python3 $PY --proxy-off      # 关闭（把模型接入改回官方；服务保留但不参与链路）
python3 $PY --status         # 渲染层 + 接入层 总状态
```

也可用双击控制台：`~/.codex/patches/detail-beautify/CODEX-详情美化-控制台.command`

## 什么时候用它

- **不想改 App 源码**（或某次升级后补丁暂时打不上）→ 开中转兜底：至少能看思考内容 + 有回溯档案；
- 想**临时验证**是不是中转的问题 → `--proxy-off` 一键切回官方链路。

## 注意

- 停用服务（而非只切链路）需要管理员权限：
  `sudo launchctl bootout system /Library/LaunchDaemons/com.xiong.deepseek-summary-proxy.plist`
- **剩余的"旧目录"**：`~/.codex/tools/deepseek-summary-proxy/` 现在只剩 3 个日志 + 1 个指向本目录的软链；Daemon 的 plist（root 属主）里仍写死旧路径，所以**要彻底清掉旧目录，需先跑一次 `迁移到补丁目录.command`**（改 plist 必须管理员密码，脚本已做校验与失败回滚）。
