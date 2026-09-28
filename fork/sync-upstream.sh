#!/usr/bin/env bash
# 上游同步：fetch + ff-only 更新镜像 main + 备份 + rebase 定制线 + 明确指引 + 可恢复。
#
# 用法（仓库根目录，建议在 feature 上执行）：
#   bash fork/sync-upstream.sh --dry-run     # 只看计划，不改任何 ref
#   bash fork/sync-upstream.sh               # 同步并推送
#   bash fork/sync-upstream.sh --no-push     # 只本地同步，不推送
#   bash fork/sync-upstream.sh --build       # 同步后跑构建校验（强烈建议定期做）
#   bash fork/sync-upstream.sh --production  # 额外把 production 重放到锚定 tag
#
# 退出码：0 成功 / 2 参数错 / 3 rebase 冲突（现场保留） / 4 production 派生冲突

set -euo pipefail
cd "$(git rev-parse --show-toplevel)"

UPSTREAM_REMOTE="${UPSTREAM_REMOTE:-upstream}"
FORK_REMOTE="${FORK_REMOTE:-origin}"
MIRROR="${MIRROR:-main}"
CUSTOM="${CUSTOM:-feature}"
PRODUCTION_BRANCH="${PRODUCTION_BRANCH:-production}"
STATE_DIR="fork/.state"
BASE_FILE="$STATE_DIR/base"
TAG_FILE="$STATE_DIR/production-tag"

DRY_RUN=0; DO_PUSH=1; DO_BUILD=0; DO_PRODUCTION=0
while [ $# -gt 0 ]; do
  case "$1" in
    -n|--dry-run)  DRY_RUN=1 ;;
    --no-push)     DO_PUSH=0 ;;
    --build)       DO_BUILD=1 ;;
    --production)  DO_PRODUCTION=1 ;;
    -h|--help)     sed -n '2,12p' "$0"; exit 0 ;;
    *) echo "未知参数: $1" >&2; exit 2 ;;
  esac; shift
done

say()  { printf '\n\033[1m== %s\033[0m\n' "$*"; }
warn() { printf '\033[33m⚠  %s\033[0m\n' "$*" >&2; }
die()  { printf '\033[31m✗  %s\033[0m\n' "$*" >&2; exit 1; }

hints() {
  cat >&2 <<EOF
  ---- 冲突处理指引（现场已保留，未自动 abort）----
    git status                      # 看 U 状态文件
    git rerere status && git rerere diff
    解决后：  git add <文件> && git rebase --continue
    该补丁已无用：  git rebase --skip
    放弃本次同步：  git rebase --abort
    或硬回到同步前：git reset --hard $BACKUP_TAG
  ------------------------------------------------
EOF
}

BACKUP_TAG="(未创建)"
trap 'echo; warn "被中断（line $LINENO）。仓库保持原样。"' INT TERM

# ------------------------------------------------------------ 0. 前置检查
say "前置检查"
[ -z "$(git status --porcelain)" ] || die "工作区不干净，请先 commit 或 stash（本流程不做自动 stash）"
git rev-parse --verify -q "$CUSTOM" >/dev/null || die "分支 $CUSTOM 不存在"
git rev-parse --verify -q "refs/remotes/$UPSTREAM_REMOTE/main" >/dev/null \
  || die "缺少 $UPSTREAM_REMOTE/main，请先跑 bash fork/init-fork.sh"

cur="$(git symbolic-ref -q --short HEAD || echo DETACHED)"
# 真实同步要在 $MIRROR 上执行 ff-only 更新，而 git 拒绝更新已 checkout 的分支。
# --dry-run 是只读的，不受此限 —— 在 $MIRROR 上预览计划正是常见用法。
if [ "$cur" = "$MIRROR" ] && [ "$DRY_RUN" != 1 ]; then
  die "当前在 $MIRROR 上。git fetch 拒绝更新已 checkout 的分支，请先 git switch $CUSTOM
      （只想看计划：bash fork/sync-upstream.sh --dry-run，在 $MIRROR 上也能跑）"
fi
[ "$cur" = "$MIRROR" ] && warn "当前在 $MIRROR 上 —— 仅 --dry-run 可用，真实同步请先切到 $CUSTOM"
echo "当前分支: $cur  定制线: $CUSTOM  镜像: $MIRROR"

# ------------------------------------------------------------ 1. fetch 上游
say "fetch $UPSTREAM_REMOTE (--prune --tags)"
before_tags="$(git tag --list | sort)"
fetch_log="$(mktemp)"
if ! git fetch --prune --tags "$UPSTREAM_REMOTE" 2>&1 | tee "$fetch_log"; then
  grep -q "would clobber existing tag" "$fetch_log" \
    && warn "本地存在与上游同名但指向不同的 tag。解决：git tag -d <冲突tag>，或 fork 自建 tag 一律用 fork/ 前缀"
  rm -f "$fetch_log"
  die "fetch 失败（见上方输出）"
fi
rm -f "$fetch_log"

new_tags="$(comm -13 <(printf '%s\n' "$before_tags") <(git tag --list | sort) | grep -v '^fork/' || true)"
[ -n "$new_tags" ] && { echo "上游新增 tag："; printf '  %s\n' $new_tags; }

