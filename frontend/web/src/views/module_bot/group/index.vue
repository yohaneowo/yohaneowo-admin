<!-- Bot 群组：bot 上报的 Discord 服务器 / LINE 群组，后台只做启停与备注 -->
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
      <FaTableHeader
        v-model:columns="columnChecks"
        v-model:showSearchBar="showSearchBar"
        :loading="loading"
        @refresh="refreshData"
      >
        <template #left>
          <FaTableHeaderLeft
            :remove-ids="selectedIds"
            :perm-delete="['module_bot:group:delete']"
            :perm-patch="['module_bot:group:patch']"
            :delete-loading="batchDeleting"
            :more-loading="moreLoading"
            @delete="handleBatchDelete"
            @more="handleMoreClick"
          />
        </template>
      </FaTableHeader>

      <FaTable
        ref="faTableRef"
        :loading="loading"
        :data="data"
        :columns="columns"
        :pagination="pagination"
        @selection-change="onTableSelectionChange"
        @pagination:size-change="handleSizeChange"
        @pagination:current-change="handleCurrentChange"
      />
    </ElCard>

    <FaDialog
      v-model="dialogVisible.visible"
      :title="dialogVisible.title"
      width="720px"
      dialog-class="crud-embed-dialog"
      modal-class="crud-embed-dialog"
      :form-mode="dialogVisible.type"
      :confirm-loading="submitLoading"
      @cancel="handleCloseDialog"
      @close="handleCloseDialog"
      @confirm="handleSubmit()"
    >
      <template v-if="dialogVisible.type === 'detail'">
        <FaDescriptions
          :column="2"
          :data="detailFormData"
          :items="detailItems"
          label-width="110px"
          max-height="70vh"
        />
      </template>
      <template v-else>
        <FaForm
          :key="formRenderKey"
          ref="dataFormRef"
          v-model="formData"
          :items="formItems"
          :rules="rules"
          label-suffix=":"
          :label-width="80"
          label-position="right"
          :span="24"
          :gutter="16"
          :show-reset="false"
          :show-submit="false"
          class="crud-dialog-art-form"
        >
          <template #status>
            <ElRadioGroup v-model="formData.status">
              <ElRadio :value="0">启用</ElRadio>
              <ElRadio :value="1">停用</ElRadio>
            </ElRadioGroup>
          </template>
        </FaForm>
      </template>
    </FaDialog>
  </div>
</template>

<script setup lang="ts">
import { h } from "vue";
import { ElAvatar, ElMessage } from "element-plus";
import { useCrudForm } from "@/hooks/core/useCrudForm";
import { confirmToggleStatus } from "@/hooks/core/useConfirm";
import BotGroupAPI, {
  type BotGroupForm,
  type BotGroupTable,
  type BotPlatform,
} from "@/api/module_bot/group";
import { renderTableOperationCell, resolveStatusColumns, type TableOperationAction } from "@utils";
import type { SearchFormItem } from "@/components/forms/fa-search-bar/index.vue";
import FaSearchBar from "@/components/forms/fa-search-bar/index.vue";
import type { FormItem } from "@/components/forms/fa-form/index.vue";
import FaForm from "@/components/forms/fa-form/index.vue";
import FaTableHeader from "@/components/tables/fa-table-header/index.vue";
import FaDescriptions, {
  type DescriptionsItem,
} from "@/components/display/fa-descriptions/index.vue";
import { PLATFORM_OPTIONS, PLATFORM_TAG } from "../shared";

defineOptions({
  name: "BotGroup",
  inheritAttrs: false,
});

type BotGroupSearchForm = {
  name?: string;
  platform?: BotPlatform;
  status?: number;
  is_joined?: boolean;
};

const STATUS_OPTIONS = [
  { label: "启用", value: 0 },
  { label: "停用", value: 1 },
];
const JOINED_OPTIONS = [
  { label: "在群内", value: true },
  { label: "已离开", value: false },
];

