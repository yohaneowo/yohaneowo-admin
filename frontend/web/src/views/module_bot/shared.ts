// Bot管理各页面共用的选项、标签、网站筛选与自动刷新
import { onActivated, onDeactivated, onUnmounted, ref, watch, type Ref } from "vue";
import type { SiteCount } from "@/api/module_bot/parse_log";

export const PLATFORM_OPTIONS = [
  { label: "Discord", value: "discord" },
  { label: "LINE", value: "line" },
];

export const PLATFORM_TAG = {
  discord: { type: "primary", text: "Discord" },
  line: { type: "success", text: "LINE" },
} as const;

// 与 bot 端 services/media.js 的 SUPPORTED_SITES 对应。bot 新增网站而这里还没加时，
// SiteFilterBar 也会按日志里出现的网站名自动补上按钮（排在最后）
export const SITE_OPTIONS = [
  "TikTok",
  "Facebook",
  "Instagram",
  "小红书",
  "YouTube",
  "Threads",
  "X",
].map((site) => ({ label: site, value: site }));

// 网站按钮的「全部」（不带 site 参数）与「其他」（只看没有网站的日志，与后端 OTHER_SITE 对应）
export const ALL_SITES = "";
export const OTHER_SITE = "__other__";

/**
 * 日志页的网站按钮：选中的网站并进查询条件；每次表格加载完（含自动刷新）用 loadSiteCounts
 * 按同样的条件（不限网站）重新统计各按钮上的条数，数量与点下去看到的条数一致。
 */
export function useSiteFilter<TForm extends object>(options: {
  searchForm: Ref<TForm>;
  /** useTable 当前的查询参数 */
  searchParams: Record<string, unknown>;
  replaceSearchParams: (params: Record<string, unknown>) => void;
  getData: () => Promise<unknown>;
  statsApi: (query: Record<string, unknown>) => Promise<{ data: { data?: SiteCount[] } }>;
}) {
  const site = ref(ALL_SITES);
  const siteCounts = ref<SiteCount[]>([]);

  async function applySearch(form: TForm = options.searchForm.value) {
    options.replaceSearchParams({ ...form, site: site.value || undefined });
    await options.getData();
  }

  async function changeSite(value: string) {
    site.value = value;
    await applySearch();
  }

  async function loadSiteCounts() {
    const query = { ...options.searchParams };
    for (const key of ["page_no", "page_size", "site"]) delete query[key];
    try {
      const res = await options.statsApi(query);
      siteCounts.value = res.data.data ?? [];
    } catch {
      // 数量只是辅助信息，失败时保留上一次的结果
    }
  }

  return { site, siteCounts, applySearch, changeSite, loadSiteCounts };
}

/**
 * 列表自动刷新。页面在 KeepAlive 缓存里时 deactivated 停掉计时器，activated 再恢复，
 * 避免切到别的标签后仍在后台轮询。
 */
export function useAutoRefresh(refresh: () => unknown, intervalMs = 5000) {
  const enabled = ref(false);
  let timer: ReturnType<typeof setInterval> | undefined;

  function stop() {
    if (timer) clearInterval(timer);
    timer = undefined;
  }

  function start() {
    stop();
    if (enabled.value) timer = setInterval(() => void refresh(), intervalMs);
  }

  watch(enabled, start);
  onActivated(start);
  onDeactivated(stop);
  onUnmounted(stop);

  return { autoRefresh: enabled };
}
