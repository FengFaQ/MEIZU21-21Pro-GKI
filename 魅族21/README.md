# 魅族 21 (M2461) —— 设备素材

本目录只存放 **魅族 21 专属的设备事实**；构建入口工作流位于
`.github/workflows/meizu21.yml`（GitHub 只识别 `.github/workflows/` 下的
workflow 文件，放在本目录里 Actions 扫描不到）。

素材由原厂 **Flyme 12.7.0.0A** 线刷包（`C:\12.7`）提取。

## 内容

```
魅族21/
  device/
    ref_stock.config              原厂 21 内核 config（IKCONFIG 抽出，7430 行）
    stock_Module.symvers          6501 个 KMI 符号 + 32 位 CRC（复用 21 Pro，见下）
    stock_Module.symvers.README.md  复用理由与交叉核对数据
    module_kmi_manifest.txt       361 个模块的符号清单（NEED 4180 / DEF 80241）
    vendor_dlkm_modules.txt       vendor_dlkm 的 modules.load（431 条）
    system_dlkm_modules.txt       system_dlkm 的 modules.load（57 条）
    boot_header.txt               boot v4 头实测值
    so.txt                        内核 release 串 + vermagic
    avb_template/                 AVB 骨架（100,142 B）+ source.txt + sha256
```

> 21 目前**没有** `patches/` 目录。`MEIZU 21pro/device/patches/01-m2481-…patch`
> 是 m2481 专属，21 会走「跳过 kit 补丁」分支 —— 这是预期行为，
> 不影响门禁 2 的 `__kcrctab` 二进制 CRC 锁定。

## 机型事实（实测）

| 项 | 值 |
|---|---|
| 原厂 boot.img sha256 | `8c899eb3b7fcb65eff6bb4afd015f90984ce50d351eceb7eda7ef06bbc4619f5` |
| 分区大小 | 100,663,296 B（96 MiB） |
| boot 头 | v4 / header_size 1584 / kernel_size 33,892,864 / ramdisk_size 0 |
| os_version | `0x1c00017a`（14.0.0 / 2023-10） |
| AVB | vbmeta_offset `0x2054000`，size 2496 |
| 内核 release 串 | `6.1.25-android14-11-maybe-dirty` |
| 模块 | vendor_dlkm 304 + system_dlkm 57 = 361 |
| KMI manifest | NEED 4180 / 内核需提供 3036 |

## AVB 等价性验证（已通过）

```
repack(原厂 Image, 骨架) sha256 == 原厂 boot.img sha256 = 8c899eb3…4619f5
差异字节数 == 0
```

骨架 `sha256 = 439e1b9bc93396a3abb25b8721fffdde35e9591665ebba254c6dfdca62fd1ed3`

## 与 21 Pro 的关系

两者 boot 头**逐字段完全相同**（size / header_version / header_size /
os_version / vbmeta 偏移全一样），唯一判据是原厂镜像 sha256。

KMI 交叉核对结果：

```
共同 NEED 符号                     : 4180
其中 CRC 不一致                    : 0
内核需提供的 3036 个符号 CRC 相同  : 3036 / 3036
魅族 21 独有                        : 0 个
21 Pro 独有                         : 3 个
```

→ 同一 GKI 源，KMI 完全同源。因此 `stock_Module.symvers` 直接复用 21 Pro 的那份。

> ⚠️ AVB 模板必须靠 `avb_template/source.txt` 里的机型名 + sha256 门禁，
> 用错机型会刷死。`scripts/avb_template_source.py` 已注册魅族 21 的两个版本
> （12.6 `12a0a757…` / 12.7 `8c899eb3…`）。

## 相关

- 构建入口：`.github/workflows/meizu21.yml`
- 共享流水线：`.github/workflows/build.yml`
- 产物：release `meizu21-finatsu-<run#>` / `meizu21-latest`，dist 分支 **`dist-meizu21`**
- 交接与踩坑：仓库根目录 `HANDOFF.md` / `MEMORY.md`
