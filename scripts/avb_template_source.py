#!/usr/bin/env python3
"""
AVB 模板来源清单（source.txt）读写 + 硬校验。

背景（血泪教训）
----------------
2026-10-27：我们曾误把「魅族 21（普通版）Flyme 12.6」的 boot.img 当成
「魅族 21 Pro（M2481）」的模板生成了骨架。两者**同为 100,663,296 字节**，
尺寸校验完全无法发现错误：

    boot.img        flyme12.6(21)             21pro(M2481)
    尺寸            100,663,296 B             100,663,296 B   <-- 一样!
    sha256          12a0a75750e2fdb0…         ebdd3dc2568c76c3…  <-- 只有这里不同
    vbmeta_offset   0x2054000                 0x2054000       <-- 也一样!
    init_boot       vbmeta_offset=0x20A000    (未知)
    vendor_dlkm     26,615,808 B             26,591,232 B
    system_dlkm     3,571,712 B              3,604,480 B

注意：**连 vbmeta_offset/vbmeta_size 都相同**，所以结构字段同样分不出机型。
唯一可靠的判据是**原厂镜像的 sha256**。

AVB0 是对内核 payload 计算 hash 再签名的。拿错机型的 AVB0 去签：
  · AVB 校验若生效 -> hash 不匹配 -> 照样回 Fastboot；
  · 就算碰巧能过，也是运气，不是正确。

所以：**模板必须携带可追溯的来源信息，且 CI 必须硬校验「设备名 == meizu21pro」。**
标错机型的模板，比没有模板更危险 —— 没有模板会硬失败，标错的会静默产出坏镜像。

source.txt 格式（key=value，一行一个，'#' 开头为注释）
----------------------------------------------------
    device=meizu21pro
    source=21pro 原厂 boot.img
    stock_size=100663296
    stock_sha256=ebdd3dc2...
    header_version=4
    kernel_size=33892864
    avb_vbmeta_offset=0x2054000
    avb_vbmeta_size=2496

为兼容早期写法，本模块同时接受别名：
    source_sha256 / stock_sha256        （二选一）
    source_size   / stock_size          （二选一）
    vbmeta_offset / avb_vbmeta_offset   （二选一）
    vbmeta_size   / avb_vbmeta_size     （二选一）
"""
import hashlib
import io
import os
import sys

# --------------------------------------------------------------------------- #
# 机型注册表（多机型支持）
#
# 历史: 本模块最初只服务 21 Pro，EXPECT_DEVICE / EXPECT_MODEL 是模块级常量，
# KNOWN_FOREIGN_SHA256 硬编码「21 的镜像是外来镜像」。接入第二机型（魅族 21 /
# M2461）后改成注册表：
#   · 每个 device 声明自己的 model、允许的 stock_sha256 白名单前缀；
#   · verify_source() 默认「按清单自己写的 device 反查注册表」——
#     机型写错、或 sha256 不属于该机型，一律 FAIL；
#   · 想强制锁死某机型时传 expect_device=（make_avb_skeleton 仍要求显式声明）。
#
# 为什么不能只删掉门禁：21 与 21 Pro 的 boot.img 同为 100,663,296 字节，
# 连 vbmeta_offset/vbmeta_size 都相同，结构字段分不出机型，sha256 是唯一判据。
# 白名单 + 黑名单双向卡，比原来的单向黑名单更严。
# --------------------------------------------------------------------------- #
EXPECT_DEVICE = "meizu21pro"   # 兼容旧调用方：默认机型
EXPECT_MODEL = "M2481"

DEVICE_REGISTRY = {
    "meizu21pro": {
        "model": "M2481",
        "soc": "SM8650",
        "stock_sha256": {
            "ebdd3dc2568c76c3": "21 Pro 原厂 boot.img (Flyme 12.6 线刷包)",
        },
    },
    "meizu21": {
        "model": "M2461",
        "soc": "SM8650",
        "stock_sha256": {
            "12a0a75750e2fdb0": "魅族 21(普通版) 原厂 boot.img (Flyme 12.6)",
            "8c899eb3b7fcb65e": "魅族 21(普通版) 原厂 boot.img (Flyme 12.7.0.0A)",
        },
    },
}

