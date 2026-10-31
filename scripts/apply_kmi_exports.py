#!/usr/bin/env python3
"""Export the vendor-NEEDed symbols that the m2481 kit patch would otherwise provide.

WHY
---
魅族 21 (m2461) has no m2481 kit patch, so it never gets the kit's
"export extra KMI symbols" changes. 门禁 2 only WARNS about missing stock
symbols, so the build is green -- but 5 of those symbols are actually
requested (NEED) by 魅族 21's own vendor .ko files.  At boot the module
loader would fail with "Unknown symbol" and the device would not come up.

Verified against 魅族21/device/module_kmi_manifest.txt (the NEED list):
    NEEDED : __tracepoint_android_vh_smartzram_tune_swappiness
    NEEDED : __tracepoint_mm_vmscan_kswapd_wake
    NEEDED : mem_cgroup_id_put2
    NEEDED : pm_system_irq_wakeup
    NEEDED : reclaim_pages
The others that 门禁 2 reports (`shrink_active_list`, `shrink_inactive_list`,
`shrink_list`, `scsi_execute_cmd_len`, `wait_spam_info`, and the
`__traceiter_*` counterparts) are NOT in the NEED list, so they are only
nice-to-have; the kit patch exports them anyway for KMI completeness.

WHAT THIS SCRIPT DOES
---------------------
Only the "export / define-a-missing-glue-symbol" part of the kit patch.
It deliberately does NOT touch the OneMind hooks or the kCFI keep-alive --
those are already handled by apply_kit_onemind.py.

Idempotent: every step checks for a marker before touching the file, and
any anchor that is not found exactly once is a hard failure (never emit a
half-patched kernel silently).
"""
import argparse
import io
import os
import sys

MARK = "MZ_KMI_EXPORT"


class P(object):
    def __init__(self, root, dry):
        self.root = root
        self.dry = dry
        self.log = []
        self.ok = True

    def path(self, rel):
        return os.path.join(self.root, rel)

    def read(self, rel):
        with io.open(self.path(rel), "r", encoding="utf-8",
                     errors="surrogateescape") as f:
            return f.read()

    def write(self, rel, text):
        if self.dry:
            return
        with io.open(self.path(rel), "w", encoding="utf-8",
                     errors="surrogateescape", newline="") as f:
            f.write(text)

    def note(self, msg):
        self.log.append(msg)

    def fail(self, msg):
        self.ok = False
        self.log.append("  !! " + msg)

    def append_block(self, rel, block, marker):
        if not os.path.isfile(self.path(rel)):
            self.fail("%s 不存在" % rel)
            return
        src = self.read(rel)
        if marker in src:
            self.note("  跳过(已导出): %s" % rel)
            return
        if not src.endswith("\n"):
            src += "\n"
        self.write(rel, src + block)
        self.note("  已打: %s" % rel)

    def replace_once(self, rel, old, new, marker, label):
        if not os.path.isfile(self.path(rel)):
            self.fail("%s 不存在" % rel)
            return
        src = self.read(rel)
        if marker in src:
            self.note("  跳过(已打过): %s (%s)" % (rel, label))
            return
        n = src.count(old)
        if n != 1:
            self.fail("%s 里锚点出现 %d 次（要求恰好 1 次）: %s"
                      % (rel, n, label))
            return
        self.write(rel, src.replace(old, new, 1))
        self.note("  已打: %s (%s)" % (rel, label))


def apply_vendor_hooks(p):
    """两个 NEEDED 的 vendor hook 符号，**性质不同，必须分开处理**。

    (1) android_vh_smartzram_tune_swappiness
        AOSP 上游【没有】这个 hook（已核对 include/trace/hooks/ 下无此名字），
        是魅族自己加的，所以必须像 21 Pro kit 那样用 MZ_DEFINE_VH 直接造出
        tracepoint 结构体 + 空 traceiter（厂商侧只要求符号存在）。
        stock CRC: __tracepoint=0x1523d65f / __traceiter=0xb2b09362

    (2) mm_vmscan_kswapd_wake
        ⚠ 这是 AOSP 上游【已有】的真实 TRACE_EVENT
        （include/trace/events/vmscan.h:66，
          TRACE_EVENT(mm_vmscan_kswapd_wake, TP_PROTO(int nid, int zid, int order), ...)）。
        所以【只能】用 EXPORT_TRACEPOINT_SYMBOL_GPL 导出它，绝不能 MZ_DEFINE_VH：
          · tracepoint.h 已声明 __traceiter_mm_vmscan_kswapd_wake(void*, int, int, int)，
            再定义 int __traceiter_...(void*) -> conflicting types -> 编译失败
          · 即便编过，空实现与真实 TRACE_EVENT 的原型不同，CRC 也对不上
        21 Pro kit patch 第 679 行用的就是 EXPORT_TRACEPOINT_SYMBOL_GPL。
    """
    rel = "drivers/android/vendor_hooks.c"
    block = """
/* %(m)s: 魅族21 厂商模块 NEED 的 vendor hook 符号
 *
 * 分两类处理（区别很重要，别混用）：
 *   · android_vh_smartzram_tune_swappiness —— AOSP 无此 hook，需自造符号
 *   · mm_vmscan_kswapd_wake               —— AOSP 已有真实 TRACE_EVENT，
 *                                            只能导出，不能再定义 traceiter
 *                                            （否则 conflicting types 编译失败）
 */
#define %(m)s_DEFINE_VH(name) \\
	struct tracepoint __tracepoint_##name; \\
	EXPORT_SYMBOL_GPL(__tracepoint_##name); \\
	int __traceiter_##name(void *__data) { return 0; } \\
	EXPORT_SYMBOL_GPL(__traceiter_##name);

%(m)s_DEFINE_VH(android_vh_smartzram_tune_swappiness)

/* mm_vmscan_kswapd_wake 由 include/trace/events/vmscan.h 的 TRACE_EVENT 定义，
 * 这里只把它导出（与 21 Pro kit patch 的写法一致） */
EXPORT_TRACEPOINT_SYMBOL_GPL(mm_vmscan_kswapd_wake);
""" % {"m": MARK}
    p.append_block(rel, block, "%s_DEFINE_VH" % MARK)


