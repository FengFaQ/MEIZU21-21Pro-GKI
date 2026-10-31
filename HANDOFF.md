# 交接文档 · MEIZU 21 / 21 Pro GKI

> 给**接手这个仓库的人（或下一个会话的 AI）**看。
> 目标：30 分钟内知道「这是什么 / 现在到哪了 / 下一步做什么 / 别碰什么」。
>
> 最后更新：2026-10-31
> 仓库：`FengFaQ/MEIZU21-21Pro-GKI`（public，`main`）
> 本地工作副本：`C:\Step\work\gki-rebuild`

---

## 0. 一句话

给 **魅族 21 Pro（M2481）** 和 **魅族 21（M2461）** 编译可刷入的 GKI 内核
（**FinaSU + SUSFS**），只替换 `boot` 分区，系统仍是原厂 Flyme。

```
GKI android14-6.1 / sub_level 25 / os_patch 2023-10
LTO = none
内核 release 串 = 6.1.25-android14-11-maybe-dirty   ← 必须与原厂逐字符一致
```

**不要跳 GKI 小版本**。跳版本会改 `struct zone` / `struct lruvec` 的字段偏移，
而魅族闭源模块是按字节偏移直接访问的 —— 开机即崩，且现场看不出原因。

---

## 1. 当前状态（截至 2026-10-31）

| 项 | 魅族 21 Pro (M2481) | 魅族 21 (M2461) |
|---|---|---|
| 设备事实 `device/` | ✅ 齐全（13 个文件） | ✅ 齐全（13 个文件） |
| AVB 骨架等价性 | ✅ `repack(原厂Image) == 原厂 boot.img`，diff = 0 | ✅ 同左，diff = 0 |
| 工作流 | ✅ 已跑通 **run#24 全绿** | ⏳ **run #1 已派发（2026-10-31），进行中** |
| release 产物 | ✅ `meizu21pro-finatsu-24` / `meizu21pro-latest` | — |
| 真机刷入验证 | ❌ **未做** | ❌ 未做 |
| 管理器 APK 绑定 | ✅ FinaSU `v1.3.2`（cert `0x02cc`） | ✅ 复用同一套 CI 逻辑 |

> **2026-10-31 首跑前的静态排查已完成并推送（`3d5df57`）**，见 §10。
> 其中两处是「21 一跑必挂 / 读错机型」的真问题，不是环境问题。

### 下一步（按顺序）

1. **跑 21 的构建** —— 已派发 run #1（`3d5df57`）。看结果：见 §8 的监控命令。
2. 拿 `boot.img` 跑 `audit_boot.py`（见 `FLASH_SOP.md` §1.4），必须全部 OK 才刷。
3. 只刷**非活动槽**，验证开机 + Root + 管理器识别 + SUSFS。
4. 首次开机后采 `dmesg` 里的 kCFI 哈希，把门禁 3 从 warn 转成硬失败。

---

## 2. 仓库结构

```
.github/workflows/
  build.yml               ← 共享主流水线（workflow_call，30 input，~72 step）
  meizu21.yml             ← 魅族 21 入口（6 个 dispatch input）
  meizu21pro.yml          ← 魅族 21 Pro 入口（6 个 dispatch input）
  emergency-cleanup.yml   ← 紧急下架 release（沿用官方）
MEIZU 21pro/
  README.md
  device/                 ← 21 Pro 设备事实（config / KMI / AVB 模板 / kit 补丁）
魅族21/
  README.md
  device/                 ← 21 设备事实
scripts/                  ← 两机型共用
  repack_avb_boot.py      ← boot.img 打包（AVB0 + AVBf 注入），**命名参数**
  make_avb_skeleton.py    ← 从原厂 boot.img 生成 AVB 骨架
  avb_template_source.py  ← 机型注册表 + 模板来源门禁
  apply_kit_onemind.py    ← kit 补丁的 OneMind hook
  check_kcfi.py           ← 门禁 3：kCFI 类型哈希
  check_krcrc_symvers.py  ← 门禁 2：KMI 审计 + __kcrctab 二进制 CRC 锁定
  finasu_compat/extract_manager_cert.py  ← 从 APK 提证书
  susfs_fixes/apply.sh    ← SUSFS 补丁
config/ data/ security_patch/ zram/ zram_patch/ hmbird_patch.c
```

---

## 3. 两个入口工作流的差异

| 项 | 魅族 21 | 魅族 21 Pro |
|---|---|---|
| 工作流文件 | `.github/workflows/meizu21.yml` | `.github/workflows/meizu21pro.yml` |
| `device_dir` | `魅族21/device` | `MEIZU 21pro/device` |
| release 标签 | `meizu21-finatsu-<run#>` | `meizu21pro-finatsu-<run#>` |
| latest 标签 | `meizu21-latest` | `meizu21pro-latest` |
| **dist 分支** | **`dist-meizu21`** | **`dist`** |
| concurrency | `meizu21-*` | `meizu21pro-*` |
| `trim_unused_ksyms` | `n` | `n` |
| `strict_kcfi` | `false` | `false` |

