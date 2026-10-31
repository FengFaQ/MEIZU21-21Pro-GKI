# 记忆文档 · MEIZU 21 / 21 Pro GKI

> **这份文档只记「为什么」和「踩过什么坑」。**
> 想知道「现在在哪、下一步做什么」，看 `HANDOFF.md`。
>
> 目标读者：下一个改这个仓库的人 / 下一个会话的 AI。
> 尤其注意：**同一类错误我犯过不止一次**，见 §9。

最后更新：2026-10-31

---

## 1. 铁律（违反了必然开不了机，且现场很难看出来）

### 1.1 GKI 版本不许跳
固定在 `android14-6.1` / `sub_level 25` / `os_patch 2023-10`。
跳版本会改 `struct zone` / `struct lruvec` 的字段偏移。魅族闭源模块
（`mz_kshrink_slabd`、`smartswap_zram` 等）是按**字节偏移直接访问**这些结构的，
偏移一变就是野指针 → 开机即崩。

### 1.2 内核 release 串必须逐字符等于原厂
`6.1.25-android14-11-maybe-dirty`

| 位置 | 布局 | 受 release 串影响 |
|---|---|---|
| `vendor_boot` ramdisk | `/lib/modules/*.ko` 扁平，绝对路径 | ❌ |
| `vendor_dlkm` | `/lib/modules/*.ko` 扁平 + `modules.load` | ❌ |
| `system_dlkm` | `/system_dlkm/lib/modules/<release>/` | ✅ |

串不对 → `system_dlkm` 的模块目录找不到 → 第一阶段 init FATAL → 重启。
**表现是「卡第一屏 2~3 秒后重启」**，和 kCFI 崩溃的表现几乎一样，
必须用 `dmesg` 区分（`version magic ... should be` vs `CFI failure`）。

### 1.3 LTO 必须是 none
`thin` 会改变 CFI 哈希分布。原厂和可启动参考都是 `none`。

### 1.4 boot.img 必须带 AVB0 + AVBf
魅族 ABL 即使 BL 已解锁也强制校验这两块。缺了 → **直接跳回 Fastboot**。
- `AVBf`（footer）在 `EOF - 64`
- `AVB0`（vbmeta）紧接在 payload 末尾

裸 `mkbootimg` 产物、或 `truncate` 零填充到 96 MB 都不行。
必须用 `scripts/repack_avb_boot.py` 从原厂骨架克隆布局。

---

## 2. AVB 打包：骨架法

### 做法
1. `scripts/make_avb_skeleton.py` 读原厂 boot.img，把 payload 区清零，
   zlib 压缩 → 得到 ~100 KB 的骨架（偏移/AVB0/AVBf 全部保真）。
2. 编译时 `repack_avb_boot.py --image <新Image> --template <骨架> --out boot.img`
   把新 Image 写进 payload 区，再把骨架里的 AVB0 / AVBf 原样放回去。

### 验证方式（**必须做，这是唯一可信的证明**）
```
repack(原厂 Image, 骨架) 的 sha256  ==  原厂 boot.img 的 sha256
```
两个机型都已验证：差异字节数 **0**。改动 `repack_avb_boot.py` 后必须重跑这个验证。

### ⚠️ 21 和 21 Pro 的 boot 头逐字段完全相同
`size 100663296` / `header_version 4` / `header_size 1584` /
`os_version 0x1c00017a` / `vbmeta 0x2054000 + 2496` —— **一个字段都分不出来**。
**唯一判据是原厂镜像 sha256**。

所以模板必须带 `source.txt`，CI 里做双向门禁：
- `avb_template_source.py` 查 `DEVICE_REGISTRY` 白名单（含 sha256 前缀）
- `build.yml` 再比一次 `source.txt` 的 `device=` 和 `device_dir` 推算出的机型名

**拿错机型的 AVB 去签 = 刷死。**

### 已知未解决：vbmeta 的 image_size 不更新
`AVB0` 是原厂块**原样拷贝**，里面 HASHTREE 的 `image_size` 还是 `0x2054000`，
而新内核的 payload 末尾可能到了 `0x2159000`（run#24 实测）。

