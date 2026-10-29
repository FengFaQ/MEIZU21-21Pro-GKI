#!/usr/bin/env python3
"""
从原厂 boot.img 生成「AVB 骨架模板」(device/meizu21pro/avb_template/stock_boot_skeleton.img.zlib)。

为什么存在这个脚本
------------------
魅族 21 Pro 的 ABL 即使 bootloader 已解锁，加载 boot 分区时仍然强制校验：
  · 分区末尾最后 64 字节的 AVBf Footer 魔数；
  · 紧随内核 payload 之后的 AVB0 Hash VBMeta 描述符。
裸 mkbootimg 产物（~34MB，无 AVB）刷进去必定回 Fastboot。

repack_avb_boot.py 需要「一个真实原厂 boot.img」作为模板，因为 AVB0 的偏移
(vbmeta_offset) 随 Flyme 版本变化，不能硬编码。但 96MB 的二进制不适合直接入库。

所以：把原厂 boot.img 的「内核 payload 区」清零，其余字节（4096B header、
AVB0 vbmeta 块、末尾 64B AVBf）全部保真，再 zlib 压缩。
  · 所有字节偏移与真实原厂镜像完全一致 -> repack 脚本可原样使用动态解析；
  · 压完只有 ~100KB，可以安心入库。

用法
----
    python3 scripts/make_avb_skeleton.py <原厂 boot.img> <输出 .img.zlib> \
        --device meizu21pro --model M2481 [--flyme M2481_12.6.x]

脚本会**拒绝**非 21 Pro 的镜像（按 source.txt 里的已知黑名单 sha256 拦截），
并自动写出 source.txt 供 CI 硬校验。
"""
import argparse
import hashlib
import os
import struct
import sys
import zlib

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import avb_template_source as srcinfo  # noqa: E402

AVB_MAGIC = b"AVBf"
HDR_MAGIC = b"ANDROID!"
PAGE = 4096


def die(msg):
    print("::error::%s" % msg, file=sys.stderr)
    print("[ERROR] " + msg, file=sys.stderr)
    sys.exit(1)


