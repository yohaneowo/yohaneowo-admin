<!-- AI 费用对账：每天（UTC）各模型的 Anthropic 实际帐单 vs AI 调用日志的记录 -->
<template>
  <div>
    <ElAlert
      v-if="summary && !summary.configured"
      type="warning"
      :closable="false"
      class="mb-3"
      title="还没设定 ANTHROPIC_ADMIN_API_KEY"
      description="对账需要 Anthropic 的 Admin API key（Console → Settings → Admin keys），放进后端的 env 设定后重启。"
    />

    <ElCard shadow="never" class="mb-3">
      <div class="flex flex-wrap items-center gap-3">
        <ElButton
          v-hasPerm="['module_ai_usage:reconcile:run']"
          type="primary"
          :loading="running"
          :disabled="summary ? !summary.configured : true"
          @click="handleRun"
        >
          立即对账
        </ElButton>
        <ElRadioGroup v-model="statusFilter" @change="load">
          <ElRadioButton value="">全部</ElRadioButton>
          <ElRadioButton value="problems">只看不一致</ElRadioButton>
        </ElRadioGroup>
        <span class="text-sm text-g-500">
          上次对账：{{ summary?.last_checked_time ?? "还没对过" }}
          <template v-if="summary?.first_day">
            · 从 {{ summary.first_day }}（UTC）开始对账</template
          >
        </span>
      </div>
      <p class="m-0 mt-2 text-xs text-g-500">
        日期是 UTC，跟 Anthropic 帐单的分日一致；只对到昨天，每次重新对最近 3
        天（帐单资料会延迟几小时才完整）。 金额差在 5% 或 1 美分以内、token 差在 2%
        以内算一致。Voyage 没有费用 API，不在这里对账。
      </p>
    </ElCard>

    <div v-loading="loading" class="grid grid-cols-1 gap-3 md:grid-cols-3 mb-3">
      <div class="fa-card px-5 py-4">
        <p class="m-0 text-sm text-g-500">本月实际帐单（{{ summary?.month ?? "—" }}，UTC）</p>
        <p class="m-0 mt-1 text-2xl font-medium">
          {{ summary ? formatUsd(summary.actual_cost_usd) : "—" }}
        </p>
      </div>
      <div class="fa-card px-5 py-4">
        <p class="m-0 text-sm text-g-500">本月 AI 调用日志记录</p>
        <p class="m-0 mt-1 text-2xl font-medium">
          {{ summary ? formatUsd(summary.logged_cost_usd) : "—" }}
        </p>
      </div>
      <div class="fa-card px-5 py-4">
        <p class="m-0 text-sm text-g-500">本月不一致</p>
        <p
          class="m-0 mt-1 text-2xl font-medium"
          :class="summary?.problem_count ? 'text-danger' : 'text-success'"
        >
          {{ summary ? `${summary.problem_count} 笔` : "—" }}
        </p>
      </div>
    </div>

    <ElCard shadow="never">
      <ElTable v-loading="loading" :data="rows" size="default">
        <template #empty>
          <span class="text-g-500">
            {{
              summary?.first_day
                ? "还没有对账结果"
                : "AI 调用日志还没有 Anthropic 的记录，对账从第一笔记录那天开始"
            }}
          </span>
        </template>
        <ElTableColumn prop="day" label="日期（UTC）" width="120" />
        <ElTableColumn prop="model" label="模型" min-width="200" show-overflow-tooltip />
        <ElTableColumn label="结果" width="150">
          <template #default="{ row }">
            <ElTooltip :content="STATUS[row.status as AiReconcileStatus].hint" placement="top">
              <ElTag :type="STATUS[row.status as AiReconcileStatus].type">
                {{ STATUS[row.status as AiReconcileStatus].text }}
              </ElTag>
            </ElTooltip>
          </template>
        </ElTableColumn>
        <ElTableColumn label="金额（实际 / 记录）" width="190">
          <template #default="{ row }">
            {{ formatUsd(row.actual_cost_usd) }} / {{ formatUsd(row.logged_cost_usd) }}
            <span
              v-if="costDiff(row.actual_cost_usd, row.logged_cost_usd)"
              class="ml-1 text-xs"
              :class="diffClass(row.status)"
              >{{ costDiff(row.actual_cost_usd, row.logged_cost_usd) }}</span
            >
          </template>
        </ElTableColumn>
        <ElTableColumn label="输入 token（实际 / 记录）" width="200">
          <template #default="{ row }">
            {{ formatTokens(row.actual_input_tokens) }} /
            {{ formatTokens(row.logged_input_tokens) }}
          </template>
        </ElTableColumn>
        <ElTableColumn label="输出 token（实际 / 记录）" width="200">
          <template #default="{ row }">
            {{ formatTokens(row.actual_output_tokens) }} /
            {{ formatTokens(row.logged_output_tokens) }}
          </template>
        </ElTableColumn>
        <ElTableColumn label="快取（实际 / 记录）" width="170">
          <template #default="{ row }">
            {{ formatTokens(row.actual_cache_read_tokens + row.actual_cache_write_tokens) }} /
            {{ formatTokens(row.logged_cache_read_tokens + row.logged_cache_write_tokens) }}
          </template>
        </ElTableColumn>
        <ElTableColumn label="记录次数" width="100">
          <template #default="{ row }">
            {{ row.logged_call_count }}
            <span v-if="row.logged_unpriced_count" class="text-xs text-warning">
              （{{ row.logged_unpriced_count }} 未定价）
            </span>
          </template>
        </ElTableColumn>
        <ElTableColumn prop="checked_time" label="对账时间" width="170" />
      </ElTable>
    </ElCard>
  </div>
