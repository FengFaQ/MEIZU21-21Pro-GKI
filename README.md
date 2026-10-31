# MEIZU 21 / 21 Pro GKI

为 **魅族 21（M2461）** 与 **魅族 21 Pro（M2481）** 构建可刷入的 GKI 内核
（**FinaSU + SUSFS**），只替换 boot 分区，系统仍是原厂 Flyme。

基线固定为 **GKI android14-6.1 / sub_level 25 / os_patch 2023-10**，不跳版本 ——
跳版本会改 `struct zone` / `struct lruvec` 的字段偏移，而魅族闭源模块是按字节偏移
直接访问的，开机即崩。

---

## 目录结构

```
MEIZU 21pro/              ← 魅族 21 Pro (M2481) 专属
  device/                 ← 设备事实：原厂 config / KMI 基准 / AVB 模板 / kit 补丁
魅族21/                    ← 魅族 21 (M2461) 专属
  device/                 ← 设备事实（已由 Flyme 12.7 线刷包提取齐全）
.github/workflows/
  build.yml               ← 共享主流水线（workflow_call，两机型复用）
  meizu21pro.yml          ← 21 Pro 构建入口
  meizu21.yml             ← 魅族 21 构建入口
  emergency-cleanup.yml   ← 紧急下架
scripts/                  ← 两机型共用的脚本（AVB / kCFI / krcrc / kit 补丁……）
config/ data/ security_patch/ zram/ zram_patch/ hmbird_patch.c
HANDOFF.md                ← 交接文档：现在在哪、下一步做什么
MEMORY.md                 ← 记忆文档：为什么这么做、踩过哪些坑
```

---

## 两个入口工作流的差异

| 项 | 魅族 21 | 魅族 21 Pro |
|---|---|---|
| 工作流文件 | `.github/workflows/meizu21.yml` | `.github/workflows/meizu21pro.yml` |
| `device_dir` | `魅族21/device` | `MEIZU 21pro/device` |
| release 标签 | `meizu21-finatsu-<run#>` | `meizu21pro-finatsu-<run#>` |
| latest 标签 | `meizu21-latest` | `meizu21pro-latest` |
| **dist 分支** | **`dist-meizu21`** | **`dist`** |
| concurrency | `meizu21-*` | `meizu21pro-*` |

其余全部复用 `build.yml`。**dist 分支必须分开**，否则两个机型互相覆盖。

---

## 管理器 APK 绑定

**所有网络操作在 CI 内完成，本地零手工步骤。**

```
job1 fetch-manager : gh release download 拉 FinaSU 官方最新 APK
                     -> scripts/finasu_compat/extract_manager_cert.py 提取证书
                     -> 输出 cert_size / cert_hash / apk_name / apk_sha256
job2 build-kernel  : 用该证书编进内核第二信任槽
                     (KSU_EXPECTED_SIZE2 / KSU_EXPECTED_HASH2 / KSU_SECOND_SIGNATURE_RELEASE)
job3 release       : 内核产物 + **这一版编译所用的那份 APK** 一起上传
```

**用户必须安装 release 附带的那份 APK** —— 内核里编的是它的证书哈希，
装别的版本管理器会显示"未安装"。

---

## 三道门禁（编译后强制执行）

| 门禁 | 内容 | 失败处理 |
|---|---|---|
| 1/3 | 内核版本串 `6.1.25-android14-11-maybe-dirty` | **硬失败**（串错 = 模块全不加载，必然开不了机） |
| 2/3 | KMI 审计 + krcrc 二进制 CRC 锁定（对照原厂 6501 符号） | **硬失败**（CRC 错 = 模块拒载） |
| 3/3 | kCFI 类型哈希（10 函数 + 8 个 OneMind traceiter 调用点） | 默认 warn（可开 `strict_kcfi` 转硬失败） |

> 门禁 1 的期望串从 `device/so.txt` 的 `kernel_release=` 读取，不再写死在 `build.yml`
> （`build.yml` 是两机型共享的）。

---

## 文档

| 文档 | 内容 |
|---|---|
| [`HANDOFF.md`](HANDOFF.md) | **交接文档**：当前进度、下一步、已知风险 |
| [`MEMORY.md`](MEMORY.md) | **记忆文档**：铁律、踩坑表、决策理由、放弃的事项 |

---

## 刷入

只替换 boot 分区。`boot.img` 已注入原厂 **AVB0 + AVBf**（魅族 ABL 即使 BL
解锁也会读取这两块）。

**只刷非活动槽，保留可回退槽：**
```bash
adb shell getprop ro.boot.slot_suffix      # 假设 = _a
fastboot flash boot_b <boot.img>
fastboot --set-active=b
# 起不来就切回（10 秒）
fastboot --set-active=a
```

⚠️ 不要两个槽都刷，否则没有可启动的槽。

---

## 触发

只有 `workflow_dispatch`（手动）。
`workflow_dispatch.inputs` 上限 **10 个**（GitHub 硬限制）—— 超限不报语法错，
但 GitHub 会拒绝注册整个 workflow（名字变路径 / dispatch 返回 422 / push 出的 run 是 0 job）。