判为可接受的依据：
1. 魅族 21 线在**完全不注入 AVB**（`truncate` 零填充）的情况下也能开机；
2. kit 的 README 说 ABL 只「读取」footer/descriptor，没有描述完整校验链。

**但如果实测跳 Fastboot，第一个怀疑对象就是这里。** 真修需要重签 vbmeta，
风险是换 key → 可能反而更糟，暂不动。

---

## 3. kCFI（本项目的头号杀手）

### 机制
`CONFIG_CFI_CLANG=y` 时，厂商模块在**间接调用点**写死了它期望的 kcfi 类型哈希：

```
ldr  w16,[x8,#-4]
movz/movk w17,#hash
cmp  w16,w17
b.eq +8
brk  #0x8228        ← CFI failure
blr  x8
```

内核侧该函数**入口前 4 字节**必须正好是那个哈希，否则 `brk` → panic → 看门狗复位。

**表现固定是「卡死 2~3 秒后重启」，绝不退化成「手感异常」。**
如果用户报的是体验问题（触摸双击、振动弱），**不要往 kCFI 上想**。

### 已修的两颗雷

| 符号 | 期望哈希 | 消费者 | 根因 |
|---|---|---|---|
| `shrink_slab` | `0xc3b4beac` | `mz_kshrink_slabd` | 上游 `mm/vmscan.c` 里是 `static`，clang 不生成哈希 → 去掉 `static` + 补前置原型 |
| `mem_cgroup_id_remove` | `0x7180a14e` | `smartswap_zram` | 同上 |

### 采期望值的正确方法（**三条都是踩过坑才总结出来的**）

1. **只认 Image 里的内联 4 字节。**
   不要用 `nm | grep __kcfi_typeid_<name>` —— 具名符号只在跨编译单元引用时才生成，
   `kfree` 有内联哈希却**没有**具名符号。
2. **不要按字面 4 字节直接搜哈希。** 期望值是 `movz/movk` 拼出来的，
   而且常见值会大量偶然命中。
3. 正确抽法：扫 `.ko` 的 `.text`，找 `brk #0x8228`（`0xd4304500`）
   **前面 40 字节内**的 `movz/movk` 常量。

### 工具链版本陷阱
`llvm-objdump` 的输出文本格式随版本变化。用旧版（llvm-14）会让 `check_kcfi.py`
的 `movk w17` 正则匹配失败 → 误报 `could not find movk w17 pair` → 被当成 kCFI 不符。
**用错版本的工具链比直接失败更危险。** 所以 `build.yml` 里按版本号降序
（`sort -Vr`）取第一个工具齐全的 clang，系统 clang 要求 ≥ 17。

> 另外：流水线开头「清理磁盘空间」会 `remove_packages: *llvm*`，
> 系统 llvm 已被删，所以**不要**用 `find /` 兜底找工具 —— 必然为空、白耗 15~60 秒。

---

## 4. 模块 CRC（`__kcrctab` 二进制锁定）

设备模块带 `__versions`（`CONFIG_MODVERSIONS=y`），内核导出符号的 CRC 必须逐个相等，
否则 `disagrees about version` → 模块拒载 → 黑屏/卡 logo。

**做法（B 方案）**：`scripts/check_krcrc_symvers.py` 直接改 `Image` 里
`__kcrctab` 的值（只写 4 字节/项，不动代码），覆写成原厂 `stock_Module.symvers` 的精确 CRC。

### 为什么不用 `TRIM_UNUSED_KSYMS=y`
run#23 实测：`=y` + 白名单 → kleaf 的
```
Checking for kmi_symbol_list_strict_mode ... failed
Symbols missing from the ksymtab: ___update_load_avg / __bforget / ...
```

**语义冲突**：
- `TRIM_UNUSED_KSYMS=y` 裁掉「没被引用」的导出符号
- `kmi_symbol_list_strict_mode` 要求 `__ksymtab` **完全等于** KMI 清单

AOSP 的 `abi_gki_aarch64` 清单里有大量我们没引用的符号，被 trim 掉后 strict_mode
就判定「缺失」。

`sed -i '/kmi_symbol_list_strict_mode/d' BUILD.bazel` **不管用** —— kleaf 的 target
是宏展开的，删了仍会跑（run#23 日志证实）。