const STATUS_TAG = {
  0: { type: "success", text: "启用" },
  1: { type: "danger", text: "停用" },
} as const;
const JOINED_TAG = {
  true: { type: "success", text: "在群内" },
  false: { type: "info", text: "已离开" },
} as const;

const emptySearchForm = (): BotGroupSearchForm => ({
  name: undefined,
  platform: undefined,
  status: undefined,
  is_joined: undefined,
});

const searchForm = ref<BotGroupSearchForm>(emptySearchForm());
const showSearchBar = ref(true);
const searchBarRef = ref<InstanceType<typeof FaSearchBar> | null>(null);

const searchItems = computed<SearchFormItem[]>(() => [
  {
    label: "名称",
    key: "name",
    type: "input",
    placeholder: "请输入群组名称",
    clearable: true,
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
    label: "状态",
    key: "status",
    type: "select",
    props: { placeholder: "请选择状态", options: STATUS_OPTIONS, clearable: true },
    span: 6,
  },
  {
    label: "在群内",
    key: "is_joined",
    type: "select",
    props: { placeholder: "请选择", options: JOINED_OPTIONS, clearable: true },
    span: 6,
  },
]);

const faTableRef = ref<{ elTableRef?: { clearSelection: () => void } } | null>(null);

// ─── 表格多选 ───
const { selectedIds, batchDeleting, onTableSelectionChange } = useTableSelection<BotGroupTable>();
const moreLoading = ref(false);

// ─── 对话框 ───
const { dialogVisible } = useCrudDialog();
const detailFormData = ref<BotGroupTable>({});

const detailItems: DescriptionsItem[] = [
  { label: "名称", prop: "name" },
  { label: "平台", prop: "platform", tag: { map: PLATFORM_TAG } },
  { label: "平台 ID", prop: "external_id" },
  { label: "成员数", prop: "member_count" },
  { label: "状态", prop: "status", tag: { map: STATUS_TAG } },
  { label: "在群内", prop: "is_joined", tag: { map: JOINED_TAG } },
  { label: "加入时间", prop: "joined_time" },
  { label: "离开时间", prop: "left_time" },
  { label: "最后活动", prop: "last_active_time" },
  { label: "登记时间", prop: "created_time" },
  { label: "备注", prop: "description", span: 2 },
];

const initialFormData: BotGroupForm = {
  id: undefined,
  status: 0,
  description: undefined,
};
const formData = ref<BotGroupForm>({ ...initialFormData });
const rules = reactive({
  status: [{ required: true, message: "请选择状态", trigger: "blur" }],
});
const dataFormRef = ref<InstanceType<typeof FaForm> | null>(null);
const formRenderKey = ref(0);

const formItems = computed<FormItem[]>(() => [
  { key: "status", label: "状态", type: "radiogroup", span: 24 },
  {
    label: "备注",
    key: "description",
    type: "input",
    span: 24,
    props: {
      type: "textarea",
      rows: 3,
      maxlength: 255,
      showWordLimit: true,
      placeholder: "例如：这个群的用途、负责人",
    },
  },
]);

