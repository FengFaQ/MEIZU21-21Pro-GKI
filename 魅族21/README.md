# 魅族 21 (M2461) —— 设备素材待补

本目录将存放魅族 21 的 GKI 构建素材，结构对齐 `MEIZU 21pro/device/`：

```
魅族21/
  device/
    ref_stock.config              ← 原厂 21 的内核 config（IKCONFIG 抽出）
    stock_Module.symvers          ← 原厂 21 的 KMI 符号 + CRC 基准
    module_kmi_manifest.txt       ← 21 的模块符号清单（472 模块 / 4891 NEED）
    vmlinux_abi_symbollist.raw    ← 符号白名单
    patches/                      ← 21 专用 kernel 补丁（可选）
    avb_template/                 ← 21 原厂 boot 的 AVB 骨架 + source.txt
    boot_header.txt / so.txt      ← boot 头实测值 / 内核串
```

## 21 与 21 Pro 的关键差异（已实测）

| 项 | 魅族 21 (M2461) | 魅族 21 Pro (M2481) |
|---|---|---|
| 原厂 boot.img sha256 | `12a0a75750e2fdb0a82308a1f0bafd33decbd6934e2ed732d27b3183d2007e31` | `ebdd3dc2568c76c36f3a5c8d83e80fa9be7a38ab647f3c306d1137397c58ab52` |
| 模块数 | 472（含 vendor_boot 里 335 个） | 361（vendor_dlkm 304 + system_dlkm 57） |
| KMI NEED | 4891 | 4183 |
| boot 头 | v4 / 96 MiB / `os_version=0x1c00017a` | **逐字段完全相同** |
| 原厂 vbmeta | `0x2054000 / 2496` | **相同** |

> ⚠️ **两个机型的 boot 头没有任何可区分字段，唯一判据是原厂镜像 sha256。**
> AVB 模板必须靠 `source.txt` 里的 sha256 + 机型名做门禁，用错机型会刷死。

## 来源

魅族 21 的原厂镜像在 `C:\Step\flyme12.6\`（Flyme 12.6 线刷包，已确认是 **21 普通版**，
不是 21 Pro）。需要的文件：

- `boot.img`（96 MB，sha256 `12a0a757…`）→ 生成 AVB 骨架
- `vendor_dlkm.img`（25.38 MB）+ `system_dlkm.img`（3.41 MB）→ 抽 KMI 清单
- `vbmeta.img`、`dtbo.img`（备用）

## 待办

1. 从 `flyme12.6` 提取上述素材，按上表结构放入 `魅族21/device/`
2. 用 `scripts/make_avb_skeleton.py` 生成 21 的 AVB 骨架，并验证
   `repack(原厂Image, 骨架) == 原厂 boot.img` 逐字节相同
3. 把 `.github/workflows/meizu21.yml` 的 `device_dir` 指向 `魅族21/device`
   （工作流已就绪，只等素材）
4. 21 线历史经验见 `C:\Step\uwuaosp\记忆\09_GKI内核线.md` 与 `10_触摸问题诊断总结.md`
   （触摸"双击/长按"问题当时未定案，线索在 vendor 侧 Flyme 游戏模式）