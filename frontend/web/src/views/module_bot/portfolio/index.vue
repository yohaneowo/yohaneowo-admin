<!-- Bot 资产记录：bot 每 15 分钟上报的交易所总资产，只读，永久保留 -->
<template>
  <div class="fa-full-height">
    <FaSearchBar
      v-show="showSearchBar"
      v-model="searchForm"
      :items="searchItems"
      :is-expand="false"
      :show-expand="false"
      :show-reset="true"
      :show-search="true"
      :disabled-search="false"
      @search="handleSearchBarSearch"
      @reset="onResetSearch"
    />

    <ElCard class="fa-table-card" :style="{ 'margin-top': showSearchBar ? '12px' : '0' }">
      <FaTableHeader
        v-model:columns="columnChecks"
        v-model:showSearchBar="showSearchBar"
        :loading="loading"
        @refresh="refreshData"
      />

      <FaTable
        :loading="loading"
        :data="data"
        :columns="columns"
        :pagination="pagination"
        @pagination:size-change="handleSizeChange"
        @pagination:current-change="handleCurrentChange"
      />
    </ElCard>

    <ElDialog v-model="detailVisible" title="资产快照详情" width="640px">
      <FaDescriptions :column="3" :data="detail" :items="detailItems" label-width="100px" />
      <ElTable :data="detailAccounts" class="mt-4" size="small" border>
        <ElTableColumn prop="exchange" label="交易所" width="120" />
        <ElTableColumn label="账户" min-width="160">
          <template #default="{ row }">{{ ACCOUNT_TYPE_LABEL[row.type] ?? row.type }}</template>
        </ElTableColumn>
        <ElTableColumn label="金额 (USDT)" align="right" min-width="140">
          <template #default="{ row }">{{ formatNumber(row.total, 2) }}</template>
        </ElTableColumn>
      </ElTable>
    </ElDialog>
  </div>
</template>

<script setup lang="ts">
import { h } from "vue";
import { ElTag } from "element-plus";
import BotPortfolioAPI, {
  type BotPortfolioSnapshot,
  type PortfolioAccount,
} from "@/api/module_bot/portfolio";
import { renderTableOperationCell, resolveStatusColumns } from "@utils";
import type { SearchFormItem } from "@/components/forms/fa-search-bar/index.vue";
import FaSearchBar from "@/components/forms/fa-search-bar/index.vue";
import FaTableHeader from "@/components/tables/fa-table-header/index.vue";
import FaDescriptions, {
  type DescriptionsItem,
} from "@/components/display/fa-descriptions/index.vue";

defineOptions({
  name: "BotPortfolio",
  inheritAttrs: false,
});

type PortfolioSearchForm = { occurred_time?: string[] };

const ACCOUNT_TYPE_LABEL: Record<string, string> = {
  spot: "现货",
  futures: "合约",
  earn: "理财",
  funding: "资金账户",
  account: "账户",
  "inverse-contract-wallet": "币本位合约钱包",
};

const searchForm = ref<PortfolioSearchForm>({});
const showSearchBar = ref(true);

const searchItems = computed<SearchFormItem[]>(() => [
  {
    label: "快照时间",
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

function formatNumber(value: number | undefined, decimals: number) {
  return (value ?? 0).toLocaleString("en-US", {
    minimumFractionDigits: decimals,
    maximumFractionDigits: decimals,
  });
}

// ─── 详情：列表行里已有全部字段，不另外请求 ───
const detailVisible = ref(false);
// 金额先格式化成字符串再交给 FaDescriptions 显示
const detail = ref<Record<string, string | number | undefined>>({});
const detailAccounts = ref<PortfolioAccount[]>([]);
const detailItems: DescriptionsItem[] = [
  { label: "快照时间", prop: "occurred_time", span: 3 },
  { label: "总资产 TWD", prop: "total_twd" },
  { label: "总资产 USDT", prop: "total_usdt" },
  { label: "汇率", prop: "twd_rate" },
];

function openDetail(row: BotPortfolioSnapshot) {
  detail.value = {
    occurred_time: row.occurred_time,
    total_twd: `NT$ ${formatNumber(row.total_twd, 0)}`,
    total_usdt: formatNumber(row.total_usdt, 2),
    twd_rate: row.twd_rate,
  };
  detailAccounts.value = row.accounts ?? [];
  detailVisible.value = true;
}

// ─── 表格 ───
const {
  columns,
  columnChecks,
  data,
  loading,
  pagination,
  getData,
  replaceSearchParams,
  resetSearchParams,
  handleSizeChange,
  handleCurrentChange,
  refreshData,
} = useTable({
  core: {
    apiFn: BotPortfolioAPI.listBotPortfolio,
    apiParams: {
      page_no: 1,
      page_size: 20,
    },
    columnsFactory: resolveStatusColumns<BotPortfolioSnapshot>(() => [
      { prop: "occurred_time", label: "快照时间", width: 180 },
      {
        prop: "total_twd",
        label: "总资产 (TWD)",
        minWidth: 140,
        align: "right",
        formatter: (row: BotPortfolioSnapshot) => `NT$ ${formatNumber(row.total_twd, 0)}`,
      },
      {
        prop: "total_usdt",
        label: "总资产 (USDT)",
        minWidth: 140,
        align: "right",
        formatter: (row: BotPortfolioSnapshot) => formatNumber(row.total_usdt, 2),
      },
      { prop: "twd_rate", label: "汇率", width: 100, align: "right" },
      {
        prop: "failures",
        label: "读取失败",
        minWidth: 180,
        formatter: (row: BotPortfolioSnapshot) =>
          row.failures?.length
            ? h(ElTag, { type: "warning", size: "small" }, () => row.failures!.join("、"))
            : "—",
      },
      {
        prop: "operation",
        label: "操作",
        width: 80,
        fixed: "right",
        align: "center",
        formatter: (row: BotPortfolioSnapshot) =>
          renderTableOperationCell([
            {
              key: "detail",
              label: "详情",
              artType: "view",
              perm: "module_bot:portfolio:query",
              run: () => openDetail(row),
            },
          ]),
      },
    ]),
  },
});

async function handleSearchBarSearch(params: PortfolioSearchForm) {
  replaceSearchParams({ ...params });
  await getData();
}

async function onResetSearch() {
  searchForm.value = {};
  await resetSearchParams();
}
</script>
