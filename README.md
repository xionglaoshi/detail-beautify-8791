# detail-beautify-8791

熊翔开发的 macOS 桌面端详情美化工具与 8791 本地中转。整理自 2026-09 的历史归档，包含 app.asar 特征串补丁、双击控制台、回退脚本，以及将 DeepSeek reasoning_text 镜像为 reasoning_summary_text 的可选中转服务。

## 本地中转

```sh
python3 patch/proxy/proxy.py --port 8791 --no-thinking-log
```

默认监听 `127.0.0.1:8791`，上游地址在 `proxy.py` 中为 `https://api.deepseek.com`。授权请求头由调用者提供。本命令不会修改客户端配置。需要时由使用者将对应客户端的 base_url 指向 `http://127.0.0.1:8791/`；关闭时恢复自己的原配置。

不加 `--no-thinking-log` 时会将思考、工具及回答记录到 `~/.codex/thinking-log/`；可用 `--thinking-log` 指定目录。折行、分段、限长选项及 `summary-cap.json` 可控制摘要镜像形态。

## 桌面端补丁

历史目标版本为 `/Applications/ChatGPT.app`，归档版本 `26.908.70816`。特征串与签名规则只代表当时实现；本次发布未修改任何已安装 App，也未验证当前版本的适用性。

```sh
python3 patch/patch.py --app /path/to/copied/ChatGPT.app --check
python3 patch/patch.py --app /path/to/copied/ChatGPT.app --apply
python3 patch/patch.py --app /path/to/copied/ChatGPT.app --revert
```

先使用 App 副本核对特征串。`--apply` 会修改目标 App 并重签；原包备份与反向字节备份是回退依据。双击脚本会结束并重启目标 App，需阅读脚本后使用。补丁和签名变化可能影响升级与权限，细节见 [历史补丁说明](patch/README.md)。

`patch/proxy/迁移到补丁目录.command` 仅用于已有历史 LaunchDaemon 的迁移，需管理员权限，不能当作新机器的安装器。

## 目录

- `patch/`：补丁工具、manifest、双击控制台及回退脚本。
- `patch/proxy/`：可选中转源码及配置示例。

历史运行日志、私人客户端配置、个人检查报告、字节备份和官方 App 文件不随仓库发布。MIT 授权，见 [LICENSE](LICENSE)。
