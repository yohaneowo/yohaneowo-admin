<!-- AI 模型单价：每天从 LiteLLM 公开价格表同步；手动新增或修改的会锁定，不被同步覆盖 -->
<template>
  <div>
    <ElCard shadow="never" class="mb-3">
      <div class="flex flex-wrap items-center gap-3">
        <ElSelect
          v-model="query.provider"
          placeholder="全部供应商"
          clearable
          class="w-40!"
          @change="reload"
        >
          <ElOption v-for="p in providerOptions" :key="p.value" :label="p.label" :value="p.value" />
        </ElSelect>
        <ElInput
          v-model="query.model"
          placeholder="模型名称"
          clearable
          class="w-56!"
          @change="reload"
        />
        <ElSelect
          v-model="query.source"
          placeholder="全部来源"
          clearable
          class="w-36!"
          @change="reload"
        >
          <ElOption label="自动同步" value="litellm" />
          <ElOption label="手动" value="manual" />
        </ElSelect>
        <ElButton
          v-hasPerm="['module_ai_usage:model_price:sync']"
          type="primary"
          :loading="syncing"
          @click="handleSync"
        >
          立即同步
        </ElButton>
        <ElButton v-hasPerm="['module_ai_usage:model_price:create']" @click="openForm()">
          新增模型
        </ElButton>
        <span class="text-sm text-g-500">
          上次同步：{{ status?.last_synced_time ?? "还没同步过" }} · 共
          {{ status?.model_count ?? 0 }} 个模型
          <template v-if="status?.unpriced_call_count">
            · <span class="text-warning">{{ status.unpriced_call_count }} 笔调用还没有单价</span>
          </template>
        </span>
      </div>
      <div class="mt-2 text-xs text-g-500 leading-5">
        <p class="m-0">
          <b>怎么看</b>：价格是每用 100 万个 token 要付多少美元。输入是送给 AI
          的内容（提示、资料），输出是 AI 写出来的内容；快取是重复使用之前送过的内容，通常便宜很多。
        </p>
        <p class="m-0">
          例：Sonnet 5.5 输入 $2、输出 $10，一次送 7,000 个 token、回 2,000 个 token，费用是 7,000 ×
          $2 ÷ 1,000,000 + 2,000 × $10 ÷ 1,000,000 ≈ <b>$0.034</b>。
        </p>
        <p class="m-0">
          价格来自 LiteLLM 维护的公开价格表（供应商没有提供价格
          API），每天自动同步。社群维护的资料偶尔会有延迟或错误，
          可以用「费用对账」跟实际帐单比对；手动修改后会锁定，不会被自动同步覆盖。已经算好的历史费用不会因为调价而改变。
        </p>
      </div>
    </ElCard>

    <ElCard shadow="never">
      <ElTable v-loading="loading" :data="rows" size="default">
        <ElTableColumn prop="provider" label="供应商" width="110" />
        <ElTableColumn prop="model" label="模型" min-width="220" show-overflow-tooltip />
        <!-- 单位直接写在表头：只看数字（$10、$50）看不出是什么价格 -->
        <ElTableColumn label="单价（美元 / 每百万 token）" align="center">
          <ElTableColumn label="输入" width="95">
            <template #default="{ row }">{{ price(row.input_price) }}</template>
          </ElTableColumn>
          <ElTableColumn label="输出" width="95">
            <template #default="{ row }">{{ price(row.output_price) }}</template>
          </ElTableColumn>
          <ElTableColumn label="快取读取" width="95">
            <template #default="{ row }">{{ price(row.cache_read_price) }}</template>
          </ElTableColumn>
          <ElTableColumn label="快取写入" width="95">
            <template #default="{ row }">{{ price(row.cache_write_price) }}</template>
          </ElTableColumn>
        </ElTableColumn>
        <ElTableColumn label="来源" width="130">
          <template #default="{ row }">
            <ElTag :type="row.source === 'manual' ? 'warning' : 'info'">
              {{ row.source === "manual" ? "手动" : "自动同步" }}
            </ElTag>
            <ElTag v-if="row.locked" type="danger" class="ml-1">锁定</ElTag>
          </template>
        </ElTableColumn>
        <ElTableColumn prop="synced_time" label="同步时间" width="170" />
        <ElTableColumn label="操作" width="80" fixed="right" align="center">
          <template #default="{ row }">
            <ElButton
              v-hasPerm="['module_ai_usage:model_price:update']"
              link
              type="primary"
              @click="openForm(row)"
            >
              修改
            </ElButton>
          </template>
        </ElTableColumn>
      </ElTable>
      <div class="mt-3 flex justify-end">
        <ElPagination
          v-model:current-page="page.no"
          v-model:page-size="page.size"
          :total="total"
          :page-sizes="[20, 50, 100]"
          layout="total, sizes, prev, pager, next"
          @current-change="load"
          @size-change="reload"
        />
      </div>
    </ElCard>

    <ElDialog
      v-model="formVisible"
      :title="editingId ? '修改模型单价' : '新增模型单价'"
      width="520px"
    >
      <ElForm ref="formRef" :model="form" :rules="rules" label-width="110px">
        <ElFormItem label="供应商" prop="provider">
          <ElSelect
            v-model="form.provider"
            :disabled="Boolean(editingId)"
            filterable
            allow-create
            class="w-full"
          >
            <ElOption
              v-for="p in providerOptions"
              :key="p.value"
              :label="p.label"
              :value="p.value"
            />
          </ElSelect>
        </ElFormItem>
        <ElFormItem label="模型" prop="model">
          <ElInput
            v-model="form.model"
            :disabled="Boolean(editingId)"
            placeholder="与 API 请求里的 model 一致"
          />
        </ElFormItem>
        <ElFormItem label="输入单价" prop="input_price">
          <ElInputNumber v-model="form.input_price" :min="0" :precision="4" :step="0.1" />
          <span class="ml-2 text-xs text-g-500">$ / 百万 token</span>
        </ElFormItem>
        <ElFormItem label="输出单价" prop="output_price">
          <ElInputNumber v-model="form.output_price" :min="0" :precision="4" :step="0.1" />
        </ElFormItem>
        <ElFormItem label="快取读取单价">
          <ElInputNumber
            v-model="form.cache_read_price"
            :min="0"
            :precision="4"
            :step="0.01"
            :value-on-clear="null"
          />
        </ElFormItem>
        <ElFormItem label="快取写入单价">
          <ElInputNumber
            v-model="form.cache_write_price"
            :min="0"
            :precision="4"
            :step="0.01"
            :value-on-clear="null"
          />
        </ElFormItem>
        <ElFormItem label="锁定">
          <ElSwitch v-model="form.locked" />
          <span class="ml-2 text-xs text-g-500">锁定后不会被每天的自动同步覆盖</span>
        </ElFormItem>
      </ElForm>
      <template #footer>
        <ElButton @click="formVisible = false">取消</ElButton>
        <ElButton type="primary" :loading="saving" @click="handleSave">保存</ElButton>
      </template>
    </ElDialog>
  </div>
