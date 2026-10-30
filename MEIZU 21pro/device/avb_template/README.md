# AVB 模板：魅族 21 Pro boot 分区

## 这个目录为什么存在

魅族 21 Pro（M2481 / SM8650）**即使 Bootloader 已解锁**，ABL 在加载 `boot` 分区时
依然强制校验两样东西：

1. 分区**末尾最后 64 字节**的 `AVBf` Footer 魔数；
2. **紧随内核 payload 之后**的 `AVB0` Hash VBMeta 描述符。

直接用 `mkbootimg.py` 产出的 ~34 MB 裸镜像（或 `truncate` 零填充到 96 MB）刷入
`boot_a/boot_b`，**开机必定直接跳回 Fastboot**。这就是 run 14/15「编译全绿但刷机不开机」
的决定性原因之一。

`scripts/repack_avb_boot.py` 负责把我们的 `Image` 装进一个**结构与原厂完全一致**的
96 MiB boot 镜像：克隆原厂 4096 字节 Boot Header v4 → 写入新 Image → 在 payload 末尾
注入 AVB0 → 在 EOF-64 注入 AVBf。

它需要**一份原厂 boot.img 作为模板**，因为 `AVB0` 的偏移（`vbmeta_offset`）随 Flyme 版本
变化，不能硬编码。

## 为什么存的是「骨架」而不是原厂 boot.img

96 MB 的二进制不适合入库。所以这里存的是**稀疏化**的原厂镜像：

- 保持 **100,663,296 字节**完整布局，所有字节偏移与真实原厂镜像**完全一致**；
- 只把**内核 payload 区清零**（那 33 MB 本来就该被我们的新 Image 覆盖）；
- 用 zlib 压缩，体积从 96 MB 降到约 **98 KB**。

因此 `repack_avb_boot.py` 可以**原样动态解析** AVBf 里的 `vbmeta_offset/vbmeta_size`，
不依赖任何硬编码。

> **保真性已验证**：用「骨架」和用「真·原厂 boot.img」当模板，产出的 boot.img
> **逐字节完全相同**（diff = 0，sha256 一致）。

## 文件

| 文件 | 说明 |
|---|---|
| `stock_boot_skeleton.img.zlib` | AVB 骨架，解压后 100,663,296 字节 |
| `stock_boot_skeleton.sha256` | 上面那个文件的 sha256（**压缩态**），CI 下载后校验 |

### 骨架的关键字段（实测）

```
解压后大小        = 100663296      (96 MiB, = boot 分区大小)
magic             = ANDROID!
header_version    = 4
header_size       = 1584
os_version        = 0x1c00017a     (Android 14.0.0 / 2023-10)
原厂 kernel_size  = 33892864
payload_end       = 0x2054000
AVBf.vbmeta_offset= 0x2054000
AVBf.vbmeta_size  = 2496
magic@vbmeta_off  = AVB0
AVBf @ EOF-64     = AVBf
```

来源：Flyme `12.6.0.0A` 线刷包的 `boot.img`。

## 什么时候需要重新生成

- Flyme 大版本升级（`os_version` / `AVB0` 大小变化）；
- 换机型或换分区表。

生成方式（需要一份真实原厂 boot.img）：

```bash
python3 scripts/make_avb_skeleton.py <原厂 boot.img> \
    device/meizu21pro/avb_template/stock_boot_skeleton.img.zlib
```

脚本会打印解压后骨架的 sha256；把压缩态文件的 sha256 写进 `.sha256` 清单。

## CI 里怎么用

`build.yml` 的「构建 Boot 镜像 (21 Pro / AVB 注入)」步骤：

```bash
SHA=$(sha256sum device/meizu21pro/avb_template/stock_boot_skeleton.img.zlib | cut -d' ' -f1)
[ "$SHA" = "$(cat device/meizu21pro/avb_template/stock_boot_skeleton.sha256)" ] || exit 1
python3 scripts/repack_avb_boot.py \
    --image Image \
    --template device/meizu21pro/avb_template/stock_boot_skeleton.img.zlib \
    --out boot.img --expect-size 100663296
```

**模板缺失时步骤必须硬失败**（`::error`），不允许静默回退到 `truncate` 零填充 ——
那正是之前踩过的坑。