**决策：退回 `=n`。** 代价只是「导出符号比原厂多」，而模块加载只检查它 NEED 的符号
**存在且 CRC 匹配**，不关心内核多导出了什么（21 线 run#26 已实证）。
真正的 CRC 保障在门禁 2 的二进制覆写。

---

## 5. 两个机型的 KMI 关系（交叉核对结论）

```
meizu21     NEED 4180 / 内核需提供 3036
21 Pro      NEED 4183 / 内核需提供 3036

共同 NEED 符号            : 4180
其中 CRC 不一致           : 0
内核需提供的 3036 个 CRC 100% 相同 : 3036 / 3036
meizu21 独有              : 0 个
21 Pro 独有               : 3 个
```

**结论：同一 GKI 源，KMI 完全同源。**

因此 `魅族21/device/stock_Module.symvers` 直接复用 21 Pro 的那份（6501 行），
来源写在 `stock_Module.symvers.README.md` 里。

### 为什么不从 21 的 Image 里反推
原厂 `Module.symvers` 只随魅族构建树提供，不在 ROM 里。
从 `Image` 的 `__ksymtab` / `__kcrctab` 反推需要精确匹配厂商的 PREL32 相对偏移布局，
**两次尝试都失败了**（锚点向前扫描 / 全镜像扫描），说明 6.1 GKI 的表布局与代码假设不同。
与其继续逆向，不如用已验证等价的数据 —— 风险更低、结论可审计。

---

## 6. 流水线踩坑（按遇到顺序，**别重踩**）

