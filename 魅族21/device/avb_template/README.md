# AVB 骨架模板 — 魅族 21 (M2461)

来自原厂 **Flyme 12.7.0.0A** 的 `boot.img`，用于 `repack_avb_boot.py`
生成可被魅族 ABL 接受的新 boot 镜像。

## 为什么需要

魅族 ABL 即使 bootloader 已解锁，加载 boot 分区时仍会读取：
- 分区末尾最后 64 字节的 **`AVBf`** footer 魔数
- 紧随内核 payload 之后的 **`AVB0`** VBMeta 描述符

裸 `mkbootimg` 产物（~34MB，无 AVB）刷进去会失败。

## 怎么做的

把原厂 `boot.img` 的**内核 payload 区清零**，其余字节全部保真：
- 前 4096 字节 boot header（v4 / `os_version=0x1c00017a`）
- `AVB0` 区块（`0x2054000` 起，2496 字节）
- 末尾 64 字节 `AVBf` footer

再 zlib 压缩：**100,663,296 → 100,142 字节（98 KB）**。

所有字节偏移与真实原厂镜像完全一致，所以 `repack_avb_boot.py`
可以原样做动态解析（`vbmeta_offset` 随 Flyme 版本变化，不能硬编码）。

## 等价性验证（已通过）

```
原厂 boot.img       sha256 = 8c899eb3b7fcb65e…4619f5
repack(原厂Image, 骨架) sha256 = 8c899eb3b7fcb65e…4619f5
差异字节数 = 0
```

## 文件

| 文件 | 说明 |
|---|---|
| `stock_boot_skeleton.img.zlib` | 骨架（zlib 压缩） |
| `source.txt` | 来源清单：`device=meizu21` / `model=M2461` / `stock_sha256=8c899eb3…` |

## ⚠️ 机型门禁

魅族 21 与 21 Pro 的 boot 头**逐字段完全相同**（size / header_version /
header_size / os_version / vbmeta 偏移都一样），结构字段无法区分机型。

**唯一判据是原厂镜像 sha256**。`scripts/avb_template_source.py` 的
`DEVICE_REGISTRY` 里：

```
meizu21    : 12a0a75750e2fdb0… (Flyme 12.6)
             8c899eb3b7fcb65e… (Flyme 12.7.0.0A)
meizu21pro : ebdd3dc2568c76c3… (Flyme 12.6)
```

CI 会校验 `source.txt` 的 `device` 与 `stock_sha256` 是否匹配，
用错机型模板会 `::error` 硬失败。

## 重做方法

```bash
python3 scripts/make_avb_skeleton.py <原厂boot.img> \
    <输出.img.zlib> --device meizu21 --model M2461 --flyme "Flyme 12.7.0.0A"
```