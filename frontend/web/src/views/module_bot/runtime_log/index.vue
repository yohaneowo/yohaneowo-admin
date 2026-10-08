<!-- Bot 运行日志：bot 进程的 console 输出，只读，永久保留 -->
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
      <SiteFilterBar
        class="mb-3"
        :model-value="site"
        :counts="siteCounts"
        show-other
        @update:model-value="changeSite"
      />
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
      width="860px"
      dialog-class="crud-embed-dialog"
      modal-class="crud-embed-dialog"
      :form-mode="dialogVisible.type"
      @cancel="handleCloseDialog"
      @close="handleCloseDialog"
    >
      <FaDescriptions
        :column="4"
        :data="detailFormData"
        :items="detailItems"
        label-width="90px"
        max-height="70vh"
      >
        <template #message>
          <pre class="m-0 whitespace-pre-wrap break-all text-xs">{{ detailFormData.message }}</pre>
        </template>
      </FaDescriptions>
    </FaDialog>
  </div>
</template>

<script setup lang="ts">
import { useCrudForm } from "@/hooks/core/useCrudForm";
import BotRuntimeLogAPI, {
  type BotLogLevel,
  type BotRuntimeLogTable,
} from "@/api/module_bot/runtime_log";
import type { BotPlatform } from "@/api/module_bot/group";
import { renderTableOperationCell, resolveStatusColumns } from "@utils";
import type { SearchFormItem } from "@/components/forms/fa-search-bar/index.vue";
import FaSearchBar from "@/components/forms/fa-search-bar/index.vue";
import FaForm from "@/components/forms/fa-form/index.vue";
import FaTableHeader from "@/components/tables/fa-table-header/index.vue";
import FaDescriptions, {
  type DescriptionsItem,
} from "@/components/display/fa-descriptions/index.vue";
import {
  ALL_SITES,
  PLATFORM_OPTIONS,
  PLATFORM_TAG,
  useAutoRefresh,
  useSiteFilter,
} from "../shared";
import SiteFilterBar from "../components/SiteFilterBar.vue";

defineOptions({
  name: "BotRuntimeLog",
  inheritAttrs: false,
});

type RuntimeLogSearchForm = {
  source?: BotPlatform;
  level?: BotLogLevel;
  message?: string;
  occurred_time?: string[];
};

const LEVEL_OPTIONS = [
  { label: "info", value: "info" },
  { label: "warn", value: "warn" },
  { label: "error", value: "error" },
];
const LEVEL_TAG = {
  info: { type: "info", text: "info" },
  warn: { type: "warning", text: "warn" },
  error: { type: "danger", text: "error" },
} as const;

const emptySearchForm = (): RuntimeLogSearchForm => ({});
const searchForm = ref<RuntimeLogSearchForm>(emptySearchForm());
const showSearchBar = ref(true);
const searchBarRef = ref<InstanceType<typeof FaSearchBar> | null>(null);

const searchItems = computed<SearchFormItem[]>(() => [
  {
    label: "等级",
    key: "level",
    type: "select",
    props: { placeholder: "请选择等级", options: LEVEL_OPTIONS, clearable: true },
    span: 6,
  },
  {
    label: "来源",
    key: "source",
    type: "select",
    props: { placeholder: "请选择来源", options: PLATFORM_OPTIONS, clearable: true },
    span: 6,
  },
  {
    label: "内容",
    key: "message",
    type: "input",
    placeholder: "请输入关键字",
    clearable: true,
    span: 6,
  },
  {
    label: "发生时间",
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

// ─── 详情对话框（只读，借用 useCrudForm 的详情加载） ───
const { dialogVisible } = useCrudDialog();
const detailFormData = ref<BotRuntimeLogTable>({});
const formData = ref<BotRuntimeLogTable>({});
const dataFormRef = ref<InstanceType<typeof FaForm> | null>(null);
const formRenderKey = ref(0);

const { handleCloseDialog, handleOpenDialog } = useCrudForm<BotRuntimeLogTable>({
  formData,
  initialFormData: {},
  dialogVisible,
  dataFormRef,
  formRenderKey,
  detailApi: BotRuntimeLogAPI.detailBotRuntimeLog,
  titles: { detail: "运行日志详情" },
  detailFormData,
});

const detailItems: DescriptionsItem[] = [
  { label: "发生时间", prop: "occurred_time" },
  { label: "等级", prop: "level", tag: { map: LEVEL_TAG } },
  { label: "来源", prop: "source", tag: { map: PLATFORM_TAG } },
  { label: "网站", prop: "site" },
  { label: "内容", prop: "message", slot: "message", span: 4 },
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
    apiFn: BotRuntimeLogAPI.listBotRuntimeLog,
    apiParams: {
      page_no: 1,
      page_size: 50,
    },
    columnsFactory: resolveStatusColumns<BotRuntimeLogTable>(() => [
      { prop: "occurred_time", label: "发生时间", width: 168 },
      { prop: "level", label: "等级", width: 80, status: LEVEL_TAG },
      { prop: "source", label: "来源", width: 96, status: PLATFORM_TAG },
      {
        prop: "site",
        label: "网站",
        width: 96,
        formatter: (row: BotRuntimeLogTable) => row.site ?? "—",
      },
      {
        prop: "message",
        label: "内容",
        minWidth: 400,
        showOverflowTooltip: true,
        formatter: (row: BotRuntimeLogTable) => (row.message ?? "").split("\n")[0],
      },
      {
        prop: "operation",
        label: "操作",
        width: 80,
        fixed: "right",
        align: "center",
        formatter: (row: BotRuntimeLogTable) =>
          renderTableOperationCell([
            {
              key: "detail",
              label: "详情",
              artType: "view",
              perm: "module_bot:runtime_log:detail",
              run: () => {
                if (row.id != null) void handleOpenDialog("detail", row.id);
              },
            },
          ]),
      },
    ]),
  },
  hooks: {
    onSuccess: () => void loadSiteCounts(),
  },
});

const { site, siteCounts, applySearch, changeSite, loadSiteCounts } = useSiteFilter({
  searchForm,
  searchParams,
  replaceSearchParams,
  getData,
  statsApi: BotRuntimeLogAPI.siteStatsBotRuntimeLog,
});

const { autoRefresh } = useAutoRefresh(refreshData);

async function handleSearchBarSearch(params: RuntimeLogSearchForm) {
  await applySearch(params);
}

async function onResetSearch() {
  searchForm.value = emptySearchForm();
  site.value = ALL_SITES;
  await applySearch();
}
</script>
