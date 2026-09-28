#!/usr/bin/env bash
# 一次性：把本仓库配置成「fork 定制 + 持续同步上游」工作流。
#
# 幂等，可重复执行。只改 .git/config（本地配置，不入库）和 fork/ 下的新文件，
# 不修改任何上游跟踪的文件。
#
# 用法（仓库根目录）：
#   bash fork/init-fork.sh
#   bash fork/init-fork.sh --production-tag v0.8.3

set -euo pipefail

ROOT="$(git rev-parse --show-toplevel)"
cd "$ROOT"

UPSTREAM_URL="https://github.com/Tencent/WeKnora.git"
FORK_REMOTE="origin"
MIRROR="main"
CUSTOM="feature"
PRODUCTION_TAG=""

while [ $# -gt 0 ]; do
  case "$1" in
    --production-tag) PRODUCTION_TAG="${2:-}"; shift 2 ;;
    -h|--help) sed -n '2,12p' "$0"; exit 0 ;;
    *) echo "未知参数: $1" >&2; exit 2 ;;
  esac
done

say()  { printf '\n\033[1m== %s\033[0m\n' "$*"; }
warn() { printf '\033[33m⚠  %s\033[0m\n' "$*" >&2; }
die()  { printf '\033[31m✗  %s\033[0m\n' "$*" >&2; exit 1; }

# ---------------------------------------------------------------- 1. remote
say "配置 remote"
if git remote get-url upstream >/dev/null 2>&1; then
  echo "upstream 已存在：$(git remote get-url upstream)"
else
  git remote add upstream "$UPSTREAM_URL"
  echo "已添加 upstream -> $UPSTREAM_URL"
fi

# 物理杜绝误推上游：pushurl 指向一个不存在的目标，任何 push 都会失败
git config --local remote.upstream.pushurl "DISABLED-no-push-to-upstream"
git config --local remote.pushDefault "$FORK_REMOTE"
git config --local remote.origin.mirror false

# ---------------------------------------------------------------- 2. 行为配置
say "写入本地 git 配置"
git config --local rerere.enabled true
git config --local rerere.autoUpdate true
git config --local merge.conflictStyle zdiff3
git config --local merge.renameLimit 20000
git config --local rebase.forkPoint false      # 让 --fork-point 默认关闭，行为确定
git config --local rebase.autoStash false      # 宁可要求干净工作区，可预测 > 方便
git config --local pull.ff only                # ★全局 pull.rebase=false 是定时炸弹，这里让它直接失败
git config --local fetch.prune true
git config --local push.default simple
git config --local push.autoSetupRemote true
git config --local push.useForceIfIncludes true
git config --local core.longpaths true

# main 的语义是「上游镜像」，对比基准应指向上游
git config --local branch."$MIRROR".remote upstream
git config --local branch."$MIRROR".merge refs/heads/main

# ---------------------------------------------------------------- 3. 钩子
# 守卫必须装到工作区之外的 .git/hooks/：fork/ 只在 feature 上存在，切到 main 会被删掉，
# 而 main 正是守卫最需要生效的分支（详见 fork/install-hooks.sh 顶部的说明）。
mkdir -p fork/.state
bash "$ROOT/fork/install-hooks.sh"

# ---------------------------------------------------------------- 4. 首次抓取
say "抓取上游（--prune --tags）"
git fetch --prune --tags upstream

# ---------------------------------------------------------------- 5. 状态基点
if [ ! -f fork/.state/base ]; then
  base="$(git merge-base "$MIRROR" "$CUSTOM")"
  printf '%s\n' "$base" > fork/.state/base
  echo "已记录同步基点 base = $base"
else
  echo "基点已存在：$(cat fork/.state/base)"
fi

if [ -n "$PRODUCTION_TAG" ]; then
  git rev-parse --verify -q "refs/tags/$PRODUCTION_TAG" >/dev/null \
    || die "tag $PRODUCTION_TAG 不存在（先 git fetch --tags upstream）"
  printf '%s\n' "$PRODUCTION_TAG" > fork/production-tag
  echo "已记录 production 锚定 tag = $PRODUCTION_TAG（fork/production-tag，需入库）"
fi

# ---------------------------------------------------------------- 6. 收尾
say "初始化完成"
cat <<EOF

分支模型：
  $MIRROR        上游纯镜像，永不提交，只允许 fast-forward
  $CUSTOM    定制开发线，rebase 到 $MIRROR
  production   同一批定制，rebase 到上游 release tag，用于构建部署

日常同步：  bash fork/sync-upstream.sh
先看计划：  bash fork/sync-upstream.sh --dry-run

人工待办（服务端设置，脚本做不了）：
  1. 给 fork 的 $MIRROR 开分支保护（禁 force push / 禁直接 push / 要求 PR）
     gh api -X PUT "repos/$(git remote get-url $FORK_REMOTE | sed -E 's#.*[:/]([^/]+/[^/.]+)(\.git)?$#\1#')/branches/$MIRROR/protection" \\
       -f 'required_pull_request_reviews[required_approving_review_count]=1' \\
       -f 'allow_force_pushes=false' -f 'allow_deletions=false' -f 'enforce_admins=true'
  2. 关掉 fork 上必然失败、又需要 DockerHub secrets 的 workflow
     gh workflow disable docker-image.yml -R <你的fork>
     （否则每次把 $MIRROR 推到 fork 都会失败一次并发邮件）

下一步：先做「上游撞车检查」再动手写定制，见 fork/README.md 第 6 节。
EOF