# 已知不属于任何【已知机型】的镜像（前缀匹配）：真正的垃圾/自制镜像才进这里。
# 曾经这里列的是「21 的 boot.img」，接入 21 后它已经是合法机型，故移入注册表白名单。
KNOWN_FOREIGN_SHA256 = {
    "6dc0e923df50c4e9": "魅族 21（普通版）init_boot.img —— 不是 boot.img，不能当 boot 模板",
}
# 说明: 各机型的原厂 boot.img 已移入 DEVICE_REGISTRY 白名单, 不再列为外来。


def devices():
    return sorted(DEVICE_REGISTRY)


def device_info(device):
    return DEVICE_REGISTRY.get(device)


def expected_model(device):
    info = DEVICE_REGISTRY.get(device)
    return info["model"] if info else None

# 键别名：（规范名，可接受的别名列表）
KEY_ALIASES = {
    "device": ("device",),
    "model": ("model", "device_model"),
    "source_file": ("source_file", "source"),
    "source_sha256": ("source_sha256", "stock_sha256"),
    "source_size": ("source_size", "stock_size"),
    "vbmeta_offset": ("vbmeta_offset", "avb_vbmeta_offset"),
    "vbmeta_size": ("vbmeta_size", "avb_vbmeta_size"),
}
REQUIRED = ("device", "model", "source_sha256", "source_size",
            "vbmeta_offset", "vbmeta_size")

# （旧的单机型 KNOWN_FOREIGN_SHA256 已移除：各机型原厂镜像现由
#   DEVICE_REGISTRY 的白名单正向匹配，见文件上半部分。）


def sha256_of(path):
    h = hashlib.sha256()
    with open(path, "rb") as f:
        for chunk in iter(lambda: f.read(1 << 20), b""):
            h.update(chunk)
    return h.hexdigest()


def _normalize(raw):
    """把别名键折叠成规范名。"""
    out = dict(raw)
    for canon, aliases in KEY_ALIASES.items():
        if canon in out:
            continue
        for al in aliases:
            if al in raw and raw[al]:
                out[canon] = raw[al]
                break
    return out


def read_source(path):
    """读 source.txt，返回规范化 dict（值都是去空白的 str）。"""
    if not os.path.isfile(path):
        raise ValueError(
            "缺少模板来源清单 %s。\n"
            "  没有来源清单就无法证明模板属于 %s，按纪律必须拒绝打包。\n"
            "  生成方式: scripts/make_avb_skeleton.py 会自动写出 source.txt。"
            % (path, EXPECT_DEVICE)
        )
    raw = {}
    with io.open(path, "r", encoding="utf-8") as f:
        for ln, line in enumerate(f, 1):
            line = line.strip()
            if not line or line.startswith("#"):
                continue
            if "=" not in line:
                raise ValueError("%s:%d 格式错误（应为 key=value）: %r" % (path, ln, line))
            k, v = line.split("=", 1)
            raw[k.strip()] = v.strip()
    d = _normalize(raw)
    missing = [k for k in REQUIRED if not d.get(k)]
    if missing:
        raise ValueError("%s 缺少字段: %s" % (path, ", ".join(missing)))
    return d