| # | 现象 | 根因 | 修法 |
|---|---|---|---|
| 1 | run 里只有 release skipped，0 job | push 展开可复用 workflow 无法解析跨 job outputs | 去掉 push，只留 `workflow_dispatch` |
| 2 | **push 出的 run 也是 0 job** | `workflow_dispatch.inputs` **超 10 个**，GitHub 拒绝注册整个 workflow（名字变路径 / dispatch 422） | build.yml 精简到 30 input，入口只留 6 个 |
| 3 | kleaf fragment 校验失败 | `extra_config` 块内混入中文裸注释 | 注释移到块外 |
| 4 | `zipalign failed / unknown flag -P` | apt 的 zipalign 太旧，不支持 16KB 对齐 | 探测 SDK build-tools，用支持 `-P` 的那个 |
| 5 | APK 证书解析成 `0x0024` 而不是 `0x0341` | 解析 APK Signing Block 时 offset 错（从 `signed_data+4` 读到了 digests 内部） | 先整段跳过 digests 再读 certificates；写了合成 APK 单测 |
| 6 | `设备目录不存在: `（空串） | heredoc 里的 `DEVICE_DIR=${DEVICE_DIR:-...}` 只写进文件，当前 shell 没赋值 | heredoc 后显式赋值并 `export` |
| 7 | `no FinaSU checkout at .../android14-6.1-25/FinaSU/kernel` | `FINASU_DIR` 写成 `$KERNEL_ROOT/FinaSU`，实际在 `$GITHUB_WORKSPACE/FinaSU` | 改路径 |
| 8 | SUSFS dry-run 报 `fs/proc/base.c Hunk #1 FAILED` | **时机错误**：在 `apply.sh` 做上下文调整之前 dry-run | dry-run 改可选（`SUSFS_CHECK_DRYRUN=1`），判据交给 `apply.sh` |
| 9 | 断言 6 个符号全缺失 | 用了 AOSP GKI 符号名，但魅族用高通 tree，`ufs_qcom`/`clk-qcom`/`sched-walt` 不在其 Kconfig 体系 | 撤回，改以原厂 config 为准 |
| 10 | 仍报 `CONFIG_MMC_BLOCK` 缺失 | **原厂 config ≠ gki_defconfig 基线**：该符号原厂 `=y`，基线里没有 | 只对基线已声明的符号严格判定 |
| 11 | `ERROR: Skipping 'KSU_EXPECTED_SIZE2=0x0342'` | **kleaf 不支持 `bazel build <target> -- VAR=val`**，`--` 后被当 target | 直接写进 FinaSU 的 `kernel/Kbuild` 头部 |
| 12 | `ld.lld: error: undefined symbol: fake_state` | FinaSU 某些版本缺这一个符号（`fake_status` 等都有），上游由 10 号补丁定义 | `scripts/finasu/patch_fake_state.py` 只补这一个定义 |
| 13 | `device/meizu21pro/manager_cert.txt 不存在`（历史） | release job 只 download-artifact，没 checkout | release job 补 `actions/checkout` |
| 14 | `autoksyms.h:6:2: error: expected identifier or '('` | `vmlinux_abi_symbollist.raw` 是 **CRLF**（6501 个 CR），内核 `trim_ksyms` 不 strip `\r` | 转 LF + CI 里加 CR 硬检查 |
| 15 | `lto=thin` 但 `lto_mode=none` | `LTO_FLAG="--lto=thin"` 硬编码，只对 6.12 分支读过 input | 两条路径（bazel / legacy）都改读 `inputs.lto_mode` |
| 16 | `redefinition of 'trace_android_vh_extfrag_for_order'` | kit 补丁**和**我们的脚本都插了 8 个 `DECLARE_HOOK` | 改成「缺了才补」语义（complete-if-missing） |
| 17 | `mm_inline.h:250: misleading indentation` + `else if` 语法错 | 我们的 `apply_mm_inline` 把插入点落在 `if` / `else if` 之间 | 改成**只校验、绝不插入** |
| 18 | `git apply` 报 `scripts/setlocalversion: does not match index` 且**全部 13 个文件回滚** | `git apply` 是**原子**的，一个文件失败全回滚 | `--reject` + 过滤掉会冲突的文件 + 幂等脚本兜底 |
| 19 | 补丁失败但 CI 绿灯 | `if cmd \| tee` 检查的是 `tee` 的退出码 | 用 `PIPESTATUS[0]` |
| 20 | 设备上 super 几何读出全 0 | `dd bs=1 skip=N` 在 toybox 下不可靠；且 `super` 前 69632 B 全是 0 —— **魅族 super 不是标准 LP** | 放弃设备端提取，改用线刷包 |
| 21 | `extract.erofs.exe -x <dir>` 出 0 个文件 | 用法错 | 正确是 `-i <img> -x -o <outdir>` |
| 22 | KMI manifest 的 DEF 数 = 0 | `.symtab`/`.strtab` 的 section type 是 2/3，不是 PROGBITS(1) | 收集器保留 `{PROGBITS, SYMTAB, STRTAB, DYNSYM}` |
| 23 | LP magic 常量写错 | `0x616C4467 = 1634485351`（不是 1635140711）；`0x414C5030 = 1095520304`（不是 1095528496） | 改正 |
| 24 | 全局 git 被污染，所有 git 操作失效 | 遗留的 `core.sshCommand = C:/uwuaosp/tools/git-ssh.cmd` 和 `url.git@ssh.github.com:.insteadof=https://github.com/` | 两个都 `--unset`；备份在 `C:\Users\Administrator\.gitconfig.bak-*` |

---

## 7. 2026-10-31 这次修的三处「共享流水线里写死 21 Pro」

`build.yml` 是两机型共享的（`workflow_call`），但里面残留了三处 m2481 专属假设。
魅族 21 第一次构建会直接撞上：

| 位置 | 原来 | 现在 |
|---|---|---|
| 门禁 1/3 版本串 | `WANT="6.1.25-android14-11-maybe-dirty"` 写死 | 从 `$DEVICE_DIR/so.txt` 的 `kernel_release=` 读，缺失兜底 `inputs.version` |
| Boot 镜像 AVB 机型门禁 | `[ "$TPL_DEVICE" = "meizu21pro" ]` | 从 `device_dir` 推算：`MEIZU 21pro/device`→`meizu21pro`，`魅族21/device`→`meizu21` |
| kit 补丁缺失 | 报 `::warning` 后 `exit 0`（没说明这是正常路径） | 补注释说明 21 走跳过分支是**预期行为**，不影响门禁 2 |

另外补了 `魅族21/device/avb_template/stock_boot_skeleton.sha256`
（原缺失 → Boot 镜像步骤会硬失败 `缺少 AVB 模板文件`）。

**教训**：改 `build.yml` 时，任何出现 `meizu21pro` / `6.1.25-android14-11-maybe-dirty`
的地方都要问一句「这是 21 Pro 专属的吗？」。能用 `DEVICE_DIR` 推的就别写死。