NEW_UP="$(git rev-parse "$UPSTREAM_REMOTE/main")"
OLD_MIRROR="$(git rev-parse "$MIRROR")"

# ------------------------------------------------------------ 2. dry-run
if [ "$DRY_RUN" = 1 ]; then
  say "DRY-RUN：以下操作会被执行（不改动任何 ref）"
  echo "  git fetch $UPSTREAM_REMOTE refs/heads/main:refs/heads/$MIRROR   # ff-only"
  echo "  git tag fork/backup/$CUSTOM/<utc-ts> $CUSTOM"
  echo "  git rebase --no-fork-point --onto $MIRROR <base> $CUSTOM"

  base="$(cat "$BASE_FILE" 2>/dev/null || git merge-base "$MIRROR" "$CUSTOM")"
  if ! git merge-base --is-ancestor "$base" "$CUSTOM" 2>/dev/null; then
    warn "记录的基点 $base 已不是 $CUSTOM 的祖先，实际会回退到 merge-base"
    base="$(git merge-base "$MIRROR" "$CUSTOM")"
  fi
  echo "  基点: $base"
  echo "  待重放定制补丁数: $(git rev-list --count "$base..$CUSTOM")"
  echo "  预计吸收上游 commit 数: $(git rev-list --count "$CUSTOM..$NEW_UP")"

  if [ "$OLD_MIRROR" = "$NEW_UP" ]; then
    echo "  $MIRROR 无变化，rebase 会被跳过"
  else
    echo "  $MIRROR 将前进: $(git rev-list --count "$OLD_MIRROR..$NEW_UP") commit"
    if git merge-tree --write-tree "$MIRROR" "$CUSTOM" >/dev/null 2>&1; then
      echo "  冲突预判: 干净（git merge-tree 退出 0）"
    else
      warn "冲突预判: 可能冲突，以下为冲突文件预览"
      git merge-tree --write-tree "$MIRROR" "$CUSTOM" 2>&1 | sed -n '1,20p' || true
    fi
  fi
  exit 0
fi

# ------------------------------------------------------------ 3. ff-only 更新镜像
say "ff-only 更新 $MIRROR（不切分支）"
if ! git fetch "$UPSTREAM_REMOTE" "refs/heads/main:refs/heads/$MIRROR"; then
  cat >&2 <<EOF
✗ $MIRROR 与 upstream/main 已分叉，或它在别的 worktree 被 checkout。
  分叉意味着有人往 $MIRROR 提交了东西 —— 这违反约定。请人工确认：
    git log --oneline $MIRROR ^$UPSTREAM_REMOTE/main    # 看多出来的是什么
    git reset --hard $UPSTREAM_REMOTE/main              # 确认无用后丢弃（旧值留在 reflog）
EOF
  exit 1
fi
MIRROR_NOW="$(git rev-parse "$MIRROR")"

if [ "$MIRROR_NOW" = "$OLD_MIRROR" ]; then
  say "上游无新提交，无需 rebase"
  exit 0
fi
echo "$MIRROR 前进: $(git rev-list --count "$OLD_MIRROR..$MIRROR_NOW") commit"

# ------------------------------------------------------------ 4. 备份
TS="$(date -u +%Y%m%dT%H%M%SZ)"
BACKUP_TAG="fork/backup/$CUSTOM/$TS"
git tag "$BACKUP_TAG" "$CUSTOM"
git tag "fork/backup/$MIRROR/$TS" "$OLD_MIRROR"
say "已备份: $BACKUP_TAG  (及 $MIRROR 旧值)"

# ------------------------------------------------------------ 5. rebase
base="$(cat "$BASE_FILE" 2>/dev/null || git merge-base "$MIRROR" "$CUSTOM")"
if ! git merge-base --is-ancestor "$base" "$CUSTOM" 2>/dev/null; then
  warn "基点 $base 已不是 $CUSTOM 的祖先，回退到 merge-base"
  base="$(git merge-base "$MIRROR" "$CUSTOM")"
fi
BEFORE="$(git rev-list --count "$base..$CUSTOM")"
say "重放 $BEFORE 条定制补丁：($base .. $CUSTOM) -> $MIRROR"

set +e
git rebase --no-fork-point --onto "$MIRROR" "$base" "$CUSTOM"
rc=$?
set -e

if [ $rc -ne 0 ]; then
  if [ -d "$(git rev-parse --git-path rebase-merge)" ] \
  || [ -d "$(git rev-parse --git-path rebase-apply)" ]; then
    warn "rebase 冲突，现场已保留"
    echo "冲突文件：" >&2
    git diff --name-only --diff-filter=U >&2 || true
    hints
    exit 3
  fi
  die "rebase 失败（退出 $rc），见上方输出；备份 tag: $BACKUP_TAG"
fi

# ------------------------------------------------------------ 6. 后置校验
say "后置校验"
git merge-base --is-ancestor "$MIRROR" "$CUSTOM" || die "$CUSTOM 未包含 $MIRROR，异常"
echo "✓ $CUSTOM 坐在 $MIRROR 之上"

