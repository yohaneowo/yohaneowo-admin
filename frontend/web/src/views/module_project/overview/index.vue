<!-- 依赖总览：统计卡片 + 需要处理的清单（已知漏洞、上游封存、落后大版本、重点依赖的问题），只读 -->
<template>
  <div>
    <ElCard shadow="never" class="mb-3">
      <div class="flex flex-wrap items-center gap-3">
        <ElSelect v-model="project" placeholder="全部项目" clearable class="w-56!" @change="load">
          <ElOption v-for="p in projects" :key="p" :label="p" :value="p" />
        </ElSelect>
        <ElButton
          v-hasPerm="['module_project:dependency:check']"
          type="primary"
          :loading="checking"
          @click="handleCheck"
        >
          立即检查
        </ElButton>
        <span class="text-sm text-g-500">
          上次检查：{{
            overview?.last_checked_time
              ? `${overview.last_checked_time}（${timeAgo(overview.last_checked_time)}）`
              : "还没检查过"
          }}
        </span>
      </div>
    </ElCard>

    <div v-loading="loading" class="grid grid-cols-2 gap-3 md:grid-cols-4 mb-3">
      <div
        v-for="card in cards"
        :key="card.label"
        class="fa-card px-5 py-4"
        :class="card.highlight ? 'border-l-4' : ''"
        :style="card.highlight ? { borderLeftColor: card.color } : {}"
      >
        <p class="m-0 text-sm text-g-500">{{ card.label }}</p>
        <p class="m-0 mt-1 text-2xl font-medium" :style="{ color: card.color }">
          {{ card.value }}
        </p>
        <p class="m-0 mt-1 text-xs text-g-500">{{ card.hint }}</p>
      </div>
    </div>

    <ElCard shadow="never">
      <template #header>
        <div class="flex items-center justify-between">
          <span class="font-medium">需要处理（{{ overview?.items.length ?? 0 }}）</span>
          <span class="text-xs text-g-500">
            非重点依赖很久没更新只算在上面的卡片里，不列出：成熟的小工具本来就很少更新
          </span>
        </div>
      </template>
      <ElTable v-loading="loading" :data="overview?.items ?? []" size="default">
        <template #empty>
          <span class="text-g-500">{{ overview ? "没有需要处理的依赖 🎉" : "加载中…" }}</span>
        </template>
        <ElTableColumn label="等级" width="100">
          <template #default="{ row }">
            <ElTag :type="LEVEL_TAG[row.level as AttentionLevel].type">
              {{ LEVEL_TAG[row.level as AttentionLevel].text }}
            </ElTag>
          </template>
        </ElTableColumn>
        <ElTableColumn label="依赖" min-width="200">
          <template #default="{ row }">
            <div class="leading-tight">
              <div class="font-medium">
                {{ row.dependency.critical ? "⭐ " : "" }}{{ row.dependency.name }}
              </div>
              <div v-if="row.dependency.usage" class="text-xs text-g-500">
                {{ row.dependency.usage }}
              </div>
            </div>
          </template>
        </ElTableColumn>
        <ElTableColumn label="项目" width="160" prop="dependency.project" show-overflow-tooltip />
        <ElTableColumn label="分类" width="120">
          <template #default="{ row }">{{ row.dependency.category || "—" }}</template>
        </ElTableColumn>
        <ElTableColumn label="版本" width="190" show-overflow-tooltip>
          <template #default="{ row }">
            {{ row.dependency.installed_version || "—" }}
            <template
              v-if="
                row.dependency.latest_version &&
                (row.dependency.outdated || row.dependency.major_behind)
              "
            >
              → {{ row.dependency.latest_version }}
            </template>
          </template>
        </ElTableColumn>
        <ElTableColumn label="原因" min-width="360">
          <template #default="{ row }">
            <div
              v-for="(reason, index) in row.reasons"
              :key="index"
              class="flex items-start gap-1 leading-snug"
            >
              <span>{{ LEVEL_TAG[reason.level as AttentionLevel].text.slice(0, 2) }}</span>
              <span>{{ reason.text }}</span>
            </div>
          </template>
        </ElTableColumn>
        <ElTableColumn label="操作" width="80" align="center" fixed="right">
          <template #default="{ row }">
            <ElButton
              v-hasPerm="['module_project:dependency:detail']"
              link
              type="primary"
              @click="detailDialog?.open(row.dependency.id)"
            >
              详情
            </ElButton>
          </template>
        </ElTableColumn>
      </ElTable>
    </ElCard>

    <DependencyDetailDialog ref="detailDialog" />
  </div>
