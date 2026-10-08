<!-- Bot 解析日志：每次解析链接一条，只读，永久保留 -->
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
        <template #url>
          <ElLink :href="detailFormData.url" target="_blank" type="primary" class="break-all">
            {{ detailFormData.url }}
          </ElLink>
        </template>
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
import BotParseLogAPI, {
  type BotParseLogTable,
  type BotParseResult,
  type BotParseSource,
} from "@/api/module_bot/parse_log";
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
  name: "BotParseLog",
  inheritAttrs: false,
});

type ParseLogSearchForm = {
  platform?: BotPlatform;
  source?: BotParseSource;
  result?: BotParseResult;
  user_name?: string;
  url?: string;
  occurred_time?: string[];
};

const SOURCE_OPTIONS = [
  { label: "自动解析", value: "auto" },
  { label: "指令", value: "command" },
];
const RESULT_OPTIONS = [
  { label: "成功", value: "success" },
  { label: "失败", value: "failed" },
  { label: "忙碌跳过", value: "busy" },
];
const SOURCE_TAG = {
  auto: { type: "info", text: "自动解析" },
  command: { type: "primary", text: "指令" },
} as const;
const RESULT_TAG = {
  success: { type: "success", text: "成功" },
  failed: { type: "danger", text: "失败" },
  busy: { type: "warning", text: "忙碌跳过" },
} as const;

const emptySearchForm = (): ParseLogSearchForm => ({});
const searchForm = ref<ParseLogSearchForm>(emptySearchForm());
const showSearchBar = ref(true);
const searchBarRef = ref<InstanceType<typeof FaSearchBar> | null>(null);

const searchItems = computed<SearchFormItem[]>(() => [
  {
    label: "结果",
    key: "result",
    type: "select",
    props: { placeholder: "请选择结果", options: RESULT_OPTIONS, clearable: true },
    span: 6,
  },
  {
    label: "平台",
    key: "platform",
    type: "select",
    props: { placeholder: "请选择平台", options: PLATFORM_OPTIONS, clearable: true },
    span: 6,
  },
  {
    label: "触发方式",
    key: "source",
    type: "select",
    props: { placeholder: "请选择", options: SOURCE_OPTIONS, clearable: true },
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
    label: "链接",
    key: "url",
    type: "input",
    placeholder: "请输入链接关键字",
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
const detailFormData = ref<BotParseLogTable>({});
const formData = ref<BotParseLogTable>({});
const dataFormRef = ref<InstanceType<typeof FaForm> | null>(null);
const formRenderKey = ref(0);

const { handleCloseDialog, handleOpenDialog } = useCrudForm<BotParseLogTable>({
  formData,
  initialFormData: {},
  dialogVisible,
  dataFormRef,
  formRenderKey,
  detailApi: BotParseLogAPI.detailBotParseLog,
  titles: { detail: "解析日志详情" },
  detailFormData,
});

const detailItems: DescriptionsItem[] = [
  { label: "发生时间", prop: "occurred_time" },
  { label: "结果", prop: "result", tag: { map: RESULT_TAG } },
  { label: "网站", prop: "site" },
  { label: "触发方式", prop: "source", tag: { map: SOURCE_TAG } },
  { label: "平台", prop: "platform", tag: { map: PLATFORM_TAG } },
  { label: "群组", prop: "group_name" },
  { label: "群组 ID", prop: "group_external_id" },
  { label: "用户", prop: "user_name" },
  { label: "用户 ID", prop: "user_external_id" },
  { label: "耗时(毫秒)", prop: "duration_ms" },
  { label: "文件数", prop: "file_count" },
  {
    label: "压缩过",
    prop: "compressed",
    tag: { map: { true: { type: "warning", text: "是" }, false: { type: "info", text: "否" } } },
  },
  { label: "链接", prop: "url", slot: "url", span: 2 },
  { label: "错误原因", prop: "error_message", slot: "error_message", span: 2 },
];

function formatChat(row: BotParseLogTable) {
  if (!row.group_external_id) return "私聊";
  return row.group_name || row.group_external_id;
}

function formatDuration(ms?: number) {
  if (ms == null) return "—";
  return ms >= 1000 ? `${(ms / 1000).toFixed(1)} 秒` : `${ms} 毫秒`;
}

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
    apiFn: BotParseLogAPI.listBotParseLog,
    apiParams: {
      page_no: 1,
      page_size: 20,
    },
    columnsFactory: resolveStatusColumns<BotParseLogTable>(() => [
      { type: "globalIndex", width: 56, label: "序号" },
      { prop: "occurred_time", label: "发生时间", width: 168 },
      { prop: "result", label: "结果", width: 96, status: RESULT_TAG },
      { prop: "site", label: "网站", width: 96 },
      { prop: "url", label: "链接", minWidth: 220, showOverflowTooltip: true },
      { prop: "platform", label: "平台", width: 96, status: PLATFORM_TAG },
      {
        prop: "group_name",
        label: "群组",
        minWidth: 140,
        showOverflowTooltip: true,
        formatter: (row: BotParseLogTable) => formatChat(row),
      },
      { prop: "user_name", label: "用户", minWidth: 120, showOverflowTooltip: true },
      { prop: "source", label: "触发方式", width: 96, status: SOURCE_TAG },
      {
        prop: "duration_ms",
        label: "耗时",
        width: 96,
        formatter: (row: BotParseLogTable) => formatDuration(row.duration_ms),
      },
      {
        prop: "error_message",
        label: "错误原因",
        minWidth: 180,
        showOverflowTooltip: true,
      },
      {
        prop: "operation",
        label: "操作",
        width: 80,
        fixed: "right",
        align: "center",
        formatter: (row: BotParseLogTable) =>
          renderTableOperationCell([
            {
              key: "detail",
              label: "详情",
              artType: "view",
              perm: "module_bot:parse_log:detail",
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
  statsApi: BotParseLogAPI.siteStatsBotParseLog,
});

const { autoRefresh } = useAutoRefresh(refreshData);

async function handleSearchBarSearch(params: ParseLogSearchForm) {
  await applySearch(params);
}

async function onResetSearch() {
  searchForm.value = emptySearchForm();
  site.value = ALL_SITES;
  await applySearch();
}
</script>
