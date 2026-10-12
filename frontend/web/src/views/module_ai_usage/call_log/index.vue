<!-- AI 调用日志：各项目每调用一次 AI 一条（谁、哪个功能、哪个模型、token、费用），只读，永久保留 -->
<template>
  <div class="fa-full-height">
    <FaSearchBar
      v-show="showSearchBar"
      ref="searchBarRef"
      v-model="searchForm"
      :items="searchItems"
      :is-expand="false"
      :show-expand="true"
      :show-reset="true"
      :show-search="true"
      :disabled-search="false"
      :default-expanded="false"
      @search="handleSearchBarSearch"
      @reset="onResetSearch"
    />

    <ElCard class="fa-table-card" :style="{ 'margin-top': showSearchBar ? '12px' : '0' }">
      <!-- 合计跟着上面的筛选条件一起变 -->
      <div class="grid grid-cols-2 gap-3 md:grid-cols-4 mb-3">
        <div v-for="card in summaryCards" :key="card.label" class="fa-card px-4 py-3">
          <p class="m-0 text-xs text-g-500">{{ card.label }}</p>
          <p class="m-0 mt-1 text-xl font-medium">{{ card.value }}</p>
          <p v-if="card.hint" class="m-0 mt-1 text-xs text-g-500">{{ card.hint }}</p>
        </div>
      </div>

      <FaTableHeader
        v-model:columns="columnChecks"
        v-model:showSearchBar="showSearchBar"
        :loading="loading"
        @refresh="refreshData"
      >
        <template #left>
          <ElSwitch v-model="autoRefresh" active-text="每 5 秒自动刷新" />
        </template>
      </FaTableHeader>

      <FaTable
        :loading="loading"
        :data="data"
        :columns="columns"
        :pagination="pagination"
        @pagination:size-change="handleSizeChange"
        @pagination:current-change="handleCurrentChange"
      />
    </ElCard>

    <FaDialog
      v-model="dialogVisible.visible"
      :title="dialogVisible.title"
      width="760px"
      dialog-class="crud-embed-dialog"
      modal-class="crud-embed-dialog"
      :form-mode="dialogVisible.type"
      @cancel="handleCloseDialog"
      @close="handleCloseDialog"
    >
      <FaDescriptions
        :column="2"
        :data="detailFormData"
        :items="detailItems"
        label-width="100px"
        max-height="70vh"
      >
        <template #user_name>
          <component :is="renderCaller(detailFormData)" />
        </template>
        <template #cost_usd>{{ formatUsd(detailFormData.cost_usd) }}</template>
        <template #error_message>
          <pre class="m-0 whitespace-pre-wrap break-all text-xs">{{
            detailFormData.error_message || "—"
          }}</pre>
        </template>
      </FaDescriptions>
    </FaDialog>
  </div>
</template>

<script setup lang="ts">
import { useCrudForm } from "@/hooks/core/useCrudForm";
import AiUsageAPI, {
  type AiCallFilterOptions,
  type AiCallLogFilters,
  type AiCallLogTable,
  type AiCallSummary,
} from "@/api/module_ai_usage";
import { renderTableOperationCell, resolveStatusColumns } from "@utils";
import type { SearchFormItem } from "@/components/forms/fa-search-bar/index.vue";
import FaSearchBar from "@/components/forms/fa-search-bar/index.vue";
import FaForm from "@/components/forms/fa-form/index.vue";
import FaTableHeader from "@/components/tables/fa-table-header/index.vue";
import FaDescriptions, {
  type DescriptionsItem,
} from "@/components/display/fa-descriptions/index.vue";
import { useAutoRefresh } from "../../module_bot/shared";
import { PLATFORM_LABEL, formatTokens, formatUsd, providerLabel, renderCaller } from "../shared";

defineOptions({
  name: "AiCallLog",
  inheritAttrs: false,
});

const SUCCESS_TAG = {
  true: { type: "success", text: "成功" },
  false: { type: "danger", text: "失败" },
} as const;

