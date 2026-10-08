import { request } from "@utils";
import type { BotPlatform } from "./group";

const API_PATH = "/bot/parse-log";

const BotParseLogAPI = {
  listBotParseLog(query: BotParseLogPageQuery) {
    return request<ApiResponse<PageResult<BotParseLogTable>>>({
      url: `${API_PATH}/list`,
      method: "get",
      params: query,
    });
  },

  detailBotParseLog(query: number) {
    return request<ApiResponse<BotParseLogTable>>({
      url: `${API_PATH}/detail/${query}`,
      method: "get",
    });
  },

  siteStatsBotParseLog(query: Omit<BotParseLogPageQuery, "page_no" | "page_size">) {
    return request<ApiResponse<SiteCount[]>>({
      url: `${API_PATH}/site-stats`,
      method: "get",
      params: query,
    });
  },
};

/** 某个网站的日志条数；site 为 null 表示与解析无关的日志（「其他」） */
export interface SiteCount {
  site: string | null;
  count: number;
}

export default BotParseLogAPI;

export type BotParseSource = "auto" | "command";
export type BotParseResult = "success" | "failed" | "busy";

export interface BotParseLogPageQuery extends PageQuery {
  platform?: BotPlatform;
  source?: BotParseSource;
  site?: string;
  result?: BotParseResult;
  group_external_id?: string;
  user_name?: string;
  url?: string;
  occurred_time?: string[];
}

export interface BotParseLogTable extends BaseType {
  platform?: BotPlatform;
  source?: BotParseSource;
  group_external_id?: string;
  group_name?: string;
  user_external_id?: string;
  user_name?: string;
  url?: string;
  site?: string;
  result?: BotParseResult;
  error_message?: string;
  duration_ms?: number;
  file_count?: number;
  compressed?: boolean;
  occurred_time?: string;
}
