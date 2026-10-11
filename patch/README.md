> 历史说明（2026-09）：描述归档时的运行环境；当前仓库入口与边界以根目录 README 为准。

# 详情美化（Codex 桌面端补丁）

> 建立 2026-09-16 ｜ 目标 App：`/Applications/ChatGPT.app`（编写时版本 26.908.70816）
> 作用：**让桌面端默认展开显示完整思考过程**，并加高显示区（从 140px → 360px，可看更多行）

## 一键操作（双击即可）

| 操作 | 脚本 |
|---|---|
| **控制台（推荐入口）** | `~/.codex/patches/detail-beautify/CODEX-详情美化-控制台.command`（菜单：打补丁 / 退回 / 开兜底 / 关兜底 / 看状态） |
| 打补丁 / 修复补丁 | `~/.codex/patches/detail-beautify/CODEX-详情美化-打补丁.command` |
| 退回官方原版（救援） | `~/.codex/patches/detail-beautify/CODEX-详情美化-退回官方.command` |

## 结构（v2：只靠改源码，不依赖中转）

| 层 | 组件 | 现状 |
|---|---|---|
| 渲染层（唯一依赖） | app.asar 补丁（本目录） | **直读模型原生思考通道**（`content`）→ 思考中展开显示正文、回答完自动折叠；工具调用详情走 `STEPS_EXECUTION` |
| 接入层（已停用、留档备用） | `proxy/` 子目录的中转服务 | 2026-09-16 停用：`base_url` 已改回官方直连，LaunchDaemon 已 bootout。**目录保留**，将来要"思考回溯档案"（`~/.codex/thinking-log`）时再 `patch.py --proxy-on` 复用 |

**为什么能不用中转**：桌面端原来只渲染"摘要通道"（DeepSeek 不产），补丁 v2 把取数改成"原生思考通道"后，模型直连即可显示全文。

```bash
PY=~/.codex/patches/detail-beautify/patch.py
python3 $PY --status        # 两层总状态
python3 $PY --proxy-on      # 开接入层兜底（把模型接入切到中转）
python3 $PY --proxy-off     # 关接入层兜底（链路改回官方）
```

**打补丁脚本的执行流程**（用户 2026-09-16 定稿，避免"半运行状态"下打补丁出错）：

```
确认提示 → 结束所有 CODEX 进程（含辅助进程，确认全部退出）
        → 打补丁（等长替换 + ad-hoc 重签）→ 校验
        → 确认中转服务在跑 → 重新拉起 CODEX
```

退回脚本流程对称：确认 → 结束进程 → 整包还原官方 → 校验 → 重新拉起。

> 技术备注：本机 `pgrep -f` **读不到**该 App 主进程的 argv（`ps` 能看到），所以脚本统一用 `ps -eo pid=,command=` 匹配，避免"以为没在运行/杀不掉"。

命令行等价：

```bash
python3 ~/.codex/patches/detail-beautify/patch.py --check    # 只看状态
python3 ~/.codex/patches/detail-beautify/patch.py --apply    # 打补丁
python3 ~/.codex/patches/detail-beautify/patch.py --verify   # 校验
python3 ~/.codex/patches/detail-beautify/patch.py --revert   # 字节级退回
python3 ~/.codex/patches/detail-beautify/patch.py --restore-bundle  # 整包还原官方
```

打完补丁需 **⌘Q 退出并重开 CODEX** 才生效。

## 改了什么（v2：5 处等长替换，不改 asar 结构）

| id | 位置 | 改动 |
|---|---|---|
| **`reasoning-render-from-content`（v2 根因修复）** | 思考块取数 | `he(c.summary)` → `he(c.content)`：**从"摘要通道"改为"原生思考通道"**，DeepSeek 不产摘要也能显示全文，不再需要中转镜像 |
| **`reasoning-stream-visible`（v2）** | 思考中是否显示正文 | `s==null\|\|s===\`resumed\`` → 恒真：**"思考中"就展开显示正文**（原来只在特定会话状态下才显示） |
| **`reasoning-collapse-after-done`（v2）** | 折叠初值 | `useState(!0)` → `useState(!1)`：**回答结束后自动折叠**，点标题可展开看全文（覆盖 v1 的"默认展开"） |
| `reasoning-height`（v1 保留） | 思考块显示区高度上限 | `8.75rem`(140px) → `22.5rem`(360px) |
| `keep-reasoning-items`（v1 保留） | 转录构建过滤条件 | `O.length===0` → `O.length>=+0`（条件恒真）：避免带工作活动项的消息把 thinking 条目整条丢弃 |

> **根因（2026-09-16 定位）**：不是数据、不是中转、不是长度限制，而是桌面端转录构建里的一行过滤：
> `p.push(...s.filter(e=>O.length===0||e.item.type!==\`reasoning\`))`
> 当消息带 work-activity（而每条执行命令的消息都带），`reasoning` 条目直接被过滤掉。

## 关键技术结论（踩过坑，务必保留）

1. **Electron 的 asar 完整性哈希只覆盖 asar 头部 JSON**（不是整个文件）。
   → 等长替换不改头部，`Info.plist` 的 `ElectronAsarIntegrity` **不需要动**；改了反而会破坏校验。
2. **重签会丢 entitlements**。原版含受限权限（`application-identifier`、`keychain-access-groups`、`aps-environment` 等，依赖 OpenAI 团队签名），ad-hoc 签名若沿用会被系统拒绝。
   → 本补丁采用 **ad-hoc、不带 entitlements、不启用 hardened runtime**，并**实测启动存活**（副本启动自检通过）。
   → 代价：推送通知、钥匙串访问组、摄像头/麦克风等权限可能受影响（当前未发现问题）。
3. **写入用原子替换**（临时文件 + rename）：App 运行中打补丁也不会读到半截文件（旧进程继续用旧 inode，下次启动生效）。

## 升级流程（App 更新后）

1. 先 **退回官方**（`CODEX-详情美化-退回官方.command`）——因为 Sparkle 安装更新时会校验代码签名，补丁后是 ad-hoc 签名会装不上；
2. 正常升级 CODEX；
3. 再双击 **打补丁**（脚本按"特征串"重新定位；若新版代码变了会报错而不是盲打）。

（若某次忘了先退回就升级失败，可用官方安装包重装后再打补丁。）

## 退回机制（三层）

| 层 | 位置 | 用途 |
|---|---|---|
| 反向字节补丁 | `backups/<版本>-<时间>.json` | 秒级退回（同版本） |
| 原版整包备份 | `~/.codex/patches/_pristine/ChatGPT.app-<版本>/` | 最坏情况整包还原（签名/资源全部官方原状） |
| 救援脚本 | `CODEX-详情美化-退回官方.command` | 双击执行，**不依赖 App 能否打开** |

> `_pristine/` 已登记为**不进 Codex 备份**（见 `~/.codex/scripts/codex_backup.py` 的 `NOISE_DIRS`）。

## 已知边界

- 只改界面渲染，不碰模型链路、不碰数据。
- 升级后特征串若不匹配 → 报错等人工更新 manifest，**不盲打**。
- 渲染层特效（图文并茂）为 v2 口子，详见 `~/.codex/workspace/maintenance/outputs/详情美化-补丁方案.md` §十二。