def verify_source(src, verbose=True, expect_device=None):
    """校验来源清单。

    expect_device 为空时「按清单自己写的 device」校验（多机型）；
    传了就再额外锁死是该机型（单机型 CI 步骤用）。
    """
    msgs = []
    ok = True

    def chk(cond, good, bad):
        nonlocal ok
        if cond:
            msgs.append("  OK   " + good)
        else:
            ok = False
            msgs.append("  FAIL " + bad)

    dev = (src.get("device") or "").strip()
    info = DEVICE_REGISTRY.get(dev)
    if expect_device:
        chk(dev == expect_device,
            "device=%s" % dev,
            "device=%s，期望 %s（模板不是给这台机器的！）" % (dev, expect_device))
        info = DEVICE_REGISTRY.get(expect_device) or info
    else:
        chk(info is not None,
            "device=%s 在机型注册表内" % dev,
            "device=%s 不在机型注册表 %s 内 —— 无法证明模板属于哪台机器"
            % (dev, devices()))

    # model：有则必须对；缺省只告警
    model = src.get("model") or ""
    want_model = info["model"] if info else None
    if model and want_model:
        chk(model == want_model,
            "model=%s" % model,
            "model=%s，期望 %s（模板不是给这台机器的！）" % (model, want_model))
    elif not model:
        msgs.append("  WARN 清单未记录 model，仅按 device 与 sha256 判定")

    # sha256：必须落在该机型白名单里（比单向黑名单更严）
    digest = (src["source_sha256"] or "").lower()
    foreign = None
    for prefix, why in KNOWN_FOREIGN_SHA256.items():
        if digest.startswith(prefix):
            foreign = why
            break
    chk(foreign is None,
        "stock_sha256 不在「已知外来镜像」名单中",
        "stock_sha256=%s 命中已知的外来镜像: %s" % (src["source_sha256"], foreign))
    if info:
        hit = None
        for prefix in info["stock_sha256"]:
            if digest.startswith(prefix):
                hit = prefix
                break
        chk(hit is not None,
            "stock_sha256 命中 %s 的原厂镜像白名单 (%s…)" % (dev, (hit or "")[:16]),
            "stock_sha256=%s 不在 %s 的白名单 %s 内 —— 机型/镜像不匹配"
            % (src["source_sha256"], dev, list(info["stock_sha256"])))

    try:
        voff = int(str(src["vbmeta_offset"]), 0)
        vsz = int(str(src["vbmeta_size"]), 0)
        ssz = int(str(src["source_size"]), 0)
        chk(voff > 0 and vsz > 0 and ssz > 0,
            "avb_vbmeta_offset=0x%X avb_vbmeta_size=%d stock_size=%d" % (voff, vsz, ssz),
            "vbmeta_offset/vbmeta_size/stock_size 有非法值")
    except ValueError as e:
        ok = False
        msgs.append("  FAIL 数值字段无法解析: %s" % e)

    if verbose:
        for m in msgs:
            print(m)
    return ok, msgs


def write_source(path, device, model, source_sha256, source_size,
                 vbmeta_offset, vbmeta_size, source_file="boot.img",
                 header_version=4, kernel_size=None, soc=None, flyme=None,
                 created_by="scripts/make_avb_skeleton.py"):
    """按 lead 指定字段名写 source.txt。"""
    info = DEVICE_REGISTRY.get(device) or {}
    wl = info.get("stock_sha256") or {}
    lines = [
        "# AVB 模板来源清单 —— 由 %s 自动生成" % created_by,
        "# CI 硬门禁: device 必须在机型注册表内; stock_sha256 必须命中该机型的原厂镜像白名单",
        "#   注册表: %s" % ", ".join(devices()),
        "#   本机型白名单: %s" % (", ".join(p + "…" for p in wl) or "<未注册>"),
        "",
        "device=%s" % device,
        "model=%s" % model,
    ]
    if soc:
        lines.append("soc=%s" % soc)
    if flyme:
        lines.append("flyme=%s" % flyme)
    lines += [
        "source=%s" % source_file,
        "stock_size=%d" % source_size,
        "stock_sha256=%s" % source_sha256,
        "header_version=%d" % header_version,
    ]
    if kernel_size is not None:
        lines.append("kernel_size=%d" % kernel_size)
    lines += [
        "avb_vbmeta_offset=0x%X" % vbmeta_offset,
        "avb_vbmeta_size=%d" % vbmeta_size,
        "created_by=%s" % created_by,
    ]
    with io.open(path, "w", encoding="utf-8", newline="\n") as f:
        f.write("\n".join(lines) + "\n")


if __name__ == "__main__":
    if len(sys.argv) != 2:
        print("用法: avb_template_source.py <source.txt>", file=sys.stderr)
        sys.exit(2)
    try:
        s = read_source(sys.argv[1])
    except ValueError as e:
        print("::error::%s" % e)
        sys.exit(1)
    ok, _ = verify_source(s)
    sys.exit(0 if ok else 1)
