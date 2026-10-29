<div align="center">

# MEIZU21PRO GKI

**魅族 21 Pro (M2481 / SM8650) 专用 GKI 内核构建 · FinaSU + SUSFS**

基线：`android14-6.1.25` / `os_patch 2023-10` / `LTO=none`
内核版本串：`6.1.25-android14-11-maybe-dirty`

</div>

---

## 这是什么

给**魅族 21 Pro（M2481 / SM8650 Pineapple）**构建可刷入的 GKI 内核（FinaSU + SUSFS）。
**只替换 boot 分区**，系统保持原厂 Flyme。

流水线基底是 [`muling0721/GKI_FinaSU`](https://github.com/muling0721/GKI_FinaSU)，
在此基础上叠加 **m2481 kit** 的 21 Pro 适配。

---

## ⚠️ 两条防变砖生死线（必读）

懂这台机器的 ABL 和 CFI 行为，比懂内核编译更重要。**不满足这两条，编出来的内核一定开不了机。**

### 生死线 1：AVB0 + AVBf Footer

魅族 21 Pro **即使 Bootloader 已解锁**，ABL 加载 `boot` 分区时仍然强制校验：

1. 分区**末尾最后 64 字节**的 `AVBf` Footer 魔数；
2. **紧随内核 payload 之后**的 `AVB0` Hash VBMeta 描述符。

直接刷 `mkbootimg` 生成的 ~34 MB 裸镜像（或 `truncate` 零填充到 96 MB）→ **开机必回 Fastboot**。

**本仓库的做法**：`scripts/repack_avb_boot.py`

```
克隆原厂 4096 字节 Boot Header v4
  → 写入新 Image
  → payload 末尾注入 AVB0（偏移从模板 AVBf 动态解析，不硬编码）
  → EOF-64 注入 AVBf
```

模板是 `device/meizu21pro/avb_template/stock_boot_skeleton.img.zlib`
——原厂 boot.img 的**稀疏骨架**（96 MB 布局保真，payload 区清零，zlib 压缩后 98 KB）。

**等价性已验证**：用原厂 Image + 骨架模板打包，产出与 21 Pro 原厂 `boot.img`
**逐字节相同**（`sha256 = ebdd3dc2568c76c3…58ab52`，diff = 0）。
打包路径与原厂产线等价。

> ⚠️ 骨架必须来自 **21 Pro** 的原厂镜像。魅族 21（普通版）的 boot.img 与之
> **同为 100,663,296 字节，连 `vbmeta_offset`/`vbmeta_size` 都相同**，
> 结构字段完全分不出机型 —— 唯一判据是原厂镜像 sha256。
> `source.txt` + `scripts/avb_template_source.py` 做了机型硬门禁。

### 生死线 2：kCFI 类型哈希

`CONFIG_CFI_CLANG=y` 下，函数的 kCFI Type Hash 存在入口前 4 字节。
若某个函数在内核树内**没有任何"取地址"操作**，Clang 会把这 4 字节优化掉。
厂商模块通过 `kallsyms` 找到它再**间接调用** → 立刻
`Kernel panic - not syncing: CFI failure` → **卡第一屏无限重启**。

典型受害者是 `mz_kshrink_slabd.ko` 间接调用的 `shrink_slab()`。

**本仓库的做法**：
- `mm/vmscan.c` 保留 `static unsigned long (*const shrink_slab_kcfi_ref)(...) __used = shrink_slab;`
- 编译后跑 `scripts/check_kcfi.py` 门禁：校验 **10 个直接函数 + 8 个 OneMind `__traceiter` 调用点**，
  任一 MISMATCH 直接拒绝打包。

---

## 管理器 APK 绑定

**所有网络操作都在 CI 里做**（拉 APK、提取证书），本地不需要任何手动下载或计算。

```
job1 fetch-manager   gh release download 拉 FinaSU 官方最新 APK
                     → scripts/finasu_compat/extract_manager_cert.py 提取证书
                     → 输出 cert_size / cert_hash / apk_name / apk_sha256
job2 build-kernel    用 cert_size/cert_hash 编进内核第二信任槽
                     (KSU_EXPECTED_SIZE2 / KSU_EXPECTED_HASH2 / KSU_SECOND_SECOND_SIGNATURE_RELEASE=1)
job3 release         内核产物 + **这一版 GKI 编译时所用的那份 APK** 一起上传
```

**为什么不自建 APK**：
1. 不需要管理 keystore / secrets；
2. 证书哈希与上传的 APK **天然同源**（同一个 job 算、同一个 artifact 传），不可能错配；
3. FinaSU 升级**零改代码**（`finasu_ref` 留空 = 自动取 `releases/latest`）。

> ⚠️ **用户必须安装本 release 附带的那份 APK**。内核里编的是它的证书哈希，
> 装别的版本会导致管理器识别不到内核。

---

## 目录结构

```
.github/workflows/
  meizu21pro.yml          入口：fetch-manager → build-kernel → release
  build.yml               主流水线（官方基底 + 21 Pro 适配）
  emergency-cleanup.yml   紧急下架（release + latest + dist 三处齐清）
config/                   官方配置
data/android14/6.1.json   版本数据线
scripts/
  repack_avb_boot.py      ★ AVB0+AVBf 注入（命名参数 --image/--template/--out）
  make_avb_skeleton.py    从原厂 boot.img 生成骨架模板（含机型黑名单）
  avb_template_source.py  source.txt 机型硬校验
  apply_kit_onemind.py    OneMind 8 Hook + kCFI 保活 + kit 原文一致性自检
  check_kcfi.py           kCFI 门禁（10 函数 + 8 traceiter）
  check_krcrc_symvers.py  KMI 审计 + 二进制 __kcrctab CRC 锁定
  finasu_compat/          管理器证书提取（官方）
  susfs_fixes/            SUSFS 补丁自归一化（官方）
  kit/                    m2481 kit 原版脚本（留档对照）
device/meizu21pro/
  ref_stock.config             原厂 config（IKCONFIG 抽出，对齐基准）
  stock_Module.symvers         原厂 6501 个 KMI 符号 + 32 位 CRC
  vmlinux_abi_symbollist.raw   原厂 6501 符号白名单（配 TRIM_UNUSED_KSYMS=y）
  patches/01-m2481-*.patch     kit 补丁（15 文件：krcrc 放行+补OneMind+kCFI）
  avb_template/                AVB 骨架 + sha256 + source.txt
  boot_header.txt              boot v4 头实测值
  module_kmi_manifest.txt      4183 NEED / 30137 DEF / 361 模块
  vendor_dlkm_modules.txt      vendor_dlkm 模块清单
  system_dlkm_modules.txt      system_dlkm 模块清单
security_patch/                CVE 修复链（官方）
zram/ zram_patch/              ZRAM 算法（官方）
```

---

## 关于 `CONFIG_TRIM_UNUSED_KSYMS`

**原厂是 `=y`**，不是关闭。

逐行 diff `ref_stock.config`（原厂 IKCONFIG 抽出）与 m2481 kit 的
`stock_kernel_clean.config`，5889 个符号里**只有一对差异**：

| | 原厂 `ref_stock.config` | kit `stock_kernel_clean.config` |
|---|---|---|
| `CONFIG_TRIM_UNUSED_KSYMS` | **`=y`** | `# ... is not set` |
| `CONFIG_UNUSED_KSYMS_WHITELIST` | 指向 `abi_symbollist.raw` | **被删掉** |

kit 是为了让配置"能直接编译"才关掉它（原有 whitelist 指向魅族内网 Jenkins 路径，不删编不过）。
**本仓库以原厂值为准：`=y`**，whitelist 指向仓库内的 `vmlinux_abi_symbollist.raw`
（路径在 CI 运行时拼绝对路径 —— Kconfig 里相对路径是相对内核源码根解析的）。

---

## 构建

### 触发

只有 **`workflow_dispatch`（手动）**。没有 push 触发器，这是实测结论：
可复用 workflow 的 job 级 `if` 不能用 `success()`/`failure()`，push 上下文展开时
无法解析跨 job outputs —— 历史上 push 触发的 run 全部 0 job 直接失败。

### 参数（6 个）

| 参数 | 说明 |
|---|---|
| `ksu_variant` | KernelSU 变体（当前仅 FinaSU） |
| `ksu_mode` | 关闭 / 禁用SUSFS / 禁用KSU |
| `finasu_ref` | FinaSU 版本，**留空 = 自动取上游 latest** |
| `lto_mode` | none（与原厂一致）/ thin |
| `boot_sign` | 不签名（克隆原厂 AVB，推荐）/ 签名 |
| `enable_susfs` | 是否启用 SUSFS |

> ⚠️ `workflow_dispatch.inputs` 的 GitHub 上限是 **10 个**。超限不会报 YAML 语法错，
> 但 GitHub 会**拒绝注册整个 workflow**：列表里名字显示成路径、dispatch 永远 422、
> push 出来的 run 是 0 job。当前 6 个，留 4 个余量。
> `workflow_call.inputs`（build.yml）不受此限。

### 产物

- release `meizu21pro-finatsu-<run#>`（prerelease）
- 别名 `meizu21pro-latest`
- `dist` 分支留档
- **每个 release 都附带本次编译所用的管理器 APK**

---

## 刷入

> ⚠️ 21 Pro 是 A/B 设备。**务必先备份当前槽位**，任何时候能切回去。

```bash
# 0. 备份（已 root）
adb shell "getprop ro.boot.slot_suffix"          # 例如 _a
adb shell su -c "dd if=/dev/block/by-name/boot_a of=/sdcard/boot_stock_a.img"
adb pull /sdcard/boot_stock_a.img

# 1. 刷非活动槽（假设当前是 _a）
fastboot flash boot_b boot.img
fastboot --set-active=b

# 2. 回退
fastboot --set-active=a
```

**不要** `fastboot erase`，**不要**动 `vendor_boot` / `init_boot`。

---

## 开机后验证

```bash
adb shell uname -r                                              # 6.1.25-android14-11-maybe-dirty
adb shell "lsmod | wc -l"                                       # 期望 ~471
adb shell "cat /proc/mz_scheduler/vip_task/enabled"              # 期望 0x1c
adb shell "cat /proc/mz_lock/enabled"                            # 期望 0xe
adb shell "cat /proc/mz_watermark_opt/low_high_watermark_ratio"  # 期望 3
adb shell "cat /proc/mz_memory/mm_protect/protect_size"          # 期望 614400
adb shell "su -c id"                                             # 期望 uid=0
```

### 故障速查

| 症状 | 最可能原因 | 验证 |
|---|---|---|
| 进 Fastboot 界面 | AVB0/AVBf 缺失、`os_version` 错、分区大小错 | 检查 boot.img 是否 100,663,296 B 且末尾有 `AVBf` |
| 卡第一屏 2~3 秒重启 | kCFI 哈希不匹配 | 看 `dmesg` 里 `CFI failure` |
| 循环重启但能到 logo | 模块加载失败（CRC / vermagic） | `dmesg` 里 `disagrees about version` / `Unknown symbol` |

---

## 紧急下架

`emergency-cleanup.yml`（仅仓库 owner 可执行）：一次清掉
正式 release + `latest` 别名 + `dist` 分支，并在 Summary 输出救急步骤。

---

## 致谢

- 流水线基底：[muling0721/GKI_FinaSU](https://github.com/muling0721/GKI_FinaSU)
- 管理器和 SUSFS：[LyraVoid/FinaSU](https://github.com/LyraVoid/FinaSU) / [susfs4ksu](https://gitlab.com/simonpunk/susfs4ksu)
- 21 Pro 适配与两大防变砖结论：m2481 kit

## 许可

见 [LICENSE](LICENSE)。本仓库**不包含**原厂内核镜像、原厂 `.ko` 模块本体或任何魅族私钥，
只包含文本形态的设备事实、已压缩的 AVB 骨架模板与符号基准表。
