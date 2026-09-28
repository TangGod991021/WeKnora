#!/usr/bin/env bash
# 把 fork 守卫钩子装进 .git/hooks/（幂等，可重复执行）。
#
# 为什么不能直接把 core.hooksPath 指向 fork/git-hooks/：
#   工作区里的 fork/ 目录只在 feature 分支上存在。切到 main 时 git 会按 main 的树
#   重建工作区，把 fork/git-hooks/pre-commit 删掉 —— 而 main 恰恰是守卫最需要生效的
#   分支。此时 core.hooksPath 指向不存在的目录，git 会【静默跳过】所有钩子，
#   守卫形同虚设。所以守卫必须复制到工作区之外的 .git/hooks/。
#
# fork/git-hooks/ 仍是唯一真相源（受版本控制、可 review），本脚本负责分发。

set -euo pipefail

ROOT="$(git rev-parse --show-toplevel)"
SRC="$ROOT/fork/git-hooks"
GIT_DIR="$(cd "$(git rev-parse --git-dir)" && pwd)"
DST="$GIT_DIR/hooks"

say()  { printf '\033[1m== %s\033[0m\n' "$*"; }
warn() { printf '\033[33m⚠  %s\033[0m\n' "$*" >&2; }
die()  { printf '\033[31m✗  %s\033[0m\n' "$*" >&2; exit 1; }

[ -d "$SRC" ] || die "找不到 $SRC（是否在 feature 分支上？守卫源文件在那里）"
mkdir -p "$DST"

say "安装守卫钩子到 $DST"
for h in pre-commit pre-push; do
  [ -f "$SRC/$h" ] || die "缺少源文件 $SRC/$h"
  cp "$SRC/$h" "$DST/$h"
  chmod +x "$DST/$h"
  echo "  $h -> $DST/$h"
done

# core.hooksPath 必须留空：git 的内置默认就是 $GIT_DIR/hooks，会自动跟随仓库位置。
# 若指向别处（例如 scripts/git-hooks），.git/hooks/ 里的守卫就被绕过了。
# 不要在这里写绝对路径 —— 仓库一旦移动，绝对路径失效，守卫又会静默失灵。
cur="$(git config --get core.hooksPath || true)"
if [ -n "$cur" ]; then
  git config --local --unset core.hooksPath
  warn "已清除 core.hooksPath（原为 '$cur'）—— 指向那里会让 .git/hooks/ 里的守卫静默失效"
fi
echo "core.hooksPath 未设置 → git 使用内置默认 $DST ✓"

cat <<'EOF'

※ 若执行过 ./scripts/install-git-hooks.sh，core.hooksPath 会被改回 scripts/git-hooks，
  守卫随即静默失效。重跑本脚本即可修复：
      bash fork/install-hooks.sh
  （上游钩子不必通过 hooksPath 启用 —— 守卫会链式转调它们。）
EOF