其余全部复用 `build.yml`。**dist 分支必须分开**，否则两个机型互相覆盖。

---

## 4. 管理器 APK 绑定（用户硬要求）

```
job1 fetch-manager : gh release download 拉 FinaSU 官方指定版本 APK
                     -> scripts/finasu_compat/extract_manager_cert.py 提证书
                     -> 输出 cert_size / cert_hash / apk_name / apk_sha256
job2 build-kernel  : 把该证书编进内核【第二信任槽】
                     (KSU_EXPECTED_SIZE2 / KSU_EXPECTED_HASH2 / KSU_SECOND_SIGNATURE_RELEASE=1)
job3 release       : 内核产物 + **这一版编译所用的那份 APK** 一起上传，
                     并写 MANAGER_INFO.txt 记录 APK sha256 与证书哈希
```

**为什么不用内核默认信任槽**：默认槽（`EXPECTED_SIZE` / `EXPECTED_HASH`）是
KernelSU 自己的发布签名，FinaSU 的 APK 过不了；必须走第二槽。

**用户必须安装 release 附带的那份 APK**。装了别的版本 → 管理器显示「未安装」
或「需要更新内核」。这不是刷机问题，是证书不匹配。

`build.yml` 的注入方式（**别改成 `bazel ... -- VAR=val`**，kleaf 会把它当 target）：

```bash
# 写进 FinaSU 的 kernel/Kbuild 头部
KSU_EXPECTED_SIZE2 := 0x02cc
KSU_EXPECTED_HASH2 := 2d89b50aaf1db013c2b6738f973f48b62ea21a3a8d7216bfec3521065d448458
KSU_SECOND_SIGNATURE_RELEASE := 1
```

---

## 5. 三道门禁（编译后强制执行）

| 门禁 | 内容 | 失败处理 | 为什么 |
|---|---|---|---|
| 1/3 | 内核 release 串（从 `device/so.txt` 读，不是写死） | **硬失败** | 串错 → `system_dlkm` 的模块目录找不到 → 模块全不加载 → 卡第一屏 |
| 2/3 | KMI 审计 + `__kcrctab` 二进制 CRC 覆写（对照原厂 `stock_Module.symvers`） | **硬失败** | CRC 错 → `disagrees about version` → 模块拒载 |
| 3/3 | kCFI 类型哈希（10 函数 + 8 个 OneMind traceiter 调用点） | 默认 warn，`strict_kcfi=true` 转硬失败 | 部分不匹配仍可能开机，先给镜像试刷比「什么都拿不到」好 |

> 门禁 1 原来写死 `6.1.25-android14-11-maybe-dirty`。build.yml 是两机型共享的，
> 2026-10-31 改成从 `$DEVICE_DIR/so.txt` 的 `kernel_release=` 读，兜底 `inputs.version`。

---

## 6. 触发方式（**只有手动**）

```powershell
$env:GH_TOKEN='<PAT>'
$h=@{'Accept'='application/vnd.github+json';'User-Agent'='dsh';'Authorization'="Bearer $env:GH_TOKEN"}

# 列出 workflow id
Invoke-RestMethod 'https://api.github.com/repos/FengFaQ/MEIZU21-21Pro-GKI/actions/workflows' -Headers $h |
  Select-Object -ExpandProperty workflows | Select-Object id,name,path

# 派发（ref 必须是 main）
Invoke-RestMethod -Uri "https://api.github.com/repos/FengFaQ/MEIZU21-21Pro-GKI/actions/workflows/<ID>/dispatches" `
  -Method Post -Headers $h -Body (@{ref='main'}|ConvertTo-Json)
