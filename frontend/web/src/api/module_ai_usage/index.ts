import { request } from "@utils";

const API_PATH = "/ai_usage";

const AiUsageAPI = {
  listCallLog(query: AiCallLogPageQuery) {
    return request<ApiResponse<PageResult<AiCallLogTable>>>({
      url: `${API_PATH}/call-log/list`,
      method: "get",
      params: query,
    });
  },

  detailCallLog(id: number) {
    return request<ApiResponse<AiCallLogTable>>({
      url: `${API_PATH}/call-log/detail/${id}`,
      method: "get",
    });
  },

  filtersCallLog() {
    return request<ApiResponse<AiCallFilterOptions>>({
      url: `${API_PATH}/call-log/filters`,
      method: "get",
    });
  },

  summaryCallLog(query: AiCallLogFilters) {
    return request<ApiResponse<AiCallSummary>>({
      url: `${API_PATH}/call-log/summary`,
      method: "get",
      params: query,
    });
  },

  ranking(query: AiCallLogFilters & { group_by: AiRankingGroup; limit?: number }) {
    return request<ApiResponse<AiCostRankingRow[]>>({
      url: `${API_PATH}/call-log/ranking`,
      method: "get",
      params: query,
    });
  },

  listModelPrice(query: AiModelPricePageQuery) {
    return request<ApiResponse<PageResult<AiModelPriceTable>>>({
      url: `${API_PATH}/model-price/list`,
      method: "get",
      params: query,
    });
  },

  priceStatus() {
    return request<ApiResponse<AiPriceStatus>>({
      url: `${API_PATH}/model-price/status`,
      method: "get",
    });
  },

  createModelPrice(body: AiModelPriceForm) {
    return request<ApiResponse<AiModelPriceTable>>({
      url: `${API_PATH}/model-price/create`,
      method: "post",
      data: body,
    });
  },

  updateModelPrice(id: number, body: AiModelPriceForm) {
    return request<ApiResponse<AiModelPriceTable>>({
      url: `${API_PATH}/model-price/update/${id}`,
      method: "put",
      data: body,
    });
  },

  listReconcile(status?: AiReconcileStatus) {
    return request<ApiResponse<AiCostReconcileRow[]>>({
      url: `${API_PATH}/reconcile/list`,
      method: "get",
      params: status ? { status } : {},
    });
  },

  reconcileSummary() {
    return request<ApiResponse<AiReconcileSummary>>({
      url: `${API_PATH}/reconcile/summary`,
      method: "get",
    });
  },

  runReconcile() {
    return request<ApiResponse<AiReconcileRunResult>>({
      url: `${API_PATH}/reconcile/run`,
      method: "post",
    });
  },

  syncModelPrice() {
    return request<ApiResponse<AiPriceSyncResult>>({
      url: `${API_PATH}/model-price/sync`,
      method: "post",
    });
  },
};

export default AiUsageAPI;

/** 列表、合计、排行共用的筛选条件 */
export interface AiCallLogFilters {
  project?: string;
  feature?: string;
  provider?: string;
  model?: string;
  user_platform?: string;
  user_external_id?: string;
  user_name?: string;
  success?: boolean;
  occurred_time?: string[];
}

export interface AiCallLogPageQuery extends PageQuery, AiCallLogFilters {}

export interface AiCallLogTable extends BaseType {
  project?: string;
  feature?: string;
  provider?: string;
  model?: string;
  trigger?: string;
  user_platform?: string;
  user_external_id?: string;
  user_name?: string;
  user_avatar_url?: string;
  input_tokens?: number;
  output_tokens?: number;
  cache_read_tokens?: number;
  cache_write_tokens?: number;
  /** 金额以字符串传回（Decimal）；null 表示未定价 */
  cost_usd?: string | null;
  duration_ms?: number;
  success?: boolean;
  error_message?: string;
  occurred_time?: string;
}

/** 搜索栏下拉选项：调用记录里出现过的值 */
export interface AiCallFilterOptions {
  projects: string[];
  features: string[];
  providers: string[];
  models: string[];
  platforms: string[];
}

export interface AiCallSummary {
  call_count: number;
  total_cost_usd: string;
  input_tokens: number;
  output_tokens: number;
  unpriced_count: number;
}

export type AiRankingGroup = "user" | "project" | "feature" | "model";

export interface AiCostRankingRow {
  key: string;
  user_platform: string | null;
  user_external_id: string | null;
  user_name: string | null;
  user_avatar_url: string | null;
  call_count: number;
  total_cost_usd: string;
  input_tokens: number;
  output_tokens: number;
  last_time: string | null;
}

export interface AiModelPricePageQuery extends PageQuery {
  provider?: string;
  model?: string;
  source?: string;
}

export interface AiModelPriceTable extends BaseType {
  provider?: string;
  model?: string;
  input_price?: string;
  output_price?: string;
  cache_read_price?: string | null;
  cache_write_price?: string | null;
  source?: "litellm" | "manual";
  locked?: boolean;
  synced_time?: string | null;
}

export interface AiModelPriceForm {
  provider: string;
  model: string;
  input_price: number;
  output_price: number;
  cache_read_price: number | null;
  cache_write_price: number | null;
  locked: boolean;
  description?: string;
}

export interface AiPriceStatus {
  last_synced_time: string | null;
  model_count: number;
  providers: string[];
  unpriced_call_count: number;
}

export interface AiPriceSyncResult {
  added: number;
  updated: number;
  skipped_locked: number;
  repriced_calls: number;
}

export type AiReconcileStatus = "ok" | "price_mismatch" | "missing_logs" | "extra_logs";

/** 一天（UTC）一个模型的对账结果：actual = 供应商帐单，logged = AI 调用日志 */
export interface AiCostReconcileRow {
  id: number;
  day: string;
  provider: string;
  model: string;
  actual_input_tokens: number;
  actual_output_tokens: number;
  actual_cache_read_tokens: number;
  actual_cache_write_tokens: number;
  actual_cost_usd: string;
  logged_call_count: number;
  logged_input_tokens: number;
  logged_output_tokens: number;
  logged_cache_read_tokens: number;
  logged_cache_write_tokens: number;
  logged_cost_usd: string;
  logged_unpriced_count: number;
  status: AiReconcileStatus;
  checked_time: string;
}

export interface AiReconcileSummary {
  configured: boolean;
  month: string;
  actual_cost_usd: string;
  logged_cost_usd: string;
  problem_count: number;
  first_day: string | null;
  last_checked_time: string | null;
}

export interface AiReconcileRunResult {
  days: string[];
  rows: number;
  problems: number;
}
