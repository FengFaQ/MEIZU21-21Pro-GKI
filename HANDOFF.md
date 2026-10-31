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
| 工作流 | ✅ 已跑通 **run#24 全绿** | ⏳ **素材刚就位，尚未触发过一次** |
| release 产物 | ✅ `meizu21pro-finatsu-24` / `meizu21pro-latest` | — |
| 真机刷入验证 | ❌ **未做** | ❌ 未做 |
| 管理器 APK 绑定 | ✅ FinaSU `v1.3.2`（cert `0x02cc`） | ✅ 复用同一套 CI 逻辑 |

### 下一步（按顺序）

1. **先跑一次 21 的构建** —— `魅族21/device/` 是 2026-10-30 才补齐的，
   一次都没经过 CI。预期第一次可能仍有小问题（见 §7 的已知风险）。
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
| 1 | **21 一次 CI 都没跑过** | `魅族21/device/` 2026-10-30 才补齐。第一次构建要盯日志 |
| 2 | **21 没有 kit 补丁** | 21 Pro 的 `patches/01-m2481-...patch` 是 m2481 专属，21 走「跳过补丁」分支（已确认是正常路径）。缺的是 OneMind hook + ko 侧 krcrc，**不影响**门禁 2 的二进制 CRC 锁定 |
| 3 | **`stock_Module.symvers` 是复用 21 Pro 的** | 见 `魅族21/device/stock_Module.symvers.README.md`。交叉核对：共同 NEED 4180 个、**CRC 差异 0**、内核需提供的 3036 个 CRC 100% 相同 → 等价。以后拿到 21 自己的直接替换即可 |
| 4 | **AVB vbmeta 是原厂块原样拷贝** | 新内核比原厂大时，vbmeta 里 HASHTREE 的 `image_size` 仍是 `0x2054000`，与实际 payload 末尾（run24 = `0x2159000`）不符。判为可接受：魅族 21 线在完全不注入 AVB 时也能开机，且 kit README 说 ABL 只「读取」footer/descriptor。**但如果实测跳 Fastboot，第一个怀疑对象就是这里** |
| 5 | 真机验证全未做 | 两台机都没刷过自编内核 |
| 6 | kCFI 期望值无实测 | 门禁 3 目前 warn。首刷拿到 `dmesg` 的 `expected type 0x...` 后回填 |
| 7 | SUSFS 校验失败只降级为警告 | `apply.sh` 里的 `\|\| true` 类处理，不阻断 |

---

## 8. 快速上手命令

```powershell
# 看 CI 状态
$env:GH_TOKEN='<PAT>'
Invoke-RestMethod 'https://api.github.com/repos/FengFaQ/MEIZU21-21Pro-GKI/actions/runs?per_page=5' -Headers @{
  'Accept'='application/vnd.github+json';'User-Agent'='dsh';'Authorization'="Bearer $env:GH_TOKEN"} |
  Select-Object -ExpandProperty workflow_runs |
  Select-Object run_number,name,status,conclusion
```

```bash
# 本地校验（不需要网络）
cd /mnt/c/Step/work/gki-rebuild
python3 scripts/avb_template_source.py "魅族21/device/avb_template/source.txt"
python3 scripts/avb_template_source.py "MEIZU 21pro/device/avb_template/source.txt"
```

```powershell
# 产物取回：走 dist 分支，不要用 git show（PowerShell 会 CRLF 转换把 Image 撑大）
$git='C:\Program Files\Git\cmd\git.exe'
& $git -C C:\Step\work\gki-rebuild fetch --depth 1 origin dist
& $git -C C:\Step\work\gki-rebuild worktree add --detach C:\Step\work\dist24 FETCH_HEAD
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
