import { request } from "@utils";

const API_PATH = "/bot/voice-session";

const BotVoiceSessionAPI = {
  listBotVoiceSession(query: BotVoiceSessionPageQuery) {
    return request<ApiResponse<PageResult<BotVoiceSessionTable>>>({
      url: `${API_PATH}/list`,
      method: "get",
      params: query,
    });
  },

  detailBotVoiceSession(query: number) {
    return request<ApiResponse<BotVoiceSessionTable>>({
      url: `${API_PATH}/detail/${query}`,
      method: "get",
    });
  },

  userStatsBotVoiceSession(
    query: Omit<BotVoiceSessionPageQuery, "page_no" | "page_size"> & { limit?: number }
  ) {
    return request<ApiResponse<BotVoiceUserStat[]>>({
      url: `${API_PATH}/user-stats`,
      method: "get",
      params: query,
    });
  },
};

export default BotVoiceSessionAPI;

export type BotVoiceEndReason = "leave" | "move" | "restart";

export interface BotVoiceSessionPageQuery extends PageQuery {
  guild_external_id?: string;
  channel_name?: string;
  user_name?: string;
  user_external_id?: string;
  end_reason?: BotVoiceEndReason;
  joined_time?: string[];
}

export interface BotVoiceSessionTable extends BaseType {
  guild_external_id?: string;
  group_name?: string;
  group_icon_url?: string;
  channel_external_id?: string;
  channel_name?: string;
  user_external_id?: string;
  user_name?: string;
  user_avatar_url?: string;
  joined_time?: string;
  joined_time_estimated?: boolean;
  left_time?: string;
  duration_seconds?: number;
  end_reason?: BotVoiceEndReason;
}

/** 语音时长排行的一行 */
export interface BotVoiceUserStat {
  user_external_id: string;
  user_name: string | null;
  user_avatar_url: string | null;
  total_seconds: number;
  session_count: number;
}
