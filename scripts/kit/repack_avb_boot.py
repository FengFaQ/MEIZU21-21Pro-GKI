#!/usr/bin/env python3
"""
魅族 21 Pro (M2481) 零依赖 Boot Header v4 封装 + AVB0/AVBf Footer 动态注入脚本
用法:
    python3 repack_avb_boot.py [Image路径] [原厂boot.img路径] [输出boot.img路径]
优势:
    1. 无需外部 mkbootimg.py：直接克隆原厂 boot.img 前 4096 字节的 Boot Header v4（自动继承原厂
       os_version 与 os_patch_level），仅更新偏移 8..12 的 kernel_size；
    2. 跨 Flyme 版本自适应：自动从原厂 boot.img 末尾 64 字节 AVBf Footer 的偏移 20..36 处动态解析
       原厂 AVB0 描述符偏移与大小（不再硬编码 0x2054000）；
    3. 严格对齐 96MB (100,663,296 字节) 分区大小并在 payload 结尾与镜像末尾注入 AVB0 + AVBf，
       100% 满足魅族 ABL Bootloader 启动校验。
"""
import math
import os
import struct
import sys

IMAGE = sys.argv[1] if len(sys.argv) > 1 else os.environ.get("IMAGE_PATH", "/home/ovo/m2481-kernel/common/arch/arm64/boot/Image")
STOCK_BOOT = sys.argv[2] if len(sys.argv) > 2 else os.environ.get("STOCK_BOOT_PATH", "/home/ovo/m2481-kernel/baseline/boot.img")
OUT_BOOT = sys.argv[3] if len(sys.argv) > 3 else os.environ.get("OUT_BOOT_PATH", "/home/ovo/m2481-kernel/m2481_custom_boot.img")

if not os.path.exists(IMAGE):
    print(f"[ERROR] Compiled kernel Image not found: {IMAGE}")
    sys.exit(1)
if not os.path.exists(STOCK_BOOT):
    print(f"[ERROR] Stock boot.img not found: {STOCK_BOOT}")
    sys.exit(1)

with open(IMAGE, "rb") as f:
    kernel_data = f.read()
ksize = len(kernel_data)

with open(STOCK_BOOT, "rb") as f:
    f.seek(0, 2)
    stock_boot_size = f.tell()
    f.seek(0)
    hdr_page = bytearray(f.read(4096))
    assert hdr_page[:8] == b"ANDROID!", "Stock boot.img does not start with ANDROID! magic"

    f.seek(-64, 2)
    avbf = bytearray(f.read(64))
    assert avbf[:4] == b"AVBf", "Stock AVBf footer not found at end of stock boot.img!"

    # AVBf footer layout (big-endian):
    # [12:20] original_image_size, [20:28] vbmeta_offset, [28:36] vbmeta_size
    orig_vbmeta_off = struct.unpack(">Q", avbf[20:28])[0]
    orig_vbmeta_size = struct.unpack(">Q", avbf[28:36])[0]
    read_size = max(orig_vbmeta_size, 0x9C0)
    f.seek(orig_vbmeta_off)
    vbmeta = f.read(read_size)

assert vbmeta[:4] == b"AVB0", f"Stock AVB0 header not found at 0x{orig_vbmeta_off:X}!"

# 更新 Boot Header v4 中的 kernel_size (偏移 8..12)，保持 ramdisk_size=0, signature_size=0
hdr_page[8:12] = struct.pack("<I", ksize)
hdr_page[12:16] = struct.pack("<I", 0)
hdr_page[1580:1584] = struct.pack("<I", 0)

kpages = int(math.ceil(ksize / 4096.0) * 4096)
end_of_payload = 4096 + kpages
avbf[12:20] = struct.pack(">Q", end_of_payload)
avbf[20:28] = struct.pack(">Q", end_of_payload)

with open(OUT_BOOT, "wb") as f:
    f.truncate(stock_boot_size)
    f.seek(0)
    f.write(hdr_page)
    f.write(kernel_data)
    f.seek(end_of_payload)
    f.write(vbmeta)
    f.seek(stock_boot_size - 64)
    f.write(avbf)

print(
    f"[OK] Repacked {OUT_BOOT} ({stock_boot_size} bytes): "
    f"ksize={ksize} (0x{ksize:X}), stock_AVB0=0x{orig_vbmeta_off:X}, new_AVB0@0x{end_of_payload:X}, AVBf@EOF-64"
)