```

**为什么不能用 push 触发**（实测两次 0 job）：

1. `build.yml` 是可复用 workflow，`build-kernel` 用 `needs.*.outputs`；
   push 上下文展开时跨 job outputs 解析不了 → 整个调用被判无效 → run 里只剩
   release skipped，日志是空 zip。
2. `workflow_dispatch.inputs` **上限 10 个**。超限 GitHub 不报语法错，而是
   **拒绝注册整个 workflow**：名字显示成路径、dispatch 返回 422、push 出的 run 是 0 job。

---

## 7. 已知风险 / 待办

| # | 项 | 说明 |
|---|---|---|
| 1 | ~~21 一次 CI 都没跑过~~ | 已派发 run #1（2026-10-31，`3d5df57`）。跑前已修掉 4 处必挂/读错机型的问题，见 §10 |
| 2 | **21 没有 kit 补丁** | 21 Pro 的 `patches/01-m2481-...patch` 是 m2481 专属，21 走「跳过补丁」分支（已确认是正常路径）。缺的是 OneMind hook + ko 侧 krcrc，**不影响**门禁 2 的二进制 CRC 锁定 |
| 3 | **`stock_Module.symvers` 是复用 21 Pro 的** | 见 `魅族21/device/stock_Module.symvers.README.md`。交叉核对：共同 NEED 4180 个、**CRC 差异 0**、内核需提供的 3036 个 CRC 100% 相同 → 等价。两份文件 sha256 实测完全相同（`580787ce…`）。以后拿到 21 自己的直接替换即可 |
| 4 | **AVB vbmeta 是原厂块原样拷贝** | 新内核比原厂大时，vbmeta 里 HASHTREE 的 `image_size` 仍是 `0x2054000`，与实际 payload 末尾（run24 = `0x2159000`）不符。判为可接受：魅族 21 线在完全不注入 AVB 时也能开机，且 kit README 说 ABL 只「读取」footer/descriptor。**但如果实测跳 Fastboot，第一个怀疑对象就是这里** |
| 5 | 真机验证全未做 | 两台机都没刷过自编内核 |
| 6 | kCFI 期望值无实测 | 门禁 3 目前 warn。首刷拿到 `dmesg` 的 `expected type 0x...` 后回填 |
| 7 | SUSFS 校验失败只降级为警告 | `apply.sh` 里的 `\|\| true` 类处理，不阻断 |
| 8 | **21 的 KMI 清单从未被内核编译消费过** | `魅族21/device/module_kmi_manifest.txt` / `vendor_dlkm_modules.txt` / `system_dlkm_modules.txt` 在 `build.yml` 里**零引用**；`ref_stock.config` 也只被新加的前置步骤检查存在性，真正生效的是 `config/` + `data/`。21 的 defconfig 事实尚未进入编译链，属未验证项 |

---

## 8. 快速上手命令

> ⚠ **本机网络限制（2026-10-31 实测）**，不知道这两条会浪费半小时：
>
> | 通道 | 状态 |
> |---|---|
> | Windows `Invoke-RestMethod` / `curl.exe` 访问 api.github.com | ❌ TLS 握手失败（`SEC_E_NO_CREDENTIALS` / `安全包中没有可用的凭证`）。系统时间正常，是 schannel 凭据库问题 |
> | `git push` 走 `github.com:22` | ❌ `Connection closed` |
> | **WSL 里 `curl` 访问 api.github.com** | ✅ **可用（走这条）** |
> | **`git push` 走 `ssh.github.com:443`** | ✅ 可用，需带私钥 + 口令 askpass |
>
> 顺带：PowerShell 控制台是 GBK 代码页，看仓库里的中文文件会显示成乱码，
> **但文件本身是干净的 UTF-8**（`file` 验证过）。审代码一律用 WSL 的 `cat`/`sed`。

```bash
# 看 CI 状态（WSL）
T='<PAT>'
H=(-H "Authorization: Bearer $T" -H 'Accept: application/vnd.github+json' -H 'User-Agent: dsh')
curl -sS "${H[@]}" 'https://api.github.com/repos/FengFaQ/MEIZU21-21Pro-GKI/actions/runs?per_page=5' \
 | python3 -c 'import json,sys;[print(r["run_number"],r["name"],r["status"],r["conclusion"]) for r in json.load(sys.stdin)["workflow_runs"]]'
```

```bash
# 推送（22 端口不通，走 443；口令 162636）
mkdir -p /tmp/gkiauth && chmod 700 /tmp/gkiauth
cp /mnt/c/Users/Administrator/.ssh/id_rsa /tmp/gkiauth/ && chmod 600 /tmp/gkiauth/id_rsa
printf '#!/bin/sh\necho 162636\n' > /tmp/gkiauth/askpass.sh && chmod 700 /tmp/gkiauth/askpass.sh
export SSH_ASKPASS=/tmp/gkiauth/askpass.sh SSH_ASKPASS_REQUIRE=force DISPLAY=dsh
cd /mnt/c/Step/work/gki-rebuild
git -c core.sshCommand='ssh -o StrictHostKeyChecking=no -i /tmp/gkiauth/id_rsa -p 443' \
  push 'ssh://git@ssh.github.com:443/FengFaQ/MEIZU21-21Pro-GKI.git' main