def main(argv):
    ap = argparse.ArgumentParser()
    ap.add_argument("src", help="原厂 boot.img")
    ap.add_argument("dst", help="输出 .img.zlib")
    ap.add_argument("--device", required=True,
                    help="机型代号，必须是 %s 之一" % ", ".join(srcinfo.devices()))
    ap.add_argument("--model", required=True, help="机型型号，如 M2481 / M2461")
    ap.add_argument("--soc", default="SM8650")
    ap.add_argument("--flyme", default="")
    a = ap.parse_args(argv[1:])

    # ---- 机型硬门禁：标错机型比没有模板更危险 ----
    info = srcinfo.device_info(a.device)
    if info is None:
        die("--device=%s 未注册；已注册机型: %s" % (a.device, ", ".join(srcinfo.devices())))
    if a.model != info["model"]:
        die("--model=%s，但 %s 的型号是 %s" % (a.model, a.device, info["model"]))

    src, dst = a.src, a.dst
    if not os.path.isfile(src):
        die("找不到原厂 boot.img: %s" % src)

    src_sha = srcinfo.sha256_of(src)
    for prefix, why in srcinfo.KNOWN_FOREIGN_SHA256.items():
        if src_sha.startswith(prefix):
            die(
                "输入的镜像 sha256=%s 命中已知的外来/非 boot 镜像：\n  %s\n"
                "  请改用 %s（%s）的原厂 boot.img。"
                % (src_sha, why, a.device, info["model"])
            )
    # ---- 正向白名单：镜像必须就是这台机器的原厂镜像 ----
    if not any(src_sha.startswith(p) for p in info["stock_sha256"]):
        others = []
        for dev, di in srcinfo.DEVICE_REGISTRY.items():
            if dev == a.device:
                continue
            if any(src_sha.startswith(p) for p in di["stock_sha256"]):
                others.append("%s(%s)" % (dev, di["model"]))
        hint = ("\n  这个 sha256 属于: %s —— 机型选错了。" % ", ".join(others)) if others else ""
        die(
            "输入的镜像 sha256=%s 不在 %s（%s）的原厂镜像白名单 %s 内。%s\n"
            "  21 与 21 Pro 的 boot.img 同为 100,663,296 字节、连 vbmeta_offset 都相同，\n"
            "  只有 sha256 能区分机型 —— 拒绝生成可能串机的模板。"
            % (src_sha, a.device, info["model"], list(info["stock_sha256"]), hint)
        )

    raw = bytearray(open(src, "rb").read())
    total = len(raw)
    if raw[:8] != HDR_MAGIC:
        die("不是 boot 镜像（magic 不是 ANDROID!）: %s" % src)
    if total < PAGE + 64:
        die("文件太小，不可能是 boot 镜像")

    kernel_size = struct.unpack_from("<I", raw, 8)[0]
    header_size = struct.unpack_from("<I", raw, 20)[0]
    header_version = struct.unpack_from("<I", raw, 40)[0]

    if raw[-64:-60] != AVB_MAGIC:
        die(
            "镜像末尾 64 字节不是 AVBf Footer —— 这个镜像没有 AVB，不能当模板。\n"
            "  请确认拿的是【原厂 boot.img】而不是我们自己打的裸镜像。"
        )

    # AVBf Footer（大端）：[12:20] original_image_size, [20:28] vbmeta_offset, [28:36] vbmeta_size
    avbf = bytes(raw[-64:])
    orig_image_size = struct.unpack(">Q", avbf[12:20])[0]
    vbmeta_off = struct.unpack(">Q", avbf[20:28])[0]
    vbmeta_size = struct.unpack(">Q", avbf[28:36])[0]

    if not (PAGE <= vbmeta_off < total):
        die("AVBf 里的 vbmeta_offset=0x%X 越界（文件 %d 字节）" % (vbmeta_off, total))
    if raw[vbmeta_off:vbmeta_off + 4] != b"AVB0":
        die("vbmeta_offset=0x%X 处不是 AVB0 魔数 —— 原厂镜像结构异常" % vbmeta_off)

    payload_end = PAGE + ((kernel_size + PAGE - 1) // PAGE) * PAGE

    # ---- 清零内核 payload 区（保留 header 与 AVB0 之间的全部真实字节之外的空白也一并清零）----
    zeroed = 0
    if vbmeta_off > PAGE:
        n = vbmeta_off - PAGE
        raw[PAGE:vbmeta_off] = b"\x00" * n
        zeroed += n
    # ---- 清零 AVB0 之后、AVBf 之前的分区尾巴 ----
    gap_start = vbmeta_off + vbmeta_size
    gap_end = total - 64
    if gap_end > gap_start:
        n = gap_end - gap_start
        raw[gap_start:gap_end] = b"\x00" * n
        zeroed += n

    if len(raw) != total:
        die("内部错误：清零后长度变了")

    blob = zlib.compress(bytes(raw), 9)
    with open(dst, "wb") as f:
        f.write(blob)

    print("源镜像        : %s (%d 字节)" % (src, total))
    print("源 sha256     : %s" % src_sha)
    print("header        : version=%d header_size=%d kernel_size=%d"
          % (header_version, header_size, kernel_size))
    print("payload_end   : 0x%X" % payload_end)
    print("AVBf 解析     : original_image_size=0x%X vbmeta_offset=0x%X vbmeta_size=%d"
          % (orig_image_size, vbmeta_off, vbmeta_size))
    print("已清零        : %d 字节" % zeroed)
    print("输出          : %s (%d 字节, zlib)" % (dst, len(blob)))
    print("skeleton sha256(raw 解压后) : %s"
          % hashlib.sha256(bytes(zlib.decompress(blob))).hexdigest())

    # ---- 写 source.txt（与骨架同目录，供 CI 硬校验机型）----
    sdir = os.path.dirname(os.path.abspath(dst))
    sfile = os.path.join(sdir, "source.txt")
    srcinfo.write_source(
        sfile,
        device=a.device,
        model=a.model,
        source_sha256=src_sha,
        source_size=total,
        vbmeta_offset=vbmeta_off,
        vbmeta_size=vbmeta_size,
        source_file=os.path.basename(src),
        header_version=header_version,
        kernel_size=kernel_size,
        soc=a.soc,
        flyme=a.flyme or None,
    )
    print("来源清单      : %s" % sfile)
    ok, _ = srcinfo.verify_source(srcinfo.read_source(sfile))
    if not ok:
        die("写出的 source.txt 自检失败")
    # ---- 一致性复核：清单里的 sha256 必须就是这次输入的镜像 ----
    if srcinfo.read_source(sfile)["source_sha256"] != src_sha:
        die("source.txt 里的 stock_sha256 与输入镜像不一致")


if __name__ == "__main__":
    main(sys.argv)
