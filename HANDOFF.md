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
| 工作流 | ✅ 已跑通 **run#24 全绿** | ✅ **run#14 全绿**（三道门禁全过，5 个 NEED 符号已补齐） |
| release 产物 | ✅ `meizu21pro-finatsu-24` / `meizu21pro-latest` | ✅ `meizu21-finatsu-14` / `meizu21-latest` |
| 真机刷入验证 | ❌ **未做** | ❌ 未做 |
| 管理器 APK 绑定 | ✅ FinaSU `v1.3.2`（cert `0x02cc`） | ✅ 复用同一套 CI 逻辑 |

> **2026-10-31 首跑前的静态排查已完成并推送（`3d5df57`）**，见 §10。
> **首跑 8 轮的逐轮卡点与修复见 §11** —— 全是 `apply_kit_onemind.py` 里
> 从未在 21 上执行过的注入代码路径（21 Pro 的 kit patch 让它们成为死代码）。

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

---

## 11. 魅族 21 首跑 8 轮的逐轮卡点（2026-10-31）

魅族 21 的**第一次**内核构建，从 run#1 到 run#8 每轮只暴露一个问题。
**全部卡在 `scripts/apply_kit_onemind.py`** —— 这段脚本在 21 Pro 上从来不会执行
（kit patch 先完成注入，脚本走「跳过(已打过)」分支），是**彻头彻尾的死代码**，
21 首跑才第一次被执行。这是「另一机型掩盖缺陷」的典型案例。

| run | commit | 卡点 | 根因 | 教训 |
|---|---|---|---|---|
| #1 | `3d5df57` | `DEVICE_DIR: device_dir not set` | **我自己引入的回归**：把 `${DEVICE_DIR:-MEIZU 21pro/device}` 改成 `${DEVICE_DIR:?...}` 却没给 step 加 `env` | `b33d87d` 补 job 级 env。**本地验证脚本里手动 export 了该变量，掩盖了 CI 下没有的事实** |
| #2 | `b33d87d` | `toybox: not found` (Exit 127)，编译 55 秒即挂 | bazel 磁盘缓存里烤死了**旧仓库名** `MEIZU21PRO_GKI` 的绝对路径；cache key 不含仓库/机型维度 | `4e6822f` key 加 `device_dir` + `github.repository` + `v2`；删掉 4 条污染缓存（约 8.8 GB） |
| #3 | `4e6822f` | **编译成功**、门禁 1/3 通过；门禁 2/3 缺 16 个符号 | 无 kit patch 时 `exit 0`，把 OneMind 注入一起跳过 | `fff1788` 解耦：patch 缺失只跳 patch，注入照跑 |
| #4 | `fff1788` | `sort_folio` 锚点 0 命中 | 脚本假设单行签名，6.1 真实是**两行且含 `struct scan_control *sc`** | `fa99525` 改正则。**21 Pro 永远走不到这段锚点** |
| #5 | `fa99525` | `mm/readahead.c` 编译错（3 error） | hook 插在函数体首行（`max_pages` 声明之前）；且传值而非 `&max_pages` | `6cbcee2` 按 kit 原文插在声明区之后。**传值是语义 bug：预读抑制会完全失效** |
| #6 | `6cbcee2` | `mm/vmscan.c:1048` C90 违规 | `src.index("\n}\n")` 先匹配到**函数内部的 if 块收尾**，声明被插进函数体中间 | `be9cd67` 改花括号配对；同时 `1aacf00` 修 `util.c` 同类问题 |
| #7 | `be9cd67` | `page_alloc.c` 4 个 unused variable | 只插了**声明**、没插**使用**；且 `mz_min/mz_low/mz_high` 是 kit 里不存在的发明变量 | `33626a4` 按 kit 原文补全为「声明 + 水位计算」两段 |
| #8 | `33626a4` | 进行中 | — | — |

### 这一轮真正的教训

1. **死代码是最大的风险源。** 21 Pro 跑通 run#24 全绿，给了「这条流水线是好的」的
   错觉，但 21 走的是一条**从未被执行过**的分支。判断「某路径是否可靠」时，
   要问的是「**这条分支上次真正跑过是什么时候**」，而不是「这个仓库上次是否成功」。

2. **照抄另一份补丁的上下文是不可靠的。** kit patch 的 hunk 依赖 21 Pro 版
   `mm/*.c` 的声明区布局。6.1 的 ACK 树声明区更散，直接照搬位置必然出问题。
   **锚点要落在语义稳定的地方**（如 `max_pages = min(...)` 这种业务语句），
   而不是「函数首行」「第 N 个 `}`」。

