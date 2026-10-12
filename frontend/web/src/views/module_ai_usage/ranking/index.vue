<!-- AI 费用排行：按用户 / 项目 / 功能 / 模型加总费用；多人共用的调用记在「系统（共用）」 -->
<template>
  <div>
    <ElCard shadow="never" class="mb-3">
      <div class="flex flex-wrap items-center gap-3">
        <ElRadioGroup v-model="groupBy" @change="load">
          <ElRadioButton v-for="g in GROUPS" :key="g.value" :value="g.value">
            {{ g.label }}
          </ElRadioButton>
        </ElRadioGroup>
        <ElRadioGroup v-model="period" @change="load">
          <ElRadioButton v-for="p in PERIODS" :key="p.value" :value="p.value">
            {{ p.label }}
          </ElRadioButton>
        </ElRadioGroup>
        <ElSelect
          v-model="project"
          placeholder="全部项目"
          clearable
          filterable
          class="w-56!"
          @change="load"
        >
          <ElOption v-for="p in projects" :key="p" :label="p" :value="p" />
        </ElSelect>
        <ElButton :loading="loading" @click="load">刷新</ElButton>
      </div>
    </ElCard>

    <div v-loading="loading" class="grid grid-cols-2 gap-3 md:grid-cols-3 mb-3">
      <div class="fa-card px-5 py-4">
        <p class="m-0 text-sm text-g-500">总费用</p>
        <p class="m-0 mt-1 text-2xl font-medium">
          {{ summary ? formatUsd(summary.total_cost_usd) : "—" }}
        </p>
        <p v-if="summary?.unpriced_count" class="m-0 mt-1 text-xs text-g-500">
          另有 {{ summary.unpriced_count }} 笔未定价，没算进去
        </p>
      </div>
      <div class="fa-card px-5 py-4">
        <p class="m-0 text-sm text-g-500">调用次数</p>
        <p class="m-0 mt-1 text-2xl font-medium">{{ summary?.call_count ?? "—" }}</p>
      </div>
      <div class="fa-card px-5 py-4">
        <p class="m-0 text-sm text-g-500">token（入 / 出）</p>
        <p class="m-0 mt-1 text-2xl font-medium">
          {{ formatTokens(summary?.input_tokens) }} / {{ formatTokens(summary?.output_tokens) }}
        </p>
      </div>
    </div>

    <ElCard shadow="never">
      <ElTable v-loading="loading" :data="rows" size="default">
        <template #empty>
          <span class="text-g-500">这段时间没有 AI 调用</span>
        </template>
        <ElTableColumn label="#" width="60">
          <template #default="{ $index }">
            <span :class="$index < 3 ? 'font-bold text-warning' : ''">{{ $index + 1 }}</span>
          </template>
        </ElTableColumn>
        <ElTableColumn :label="GROUPS.find((g) => g.value === groupBy)?.label" min-width="200">
          <template #default="{ row }">
            <component :is="renderCaller(row)" v-if="groupBy === 'user'" />
            <span v-else>{{ row.key }}</span>
          </template>
        </ElTableColumn>
        <ElTableColumn v-if="groupBy === 'user'" label="平台" width="120">
          <template #default="{ row }">{{
            PLATFORM_LABEL[row.user_platform] ?? row.user_platform
          }}</template>
        </ElTableColumn>
        <ElTableColumn label="费用" width="120">
          <template #default="{ row }">{{ formatUsd(row.total_cost_usd) }}</template>
        </ElTableColumn>
        <ElTableColumn label="占比" width="160">
          <template #default="{ row }">
            <ElProgress :percentage="share(row.total_cost_usd)" :stroke-width="10" />
          </template>
        </ElTableColumn>
        <ElTableColumn prop="call_count" label="调用次数" width="100" />
        <ElTableColumn label="token（入 / 出）" width="140">
          <template #default="{ row }">
            {{ formatTokens(row.input_tokens) }} / {{ formatTokens(row.output_tokens) }}
          </template>
        </ElTableColumn>
        <ElTableColumn prop="last_time" label="最近一次" width="170" />
      </ElTable>
    </ElCard>
  </div>
</template>

<script setup lang="ts">
import AiUsageAPI, {
  type AiCallLogFilters,
  type AiCallSummary,
  type AiCostRankingRow,
  type AiRankingGroup,
} from "@/api/module_ai_usage";
import { PLATFORM_LABEL, formatTokens, formatUsd, renderCaller } from "../shared";

defineOptions({ name: "AiCostRanking" });

const GROUPS: { label: string; value: AiRankingGroup }[] = [
  { label: "用户", value: "user" },
  { label: "项目", value: "project" },
  { label: "功能", value: "feature" },
  { label: "模型", value: "model" },
];
const PERIODS = [
  { label: "本月", value: "month" },
  { label: "近 7 天", value: "7d" },
  { label: "近 30 天", value: "30d" },
  { label: "全部", value: "all" },
] as const;

const groupBy = ref<AiRankingGroup>("user");
const period = ref<(typeof PERIODS)[number]["value"]>("month");
const project = ref("");
// 项目选项取自调用记录里出现过的值
const projects = ref<string[]>([]);
const rows = ref<AiCostRankingRow[]>([]);
const summary = ref<AiCallSummary | null>(null);
const loading = ref(false);

function formatDateTime(date: Date) {
  const pad = (n: number) => String(n).padStart(2, "0");
  return `${date.getFullYear()}-${pad(date.getMonth() + 1)}-${pad(date.getDate())} ${pad(date.getHours())}:${pad(date.getMinutes())}:${pad(date.getSeconds())}`;
}

function filters(): AiCallLogFilters {
  const now = new Date();
  const start =
    period.value === "month"
      ? new Date(now.getFullYear(), now.getMonth(), 1)
      : period.value === "7d"
        ? new Date(now.getTime() - 7 * 86400000)
        : period.value === "30d"
          ? new Date(now.getTime() - 30 * 86400000)
          : null;
  return {
    ...(start ? { occurred_time: [formatDateTime(start), formatDateTime(now)] } : {}),
    ...(project.value ? { project: project.value } : {}),
  };
}

function share(cost: string) {
  const total = Number(summary.value?.total_cost_usd ?? 0);
  return total > 0 ? Math.round((Number(cost) / total) * 1000) / 10 : 0;
}

async function load() {
  loading.value = true;
  try {
    const query = filters();
    const [ranking, totals] = await Promise.all([
      AiUsageAPI.ranking({ ...query, group_by: groupBy.value, limit: 50 }),
      AiUsageAPI.summaryCallLog(query),
    ]);
    rows.value = ranking.data.data ?? [];
    summary.value = totals.data.data ?? null;
  } finally {
    loading.value = false;
  }
}

onMounted(async () => {
  await load();
  projects.value = (await AiUsageAPI.filtersCallLog()).data.data?.projects ?? [];
});
</script>