---

## 8. 环境事实（本机）

| 项 | 值 |
|---|---|
| 工作副本 | `C:\Step\work\gki-rebuild`（branch `main`） |
| 远端 | `git@github.com:FengFaQ/MEIZU21-21Pro-GKI.git`（GitHub 不允许空格，所以仓库名不是「MEIZU 21/21pro GKI」） |
| 分析脚本 / 日志 | `C:\Step\work\gki-pro` |
| PAT | `C:\Users\Administrator\.ssh\GITHUB_CREDENTIALS.md`（已验证 `admin=True push=True`） |
| SSH key 口令 | `162636` |
| 21 Pro 12.7 线刷包 | `C:\12.7` |
| 魅族 21 12.6 线刷包 | `C:\Step\flyme12.6` |
| 21 Pro 原厂 boot 备份 | `C:\Step\work\gki-pro\backup\boot_stock_21pro.img` |

### 镜像速查（**刷错就砖**）

| 机型 | 文件 | sha256 前缀 | 大小 |
|---|---|---|---|
| 21 Pro | `boot.img` | `ebdd3dc2568c76c3…` | 100,663,296 |
| 魅族 21（12.6） | `boot.img` | `12a0a75750e2fdb0…` | 100,663,296 |
| 魅族 21（12.7） | `boot.img` | `8c899eb3b7fcb65e…` | 100,663,296 |

> 从 `Downloads\21pro\boot.img` 拿到的 21 Pro 原厂镜像是
> `ebdd3dc2…`；`C:\12.7` 里的 `boot.img` 是 `8c899eb3…`，**那是魅族 21 的**。
> 曾经因为「12.7 包在 C:\12.7 就以为是 21 Pro」差点用错 —— **一律信 sha256，别信目录名。**

---

## 9. 我反复犯的两个方法论错误（**最容易重犯，重点看**）

### 9.1 拿一个数据源的结论去断言另一个数据源
坑 9、10 都是这个：
- 先套 AOSP 的 GKI 符号名去要求魅族的内核（魅族用高通 tree，符号根本不在）
- 又拿原厂 config 去要求 `gki_defconfig` 基线（原厂 `=y` 而基线没有该符号）

**判断前必须先确认两者是否同一体系。**
同样的错误也发生在「12.7 包放在 `C:\12.7` 所以是 21 Pro」上 —— 实际是魅族 21。

### 9.2 修错对象
坑 8 之前想放宽 `STRICT_DRY_RUN`。做了 `patch` 退出码对照实验
（offset 不匹配 → exit 0；真失败 → exit 1）才发现严格判定本来就是对的，
**问题在时机**，不在严格性。

**先做最小对照实验定位，再改代码。**

---

## 10. 明确放弃 / 不做的事

| 项 | 原因 |
|---|---|
| **休眠 / suspend 相关工具** | 用户明确：**放弃这个措施，完成不了它**。不要再提。 |
| 从 `Image` 反推 `__ksymtab` | 两次失败，性价比太低（见 §5） |
| 设备端提取 `super` 分区 | 魅族 super 不是标准 LP，前 69632 B 全 0（见坑 20） |
| 从 `vendor_boot` 解 21 Pro 的模块 | ramdisk 载荷被混淆（9,706,817 B），解不出来，**别再试** |
| `TRIM_UNUSED_KSYMS=y` | 与 `kmi_symbol_list_strict_mode` 语义冲突（见 §4） |
| 重签 vbmeta | 换 key 风险可能比现状更大（见 §2） |
| 两槽一起刷 | 刷坏了没有可启动的槽，直接变砖 |

---

## 11. 待验证的假设（**未证实，别当事实用**）

1. 「魅族 ABL 只读取 AVB footer/descriptor，不做完整校验」——
   来自 kit README 的描述，**没有实测**。
2. 「21 与 21 Pro 的 kCFI 期望哈希完全相同」——
   基于同一 GKI 源推断，**没有实测**（21 从未开机过）。
3. 「21 复用 21 Pro 的 `stock_Module.symvers` 够用」——
   交叉核对 CRC 100% 相同，但**没在真机上验证过模块能否全部加载**。
