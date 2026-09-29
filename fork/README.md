# fork 定制 + 上游同步工作流

## 1. 功能说明

本仓库是 [`Tencent/WeKnora`](https://github.com/Tencent/WeKnora) 的 fork。这套工作流解决一个问题：

> **`main` 必须永远等于上游，定制代码不能污染它；同时能以低代价、可重复地吃进上游的每日更新。**

核心思路是「**一份定制，两种视图**」：定制提交只真实存在于 `feature` 上，`production` 是它重放到上游 release tag 上的派生视图。配合 rebase 策略，`git diff upstream/main...feature` 任何时候都能一眼看清「我到底改了什么」。

放在 `fork/` 下是刻意的：这是上游不存在的路径，因此**本工作流的全部文件与上游结构性零冲突**。

## 0. 日常流程速查

### 心智模型（先记住这个）

| 分支 | 你怎么对待它 |
|---|---|
| `main` | **永远不碰。** 脚本自己更新，你不需要、也不应该 checkout 它工作 |
| `feature` | **你干活的地方。** 所有定制提交都落在这里 |
| `production` | **只在要部署时重建。** 不手工提交，不手工 rebase |

一句话：**你只在 `feature` 上写代码，其余交给脚本。**

### A. 写新功能（最常做）

```bash
git switch feature                  # 确保在 feature 上
# ── 动手前必做：上游撞车检查（上游日更，很容易重复实现）──
git log --oneline --since='90 days ago' upstream/main -- <你要改的目录>
git log --oneline -i --grep='<关键词>' upstream/main | head

# ── 写代码，然后提交 ──
git add <文件> && git commit -m "feat(scope): 说明"
git push origin feature
```

**补丁尽量写成「新文件 + 1~3 行接缝」**，不要改上游大文件里的几十行——那是冲突温床（详见第 6 节）。

### B. 同步上游 main 代码（定期做）

```bash
bash fork/sync-upstream.sh --dry-run     # ① 先看计划：待重放补丁数、冲突预判，不改任何 ref
bash fork/sync-upstream.sh --build       # ② 真同步 + 构建校验（推荐，能提前发现编译不兼容）
```

`--build` 会跑 `go build ./cmd/server`。**rebase 干净 ≠ 能编译**——上游改了签名或 revert 了
你依赖的功能时，rebase 会成功但构建会炸，这是最常见的隐性故障。

想先本地看看再推：加 `--no-push`。

### C. 同步时遇到冲突

脚本**不会自动 abort**，会保留现场并打印指引，退出码 3：

```bash
git status                                    # 看 U 状态文件
git rerere status && git rerere diff          # 看 rerere 是否已自动解过
#   ── 手工解决后 ──
git add <文件> && git rebase --continue
#   ── 放弃本次同步，回到同步前 ──
git rebase --abort
#   ── 或硬回退到脚本打印的备份 tag ──
git reset --hard fork/backup/feature/<时间戳>
```

**冲突解完继续，不要解一半就 abort**——rerere 只在「解决且操作继续」时记录解法，
解一半放弃等于这次白解，下次还要重解。

### D. 部署生产版本

```bash
bash fork/sync-upstream.sh --production      # 重建 production 并推送
```

生产构建**只认 `fork/build/<tag>-<时间戳>` 这个 tag**，不要引用 `production` 分支名——
它的 SHA 每次重建都会变。

升级到新的上游版本：改 `fork/production-tag` 里的版本号 → 提交 → 再跑上面那条。

### E. 上游把你的补丁吸收了

同步时脚本会告警 `定制补丁数从 N 降到 M`，并列出**被丢弃的那条**（打 `-` 的）。
这是预期行为（patch-id 完全相同才会被丢），去 `fork/README.md` 第 10 节「定制清单」里
把对应条目删掉即可。

### 三条铁律

1. **不在 `main` 上提交** —— 钩子会拦，且 `main` 必须保持与上游逐字节一致
2. **不在 `feature` 上执行 `git pull`** —— 全局 `pull.rebase=false` 会造出 merge commit，
   破坏「线性定制序列」不变式（脚本的后置校验会因此报错）
3. **推送定制分支用同步脚本，不要手敲 `--force`** —— 手工非快进推送会被 pre-push 守卫拦下

### 出问题时的自检命令

```bash
# 三条核心断言：只要都成立，工作流就是健康的
git merge-base --is-ancestor upstream/main feature && echo "✓ 在最新上游之上"
git rev-list --count feature..upstream/main          # 应为 0（不是 0 就该同步了）
git rev-list --merges upstream/main..feature | wc -l # 应为 0（线性，无 merge commit）

# 我到底改了什么（这就是你的冲突面）
git diff --stat upstream/main...feature
git log --oneline upstream/main..feature
```

## 2. 涉及文件清单

| 文件 | 用途 |
|---|---|
| `fork/README.md` | 本文档 |
| `fork/init-fork.sh` | 一次性初始化（幂等）：配 remote、写 git config、装钩子、记录同步基点 |
| `fork/sync-upstream.sh` | 日常同步：fetch → ff-only 镜像 main → 备份 → rebase feature |
| `fork/install-hooks.sh` | 把守卫从 `fork/git-hooks/` 分发到 `.git/hooks/`（幂等，可重复执行） |
| `fork/git-hooks/pre-commit` | **守卫真相源**：禁止在 `main`/`production` 上提交，链式转调上游钩子 |
| `fork/git-hooks/pre-push` | **守卫真相源**：禁止推 `upstream`、禁止强推 `main`，链式转调上游钩子 |
| `fork/production-tag` | **入库**：production 锚定的上游 release tag（当前 `v0.8.2`）。属项目配置，不是本地状态 |
| `fork/.state/` | 本地同步状态（基点、同步日志）。被 `.gitignore` 的 `.*` 规则自动忽略，不入库 |
| `CLAUDE.md`（仓库根） | 给 AI 的硬规则入口，指向本文档 |

**本工作流不修改任何上游跟踪的文件。** 唯一改动的 `core.hooksPath` 是 `.git/config` 里的本地配置项，不是文件。

> ⚠️ **`CLAUDE.md` 必须用 `git add -f` 强制纳入版本管理。**
> 上游 `.gitignore` 第 52 行是 `Claude.md`，而本机 `core.ignorecase=true`，因此它
> **大小写不敏感地匹配了根目录的 `CLAUDE.md`**（`git check-ignore -v CLAUDE.md` 可复现）。
> 若不用 `-f`，规则文件会停留在未跟踪状态，重新 clone 即丢失，违背「规则可复现」的要求。
> 已跟踪文件不受 `.gitignore` 影响，因此强制添加后不会再被忽略。

## 3. 核心逻辑

### 3.1 分支模型

```
upstream (Tencent/WeKnora)          ← 只读；pushurl 已指向 DISABLED-no-push-to-upstream
      │ fetch
      ▼
   main       纯镜像，永不提交，只允许 fast-forward
      │
      ▼ rebase --onto main <base>
  feature     权威定制线：写码、测试、构建都在这里
      │
      ▼ rebase --onto <release-tag> main
 production   同一批定制重放到上游 release tag，用于构建部署
```

三条分支职责单一。**定制提交只有一份真相源（`feature`）**，`production` 是派生视图 —— 每次由脚本重建，SHA 会变，因此**对外只认 `fork/build/*` tag，不要引用 `production` 分支名**。

### 3.2 为什么 `main` 不用 checkout

同步 `main` 用的是：

```bash
git fetch upstream refs/heads/main:refs/heads/main
```

不带 `+` 前缀的 refspec 在非快进时会**报错拒绝**（而非静默强推），正是想要的语义。副作用是 `main` 根本不需要 checkout —— 于是「在 main 上误提交」从「靠钩子拦」退化成「流程上不会发生」，钩子只是兜底。

两个已知边界：

- 当前若正 checkout 在 `main`（含任意 worktree），git 会 `fatal: refusing to fetch into branch ... checked out at ...` 而拒绝。`sync-upstream.sh` 会前置检测并提示。
- 本地 `main` 若与上游分叉，fetch 报 non-fast-forward 并拒绝。这是保护不是故障，脚本会给出「先 `git log` 看多出来什么，再决定 `git reset --hard`」的指引，**不会自动强推**。

### 3.3 定制补丁的静默丢弃（必须知道）

`git rebase` 会**静默丢弃 clean cherry-pick**（patch-id 与上游某提交完全相同的那些）。含义：

- 一旦你的补丁被上游以相同内容吸收，下次 rebase 会直接删掉这条 commit，只打印一行 `dropping <sha> -- patch contents already upstream`。
- 这是特性不是 bug，但必须被察觉。`sync-upstream.sh` 会比较 rebase 前后的补丁数，数量下降时显式告警。

因此**不要用 `--keep-base`**（它隐含 `--reapply-cherry-picks`，会让上述去重失效）。

另外，`--onto` 与 `--fork-point` 的组合一律显式写死：脚本用 `--no-fork-point --onto <newbase> <exclude>`，其中 `<exclude>` 取自 `fork/.state/base`（不是 reflog）—— reflog 会被 gc 掉，状态文件不会。

### 3.4 上游做了 rewrite 怎么办

remote-tracking ref 的 `+` 前缀会让 `upstream/main` 被强制更新，而本地 `main` 会报错。这时的现象是 `upstream/main` 悄悄前移、`main:main` 报 non-fast-forward。

排查（只读）：

```bash
git reflog show upstream/main | head -5
git log --oneline upstream/main@{1}..upstream/main     # 新增的
git log --oneline upstream/main..upstream/main@{1}     # "消失"的（被 rewrite 掉）
git range-diff upstream/main@{1}..upstream/main        # 改写后的内容对比，最有用
```

要点：**rewrite 之后不能再写 `git rebase main feature`**（会重复重放上游 commit），必须用 `--onto <new> <old-base>`。

## 4. 依赖关系

- **git ≥ 2.35**（worktree 感知的 head 检查）。本机实测 2.53.0.windows.1，满足。
- **bash**：脚本兼容 Git Bash。Windows 下用 `bash fork/xxx.sh` 调用，不要依赖可执行位（本仓库 `core.filemode=false`）。
- **上游 hook 体系**：`fork/git-hooks/*` 链式转调 `scripts/git-hooks/*`（上游跟踪的文件）。因此：
  - 链式调用必须用**绝对路径** exec —— 上游钩子靠 `dirname "$0"` 推导 ROOT。
  - `pre-push` 必须先 `STDIN_DATA="$(cat)"` 缓冲再回灌 —— git 通过 stdin 传 ref 列表，被读干会让上游检查**静默失活**。

### 4.1 守卫钩子为什么必须装在 `.git/hooks/`

这是本工作流踩过的一个**真实坑**，值得单独说明：

> 最初的设计是把 `core.hooksPath` 指向工作区内的 `fork/git-hooks/`。看起来更优雅（受版本控制、可 review），
> 但它有一个致命缺陷：**`fork/` 目录只在 `feature` 分支上存在**。当你 `git checkout main` 时，git 会按
> `main` 的树重建工作区，把 `fork/git-hooks/pre-commit` 一并删掉 —— 而 `main` 恰恰是守卫**最需要生效**
> 的分支。此时 `core.hooksPath` 指向一个不存在的目录，**git 会静默跳过所有钩子**，守卫形同虚设，
> 而且没有任何报错。（该失效已在实现过程中被实际复现。）

因此守卫必须放在**工作区之外**的 `.git/hooks/`：那里的文件不受任何分支切换影响。

- `fork/git-hooks/` 仍是**唯一真相源**（入库、可 review）。
- `fork/install-hooks.sh` 负责把守卫复制到 `.git/hooks/`，幂等，可重复执行。
- **`core.hooksPath` 留空** —— git 的内置默认就是 `$GIT_DIR/hooks`，会自动跟随仓库位置。
  不要写绝对路径：仓库一旦移动，绝对路径失效，守卫又会静默失灵（同一类故障的另一种形态）。
- 改完 `fork/git-hooks/` 下的文件后**必须重跑** `bash fork/install-hooks.sh`，否则改动不生效。

⚠️ **`./scripts/install-git-hooks.sh` 会破坏守卫**：它会把 `core.hooksPath` 设成 `scripts/git-hooks`，
`.git/hooks/` 里的守卫随即被绕过。上游钩子不必通过 hooksPath 启用 —— 守卫会链式转调它们。
若已误执行，跑一次 `bash fork/install-hooks.sh` 即可修复。

## 5. 使用方式

### 5.1 初始化（一次性）

```bash
bash fork/init-fork.sh
# 若要用 production 线，指定锚定的上游 release tag：
bash fork/init-fork.sh --production-tag v0.8.3
```

初始化后的人工待办（服务端设置，脚本做不了，见第 7 节）：

```bash
# 1) 给 fork 的 main 开分支保护
gh api -X PUT "repos/<你的fork>/branches/main/protection" \
  -f 'required_pull_request_reviews[required_approving_review_count]=1' \
  -f 'allow_force_pushes=false' -f 'allow_deletions=false' -f 'enforce_admins=true'

# 2) 关掉 fork 上必然失败、且需要 DockerHub secrets 的 workflow
gh workflow disable docker-image.yml -R <你的fork>
```

### 5.2 日常同步

```bash
bash fork/sync-upstream.sh --dry-run     # 先看计划：待重放补丁数、冲突预判，不改任何 ref
bash fork/sync-upstream.sh --build       # 同步 + 构建校验（推荐定期做）
bash fork/sync-upstream.sh --no-push     # 只本地同步，不推送
bash fork/sync-upstream.sh --production  # 额外派生 production
```

同步做了什么：fetch 上游（`--prune --tags`）→ 报告新增 tag → ff-only 更新 `main` → 打备份 tag `fork/backup/<分支>/<UTC时间戳>` → `rebase --no-fork-point --onto main <base> feature` → 后置校验（祖先性、无 merge commit、补丁数）→ 构建（可选）→ 推送。

**冲突时脚本不自动 abort**，会保留现场并打印指引，退出码 3。解完用 `git rebase --continue`；放弃用 `git rebase --abort` 或 `git reset --hard <备份tag>`。

推送一律 `--force-with-lease --force-if-includes`（`push.useForceIfIncludes` 已在初始化时设好，裸 `--force` 会被 pre-push 拦下）。

### 5.3 production 分支

`production` 的定制补丁重放到上游 release tag 上，用于构建部署。**当前锚定 `v0.8.2`**
（记录在入库文件 `fork/production-tag` 中）。

重建 / 升级：

```bash
# 升级到新版本：先改锚定 tag，再重新派生
printf 'v0.9.0\n' > fork/production-tag && git commit -am "chore(fork): production 锚定 v0.9.0"
bash fork/sync-upstream.sh --production --no-push   # 本地派生并验证
bash fork/sync-upstream.sh --production             # 验证通过后派生并推送
```

等价的手工形式：

```bash
git switch -C production feature
git rebase --no-fork-point --onto v0.9.0 main production
```

含义：「把 `production` 相对 `main` 多出来的提交，重放到 `v0.9.0` 上」。因为
`feature` = `main` + 定制，相对 `main` 多出来的正好是定制补丁。

**注意 `--production` 不依赖上游是否有新提交** —— 上游没动时也能重建（这正是最常见的用法）。
派生成功会打 `fork/build/<tag>-<时间戳>` 注解 tag，**对外只认这个 tag，不要引用 `production`
分支名**：它的 SHA 每次派生都会变。

⚠️ **v0.8.2 落后上游 main 67 个提交 / 736 个文件**，且**不含语雀目录结构保持功能**
（`4364e61a` 晚于 v0.8.2）。若生产上需要该功能，需等 v0.8.3 或改锚到更新的版本。

## 6. 缩小冲突面（最重要的一节）

rebase 策略的天花板由「定制差异面」决定。动手写任何定制**之前**，先做上游撞车检查：

```bash
git log --oneline --since='90 days ago' upstream/main -- <你要改的目录>
git log --oneline -i --grep='<关键词>' upstream/main | head
gh search prs --repo Tencent/WeKnora --state open <关键词>
```

按收益排序的手段：

1. **先确认上游有没有现成实现。** 实测：语雀的「保持目录结构」上游**已完整实现**（`4364e61a` / PR #3476，代码含 `BuildTOCPaths` / `BuildFolderFileName` / `ParseFolderSettings`），而飞书 wiki 连接器**没有**层级→文件夹映射（只有 `ListWikiNodesRecursiveFrom` 递归遍历）。**别重复造上游已有的轮子** —— 白赚的差异面缩减。
2. **补丁写成「新文件 + 1~3 行接缝」。** 不要改上游大文件里的几十行（冲突温床），而是新增自己的文件，在上游文件里只加一行注册/调用。上游 yuque 的实现就是最好的模板 —— 照它给 feishu 写一套，注册中枢在 `internal/container/container.go`。
3. **部署类定制走配置，不进 git 差异**：`.env`（已 gitignore）承载密钥与开关；`docker-compose.override.yml` 是 Compose 自动加载、上游尚未占用的天然切入点。
4. **版本标识用环境变量，不要改文件**：`scripts/get_version.sh` 支持 `EDITION` 环境变量，用 `EDITION=fork` 即可。不要改 `VERSION`、`CHANGELOG.md`（上游高频改动）。
5. **定期审视差异面**：`git diff --stat upstream/main...feature`。失控就说明该重构隔离了。

**更激进但更省事的选项**：上游明显接受外部贡献（已合并 3794 个 PR）。能上游化的定制就直接提 PR —— 一旦被 merge，定制层归零，维护成本从「每 2~4 周重放几千文件」变成 0。语雀需求已经证明这条路是通的。

## 7. CI 的现实（实测结论）

- **10 / 13 个 workflow 有 `pull_request` 触发且只过滤 `paths:`、不过滤 `branches:`** → 在 fork 内开 `feature → main` 的 PR 就能跑它们（app / frontend / cli / go-lint / docreader / helm / anydoc / dsh-plugin / mcp-server / cli-e2e）。想借 CI 就跑 PR。
- **`docker-image.yml` 在 `push: branches: [main]` 和 `tags: v*` 触发，且需要 DockerHub secrets** —— fork 里没有，所以每次把 `main` 推到 fork 都会失败一次并发邮件。**应对：`gh workflow disable docker-image.yml`**（服务端设置，不动文件）。
- **不要把上游的 `v*` tag push 到 fork**（会触发上面的失败）。fork 自建 tag 一律用 `fork/` 前缀：既避开 `v*` 匹配，也避开与上游 tag 同名冲突（同名不同 SHA 会让 `git fetch --tags` 报 `would clobber existing tag` 而中断）。
- **不要用 `--prune-tags`**：它会删掉上游没有的本地 tag，包括 `fork/backup/*` 和 `fork/build/*`。
- 需要定制分支专属检查时，**新增**一个 fork 命名前缀的 workflow 文件（新文件永不冲突），不要改那 13 个。

## 8. Windows / 本机环境注意事项

- **不要改 `core.autocrlf`**。实测工作区 1646 / 4604 个文件是 CRLF、索引是 LF，靠全局 `core.autocrlf=true` 维持一致；改动会让这批文件全部显示为已修改，制造整文件级的假冲突。
- `.gitattributes` 的 `*.sh text eol=lf`、`*.go text eol=lf` 是**无斜杠模式、匹配任意层级**，所以 `fork/*.sh` 自动获得 LF，无需改动 `.gitattributes`。
- `.gitignore` 第二条是 `.*`（无斜杠 → **任意层级**匹配），所以 `fork/.state/` 天然被忽略；但若想让 `fork/` 下某个点文件入库，必须 `git add -f`。
- `core.filemode=false` → 不要依赖可执行位，用 `bash fork/xxx.sh` 调用。
- `core.ignorecase=true` → **不要做仅大小写不同的文件重命名**，行为不一致。

## 9. 验证手段

```bash
# 核心断言 1：feature 干净地坐在上游最新之上（behind 应为 0）
git merge-base --is-ancestor upstream/main feature && echo "ancestor=OK"
git rev-list --count feature..upstream/main

# 核心断言 2：feature 上只有自己的定制，且线性（merges 应为 0）
git log --oneline upstream/main..feature
git rev-list --merges upstream/main..feature | wc -l

# 差异面总览
git diff --stat upstream/main...feature

# 哪些补丁已被上游吸收（下次 rebase 会被静默丢弃，以 '-' 开头）
git cherry -v upstream/main feature | grep '^-'

# 钩子生效：守卫必须装在 .git/hooks/，且 core.hooksPath 必须留空
ls .git/hooks/ | grep -v sample          # 期望看到 pre-commit / pre-push
git config --get core.hooksPath          # 期望无输出（未设置）

# ★关键回归测试：在一个【没有 fork/ 目录】的分支上提交也应被拒绝
#   （用 production 派生自 upstream/main 复现，比拿 main 做测试安全）
git branch -f production upstream/main
git switch production && git commit --allow-empty -m probe    # 期望被拒
git switch feature && git branch -D production

# 误推上游已被阻断（期望失败）
git push upstream main

# 演练（在临时 worktree，不影响主工作区）
git worktree add ../weknora-sync-test feature
bash fork/sync-upstream.sh --dry-run
git worktree remove ../weknora-sync-test
```

`sync-upstream.sh` 每次结束会自动打印健康检查（ancestor / behind / custom / merges / upstreamed）。

## 10. 定制清单

**每次新增或删除定制提交时同步更新本节。** 这是冲突处理的第一手资料，也是判断「某项定制该不该退休」的依据。

| # | 定制内容 | 涉及的上游文件 | 补丁方式 | 上游是否已有替代实现 |
|---|---|---|---|---|
| 1 | 本工作流自身（`fork/` 工具、`CLAUDE.md`） | **无** —— 全部是上游不存在的新路径 | 新增文件 | 不适用（fork 专属） |
| 2 | 报表自动化方案（语雀报表文档规范 + `order-report` 技能包 + Agent 提示词 + 分层自检清单），见 `fork/report-automation/README.md` | **无** —— 全部新增文件，落在上游不存在的 `fork/report-automation/` | 新增文件（零上游代码改动） | 上游**无**报表/导出类实现；本方案只用上游既有的「上传技能包 + 沙箱密钥 + 自定义 Agent + 沙箱产物链路」四个扩展点 |
| 3 | **飞书数据源同步保持目录结构**：把源节点层级编码进 `FileName`，供下游拆成 `folder_path`。另修 `usesSourceIdentityDuplicateCheck` 的漏项 | `feishu/core/engine.go`（+17）、`feishu/wiki/connector.go`（+4/-3）、`feishu/drive/connector.go`（+5/-4）、`knowledge_create.go`（+21/-1）；新增文件 6 个（`datasource/folder.go`、`feishu/core/folder_path.go` + 4 个测试） | 新文件 + 接缝，接缝合计 **55 行** | 上游**无**飞书层级→文件夹映射。语雀同类能力上游已实现（`4364e61a` / PR #3476，含 `folder_mode`/`toc_only` 开关），**复用它，不要自己写** |

> 现状：`feature` 共 18 个提交，其中业务定制 2 个（第 2、3 项）。
> 第 1、2 项是**纯新增文件**（不动上游任何路径），冲突面为零；第 3 项**首次触及上游文件**，
> 接缝合计 55 行（`engine.go` 17 / `knowledge_create.go` 22 / wiki 7 / drive 9），
> 是当前唯一的冲突面来源。别让它继续长。
>
> 第 2 项落地时确认过的上游事实（供后续复用，别重复踩）：
> - `database_query` 工具的白名单**硬编码**为 `knowledge_bases` / `knowledges` / `chunks`
>   三张表（`internal/agent/tools/database_query.go:260`），**不能**用来查业务库；
>   全仓无 text2sql / nl2sql 模块，`internal/datasource/connector/` 下只有文档源连接器
>   （语雀/Notion/飞书/Confluence），没有数据库连接器。
> - 技能的环境变量声明走 `.weknora/requirements.json`，值由管理员在「沙箱密钥」填写，
>   AES-GCM 加密；变量名必须**字面出现在包内某个文件里**才会被登记
>   （`bundleMentionsEnvName`，见 `internal/application/service/tenant_skill_env_declare.go`）。
> - 已安装技能执行时 WorkDir 是 `/workspace` 而非技能目录，脚本必须用
>   `$WEKNORA_SKILL_DIR` 定位（`internal/agent/skills/manager.go:33`）。
> - 切块器保护 Markdown 表格行（跨块重复表头），但**不保护代码块**
>   （`internal/infrastructure/chunker/splitter.go` 的 `protectedPatterns`）。
>
> 第 3 项落地时确认过的上游事实：
> - **上游语雀的「保持目录结构」是默认关闭的开关，不是缺失的功能。** 2026-09-29 实测
>   「同步后前端没有目录层级」，排查结论是开关没开，不是 bug：`defaultFolderSettings()`
>   返回 `folderModeNone`（`internal/datasource/connector/yuque/types.go:389`），前端只在
>   **新建**数据源时才写 `folder_mode='toc'`（`DataSourceEditorDialog.vue` 的 `selectType()`，
>   注释原文 "Only on create: an existing source keeps what it was built with"），
>   **已存在的数据源不会被改**。所以「目录不显示」的第一嫌疑永远是开关。
>   判定方法：编辑该语雀数据源，把「保持目录结构」选成 toc 重跑同步，看后端有没有
>   `[Yuque] folder path derivation enabled: folder_mode=toc`（`connector.go:165`）。
>   这条排查路径本可以省掉一整轮重复实现，记在这里。
> - `FetchedItem.FileName` 是目录层级的**唯一**载体：下游靠 `SplitKnowledgeRelativePath`
>   → `NormalizeKnowledgeFolderPath` 拆出 `folder_path`
>   （`internal/types/knowledge_folder.go:83`）。连接器要产出目录，只能把层级编码进
>   FileName，没有第二条路。
> - `usesSourceIdentityDuplicateCheck`（`knowledge_create.go`）决定该通道按源路径还是按
>   内容去重。**上游语雀在 `folder_mode=toc` 下已经输出带路径的 FileName，却没有把自己
>   加进这个名单**（上游至今只有 GitLab/Confluence），同内容不同目录的文档会被误判为重复
>   丢弃 —— 这是上游漏项，第 3 项一并补上 Feishu/FeishuDrive/LarkDrive/Yuque。
> - 本机 `internal/types` 包 init 时 `gojieba` 的 CGO 会段错误（`_Cfunc_NewJieba`，
>   Exception 0xc0000005），**所有 import `internal/types` 的测试包在本机都跑不起来**。
>   已在纯净 `upstream/main` 上复现，与定制无关。本机验证只能靠
>   `go build ./cmd/server` + `go vet`（vet 会编译测试文件但不跑 init）。
>
> 下一个计划中的定制：暂无。语雀那部分上游已实现（`4364e61a` / PR #3476），直接复用，
> 不要自己写。动手前先做第 6 节的上游撞车检查。

## 11. 变更记录

| 日期 | 变更 |
|---|---|
| 2026-09-28 | 初版：建立三分支模型、初始化与同步脚本、链式钩子、AI 硬规则 |
| 2026-09-28 | 修复守卫在 `main` 上静默失效：改由 `fork/install-hooks.sh` 分发到 `.git/hooks/`，`core.hooksPath` 留空（见 4.1 节） |
| 2026-09-28 | 新增 `fork/.gitattributes`，把无扩展名的钩子钉死为 LF（否则 `core.autocrlf=true` 下下次 checkout 会破坏 shebang） |
| 2026-09-28 | `--production` 脱离「上游是否有新提交」：原先 main 没动就 `exit 0`，导致 production 永远派生不出来 |
| 2026-09-28 | 锚定 tag 从本地状态 `fork/.state/production-tag` 改为入库的 `fork/production-tag` |
| 2026-09-28 | 建立 `production` 分支并推送至 origin，锚定 `v0.8.2` |
| 2026-09-28 | 修：同步脚本被自己的 pre-push 守卫挡住 —— 脚本作为授权路径显式设 `ALLOW_FORCE_PUSH=1`（它一律用 `--force-with-lease`） |
| 2026-09-28 | 新增「排障：git 连不上 GitHub」小节（系统代理与 git 代理的区别、hosts 不能配代理） |
| 2026-09-28 | 新增第 0 节「日常流程速查」：写功能 / 同步上游 / 处理冲突 / 部署 / 自检命令 |
| 2026-09-28 | 第 10 节新增定制项 2：报表自动化方案（`fork/report-automation/`，纯新增文件，不动上游路径）；同节记入落地时确认的上游事实（`database_query` 白名单硬编码、技能 env 声明的登记规则、`$WEKNORA_SKILL_DIR`、代码块不被切块保护） |
| 2026-09-29 | 第 10 节新增定制项 3：飞书数据源同步保持目录结构（首次触及上游文件，接缝 55 行）；同节记入「语雀目录开关默认关闭」的排查结论、`FileName` 是目录层级唯一载体、`usesSourceIdentityDuplicateCheck` 的上游漏项、本机 gojieba CGO 导致测试跑不起来的限制 |
| 2026-09-29 | 同步上游至 `1912ba26c`（吸收 23 个提交），`feature` 重放 16→18 条定制补丁 |

### 验证记录（模拟上游）

真实上游的推进不可控，因此上述路径是用一个**模拟上游**端到端实测的：从本仓库克隆一份带工作区的
副本作为假上游，在其上制造提交，再把 `UPSTREAM_REMOTE` 指向它跑真实同步。

| 场景 | 结果 |
|---|---|
| `--dry-run` 正常路径 | ✓ 打印基点、待重放补丁数、冲突预判 |
| 干净同步（无冲突） | ✓ ff-only 更新 main → 备份 → rebase → 后置校验 → 健康检查 → 写状态文件 |
| 冲突（上游改同一文件） | ✓ 退出码 3、**现场保留不自动 abort**、列出冲突文件、提示带真实备份 tag |
| 冲突后 `rebase --continue` | ✓ 继续跑通，备份 tag 可回退 |
| 上游吸收了我的补丁 | ✓ 检出静默丢弃并列出**被丢弃的那一条** |
| 全新 clone（无 `.state/`） | ✓ 自动补建状态目录，用 `merge-base` 回退基点 |
| 在无 `fork/` 的分支上提交 | ✓ 守卫拦截（来自 `.git/hooks/`，不受分支影响） |
| 误推 upstream | ✓ 被 pushurl 阻断 |
| 前置检查：脏工作区 / 缺 `upstream/main` / 缺本地 `main` | ✓ 均给出明确提示并中止 |

**未在真实上游验证过**：上游做 history rewrite（force-push）时的 `--onto` 处理路径 —— 该场景
无法按需构造。逻辑见 3.4 节，届时请留意 `git range-diff upstream/main@{1}..upstream/main`。

### 排障：git 连不上 GitHub

**症状**：浏览器能打开 GitHub，但 git 报 `Failed to connect ... port 443` 或
`Recv failure: Connection was reset`。

**原因**：梯子通常只设置 Windows **系统代理**（浏览器读它），而 **git 不读系统代理** ——
git 只认环境变量或自己的 `http.proxy` 配置。于是浏览器通、git 不通。

**诊断**：

```bash
# 看系统代理是否开着（Windows）
reg query "HKCU\Software\Microsoft\Windows\CurrentVersion\Internet Settings" | grep -i proxyserver
# 看 git 自己的代理配置
git config --get-regexp 'http.*proxy' || echo "(未配置)"
```

**修复**（按域配置，只有 github.com 走代理，不影响内网 git 服务器）：

```bash
git config --global http.https://github.com/.proxy http://127.0.0.1:<你的代理端口>
# 常见端口：7890 / 7897 (Clash)、10809 (v2ray)、1080
```

验证（**不要**设环境变量，那样测不出配置是否生效）：

```bash
git ls-remote --heads https://github.com/Tencent/WeKnora.git main
```

其他备注：

- **临时用法**（不改配置）：`HTTPS_PROXY=http://127.0.0.1:7897 bash fork/sync-upstream.sh`
- **端口会变**：Clash 重启后端口可能变化，届时同步会突然失败，先查端口再改配置。
- **`gh` CLI 不读 git 的 `http.proxy`**，它只认 `HTTPS_PROXY`/`HTTP_PROXY` 环境变量。
  所以 `gh api ...`、`gh workflow disable ...` 这类命令需要先设环境变量。
- **不要改 hosts 文件来"配代理"**：hosts 只能做「域名 → IP」映射，**没有端口语法**，
  无法表达代理。把 GitHub 钉到某个直连 IP 反而会绕过你的梯子，且 GitHub 的 IP 会轮换，
  几周后失效且报错具有迷惑性。
