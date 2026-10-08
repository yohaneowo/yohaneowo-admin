import { request } from "@utils";

const API_PATH = "/bot/group";

const BotGroupAPI = {
  listBotGroup(query: BotGroupPageQuery) {
    return request<ApiResponse<PageResult<BotGroupTable>>>({
      url: `${API_PATH}/list`,
      method: "get",
      params: query,
    });
  },

  detailBotGroup(query: number) {
    return request<ApiResponse<BotGroupTable>>({
      url: `${API_PATH}/detail/${query}`,
      method: "get",
    });
  },

  updateBotGroup(id: number, body: BotGroupForm) {
    return request<ApiResponse>({
      url: `${API_PATH}/update/${id}`,
      method: "put",
      data: body,
    });
  },

  deleteBotGroup(body: number[]) {
    return request<ApiResponse>({
      url: `${API_PATH}/delete`,
      method: "delete",
      data: body,
    });
  },

  batchBotGroup(body: BatchType) {
    return request<ApiResponse>({
      url: `${API_PATH}/status/batch`,
      method: "patch",
      data: body,
    });
  },
};

export default BotGroupAPI;

export type BotPlatform = "discord" | "line";

export interface BotGroupPageQuery extends PageQuery {
  name?: string;
  platform?: BotPlatform;
  external_id?: string;
  status?: number;
  is_joined?: boolean;
}

export interface BotGroupTable extends BaseType {
  platform?: BotPlatform;
  external_id?: string;
  name?: string;
  icon_url?: string;
  member_count?: number;
  status?: number;
  is_joined?: boolean;
  joined_time?: string;
  left_time?: string;
  last_active_time?: string;
  description?: string;
}

/** 后台只能改状态和备注，其余字段由 bot 上报 */
export interface BotGroupForm extends BaseFormType {
  status?: number;
  description?: string;
}
