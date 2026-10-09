<!-- Bot 语音记录：Discord 语音频道里每段连续停留一条（离开时上报），只读，永久保留 -->
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
      <!-- 排行跟着上面的筛选条件（服务器、时间范围等）一起变 -->
      <div class="mb-3">
        <p class="m-0 mb-2 text-sm font-medium">语音时长排行</p>
        <div v-if="userStats.length" class="flex flex-wrap gap-2">
          <ElTag
            v-for="(stat, index) in userStats"
            :key="stat.user_external_id"
            :type="index < 3 ? 'warning' : 'info'"
            effect="plain"
            class="cursor-pointer"
            @click="filterByUser(stat)"
          >
            <span class="inline-flex items-center gap-1">
              {{ index + 1 }}.
              <ElAvatar :src="stat.user_avatar_url ?? undefined" :size="16">
                {{ (stat.user_name ?? "?").slice(0, 1) }}
              </ElAvatar>
              {{ stat.user_name || stat.user_external_id }} ·
              {{ formatSeconds(stat.total_seconds) }}（{{ stat.session_count }} 次）
            </span>
          </ElTag>
        </div>
        <p v-else class="m-0 text-xs text-g-500">没有记录</p>
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
        <template #joined_time>
          {{ detailFormData.joined_time }}
          <span v-if="detailFormData.joined_time_estimated" class="text-xs text-g-500">
            （估计值）
          </span>
        </template>
        <template #group_name>
          <component :is="renderGuild(detailFormData)" />
        </template>
        <template #user_name>
          <component :is="renderUser(detailFormData)" />
        </template>
        <template #duration_seconds>
          {{ formatSeconds(detailFormData.duration_seconds) }}
        </template>
      </FaDescriptions>
    </FaDialog>
  </div>
</template>

<script setup lang="ts">
import { useCrudForm } from "@/hooks/core/useCrudForm";
import BotVoiceSessionAPI, {
  type BotVoiceEndReason,
  type BotVoiceSessionTable,
  type BotVoiceUserStat,
} from "@/api/module_bot/voice_session";
import BotGroupAPI from "@/api/module_bot/group";
import { ElAvatar } from "element-plus";
import { renderTableOperationCell, resolveStatusColumns } from "@utils";
import type { SearchFormItem } from "@/components/forms/fa-search-bar/index.vue";
import FaSearchBar from "@/components/forms/fa-search-bar/index.vue";
import FaForm from "@/components/forms/fa-form/index.vue";
import FaTableHeader from "@/components/tables/fa-table-header/index.vue";
import FaDescriptions, {
  type DescriptionsItem,
} from "@/components/display/fa-descriptions/index.vue";
import { useAutoRefresh } from "../shared";

defineOptions({
  name: "BotVoiceSession",
  inheritAttrs: false,
});

type VoiceSessionSearchForm = {
  guild_external_id?: string;
  channel_name?: string;
  user_name?: string;
  user_external_id?: string;
  end_reason?: BotVoiceEndReason;
  joined_time?: string[];
};

const END_REASON_OPTIONS = [
  { label: "离开", value: "leave" },
  { label: "换频道", value: "move" },
  { label: "bot 重启", value: "restart" },
];
const END_REASON_TAG = {
  leave: { type: "info", text: "离开" },
  move: { type: "primary", text: "换频道" },
  restart: { type: "warning", text: "bot 重启" },
} as const;

const emptySearchForm = (): VoiceSessionSearchForm => ({});
const searchForm = ref<VoiceSessionSearchForm>(emptySearchForm());
const showSearchBar = ref(true);
const searchBarRef = ref<InstanceType<typeof FaSearchBar> | null>(null);

// 服务器下拉选项来自群组列表（只有 Discord 有语音）
const guildOptions = ref<{ label: string; value: string }[]>([]);
async function loadGuildOptions() {
  const res = await BotGroupAPI.listBotGroup({ page_no: 1, page_size: 100, platform: "discord" });
  guildOptions.value = (res.data.data.items ?? [])
    .filter((group) => group.external_id)
    .map((group) => ({ label: group.name || group.external_id!, value: group.external_id! }));
}

