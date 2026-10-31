# 魅族 21 (M2461) 设备事实

本目录由原厂 **Flyme 12.7.0.0A** 线刷包（`C:\12.7`）提取，供
`.github/workflows/meizu21.yml` 使用。

## 文件

| 文件 | 来源 | 用途 |
|---|---|---|
| `ref_stock.config` | boot.img 内嵌 IKCONFIG | 编译后 config 对齐基准 |
| `module_kmi_manifest.txt` | vendor_dlkm 304 + system_dlkm 57 个 .ko | KMI 审计 + krcrc CRC 对齐 |
| `avb_template/` | 原厂 boot.img 生成的 AVB 骨架（100 KB） | boot.img 注入 AVB0+AVBf |
| `boot_header.txt` | boot.img 实测 | boot 头字段门禁 |
| `so.txt` | 实测 | 内核 release 串 / vermagic |
| `vendor_dlkm_modules.txt` | modules.load | 模块加载清单 |
| `system_dlkm_modules.txt` | modules.load | 模块加载清单 |

## 关键数值（实测）

| 项 | 值 |
|---|---|
| 原厂 boot.img sha256 | `8c899eb3b7fcb65eff6bb4afd015f90984ce50d351eceb7eda7ef06bbc4619f5` |
| 分区大小 | 100,663,296 B（96 MiB） |
| boot 头 | v4 / header_size 1584 / kernel_size 33,892,864 / ramdisk_size 0 |
| os_version | `0x1c00017a`（14.0.0 / 2023-10） |
| AVB | vbmeta_offset `0x2054000`，size 2496 |
| 内核 release 串 | `6.1.25-android14-11-maybe-dirty` |
| vendor_dlkm | 304 个 .ko |
| system_dlkm | 57 个 .ko |
| KMI manifest | NEED 4180 / 内核需提供 3036 |

## AVB 等价性验证（已通过）

```
repack(原厂Image, 骨架) sha256 == 原厂 boot.img sha256
差异字节数 == 0
```

## 与 21 Pro 的关系

两者 boot 头**逐字段完全相同**，唯一判据是原厂镜像 sha256。
KMI 交叉核对：共同符号 4180 个、**CRC 差异 0**，内核需提供的
3036 个符号 CRC **100%% 相同** → 同一 GKI 源。

> ⚠️ AVB 模板必须靠 `avb_template/source.txt` 里的机型名 + sha256 门禁，
> 用错机型会刷死。`scripts/avb_template_source.py` 已注册 21 的两个版本
> （12.6 `12a0a757…` / 12.7 `8c899eb3…`）。