const emptySearchForm = (): AiCallLogFilters => ({});
const searchForm = ref<AiCallLogFilters>(emptySearchForm());
const showSearchBar = ref(true);
const searchBarRef = ref<InstanceType<typeof FaSearchBar> | null>(null);

// 下拉选项取自调用记录里出现过的值（同项目依赖的做法），不用手打、也不会拼错
const filterOptions = ref<AiCallFilterOptions | null>(null);
const selectProps = (
  values: string[] | undefined,
  label: (value: string) => string = (value) => value
) => ({
  placeholder: "全部",
  options: (values ?? []).map((value) => ({ label: label(value), value })),
  clearable: true,
  filterable: true,
});

const searchItems = computed<SearchFormItem[]>(() => [
  {
    label: "项目",
    key: "project",
    type: "select",
    props: selectProps(filterOptions.value?.projects),
    span: 6,
  },
  {
    label: "功能",
    key: "feature",
    type: "select",
    props: selectProps(filterOptions.value?.features),
    span: 6,
  },
  {
    label: "供应商",
    key: "provider",
    type: "select",
    props: selectProps(filterOptions.value?.providers, providerLabel),
    span: 6,
  },
  {
    label: "模型",
    key: "model",
    type: "select",
    props: selectProps(filterOptions.value?.models),
    span: 6,
  },
  {
    label: "用户",
    key: "user_name",
    type: "input",
    placeholder: "请输入用户名称",
    clearable: true,
    span: 6,
  },
  {
    label: "平台",
    key: "user_platform",
    type: "select",
    props: selectProps(filterOptions.value?.platforms, (value) => PLATFORM_LABEL[value] ?? value),
    span: 6,
  },
  {
    label: "结果",
    key: "success",
    type: "select",
    props: {
      placeholder: "全部",
      options: [
        { label: "成功", value: true },
        { label: "失败", value: false },
      ],
      clearable: true,
    },
    span: 6,
  },
  {
    label: "调用时间",
    key: "occurred_time",
    type: "datetimerange",
    props: {
      style: { width: "100%" },
      type: "datetimerange",
      rangeSeparator: "至",
      startPlaceholder: "开始",
      endPlaceholder: "结束",
      valueFormat: "YYYY-MM-DD HH:mm:ss",
    },
    span: 12,
  },
]);

// ─── 合计 ───
const summary = ref<AiCallSummary | null>(null);
const summaryCards = computed(() => [
  {
    label: "总费用",
    value: summary.value ? formatUsd(summary.value.total_cost_usd) : "—",
    hint: summary.value?.unpriced_count
      ? `另有 ${summary.value.unpriced_count} 笔未定价，没算进去`
      : "",
  },
  { label: "调用次数", value: summary.value?.call_count ?? "—", hint: "" },
  { label: "输入 token", value: formatTokens(summary.value?.input_tokens), hint: "" },
  { label: "输出 token", value: formatTokens(summary.value?.output_tokens), hint: "" },
]);

// ─── 详情对话框（只读，借用 useCrudForm 的详情加载） ───
const { dialogVisible } = useCrudDialog();
const detailFormData = ref<AiCallLogTable>({});
const formData = ref<AiCallLogTable>({});
const dataFormRef = ref<InstanceType<typeof FaForm> | null>(null);
const formRenderKey = ref(0);

const { handleCloseDialog, handleOpenDialog } = useCrudForm<AiCallLogTable>({
  formData,
  initialFormData: {},
  dialogVisible,
  dataFormRef,
  formRenderKey,
  detailApi: AiUsageAPI.detailCallLog,
  titles: { detail: "AI 调用详情" },
  detailFormData,
});

