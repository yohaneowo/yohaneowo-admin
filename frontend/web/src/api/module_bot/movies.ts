import { request } from "@utils";
export interface MovieSubscription {
  profileId: string;
  userId: string | null;
  siteUserId: string | null;
  traktUser: string;
  traktName: string;
  region: string;
  active: boolean;
  recommendations: boolean;
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