merges="$(git rev-list --merges "$MIRROR..$CUSTOM")"
if [ -n "$merges" ]; then
  die "$CUSTOM 上出现 merge commit —— 说明有人执行过 git pull 造出 merge。
      请先 git rebase -i $MIRROR 拍平，再重新同步。
      备份 tag: $BACKUP_TAG"
fi
echo "✓ 定制序列线性，无 merge commit"

AFTER="$(git rev-list --count "$MIRROR..$CUSTOM")"
if [ "$AFTER" -lt "$BEFORE" ]; then
  warn "定制补丁数从 $BEFORE 降到 $AFTER —— 有补丁被判定为 clean cherry-pick 而静默丢弃。"
  warn "若这是「上游已实现了我的补丁」，属预期；若否，用 git reset --hard $BACKUP_TAG 回退。"
  warn "下次 rebase 丢的补丁："
  git log --oneline --right-only "$MIRROR...$CUSTOM" >&2 || true
fi
echo "✓ 定制补丁数: $AFTER"

printf '%s\n' "$MIRROR_NOW" > "$BASE_FILE"
printf '%s mirror=%s custom=%s n=%s\n' \
  "$(date -u +%FT%TZ)" "$MIRROR_NOW" "$CUSTOM" "$AFTER" >> "$STATE_DIR/last-sync"

# ------------------------------------------------------------ 7. 构建校验
if [ "$DO_BUILD" = 1 ]; then
  say "构建校验（rebase 干净 != 能编译）"
  go build ./cmd/server \
    || die "构建失败：rebase 干净但代码不兼容（典型：上游改了签名，或 revert 了你依赖的功能）。备份 tag: $BACKUP_TAG"
  echo "✓ go build ./cmd/server 通过"
fi

# ------------------------------------------------------------ 8. production 派生
if [ "$DO_PRODUCTION" = 1 ]; then
  [ -f "$TAG_FILE" ] || die "未记录 production tag，先跑：bash fork/init-fork.sh --production-tag <tag>"
  TAG="$(cat "$TAG_FILE")"
  git rev-parse --verify -q "refs/tags/$TAG" >/dev/null || die "tag $TAG 不存在"
  git merge-base --is-ancestor "$TAG" "$MIRROR" \
    || die "tag $TAG 不是 $MIRROR 的祖先（release 分支 hotfix 情形），请改用两 tag 形式"

  say "派生 $PRODUCTION_BRANCH := 定制补丁重放到 $TAG"
  git switch -C "$PRODUCTION_BRANCH" "$CUSTOM"
  set +e
  git rebase --no-fork-point --onto "$TAG" "$MIRROR" "$PRODUCTION_BRANCH"
  rc=$?
  set -e
  if [ $rc -ne 0 ]; then
    warn "$PRODUCTION_BRANCH 派生冲突"
    git diff --name-only --diff-filter=U >&2 || true
    hints
    exit 4
  fi
  git merge-base --is-ancestor "$TAG" "$PRODUCTION_BRANCH" || die "$PRODUCTION_BRANCH 异常"
  git tag -a "fork/build/$TAG-$TS" -m "fork build on $TAG (custom=$(git rev-parse --short "$CUSTOM"))"
  warn "$PRODUCTION_BRANCH 的 SHA 每次派生都会变。对外只认 fork/build/* tag，不要引用分支名。"
  git switch "$CUSTOM"
fi

# ------------------------------------------------------------ 9. 推送
if [ "$DO_PUSH" = 1 ]; then
  say "推送到 $FORK_REMOTE"
  git fetch --prune "$FORK_REMOTE"
  git push --force-with-lease --force-if-includes "$FORK_REMOTE" "$CUSTOM"

  tracked="$(git rev-parse --verify -q "refs/remotes/$FORK_REMOTE/$MIRROR" || echo none)"
  if [ "$tracked" != "$MIRROR_NOW" ]; then
    say "推送 $MIRROR 镜像到 fork"
    git push "$FORK_REMOTE" "$MIRROR:$MIRROR" \
      || warn "$MIRROR 推送被拒（fork 上的 $MIRROR 可能有额外提交，别强推，先查）"
  fi

  if [ "$DO_PRODUCTION" = 1 ]; then
    git push --force-with-lease --force-if-includes "$FORK_REMOTE" "$PRODUCTION_BRANCH"
  fi
fi

# ------------------------------------------------------------ 10. 健康检查
say "健康检查"
cat <<EOF
  ancestor : $(git merge-base --is-ancestor "$MIRROR" "$CUSTOM" && echo OK || echo FAIL)
  behind   : $(git rev-list --count "$CUSTOM..$UPSTREAM_REMOTE/main")  (应为 0)
  custom   : $AFTER 条定制补丁
  merges   : $(git rev-list --merges "$MIRROR..$CUSTOM" | wc -l)  (应为 0)
  upstreamed: $(git cherry -v "$MIRROR" "$CUSTOM" 2>/dev/null | grep -c '^-' || true)  (已被上游吸收的补丁数)
EOF
say "同步完成"
