import { request } from "@utils";

const API_PATH = "/bot/portfolio";

/** 首页卡片与「资产记录」页共用的权限：总资产是敏感信息，只授予需要的角色 */
export const PORTFOLIO_PERM = "module_bot:portfolio:query";

const BotPortfolioAPI = {
  latestBotPortfolio() {
    return request<ApiResponse<BotPortfolioLatest>>({
      url: `${API_PATH}/latest`,
      method: "get",
    });
  },

  listBotPortfolio(query: BotPortfolioPageQuery) {
    return request<ApiResponse<PageResult<BotPortfolioSnapshot>>>({
      url: `${API_PATH}/list`,
      method: "get",
      params: query,
    });
  },
};

export default BotPortfolioAPI;

export interface PortfolioAccount {
  exchange: string;
  /** spot / futures / earn / funding / account / inverse-contract-wallet */
  type: string;
  /** USDT */
  total: number;
}

export interface BotPortfolioSnapshot extends BaseType {
  total_twd?: number;
  total_usdt?: number;
  twd_rate?: number;
  accounts?: PortfolioAccount[];
  /** 读取失败的账户；非空时总额少算了这些账户 */
  failures?: string[];
  occurred_time?: string;
}

export interface BotPortfolioLatest {
  latest: BotPortfolioSnapshot | null;
  previous_total_twd: number | null;
  change_percent: number | null;
}

export interface BotPortfolioPageQuery extends PageQuery {
  occurred_time?: string[];
}