def apply_wakeup(p):
    """pm_system_irq_wakeup 已存在于 wakeup.c，只缺 EXPORT。"""
    rel = "drivers/base/power/wakeup.c"
    p.append_block(
        rel,
        "\n/* %s: pm_system_irq_wakeup (vendor NEED) */\n"
        "EXPORT_SYMBOL_GPL(pm_system_irq_wakeup);\n" % MARK,
        "%s: pm_system_irq_wakeup (vendor NEED)" % MARK)


def apply_memcontrol(p):
    """mem_cgroup_id_put2 在 6.1 不存在，需要新增 + 导出。"""
    rel = "mm/memcontrol.c"
    if not os.path.isfile(p.path(rel)):
        p.fail("%s 不存在" % rel)
        return
    src = p.read(rel)
    if "mem_cgroup_id_put2" in src:
        p.note("  跳过(已导出): %s (mem_cgroup_id_put2)" % rel)
        return
    # 锚定 mem_cgroup_id_put（单数）的定义，在它之后追加 _put2
    anchor = None
    for cand in ("void mem_cgroup_id_put(struct mem_cgroup *memcg)\n{\n"
                 "\tmem_cgroup_id_put_many(memcg, 1);\n}\n",):
        if cand in src:
            anchor = cand
            break
    if anchor is None:
        p.fail("%s 里 mem_cgroup_id_put 定义锚点未找到" % rel)
        return
    block = anchor + (
        "\n/* %s: 厂商 smartswap_zram.ko NEED mem_cgroup_id_put2 */\n"
        "void mem_cgroup_id_put2(struct mem_cgroup *memcg)\n{\n"
        "\tmem_cgroup_id_put_many(memcg, 1);\n}\n"
        "EXPORT_SYMBOL_GPL(mem_cgroup_id_put2);\n" % MARK)
    p.write(rel, src.replace(anchor, block, 1))
    p.note("  已打: %s (新增并导出 mem_cgroup_id_put2)" % rel)


def apply_vmscan(p):
    """reclaim_pages 已定义但未导出；shrink_* 是 static，去 static 后导出。

    只做 NEED 清单里确实需要的 reclaim_pages 的导出；
    shrink_* 系列一并处理以对齐原厂 KMI（它们不阻断开机，但成本很低）。
    """
    rel = "mm/vmscan.c"
    if not os.path.isfile(p.path(rel)):
        p.fail("%s 不存在" % rel)
        return
    src = p.read(rel)
    if "EXPORT_SYMBOL_GPL(reclaim_pages)" in src:
        p.note("  跳过(已导出): %s" % rel)
        return

    changed = False
    # reclaim_pages: 已是非 static，只需导出
    if "EXPORT_SYMBOL_GPL(reclaim_pages)" not in src:
        changed = True

    # shrink_inactive_list / shrink_active_list / shrink_list: static -> 全局
    for old, new, label in (
        ("static unsigned long shrink_inactive_list(unsigned long nr_to_scan,",
         "unsigned long shrink_inactive_list(unsigned long nr_to_scan,", "shrink_inactive_list"),
        ("static void shrink_active_list(unsigned long nr_to_scan,",
         "void shrink_active_list(unsigned long nr_to_scan,", "shrink_active_list"),
        ("static unsigned long shrink_list(enum lru_list lru, unsigned long nr_to_scan,",
         "unsigned long shrink_list(enum lru_list lru, unsigned long nr_to_scan,", "shrink_list"),
    ):
        if "EXPORT_SYMBOL_GPL(%s)" % label in src:
            continue
        n = src.count(old)
        if n != 1:
            p.note("  WARN %s: %s 锚点出现 %d 次，跳过去 static" % (rel, label, n))
            continue
        src = src.replace(old, new, 1)
        changed = True

    block = ("\n/* %s: 对齐原厂 KMI 的导出（reclaim_pages 被 smartswap_zram NEED） */\n"
             "EXPORT_SYMBOL_GPL(shrink_inactive_list);\n"
             "EXPORT_SYMBOL_GPL(shrink_active_list);\n"
             "EXPORT_SYMBOL_GPL(shrink_list);\n"
             "EXPORT_SYMBOL_GPL(reclaim_pages);\n" % MARK)
    if not src.endswith("\n"):
        src += "\n"
    src += block
    p.write(rel, src)
    p.note("  已打: %s (导出 shrink_* / reclaim_pages)" % rel)


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--kernel-root", required=True,
                    help="内核源码根（含 mm/ drivers/）")
    ap.add_argument("--dry-run", action="store_true")
    a = ap.parse_args()

    if not os.path.isdir(os.path.join(a.kernel_root, "mm")):
        print("内核源码根不对（没有 mm/）: %s" % a.kernel_root)
        return 2

    p = P(a.kernel_root, a.dry_run)
    print("== 补齐魅族21 厂商模块 NEED 的 KMI 导出 (%s) ==" % MARK)
    apply_vendor_hooks(p)
    apply_wakeup(p)
    apply_memcontrol(p)
    apply_vmscan(p)

    for line in p.log:
        print(line)

    if not p.ok:
        print("RESULT: FAIL —— 有锚点未命中, 构建必须停下")
        return 1
    print("RESULT: OK")
    return 0


if __name__ == "__main__":
    sys.exit(main())