3. **验证要对着真实上游源码，不能靠记忆或推测。** 这一轮里有两处判断是错的：
   - 有分析称 6.1 没有 `max_pages` 局部变量、应传 `ractl->size` —— 实际第 581 行
     明明白白有，照做会让预读抑制彻底失效；
   - 有分析称 `sort_folio` 会挂 —— 实际 `bool bypass` 是**声明**，接在声明区里是合法的。
   获取真实源码：`curl -sS 'https://android.googlesource.com/kernel/common/+/\<b>/refs/tags/android14-6.1-2023-10_r1/<path>?format=TEXT' | base64 -d`

4. **试过给注入脚本加「C 结构自检」，失败了，已撤回。** 手写 C 解析器在
   `#if` / 宏 / 复合字面量上会大量误报（50 条全假），**误报比没有更糟**——
   它会在正常构建时制造假失败。要治本，应该用真编译器
   （`clang -fsyntax-only`）而不是自己写正则。

---

## 12. vendor hook（`DECLARE_HOOK`）注入的两条硬性规则

`apply_kit_onemind.py` 往 `include/trace/hooks/*.h` 注入 hook 时，以下两点错了
都会**编译全绿、链接才炸**，而且报错信息（`undefined symbol`）离真实原因很远。

### 12.1 守卫必须「镜像 TRACE_HEADER_MULTI_READ」

```c
/* ❌ 错：第二次包含被挡掉，DEFINE_TRACE 永不生成 */
#ifndef MZ_KIT_P4_MM_H
#define MZ_KIT_P4_MM_H

/* ✅ 对：与上游 _TRACE_HOOK_MM_H 自己的写法一致 */
#if !defined(MZ_KIT_P4_MM_H) || defined(TRACE_HEADER_MULTI_READ)
#define MZ_KIT_P4_MM_H
```

**机制**：`<trace/define_trace.h>` 生成 `__tracepoint_*` / `__traceiter_*` /
`__SCK__tp_func_*` 的方式是**把同一个头文件再包含一次**（此时
`TRACE_HEADER_MULTI_READ` 已定义）。普通 `#ifndef` 会在第二次包含时把整块跳过，
于是只剩声明、没有定义。

**最小验证**（不需要跑 CI）：
```bash
printf '#define HOOK(n) X_##n\n#include "h.h"\n#define TRACE_HEADER_MULTI_READ\n#include "h.h"\n' > t.c
gcc -E -P t.c | grep -c X_our     # 期望 2（声明+定义）；普通守卫只会得到 1
```

**症状对照**（run#8，943 秒编译通过后）：
```
ld.lld: error: undefined symbol: __tracepoint_android_vh_extfrag_for_order
>>> referenced by vendor_hooks.c (___ksymtab_gpl+...)
>>> referenced by mm.h:180 (include/trace/hooks/mm.h:180) mm/vmstat.o:(extfrag_for_order)
```
共 20 个 = 8 hook ×(`__tracepoint_*` + `__traceiter_*`) + SCK 符号。
**注意**：`referenced by mm.h:180` 说明调用点在、声明也在，缺的只是**定义**。

### 12.2 插入位置在 `#endif /* _TRACE_HOOK_*_H */` 之前是对的

上游头文件尾部固定是：
```c
#endif /* _TRACE_HOOK_MM_H */

/* This part must be outside protection */
#include <trace/define_trace.h>
```
把 hook 插在 `#endif` 之前（即保护块内部）是**正确**的——因为第二次包含时
外层 `#if` 会成立。**不要**按「挪到 define_trace.h 之后」去改，那样反而会让
第一次包含看不到它。（run#8 时有两条分析都建议挪到 `#endif` 后面，方向是错的，
是守卫而非位置导致了 undefined。）

### 12.3 不要用 `MZ_DEFINE_VH` 造符号

kit patch 里有个宏：
```c
#define MZ_DEFINE_VH(name) \
	struct tracepoint __tracepoint_##name; \
	EXPORT_SYMBOL_GPL(__tracepoint_##name); \
	int __traceiter_##name(void *__data) { return 0; } \
	EXPORT_SYMBOL_GPL(__traceiter_##name);
```
它能让链接通过、符号齐全、门禁 2 的 CRC 也对得上，但 `__traceiter` 是
**`return 0` 空实现** —— OneMind 的 8 个回调永不触发。**功能全失效但一切检查都是绿的**，
比直接报错危险得多。21 Pro 只对 `binder_alloc` / `binder_ioctl` /
`smartzram_tune_swappiness` 这 3 个「只需符号存在」的 hook 用它。
**8 个 OneMind hook 绝对不能用它。**

