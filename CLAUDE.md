# 本仓库的工作约定（fork 定制分支）

本仓库是 [`Tencent/WeKnora`](https://github.com/Tencent/WeKnora) 的 **fork**。
完整工作流见 **[`fork/README.md`](fork/README.md)** —— 动手前先读它。

## 硬性规则（AI 提交代码时必须遵守）

1. **禁止在 `main` 上提交任何代码。** `main` 只是 `upstream/main` 的纯镜像，只允许 fast-forward。
2. **所有定制提交必须落在 `feature` 分支。** 不要在 `production` 上手工提交 —— 它由
   `bash fork/sync-upstream.sh --production` 从 `feature` 派生，基底是 `fork/production-tag`
   记录的上游 release tag（当前 `v0.8.2`）。生产构建只认 `fork/build/*` tag，不认分支名。
3. **推送定制分支统一用 `git push --force-with-lease --force-if-includes`**，禁止裸 `--force`。
4. **不得向 `upstream` 推送**（pushurl 已重定向到 `DISABLED-no-push-to-upstream`，物理上会失败）。
5. **fork 自建 tag 一律用 `fork/` 前缀**，避免与上游 `v*` tag 冲突、避免触发需 secrets 的镜像构建。
6. **推送前先 `git fetch origin`**，否则 `--force-with-lease` 的租赁基准是陈旧的，安全性退化。
7. 用户要求提交/推送代码时，**按 `fork/README.md` 第 5 节的流程执行**；不确定就先跑 `bash fork/sync-upstream.sh --dry-run` 看计划。

## 写定制代码前必做：上游撞车检查

上游是日更项目，**已多次出现「你打算做的功能上游已经做了」**。动手写之前先查：

```bash
git log --oneline --since='90 days ago' upstream/main -- <你要改的目录>
git log --oneline -i --grep='<关键词>' upstream/main | head
gh search prs --repo Tencent/WeKnora --state open <关键词>
```

已知实例：**语雀「保持目录结构」上游已完整实现**（PR #3476，含 `BuildTOCPaths` /
`BuildFolderFileName` / `ParseFolderSettings`），**不要重复实现**；飞书 wiki 尚无层级→文件夹
映射，那才是真正的增量。

## 写定制代码时的结构约束

目标是**最小化与上游的 diff**，因为 diff 就是未来的冲突面：

- **补丁写成「新文件 + 1~3 行接缝」**：新增自己的文件，只在 `internal/container/container.go`
  等注册中枢加一行。不要改上游大文件里的几十行。
- 部署类定制走 `.env`（已 gitignore）或 `docker-compose.override.yml`（上游未占用），不要改源码。
- 版本标识用 `EDITION` 环境变量（`scripts/get_version.sh` 支持），不要改 `VERSION` / `CHANGELOG.md`。
- **不要改 `.gitattributes`**，也不要改 `core.autocrlf`（本机 1646/4604 文件靠它维持 CRLF 一致）。

## 每次新增/删除定制提交后

更新 `fork/README.md` 第 10 节「定制清单」表格：改了哪些上游文件、为什么、上游是否已有替代实现。

## 文档位置

- 本 fork 的工作流文档放 **`fork/`**（上游不存在的路径，结构性零冲突），**不要放 `docs/`** ——
  该目录的 `README.md` 明文禁止再添加新文档（只保留编译发布所需资源）。
- 其余按全局 CLAUDE.md 约定。

## 不要做的事

- **不要再执行 `./scripts/install-git-hooks.sh`** —— 它会把 `core.hooksPath` 设成
  `scripts/git-hooks`，绕过 `.git/hooks/` 里的 fork 守卫（上游钩子已由守卫链式调用，功能不丢）。
  误执行后跑 `bash fork/install-hooks.sh` 修复。
- **改过 `fork/git-hooks/` 下的文件后必须重跑 `bash fork/install-hooks.sh`** ——
  `fork/git-hooks/` 只是真相源，实际执行的是 `.git/hooks/` 里的副本，不会自动同步。
- **不要把守卫挪回工作区内**（如把 `core.hooksPath` 指向 `fork/git-hooks`）。`fork/` 只在
  `feature` 上存在，切到 `main` 时会被 git 删除，守卫会**静默失效**且无任何报错。原因详见
  `fork/README.md` 第 4.1 节。
- **不要在 `feature` 上执行 `git pull`** —— 全局 `pull.rebase=false` 会造出 merge commit，
  破坏「线性定制序列」不变式。同步只用 `bash fork/sync-upstream.sh`。
- **不要改 `.github/workflows/` 里那 13 个文件**（上游跟踪，必冲突）。需要专属 CI 就新增
  一个 fork 命名前缀的 workflow 文件。
