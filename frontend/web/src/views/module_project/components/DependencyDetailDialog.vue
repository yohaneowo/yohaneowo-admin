<!-- 依赖详情：基本资料、已知漏洞、最近的检查记录。总览与依赖健康两页共用 -->
<template>
  <ElDialog v-model="visible" :title="`依赖详情：${detail.name ?? ''}`" width="900px">
    <FaDescriptions :column="3" :data="info" :items="items" label-width="100px" />

    <template v-if="detail.vulnerabilities?.length">
      <p class="mt-4 mb-2 text-sm font-medium">
        已知漏洞（{{ detail.vulnerabilities.length }} 个）
        <span class="font-normal text-g-500">
          {{
            detail.fix_version ? `升级到 ${detail.fix_version} 可全部修复` : "部分漏洞尚无修复版本"
          }}
        </span>
      </p>
      <ElTable :data="detail.vulnerabilities" size="small" border max-height="300">
        <ElTableColumn label="编号" width="190">
          <template #default="{ row }">
            <ElLink :href="osvUrl(row.id)" target="_blank" type="primary">{{ row.id }}</ElLink>
            <div v-if="row.aliases.length" class="text-xs text-g-500">
              {{ row.aliases.join("、") }}
            </div>
          </template>
        </ElTableColumn>
        <ElTableColumn label="等级" width="70">
          <template #default="{ row }">
            <ElTag
              v-if="row.severity"
              :type="SEVERITY_TAG[row.severity as VulnSeverity].type"
              size="small"
            >
              {{ SEVERITY_TAG[row.severity as VulnSeverity].text }}
            </ElTag>
            <span v-else>—</span>
          </template>
        </ElTableColumn>
        <ElTableColumn prop="summary" label="说明" min-width="300" show-overflow-tooltip />
        <ElTableColumn label="修复版本" width="110">
          <template #default="{ row }">{{ row.fixed || "尚无修复" }}</template>
        </ElTableColumn>
      </ElTable>
    </template>

    <p class="mt-4 mb-2 text-sm font-medium">最近的检查记录</p>
    <ElTable :data="detail.checks ?? []" size="small" border max-height="280">
      <ElTableColumn prop="checked_time" label="检查时间" width="170" />
      <ElTableColumn label="状态" width="90">
        <template #default="{ row }">
          <ElTag :type="STATUS_TAG[row.status as DependencyStatus]?.type" size="small">
            {{ STATUS_TAG[row.status as DependencyStatus]?.text ?? row.status }}
          </ElTag>
        </template>
      </ElTableColumn>
      <ElTableColumn prop="installed_version" label="你的版本" min-width="110" />
      <ElTableColumn prop="latest_version" label="最新版本" min-width="110" />
      <ElTableColumn prop="vulnerability_count" label="漏洞" width="60" />
      <ElTableColumn prop="upstream_updated_time" label="上游最后更新" width="170" />
      <ElTableColumn prop="check_error" label="错误" min-width="140" show-overflow-tooltip />
    </ElTable>
  </ElDialog>
</template>

<script setup lang="ts">
import ProjectDependencyAPI, {
  type DependencyStatus,
  type ProjectDependencyDetail,
  type VulnSeverity,
} from "@/api/module_project/dependency";
import FaDescriptions, {
  type DescriptionsItem,
} from "@/components/display/fa-descriptions/index.vue";
import { KIND_TAG, SEVERITY_TAG, STATUS_TAG, osvUrl, timeAgo } from "../shared";

const visible = ref(false);
const detail = ref<ProjectDependencyDetail>({});
const info = ref<Record<string, string | undefined>>({});

const items: DescriptionsItem[] = [
  { label: "项目", prop: "project" },
  { label: "分类", prop: "category" },
  { label: "类型", prop: "kind" },
  { label: "来源", prop: "source" },
  { label: "重点依赖", prop: "critical" },
  { label: "已封存", prop: "archived" },
  { label: "用途", prop: "usage", span: 3 },
  { label: "你的版本", prop: "installed_version" },
  { label: "最新版本", prop: "latest_version" },
  { label: "上游最后更新", prop: "upstream_updated_time" },
  { label: "上次上报", prop: "reported_time" },
  { label: "上次检查", prop: "checked_time" },
  { label: "错误", prop: "check_error", span: 3 },
];

async function open(id: number) {
  const res = await ProjectDependencyAPI.detailDependency(id);
  const d = (detail.value = res.data.data ?? {});
  let latest = d.latest_version || "—";
  if (d.major_behind) latest += "（落后大版本）";
  else if (d.outdated) latest += "（可更新）";
  info.value = {
    project: d.project,
    category: d.category || "—",
    kind: d.kind ? KIND_TAG[d.kind].text : undefined,
    source: d.source || "—",
    critical: d.critical ? "⭐ 是" : "否",
    archived: d.archived ? "是" : "否",
    usage: d.usage || "—",
    installed_version: d.installed_version || "—",
    latest_version: latest,
    upstream_updated_time: d.upstream_updated_time
      ? `${d.upstream_updated_time}（${timeAgo(d.upstream_updated_time)}）`
      : "—",
    reported_time: d.reported_time,
    checked_time: d.checked_time || "还没检查过",
    check_error: d.check_error || "—",
  };
  visible.value = true;
}

defineExpose({ open });
</script>