</template>

<script setup lang="ts">
import { ElMessage } from "element-plus";
import AiUsageAPI, {
  type AiCostReconcileRow,
  type AiReconcileStatus,
  type AiReconcileSummary,
} from "@/api/module_ai_usage";
import { formatTokens, formatUsd } from "../shared";

defineOptions({ name: "AiCostReconcile" });

const STATUS: Record<
  AiReconcileStatus,
  { text: string; type: "success" | "warning" | "danger"; hint: string }
> = {
  ok: { text: "一致", type: "success", hint: "token 与金额都在误差范围内" },
  price_mismatch: {
    text: "单价不对",
    type: "warning",
    hint: "token 一致但金额不同：到「模型价格」修正这个模型的单价并锁定；有未定价的调用也会这样",
  },
  missing_logs: {
    text: "有调用没记录",
    type: "danger",
    hint: "帐单的 token 比记录多：有功能没经过 recordAiCall，或同一个帐号还有别的程序在用",
  },
  extra_logs: {
    text: "记录比实际多",
    type: "warning",
    hint: "记录的 token 比帐单多：可能是跨日的调用被算到不同天，或帐单还没更新完",
  },
};

const rows = ref<AiCostReconcileRow[]>([]);
const summary = ref<AiReconcileSummary | null>(null);
const statusFilter = ref<"" | "problems">("");
const loading = ref(false);
const running = ref(false);

function costDiff(actualCost: string, loggedCost: string) {
  const actual = Number(actualCost);
  const logged = Number(loggedCost);
  if (actual === logged) return "";
  if (actual === 0) return "实际为 0";
  const percent = ((logged - actual) / actual) * 100;
  return `${percent > 0 ? "+" : ""}${percent.toFixed(1)}%`;
}

function diffClass(status: string) {
  return status === "ok" ? "text-g-500" : "text-danger";
}

async function load() {
  loading.value = true;
  try {
    const [list, total] = await Promise.all([
      AiUsageAPI.listReconcile(),
      AiUsageAPI.reconcileSummary(),
    ]);
    const all = list.data.data ?? [];
    rows.value = statusFilter.value === "problems" ? all.filter((row) => row.status !== "ok") : all;
    summary.value = total.data.data ?? null;
  } finally {
    loading.value = false;
  }
}

async function handleRun() {
  running.value = true;
  try {
    const res = await AiUsageAPI.runReconcile();
    const r = res.data.data;
    ElMessage.success(
      r.days.length
        ? `对账完成：${r.days.length} 天、${r.rows} 笔，其中 ${r.problems} 笔不一致`
        : "还没有可以对账的日子（AI 调用日志的记录要到隔天才对得到）"
    );
    await load();
  } finally {
    running.value = false;
  }
}

onMounted(load);
</script>