const detailItems: DescriptionsItem[] = [
  { label: "调用时间", prop: "occurred_time" },
  { label: "结果", prop: "success", tag: { map: SUCCESS_TAG } },
  { label: "项目", prop: "project" },
  { label: "功能", prop: "feature" },
  { label: "供应商", prop: "provider" },
  { label: "模型", prop: "model" },
  { label: "用户", prop: "user_name", slot: "user_name" },
  { label: "用户 ID", prop: "user_external_id" },
  { label: "触发方式", prop: "trigger" },
  { label: "耗时(毫秒)", prop: "duration_ms" },
  { label: "输入 token", prop: "input_tokens" },
  { label: "输出 token", prop: "output_tokens" },
  { label: "快取读取", prop: "cache_read_tokens" },
  { label: "快取写入", prop: "cache_write_tokens" },
  { label: "费用", prop: "cost_usd", slot: "cost_usd" },
  { label: "失败原因", prop: "error_message", slot: "error_message", span: 2 },
];

// ─── 表格 ───
const {
  columns,
  columnChecks,
  data,
  loading,
  pagination,
  getData,
  searchParams,
  replaceSearchParams,
  handleSizeChange,
  handleCurrentChange,
  refreshData,
} = useTable({
  core: {
    apiFn: AiUsageAPI.listCallLog,
    apiParams: {
      page_no: 1,
      page_size: 20,
    },
    columnsFactory: resolveStatusColumns<AiCallLogTable>(() => [
      { type: "globalIndex", width: 56, label: "序号" },
      { prop: "occurred_time", label: "调用时间", width: 168 },
      {
        prop: "user_name",
        label: "用户",
        minWidth: 150,
        showOverflowTooltip: true,
        formatter: (row: AiCallLogTable) => renderCaller(row),
      },
      { prop: "project", label: "项目", minWidth: 130, showOverflowTooltip: true },
      { prop: "feature", label: "功能", minWidth: 130, showOverflowTooltip: true },
      { prop: "model", label: "模型", minWidth: 170, showOverflowTooltip: true },
      {
        prop: "input_tokens",
        label: "token（入/出）",
        width: 120,
        formatter: (row: AiCallLogTable) =>
          `${formatTokens(row.input_tokens)} / ${formatTokens(row.output_tokens)}`,
      },
      {
        prop: "cost_usd",
        label: "费用",
        width: 100,
        formatter: (row: AiCallLogTable) => formatUsd(row.cost_usd),
      },
      {
        prop: "duration_ms",
        label: "耗时",
        width: 90,
        formatter: (row: AiCallLogTable) =>
          row.duration_ms == null ? "—" : `${(row.duration_ms / 1000).toFixed(1)} 秒`,
      },
      { prop: "success", label: "结果", width: 80, status: SUCCESS_TAG },
      {
        prop: "operation",
        label: "操作",
        width: 80,
        fixed: "right",
        align: "center",
        formatter: (row: AiCallLogTable) =>
          renderTableOperationCell([
            {
              key: "detail",
              label: "详情",
              artType: "view",
              perm: "module_ai_usage:call_log:detail",
              run: () => {
                if (row.id != null) void handleOpenDialog("detail", row.id);
              },
            },
          ]),
      },
    ]),
  },
  hooks: {
    onSuccess: () => void loadSummary(),
  },
});

async function loadSummary() {
  const query: Record<string, unknown> = { ...searchParams };
  for (const key of ["page_no", "page_size"]) delete query[key];
  try {
    const res = await AiUsageAPI.summaryCallLog(query as AiCallLogFilters);
    summary.value = res.data.data ?? null;
  } catch {
    // 合计只是辅助信息，失败时保留上一次的结果
  }
}

const { autoRefresh } = useAutoRefresh(refreshData);

async function applySearch(params: AiCallLogFilters = {}) {
  replaceSearchParams({ ...params });
  await getData();
}

async function handleSearchBarSearch(params: AiCallLogFilters) {
  await applySearch(params);
}

onMounted(async () => {
  filterOptions.value = (await AiUsageAPI.filtersCallLog()).data.data ?? null;
});

async function onResetSearch() {
  searchForm.value = emptySearchForm();
  await applySearch();
}
</script>