---

## 13. run#11 全绿 —— 魅族 21 首个可用内核产物（2026-10-31）

**commit `b7e1a68`，run 41173076472，三个 job 全部 success。**

### 三门禁实测数据（全部首次拿到）

| 门禁 | 结果 | 关键输出 |
|---|---|---|
| 1/3 版本串 | ✅ 通过 | `Linux version 6.1.25-android14-11-maybe-dirty ... clang 17.0.2` |
| 2/3 KMI+CRC | ✅ 通过 | `Stock 6501 / Built 15020 / Missing 16 / Raw CRC 6325 / 6501`，`ALL KMI & MEIZU ONEMIND HOOK EXPORTS VERIFIED 100%` |
| 3/3 kCFI | ✅ **100% MATCH** | `[ALL kCFI CHECKS PASSED 100%!]`，`cfi_unverified.txt` 为空 |

**16 个 OneMind hook 全部补齐**：缺失数从 run#3 的 32 降到 16，且剩余的 16 个
**不含任何 OneMind hook**，全是别的符号：
`__tracepoint_/__traceiter_{binder_alloc, binder_ioctl, smartzram_tune_swappiness,
mm_vmscan_kswapd_wake}`（8 个）+ `mem_cgroup_id_put2, pm_system_irq_wakeup,
reclaim_pages, scsi_execute_cmd_len, shrink_active_list, shrink_inactive_list,
shrink_list, wait_spam_info`（8 个）。

> 这 16 个**不是 OneMind 的**，门禁只 WARN 不阻断。它们分别对应 kit patch 里
> `MZ_DEFINE_VH(...)` 那 3 个 hook（binder_alloc / binder_ioctl / smartzram）与
> `mm_vmscan_kswapd_wake`，以及若干 m2481 专属导出（`wait_spam_info`、
> `pm_system_irq_wakeup` 等）。**若真机出现 Unknown symbol，先查这 16 个。**
> 需要时可用环境变量/严格模式把它们纳入阻断。

### 产物

release `meizu21-finatsu-11` 与 `meizu21-latest`，`dist-meizu21` 分支已建立：

| 文件 | 大小 |
|---|---|
| `android14-6.1.25-2023-10-boot.img` | 100,663,296 |
| `android14-6.1.25-2023-10-AnyKernel3.zip` | 17,516,450 |
| `FinaSU_v1.3.2_33090-release.apk` | 28,129,308 |
| `boot_avb_report.txt` / `check_kcfi.log` / `krcrc_audit.log` / `kit_onemind.log` / `MANAGER_INFO.txt` | — |

`boot.img` 结构自检：`BOOT IMG CHECK PASSED (AVB0 + AVBf 就位, 与原厂可启动结构一致)`，
`kernel_size=34961920`，`payload_end=0x2159000`，模板来自 `魅族21/device/avb_template`。

### 为什么前面 10 轮全挂（一句话总结）

`apply_kit_onemind.py` 在 21 Pro 上**从未执行过**（kit patch 让它永远走「已打过」分支），
是彻头彻尾的死代码。魅族 21 首跑才逐层暴露：锚点写法（#4）→ 插入位置（#5）→
函数边界（#6）→ 只声明不引用（#7）→ tracepoint 守卫挡住 MULTI_READ 第二遍（#8/#9/#10）。

**下一件事**：拿 `boot.img` 跑 `/mnt/c/Step/work/gki-pro/FLASH_SOP.md` §1.4 的 `audit_boot.py`，
全 OK 后**只刷非活动槽**验证开机、Root、管理器识别、SUSFS。

---

## 14. run#14 全绿 —— 补齐 5 个厂商 NEED 符号（2026-10-31）

**commit `344e201`，run 41177176666，三个 job 全部 success。**
这是**应该刷的版本**（run#11 虽全绿但缺 5 个厂商模块真正需要的符号）。

### 与 run#11 的差别

| 项 | run#11 (`b7e1a68`) | run#14 (`344e201`) |
|---|---|---|
| Missing stock symbols | 16 | **6** |
| Raw CRC matches | 6325 / 6501 | **6329 / 6501** |
| 5 个 NEED 符号 | ❌ 缺 | ✅ 已导出 |
| kCFI | 18/18 MATCH | 18/18 MATCH |

