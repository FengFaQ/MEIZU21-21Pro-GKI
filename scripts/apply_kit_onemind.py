#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""移植 m2481kit 的 OneMind 8 大内存/I/O Vendor Hook 与 kCFI 保活 (阶段 4)。

为什么需要它（而不是继续用 tools/apply_kmi_shim.py 的占位）
---------------------------------------------------------
`tools/apply_kmi_shim.py` 把 8 个 OneMind hook 声明成了：

    DECLARE_HOOK(android_vh_extfrag_for_order,
            TP_PROTO(void *unused),
            TP_ARGS(unused));

这只是「让符号存在并导出」，**内核侧从来不会调用它** —— 于是：
  · 魅族 mz_compaction_opt / mz_watermark_opt / mz_mm_vip / mz_readahead_opt /
    mz_mm_protect 注册的回调永远不被触发，OneMind 等于完全没生效；
  · `TP_PROTO` 的形参类型决定了 `__traceiter_*` 的 kCFI Type Hash。原厂是
    `void(info, struct zone*, unsigned int)` 这类真签名，我们写成 `void*` 会得到
    另一个哈希 —— 模块侧调用点写死了原厂哈希，`CONFIG_CFI_CLANG=y` 下会
    `Kernel panic - not syncing: CFI failure`。

本脚本用 kit patch 的**真实签名**重写这 8 个 hook，并在内核各调用点插入
`trace_android_vh_*` 调用，同时补上 shrink_slab 的 kCFI 保活引用。

设计要点
--------
· 幂等：所有改动都带 `MZ_KIT_P4` 标记，重复执行不会叠加；
· 严格锚点：每个插入点都必须精确命中，命中不了就报错退出（绝不半途改）；
· 不碰 setlocalversion：kit patch 会把 scripts/setlocalversion 整个压成 2 行，
  那会毁掉我们的 `-android14-11-maybe-dirty` 版本串逻辑，这里刻意不移植那一段。

用法
----
    python3 scripts/apply_kit_onemind.py --kernel-root <common 目录> [--dry-run]
"""
import argparse
import io
import os
import re
import sys

MARK = "MZ_KIT_P4"


class Patcher(object):
    def __init__(self, root, dry):
        self.root = root
        self.dry = dry
        self.log = []
        self.ok = True

    def path(self, rel):
        return os.path.join(self.root, rel)

    def read(self, rel):
        with open(self.path(rel), "r", encoding="utf-8", errors="surrogateescape") as f:
            return f.read()

    def write(self, rel, text):
        if self.dry:
            return
        with open(self.path(rel), "w", encoding="utf-8",
                  errors="surrogateescape", newline="") as f:
            f.write(text)

    def note(self, msg):
        self.log.append(msg)

    def fail(self, msg):
        self.ok = False
        self.log.append("  !! " + msg)

    # ------------------------------------------------------------------
    # 通用：在 anchor 之后追加 block（幂等，靠 marker）
    # ------------------------------------------------------------------
    def insert_after(self, rel, anchor, block, marker):
        if not os.path.isfile(self.path(rel)):
            self.fail("%s 不存在" % rel)
            return False
        src = self.read(rel)
        if marker in src:
            self.note("  跳过(已打过): %s [%s]" % (rel, marker))
            return True
        if src.count(anchor) != 1:
            self.fail("%s 里锚点出现 %d 次（要求恰好 1 次）: %r"
                      % (rel, src.count(anchor), anchor[:60]))
            return False
        src = src.replace(anchor, anchor + block, 1)
        self.write(rel, src)
        self.note("  已打: %s [%s]" % (rel, marker))
        return True

    def insert_before(self, rel, anchor, block, marker):
        if not os.path.isfile(self.path(rel)):
            self.fail("%s 不存在" % rel)
            return False
        src = self.read(rel)
        if marker in src:
            self.note("  跳过(已打过): %s [%s]" % (rel, marker))
            return True
        if src.count(anchor) != 1:
            self.fail("%s 里锚点出现 %d 次（要求恰好 1 次）: %r"
                      % (rel, src.count(anchor), anchor[:60]))
            return False
        src = src.replace(anchor, block + anchor, 1)
        self.write(rel, src)
        self.note("  已打: %s [%s]" % (rel, marker))
        return True

    def replace_once(self, rel, old, new, marker):
        if not os.path.isfile(self.path(rel)):
            self.fail("%s 不存在" % rel)
            return False
        src = self.read(rel)
        if marker in src:
            self.note("  跳过(已打过): %s [%s]" % (rel, marker))
            return True
        if src.count(old) != 1:
            self.fail("%s 里待替换锚点出现 %d 次（要求恰好 1 次）: %r"
                      % (rel, src.count(old), old[:70]))
            return False
        self.write(rel, src.replace(old, new, 1))
        self.note("  已改: %s [%s]" % (rel, marker))
        return True


# ======================================================================
# 1) include/trace/hooks/mm.h —— 用真实签名声明 6 个 MM hook
# ======================================================================
MM_H_ANCHOR = "#endif /* _TRACE_HOOK_MM_H */"

MM_H_BLOCK = """
/* %(mark)s: OneMind 8 大 Hook(MM 部分) —— 签名必须与原厂逐字符一致,
 * 因为 TP_PROTO 的形参类型参与 __traceiter_* 的 kCFI Type Hash 计算。
 * 来源: m2481kit/patches/01-m2481-ko-krcrc-and-onemind.patch
 */
#if !defined(%(mark)s_MM_H) || defined(TRACE_HEADER_MULTI_READ)
#define %(mark)s_MM_H
struct readahead_control;
DECLARE_HOOK(android_vh_extfrag_for_order,
	TP_PROTO(void *info, struct zone *zone, unsigned int order),
	TP_ARGS(info, zone, order));
DECLARE_HOOK(android_vh_tune_watermark_ratio,
	TP_PROTO(unsigned int *low_high_wm_ratio),
	TP_ARGS(low_high_wm_ratio));
DECLARE_HOOK(android_vh_mm_vip_alloc_harder,
	TP_PROTO(gfp_t gfp_mask, unsigned int *alloc_flags, int *vip_promote),
	TP_ARGS(gfp_mask, alloc_flags, vip_promote));
DECLARE_HOOK(android_vh_mm_vip_kvmalloc,
	TP_PROTO(gfp_t *flags),
	TP_ARGS(flags));
DECLARE_HOOK(android_vh_mm_vip_alloc_harder_stat,
	TP_PROTO(unsigned int alloc_flags, unsigned long alloc_start, unsigned int order),
	TP_ARGS(alloc_flags, alloc_start, order));
DECLARE_HOOK(android_vh_ra_tuning_max_page,
	TP_PROTO(struct readahead_control *ractl, unsigned long *max_pages),
	TP_ARGS(ractl, max_pages));
#endif /* %(mark)s_MM_H */

