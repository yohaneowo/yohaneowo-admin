<template>
  <div class="fa-full-height">
    <ElAlert
      title="观影档案在网站上建立（Trakt 帐号与地区），可关联 Discord 帐号接收 bot 的每日推荐；同步任务更新 Trakt / TMDB，bot 离线仍可查看统计。"
      type="info"
      :closable="false"
    />
    <ElCard class="fa-table-card mt-3">
      <ElSwitch v-model="autoRefresh" active-text="自动刷新（15 秒）" />
      <FaTableHeader v-model:columns="columnChecks" :loading="loading" @refresh="refreshData" />
      <FaTable
        :loading="loading"
        :data="data"
        :columns="columns"
        :pagination="pagination"
        @pagination:size-change="handleSizeChange"
        @pagination:current-change="handleCurrentChange"
      />
    </ElCard>
  </div>
</template>
<script setup lang="ts">
import MoviesAPI, { type MovieSubscription } from "@/api/module_bot/movies";
import { renderTableOperationCell } from "@utils";
import FaTableHeader from "@/components/tables/fa-table-header/index.vue";
import { useAutoRefresh } from "../shared";
defineOptions({ name: "BotMovies", inheritAttrs: false });
const {
  columns,
  columnChecks,
  data,
  loading,
  pagination,
  refreshData,
  handleSizeChange,
  handleCurrentChange,
} = useTable({
  core: {
    apiFn: MoviesAPI.list,
    apiParams: { page_no: 1, page_size: 20 },
    columnsFactory: () => [
      { prop: "traktName", label: "Trakt", minWidth: 150 },
      {
        prop: "userId",
        label: "Discord ID",
        minWidth: 190,
        formatter: (row: MovieSubscription) => row.userId ?? "—",
      },
      {
        prop: "siteUserId",
        label: "网站帐号",
        width: 100,
        formatter: (row: MovieSubscription) => (row.siteUserId ? "已关联" : "—"),
      },
      { prop: "region", label: "地区", width: 80 },
      {
        prop: "recommendations",
        label: "Discord 推荐",
        width: 110,
        formatter: (row: MovieSubscription) =>
          !row.active ? "已取消" : row.recommendations ? "开启" : "关闭",
      },
      { prop: "syncedAt", label: "更新于（台北）", minWidth: 180 },
      {
        prop: "syncRequested",
        label: "同步状态",
        width: 110,
        formatter: (row: MovieSubscription) =>
          !row.active ? "—" : row.syncing ? "同步中" : row.syncRequested ? "等待更新" : "已更新",
      },
      { prop: "syncError", label: "最近错误", minWidth: 220 },
      {
        prop: "operation",
        label: "操作",
        width: 100,
        formatter: (row: MovieSubscription) =>
          row.active
            ? renderTableOperationCell([
                {
                  key: "refresh",
                  label: "更新统计",
                  artType: "edit",
                  perm: "module_bot:movies:refresh",
                  run: async () => {
                    await MoviesAPI.refresh(row.profileId);
                    await refreshData();
                  },
                },
              ])
            : "—",
      },
    ],
  },
});
const { autoRefresh } = useAutoRefresh(refreshData, 15000);
</script>