rm -f /tmp/gkiauth/id_rsa      # 用完删掉私钥副本
```

```bash
# 跟踪某个 run 的逐步骤进度
python3 /mnt/c/Step/work/gki-pro/mon21/watch.py
```

```bash
# 本地校验（不需要网络）
cd /mnt/c/Step/work/gki-rebuild
python3 scripts/avb_template_source.py "魅族21/device/avb_template/source.txt"
python3 scripts/avb_template_source.py "MEIZU 21pro/device/avb_template/source.txt"
```

```powershell
# 产物取回：走 dist 分支，不要用 git show（PowerShell 会 CRLF 转换把 Image 撑大）
# 21 线是 dist-meizu21，21 Pro 线是 dist —— 别拿错
$git='C:\Program Files\Git\cmd\git.exe'
& $git -C C:\Step\work\gki-rebuild fetch --depth 1 origin dist-meizu21
& $git -C C:\Step\work\gki-rebuild worktree add --detach C:\Step\work\dist21 FETCH_HEAD
```

---

## 9. 相关文档

| 文档 | 内容 |
|---|---|
| `MEMORY.md`（本仓库） | **踩坑记录 + 决策理由**，改代码前先看 |
| `MEIZU 21pro/README.md` | 21 Pro 设备事实 + 实测数值 |
| `魅族21/README.md` | 魅族 21 设备事实 + 实测数值 |
| `魅族21/device/stock_Module.symvers.README.md` | 为什么 21 复用 21 Pro 的 symvers |
| `C:\Step\work\gki-pro\FLASH_SOP.md` | 刷入 + 故障诊断 SOP（含刷前审计脚本用法） |
| `C:\Step\uwuaosp\记忆\09_GKI内核线.md` | 更早的 21 线历史（ReSukiSU 时代，教训仍适用） |

---

## 10. 2026-10-31 首跑前的修复（commit `3d5df57`）

21 一次 CI 都没跑过。静态排查发现**四处会让 21 读错机型或直接硬失败**的问题，
全部修完才派发 run #1。共同点：都是「共享流水线里残留 m2481 假设」这一类。

| # | 问题 | 后果 | 修法 |
|---|---|---|---|
| 1 | **`DEVICE_DIR` 从未被赋值** | `build.yml` 里 8 处 `${DEVICE_DIR:-MEIZU 21pro/device}`，而该变量只在 input 里声明、从未 `export`（入口用 `with:` 传给 `workflow_call`，默认不进 step 的 `env`）。于是**所有设备事实路径静默落到 21 Pro 目录**：KMI 基准、原厂 config、AVB 模板全读错机型，且门禁 2 会拿 21 Pro 的 CRC 去锁 21 的内核 | 8 处统一改 `${DEVICE_DIR:?device_dir not set}`（缺失即中断） |
| 2 | **21 会在「配置内核选项」硬失败** | 该步骤开头有一段 `TRIM_WANT="y"` + 缺白名单就 `exit 1` 的断言，但 `魅族21/device` 没有 `vmlinux_abi_symbollist.raw`，而两入口都传 `trim_unused_ksyms=n` —— 断言与后面实际取值自相矛盾（死代码，对 21 Pro 无害只因其有该文件） | 断言改为跟 `inputs.trim_unused_ksyms` 走，只在 `=y` 时要求白名单 |
| 3 | **门禁 1 的 `so.txt` 解析对 21 Pro 一直失效** | 两份 `so.txt` 写法不同：魅族21 是 `kernel_release=...`，21 Pro 是 `kernel_release      = ...`（等号两侧有空格）。原正则 `^kernel_release=` 对 21 Pro 解析为空 → 一直**静默退回 `inputs.version`** | 两端统一用 `kernel_release[[:space:]]*=[[:space:]]*` |
| 4 | 设备目录问题会伪装成「版本串不符」 | 上面的错法都指向门禁 1，把人引向 kCFI/版本串方向 | 新增步骤「校验设备事实目录 (device_dir)」，在 checkout 后立刻断言目录、关键文件、`kernel_release` 可解析 |

另：`meizu21.yml` 的 release 标题 `21Pro GKI` → `魅族 21 GKI`。

**验证方式（全部是本地静态，没跑 CI 就下了结论）**：
- 四个 workflow YAML 可解析；两入口 dispatch inputs 仍为 6（上限 10）
- `build.yml` 全部 58 个步骤的 shell 片段 `bash -n` 通过
- 新步骤按 `device_dir` 实跑三种取值：`魅族21/device` ✅、`MEIZU 21pro/device` ✅、不存在的目录 → exit 1
- TRIM 前置断言按 `n` / `y` 实跑：`n` 通过、`y`+无白名单 → exit 1、`y`+有白名单 → 通过
- `${VAR:?msg}` 在 `set -e` 与 `set +e` 下均会中止脚本（已验证）
- 两份 `stock_Module.symvers` sha256 完全相同 `580787ce…`
- 改动文件均 LF、无 BOM

**教训**：`workflow_call` 的 `with:` 参数不会自动变成 step 的 `env`。
凡是 `${DEVICE_DIR:-<某机型>}` 这种兜底，等于「悄悄用了错误机型」，
比直接报错危险得多。**共享流水线里不要留任何机型兜底。**