""" % {"mark": MARK}

# ======================================================================
# 2) include/trace/hooks/vmscan.h —— 2 个 MGLRU hook
# ======================================================================
VMSCAN_H_ANCHOR = "#endif /* _TRACE_HOOK_VMSCAN_H */"

VMSCAN_H_BLOCK = """
/* %(mark)s: OneMind 8 大 Hook(MGLRU 部分), 签名与原厂一致 */
#if !defined(%(mark)s_VMSCAN_H) || defined(TRACE_HEADER_MULTI_READ)
#define %(mark)s_VMSCAN_H
struct lruvec;
struct lru_gen_folio;
DECLARE_HOOK(android_vh_memcg_filepages_protect,
	TP_PROTO(struct lruvec *lruvec, int file, bool *bypass),
	TP_ARGS(lruvec, file, bypass));
DECLARE_HOOK(android_vh_memcg_protect_lru_gen_add_folio,
	TP_PROTO(int type, struct lru_gen_folio *lrugen, struct lruvec *lruvec,
		unsigned long *seq),
	TP_ARGS(type, lrugen, lruvec, seq));
#endif /* %(mark)s_VMSCAN_H */

""" % {"mark": MARK}


def apply_headers(p):
    # ⚠ P0：kit 补丁本身已经往这两个头文件插了全部 8 个 DECLARE_HOOK
    #   （见 patches/01-m2481-...patch 的 include/trace/hooks/mm.h 与 vmscan.h）。
    #   我们若再插一份 -> **DECLARE_HOOK 重定义 -> 编译失败**。
    #   本地 e2e 树里已确认同一文件出现两份
    #   `DECLARE_HOOK(android_vh_extfrag_for_order`（run#20 之后的静态排查）。
    #
    #   因此这里是【补齐】语义，不是【无条件插入】：
    #     · 已声明且签名与 kit 原文一致 -> 跳过（kit patch 已做对）
    #     · 已声明但签名不对（如旧的 void *unused 占位）-> 就地替换签名
    #     · 完全没声明 -> 插入
    #   幂等判据用真实代码特征，不用易漂移的标记字符串。
    _ensure_hooks(p, "include/trace/hooks/mm.h", MM_H_ANCHOR,
                  MM_H_BLOCK, "MM_H")
    _ensure_hooks(p, "include/trace/hooks/vmscan.h", VMSCAN_H_ANCHOR,
                  VMSCAN_H_BLOCK, "VMSCAN_H")


# 每个头文件负责的 hook 名（用于"是否已声明"判定）
MM_H_NAMES = [
    "android_vh_extfrag_for_order",
    "android_vh_tune_watermark_ratio",
    "android_vh_mm_vip_alloc_harder",
    "android_vh_mm_vip_kvmalloc",
    "android_vh_mm_vip_alloc_harder_stat",
    "android_vh_ra_tuning_max_page",
]
VMSCAN_H_NAMES = [
    "android_vh_memcg_filepages_protect",
    "android_vh_memcg_protect_lru_gen_add_folio",
]


def _ensure_hooks(p, rel, anchor, block, tag):
    """确保 hook 在头文件里存在且签名正确，绝不重复插入。"""
    import re as _re
    names = MM_H_NAMES if tag == "MM_H" else VMSCAN_H_NAMES
    if not os.path.isfile(p.path(rel)):
        p.fail("%s 不存在" % rel)
        return
    src = p.read(rel)

    # ---------------------------------------------------------------
    # ★ 守卫修正（必须在"是否已声明"判断之前做）
    # ---------------------------------------------------------------
    # run#8 实测：把 DECLARE_HOOK 整块插在 #endif /* _TRACE_HOOK_MM_H */ 之前
    # 是【对的】，但块自身若用 `#ifndef MZ_KIT_P4_MM_H` 保护，就会挡住
    # <trace/define_trace.h> 触发的【第二次包含】(TRACE_HEADER_MULTI_READ)：
    #   · 第一次包含：MZ_KIT_P4_MM_H 未定义 -> 展开（只有声明）
    #   · 第二次包含：MZ_KIT_P4_MM_H 已定义 -> 被 #ifndef 挡掉
    #     -> DEFINE_TRACE / __tracepoint_* / __traceiter_* 从未生成
    #   -> vmlinux 链接报 20 个 undefined symbol（__tracepoint_*/__traceiter_*/__SCK__tp_func_*）
    # 上游头文件自己的守卫就是这种"镜像 TRACE_HEADER_MULTI_READ"写法，必须一致。
    # 这里做就地修正，使【已经插错过】的树也能被自动救回来。
    # ---------------------------------------------------------------
    old_guard = "#ifndef %s_%s\n#define %s_%s\n" % (MARK, tag, MARK, tag)
    new_guard = ("#if !defined(%s_%s) || defined(TRACE_HEADER_MULTI_READ)\n"
                 "#define %s_%s\n" % (MARK, tag, MARK, tag))
    if old_guard in src:
        src = src.replace(old_guard, new_guard, 1)
        p.write(rel, src)
        p.note("  已修: %s (守卫改为镜像 TRACE_HEADER_MULTI_READ，否则 DEFINE_TRACE 不生成)" % rel)

    declared = [n for n in names
                if _re.search(r"DECLARE_HOOK\(\s*%s\s*," % _re.escape(n), src)]

    if len(declared) == len(names):
        # 全部已声明；校验签名，不一致就地修（不新增条目 -> 不会重定义）
        fixed = 0
        for n in names:
            want_proto = KIT_REFERENCE[n][0]
            pat = _re.compile(
                r"(DECLARE_HOOK\(\s*%s\s*,\s*TP_PROTO\()(.*?)(\)\s*,\s*TP_ARGS\()"
                % _re.escape(n), _re.S)
            m = pat.search(src)
            if not m:
                continue
            if _squash(m.group(2)) == _squash(want_proto):
                continue
            src = src[:m.start(2)] + want_proto + src[m.end(2):]
            fixed += 1
        if fixed:
            p.write(rel, src)
            p.note("  已修: %s (%d 个 hook 签名与 kit 原文不符，就地修正)"
                   % (rel, fixed))
        else:
            p.note("  跳过(已声明且签名正确): %s" % rel)
        return

    if declared:
        # 部分声明 —— 危险：直接插整块会重复。只插缺失的，且不动已声明的。
        missing = [n for n in names if n not in declared]
        p.note("  %s 已声明 %d/%d，仅补缺失的 %s"
               % (rel, len(declared), len(names), missing))
        extra = ""
        for n in missing:
            proto, args = KIT_REFERENCE[n]
            extra += ("DECLARE_HOOK(%s,\n\tTP_PROTO(%s),\n\tTP_ARGS(%s));\n"
                      % (n, proto, args))
        p.insert_before(rel, anchor, "\n" + extra + "\n",
                        "#ifndef %s_%s" % (MARK, tag))
        return

    # 完全没声明 -> 插入整块
    p.insert_before(rel, anchor, block, "#ifndef %s_%s" % (MARK, tag))


# ======================================================================
# 3) 调用点：让内核真的去调用这些 hook
# ======================================================================
def apply_call_sites(p):
    # --- mm/vmstat.c: extfrag_for_order() ---
    # 原厂: trace_android_vh_extfrag_for_order(&info, zone, order) 修正高阶空闲块统计
    rel = "mm/vmstat.c"
    if os.path.isfile(p.path(rel)):
        src = p.read(rel)
        if "trace_android_vh_extfrag_for_order" in src:
            p.note("  跳过(已打过): %s (extfrag_for_order)" % rel)
        elif True:
            anchor = "\tfill_contig_page_info(zone, order, &info);\n"
            # 该调用在 vmstat.c 里出现多次（fill_contig_page_info 是公共辅助函数），
            # 必须定位到 extfrag_for_order() 函数体内的那一处。
            fn_anchor = "unsigned int extfrag_for_order(struct zone *zone, unsigned int order)\n{\n"
            if src.count(fn_anchor) == 1:
                fi = src.index(fn_anchor)
                ni = src.find(anchor, fi)
                fe = src.find("\n}\n", fi)
                if ni < 0 or ni > fe:
                    p.fail("%s 里 extfrag_for_order 函数体内找不到 fill_contig_page_info 调用" % rel)
                else:
                    block = ("\t/* %s: OneMind mz_compaction_opt 修正高阶空闲块统计 */\n"
                             "\ttrace_android_vh_extfrag_for_order(&info, zone, order);\n" % MARK)
                    src = src[:ni + len(anchor)] + block + src[ni + len(anchor):]
                    if "<trace/hooks/mm.h>" not in src:
                        a_inc = "#include <linux/page_owner.h>\n"
                        if src.count(a_inc) == 1:
                            src = src.replace(a_inc, a_inc + "#include <trace/hooks/mm.h>\n", 1)
                    src += "\n/* %s */\n" % (MARK + "_VMSTAT")
                    p.write(rel, src)
                    p.note("  已打: %s (extfrag_for_order hook 调用点)" % rel)
            else:
                p.fail("%s 里 extfrag_for_order 函数定义锚点出现 %d 次" % (rel, src.count(fn_anchor)))

    # --- mm/page_alloc.c: gfp_to_alloc_flags + __alloc_pages_slowpath + __setup_per_zone_wmarks ---
    rel = "mm/page_alloc.c"
    if os.path.isfile(p.path(rel)):
        src = p.read(rel)
        if "trace_android_vh_mm_vip_alloc_harder(" in src:
            p.note("  跳过(已打过): %s (mm_vip_alloc_harder)" % rel)
        elif True:
            changed = False
            # (a) gfp_to_alloc_flags: VIP 线程 ALLOC_HARDER
            a1 = ("\t} else if (unlikely(rt_task(current)) && in_task())\n"
                  "\t\talloc_flags |= ALLOC_HARDER;\n")
            if src.count(a1) == 1:
                block = ("\telse {\n"
                         "\t\tint vip_promote = 0;\n"
                         "\n"
                         "\t\t/* %s: OneMind mz_mm_vip 给前台 VIP 线程 ALLOC_HARDER 特权 */\n"
                         "\t\ttrace_android_vh_mm_vip_alloc_harder(gfp_mask, &alloc_flags, &vip_promote);\n"
                         "\t\tif (vip_promote)\n"
                         "\t\t\talloc_flags |= ALLOC_HARDER;\n"
                         "\t}\n" % MARK)
                src = src.replace(a1, a1 + block, 1)
                changed = True
            else:
                p.fail("%s 里 gfp_to_alloc_flags 的 rt_task 分支锚点出现 %d 次" % (rel, src.count(a1)))

            # (b) __alloc_pages_slowpath: 统计 VIP 慢路径
            a2 = "\tif (page)\n\t\tgoto got_pg;\n\n\t/*\n\t * For costly allocations, try direct compaction first,"
            if src.count(a2) == 1:
                block = ("\t/* %s: OneMind mz_mm_vip 统计 VIP 线程慢路径分配耗时 */\n"
                         "\ttrace_android_vh_mm_vip_alloc_harder_stat(alloc_flags, alloc_start, order);\n\n" % MARK)
                # 插在 goto got_pg 之后
                cut = "\tif (page)\n\t\tgoto got_pg;\n\n"
                src = src.replace(cut + "\t/*\n\t * For costly allocations",
                                  cut + block + "\t/*\n\t * For costly allocations", 1)
                changed = True
            else:
                p.fail("%s 里 __alloc_pages_slowpath 的 costly allocations 锚点出现 %d 次" % (rel, src.count(a2)))

            # (c) __setup_per_zone_wmarks: 水位比
            #
            # ⚠ run#7 实测：原实现只插了变量【声明】，没有任何代码使用它们，
            #   内核带 -Werror 直接报：
            #       page_alloc.c:8867: unused variable 'low_high_wm_ratio' [-Werror,-Wunused-variable]
            #       page_alloc.c:8868: unused variable 'mz_min' / 'mz_low' / 'mz_high'
            #   且 mz_min/mz_low/mz_high 是 kit 里【根本不存在】的发明物。
            #   按 21 Pro kit patch 原文补全：声明 + 真正使用它的水位计算。
            a3 = ("static void __setup_per_zone_wmarks(void)\n"
                  "{\n")
            if src.count(a3) == 1:
                i = src.index(a3)
                decl_anchor = "\tunsigned long flags;\n"
                k = src.find(decl_anchor, i)
                if k < 0:
                    p.fail("%s 里 __setup_per_zone_wmarks 声明区锚点未找到" % rel)
                else:
                    ins = k + len(decl_anchor)
                    src = (src[:ins]
                           + "\t/* %s: OneMind mz_watermark_opt 水位比 */\n"
                             "\tunsigned int low_high_wm_ratio = 1;\n" % MARK
                           + src[ins:])

                wm_old = ("\t\tzone->_watermark[WMARK_LOW]  = min_wmark_pages(zone) + tmp;\n"
                          "\t\tzone->_watermark[WMARK_HIGH] = low_wmark_pages(zone) + tmp;\n")
                if src.count(wm_old) == 1:
                    wm_new = ("\t\t/* %s: 厂商可调 LOW/HIGH 水位比 */\n"
                              "\t\ttrace_android_vh_tune_watermark_ratio(&low_high_wm_ratio);\n"
                              "\t\tif (low_high_wm_ratio != 1) {\n"
                              "\t\t\tzone->_watermark[WMARK_LOW] = min_wmark_pages(zone) +\n"
                              "\t\t\t\t(2 * tmp * low_high_wm_ratio) / (low_high_wm_ratio + 1);\n"
                              "\t\t\tzone->_watermark[WMARK_HIGH] = low_wmark_pages(zone) +\n"
                              "\t\t\t\t(2 * tmp) / (low_high_wm_ratio + 1);\n"
                              "\t\t} else {\n"
                              "\t\t\tzone->_watermark[WMARK_LOW]  = min_wmark_pages(zone) + tmp;\n"
                              "\t\t\tzone->_watermark[WMARK_HIGH] = low_wmark_pages(zone) + tmp;\n"
                              "\t\t}\n" % MARK)
                    src = src.replace(wm_old, wm_new, 1)
                    changed = True
                else:
                    p.fail("%s 里 watermark LOW/HIGH 计算锚点出现 %d 次"
                           "（tune_watermark_ratio 无处落地 -> unused variable）"
                           % (rel, src.count(wm_old)))
            else:
                p.fail("%s 里 __setup_per_zone_wmarks 锚点出现 %d 次" % (rel, src.count(a3)))

            if changed:
                if "<trace/hooks/mm.h>" not in src:
                    a_inc = "#include <linux/mm.h>\n"
                    if src.count(a_inc) >= 1:
                        src = src.replace(a_inc, a_inc + "#include <trace/hooks/mm.h>\n", 1)
                src += "\n/* %s */\n" % (MARK + "_PAGEALLOC")
                p.write(rel, src)
                p.note("  已打: %s (mm_vip_alloc_harder / _stat / tune_watermark_ratio)" % rel)

    # --- mm/util.c: kvmalloc_node () -> mm_vip_kvmalloc ---
    #
    # ⚠ 与 readahead.c 同类问题（run#5 后代码审查发现）：
    #   kvmalloc_node() 的函数体开头是两条【声明】：
    #       gfp_t kmalloc_flags = flags;
    #       void *ret;
    #   原实现把 hook（一条【语句】）插在函数体首行，其后紧跟上面两条声明，
    #   内核带 -Werror -Wdeclaration-after-statement（C90 不允许语句后再声明），
    #   于是 readahead.c 修好后这里必然接棒编译失败。
    #   修法：锚定声明区结束（两条声明之后）再插入语句。
    rel = "mm/util.c"
    if os.path.isfile(p.path(rel)):
        src = p.read(rel)
        if "trace_android_vh_mm_vip_kvmalloc" in src:
            p.note("  跳过(已打过): %s (mm_vip_kvmalloc)" % rel)
        else:
            sig = "void *kvmalloc_node(size_t size, gfp_t flags, int node)\n{"
            decl_end = "\tgfp_t kmalloc_flags = flags;\n\tvoid *ret;\n"
            if src.count(sig) == 1 and src.count(decl_end) >= 1:
                block = ("\n\t/* %s: OneMind mz_mm_vip 优化 VIP 线程 kvmalloc 回退策略 */\n"
                         "\ttrace_android_vh_mm_vip_kvmalloc(&flags);\n" % MARK)
                # 在函数签名之后的那个 decl_end 处插入（保证是本函数内的声明区末尾）
                i = src.index(sig)
                k = src.find(decl_end, i)
                if k < 0:
                    p.fail("%s 里 kvmalloc_node 声明区锚点未找到" % rel)
                else:
                    ins = k + len(decl_end)
                    src = src[:ins] + block + src[ins:]
                    if "<trace/hooks/mm.h>" not in src:
                        a_inc = "#include <linux/mm.h>\n"
                        if src.count(a_inc) >= 1:
                            src = src.replace(a_inc, a_inc + "#include <trace/hooks/mm.h>\n", 1)
                    src += "\n/* %s */\n" % (MARK + "_UTIL")
                    p.write(rel, src)
                    p.note("  已打: %s (mm_vip_kvmalloc 调用点，插在声明区之后)" % rel)
            else:
                p.fail("%s 里 kvmalloc_node 锚点出现 %d 次（签名 %d / 声明区 %d）"
                       % (rel, src.count(sig), src.count(sig), src.count(decl_end)))

    # --- mm/readahead.c: ondemand_readahead -> ra_tuning_max_page ---
    #
    # ⚠ run#5 实测（魅族21，编译期 3 个 error）：
    #   原实现把 hook 插在【函数体开头】（第一个 '{' 之后），且传值 max_pages。
    #   但 6.1 的 ondemand_readahead 里 max_pages 是局部变量、声明在函数体中段，
    #   于是编译报：
    #       error: use of undeclared identifier 'max_pages'   (x2)
    #       error: mixing declarations and code ... [-Wdeclaration-after-statement]
    #
    #   更严重的是【语义 bug】：hook 签名是
    #       TP_PROTO(struct readahead_control *ractl, unsigned long *max_pages)
    #   必须传 &max_pages，mz_readahead_opt 才能【改写】该值来抑制 UFS 4.0
    #   预读放大。传值的话即使编译通过，OneMind 的预读抑制也完全无效。
    #
    #   正确锚点与写法以 21 Pro kit patch 为准（patch 里 mm/readahead.c 的 hunk）：
    #       if (req_size > max_pages && bdi->io_pages > max_pages)
    #               max_pages = min(req_size, bdi->io_pages);
    #       trace_android_vh_ra_tuning_max_page(ractl, &max_pages);
    #   已核对 AOSP android14-6.1-2023-10_r1 的 mm/readahead.c 第 591-592 行，
    #   该两行唯一存在，锚点稳定。
    rel = "mm/readahead.c"
    if os.path.isfile(p.path(rel)):
        src = p.read(rel)
        if "trace_android_vh_ra_tuning_max_page" in src:
            p.note("  跳过(已打过): %s (ra_tuning_max_page)" % rel)
        else:
            a = ("\tif (req_size > max_pages && bdi->io_pages > max_pages)\n"
                 "\t\tmax_pages = min(req_size, bdi->io_pages);\n")
            if src.count(a) == 1:
                block = a + ("\t/* %s: OneMind mz_readahead_opt 抑制 UFS 4.0 预读放大 */\n"
                             "\ttrace_android_vh_ra_tuning_max_page(ractl, &max_pages);\n" % MARK)
                src = src.replace(a, block, 1)
                if "<trace/hooks/mm.h>" not in src:
                    a_inc = "#include <linux/mm.h>\n"
                    if src.count(a_inc) >= 1:
                        src = src.replace(a_inc, a_inc + "#include <trace/hooks/mm.h>\n", 1)
                src += "\n/* %s */\n" % (MARK + "_READAHEAD")
                p.write(rel, src)
                p.note("  已打: %s (ra_tuning_max_page 调用点，传 &max_pages)" % rel)
            else:
                p.fail("%s 里 max_pages 调整锚点出现 %d 次（要求恰好 1 次）"
                       % (rel, src.count(a)))


# ======================================================================
# 4) mm/vmscan.c —— shrink_slab 的 kCFI 保活 + MGLRU 两个 hook
# ======================================================================
def apply_vmscan(p):
    rel = "mm/vmscan.c"
    if not os.path.isfile(p.path(rel)):
        p.fail("%s 不存在" % rel)
        return
    src = p.read(rel)

    # (a) kCFI 保活：让 shrink_slab 被"取地址"，从而生成 KCFI 类型哈希前导码。
    #     魅族 mz_kshrink_slabd 通过 kallsyms 找到 shrink_slab 再间接调用。
    #
    # ⚠ run#6 实测教训：
    #   原实现用 src.index("\n}\n", i) 找函数体结束，但 shrink_slab 内部有
    #   if 块收尾也是 "\n}\n"，于是最先匹配到的是【函数内部】的块结束，
    #   声明被插进函数体中间（`return freed;` 之后、真正的 `}` 之前），变成
    #   "语句后接声明" -> 编译失败：
    #       mm/vmscan.c:1048:30: error: mixing declarations and code is
    #       incompatible with standards before C99 [-Werror,-Wdeclaration-after-statement]
    #   修法：用花括号配对找真正的函数体结束。
    if "shrink_slab_kcfi_ref" in src:
        p.note("  跳过(已打过): %s (shrink_slab kCFI 保活引用)" % rel)
    else:
        anchor = "static unsigned long shrink_slab(gfp_t gfp_mask, int nid,"
        if src.count(anchor) == 1:
            i = src.index(anchor)
            brace = src.index("{", i)
            depth = 0
            j = -1
            for k in range(brace, len(src)):
                if src[k] == "{":
                    depth += 1
                elif src[k] == "}":
                    depth -= 1
                    if depth == 0:
                        j = k          # 真正的函数体结束 '}'
                        break
            if j < 0:
                p.fail("%s 里 shrink_slab 花括号不配对" % rel)
            else:
                block = ("\n\n/*\n"
                         " * %s: 厂商模块 mz_kshrink_slabd 用 kallsyms 取到 shrink_slab 后经函数指针\n"
                         " * 间接调用。若内核树内无任何取地址操作，Clang 会优化掉函数入口前 -4 字节的\n"
                         " * kCFI Type Hash(0xc3b4beac)，模块间接调用即 `CFI failure` panic 卡第一屏。\n"
                         " * 这里保留一个 __used 函数指针，强制编译器生成类型前导码。\n"
                         " */\n"
                         "static unsigned long (*const shrink_slab_kcfi_ref)(gfp_t, int,\n"
                         "\t\tstruct mem_cgroup *, int) __used = shrink_slab;\n" % MARK)
                src = src[:j + 1] + block + src[j + 1:]
                p.note("  已打: %s (shrink_slab kCFI 保活引用，插在函数体结束之后)" % rel)
        else:
            p.fail("%s 里 shrink_slab 函数定义锚点出现 %d 次" % (rel, src.count(anchor)))

    # (b) MGLRU: sort_folio -> memcg_filepages_protect
    #     原厂在 sort_folio 里询问是否绕过回收，保护 600MB 系统核心文件缓存
    #
    # ⚠ run#4 实测（魅族21，无 kit patch 的干净 ACK 6.1.25 树）：
    #   此处锚点曾经写成【单行】：
    #       static bool sort_folio(struct lruvec *lruvec, struct folio *folio, int tier_idx)\n{\n
    #   但 6.1 的真实定义是【跨两行】且多一个 struct scan_control *sc 参数：
    #       static bool sort_folio(struct lruvec *lruvec, struct folio *folio, struct scan_control *sc,
    #                              int tier_idx)
    #       {
    #   于是 count==0 -> p.fail() -> 整个注入链中断 -> 门禁 2 的 16 个
    #   OneMind hook 全缺。21 Pro 之所以没暴露这个问题，是因为它的 kit patch
    #   会先完成注入，脚本走到 "已打过" 分支就直接跳过，永远走不到这段锚点。
    #   锚点以正则匹配，容忍两行、参数与空白差异。
    if "trace_android_vh_memcg_filepages_protect" in src:
        p.note("  跳过(已打过): %s (memcg_filepages_protect)" % rel)
    else:
        pat = re.compile(
            r"^static bool sort_folio\(\s*struct lruvec \*lruvec,\s*"
            r"struct folio \*folio,\s*struct scan_control \*sc,\s*"
            r"int tier_idx\)\s*\n\{\n",
            re.M)
        hits = list(pat.finditer(src))
        if len(hits) == 1:
            # 与 21 Pro kit patch 逐字节一致的注入方式：
            #   · 函数开头加 `bool bypass = false;`
            #   · 结尾 `return false;` 之前插入 hook 调用 + 回滚逻辑
            #   （回归到 kit 里已验证过的形态，避免出现两种不同的语义）
            src = src[:hits[0].end()] + "\tbool bypass = false;\n" + src[hits[0].end():]
            hits = list(pat.finditer(src))
            i = hits[0].end()
            j = src.index("\n\treturn false;\n", i)
            mz_block = ("\n"
                        "\t/* %s: OneMind mz_mm_protect 保护系统核心文件缓存免被驱逐 */\n"
                        "\ttrace_android_vh_memcg_filepages_protect(lruvec, type, &bypass);\n"
                        "\tif (bypass && gen == lru_gen_from_seq(lrugen->min_seq[type])) {\n"
                        "\t\tgen = folio_inc_gen(lruvec, folio, false);\n"
                        "\t\tlist_move(&folio->lru, &lrugen->folios[gen][type][zone]);\n"
                        "\t\treturn true;\n"
                        "\t}\n" % MARK)
            src = src[:j] + mz_block + src[j:]
            p.note("  已打: %s (sort_folio 的 memcg_filepages_protect 调用点)" % rel)
        else:
            p.fail("%s 里 sort_folio 锚点命中 %d 次（期望 1）。"
                   "6.1 真实签名为两行："
                   "static bool sort_folio(struct lruvec *lruvec, struct folio *folio, "
                   "struct scan_control *sc, int tier_idx)" % (rel, len(hits)))

    if MARK + "_VMSCAN" not in src:
        if "trace/hooks/vmscan.h" not in src:
            src = src.replace("#include <linux/vmscan.h>",
                              "#include <linux/vmscan.h>\n#include <trace/hooks/vmscan.h>", 1)
            if "trace/hooks/vmscan.h" not in src:
                src = '#include <trace/hooks/vmscan.h>\n' + src
        src += "\n/* %s */\n" % (MARK + "_VMSCAN")
    p.write(rel, src)


# ======================================================================
# 5) include/linux/mm_inline.h —— lru_gen_add_folio 的 MGLRU hook
# ======================================================================
def apply_mm_inline(p):
    """只【校验】kit patch 是否已正确插入 lru_gen hook，绝不自己插入。

    ## 为什么改成"只校验不插入"（run#22 实测教训）
    原实现往 `lru_gen_add_folio()` 的 if/else 链中间插：
        if (folio_test_active(folio))
            mz_seq = seq;                                          <- 插在这
            trace_android_vh_memcg_protect_lru_gen_add_folio(...);  <- 还有这个
        else if (...)                                              <- else 前有语句 -> 语法错

    run#22 报错：
        mm_inline.h:250:3: error: misleading indentation; statement is not part of
                           the previous 'if' [-Werror,-Wmisleading-indentation]
            mz_seq = seq;
        mm_inline.h:252:3: error: call to undeclared function
                           'trace_android_vh_memcg_protect_lru_gen_add_folio'
        mm_inline.h:253:2: error: expected expression
            else if (...)

    而 **kit patch 自己已经做对了** —— 插在整条 if/else 链【结束之后】：
        @@ -248,6 +251,7 @@
         	else
         		seq = lrugen->min_seq[type];

        +	memcg_protect_lru_gen_add_folio(type, lrugen, lruvec, &seq);
         	gen = lru_gen_from_seq(seq);

    我们那份是重复劳动且位置错误。而且我们调的是 **trace hook 函数**，
    需要 `#include <trace/hooks/mm.h>` 才可见；kit 插的是 mm_inline.h 内部
    自己声明的普通函数，没有这个依赖。删掉我们的插入后 undeclared 也自动消失。
    """
    rel = "include/linux/mm_inline.h"
    if not os.path.isfile(p.path(rel)):
        p.fail("%s 不存在" % rel)
        return
    src = p.read(rel)

    # 第一步：若存在我们旧版插入的痕迹（mz_seq），自动还原
    if "mz_seq" in src:
        import re as _re
        before = src
        src = _re.sub(
            r"\n\t\t/\* %s: 通知厂商模块[^\n]*\*/\n"
            r"\t\ttrace_android_vh_memcg_protect_lru_gen_add_folio\([^)]*&mz_seq\);\n"
            % _re.escape(MARK), "\n", src)
        src = _re.sub(r"\n\t\tmz_seq = seq;\n", "\n", src)
        src = _re.sub(
            r"\n\t/\* %s: OneMind mz_mm_protect 在 MGLRU 入队时识别 protect memcg \*/\n"
            r"\tunsigned long mz_seq;\n" % _re.escape(MARK), "\n", src)
        src = src.replace("\n/* %s */\n" % (MARK + "_LRUGEN"), "\n")
        if src != before:
            p.write(rel, src)
            p.note("  已还原: %s（删除旧版错误插入的 mz_seq hook，它破坏了 if/else 结构）"
                   % rel)
        else:
            p.fail("%s 里有 mz_seq 但未能自动还原，会导致编译失败，请人工检查" % rel)
            return

    # 第二步：校验 kit patch 的正确插入是否存在
    if "memcg_protect_lru_gen_add_folio" in src:
        p.note("  OK   %s: 已有 memcg_protect_lru_gen_add_folio 调用（kit patch 已正确插入）"
               % rel)
    else:
        # kit 的该 hunk 可能被 reject。**不自己插**（会再次插错位置），只告警：
        # 该 hook 缺失只影响 MGLRU 下文件缓存保护，不影响开机（非生死线）。
        p.note("  WARN %s: 未发现 memcg_protect_lru_gen_add_folio 调用"
               "（kit patch 该 hunk 可能被 reject；不影响开机）" % rel)


# ======================================================================
# 6) kernel/module/version.c —— bad_version 放行（CRC 双保险）
# ======================================================================
def _warn_version(p, msg):
    """version.c 项的告警（不阻塞构建）。

    与 p.fail() 的区别：p.fail() 会让整个脚本 RESULT: FAIL、CI 步骤失败；
    这里只记录告警。原因见 apply_version_c() 里的说明 ——
    bad_version 放行属于"双保险"的第二道，第一道是二进制层 CRC 覆写。
    """
    p.log.append("[WARN] kernel/module/version.c: " + msg)


def apply_vendor_hooks(p):
    """导出 OneMind 8 大 Hook 的 tracepoint 符号（100% KMI 兼容）。

    ⚠ 为什么必须有这一步（run#5 前的代码审查结论）：
      DECLARE_HOOK 展开为 DECLARE_TRACE，它只【定义】 __tracepoint_/__traceiter_，
      **不会导出**这两个符号。AOSP 的做法是在 drivers/android/vendor_hooks.c 里
      对每个 hook 写一行 EXPORT_TRACEPOINT_SYMBOL_GPL()，其展开为：
          EXPORT_SYMBOL_GPL(__tracepoint_##name);
          EXPORT_SYMBOL_GPL(__traceiter_##name);
          EXPORT_STATIC_CALL_GPL(tp_func_##name)
      （见 AOSP include/linux/tracepoint.h:327）

      21 Pro 的 kit patch 里有这 8 行，所以 21 Pro 门禁 2 能过；
      魅族21 走「无 kit patch」分支时，apply_kit_onemind.py 原来只写
      DECLARE_HOOK + 调用点、从不导出 -> 门禁 2 的 16 个符号
      （8 个 hook × __tracepoint_/__traceiter_）必然缺失。
      这就是 run#3 那句：
          [ERROR] Missing Meizu OneMind MM Hook exports: [ 16 个 ]
      的完整成因之一。缺了这些符号，厂商 .ko 加载时 Unknown symbol -> 卡第一屏。

      已核实：这 16 个符号在【魅族21 自己的原厂 stock_Module.symvers】里 16/16 存在。
    """
    rel = "drivers/android/vendor_hooks.c"
    if not os.path.isfile(p.path(rel)):
        p.fail("%s 不存在（无法导出 OneMind hook 符号，门禁 2 必然失败）" % rel)
        return

    src = p.read(rel)
    if MARK + "_VH_EXPORT" in src:
        p.note("  跳过(已打过): %s [%s_VH_EXPORT]" % (rel, MARK))
        return

    hooks = [
        "android_vh_extfrag_for_order",
        "android_vh_memcg_filepages_protect",
        "android_vh_memcg_protect_lru_gen_add_folio",
        "android_vh_mm_vip_alloc_harder",
        "android_vh_mm_vip_alloc_harder_stat",
        "android_vh_mm_vip_kvmalloc",
        "android_vh_ra_tuning_max_page",
        "android_vh_tune_watermark_ratio",
    ]

    # 已经导出过的（21 Pro 走 kit patch 时会先写进去）就不要再写，避免重复定义
    need = [h for h in hooks
            if ("EXPORT_TRACEPOINT_SYMBOL_GPL(%s)" % h) not in src]
    if not need:
        p.note("  跳过(已导出): %s 里 8 个 hook 均已 EXPORT_TRACEPOINT_SYMBOL_GPL" % rel)
        return

    block = ["\n/* %s_VH_EXPORT: OneMind 8 大 Hook 的 KMI 兼容导出 */" % MARK]
    for h in need:
        block.append("EXPORT_TRACEPOINT_SYMBOL_GPL(%s);" % h)
    block.append("/* %s_VH_EXPORT */\n" % MARK)

    # 追加到文件末尾即可：vendor_hooks.c 的 EXPORT_* 都是文件作用域语句。
    src = src.rstrip("\n") + "\n" + "\n".join(block)
    p.write(rel, src)
    p.note("  已打: %s (导出 %d 个 OneMind hook: %s)"
           % (rel, len(need), ", ".join(need)))


def apply_version_c(p):
    rel = "kernel/module/version.c"
    path = p.path(rel)
    if not os.path.isfile(path):
        # 这个文件必然存在（kernel/module/version.c 是 6.1 标配）。
        # 不存在说明 COMMON 路径判断错了，值得告警但不阻塞编译。
        _warn_version(p, "文件不存在: %s" % path)
        return
    src = p.read(rel)

    # ---------------- DEBUG（run#19 诊断用，成功后可移除）----------------
    try:
        print("[DEBUG version.c] path=%s size=%d" % (path, os.path.getsize(path)))
        _labels = [m for m in __import__("re").finditer(r"^bad_version:", src,
                                                        __import__("re").M)]
        print("[DEBUG version.c] 行首 'bad_version:' 标签数 = %d" % len(_labels))
        if _labels:
            _i = _labels[0].start()
            print("[DEBUG version.c] 其后 200 字符: %r" % src[_i:_i + 200])
        else:
            _i = src.find("bad_version")
            print("[DEBUG version.c] 无行首标签；首次出现位置=%d" % _i)
            if _i >= 0:
                print("[DEBUG version.c] 上下文: %r" % src[max(0, _i - 80):_i + 160])
            else:
                print("[DEBUG version.c] 全文没有 'bad_version' 字符串")
                print("[DEBUG version.c] 文件前 300 字符: %r" % src[:300])
    except Exception as _e:
        print("[DEBUG version.c] 诊断异常: %r" % (_e,))
    # ---------------- END DEBUG ----------------

    # ★ 幂等判据必须检查【实际效果】，不能检查特定注释文本。
    #   因为 kit 的 apply_mz_ko_and_onemind.py 也会改这个文件，
    #   它写的是不带我们注释的裸版本（`bad_version:\n\treturn 1;`）。
    #   若按注释判断就会出现"已经改好了却报找不到锚点"的假失败（本地实测踩过）。
    import re as _re
    if _re.search(r"bad_version:\s*\n(?:\s*(?:/\*.*?\*/\s*\n)?)*\s*return\s+1\s*;",
                  src, _re.S):
        p.note("  跳过(已放行): %s (bad_version 已是 return 1)" % rel)
        return

    # 宽松兜底：只要 bad_version 标签后面（到函数结束前）不再有 return 0，
    # 也认为已经放行。有些上游/patch 组合会保留 pr_warn 但去掉 return 0。
    _seg = _re.search(r"^bad_version:(.+?)^}", src, _re.S | _re.M)
    if _seg and "return 0" not in _seg.group(1):
        p.note("  跳过(已放行): %s (bad_version 段内无 return 0)" % rel)
        return

    # kits 的做法：bad_version: 分支直接 return 1
    old = """bad_version:
	pr_warn("%s: disagrees about version of symbol %s\\n", info->name, symname);
	return 0;"""
    new = """bad_version:
	/* %s: 放行 krcrc(MODVERSIONS) 符号 CRC 不一致 —— 魅族 471 个原厂 .ko
	 * 的 CRC 与自编译内核可能有微小差异, 这里按 kit 的做法直接判为兼容,
	 * 作为符号名匹配之外的"双保险"(符号名/命名空间仍严格校验)。
	 * 注意: 只放宽 CRC, 不放宽符号缺失 —— 缺失符号仍然由 resolve_symbol 报错。
	 */
	return 1;""" % MARK
    if src.count(old) == 1:
        p.write(rel, src.replace(old, new, 1))
        p.note("  已打: %s (bad_version 放行)" % rel)
    else:
        # 兼容上游写法差异：用正则退而求其次
        import re
        pat = re.compile(r"bad_version:\s*\n\s*pr_warn\([^\n]*\n\s*return 0;")
        m = pat.search(src)
        if m:
            p.write(rel, src[:m.start()] + new + src[m.end():])
            p.note("  已打(正则匹配): %s (bad_version 放行)" % rel)
        else:
            # ★ 降级为 WARN，不阻塞编译。
            #   bad_version 放行是 krcrc 的【第二道】防线；第一道（也是真正生效的）
            #   是「门禁 2/3」里 check_krcrc_symvers.py 在二进制层把 __kcrctab
            #   的 CRC 覆写为原厂值。即使这一项没改上，CRC 不一致的模块也能加载。
            #   不能因为这道"双保险"缺失就挡住整个构建 —— 先编出来验证
            #   AVB / kCFI 这两个真正的死因。
            _warn_version(p, "未找到可放行的 bad_version 分支，"
                             "依赖二进制层 __kcrctab 覆写兜底")


# ======================================================================
# 7) 【权威基准】kit patch 原文里的 8 个 hook 签名
# ----------------------------------------------------------------------
# 判据来源: m2481kit/patches/01-m2481-ko-krcrc-and-onemind.patch 的原始行，
# 不是"按我们实现反推"。校验方式是让 kit 当准绳、我们当被测对象。
#
# 为什么必须逐字符一致：TP_PROTO 的形参类型参与 __traceiter_* 的
# kCFI Type Hash 计算。签名差一个类型 -> 哈希不同 -> 模块调用点写死的
# 原厂哈希对不上 -> CONFIG_CFI_CLANG=y 下 `CFI failure` panic 卡第一屏。
# ======================================================================
KIT_REFERENCE = {
    # --- include/trace/hooks/mm.h ---
    "android_vh_extfrag_for_order": (
        "void *info, struct zone *zone, unsigned int order",
        "info, zone, order"),
    "android_vh_tune_watermark_ratio": (
        "unsigned int *low_high_wm_ratio",
        "low_high_wm_ratio"),
    "android_vh_mm_vip_alloc_harder": (
        "gfp_t gfp_mask, unsigned int *alloc_flags, int *vip_promote",
        "gfp_mask, alloc_flags, vip_promote"),
    "android_vh_mm_vip_kvmalloc": (
        "gfp_t *flags",
        "flags"),
    "android_vh_mm_vip_alloc_harder_stat": (
        "unsigned int alloc_flags, unsigned long alloc_start, unsigned int order",
        "alloc_flags, alloc_start, order"),
    "android_vh_ra_tuning_max_page": (
        "struct readahead_control *ractl, unsigned long *max_pages",
        "ractl, max_pages"),
    # --- include/trace/hooks/vmscan.h ---
    "android_vh_memcg_filepages_protect": (
        "struct lruvec *lruvec, int file, bool *bypass",
        "lruvec, file, bypass"),
    "android_vh_memcg_protect_lru_gen_add_folio": (
        "int type, struct lru_gen_folio *lrugen, struct lruvec *lruvec, unsigned long *seq",
        "type, lrugen, lruvec, seq"),
}


def _squash(text):
    """把任意空白压成单空格，便于做"签名等价"比较（容忍换行/缩进差异）。"""
    return " ".join(text.split())


def verify_hook_signatures(root, verbose=True):
    """校验内核树里 8 个 hook 的 TP_PROTO 与 kit 原文逐字符一致。

    返回 (ok, messages)。这是"以 kit 为准绳"的自检，独立于我们怎么写的实现。
    """
    ok = True
    msgs = []

    targets = [
        ("include/trace/hooks/mm.h", [
            "android_vh_extfrag_for_order",
            "android_vh_tune_watermark_ratio",
            "android_vh_mm_vip_alloc_harder",
            "android_vh_mm_vip_kvmalloc",
            "android_vh_mm_vip_alloc_harder_stat",
            "android_vh_ra_tuning_max_page",
        ]),
        ("include/trace/hooks/vmscan.h", [
            "android_vh_memcg_filepages_protect",
            "android_vh_memcg_protect_lru_gen_add_folio",
        ]),
    ]

    import re
    for rel, names in targets:
        path = os.path.join(root, rel)
        if not os.path.isfile(path):
            ok = False
            msgs.append("  FAIL %s 不存在" % rel)
            continue
        with io.open(path, "r", encoding="utf-8", errors="surrogateescape") as f:
            src = f.read()

        for name in names:
            want_proto, want_args = KIT_REFERENCE[name]
            # 抓 DECLARE_HOOK(name, TP_PROTO(...), TP_ARGS(...));
            m = re.search(
                r"DECLARE_HOOK\(\s*%s\s*,\s*TP_PROTO\((.*?)\)\s*,\s*TP_ARGS\((.*?)\)\s*\)\s*;"
                % re.escape(name),
                src, re.S)
            if not m:
                ok = False
                msgs.append("  FAIL %s: 在 %s 里找不到 DECLARE_HOOK（可能没打上）" % (name, rel))
                continue
            got_proto = _squash(m.group(1))
            got_args = _squash(m.group(2))
            exp_proto = _squash(want_proto)
            exp_args = _squash(want_args)

            # 致命错误：占位签名 void *unused —— OneMind 回调永远不会被调用
            if got_proto == "void *unused" or got_proto == "void * unused":
                ok = False
                msgs.append(
                    "  FAIL %s: TP_PROTO 仍是占位 'void *unused'！"
                    " 这会让 OneMind 回调永不触发，且 kCFI 哈希与原厂不符 → 卡第一屏" % name)
                continue

            if got_proto != exp_proto:
                ok = False
                msgs.append("  FAIL %s: TP_PROTO 与 kit 原文不符\n"
                            "         期望: %s\n         实际: %s"
                            % (name, exp_proto, got_proto))
            elif got_args != exp_args:
                ok = False
                msgs.append("  FAIL %s: TP_ARGS 与 kit 原文不符\n"
                            "         期望: %s\n         实际: %s"
                            % (name, exp_args, got_args))
            else:
                msgs.append("  OK   %s: TP_PROTO/TP_ARGS 与 kit 原文逐字符一致" % name)

    if verbose:
        for m in msgs:
            print(m)
    return ok, msgs


def verify_kernel_release(root, expect="6.1.25-android14-11-maybe-dirty", verbose=True):
    """校验最终内核版本串（vermagic 前缀）是否与原厂一致。

    ★ 刻意【不】移植 kit 的 scripts/setlocalversion 整文件替换：
      那会把 173 行脚本压成 2 行，并覆盖 build.yml「配置内核名称」步骤
      对 setlocalversion / stamp.bzl 的既有处理，两套逻辑会互相打架。
      我们的写法已在 run 14/15 产出正确版本串，这里只做结果断言。
    """
    ok = True
    msgs = []
    cand = [
        os.path.join(root, "include", "config", "kernel.release"),
        os.path.join(root, "include", "generated", "utsrelease.h"),
    ]
    found = None
    for c in cand:
        if os.path.isfile(c):
            found = c
            break
    if not found:
        msgs.append("  WARN 未找到 kernel.release/utsrelease.h（未编译或路径不同），跳过版本串断言")
        if verbose:
            for m in msgs:
                print(m)
        return True, msgs
    with io.open(found, "r", encoding="utf-8", errors="surrogateescape") as f:
        text = f.read()
    if found.endswith("utsrelease.h"):
        import re
        m = re.search(r'UTS_RELEASE\s+"([^"]+)"', text)
        got = m.group(1) if m else ""
    else:
        got = text.strip()
    if expect in got:
        msgs.append("  OK   内核版本串 = %s（含 %s）" % (got, expect))
    else:
        ok = False
        msgs.append("  FAIL 内核版本串 = %r，期望包含 %r —— 厂商 471 个 .ko 的 vermagic 会对不上"
                    % (got, expect))
    if verbose:
        for m in msgs:
            print(m)
    return ok, msgs


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--kernel-root", required=True, help="内核源码根（含 drivers/ mm/ include/）")
    ap.add_argument("--dry-run", action="store_true")
    a = ap.parse_args()

    if not os.path.isdir(os.path.join(a.kernel_root, "mm")):
        print("内核源码根不对（没有 mm/）: %s" % a.kernel_root)
        return 2

    p = Patcher(a.kernel_root, a.dry_run)
    print("== 移植 m2481kit OneMind 8 大 Hook + kCFI 保活 (%s) ==" % MARK)
    apply_headers(p)
    apply_call_sites(p)
    apply_vmscan(p)
    apply_mm_inline(p)
    apply_vendor_hooks(p)
    apply_version_c(p)

    for line in p.log:
        print(line)

    print()
    print("== 自检 1: 8 个 hook 的 TP_PROTO/TP_ARGS 与 kit 原文一致性 ==")
    sig_ok, _ = verify_hook_signatures(a.kernel_root, verbose=not a.dry_run or True)

    print()
    print("== 自检 2: 内核版本串（vermagic 前缀） ==")
    ver_ok, _ = verify_kernel_release(a.kernel_root)

    if not p.ok:
        print("RESULT: FAIL —— 有锚点未命中, 构建必须停下（不要拿半成品刷机）")
        return 1
    if not sig_ok:
        print("RESULT: FAIL —— hook 签名与 kit 原文不符, 会导致 kCFI 哈希错位, 拒绝继续")
        return 1
    if not ver_ok:
        print("RESULT: FAIL —— 内核版本串不符, 厂商模块 vermagic 不匹配")
        return 1
    print("RESULT: OK")
    return 0


if __name__ == "__main__":
    sys.exit(main())
