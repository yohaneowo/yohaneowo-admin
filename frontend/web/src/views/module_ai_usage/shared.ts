import { ElAvatar } from "element-plus";

/** 费用归属的平台。system = 多人共用的调用（例如新片的向量），不算在任何人头上 */
export const PLATFORM_LABEL: Record<string, string> = {
  discord: "Discord",
  line: "LINE",
  admin: "后台",
  system: "系统（共用）",
};

const PROVIDER_LABEL: Record<string, string> = {
  anthropic: "Anthropic",
  voyage: "Voyage",
  openai: "OpenAI",
  deepseek: "DeepSeek",
  gemini: "Gemini",
};

/** 供应商的显示名称；没收录的照原样显示 */
export function providerLabel(value: string) {
  return PROVIDER_LABEL[value] ?? value;
}

/** 美元金额：小额显示到 0.0001，未定价显示「未定价」 */
export function formatUsd(value?: string | number | null) {
  if (value == null) return "未定价";
  const amount = Number(value);
  if (amount === 0) return "$0";
  return amount < 0.01 ? `$${amount.toFixed(4)}` : `$${amount.toFixed(2)}`;
}

export function formatTokens(value?: number | null) {
  if (value == null) return "—";
  return value >= 1000 ? `${(value / 1000).toFixed(1)}k` : String(value);
}

/** 头像 + 名称；system 没有头像，显示「系统（共用）」 */
export function renderCaller(row: {
  user_platform?: string | null;
  user_external_id?: string | null;
  user_name?: string | null;
  user_avatar_url?: string | null;
}) {
  const name: string =
    row.user_platform === "system"
      ? (PLATFORM_LABEL.system ?? "系统")
      : row.user_name || row.user_external_id || "—";
  return h("div", { class: "flex items-center gap-2" }, [
    h(ElAvatar, { src: row.user_avatar_url ?? undefined, size: 24 }, () =>
      row.user_platform === "system" ? "⚙" : name.slice(0, 1)
    ),
    h("span", name),
  ]);
}
