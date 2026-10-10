import { request } from "@utils";

const API_PATH = "/project/dependency";

const ProjectDependencyAPI = {
  /** 搜索栏的下拉选项：有资料的项目与分类 */
  listFilters() {
    return request<ApiResponse<{ projects: string[]; categories: string[] }>>({
      url: `${API_PATH}/filters`,
      method: "get",
    });
  },

  overview(project?: string) {
    return request<ApiResponse<ProjectDependencyOverview>>({
      url: `${API_PATH}/overview`,
      method: "get",
      params: { project },
    });
  },

  listDependency(query: ProjectDependencyPageQuery) {
    return request<ApiResponse<PageResult<ProjectDependencyTable>>>({
      url: `${API_PATH}/list`,
      method: "get",
      params: query,
    });
  },

  detailDependency(query: number) {
    return request<ApiResponse<ProjectDependencyDetail>>({
      url: `${API_PATH}/detail/${query}`,
      method: "get",
    });
  },

  checkDependency(project?: string) {
    return request<ApiResponse<{ checked: number; failed: number }>>({
      url: `${API_PATH}/check`,
      method: "post",
      params: { project },
    });
  },
};

export default ProjectDependencyAPI;

export type DependencyKind = "npm" | "pypi" | "github" | "docker" | "none";
/** skipped：没有上游可查（如 ffmpeg、Node.js），只显示实际版本 */
export type DependencyStatus = "healthy" | "warning" | "risk" | "unknown" | "error" | "skipped";

export interface ProjectDependencyPageQuery extends PageQuery {
  project?: string;
  name?: string;
  category?: string;
  kind?: DependencyKind;
  status?: DependencyStatus;
  outdated?: boolean;
  critical?: boolean;
  vulnerable?: boolean;
  major_behind?: boolean;
}

export interface ProjectDependencyTable extends BaseType {
  project?: string;
  name?: string;
  /** 由上报方决定，如 后端、前端、前端开发工具、Docker 镜像 */
  category?: string;
  kind?: DependencyKind;
  source?: string;
  source_url?: string | null;
  usage?: string;
  /** 重点依赖：坏了影响最大 */
  critical?: boolean;
  installed_version?: string;
  reported_time?: string;
  latest_version?: string;
  upstream_updated_time?: string;
  archived?: boolean;
  outdated?: boolean;
  /** 落后一个大版本，升级可能有不兼容改动 */
  major_behind?: boolean;
  vulnerable?: boolean;
  vulnerabilities?: DependencyVulnerability[] | null;
  /** 已知漏洞的最高等级 */
  vuln_severity?: VulnSeverity | null;
  /** 修复全部已知漏洞要升级到的版本；有漏洞尚无修复时为空 */
  fix_version?: string | null;
  status?: DependencyStatus;
  check_error?: string;
  checked_time?: string;
}

export type VulnSeverity = "CRITICAL" | "HIGH" | "MODERATE" | "LOW";

export interface DependencyVulnerability {
  id: string;
  aliases: string[];
  summary: string;
  severity: VulnSeverity | null;
  /** 这个包的修复版本；为空表示尚无修复 */
  fixed: string | null;
}

export type AttentionLevel = "critical" | "high" | "medium";

export interface ProjectDependencyOverview {
  total: number;
  attention: number;
  vulnerable: number;
  vulnerability_count: number;
  major_behind: number;
  outdated: number;
  risk: number;
  warning: number;
  healthy: number;
  error: number;
  critical: number;
  last_checked_time: string | null;
  items: {
    dependency: ProjectDependencyTable;
    level: AttentionLevel;
    reasons: { level: AttentionLevel; text: string }[];
  }[];
}

export interface ProjectDependencyCheck extends BaseType {
  installed_version?: string;
  latest_version?: string;
  upstream_updated_time?: string;
  archived?: boolean;
  vulnerability_count?: number;
  status?: DependencyStatus;
  check_error?: string;
  checked_time?: string;
}

export interface ProjectDependencyDetail extends ProjectDependencyTable {
  checks?: ProjectDependencyCheck[];
}
