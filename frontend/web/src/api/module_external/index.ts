import { request } from "@utils";

const API_PATH = "/external";

const ExternalAPI = {
  listUsage() {
    return request<ApiResponse<ExternalProvider[]>>({
      url: `${API_PATH}/usage/list`,
      method: "get",
    });
  },

  usageHistory(provider: string, days = 14) {
    return request<ApiResponse<ExternalUsageDay[]>>({
      url: `${API_PATH}/usage/history/${provider}`,
      method: "get",
      params: { days },
    });
  },

  saveProvider(provider: string, body: ExternalProviderForm) {
    return request<ApiResponse<ExternalProvider>>({
      url: `${API_PATH}/usage/provider/${provider}`,
      method: "put",
      data: body,
    });
  },
};

export default ExternalAPI;

export interface ExternalUsageToday {
  requests: number;
  cache_hits: number;
  failures: number;
  last_error?: string | null;
  last_error_at?: string | null;
}

export interface ExternalProvider {
  provider: string;
  label: string;
  daily_limit: number | null;
  reset_timezone: string;
  note: string;
  configured: boolean;
  blocked: boolean;
  blocked_reason: string | null;
  today: ExternalUsageToday;
}

export interface ExternalUsageDay {
  day: string;
  requests: number;
  cache_hits: number;
  failures: number;
}

export interface ExternalProviderForm {
  daily_limit: number | null;
  reset_timezone: string;
  note: string;
}