const { submitLoading, handleCloseDialog, handleOpenDialog, handleSubmit } =
  useCrudForm<BotGroupForm>({
    formData,
    initialFormData,
    dialogVisible,
    dataFormRef,
    formRenderKey,
    detailApi: BotGroupAPI.detailBotGroup,
    updateApi: BotGroupAPI.updateBotGroup,
    titles: { update: "修改群组", detail: "群组详情" },
    detailFormData,
    onUpdateSuccess: async () => {
      await refreshUpdate();
    },
  });

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
  refreshUpdate,
  refreshRemove,
} = useTable({
  core: {
    apiFn: BotGroupAPI.listBotGroup,
    apiParams: {
      page_no: 1,
      page_size: 10,
    },
    columnsFactory: resolveStatusColumns<BotGroupTable>(() => [
      { type: "selection", width: 48, fixed: "left" },
      { type: "globalIndex", width: 56, label: "序号" },
      {
        prop: "name",
        label: "名称",
        minWidth: 180,
        showOverflowTooltip: true,
        formatter: (row: BotGroupTable) =>
          h("div", { class: "flex items-center gap-2" }, [
            h(ElAvatar, { src: row.icon_url, size: 24, shape: "square" }, () =>
              (row.name ?? "?").slice(0, 1)
            ),
            h("span", row.name || "—"),
          ]),
      },
      { prop: "platform", label: "平台", width: 96, status: PLATFORM_TAG },
      { prop: "external_id", label: "平台 ID", minWidth: 180, showOverflowTooltip: true },
      { prop: "member_count", label: "成员数", width: 88 },
      { prop: "status", label: "状态", width: 88, status: STATUS_TAG },
      { prop: "is_joined", label: "在群内", width: 88, status: JOINED_TAG },
      {
        prop: "last_active_time",
        label: "最后活动",
        width: 168,
        sortable: true,
        showOverflowTooltip: true,
      },
      { prop: "joined_time", label: "加入时间", width: 168, sortable: true },
      { prop: "description", label: "备注", minWidth: 140, showOverflowTooltip: true },
      {
        prop: "operation",
        label: "操作",
        width: 200,
        fixed: "right",
        align: "center",
        formatter: (row: BotGroupTable) =>
          renderTableOperationCell(buildRowActions(row), {
            wrapperClass: "inline-flex flex-wrap items-center justify-end gap-1",
          }),
      },
    ]),
  },
});

async function handleSearchBarSearch(params: BotGroupSearchForm) {
  replaceSearchParams({ ...params });
  await getData();
}

async function onResetSearch() {
  searchForm.value = emptySearchForm();
  await resetSearchParams();
}

async function deleteRow(id: number, name: string) {
  try {
    await confirmDelete(`确定删除「${name}」吗？bot 下次同步时仍在群内的话会重新登记。`);
    await BotGroupAPI.deleteBotGroup([id]);
    faTableRef.value?.elTableRef?.clearSelection();
    await refreshRemove();
  } catch {
    // 用户取消
  }
}

function buildRowActions(row: BotGroupTable): TableOperationAction[] {
  return [
    {
      key: "detail",
      label: "详情",
      artType: "view",
      perm: "module_bot:group:detail",
      run: () => {
        if (row.id != null) void handleOpenDialog("detail", row.id);
      },
    },
    {
      key: "edit",
      label: "编辑",
      artType: "edit",
      icon: "ri:edit-2-line",
      perm: "module_bot:group:update",
      run: () => {
        if (row.id != null) void handleOpenDialog("update", row.id);
      },
    },
    {
      key: "delete",
      label: "删除",
      artType: "delete",
      icon: "ri:delete-bin-4-line",
      perm: "module_bot:group:delete",
      run: () => {
        if (row.id != null) void deleteRow(row.id, row.name ?? "");
      },
    },
  ];
}

async function handleBatchDelete() {
  const ids = selectedIds.value;
  if (ids.length === 0) return;
  try {
    await confirmBatchDelete(
      ids.length,
      (data.value as BotGroupTable[])
        .filter((r) => ids.includes(r.id!))
        .map((r) => String(r.name || r.id))
    );
    batchDeleting.value = true;
    await BotGroupAPI.deleteBotGroup(ids);
    faTableRef.value?.elTableRef?.clearSelection();
    await refreshRemove();
  } catch {
    // 用户取消
  } finally {
    batchDeleting.value = false;
  }
}

async function handleMoreClick(value: "enable" | "disable") {
  const ids = selectedIds.value;
  if (!ids.length) {
    ElMessage.warning("请先选择要操作的数据");
    return;
  }
  try {
    await confirmToggleStatus(value);
    moreLoading.value = true;
    await BotGroupAPI.batchBotGroup({ ids, status: value === "enable" ? 0 : 1 });
    await refreshData();
  } catch {
    // 用户取消或操作失败
  } finally {
    moreLoading.value = false;
  }
}
</script>
