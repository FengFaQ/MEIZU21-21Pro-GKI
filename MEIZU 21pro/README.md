# MEIZU 21 Pro (M2481) —— 设备素材

本目录只存放 **21 Pro 专属的设备事实**；构建入口工作流位于
`.github/workflows/meizu21pro.yml`（GitHub 只识别 `.github/workflows/` 下的
workflow 文件，放在本目录里 Actions 扫描不到）。

## 内容

```
MEIZU 21pro/
  device/
    ref_stock.config              原厂 21 Pro 内核 config（IKCONFIG 抽出，TRIM_UNUSED_KSYMS=y）
    stock_Module.symvers          原厂 6501 个 KMI 符号 + 32 位 CRC（krcrc 唯一基准）
    vmlinux_abi_symbollist.raw    6501 符号白名单
    module_kmi_manifest.txt       模块符号清单（4183 NEED / 361 模块）
    vendor_dlkm_modules.txt       vendor_dlkm 的 modules.load（431 条）
    system_dlkm_modules.txt       system_dlkm 的 modules.load（57 条）
    boot_header.txt               boot v4 头实测值
    so.txt                        内核 release 串
    patches/01-m2481-ko-krcrc-and-onemind.patch   kit 15 文件补丁
    avb_template/                 AVB 骨架（98 KB）+ source.txt + sha256
```

## 机型事实（实测）

| 项 | 值 |
|---|---|
| 原厂 boot.img sha256 | `ebdd3dc2568c76c36f3a5c8d83e80fa9be7a38ab647f3c306d1137397c58ab52` |
| 分区大小 | 100,663,296 B（96 MiB） |
| boot 头 | v4 / header_size 1584 / kernel_size 33,892,864 / ramdisk_size 0 |
| os_version | `0x1c00017a`（14.0.0 / 2023-10） |
| AVB | footer `0x2054000 / 2496`，AVB0 紧随 payload |
| 内核 release 串 | `6.1.25-android14-11-maybe-dirty` |
| 模块 | vendor_dlkm 304 + system_dlkm 57 = 361 |

> ⚠️ 21 与 21 Pro 的 boot 头**逐字段完全相同**，唯一判据是原厂镜像 sha256。
> AVB 模板靠 `avb_template/source.txt` 里的机型名 + sha256 做门禁。

## 相关

- 构建入口：`.github/workflows/meizu21pro.yml`
- 共享流水线：`.github/workflows/build.yml`
- 产物：release `meizu21pro-finatsu-<run#>` / `meizu21pro-latest`，dist 分支 `dist`
