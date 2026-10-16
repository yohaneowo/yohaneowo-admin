<!-- 第三方接口：TMDB、Trakt、OMDb、Wikipedia 每天的用量与额度。额度在这里改，不写死在代码里 -->
<template>
  <div>
    <ElCard shadow="never" class="mb-3">
      <div class="flex flex-wrap items-center gap-3">
        <ElButton :loading="loading" @click="load">重新整理</ElButton>
        <span class="text-sm text-g-500">日期按各服务的重置时区计算</span>
      </div>
      <div class="mt-2 text-xs text-g-500 leading-5">
        <p class="m-0">
          <b>怎么看</b
          >：「请求」是今天真的打到对方的次数，「命中快取」是直接用了存好的资料、没有打出去的次数。
          用到上限的 90% 会在日志记一条 warning，用完记
          error，之后当天的请求会被跳过（没查到的下次同步再补）。
        </p>
        <p class="m-0">
          对方自己回「额度用完」或「key
          无效」时，当天也会停止调用。订阅了付费方案后，在「设定」把上限调大或清空，
          保存后会立刻解除当天的停止状态。
        </p>
      </div>
    </ElCard>

    <ElCard shadow="never">
      <ElTable
        v-loading="loading"
        :data="rows"
        row-key="provider"
        size="default"
        @expand-change="loadHistory"
      >
        <ElTableColumn type="expand">
          <template #default="{ row }">
            <div class="px-6 py-2">
              <div class="mb-2 text-sm text-g-500">最近 14 天</div>
              <ElTable :data="history[row.provider] ?? []" size="small" max-height="320">
                <ElTableColumn prop="day" label="日期" width="130" />
                <ElTableColumn prop="requests" label="请求" width="100" />
                <ElTableColumn prop="cache_hits" label="命中快取" width="100" />
                <ElTableColumn prop="failures" label="失败" width="100" />
              </ElTable>
            </div>
          </template>
        </ElTableColumn>
        <ElTableColumn prop="label" label="服务" width="120" />
        <ElTableColumn label="今天请求 / 上限" min-width="220">
          <template #default="{ row }">
            <div class="flex items-center gap-3">
              <span class="whitespace-nowrap">
                {{ row.today.requests }} / {{ row.daily_limit ?? "不限" }}
              </span>
              <ElProgress
                v-if="row.daily_limit"
                class="flex-1"
                :percentage="share(row)"
                :status="share(row) >= 100 ? 'exception' : share(row) >= 90 ? 'warning' : undefined"
                :stroke-width="10"
              />
            </div>
          </template>
        </ElTableColumn>
        <ElTableColumn label="命中快取" width="100">
          <template #default="{ row }">{{ row.today.cache_hits }}</template>
        </ElTableColumn>
        <ElTableColumn label="失败" width="80">
          <template #default="{ row }">
            <span :class="row.today.failures ? 'text-danger' : ''">{{ row.today.failures }}</span>
          </template>
        </ElTableColumn>
        <ElTableColumn label="状态" width="150">
          <template #default="{ row }">
            <ElTag v-if="!row.configured" type="info">没有设定 key</ElTag>
            <ElTooltip v-else-if="row.blocked" :content="row.blocked_reason ?? ''" placement="top">
              <ElTag type="danger">今天已停止</ElTag>
            </ElTooltip>
            <ElTag v-else type="success">正常</ElTag>
          </template>
        </ElTableColumn>
        <ElTableColumn label="最近错误" min-width="220" show-overflow-tooltip>
          <template #default="{ row }">
            <template v-if="row.today.last_error">
              {{ row.today.last_error_at }} · {{ row.today.last_error }}
            </template>
            <span v-else class="text-g-500">—</span>
          </template>
        </ElTableColumn>
        <ElTableColumn prop="reset_timezone" label="重置时区" width="140" />
        <ElTableColumn prop="note" label="备注" min-width="160" show-overflow-tooltip />
        <ElTableColumn label="操作" width="80" fixed="right" align="center">
          <template #default="{ row }">
            <ElButton
              v-hasPerm="['module_external:usage:update']"
              link
              type="primary"
              @click="openForm(row)"
            >
              设定
            </ElButton>
          </template>
        </ElTableColumn>
      </ElTable>
    </ElCard>

    <ElDialog v-model="formVisible" :title="`${editing?.label ?? ''} 额度设定`" width="480px">
      <ElForm :model="form" label-width="90px">
        <ElFormItem label="每日上限">
          <ElInputNumber
            v-model="form.daily_limit"
            :min="1"
            :step="100"
            :value-on-clear="null"
            placeholder="不限"
          />
          <span class="ml-2 text-xs text-g-500">清空 = 不限</span>
        </ElFormItem>
        <ElFormItem label="重置时区">
          <ElSelect v-model="form.reset_timezone" filterable allow-create class="w-full">
            <ElOption v-for="zone in timezones" :key="zone" :label="zone" :value="zone" />
          </ElSelect>
          <div class="text-xs text-g-500">对方的额度在这个时区的零点重置（OMDb 是 UTC）</div>
        </ElFormItem>
        <ElFormItem label="备注">
          <ElInput v-model="form.note" maxlength="255" placeholder="例如：免费方案、Patreon $1" />
        </ElFormItem>
      </ElForm>
      <p v-if="editing?.blocked" class="m-0 text-xs text-warning">保存后会解除今天的停止状态。</p>
      <template #footer>
        <ElButton @click="formVisible = false">取消</ElButton>
        <ElButton type="primary" :loading="saving" @click="handleSave">保存</ElButton>
      </template>
    </ElDialog>
  </div>
</template>

<script setup lang="ts">
import { ElMessage } from "element-plus";
import ExternalAPI, {
  type ExternalProvider,
  type ExternalProviderForm,
  type ExternalUsageDay,
} from "@/api/module_external";

defineOptions({ name: "ExternalUsage" });

const timezones = [
  "UTC",
  "Asia/Taipei",
  "America/Los_Angeles",
  "America/New_York",
  "Europe/London",
];
const rows = ref<ExternalProvider[]>([]);
const history = reactive<Record<string, ExternalUsageDay[]>>({});
const loading = ref(false);

// Table slots type their row loosely, so the helpers take a partial row.
type Row = Partial<ExternalProvider>;

function share(row: Row) {
  if (!row.daily_limit) return 0;
  return Math.min(100, Math.round(((row.today?.requests ?? 0) / row.daily_limit) * 100));
}

async function load() {
  loading.value = true;
  try {
    rows.value = (await ExternalAPI.listUsage()).data.data ?? [];
  } finally {
    loading.value = false;
  }
}

async function loadHistory(row: ExternalProvider) {
  history[row.provider] = (await ExternalAPI.usageHistory(row.provider)).data.data ?? [];
}

// ─── 设定 ───
const formVisible = ref(false);
const editing = ref<ExternalProvider | null>(null);
const form = ref<ExternalProviderForm>({ daily_limit: null, reset_timezone: "UTC", note: "" });
const saving = ref(false);

function openForm(row: Row) {
  editing.value = row as ExternalProvider;
  form.value = {
    daily_limit: row.daily_limit ?? null,
    reset_timezone: row.reset_timezone ?? "UTC",
    note: row.note ?? "",
  };
  formVisible.value = true;
}

async function handleSave() {
  if (!editing.value) return;
  saving.value = true;
  try {
    await ExternalAPI.saveProvider(editing.value.provider, form.value);
    ElMessage.success("已保存");
    formVisible.value = false;
    await load();
  } finally {
    saving.value = false;
  }
}

onMounted(load);
</script>