const searchItems = computed<SearchFormItem[]>(() => [
  {
    label: "服务器",
    key: "guild_external_id",
    type: "select",
    props: { placeholder: "全部服务器", options: guildOptions.value, clearable: true },
    span: 6,
  },
  {
    label: "频道",
    key: "channel_name",
    type: "input",
    placeholder: "请输入频道名称",
    clearable: true,
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
    label: "结束原因",
    key: "end_reason",
    type: "select",
    props: { placeholder: "请选择", options: END_REASON_OPTIONS, clearable: true },
    span: 6,
  },
  {
    label: "进入时间",
    key: "joined_time",
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

function formatSeconds(seconds?: number) {
  if (seconds == null) return "—";
  const hours = Math.floor(seconds / 3600);
  const minutes = Math.floor((seconds % 3600) / 60);
  if (hours) return `${hours} 小时 ${minutes} 分`;
  if (minutes) return `${minutes} 分 ${seconds % 60} 秒`;
  return `${seconds} 秒`;
}

// 头像 + 名称，同群组列表的写法；没有头像时显示名称首字
function renderUser(row: BotVoiceSessionTable) {
  return h("div", { class: "flex items-center gap-2" }, [
    h(ElAvatar, { src: row.user_avatar_url, size: 24 }, () => (row.user_name ?? "?").slice(0, 1)),
    h("span", row.user_name || row.user_external_id || "—"),
  ]);
}

// 服务器头像 + 名称，同群组列表（方形头像）
function renderGuild(row: BotVoiceSessionTable) {
  const name = row.group_name || row.guild_external_id || "—";
  return h("div", { class: "flex items-center gap-2" }, [
    h(ElAvatar, { src: row.group_icon_url, size: 24, shape: "square" }, () => name.slice(0, 1)),
    h("span", name),
  ]);
}

// ─── 详情对话框（只读，借用 useCrudForm 的详情加载） ───
const { dialogVisible } = useCrudDialog();
const detailFormData = ref<BotVoiceSessionTable>({});
const formData = ref<BotVoiceSessionTable>({});
const dataFormRef = ref<InstanceType<typeof FaForm> | null>(null);
const formRenderKey = ref(0);

const { handleCloseDialog, handleOpenDialog } = useCrudForm<BotVoiceSessionTable>({
  formData,
  initialFormData: {},
  dialogVisible,
  dataFormRef,
  formRenderKey,
  detailApi: BotVoiceSessionAPI.detailBotVoiceSession,
  titles: { detail: "语音停留详情" },
  detailFormData,
});

const detailItems: DescriptionsItem[] = [
  { label: "用户", prop: "user_name", slot: "user_name" },
  { label: "用户 ID", prop: "user_external_id" },
  { label: "服务器", prop: "group_name", slot: "group_name" },
  { label: "服务器 ID", prop: "guild_external_id" },
  { label: "频道", prop: "channel_name" },
  { label: "频道 ID", prop: "channel_external_id" },
  { label: "进入时间", prop: "joined_time", slot: "joined_time" },
  { label: "离开时间", prop: "left_time" },
  { label: "停留时长", prop: "duration_seconds", slot: "duration_seconds" },
  { label: "结束原因", prop: "end_reason", tag: { map: END_REASON_TAG } },
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
    apiFn: BotVoiceSessionAPI.listBotVoiceSession,
    apiParams: {
      page_no: 1,
      page_size: 20,
    },
    columnsFactory: resolveStatusColumns<BotVoiceSessionTable>(() => [
      { type: "globalIndex", width: 56, label: "序号" },
      {
        prop: "user_name",
        label: "用户",
        minWidth: 140,
        showOverflowTooltip: true,
        formatter: (row: BotVoiceSessionTable) => renderUser(row),
      },
      { prop: "channel_name", label: "频道", minWidth: 140, showOverflowTooltip: true },
      {
        prop: "group_name",
        label: "服务器",
        minWidth: 140,
        showOverflowTooltip: true,
        formatter: (row: BotVoiceSessionTable) => renderGuild(row),
      },
      {
        prop: "joined_time",
        label: "进入时间",
        width: 180,
        formatter: (row: BotVoiceSessionTable) =>
          row.joined_time_estimated ? `${row.joined_time}（估计）` : (row.joined_time ?? "—"),
      },
      { prop: "left_time", label: "离开时间", width: 168 },
      {
        prop: "duration_seconds",
        label: "停留时长",
        width: 120,
        formatter: (row: BotVoiceSessionTable) => formatSeconds(row.duration_seconds),
      },
      { prop: "end_reason", label: "结束原因", width: 100, status: END_REASON_TAG },
      {
        prop: "operation",
        label: "操作",
        width: 80,
        fixed: "right",
        align: "center",
        formatter: (row: BotVoiceSessionTable) =>
          renderTableOperationCell([
            {
              key: "detail",
              label: "详情",
              artType: "view",
              perm: "module_bot:voice_session:detail",
              run: () => {
                if (row.id != null) void handleOpenDialog("detail", row.id);
              },
            },
          ]),
      },
    ]),
  },
  hooks: {
    onSuccess: () => void loadUserStats(),
  },
});

// ─── 排行（与列表共用筛选条件） ───
const userStats = ref<BotVoiceUserStat[]>([]);
async function loadUserStats() {
  const query: Record<string, unknown> = { ...searchParams };
  for (const key of ["page_no", "page_size"]) delete query[key];
  try {
    const res = await BotVoiceSessionAPI.userStatsBotVoiceSession({ ...query, limit: 10 });
    userStats.value = res.data.data ?? [];
  } catch {
    // 排行只是辅助信息，失败时保留上一次的结果
  }
}

const { autoRefresh } = useAutoRefresh(refreshData);

async function applySearch(params: VoiceSessionSearchForm = {}) {
  replaceSearchParams({ ...params });
  await getData();
}

async function handleSearchBarSearch(params: VoiceSessionSearchForm) {
  await applySearch(params);
}

async function onResetSearch() {
  searchForm.value = emptySearchForm();
  await applySearch();
}

// 点排行里的人：只看这个人的记录（其他筛选条件保留）
async function filterByUser(stat: BotVoiceUserStat) {
  searchForm.value = {
    ...searchForm.value,
    user_name: undefined,
    user_external_id: stat.user_external_id,
  };
  await applySearch(searchForm.value);
}

onMounted(() => void loadGuildOptions());
</script>