</template>

<script setup lang="ts">
import { ElMessage } from "element-plus";
import ProjectDependencyAPI, {
  type AttentionLevel,
  type ProjectDependencyOverview,
} from "@/api/module_project/dependency";
import DependencyDetailDialog from "../components/DependencyDetailDialog.vue";
import { LEVEL_TAG, timeAgo } from "../shared";

defineOptions({
  name: "ProjectOverview",
  inheritAttrs: false,
});

const project = ref<string>();
const projects = ref<string[]>([]);
const overview = ref<ProjectDependencyOverview>();
const loading = ref(false);
const detailDialog = ref<InstanceType<typeof DependencyDetailDialog> | null>(null);

const RED = "var(--el-color-danger)";
const ORANGE = "var(--el-color-warning)";
const GREEN = "var(--el-color-success)";

const cards = computed(() => {
  const o = overview.value;
  if (!o) return [];
  return [
    {
      label: "需要处理",
      value: o.attention,
      hint: "下方清单",
      color: o.attention ? RED : GREEN,
      highlight: o.attention > 0,
    },
    {
      label: "安全漏洞",
      value: o.vulnerable,
      hint: `个依赖有已知漏洞，共 ${o.vulnerability_count} 个漏洞`,
      color: o.vulnerable ? RED : GREEN,
      highlight: o.vulnerable > 0,
    },
    {
      label: "落后大版本",
      value: o.major_behind,
      hint: "升级可能有不兼容改动",
      color: o.major_behind ? ORANGE : GREEN,
      highlight: false,
    },
    {
      label: "可更新",
      value: o.outdated,
      hint: "有比你装的更新的版本",
      color: "var(--el-text-color-primary)",
      highlight: false,
    },
    {
      label: "超过一年没更新",
      value: o.risk,
      hint: "或上游已封存",
      color: o.risk ? ORANGE : GREEN,
      highlight: false,
    },
    {
      label: "半年到一年没更新",
      value: o.warning,
      hint: "上游更新变慢",
      color: "var(--el-text-color-primary)",
      highlight: false,
    },
    {
      label: "健康",
      value: `${o.healthy} / ${o.total}`,
      hint: "半年内有更新",
      color: GREEN,
      highlight: false,
    },
    {
      label: "⭐ 重点依赖",
      value: o.critical,
      hint: "坏了影响最大，有问题都会列出",
      color: "var(--el-text-color-primary)",
      highlight: false,
    },
  ];
});

async function load() {
  loading.value = true;
  try {
    const res = await ProjectDependencyAPI.overview(project.value || undefined);
    overview.value = res.data.data;
  } finally {
    loading.value = false;
  }
}

async function loadProjects() {
  const res = await ProjectDependencyAPI.listFilters();
  projects.value = res.data.data?.projects ?? [];
}

const checking = ref(false);

async function handleCheck() {
  checking.value = true;
  try {
    const res = await ProjectDependencyAPI.checkDependency(project.value || undefined);
    const result = res.data.data;
    if (result) {
      const message = `检查完成：${result.checked} 个依赖`;
      if (result.failed) ElMessage.warning(`${message}，${result.failed} 个检查失败`);
      else ElMessage.success(message);
    }
    await Promise.all([load(), loadProjects()]);
  } finally {
    checking.value = false;
  }
}

onMounted(() => Promise.all([load(), loadProjects()]));
</script>