**剩余 6 个缺失符号已逐个 grep 确认「不在 NEED 列表」**，不影响开机：
`__tracepoint_/__traceiter_{android_vh_binder_alloc, android_vh_binder_ioctl}`、
`scsi_execute_cmd_len`、`wait_spam_info`。

### 新增 `scripts/apply_kmi_exports.py`

只做 kit patch 里"补导出符号"那部分，不碰 OneMind / kCFI 保活：

| 文件 | 动作 |
|---|---|
| `drivers/android/vendor_hooks.c` | `MZ_DEFINE_VH(android_vh_smartzram_tune_swappiness)`（AOSP 无此 hook，需自造） |
| `drivers/base/power/wakeup.c` | `EXPORT_SYMBOL_GPL(pm_system_irq_wakeup)` |
| `mm/memcontrol.c` | 新增 `mem_cgroup_id_put2()` + 导出 |
| `mm/vmscan.c` | `shrink_{inactive,active}_list` / `shrink_list` 去 static；导出 4 个 + `EXPORT_TRACEPOINT_SYMBOL_GPL(mm_vmscan_kswapd_wake)` |

### ⚠ 三处踩过的坑（**都只在 21 上暴露**）

1. **`mm_vmscan_kswapd_wake` 不能用 `MZ_DEFINE_VH`** —— 它是 AOSP 已有的真实
   `TRACE_EVENT`（`include/trace/events/vmscan.h:66`）。自己再造
   `int __traceiter_...(void *__data)` 会与 `tracepoint.h:415` 的
   `extern int __traceiter_...(void*,int,int,int)` 冲突 → 编译失败。
2. **`EXPORT_TRACEPOINT_SYMBOL_GPL` 必须放在 `CREATE_TRACE_POINTS` 单元** ——
   它展开成 `__tracepoint_##name` / `__traceiter_##name` / `__SCK__tp_func_##name`，
   要求这些标识符在**本 TU** 可见。放 `vendor_hooks.c`（只 include
   `trace/hooks/*.h`）会报 3 个 undeclared。
   **正确位置是 `mm/vmscan.c`**（第 70-71 行就是
   `#define CREATE_TRACE_POINTS` + `#include <trace/events/vmscan.h>`），
   与 21 Pro kit patch 第 679 行一致。
3. **`mem_cgroup_id_put` 在 6.1 是 `static inline`** —— 锚点用
   `void mem_cgroup_id_put(` 能子串命中（`static inline ` 在前），
   新函数 `mem_cgroup_id_put2()` 插在它后面时，`mem_cgroup_id_put_many()`
   （第 5167 行，`static`）在作用域内，可编译。

### 两个仍未解决的问题（**不影响本次刷机**）

1. **`__kcrctab` 二进制锁定未执行**：`[WARN] Could not locate struct kernel_symbol
   for 'module_layout' in Image.` —— `sync_image_kcrctab()` 靠
   `\0module_layout\0` 字符串回溯定位，在本次 Image 上失败。
   本次 CRC 正确是**因为源码层导出的 CRC 本身就对**，不是靠二进制覆写。
   这条兜底路径仍断着，将来若源码 CRC 出现偏差就失去保护。
   真要修，得改用 `__ksymtab` 符号名表 + 段地址硬定位，别再用字符串启发式。
2. **`apply_kit_onemind.py` 的「自检 2: 内核版本串」是空转的**：
   它查 `$KERNEL_ROOT/common/include/generated/utsrelease.h`，但 kleaf 不生成该文件
   （产物在 bazel out/），所以永远 `WARN ... 跳过版本串断言`。
   **kit 步骤那句 `RESULT: OK` 里的版本串断言不可信**，真正的校验只有门禁 1/3
   （门禁 1 本身是对的，已连续两轮通过）。

### 可选加固

`strict_kcfi` 可以置 `true`：run#11 与 run#14 连续两轮 **18/18 MATCH**，
`cfi_unverified.txt` 中"未验证"项都是 N/A 型（`do_shrink_slab` 等本就无 expected 值）。

### 下一步

拿 `meizu21-finatsu-14` 的 `boot.img` 跑 `FLASH_SOP.md` §1.4 的 `audit_boot.py`，
全 OK 后**只刷非活动槽**，验证开机 / Root / 管理器识别 / SUSFS。
