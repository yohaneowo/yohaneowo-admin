#!/usr/bin/env bash
# 构建并推送 NAS 用的两个镜像（在电脑上用 Git Bash 执行）：
#   yohane0w0/yohaneowo-admin-backend:{latest,<git 版本>}
#   yohane0w0/yohaneowo-admin-web:{latest,<git 版本>}
#
# 用法：docker/nas/publish.sh [git 引用，默认 master]
#
# 从「已提交」的代码构建：先把指定引用检出到临时 worktree，工作目录里没提交的改动不会被打包。
# 需要：docker（已 docker login）、pnpm、git。
set -euo pipefail

REF="${1:-master}"
REGISTRY="yohane0w0"
REPO_ROOT="$(git -C "$(dirname "$0")" rev-parse --show-toplevel)"
NAS_DIR="$REPO_ROOT/docker/nas"
WORKTREE="$(mktemp -d)/yohaneowo-admin-build"
VERSION="$(git -C "$REPO_ROOT" rev-parse --short "$REF")"

cleanup() {
	git -C "$REPO_ROOT" worktree remove --force "$WORKTREE" 2>/dev/null || true
	rm -rf "$NAS_DIR/dist"
}
trap cleanup EXIT

echo "==> 检出 $REF ($VERSION)"
git -C "$REPO_ROOT" worktree add --detach "$WORKTREE" "$REF" >/dev/null

echo "==> 构建前端"
(
	cd "$WORKTREE/frontend/web"
	# 前后端同源部署：API 用相对路径（.env 里的 /api/v1），SSE 端点留空即用当前页面的源
	cat > .env.production <<'EOF'
VITE_APP_ENV = prod
VITE_APP_TITLE = yohaneowo-admin
VITE_API_BASE_URL =
VITE_APP_WS_ENDPOINT =
VITE_DROP_CONSOLE = true
EOF
	pnpm install --frozen-lockfile
	pnpm build:prod
)
rm -rf "$NAS_DIR/dist"
cp -r "$WORKTREE/frontend/web/dist" "$NAS_DIR/dist"

echo "==> 构建后端镜像"
docker build -f "$WORKTREE/docker/backend/Dockerfile" \
	-t "$REGISTRY/yohaneowo-admin-backend:latest" \
	-t "$REGISTRY/yohaneowo-admin-backend:$VERSION" \
	"$WORKTREE"

echo "==> 构建前端镜像"
docker build -f "$NAS_DIR/web.Dockerfile" \
	-t "$REGISTRY/yohaneowo-admin-web:latest" \
	-t "$REGISTRY/yohaneowo-admin-web:$VERSION" \
	"$NAS_DIR"

if [[ "${NO_PUSH:-}" == "1" ]]; then
	echo "==> NO_PUSH=1，只构建不推送"
	exit 0
fi

echo "==> 推送"
for image in yohaneowo-admin-backend yohaneowo-admin-web; do
	docker push "$REGISTRY/$image:latest"
	docker push "$REGISTRY/$image:$VERSION"
done

echo "==> 完成：$VERSION。NAS 上执行 docker compose pull && docker compose up -d"
