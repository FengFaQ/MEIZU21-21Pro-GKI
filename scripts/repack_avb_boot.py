#!/usr/bin/env python3
"""
魅族 21 Pro (M2481 / SM8650) boot.img 打包：克隆原厂 Boot Header v4 + 注入 AVB0 + AVBf Footer。

为什么不能用 mkbootimg + truncate
--------------------------------
魅族 21 Pro 即使 Bootloader 已解锁，ABL 在加载 boot 分区时依然强制校验：
  1. 分区末尾最后 64 字节的 AVBf Footer 魔数；
  2. 紧随内核 payload 之后的 AVB0 Hash VBMeta 描述符。
直接刷 mkbootimg 生成的 ~34MB 裸镜像（或 truncate 零填充到 96MB）→ 开机必回 Fastboot。
（实测原厂 init_boot.img: AVBf 存在, vbmeta_offset=2138112, vbmeta_size=832, magic@vboff=AVB0）

做法（移植自 m2481kit/scripts/repack_avb_boot.py，并加上硬校验）
--------------------------------------------------------------
  1. 克隆原厂镜像前 4096 字节的 Boot Header v4（自动继承原厂 os_version / os_patch_level /
     header_size，避免我们再手写这些字段写错）；
  2. 从模板末尾 64 字节 AVBf Footer 的 [20:28] / [28:36] 动态解析原厂 AVB0 偏移与大小
     —— 不硬编码 0x2054000，Flyme 升版本也能自适应；
  3. 只更新 header 偏移 [8:12] 的 kernel_size，ramdisk_size=0、signature_size=0；
  4. 保持分区总大小与原厂一致（96 MiB），在 payload 末尾写 AVB0、在 EOF-64 写 AVBf。

模板可以是「AVB 骨架」：payload 区已清零、但 header / AVB0 / AVBf 在原始偏移上保真。
（见 scripts/make_avb_skeleton.py 与 device/meizu21pro/avb_template/README.md）

用法
----
    repack_avb_boot.py --image Image --template <boot.img|.img.zlib> --out boot.img
可选：
    --expect-size 100663296      校验输出总大小（默认按模板大小）
    --report <path>              写机器可读的报告（key=value）

退出码：0 成功；非 0 失败（带 ::error:: 便于 GitHub Actions 直接标注）
"""
import argparse
import hashlib
import math
import os
import struct
import sys
import zlib

PAGE = 4096
HDR_MAGIC = b"ANDROID!"
AVBF_MAGIC = b"AVBf"
AVB0_MAGIC = b"AVB0"
# AVBf Footer 至少要覆盖到 [28:36] 的 vbmeta_size 字段
AVBF_MIN_LEN = 64


def err(msg):
    print("::error::%s" % msg, file=sys.stderr)
    print("[ERROR] %s" % msg, file=sys.stderr)
    sys.exit(1)


