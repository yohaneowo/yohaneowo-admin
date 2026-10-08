<!-- 首页总资产卡片：bot 每 15 分钟上报的交易所资产，TWD 为主、USDT 为辅；需 module_bot:portfolio:query -->
<template>
  <!-- 首页这一栏很窄（约 180px），不放图标，USDT 与涨跌分两行 -->
  <div
    class="fa-card h-32 flex flex-col justify-center px-5 transition-transform duration-200 hover:-translate-y-0.5 bg-theme/10!"
  >
    <p class="m-0 text-base font-medium flex items-center gap-1" style="color: var(--theme-color)">
      总资产
      <ElTooltip v-if="failures.length" placement="top">
        <template #content>
          以下账户读取失败，总额没有算进去：<br />{{ failures.join("、") }}
        </template>
        <FaSvgIcon icon="ri:error-warning-line" class="text-base text-warning" />
      </ElTooltip>
      <span v-if="latest" class="ml-auto text-xs font-normal text-g-500">{{ updatedAgo }}</span>
    </p>
    <template v-if="latest">
      <div class="flex items-baseline gap-1 whitespace-nowrap">
        <span class="text-xs font-medium">NT$</span>
        <FaCountTo
          class="m-0 text-xl font-medium"
          :target="latest.total_twd ?? 0"
          :duration="1500"
          :decimals="0"
          separator=","
        />
      </div>
      <p class="mt-1 mb-0 text-xs text-g-500 truncate">
        USDT {{ formatNumber(latest.total_usdt, 2) }}
      </p>
      <p
        v-if="changePercent != null"
        class="m-0 text-xs"
        :class="changePercent >= 0 ? 'text-success' : 'text-danger'"
      >
        24h {{ changePercent >= 0 ? "+" : "" }}{{ changePercent.toFixed(2) }}%
      </p>
    </template>
    <p v-else class="mt-1 mb-0 text-sm text-g-500">
      {{ loaded ? "还没有数据，等 bot 上报" : "加载中…" }}
    </p>
  </div>
</template>

<script setup lang="ts">
import BotPortfolioAPI, { type BotPortfolioSnapshot } from "@/api/module_bot/portfolio";
import { useAutoRefresh } from "../shared";

defineOptions({ name: "PortfolioStatsCard" });

const latest = ref<BotPortfolioSnapshot | null>(null);
const changePercent = ref<number | null>(null);
const loaded = ref(false);
// 用来让「几分钟前」随时间更新
const now = ref(Date.now());

const failures = computed(() => latest.value?.failures ?? []);

const updatedAgo = computed(() => {
  if (!latest.value?.occurred_time) return "";
  // 后端时间按 UTC 存、不带时区，补上 Z 再解析
  const at = new Date(`${latest.value.occurred_time.replace(" ", "T")}Z`).getTime();
  const minutes = Math.max(0, Math.round((now.value - at) / 60000));
  if (minutes < 1) return "刚刚更新";
  if (minutes < 60) return `${minutes} 分钟前`;
  const hours = Math.floor(minutes / 60);
  return hours < 24 ? `${hours} 小时前` : `${Math.floor(hours / 24)} 天前`;
});

function formatNumber(value: number | undefined, decimals: number) {
  return (value ?? 0).toLocaleString("en-US", {
    minimumFractionDigits: decimals,
    maximumFractionDigits: decimals,
  });
}

async function load() {
  now.value = Date.now();
  try {
    const res = await BotPortfolioAPI.latestBotPortfolio();
    latest.value = res.data.data?.latest ?? null;
    changePercent.value = res.data.data?.change_percent ?? null;
  } catch {
    // 卡片只是概览，失败时保留上一次的数据
  } finally {
    loaded.value = true;
  }
}

const { autoRefresh } = useAutoRefresh(load, 60 * 1000);
autoRefresh.value = true;
onMounted(load);
</script>
