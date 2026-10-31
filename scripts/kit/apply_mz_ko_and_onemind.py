#!/usr/bin/env python3
"""
魅族 21 Pro (M2481) 原厂 .ko 驱动兼容 + krcrc 放行 + kCFI 保活 + OneMind 8 大内存/I/O Hook 自动接通脚本
用法:
    python3 apply_mz_ko_and_onemind.py [/path/to/common]
说明:
    如果你不想用 git apply 打 01-m2481-ko-krcrc-and-onemind.patch（或者你的 6.1 分支存在行号偏移），
    可以直接运行本脚本，它会按代码块精确匹配并幂等地完成全部 15 个文件的修改。
"""
import sys
from pathlib import Path

ROOT = Path(sys.argv[1]) if len(sys.argv) > 1 else Path("/home/ovo/m2481-kernel/common")


def patch_file(rel_path: str, old: str, new: str, marker: str = None):
    """把 old 片段替换成 new；已打过则跳过。

    ⚠ 幂等判据（run#20 教训，必须用 marker）
    ------------------------------------------------------------
    原来用 `if new in text` 判断"是否已打过"。但这要求 patch 与脚本的
    文本**逐字节一致**，一旦有人改了注释 / 换行 / 缩进 / 形参折行方式，
    判据就失配 —— 而 `old` 片段此时往往仍然存在（因为它通常是被替换内容
    的前缀或相邻上下文），于是**重复插入** -> 编译期 `redefinition`。

    实测事故（魅族21 走 patch 流程时）：
        mm/vmscan.c:1057: error: redefinition of 'shrink_slab_kcfi_ref'
        脚本 2b:  (gfp_t, int, struct mem_cgroup *, int)  ← 单行形参
        patch  :  (gfp_t, int,\n\t\tstruct mem_cgroup *, int)  ← 分行形参
      -> new 整段失配 -> 二次插入。

    因此改为：幂等判据优先用 `marker`（一处改动里**最具特异性**的单一
    标识符，如新函数名 / 新符号名），与注释、换行、缩进完全解耦。
    未显式给 marker 时退回 new（行为与旧版一致，仅供无歧义场景使用）。
    """
    p = ROOT / rel_path
    text = p.read_text(encoding="utf-8")
    probe = marker if marker is not None else new
    if probe in text:
        print(f"[SKIP] {rel_path} (already patched: {probe if marker else 'new block'})")
        return
    if old not in text:
        raise RuntimeError(f"[ERROR] Target snippet not found in {rel_path}")
    if text.count(old) != 1:
        raise RuntimeError(f"[ERROR] Target snippet matched {text.count(old)} times in {rel_path}")
    p.write_text(text.replace(old, new, 1), encoding="utf-8", newline="\n")
    print(f"[OK]   Patched {rel_path}")


