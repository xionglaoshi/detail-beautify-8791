#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
「详情美化」补丁工具（Codex 桌面端 app.asar）

作用：让 Codex 桌面端默认展开显示完整思考过程（并把显示区加高）。
特性：等长字节替换（不改 asar 结构）· 幂等 · 可秒级回退 · 升级后按特征串重打

用法：
    patch.py --check              # 只检查：版本 / 特征串 / 当前是否已打
    patch.py --apply              # 打补丁（备份 → 改字节 → 改完整性哈希 → ad-hoc 重签）
    patch.py --revert             # 退回官方原版（写回原始字节 + 恢复哈希 + 重签）
    patch.py --verify             # 校验：哈希 / 签名 / 补丁状态
    patch.py --pre-update         # = --revert（升级前先退回官方）
    patch.py --post-update        # = --apply（升级后重打）
    patch.py --restore-bundle     # 用 _pristine 原版整包覆盖回官方（最坏情况救援）

可选参数：
    --app PATH    目标 App（默认 /Applications/ChatGPT.app，可指向副本做演练）
    --force       目标 App 正在运行时也继续
"""

import argparse
import hashlib
import json
import os
import plistlib
import shutil
import subprocess
import sys
from datetime import datetime
from pathlib import Path

HERE = Path(__file__).resolve().parent
PATCH_DIR = HERE
MANIFEST = HERE / "manifest.json"
BACKUP_DIR = HERE / "backups"
PRISTINE_DIR = HERE.parent / "_pristine"

DEFAULT_APP = Path("/Applications/ChatGPT.app")
INTEGRITY_KEY = "ElectronAsarIntegrity"
INTEGRITY_SUB = "Resources/app.asar"

# ── 可选组件：接入层兜底（思考详情中转服务）────────────────
CODEX_HOME = Path.home() / ".codex"
CONFIG_TOML = CODEX_HOME / "config.toml"
PROXY_PY_CANDIDATES = [
    Path(__file__).resolve().parent / "proxy/proxy.py",          # 补丁目录（正主）
    CODEX_HOME / "tools/deepseek-summary-proxy/proxy.py",        # 旧路径（兼容软链）
]
PROXY_PORT = 8791
PROXY_URL = "http://127.0.0.1:8791/"
PROXY_UPSTREAM = "https://api.deepseek.com/"
PROXY_LABEL = "com.xiong.deepseek-summary-proxy"


def log(msg):
    print(msg, flush=True)


def sh(cmd, timeout=600):
    r = subprocess.run(cmd, capture_output=True, text=True, timeout=timeout)
    return r.returncode, (r.stdout or "") + (r.stderr or "")


def app_paths(app: Path):
    return {
        "app": app,
        "asar": app / "Contents/Resources/app.asar",
        "plist": app / "Contents/Info.plist",
    }


def app_version(plist: Path) -> str:
    with open(plist, "rb") as fh:
        return plistlib.load(fh).get("CFBundleShortVersionString", "unknown")


def read_plist(plist: Path) -> dict:
    with open(plist, "rb") as fh:
        return plistlib.load(fh)


def write_plist(plist: Path, data: dict):
    with open(plist, "wb") as fh:
        plistlib.dump(data, fh, sort_keys=False)


def asar_hash(path: Path) -> str:
    h = hashlib.sha256()
    with open(path, "rb") as fh:
        for chunk in iter(lambda: fh.read(8 * 1024 * 1024), b""):
            h.update(chunk)
    return h.hexdigest()


def asar_header_hash(path: Path) -> str:
    """Electron 的 ElectronAsarIntegrity 校验的是 **asar 头部 JSON**，不是整个文件。
    头部 = 16 字节 pickle 头 + JSON 目录；文件内容（等长替换）改动不影响它。"""
    import struct
    with open(path, "rb") as fh:
        head = fh.read(16)
        json_len = struct.unpack("<I", head[12:16])[0]
        payload = fh.read(json_len)
    return hashlib.sha256(payload).hexdigest()


def plist_hash(plist: Path):
    data = read_plist(plist)
    node = (data.get(INTEGRITY_KEY) or {}).get(INTEGRITY_SUB)
    return node.get("hash") if isinstance(node, dict) else None


def set_plist_hash(plist: Path, value):
    data = read_plist(plist)
    node = data.setdefault(INTEGRITY_KEY, {}).setdefault(INTEGRITY_SUB, {})
    node["hash"] = value
    node.setdefault("algorithm", "SHA256")
    write_plist(plist, data)


def load_manifest() -> dict:
    with open(MANIFEST, encoding="utf-8") as fh:
        return json.load(fh)


def count_occurrences(blob: bytes, needle: str) -> int:
    return blob.count(needle.encode("utf-8"))


def atomic_write(path: Path, data: bytes):
    """原子替换：先写同目录临时文件再 rename。App 正在运行时也不会读到半截文件
    （运行中的进程继续持有旧 inode，下次启动才用新文件）。"""
    tmp = path.with_name(path.name + ".patch-tmp")
    with open(tmp, "wb") as fh:
        fh.write(data)
    try:
        shutil.copymode(path, tmp)
    except OSError:
        pass
    os.replace(tmp, path)


def patch_state(blob: bytes):
    """返回 ('clean' | 'patched' | 'partial', 明细)"""
    details, ok_clean, ok_patched = [], True, True
    for t in load_manifest()["targets"]:
        n_find = count_occurrences(blob, t["find"])
        n_repl = count_occurrences(blob, t["replace"])
        exp = t.get("expect", 1)
        state = "clean" if n_find == exp and n_repl == 0 else (
            "patched" if n_repl == exp and n_find == 0 else "partial")
        ok_clean &= state == "clean"
        ok_patched &= state == "patched"
        details.append((t["id"], state, n_find, n_repl))
    if ok_patched:
        return "patched", details
    if ok_clean:
        return "clean", details
    if all(st in ("clean", "patched") for _, st, _, _ in details):
        return "mixed", details
    return "partial", details


def app_running(app: Path) -> bool:
    """注意：本机实测 pgrep -f 读不到该 App 主进程的 argv（ps 能看到），
    因此统一用 ps 匹配进程命令行，避免"以为没在运行"。"""
    return bool(codex_pids(app))


def codex_pids(app: Path) -> list:
    code, out = sh(["/bin/ps", "-eo", "pid=,command="], timeout=30)
    if code != 0:
        return []
    needle = str(app) + "/Contents/"
    pids = []
    for line in out.splitlines():
        line = line.strip()
        if not line:
            continue
        pid, _, cmd = line.partition(" ")
        if needle in cmd and pid.isdigit() and int(pid) != os.getpid():
            pids.append(int(pid))
    return pids


def resign(app: Path) -> bool:
    """ad-hoc 重签（不带 entitlements / 不启用 hardened runtime）。
    实测：该形式下 ChatGPT.app 可正常启动（已做启动存活自检）。
    不沿用原版 entitlements 的原因：其中含受限权限（application-identifier / keychain-access-groups，
    依赖 OpenAI 团队签名），用在 ad-hoc 签名上反而可能被系统拒绝启动。"""
    log("· 重新签名（ad-hoc）…")
    code, out = sh(["/usr/bin/codesign", "--force", "--deep", "--sign", "-", str(app)])
    if code != 0:
        log("✗ 重签失败：\n" + out.strip()[:800])
        return False
    code, out = sh(["/usr/bin/codesign", "--verify", "--deep", "--strict", str(app)])
    if code != 0:
        log("✗ 签名校验失败：\n" + out.strip()[:800])
        return False
    log("· 签名校验通过")
    return True


def cmd_check(paths: dict) -> int:
    blob = paths["asar"].read_bytes()
    ver = app_version(paths["plist"])
    state, details = patch_state(blob)
    mf = load_manifest()
    log("目标 App：%s（版本 %s）" % (paths["app"], ver))
    log("补丁：%s（编写于版本 %s）" % (mf["name"], mf.get("authored_for_version")))
    for tid, st, nf, nr in details:
        mark = {"clean": "待打", "patched": "已打", "partial": "不匹配"}[st]
        log("  - %-26s %s（特征串命中 %d / 已替换 %d）" % (tid, mark, nf, nr))
    log("当前状态：%s" % {"clean": "未打补丁", "patched": "已打补丁",
                          "mixed": "部分已打（可用 --apply 补齐）", "partial": "需人工核对"}[state])
    return 0 if state in ("clean", "patched", "mixed") else 2


def cmd_apply(paths: dict, force: bool) -> int:
    if app_running(paths["app"]) and not force:
        log("⚠ App 正在运行。建议先退出（⌘Q）再打补丁；确认要继续请加 --force")
        return 3
    blob = paths["asar"].read_bytes()
    ver = app_version(paths["plist"])
    orig_hash = asar_hash(paths["asar"])
    orig_plist_hash = plist_hash(paths["plist"])
    record = {
        "version": ver, "app": str(paths["app"]), "time": datetime.now().isoformat(timespec="seconds"),
        "asar_sha256_before": orig_hash, "plist_hash_before": orig_plist_hash, "items": [],
    }

    buf = bytearray(blob)
    todo, done, bad = [], [], []
    for t in load_manifest()["targets"]:
        exp = t.get("expect", 1)
        n_find = bytes(buf).count(t["find"].encode())
        n_repl = bytes(buf).count(t["replace"].encode())
        if n_repl == exp and n_find == 0:
            done.append(t["id"])
        elif n_find == exp and n_repl == 0:
            todo.append(t)
        else:
            bad.append((t["id"], n_find, n_repl))

    for tid, nf, nr in bad:
        log("✗ 补丁 %s 状态不匹配（原文命中 %d / 已替换 %d，期望 %d），中止" % (tid, nf, nr, 1))
    if bad:
        return 2
    if not todo:
        log("已是补丁状态，无需重复打。")
        return 0

    for t in todo:
        find_b = t["find"].encode("utf-8")
        repl_b = t["replace"].encode("utf-8")
        if len(find_b) != len(repl_b):
            log("✗ 补丁 %s 不是等长替换，本工具拒绝执行（需走重打包路径）" % t["id"])
            return 2
        start, hits = 0, []
        while True:
            i = bytes(buf).find(find_b, start)
            if i < 0:
                break
            hits.append(i)
            start = i + 1
        if len(hits) != t.get("expect", 1):
            log("✗ 补丁 %s 命中 %d 处（期望 %d），中止" % (t["id"], len(hits), t.get("expect", 1)))
            return 2
        for i in hits:
            record["items"].append({"id": t["id"], "offset": i, "original_hex": bytes(buf[i:i + len(find_b)]).hex()})
            buf[i:i + len(find_b)] = repl_b
        log("· 已改：%s（%d 处）" % (t["id"], len(hits)))
    if done:
        log("· 已在补丁状态（跳过）：%s" % "、".join(done))

    BACKUP_DIR.mkdir(parents=True, exist_ok=True)
    rec_path = BACKUP_DIR / ("%s-%s.json" % (ver, datetime.now().strftime("%Y%m%d-%H%M%S")))
    with open(rec_path, "w", encoding="utf-8") as fh:
        json.dump(record, fh, ensure_ascii=False, indent=2)
    log("· 反向补丁已记录：%s" % rec_path)

    atomic_write(paths["asar"], bytes(buf))

    # 等长替换不改 asar 头部 → 完整性哈希（校验头部 JSON）保持有效，**不动 Info.plist**
    hdr = asar_header_hash(paths["asar"])
    if plist_hash(paths["plist"]) != hdr:
        log("⚠ 完整性哈希与 asar 头部不一致（plist=%s… 头部=%s…），已按头部值修正"
            % ((plist_hash(paths["plist"]) or "空")[:12], hdr[:12]))
        set_plist_hash(paths["plist"], hdr)
    else:
        log("· 完整性哈希仍有效（头部 JSON 未变）：%s…" % hdr[:12])

    if not resign(paths["app"]):
        return 4
    log("✅ 补丁完成。请退出并重新打开 Codex 查看效果。")
    return 0


def latest_record(app: Path):
    if not BACKUP_DIR.exists():
        return None
    recs = sorted(BACKUP_DIR.glob("*.json"))
    for p in reversed(recs):
        try:
            d = json.load(open(p, encoding="utf-8"))
        except Exception:
            continue
        if d.get("app") == str(app) and d.get("items"):
            return p, d
    return None


def all_records(app: Path) -> list:
    """该 App 的全部反向补丁记录（新→旧），用于把多轮补丁一次性退回。"""
    if not BACKUP_DIR.exists():
        return []
    out = []
    for p in sorted(BACKUP_DIR.glob("*.json"), reverse=True):
        try:
            d = json.load(open(p, encoding="utf-8"))
        except Exception:
            continue
        if d.get("app") == str(app) and d.get("items"):
            out.append((p, d))
    return out


def cmd_revert(paths: dict, force: bool) -> int:
    if app_running(paths["app"]) and not force:
        log("⚠ App 正在运行。建议先退出（⌘Q）再退回；确认要继续请加 --force")
        return 3
    blob = paths["asar"].read_bytes()
    state, _ = patch_state(blob)
    if state == "clean":
        log("当前已是官方原版（未打补丁），无需退回。")
        return 0
    recs = all_records(paths["app"])
    if not recs:
        log("✗ 找不到反向补丁记录。请改用 --restore-bundle（从 _pristine 整包还原）。")
        return 2
    buf = bytearray(blob)
    repl_by_id = {t["id"]: t["replace"].encode("utf-8") for t in load_manifest()["targets"]}
    seen_offsets, restored = set(), 0
    for rec_path, rec in recs:
        for it in rec["items"]:
            off, orig = it["offset"], bytes.fromhex(it["original_hex"])
            if off in seen_offsets:
                continue
            cur = bytes(buf[off:off + len(orig)])
            repl = repl_by_id.get(it["id"])
            if cur == orig:
                seen_offsets.add(off)
                continue
            if repl is not None and cur != repl:
                log("✗ 偏移 %d 处内容既非补丁值也非原值，中止（文件可能已被其它改动）" % off)
                return 2
            buf[off:off + len(orig)] = orig
            seen_offsets.add(off)
            restored += 1
    atomic_write(paths["asar"], bytes(buf))
    orig_plist = recs[-1][1].get("plist_hash_before")
    if orig_plist and plist_hash(paths["plist"]) != orig_plist:
        set_plist_hash(paths["plist"], orig_plist)
        log("· 已恢复原完整性哈希")
    log("· 已写回官方原始字节 %d 处（来自 %d 条记录）" % (restored, len(recs)))
    if not resign(paths["app"]):
        return 4
    log("✅ 已退回官方状态。")
    return 0


def cmd_verify(paths: dict) -> int:
    ver = app_version(paths["plist"])
    blob = paths["asar"].read_bytes()
    state, _ = patch_state(blob)
    cur = plist_hash(paths["plist"])
    real = asar_header_hash(paths["asar"])
    full = asar_hash(paths["asar"])
    code, out = sh(["/usr/bin/codesign", "--verify", "--deep", "--strict", str(paths["app"])])
    log("版本：%s" % ver)
    log("补丁状态：%s" % {"clean": "官方原版", "patched": "已打补丁",
                          "mixed": "部分已打（--apply 可补齐）", "partial": "不匹配"}[state])
    log("完整性哈希（头部）：%s" % ("一致 ✅" if cur == real else "不一致 ❌（plist=%s… 头部=%s…）" % ((cur or "空")[:12], real[:12])))
    log("asar 整文件 SHA256：%s…（仅供参考，Electron 不校验它）" % full[:16])
    log("签名：%s" % ("有效 ✅" if code == 0 else "无效 ❌"))
    return 0 if (cur == real and code == 0) else 4


# ── 可选组件：接入层兜底（中转服务）────────────────────────

def proxy_port_listening() -> bool:
    import socket
    s = socket.socket()
    s.settimeout(1.0)
    try:
        s.connect(("127.0.0.1", PROXY_PORT))
        return True
    except OSError:
        return False
    finally:
        s.close()


def proxy_service_loaded() -> bool:
    if (Path("/Library/LaunchDaemons") / (PROXY_LABEL + ".plist")).exists():
        return True
    if (Path.home() / "Library/LaunchAgents" / (PROXY_LABEL + ".plist")).exists():
        return True
    return False


def config_base_url() -> str:
    if not CONFIG_TOML.exists():
        return ""
    section = ""
    for line in CONFIG_TOML.read_text(encoding="utf-8").splitlines():
        s = line.strip()
        if s.startswith("[") and s.endswith("]"):
            section = s
            continue
        if section == "[model_providers.deepseek]" and s.startswith("base_url"):
            return s.split("=", 1)[1].strip().strip('"')
    return ""


def set_config_base_url(url: str) -> bool:
    if not CONFIG_TOML.exists():
        return False
    lines = CONFIG_TOML.read_text(encoding="utf-8").splitlines()
    section, done = "", False
    for i, line in enumerate(lines):
        s = line.strip()
        if s.startswith("[") and s.endswith("]"):
            section = s
            continue
        if section == "[model_providers.deepseek]" and s.startswith("base_url"):
            lines[i] = 'base_url = "%s"' % url
            done = True
            break
    if not done:
        return False
    tmp = CONFIG_TOML.with_suffix(".toml.proxy-tmp")
    tmp.write_text("\n".join(lines).rstrip("\n") + "\n", encoding="utf-8")
    os.chmod(tmp, 0o600)
    os.replace(tmp, CONFIG_TOML)
    return True


def cmd_proxy_status() -> int:
    cur = config_base_url()
    listening = proxy_port_listening()
    loaded = proxy_service_loaded()
    on = cur.startswith("http://127.0.0.1")
    log("接入层兜底（思考详情中转）：%s" % ("已启用" if on else "未启用"))
    log("  - 服务定义：%s" % ("存在（%s）" % PROXY_LABEL if loaded else "未安装"))
    log("  - 端口 %d：%s" % (PROXY_PORT, "监听中 ✅" if listening else "未监听 ❌"))
    log("  - 模型接入 base_url：%s" % (cur or "（未找到）"))
    prog = next((p for p in PROXY_PY_CANDIDATES if p.exists()), None)
    log("  - 程序：%s" % (prog if prog else "未找到"))
    return 0


def cmd_proxy_on() -> int:
    prog = next((p for p in PROXY_PY_CANDIDATES if p.exists()), None)
    if prog is None:
        log("✗ 找不到中转程序（已查：%s）" % "、".join(str(p) for p in PROXY_PY_CANDIDATES))
        return 2
    if not proxy_port_listening():
        log("⚠ 中转服务未在监听。请先启动服务（需管理员）：")
        log("    sudo launchctl bootstrap system /Library/LaunchDaemons/%s.plist" % PROXY_LABEL)
        log("  或双击：~/.codex/scripts/CODEX-DeepSeek-思考与工具详情-一键开启.command")
        return 3
    if set_config_base_url(PROXY_URL):
        log("· 已把模型接入切到中转：%s" % PROXY_URL)
        log("✅ 接入层兜底已启用（思考内容可见；「默认展开/多行」仍需渲染层补丁）")
        return 0
    log("✗ 未能写入 config.toml")
    return 4


def cmd_proxy_off() -> int:
    if set_config_base_url(PROXY_UPSTREAM):
        log("· 已把模型接入改回官方：%s" % PROXY_UPSTREAM)
        log("✅ 接入层兜底已关闭（中转服务仍在运行，但不参与链路，不占资源）")
        return 0
    log("✗ 未能写入 config.toml")
    return 4


def cmd_status(paths: dict) -> int:
    rc = cmd_verify(paths)
    log("")
    cmd_proxy_status()
    return rc


def cmd_restore_bundle(paths: dict) -> int:
    cands = sorted([p for p in PRISTINE_DIR.glob("ChatGPT.app*") if p.is_dir()])
    if not cands:
        log("✗ 找不到原版整包备份（%s）" % PRISTINE_DIR)
        return 2
    src = cands[-1]
    log("· 从原版整包还原：%s" % src)
    if app_running(paths["app"]):
        log("· 先结束正在运行的 App…")
        sh(["/usr/bin/pkill", "-f", str(paths["app"] / "Contents/MacOS")], timeout=30)
        sh(["/bin/sleep", "2"])
    dest = paths["app"]
    if dest.exists():
        shutil.rmtree(dest)
    code, out = sh(["/usr/bin/ditto", str(src), str(dest)], timeout=1800)
    if code != 0:
        log("✗ 复制失败：\n" + out.strip()[:500])
        return 4
    code, out = sh(["/usr/bin/codesign", "--verify", "--deep", "--strict", str(dest)])
    log("· 签名校验：%s" % ("通过 ✅" if code == 0 else "失败 ❌"))
    log("✅ 已还原为官方原版整包。")
    return 0


def main() -> int:
    ap = argparse.ArgumentParser(add_help=True)
    ap.add_argument("--app", default=str(DEFAULT_APP))
    ap.add_argument("--force", action="store_true")
    g = ap.add_mutually_exclusive_group(required=True)
    for flag in ("check", "apply", "revert", "verify", "pre-update", "post-update", "restore-bundle",
                 "status", "proxy-on", "proxy-off", "proxy-status"):
        g.add_argument("--" + flag, action="store_true")
    a = ap.parse_args()

    app = Path(a.app).resolve()
    paths = app_paths(app)
    for k in ("asar", "plist"):
        if not paths[k].exists():
            log("✗ 找不到 %s" % paths[k])
            return 2

    if a.check:
        return cmd_check(paths)
    if a.apply or a.post_update:
        return cmd_apply(paths, a.force)
    if a.revert or a.pre_update:
        return cmd_revert(paths, a.force)
    if a.verify:
        return cmd_verify(paths)
    if a.restore_bundle:
        return cmd_restore_bundle(paths)
    if a.status:
        return cmd_status(paths)
    if a.proxy_on:
        return cmd_proxy_on()
    if a.proxy_off:
        return cmd_proxy_off()
    if a.proxy_status:
        return cmd_proxy_status()
    return 2


if __name__ == "__main__":
    sys.exit(main())
