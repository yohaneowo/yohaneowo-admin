import { request } from "@utils";
import type { BotPlatform } from "./group";
import type { SiteCount } from "./parse_log";

const API_PATH = "/bot/runtime-log";

const BotRuntimeLogAPI = {
  listBotRuntimeLog(query: BotRuntimeLogPageQuery) {
    return request<ApiResponse<PageResult<BotRuntimeLogTable>>>({
      url: `${API_PATH}/list`,
      method: "get",
      params: query,
    });
  },

  detailBotRuntimeLog(query: number) {
    return request<ApiResponse<BotRuntimeLogTable>>({
      url: `${API_PATH}/detail/${query}`,
      method: "get",
    });
  },

  siteStatsBotRuntimeLog(query: Omit<BotRuntimeLogPageQuery, "page_no" | "page_size">) {
    return request<ApiResponse<SiteCount[]>>({
      url: `${API_PATH}/site-stats`,
      method: "get",
      params: query,
    });
  },
};

export default BotRuntimeLogAPI;

export type BotLogLevel = "info" | "warn" | "error";

export interface BotRuntimeLogPageQuery extends PageQuery {
  source?: BotPlatform;
  level?: BotLogLevel;
  site?: string;
  message?: string;
  occurred_time?: string[];
}

export interface BotRuntimeLogTable extends BaseType {
  source?: BotPlatform;
  level?: BotLogLevel;
  site?: string;
  message?: string;
  occurred_time?: string;
}