def load_template(path):
    """读模板。支持 .zlib（骨架）与原始 .img。返回 (bytes, 描述)。"""
    if not os.path.isfile(path):
        err("AVB 模板不存在: %s —— 缺模板时绝不能静默跳过 AVB 注入" % path)
    raw = open(path, "rb").read()
    if raw[:8] == HDR_MAGIC:
        return raw, "raw(%d 字节)" % len(raw)
    # zlib 流的首字节恒为 0x78（CMF=0x78: deflate, 32K window）
    if raw[:1] == b"\x78":
        try:
            data = zlib.decompress(raw)
            return data, "zlib(%d -> %d 字节)" % (len(raw), len(data))
        except zlib.error as e:
            err("模板 %s 是 zlib 但解压失败: %s" % (path, e))
    err("模板 %s 既不是 ANDROID! 镜像也不是 zlib 载荷（前 8 字节: %r）" % (path, raw[:8]))


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--image", required=True, help="编译出的 arm64 Image（未压缩）")
    ap.add_argument("--template", required=True, help="原厂 boot.img 或骨架 .img.zlib")
    ap.add_argument("--out", required=True, help="输出 boot.img")
    ap.add_argument("--expect-size", type=int, default=0, help="期望输出总字节数")
    ap.add_argument("--report", default="", help="写 key=value 报告文件")
    a = ap.parse_args()

    if not os.path.isfile(a.image):
        err("找不到内核 Image: %s" % a.image)
    kernel = open(a.image, "rb").read()
    ksize = len(kernel)
    if ksize < 1024 * 1024:
        err("Image 只有 %d 字节，明显不是内核镜像" % ksize)

    tpl, tpl_desc = load_template(a.template)
    total = len(tpl)
    if total < PAGE + AVBF_MIN_LEN:
        err("模板太小: %d 字节" % total)

    hdr = bytearray(tpl[:PAGE])
    if hdr[:8] != HDR_MAGIC:
        err("模板前 8 字节不是 ANDROID!: %r" % bytes(hdr[:8]))
    header_version = struct.unpack_from("<I", hdr, 40)[0]
    header_size = struct.unpack_from("<I", hdr, 20)[0]
    if header_version != 4:
        err("模板 header_version=%d，本脚本只支持 v4（21 Pro 原厂是 v4）" % header_version)

    avbf = bytearray(tpl[total - AVBF_MIN_LEN:])
    if avbf[:4] != AVBF_MAGIC:
        err(
            "模板末尾 64 字节不是 AVBf Footer —— 这不是原厂影像或骨架没做对。\n"
            "  %s 末尾 64 字节: %r" % (a.template, bytes(avbf[:16]))
        )

    orig_image_size = struct.unpack(">Q", avbf[12:20])[0]
    vbmeta_off = struct.unpack(">Q", avbf[20:28])[0]
    vbmeta_size = struct.unpack(">Q", avbf[28:36])[0]

    if not (PAGE <= vbmeta_off <= total):
        err("AVBf 中 vbmeta_offset=0x%X 越界（模板 %d 字节）" % (vbmeta_off, total))
    if vbmeta_size < 4 or vbmeta_off + vbmeta_size > total:
        err("AVBf 中 vbmeta_size=%d 不合理（offset=0x%X, 模板 %d 字节）"
            % (vbmeta_size, vbmeta_off, total))
    vbmeta = tpl[vbmeta_off:vbmeta_off + vbmeta_size]
    if vbmeta[:4] != AVB0_MAGIC:
        err("vbmeta_offset=0x%X 处不是 AVB0（实际 %r）" % (vbmeta_off, vbmeta[:4]))

    # ---- 重写 header：只改 kernel_size；ramdisk/signature 归零 ----
    hdr[8:12] = struct.pack("<I", ksize)     # kernel_size
    hdr[12:16] = struct.pack("<I", 0)        # ramdisk_size
    hdr[1580:1584] = struct.pack("<I", 0)    # signature_size (v4 header 固定偏移)

    kpages = int(math.ceil(ksize / float(PAGE))) * PAGE
    payload_end = PAGE + kpages
    if payload_end + len(vbmeta) > total - AVBF_MIN_LEN:
        err(
            "内核 Image 太大：payload 结束于 0x%X，再加 AVB0(%d) 会压到 AVBf 上"
            "（模板总长 %d）。原厂 kernel_size=%d，我们用 %d。"
            % (payload_end, len(vbmeta), total, orig_image_size - PAGE, ksize)
        )

    # ---- AVBf 改写：original_image_size 与 vbmeta_offset 都指向新 payload 结束处 ----
    avbf[12:20] = struct.pack(">Q", payload_end)
    avbf[20:28] = struct.pack(">Q", payload_end)

    out = bytearray(total)               # 全零，保证 payload 之后到 AVB0 之间是干净的
    out[0:PAGE] = hdr
    out[PAGE:PAGE + ksize] = kernel
    out[payload_end:payload_end + len(vbmeta)] = vbmeta
    out[total - AVBF_MIN_LEN:] = avbf

    with open(a.out, "wb") as f:
        f.write(out)

    actual_size = len(out)
    if a.expect_size and actual_size != a.expect_size:
        err("输出大小 %d != 期望 %d" % (actual_size, a.expect_size))

    # ---- 自检：回读并核对关键结构，而不是只信任内存里的变量 ----
    chk = open(a.out, "rb").read()
    problems = []
    if chk[:8] != HDR_MAGIC:
        problems.append("magic")
    if struct.unpack_from("<I", chk, 8)[0] != ksize:
        problems.append("kernel_size")
    if chk[-64:-60] != AVBF_MAGIC:
        problems.append("AVBf footer")
    if chk[payload_end:payload_end + 4] != AVB0_MAGIC:
        problems.append("AVB0 @payload_end")
    if problems:
        err("打包自检失败: %s" % ", ".join(problems))

    sha = hashlib.sha256(chk).hexdigest()
    lines = [
        "template=%s" % a.template,
        "template_desc=%s" % tpl_desc,
        "output=%s" % a.out,
        "output_size=%d" % actual_size,
        "kernel_size=%d" % ksize,
        "header_version=%d" % header_version,
        "header_size=%d" % header_size,
        "os_version=0x%08x" % struct.unpack_from("<I", chk, 16)[0],
        "payload_end=0x%X" % payload_end,
        "stock_vbmeta_offset=0x%X" % vbmeta_off,
        "stock_vbmeta_size=%d" % vbmeta_size,
        "stock_original_image_size=0x%X" % orig_image_size,
        "new_magic_at_payload_end=%s" % chk[payload_end:payload_end + 4].decode("ascii", "replace"),
        "avbf_at_eof_minus_64=%s" % chk[-64:-60].decode("ascii", "replace"),
        "sha256=%s" % sha,
    ]
    print("[OK] Repacked %s (%d 字节)" % (a.out, actual_size))
    print("     kernel_size   = %d (0x%X)" % (ksize, ksize))
    print("     原厂 AVB0     @0x%X size=%d" % (vbmeta_off, vbmeta_size))
    print("     新  AVB0      @0x%X (紧随 payload)" % payload_end)
    print("     AVBf Footer   @EOF-64")
    print("     header 继承原厂 os_version=0x%08x header_size=%d"
          % (struct.unpack_from("<I", chk, 16)[0], header_size))
    print("     sha256        = %s" % sha)
    if a.report:
        with open(a.report, "w") as f:
            f.write("\n".join(lines) + "\n")
        print("     报告           -> %s" % a.report)


if __name__ == "__main__":
    main()
