import { request } from "@utils";
export interface MovieSubscription {
  userId: string;
  traktUser: string;
  traktName: string;
  region: string;
  active: boolean;
  subscribedAt: string;
  syncedAt: string | null;
  syncError: string | null;
  syncRequested: boolean;
  syncing: boolean;
}
export default {
  list(query: PageQuery) {
    return request<ApiResponse<PageResult<MovieSubscription>>>({
      url: "/bot/movies/list",
      method: "get",
      params: query,
    });
  },
  refresh(id: string) {
    return request<ApiResponse>({ url: "/bot/movies/" + id + "/refresh", method: "post" });
  },
};
