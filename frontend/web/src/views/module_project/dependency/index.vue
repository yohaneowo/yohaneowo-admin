<!-- 项目依赖健康：项目自己上报的依赖清单 + admin 查到的上游状态，只读 -->
<!-- 依赖一次全部列出：不用 fa-full-height（锁在窗口高度、表格内部滚动），表格随行数长高，整页滚动 -->
<template>
  <div>
    <FaSearchBar
      v-show="showSearchBar"
      v-model="searchForm"
      :items="searchItems"
      :is-expand="false"
      :show-expand="true"
      :default-expanded="false"
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
      >
        <template #left>
          <ElButton
            v-hasPerm="['module_project:dependency:check']"
            type="primary"
            :loading="checking"
            @click="handleCheck"
          >
            立即检查
          </ElButton>
          <span class="ml-3 text-xs text-g-500">
            🟢 半年内有更新 · 🟡 半年到一年没更新 · 🔴 超过一年没更新或已封存
          </span>
        </template>
      </FaTableHeader>

      <FaTable
        height="auto"
        :loading="loading"
        :data="data"
        :columns="columns"
        :pagination="pagination"
        @pagination:size-change="handleSizeChange"
        @pagination:current-change="handleCurrentChange"
      />
    </ElCard>

    <DependencyDetailDialog ref="detailDialog" />
  </div>
</template>

<script setup lang="ts">
import { h } from "vue";
import { ElLink, ElMessage, ElTag } from "element-plus";
import ProjectDependencyAPI, {
  type DependencyStatus,
  type ProjectDependencyTable,
} from "@/api/module_project/dependency";
import { renderTableOperationCell, resolveStatusColumns } from "@utils";
import type { SearchFormItem } from "@/components/forms/fa-search-bar/index.vue";
import FaSearchBar from "@/components/forms/fa-search-bar/index.vue";
import FaTableHeader from "@/components/tables/fa-table-header/index.vue";
import DependencyDetailDialog from "../components/DependencyDetailDialog.vue";
import { KIND_TAG, SEVERITY_TAG, STATUS_TAG, timeAgo } from "../shared";

defineOptions({
  name: "ProjectDependency",
  inheritAttrs: false,
});

type DependencySearchForm = {
  project?: string;
  name?: string;
  category?: string;
  status?: DependencyStatus;
  outdated?: boolean;
  /** 「只看」：对应后端的 critical / vulnerable / major_behind 筛选 */
  only?: "critical" | "vulnerable" | "major_behind";
};

const projectOptions = ref<{ label: string; value: string }[]>([]);
const categoryOptions = ref<{ label: string; value: string }[]>([]);
const searchForm = ref<DependencySearchForm>({});
const showSearchBar = ref(true);

const searchItems = computed<SearchFormItem[]>(() => [
  {
    label: "项目",
    key: "project",
    type: "select",
    props: {
      placeholder: "全部项目",
      options: projectOptions.value,
      clearable: true,
      onChange: searchNow,
    },
    span: 6,
  },
  {
    label: "分类",
    key: "category",
    type: "select",
    props: {
      placeholder: "全部分类",
      options: categoryOptions.value,
      clearable: true,
      onChange: searchNow,
    },
    span: 6,
  },
  {
    label: "名称",
    key: "name",
    type: "input",
    placeholder: "请输入依赖名称",
    clearable: true,
    span: 6,
  },
  {
    label: "只看",
    key: "only",
    type: "select",
    props: {
      placeholder: "全部",
      options: [
        { label: "⭐ 重点依赖", value: "critical" },
        { label: "有已知漏洞", value: "vulnerable" },
        { label: "落后大版本", value: "major_behind" },
      ],
      clearable: true,
      onChange: searchNow,
    },
    span: 6,
  },
  {
    label: "状态",
    key: "status",
    type: "select",
    props: {
      placeholder: "全部状态",
      options: Object.entries(STATUS_TAG).map(([value, tag]) => ({ label: tag.text, value })),
      clearable: true,
      onChange: searchNow,
    },
    span: 6,
  },
  {
    label: "版本",
    key: "outdated",
    type: "select",
    props: {
      placeholder: "全部",
      options: [
        { label: "可更新", value: true },
        { label: "已是最新", value: false },
      ],
      clearable: true,
      onChange: searchNow,
    },
    span: 6,
  },
]);