</template>

<script setup lang="ts">
import type { FormInstance, FormRules } from "element-plus";
import { ElMessage } from "element-plus";
import AiUsageAPI, {
  type AiModelPriceForm,
  type AiModelPricePageQuery,
  type AiModelPriceTable,
  type AiPriceStatus,
} from "@/api/module_ai_usage";
import { providerLabel } from "../shared";

defineOptions({ name: "AiModelPrice" });

const query = reactive<Omit<AiModelPricePageQuery, "page_no" | "page_size">>({});
const page = reactive({ no: 1, size: 20 });
const rows = ref<AiModelPriceTable[]>([]);
const total = ref(0);
const status = ref<AiPriceStatus | null>(null);
const loading = ref(false);
const syncing = ref(false);
// 供应商选项取自价格表里有的供应商（每天从 LiteLLM 同步）
const providerOptions = computed(() =>
  (status.value?.providers ?? []).map((value) => ({ label: providerLabel(value), value }))
);

function price(value?: string | null) {
  return value == null ? "—" : `$${Number(value)}`;
}

async function load() {
  loading.value = true;
  try {
    const [list, state] = await Promise.all([
      AiUsageAPI.listModelPrice({ ...query, page_no: page.no, page_size: page.size }),
      AiUsageAPI.priceStatus(),
    ]);
    rows.value = list.data.data.items ?? [];
    total.value = list.data.data.total ?? 0;
    status.value = state.data.data ?? null;
  } finally {
    loading.value = false;
  }
}

function reload() {
  page.no = 1;
  return load();
}

async function handleSync() {
  syncing.value = true;
  try {
    const res = await AiUsageAPI.syncModelPrice();
    const r = res.data.data;
    ElMessage.success(
      `同步完成：新增 ${r.added}、更新 ${r.updated}、锁定跳过 ${r.skipped_locked}、补算 ${r.repriced_calls} 笔调用`
    );
    await load();
  } finally {
    syncing.value = false;
  }
}

// ─── 新增 / 修改 ───
const emptyForm = (): AiModelPriceForm => ({
  provider: "anthropic",
  model: "",
  input_price: 0,
  output_price: 0,
  cache_read_price: null,
  cache_write_price: null,
  locked: true,
});
const formVisible = ref(false);
const editingId = ref<number | null>(null);
const form = ref<AiModelPriceForm>(emptyForm());
const formRef = ref<FormInstance | null>(null);
const saving = ref(false);
const rules: FormRules = {
  provider: [{ required: true, message: "请选择供应商", trigger: "change" }],
  model: [{ required: true, message: "请输入模型名称", trigger: "blur" }],
};

function openForm(row?: AiModelPriceTable) {
  editingId.value = row?.id ?? null;
  form.value = row
    ? {
        provider: row.provider ?? "",
        model: row.model ?? "",
        input_price: Number(row.input_price ?? 0),
        output_price: Number(row.output_price ?? 0),
        cache_read_price: row.cache_read_price == null ? null : Number(row.cache_read_price),
        cache_write_price: row.cache_write_price == null ? null : Number(row.cache_write_price),
        // 手动修改默认锁定，免得明天又被同步改回去
        locked: true,
      }
    : emptyForm();
  formVisible.value = true;
}

async function handleSave() {
  if (!(await formRef.value?.validate().catch(() => false))) return;
  saving.value = true;
  try {
    if (editingId.value) await AiUsageAPI.updateModelPrice(editingId.value, form.value);
    else await AiUsageAPI.createModelPrice(form.value);
    ElMessage.success("已保存");
    formVisible.value = false;
    await load();
  } finally {
    saving.value = false;
  }
}

onMounted(load);
</script>
