// 项目依赖各页面共用的标签与时间显示

export const STATUS_TAG = {
  healthy: { type: "success", text: "🟢 健康" },
  warning: { type: "warning", text: "🟡 注意" },
  risk: { type: "danger", text: "🔴 风险" },
  unknown: { type: "info", text: "未知" },
  error: { type: "danger", text: "检查失败" },
  skipped: { type: "info", text: "不检查" },
} as const;

export const KIND_TAG = {
  npm: { type: "danger", text: "npm" },
  pypi: { type: "primary", text: "PyPI" },
  github: { type: "info", text: "GitHub" },
  docker: { type: "primary", text: "Docker" },
  none: { type: "info", text: "其他" },
} as const;

// 漏洞等级（GitHub Advisory 的分级）
export const SEVERITY_TAG = {
  CRITICAL: { type: "danger", text: "严重" },
  HIGH: { type: "danger", text: "高" },
  MODERATE: { type: "warning", text: "中" },
  LOW: { type: "info", text: "低" },
} as const;

// 总览「需要处理」的等级
export const LEVEL_TAG = {
  critical: { type: "danger", text: "🔴 紧急" },
  high: { type: "warning", text: "🟠 重要" },
  medium: { type: "info", text: "🟡 留意" },
} as const;

/** 后端时间按 UTC 存、不带时区，补上 Z 再算「多久之前」 */
export function timeAgo(value?: string | null) {
  if (!value) return "—";
  const days = Math.floor(
    (Date.now() - new Date(`${value.replace(" ", "T")}Z`).getTime()) / 86400000
  );
  if (days < 1) return "今天";
  if (days < 30) return `${days} 天前`;
  if (days < 365) return `${Math.floor(days / 30)} 个月前`;
  const years = Math.floor(days / 365);
  const months = Math.floor((days % 365) / 30);
  return months ? `${years} 年 ${months} 个月前` : `${years} 年前`;
}

export function osvUrl(id: string) {
  return `https://osv.dev/vulnerability/${id}`;
}