// ─── 详情 ───
const detailDialog = ref<InstanceType<typeof DependencyDetailDialog> | null>(null);

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
    apiFn: ProjectDependencyAPI.listDependency,
    apiParams: {
      page_no: 1,
      // 一页最多 100（后端分页上限），admin 自己的依赖就有一百多个
      page_size: 100,
    },
    columnsFactory: resolveStatusColumns<ProjectDependencyTable>(() => [
      { prop: "status", label: "状态", width: 100, status: STATUS_TAG },
      {
        prop: "name",
        label: "依赖",
        minWidth: 200,
        formatter: (row: ProjectDependencyTable) =>
          h("div", { class: "leading-tight" }, [
            h("div", { class: "font-medium" }, `${row.critical ? "⭐ " : ""}${row.name}`),
            row.usage ? h("div", { class: "text-xs text-g-500" }, row.usage) : null,
          ]),
      },
      { prop: "project", label: "项目", width: 160, showOverflowTooltip: true },
      {
        prop: "category",
        label: "分类",
        width: 120,
        showOverflowTooltip: true,
        formatter: (row: ProjectDependencyTable) => row.category || "—",
      },
      { prop: "kind", label: "类型", width: 90, status: KIND_TAG },
      {
        prop: "vulnerabilities",
        label: "漏洞",
        width: 100,
        formatter: (row: ProjectDependencyTable) =>
          row.vulnerable && row.vulnerabilities
            ? h(
                ElTag,
                { type: row.vuln_severity ? SEVERITY_TAG[row.vuln_severity].type : "warning" },
                () =>
                  `${row.vulnerabilities!.length} 个` +
                  (row.vuln_severity ? `·${SEVERITY_TAG[row.vuln_severity].text}` : "")
              )
            : "—",
      },
      {
        prop: "installed_version",
        label: "你的版本",
        minWidth: 120,
        // ffmpeg 这类工具的版本字串很长，单行显示、悬停看全文
        showOverflowTooltip: true,
        formatter: (row: ProjectDependencyTable) => row.installed_version || "—",
      },
      {
        prop: "latest_version",
        label: "最新版本",
        minWidth: 150,
        formatter: (row: ProjectDependencyTable) =>
          h("span", { class: "inline-flex items-center gap-1" }, [
            row.latest_version || "—",
            row.major_behind
              ? h(ElTag, { type: "danger", size: "small" }, () => "大版本")
              : row.outdated
                ? h(ElTag, { type: "warning", size: "small" }, () => "可更新")
                : null,
          ]),
      },
      {
        prop: "upstream_updated_time",
        label: "上游最后更新",
        width: 130,
        formatter: (row: ProjectDependencyTable) =>
          h(
            "span",
            { title: row.upstream_updated_time ?? "" },
            row.archived ? "已封存" : timeAgo(row.upstream_updated_time)
          ),
      },
      {
        prop: "source",
        label: "来源",
        minWidth: 200,
        showOverflowTooltip: true,
        formatter: (row: ProjectDependencyTable) =>
          row.source_url
            ? h(
                ElLink,
                { href: row.source_url, target: "_blank", type: "primary" },
                () => row.source
              )
            : row.source || "—",
      },
      {
        prop: "checked_time",
        label: "上次检查",
        width: 110,
        formatter: (row: ProjectDependencyTable) => timeAgo(row.checked_time),
      },
      {
        prop: "operation",
        label: "操作",
        width: 80,
        fixed: "right",
        align: "center",
        formatter: (row: ProjectDependencyTable) =>
          renderTableOperationCell([
            {
              key: "detail",
              label: "详情",
              artType: "view",
              perm: "module_project:dependency:detail",
              run: () => {
                if (row.id != null) void detailDialog.value?.open(row.id);
              },
            },
          ]),
      },
    ]),
  },
});

// 下拉选单一选就筛选，不必再按「查询」；名称是输入框，仍按 Enter 或「查询」
function searchNow() {
  void nextTick(() => handleSearchBarSearch(searchForm.value));
}

async function handleSearchBarSearch(params: DependencySearchForm) {
  const { only, ...rest } = params;
  replaceSearchParams({ ...rest, ...(only ? { [only]: true } : {}) });
  await getData();
}

async function onResetSearch() {
  searchForm.value = {};
  await resetSearchParams();
}

// ─── 立即检查（查当前选的项目；没选就全部） ───
const checking = ref(false);

async function handleCheck() {
  checking.value = true;
  try {
    const res = await ProjectDependencyAPI.checkDependency(searchForm.value.project);
    const result = res.data.data;
    if (result) {
      const message = `检查完成：${result.checked} 个依赖`;
      if (result.failed) ElMessage.warning(`${message}，${result.failed} 个检查失败`);
      else ElMessage.success(message);
    }
    // 检查时 admin 会重新收集自己的清单，可能多出新的项目或分类
    await Promise.all([refreshData(), loadFilters()]);
  } finally {
    checking.value = false;
  }
}

async function loadFilters() {
  const res = await ProjectDependencyAPI.listFilters();
  const toOption = (value: string) => ({ label: value, value });
  projectOptions.value = (res.data.data?.projects ?? []).map(toOption);
  categoryOptions.value = (res.data.data?.categories ?? []).map(toOption);
}

onMounted(loadFilters);
</script>