def main():
    if not ROOT.is_dir():
        print(f"[ERROR] Kernel source directory not found: {ROOT}")
        sys.exit(1)

    # 0. 删除 GKI 导出符号保护列表 (解除 471 个外部 .ko 模块的符号命名空间限制)
    prot_file = ROOT / "android/abi_gki_protected_exports_aarch64"
    if prot_file.exists():
        prot_file.unlink()
        print("[OK]   Deleted android/abi_gki_protected_exports_aarch64")
    else:
        print("[SKIP] android/abi_gki_protected_exports_aarch64 (already deleted)")

    # 1. scripts/setlocalversion: 固定 vermagic 为 -android14-11-maybe-dirty，确保原厂 471 个 .ko 校验通过
    setlocal = ROOT / "scripts/setlocalversion"
    setlocal.write_text('#!/bin/sh\necho "-android14-11-maybe-dirty"\n', encoding="utf-8", newline="\n")
    print("[OK]   Updated scripts/setlocalversion -> -android14-11-maybe-dirty")

    # 2. kernel/module/version.c: krcrc (CONFIG_MODVERSIONS) 符号 CRC 校验安全兜底放行
    patch_file(
        "kernel/module/version.c",
        """bad_version:
\tpr_warn("%s: disagrees about version of symbol %s\\n", info->name, symname);
\treturn 0;
}""",
        """bad_version:
\treturn 1;
}""",
        marker='bad_version:\n\treturn 1;'
    )

    # 3. 补齐魅族专有 .ko 依赖的额外内核导出符号
    patch_file(
        "drivers/base/power/wakeup.c",
        "postcore_initcall(wakeup_sources_debugfs_init);",
        "postcore_initcall(wakeup_sources_debugfs_init);\n\nEXPORT_SYMBOL_GPL(pm_system_irq_wakeup);",
        marker='EXPORT_SYMBOL_GPL(pm_system_irq_wakeup);'
    )

    patch_file(
        "drivers/scsi/scsi_lib.c",
        """\treturn ret;
}
EXPORT_SYMBOL(scsi_execute_cmd);""",
        """\treturn ret;
}
EXPORT_SYMBOL(scsi_execute_cmd);
int scsi_execute_cmd_len(struct scsi_device *sdev, const unsigned char *cmd,
\t\t\t blk_opf_t opf, void *buffer, unsigned int bufflen,
\t\t\t int timeout, int retries,
\t\t\t const struct scsi_exec_args *args, int cmd_len)
{
\treturn scsi_execute_cmd(sdev, cmd, opf, buffer, bufflen, timeout, retries, args);
}
EXPORT_SYMBOL(scsi_execute_cmd_len);""",
        marker='scsi_execute_cmd_len('
    )

    patch_file(
        "mm/memcontrol.c",
        """static inline void mem_cgroup_id_put(struct mem_cgroup *memcg)
{
\tmem_cgroup_id_put_many(memcg, 1);
}""",
        """static inline void mem_cgroup_id_put(struct mem_cgroup *memcg)
{
\tmem_cgroup_id_put_many(memcg, 1);
}
void mem_cgroup_id_put2(struct mem_cgroup *memcg)
{
\tmem_cgroup_id_put_many(memcg, 1);
}
EXPORT_SYMBOL_GPL(mem_cgroup_id_put2);""",
        marker='mem_cgroup_id_put2('
    )

    # 4. mm/vmscan.c: shrink_slab kCFI 前导码保活引用 + 内存回收符号导出
    patch_file(
        "mm/vmscan.c",
        """static void drop_slab_node(int nid)""",
        """/*
 * Vendor module mz_kshrink_slabd resolves shrink_slab via kallsyms and calls it
 * indirectly; keep it address-taken so the KCFI type id preamble is emitted.
 */
static unsigned long (*const shrink_slab_kcfi_ref)(gfp_t, int, struct mem_cgroup *, int)
\t__used = shrink_slab;

static void drop_slab_node(int nid)""",
        marker='shrink_slab_kcfi_ref'
    )

    patch_file(
        "mm/vmscan.c",
        """static unsigned long shrink_inactive_list(unsigned long nr_to_scan,
\t\tstruct lruvec *lruvec, struct scan_control *sc,
\t\tenum lru_list lru)""",
        """unsigned long shrink_inactive_list(unsigned long nr_to_scan,
\t\tstruct lruvec *lruvec, struct scan_control *sc,
\t\tenum lru_list lru)""",
        # ⚠ 必须带行首 \n：'unsigned long shrink_inactive_list(...)' 是
        #   'static unsigned long shrink_inactive_list(...)' 的子串，不加行首会在
        #   打补丁前就命中 -> 误判已打过 -> 该改动被跳过。
        marker='\nunsigned long shrink_inactive_list(unsigned long nr_to_scan,'
    )

    patch_file(
        "mm/vmscan.c",
        """static void shrink_active_list(unsigned long nr_to_scan,
\t\t\t       struct lruvec *lruvec,
\t\t\t       struct scan_control *sc,
\t\t\t       enum lru_list lru)""",
        """void shrink_active_list(unsigned long nr_to_scan,
\t\t\t       struct lruvec *lruvec,
\t\t\t       struct scan_control *sc,
\t\t\t       enum lru_list lru)""",
        marker='\nvoid shrink_active_list(unsigned long nr_to_scan,'
    )

    patch_file(
        "mm/vmscan.c",
        """static unsigned long shrink_list(enum lru_list lru, unsigned long nr_to_scan,
\t\t\t\t struct lruvec *lruvec, struct scan_control *sc)""",
        """unsigned long shrink_list(enum lru_list lru, unsigned long nr_to_scan,
\t\t\t\t struct lruvec *lruvec, struct scan_control *sc)""",
        marker='\nunsigned long shrink_list(enum lru_list lru, unsigned long nr_to_scan,'
    )

    patch_file(
        "mm/vmscan.c",
        "EXPORT_SYMBOL_GPL(check_move_unevictable_folios);",
        """EXPORT_SYMBOL_GPL(check_move_unevictable_folios);

EXPORT_SYMBOL_GPL(shrink_inactive_list);
EXPORT_SYMBOL_GPL(shrink_active_list);
EXPORT_SYMBOL_GPL(shrink_list);
EXPORT_SYMBOL_GPL(reclaim_pages);
EXPORT_TRACEPOINT_SYMBOL_GPL(mm_vmscan_kswapd_wake);""",
        marker='EXPORT_TRACEPOINT_SYMBOL_GPL(mm_vmscan_kswapd_wake);'
    )

    # 5. 声明与导出魅族 OneMind 8 大专属 MM Vendor Hooks
    patch_file(
        "include/trace/hooks/mm.h",
        """DECLARE_HOOK(android_vh_adjust_kvmalloc_flags,
\tTP_PROTO(unsigned int order, gfp_t *alloc_flags),
\tTP_ARGS(order, alloc_flags));
#endif /* _TRACE_HOOK_MM_H */""",
        """DECLARE_HOOK(android_vh_adjust_kvmalloc_flags,
\tTP_PROTO(unsigned int order, gfp_t *alloc_flags),
\tTP_ARGS(order, alloc_flags));
struct readahead_control;
DECLARE_HOOK(android_vh_extfrag_for_order,
\tTP_PROTO(void *info, struct zone *zone, unsigned int order),
\tTP_ARGS(info, zone, order));
DECLARE_HOOK(android_vh_tune_watermark_ratio,
\tTP_PROTO(unsigned int *low_high_wm_ratio),
\tTP_ARGS(low_high_wm_ratio));
DECLARE_HOOK(android_vh_mm_vip_alloc_harder,
\tTP_PROTO(gfp_t gfp_mask, unsigned int *alloc_flags, int *vip_promote),
\tTP_ARGS(gfp_mask, alloc_flags, vip_promote));
DECLARE_HOOK(android_vh_mm_vip_kvmalloc,
\tTP_PROTO(gfp_t *flags),
\tTP_ARGS(flags));
DECLARE_HOOK(android_vh_mm_vip_alloc_harder_stat,
\tTP_PROTO(unsigned int alloc_flags, unsigned long alloc_start, unsigned int order),
\tTP_ARGS(alloc_flags, alloc_start, order));
DECLARE_HOOK(android_vh_ra_tuning_max_page,
\tTP_PROTO(struct readahead_control *ractl, unsigned long *max_pages),
\tTP_ARGS(ractl, max_pages));
#endif /* _TRACE_HOOK_MM_H */""",
        marker='DECLARE_HOOK(android_vh_extfrag_for_order,'
    )

    patch_file(
        "include/trace/hooks/vmscan.h",
        """DECLARE_HOOK(android_vh_vmscan_kswapd_done,
\tTP_PROTO(int node_id, unsigned int highest_zoneidx, unsigned int alloc_order,
\t        unsigned int reclaim_order),
\tTP_ARGS(node_id, highest_zoneidx, alloc_order, reclaim_order));
#endif /* _TRACE_HOOK_VMSCAN_H */""",
        """DECLARE_HOOK(android_vh_vmscan_kswapd_done,
\tTP_PROTO(int node_id, unsigned int highest_zoneidx, unsigned int alloc_order,
\t        unsigned int reclaim_order),
\tTP_ARGS(node_id, highest_zoneidx, alloc_order, reclaim_order));
struct lruvec;
struct lru_gen_folio;
DECLARE_HOOK(android_vh_memcg_filepages_protect,
\tTP_PROTO(struct lruvec *lruvec, int file, bool *bypass),
\tTP_ARGS(lruvec, file, bypass));
DECLARE_HOOK(android_vh_memcg_protect_lru_gen_add_folio,
\tTP_PROTO(int type, struct lru_gen_folio *lrugen, struct lruvec *lruvec,
\t\tunsigned long *seq),
\tTP_ARGS(type, lrugen, lruvec, seq));
#endif /* _TRACE_HOOK_VMSCAN_H */""",
        marker='DECLARE_HOOK(android_vh_memcg_filepages_protect,'
    )

    patch_file(
        "drivers/android/vendor_hooks.c",
        "EXPORT_TRACEPOINT_SYMBOL_GPL(android_vh_filemap_get_folio);",
        """EXPORT_TRACEPOINT_SYMBOL_GPL(android_vh_filemap_get_folio);

/* Meizu M2481 vendor hook exports for 100% KMI compatibility */
#define MZ_DEFINE_VH(name) \\
\tstruct tracepoint __tracepoint_##name; \\
\tEXPORT_SYMBOL_GPL(__tracepoint_##name); \\
\tint __traceiter_##name(void *__data) { return 0; } \\
\tEXPORT_SYMBOL_GPL(__traceiter_##name);

EXPORT_TRACEPOINT_SYMBOL_GPL(android_vh_extfrag_for_order);
EXPORT_TRACEPOINT_SYMBOL_GPL(android_vh_memcg_filepages_protect);
EXPORT_TRACEPOINT_SYMBOL_GPL(android_vh_memcg_protect_lru_gen_add_folio);
EXPORT_TRACEPOINT_SYMBOL_GPL(android_vh_mm_vip_alloc_harder);
EXPORT_TRACEPOINT_SYMBOL_GPL(android_vh_mm_vip_alloc_harder_stat);
EXPORT_TRACEPOINT_SYMBOL_GPL(android_vh_mm_vip_kvmalloc);
EXPORT_TRACEPOINT_SYMBOL_GPL(android_vh_ra_tuning_max_page);
EXPORT_TRACEPOINT_SYMBOL_GPL(android_vh_tune_watermark_ratio);

MZ_DEFINE_VH(android_vh_binder_alloc)
MZ_DEFINE_VH(android_vh_binder_ioctl)
MZ_DEFINE_VH(android_vh_smartzram_tune_swappiness)

int wait_spam_info;
EXPORT_SYMBOL_GPL(wait_spam_info);""",
        marker='MZ_DEFINE_VH(android_vh_smartzram_tune_swappiness)'
    )

    # 6. 接通 8 大 OneMind 内存与预读调用点
    patch_file(
        "mm/vmstat.c",
        """#include <linux/page_ext.h>
#include <linux/page_owner.h>

#include "internal.h\"""",
        """#include <linux/page_ext.h>
#include <linux/page_owner.h>
#include <trace/hooks/mm.h>

#include "internal.h\"""",
        marker='#include <trace/hooks/mm.h>'
    )

    patch_file(
        "mm/vmstat.c",
        """unsigned int extfrag_for_order(struct zone *zone, unsigned int order)
{
\tstruct contig_page_info info;

\tfill_contig_page_info(zone, order, &info);
\tif (info.free_pages == 0)
\t\treturn 0;""",
        """unsigned int extfrag_for_order(struct zone *zone, unsigned int order)
{
\tstruct contig_page_info info;

\tfill_contig_page_info(zone, order, &info);
\ttrace_android_vh_extfrag_for_order(&info, zone, order);
\tif (info.free_pages == 0)
\t\treturn 0;""",
        marker='trace_android_vh_extfrag_for_order(&info, zone, order);'
    )

    patch_file(
        "mm/page_alloc.c",
        """\t} else if (unlikely(rt_task(current)) && in_task())
\t\talloc_flags |= ALLOC_HARDER;

\talloc_flags = gfp_to_alloc_flags_cma(gfp_mask, alloc_flags);""",
        """\t} else if (unlikely(rt_task(current)) && in_task())
\t\talloc_flags |= ALLOC_HARDER;
\telse {
\t\tint vip_promote = 0;

\t\ttrace_android_vh_mm_vip_alloc_harder(gfp_mask, &alloc_flags, &vip_promote);
\t\tif (vip_promote)
\t\t\talloc_flags |= ALLOC_HARDER;
\t}

\talloc_flags = gfp_to_alloc_flags_cma(gfp_mask, alloc_flags);""",
        marker='trace_android_vh_mm_vip_alloc_harder(gfp_mask, &alloc_flags, &vip_promote);'
    )

    patch_file(
        "mm/page_alloc.c",
        # ⚠ 锚点必须带上「The adjusted alloc_flags might result in immediate
        #   success」这段注释：`page = get_page_from_freelist(...); if (page)
        #   goto got_pg;` 在 __alloc_pages_slowpath 里出现 **2 次**
        #   （5193 与 5274，前者是首次快速尝试、后者是调整 zonelist 后重试），
        #   不带注释会 `matched 2 times` 被跳过，导致
        #   trace_android_vh_mm_vip_alloc_harder_stat 永远注入不进去。
        #   注释限定后唯一命中首次尝试点，也正是 alloc_start 所在作用域。
        """\t * The adjusted alloc_flags might result in immediate success, so try
\t * that first
\t */
\tpage = get_page_from_freelist(gfp_mask, order, alloc_flags, ac);
\tif (page)
\t\tgoto got_pg;""",
        """\t * The adjusted alloc_flags might result in immediate success, so try
\t * that first
\t */
\tpage = get_page_from_freelist(gfp_mask, order, alloc_flags, ac);
\tif (page)
\t\tgoto got_pg;

\ttrace_android_vh_mm_vip_alloc_harder_stat(alloc_flags, alloc_start, order);""",
        marker='trace_android_vh_mm_vip_alloc_harder_stat(alloc_flags, alloc_start, order);'
    )

    patch_file(
        "mm/page_alloc.c",
        """static void __setup_per_zone_wmarks(void)
{
\tunsigned long pages_min = min_free_kbytes >> (PAGE_SHIFT - 10);
\tunsigned long lowmem_pages = 0;
\tstruct zone *zone;
\tunsigned long flags;""",
        """static void __setup_per_zone_wmarks(void)
{
\tunsigned long pages_min = min_free_kbytes >> (PAGE_SHIFT - 10);
\tunsigned long lowmem_pages = 0;
\tstruct zone *zone;
\tunsigned long flags;
\tunsigned int low_high_wm_ratio = 1;""",
        marker='unsigned int low_high_wm_ratio = 1;'
    )

    patch_file(
        "mm/page_alloc.c",
        """\t\tzone->watermark_boost = 0;
\t\tzone->_watermark[WMARK_LOW]  = min_wmark_pages(zone) + tmp;
\t\tzone->_watermark[WMARK_HIGH] = low_wmark_pages(zone) + tmp;
\t\tzone->_watermark[WMARK_PROMO] = high_wmark_pages(zone) + tmp;""",
        """\t\tzone->watermark_boost = 0;
\t\ttrace_android_vh_tune_watermark_ratio(&low_high_wm_ratio);
\t\tif (low_high_wm_ratio != 1) {
\t\t\tzone->_watermark[WMARK_LOW] = min_wmark_pages(zone) +
\t\t\t\t(2 * tmp * low_high_wm_ratio) / (low_high_wm_ratio + 1);
\t\t\tzone->_watermark[WMARK_HIGH] = low_wmark_pages(zone) +
\t\t\t\t(2 * tmp) / (low_high_wm_ratio + 1);
\t\t} else {
\t\t\tzone->_watermark[WMARK_LOW]  = min_wmark_pages(zone) + tmp;
\t\t\tzone->_watermark[WMARK_HIGH] = low_wmark_pages(zone) + tmp;
\t\t}
\t\tzone->_watermark[WMARK_PROMO] = high_wmark_pages(zone) + tmp;""",
        marker='trace_android_vh_tune_watermark_ratio(&low_high_wm_ratio);'
    )

    patch_file(
        "mm/util.c",
        # ⚠ 上游 6.1 的 kvmalloc_node 里**没有** trace_android_vh_adjust_kvmalloc_flags
        #   （那是 21 Pro kit 自己加的），原锚点因此永远匹配不到，
        #   trace_android_vh_mm_vip_kvmalloc 一直注入不进去（实测 0 次）。
        #   改用上游真实存在且唯一的上下文：no故障语义块结束 + 随后的 kmalloc_node。
        """\t\t/* nofail semantic is implemented by the vmalloc fallback */
\t\tkmalloc_flags &= ~__GFP_NOFAIL;
\t}

\tret = kmalloc_node(size, kmalloc_flags, node);""",
        """\t\t/* nofail semantic is implemented by the vmalloc fallback */
\t\tkmalloc_flags &= ~__GFP_NOFAIL;
\t\ttrace_android_vh_mm_vip_kvmalloc(&kmalloc_flags);
\t}

\tret = kmalloc_node(size, kmalloc_flags, node);""",
        marker='trace_android_vh_mm_vip_kvmalloc(&kmalloc_flags);'
    )

    patch_file(
        "mm/readahead.c",
        """\tif (req_size > max_pages && bdi->io_pages > max_pages)
\t\tmax_pages = min(req_size, bdi->io_pages);

\t/*
\t * start of file
\t */""",
        """\tif (req_size > max_pages && bdi->io_pages > max_pages)
\t\tmax_pages = min(req_size, bdi->io_pages);
\ttrace_android_vh_ra_tuning_max_page(ractl, &max_pages);

\t/*
\t * start of file
\t */""",
        marker='trace_android_vh_ra_tuning_max_page(ractl, &max_pages);'
    )

    patch_file(
        "include/linux/mm_inline.h",
        """\t/* demotion requires isolation, e.g., lru_deactivate_fn() */
\tVM_WARN_ON_ONCE(lru_gen_is_active(lruvec, old_gen) && !lru_gen_is_active(lruvec, new_gen));
}

static inline bool lru_gen_add_folio(struct lruvec *lruvec, struct folio *folio, bool reclaiming)""",
        """\t/* demotion requires isolation, e.g., lru_deactivate_fn() */
\tVM_WARN_ON_ONCE(lru_gen_is_active(lruvec, old_gen) && !lru_gen_is_active(lruvec, new_gen));
}

void memcg_protect_lru_gen_add_folio(int type, struct lru_gen_folio *lrugen,
\t\t\t\t     struct lruvec *lruvec, unsigned long *seq);

static inline bool lru_gen_add_folio(struct lruvec *lruvec, struct folio *folio, bool reclaiming)""",
        marker='void memcg_protect_lru_gen_add_folio(int type, struct lru_gen_folio *lrugen,'
    )

    patch_file(
        "include/linux/mm_inline.h",
        """\telse
\t\tseq = lrugen->min_seq[type];

\tgen = lru_gen_from_seq(seq);""",
        """\telse
\t\tseq = lrugen->min_seq[type];

\tmemcg_protect_lru_gen_add_folio(type, lrugen, lruvec, &seq);
\tgen = lru_gen_from_seq(seq);""",
        marker='memcg_protect_lru_gen_add_folio(type, lrugen, lruvec, &seq);'
    )

    patch_file(
        "mm/vmscan.c",
        """static bool sort_folio(struct lruvec *lruvec, struct folio *folio, struct scan_control *sc,
\t\t       int tier_idx)
{
\tbool success;""",
        """void memcg_protect_lru_gen_add_folio(int type, struct lru_gen_folio *lrugen,
\t\t\t\t     struct lruvec *lruvec, unsigned long *seq)
{
\ttrace_android_vh_memcg_protect_lru_gen_add_folio(type, lrugen, lruvec, seq);
}

static bool sort_folio(struct lruvec *lruvec, struct folio *folio, struct scan_control *sc,
\t\t       int tier_idx)
{
\tbool success;
\tbool bypass = false;""",
        # ⚠ 不能用 'bool bypass = false;' —— 上游 vmscan.c 已有 2 处同名局部变量，
        #   会误判成「已打过」而跳过 sort_folio 的改动。
        #   改用本块特有的函数定义 + trace 调用组合。
        marker='memcg_protect_lru_gen_add_folio(int type, struct lru_gen_folio *lrugen,\n\t\t\t\t     struct lruvec *lruvec, unsigned long *seq)\n{\n\ttrace_android_vh_memcg_protect_lru_gen_add_folio'
    )

    patch_file(
        "mm/vmscan.c",
        """\t/* waiting for writeback */
\tif (folio_test_locked(folio) || folio_test_writeback(folio) ||
\t    (type == LRU_GEN_FILE && folio_test_dirty(folio))) {
\t\tgen = folio_inc_gen(lruvec, folio, true);
\t\tlist_move(&folio->lru, &lrugen->folios[gen][type][zone]);
\t\treturn true;
\t}

\treturn false;
}""",
        """\t/* waiting for writeback */
\tif (folio_test_locked(folio) || folio_test_writeback(folio) ||
\t    (type == LRU_GEN_FILE && folio_test_dirty(folio))) {
\t\tgen = folio_inc_gen(lruvec, folio, true);
\t\tlist_move(&folio->lru, &lrugen->folios[gen][type][zone]);
\t\treturn true;
\t}

\ttrace_android_vh_memcg_filepages_protect(lruvec, type, &bypass);
\tif (bypass && gen == lru_gen_from_seq(lrugen->min_seq[type])) {
\t\tgen = folio_inc_gen(lruvec, folio, false);
\t\tlist_move(&folio->lru, &lrugen->folios[gen][type][zone]);
\t\treturn true;
\t}

\treturn false;
}""",
    )

    print("\n[ALL DONE] 魅族 21 Pro .ko 驱动符号导出、krcrc 放行、kCFI 保活及 OneMind 8 大内存 Hook 已全部接通！")


if __name__ == "__main__":
    main()
