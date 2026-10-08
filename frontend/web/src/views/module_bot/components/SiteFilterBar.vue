<!-- 按网站一键筛选日志：全部 / 各网站 / 其他，按钮上显示当前筛选条件下的条数 -->
<template>
  <ElRadioGroup v-model="site" class="site-filter-bar">
    <ElRadioButton :value="ALL_SITES">全部 ({{ total }})</ElRadioButton>
    <ElRadioButton v-for="option in SITE_OPTIONS" :key="option.value" :value="option.value">
      {{ option.label }} ({{ countOf(option.value) }})
    </ElRadioButton>
    <ElRadioButton v-if="showOther" :value="OTHER_SITE">其他 ({{ countOf(null) }})</ElRadioButton>
  </ElRadioGroup>
</template>

<script setup lang="ts">
import type { SiteCount } from "@/api/module_bot/parse_log";
import { ALL_SITES, OTHER_SITE, SITE_OPTIONS } from "../shared";

const props = defineProps<{
  counts: SiteCount[];
  /** 运行日志里有与解析无关的日志，用「其他」按钮查看 */
  showOther?: boolean;
}>();

const site = defineModel<string>({ required: true });

const total = computed(() => props.counts.reduce((sum, item) => sum + item.count, 0));

function countOf(value: string | null) {
  return props.counts.find((item) => item.site === value)?.count ?? 0;
}
</script>

<style scoped>
.site-filter-bar {
  flex-wrap: wrap;
  row-gap: 8px;
}
</style>
