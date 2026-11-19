---
name: s11-gsb-prompt-create
description: "solo11 单轮 pointwise 批量出题：可从 GitHub/Git 仓库或普通本地目录快照生成，真实启动基准并复用依赖，默认按仓库最高有效容量、类型比例仅作建议，按 semantic-v2 业务题位创建每题独立 GitHub 仓库，只使用 main、origin 和 workspace；生成提示词并通过难度及跨项目查重，再交接给 s11-feedback-run。用于新批次建仓、出题和运行交接。"
---

# S11 批量出题

数据仓库发布身份以当前电脑 `gh` 实际生效令牌为准，用 `gh api user` 读取活跃账号；不同电脑不要求固定账号，`_config/settings.json` 的 `github_owner` 不再决定建仓 owner。显式传入的 owner 只能与当前活跃账号一致。活跃账号为 `fengyin-solo` 时必须立即停止任务并提示切换账号，不能尝试其他账号回退。旧交付链接与历史证据保持原样，迁移既有仓库需另行明确处理。

从 `gsb-prompt-create` 复制的单次运行版本。先真实启动 Git 源仓库或普通本地目录快照、冻结依赖基线，再按业务 topic 排题位、建仓、生成首轮提示词，最后输出 `s11-feedback-run` 可直接读取的批次。不投递 Trae、不运行模型、不生成 Feedback。

本 skill 内的脚本和类型规则独立维护；不要调用原 GSB 的 A/B 建仓脚本，或用共享 solo-create 脚本写表。本文命令中的 `<skill>` 指本 `SKILL.md` 所在目录。

默认出题流程不设置用户确认节点：生成侧完成内部设计、自动样稿检查和逐题语义复核后，
直接进入正式工作簿生成与后续闸门，不暂停等待用户认可。只有用户明确要求“只先看预览”
时，才按指定范围展示预览并暂时不写正式工作簿；不需要再次询问是否可以继续。

## 高速执行路径

提速只允许压缩等待和重复计算，不允许减少检查面或降低阈值。正式批次按以下顺序执行：

1. 完成容量排序、题位冻结、启动预检，并通过 topic map 的领域、类型、困难设计和
   语义身份检查。新批次的同一业务动作不能因为任务类型不同而重复占位。
2. 立即冻结 GitHub 建仓队列并启动后台 worker；它可以在前台继续做源码审计和题面设计时
   承担逐个建仓、频控等待、推送、克隆和最终远端复检。
3. 题面按题位继续逐条设计，不做“先写完全部再一次性发现问题”的流程。每条样稿仍先过
   表达、自然度、领域、类型、H1/H2/H3 和语义身份自动准入。
4. 全量候选先运行一次只读聚合预检，把同一候选在所有门禁上的问题一次列出并集中返修。
5. 聚合预检通过后才调用 `update-many` 正式写表。跨项目查重默认读取增量缓存；同项目和
   同 topic 的语义身份比较始终执行，不因任务类型不同而跳过。
6. 写表完成后仍执行全部正式闸、最终全量复检、工作簿回读、交接清单和 `fbr_run` dry-run。
   缓存命中、后台建仓完成或聚合计通过都不能替代这些最终检查。

后台建仓命令：

```bash
# 先冻结队列；该步骤只验证计划，不逐个查询或创建任务仓库
python3 <skill>/scripts/create_batch_tasks.py \
  --parent <root>/batches/b1111-01 \
  --source <源仓库或冻结快照> \
  --topic-map <batch目录>/repo-topic-map.json \
  --preflight <batch目录>/startup-preflight.json \
  --start-index 123 --queue
# 本地冻结快照模式再加 --snapshot-manifest <batch目录>/source-snapshot.json

# 启动独立后台 worker；日志写入 <batch目录>/creation-worker.log
python3 <skill>/scripts/create_batch_tasks.py \
  --parent <root>/batches/b1111-01 --resume --detach

# 随时只读查看逐个仓库的阶段、重试时间和错误
python3 <skill>/scripts/create_batch_tasks.py \
  --parent <root>/batches/b1111-01 --status

# 前台恢复；也可加 --detach 再次转入后台
python3 <skill>/scripts/create_batch_tasks.py \
  --parent <root>/batches/b1111-01 --resume
```

`creation-queue.json` 冻结 owner/repo、基准提交、阶段、attempts、重试时间和错误；
单 worker 文件锁阻止两个进程同时消费同一队列。worker 逐个恢复时仍只复用 journal
登记的本批仓库。所有逐仓任务完成后，它会再跑一次不加筛选的完整建仓复检，确认 origin、
workspace、远端仅有 main 和清单一致，然后才生成工作簿。非限流错误会保留为
`failed`，不会改名或认领陌生仓库；权限、认证和仓库冲突仍需按实际错误处理。

聚合预检命令：

```bash
python3 <skill>/scripts/preflight_batch.py \
  --parent <batch目录> \
  --repo <任务目录>/origin \
  --topic-map <batch目录>/repo-topic-map.json \
  --opportunity-map <parent>/prompt-opportunities/<project_id>.json \
  --require-opportunity-map \
  --candidates-file <完整候选JSON> \
  --output <batch目录>/preflight-report.json
```

预检是只读入口，按候选和规则同时汇总表达、自然度、领域、类型、困难契约、主题、
结构难度、批内 A-5/查重、G16/G19 strict、跨项目和平台语料问题。未提供完整题面时默认
失败；只有明确查看中途状态才可加 `--allow-pending`，正式写表前必须去掉。

跨项目缓存默认位于用户缓存目录，不在批次业务目录或 Git 仓库内。缓存键绑定题面、
任务类型、阈值、checker 版本、注册表、peer 工作簿和平台语料哈希；任一来源变化都会
自动失效。需要取证式全量重算时传 `--no-cache`，或在 `gsb-cross-project.json` 设置
`cache_enabled: false`；`cache_dir` 可指定独立机器本地目录。

## 默认容量优先与任务类型

| 任务类型（写表和交接使用这个原名） | slug | 建议比例 | 题意 |
|---|---|---:|---|
| 功能开发 | development | 30% | 新增用户可感知的能力，可在证据支持下新增模块 |
| Bugfix | bugfix | 30% | 真实代码中可验证的错误行为与修复目标 |
| 功能优化 | optimization | 30% | 改善已有功能的性能、易用性、可靠性等 |
| 代码重构 | refactor | 5% | 有真实结构目标，保持约定行为 |
| 依赖升级与迁移 | migration | 5% | 明确依赖版本、兼容影响或平台迁移目标 |

任务类型不是标签，也不是只改开头动词。`domain_fit.main_task` 与
`prompt_main_request` 必须写出该类任务的真实业务意图，最终题面还必须以
`prompt_main_request` 原样开头：

- 功能开发：说明当前能力尚未形成、缺少或没有，并新增可观察的数据能力、模块或产物。
  不能把读取现有数据、计算已有结果改写成“功能开发”。
- Bugfix：开头必须出现可复现的缺陷、错误现象或失败后果，修复目标是恢复既有行为；
  不能把正常的数据加工需求加一个“修复”前缀。
- 功能优化：必须针对现有、已有或当前链路，写清性能、稳定性、去重、失败恢复等改进目标；
  不能是首次新增能力。
- 代码重构：必须写明既有行为、输入输出或口径保持不变。
- 依赖升级与迁移：必须有真实版本、依赖、兼容、存量、回滚或迁移目标。

题位 `family` 也必须与类型一致。`check_prompt_type_intent.py` 是独立硬闸：
题位阶段查 `main_task`、`prompt_main_request`、`family`、slug 和类型；写表及交接阶段
再查工作簿最终题面。该闸与领域、难度、主题和查重闸并列，不能用备注放行。

正式批次数量取仓库有效容量，不再固定 36 条。有效容量小于 36 时按实际容量生成并交付；容量大于 36 时按 12 个可用需求模式、每模式最多 3 条封顶为 36 条。题位数量必须先由 `compute_capacity` 和 `seat_capacity` 决定，再生成同数量的机会卡与工作簿行；不能为凑固定数量编造缺陷、放宽难度、去重、topic 家族或模式上限。`30/30/30/5/5` 仅作任务类型建议，真实 Bugfix、重构或迁移目标不足时使用其他有证据支持的类型。不出代码理解，不把普通工程化泛题标作依赖升级。

2026-11-18 起，新批次必须填写 `dedup_contract_version: 1`。每个 `task_plan[]` 和正式
机会卡都要写同一份 `semantic_identity`：

- `object_key` 等于 topic id；
- `operation_key` 使用有限动作枚举：`create`、`update`、`query`、`export`、`validate`、
  `retry_recover`、`archive`、`rollback`、`publish`、`deduplicate`、`schedule`、
  `notify`、`migrate`、`delete`；
- 至少两个 `outcome_keys`、一个 `artifact_keys` 和至少一个
  `acceptance_entities`；
- 同 topic 的第二个题位必须为同主题其他题位写 `semantic_contrast`，至少两个不同维度，
  且包含 `operation` 或 `observable_result`。

同一 `object_key`、`operation_key` 且 outcome、artifact 或 acceptance 有交集时直接判为
同一语义任务。`Bugfix` 与 `功能优化` 不是语义差异，不能靠改任务类型、换 family、改开头、
增加并发/幂等/历史保留边界来绕过。无法写出两个用户可观察结果差异的主题只能保留一条；
需要更多容量时换业务主题或换仓库。

`scripts/s11_policy.py` 统一校验五类名称；topic map 的 `type_policy` 默认 `capacity-first`，只有用户明确要求固定比例时才设 `strict-ratio`。`allocate_counts` 保留最大余数法作为建议；计划、建仓和交接遵守同一策略。需求模式按实际题位数均匀铺开，每模式不超过 3；有足够题位时覆盖全部 12 个可用模式。

2026-11-16 起，新批次必须填写 `difficulty_contract_version: 3`，并为每个
`task_plan[]` 写结构化的 `difficulty_design`（H1 硬推理约束、H2 数据流模块整合、
H3 可验证设计裁决）。v3 新增的校准来自 `dfzr10012`：`14-bugfix` 在清理与恢复同时
更新同一检查点时，保留了一套真实的状态终态裁决，平台 `QC_PASSED`；
`10-development` 和 `11-bugfix` 虽然 H1/H2 成立、本地结构契约也通过，但题面已经
提前规定“保护引用快照”或“不能写回旧配置”，第二个方案因此违反 H1，平台按 P5 判缺 H3。
旧版只保留历史含义，不再用于新题位。平台判废的 13 条历史题和本批正反样本已固定在
[references/difficulty-hardening.md](references/difficulty-hardening.md)；新题位不得再落到
“单页/单台账状态一致、第二模块只展示、题面把实现步骤写死”的中等模板。

`dfzr10012 / b1115-01` 的最终回执又提供了 17 条失败校准：12 条是 H1/H2 成立但 H3 不成立，
2 条是 A-5 公共长片段，1 条是 P6 工具调用不足，2 条是运行期读取私有会话轨迹。逐题原因、
H3 三段测试和工作量要求固定在
[references/platform-rejection-calibration.md](references/platform-rejection-calibration.md)。

GitHub 明确返回主/次级限流、HTTP 429 或 `created too many repositories, too quickly` 创建频控时，记录原因并等待重试，优先遵守 Retry-After，否则从 60 秒指数退避到最多 900 秒，默认等待预算 2 小时，可用 `S11_GITHUB_RETRY_BUDGET_SECONDS` 调整。等待期间每次最多暂停 30 秒并向用户报告进展。权限、认证、仓库重名和非限流错误不能冒充限流重试。预算耗尽后保留清单并报告恢复位置；重试仍只能使用本批登记的仓库，不改名或认领陌生仓库。

## 单份目录和 main

```
<root>/_config/settings.json
<root>/batches/<batch>/
  repo-topic-map.json
  source-snapshot.json                本地目录模式：冻结来源、文件摘要与快照提交
  source-snapshot/                    本地目录模式：独立 Git 快照，作为所有任务仓库的共同 main 基线
  startup-preflight.json
  tasks.xlsx
  creation-queue.json                推荐建仓队列与逐仓状态
  creation-worker.log                后台 worker 日志
  preflight-report.json              只读聚合预检报告
  s11-task-repos.json
  handoff-metadata.json
  batch-manifest.json                 交接时生成
  cc10003-01-bugfix/
    origin/                           独立 Git 克隆，main 固定在初始提交，保留本地依赖
    workspace/                        建仓时空；交接时从任务仓库 main 克隆，作为宿主 Git 交付树
    task-seed.json                    编号、类型、topic、仓库、初始提交
    task.json recording-plan.json review-contract.json prompt.txt   交接时生成
```

一个提示词对应一个任务目录、一个独立 GitHub 仓库、一份 origin、一份 workspace。远端只创建 `main`。初始时 main 指向基准提交；运行侧之后把产物提交推进到同一 main。初始快照使用 `/commit/<initial_commit>`，不要要求运行后的 main 仍停在基线，也不要另建基线、A、B 分支。

来源支持两种模式：

- `git_remote`：现有 Git 模式。原仓库必须有干净提交和可访问的 `origin`，最终提交已在远端分支。
- `directory_snapshot`：普通本地目录模式。原目录不需要 `.git` 或远端；先用 `create_source_snapshot.py` 生成 `<batch>/source-snapshot/` 和 `source-snapshot.json`，后续启动、topic map 和建仓都使用该冻结快照。任务仓库仍然必须是独立 GitHub 仓库。

任务目录名、任务 ID、容器名和 GitHub 任务仓库名统一为 `<来源名>-<提示词编号两位数>-<任务类型英文名>`。Git 模式的来源名取原仓库远端名，本地目录模式取 `--name` 或目录名。提示词编号取 task_plan 的 sequence，例如来源名 `cc10003` 的第一题为 Bugfix 时，目录和仓库都命名为 `cc10003-01-bugfix`。五类英文名固定为 `development`、`bugfix`、`optimization`、`refactor`、`migration`。`--start-index` 仅控制工作簿“编号”列的起始值，不参与目录或仓库命名。同一 root 的不同批次不得复用任务 ID（容器名就是任务 ID）。仓库名已被占用且未登记在本批清单时立即停止，不换名、不认领；只有清单明确登记的仓库可恢复复用。旧 `s11-六位数字` 批次保持原样并可继续读取，不自动重命名；已启动批次可按冻结的完整计划恢复未完成建仓，沿用原编号和 start-index。旧批次的额外扩容需单独处理，不与新命名混用。

旧 A/B 批次不自动删除、改名或覆盖；本入口会拒绝。需要迁移时先明确保留哪次产物并单独处理，不能把两侧结果合成一次运行。

## 1. 真实启动和依赖基线

先读 [references/startup-baseline.md](references/startup-baseline.md)。必须实际执行项目主入口，Web 项目优先内置浏览器验证页面，并检查前后端就绪；测试或编译成功不能替代启动。Git 模式的工作树必须属于最终基准提交；本地目录模式必须在生成的 `source-snapshot/` 上预检并绑定快照提交。

普通本地目录先冻结来源，不直接拿会继续变化的目录充当基准：

```bash
python3 <skill>/scripts/create_source_snapshot.py \
  --source <本地源码目录> \
  --batch <root>/batches/b1111-01 \
  --name <任务仓库名前缀>
# 可选：--exclude path/to/runtime-data --exclude '*.sqlite'
```

脚本会生成稳定 `snapshot_commit`、`snapshot_id`、文件摘要和 `snapshot_tree`，排除 `.git`、常见依赖缓存、`.env` 等密钥文件，并在快照 `.gitignore` 写入受管排除块。原始目录内容变化不会影响已冻结快照；需要采用新源码时创建新批次或显式生成新快照，不覆盖旧快照。后续安装的依赖只保留在快照工作树中，不进入提交。

依赖安装成功后保留依赖目录，只停本次进程。Git 模式缺忽略规则时只修改 `.gitignore`，检查暂存列表，提交并推送原始仓库，再以推送后的提交重新启动验证。本地目录模式必须在生成快照前用 `--exclude` 声明项目特有依赖；若后续安装产生新的未忽略依赖目录，立即停止并更换新批次/新快照，不能手工改冻结快照后继续，也不能让依赖进入任务仓库。不擅自修业务源码。失败即停，保留命令、SHA、日志和原因，不建题、不写表。

在批次目录保存真实预检证据 `startup-preflight.json`，字段见 [references/handoff-schema.md](references/handoff-schema.md)。它不是让脚本代替启动的自我声明；所有成功值都必须有本轮实跑日志支持。脚本校验 SHA、必填证据、来源基线（Git 远端或快照身份）以及依赖存在和忽略状态。

## 2. 排题位、建仓

先读 [references/domain-fit.md](references/domain-fit.md)，按题目的主要工作及领域数据结果核验领域准入；再读 [references/semantic-v2-topic-map.md](references/semantic-v2-topic-map.md)，按真实源码和页面建立 `repo-topic-map.json`，`profile` 保留 `gsb-semantic-v2`。topic map 必须写明 `domain`、`subdirection`；每个 topic 写明 `domain_objects`、`domain_results`，每个 `task_plan` 题位写明 v2 `domain_fit`：主任务、领域结果、`domain_chain`、前置 `prompt_main_request`、反事实通用任务和至少两条带行号原文的 `domain_anchors`。`task_plan` 使用上表五个 slug 与任务类型，逐项包含 sequence、topic、family、angle。类型比例不阻塞容量优先计划，同 topic 的多题跨家族并受 seat_capacity 限制。

每个题位先确认领域适配和 H1/H2/H3 困难设计，再计入容量、创建仓库。所属仓库是数据工程项目、topic 名叫血缘或圈选，都不能证明题目主体属于数据工程；服务路由、负载均衡、限流降级、权限配额、网关预检、发布流水线和依赖锁默认不属于数据工程，除非它们只是真实数据采集/加工/存储链路的支撑且主工作句明确改变数据结果。通用前端升级、构建、路由或部署不得靠借用两个业务页面取得领域资格。迁移题缺少真实领域目标时换成有源码依据的合格题位，不为配比凑迁移题。

容量沿用语义题位、主体/模式护栏和单仓上限 50、每模式 3；源码派生有 warning 就先修扫描，不猜容量。新主体仅功能开发可用，并遵守原来的限流、跨域和台账规则。扩容只向最大 sequence 后追加；旧序号、类型、topic、family、angle 和仓库保持原样，追加默认保持容量优先；只有显式 strict-ratio 才核对整体比例。不要重排历史题。

```bash
python3 <skill>/scripts/validate_topic_map.py --map <batch目录>/repo-topic-map.json --repo <源仓库> --expected-base-commit <SHA> --require-domain-fit --require-difficulty-design --require-semantic-dedup
python3 <skill>/scripts/check_prompt_type_intent.py --parent <batch目录> --json
python3 <skill>/scripts/domain_fit.py --parent <batch目录> --repo <源仓库>
python3 <skill>/scripts/create_batch_tasks.py --parent <root>/batches/b1111-01 --source <源仓库> --topic-map <batch目录>/repo-topic-map.json --preflight <batch目录>/startup-preflight.json --start-index 123 --dry-run
# 推荐：冻结并后台建仓，前台同时继续源码审计和题面设计
python3 <skill>/scripts/create_batch_tasks.py --parent <root>/batches/b1111-01 --source <源仓库> --topic-map <batch目录>/repo-topic-map.json --preflight <batch目录>/startup-preflight.json --start-index 123 --queue
python3 <skill>/scripts/create_batch_tasks.py --parent <root>/batches/b1111-01 --resume --detach

# 兼容同步路径：核对演练清单后，去掉 --dry-run 创建并发布初始仓库
python3 <skill>/scripts/create_batch_tasks.py --parent <root>/batches/b1111-01 --source <源仓库> --topic-map <batch目录>/repo-topic-map.json --preflight <batch目录>/startup-preflight.json --start-index 123

# 本地目录快照模式：--source 指向冻结快照，并额外传清单
python3 <skill>/scripts/create_batch_tasks.py --parent <root>/batches/b1111-01 --source <batch目录>/source-snapshot --snapshot-manifest <batch目录>/source-snapshot.json --topic-map <batch目录>/repo-topic-map.json --preflight <batch目录>/startup-preflight.json --start-index 123 --dry-run
```

依赖只复制到 origin，保留链接、权限和时间戳，不用硬链接，不提交依赖。至少在代表性 origin 和实际任务环境分别做无安装启动 smoke，全栈覆盖前后端。宿主 native 依赖不能直接搬到 Linux 容器。交接时 workspace 仍为干净克隆；运行侧从冻结初始提交生成不含 `.git` 的临时运行树，按实际 Linux 镜像准备、验证依赖后只把依赖缓存复制到运行树。初始和产物 Git 快照均不得包含依赖。详见 [运行侧缓存规则](../s11-feedback-run/references/performance.md)。

`--verify` 只读复检原始计划对应的任务、origin/main 与远端仅有 main；运行后 main 可以推进，origin 和 task.json 的初始 SHA 保留。本地目录批次复检时也要传同一 `--snapshot-manifest`。输出容量三块座位、实际题数、各类型数量、成功与失败清单。限流按上面的等待策略重试；其他失败不静默重试、不覆盖已有内容。

## 3. 生成提示词

先按 [references/domain-fit.md](references/domain-fit.md) 逐题复核最终题面的主任务、真实数据链路及可观察领域结果，再读 [references/difficulty-hardening.md](references/difficulty-hardening.md)、[references/generation-rules.md](references/generation-rules.md) 和 [references/natural-prompt-style.md](references/natural-prompt-style.md)，继承原 GSB 的真实代码准入、G16/G19 难度、负向案例、主题去重和跨项目防撞。类型已换为 S11 五类；功能优化按已有能力题审视，依赖升级与迁移必须有真实版本/兼容目标。平台 G16 的基础项数仍是 `Bugfix` 三项、其余至少两项且至少一项硬证据；S11 额外要求每个题位都具备 H1/H2/H3，因为本批平台最终 P5 对全部通过样本都查实了这三项。每题至少牵动两个真实功能模块；普通列表/概览一致不算第二模块。

交接前还要按 [references/review-readiness.md](references/review-readiness.md) 填写 `review_contract`：需求到业务行为的证据映射、真实测试行为、关键约束句和环境噪声排除项。平台已实测的环境故障、电报式过程描述和泛化测试总数，不能留给运行侧事后猜。不生成上下文隔离声明，也不要求 `context_isolation` 元数据；题面不得限定上下文、会话、记忆或答案来源。

写题面前先填写 `difficulty_design`，不能用最终题面倒填。H1 至少写两条不同类别且相互作用的硬约束，每条使用 `C1`、`C2` 等稳定编号，并写清省略后的失败后果；H2 的第二模块必须消费、写入、持久化或编排真实数据产物，不能只是列表/详情/概览展示；H3 的两个可行方案、代价和选择影响只写在内部设计中，题面用具体冲突条件、处理边界和可观察结果自然承载取舍，不写“由你权衡优先项”“由设计者决定”“请自行选择”等委托句，也不列参考实现选项。仅写“兼容历史”“固定以某版本为准”“失败后重试”或只写一句结果不变量都不算 H3。同一冲突轴不能被题面预先固定为唯一实现。

每个题位还要填写 `workload_design`：至少 4 个工作切片、跨至少 2 个模块、每个切片有独立
数据结果和验证、至少 2 条真实验证路径。P6 依据工作切片、模块和验证路径评估。

三段 `prompt_excerpt` 必须逐字出现在最终题面，源码锚点必须跨两个模块并可回查。`check_prompt_g16.py` 是平台词面下限和已知正样本校准；`difficulty_contract.py` v3 是独立的 H3 结构审计，两者都必须通过。旧校准语料中的显式委托句仅保留历史识别用途，不是新题模板；新题由业务矛盾及结构化方案代价证明 H3，表达闸拒绝显式委托句。

### H3 实证裁决模型

H3 不是“题面出现冲突”，也不是“登记了两个选项”。每个 `design_tradeoff` 必须完成一张可审计的裁决卡：

1. `trigger` 写真实触发条件：哪份数据、哪两个事件或哪两个版本在同一对象上相遇。
2. `decision_axis` 写裁决轴，例如完成先后、当前版本与冻结版本、清理与恢复、迁移切换、权限来源或并发写入顺序。
3. 每个方案必须在 `option_analysis.preserves_constraint_ids` 中覆盖全部 H1 约束编号。任一方案违反 H1、H2 或共同不变量，就不是 H3，直接淘汰。
4. 每个方案必须分别写 `correctness_effect` 和 `observable_result`，且至少有两个互不相同的正确性后果和用户可观察结果。只改变类结构、锁类型、函数拆分、重试次数或页面提示，不算 H3。
5. `outcome_invariant` 是两种方案都必须守住的共同边界，例如“只能有一个确认版本”“下游不能读到半成品”；它不是某一方案的胜负。
6. 题面只能呈现冲突和共同不变量，不能提前写“必须采用新版本”“必须保护引用快照”“不能写回旧值”等分支胜负。`prompt_keeps_choice_open`、`both_options_valid`、`choice_changes_correctness` 必须为 `true`，`fixed_branch_excluded` 必须为 `true`，`false_h3_guards` 八项必须全为 `false`。
7. `context_anchors` 至少两条，一条 `supports=trigger`，另一条支撑 `option_a` 或 `option_b`；只指向 `STATUS_ORDER`、函数名或通用 CRUD 代码不算真实取舍依据。

新增的 `source_selection_only` 和 `equivalent_user_outcome` 必须为 `false`：两个方案不能只是
输入来源、锁、队列或写入机制不同，也不能在数据版本、下游文件、通知或用户页面上得到近似
相同结果。删掉题面的固定裁决句后，如果一条路径必须违反 H1，或两种路径最终数据结果相同，
该项不是 H3，必须换题。

正向校准是 `14-bugfix`：清理回执与恢复事件同时更新同一检查点时，既可以让清理终态获胜并延后恢复，也可以让恢复终态获胜并延后清理；两种都满足“只能有一个确认版本”，但路径释放、恢复入口和下游可见结果不同。
反向校准是 `10-development` 和 `11-bugfix`：前者已规定引用快照不可清理，后者已规定旧配置不能写回；第二个方案被 H1 排除，题面没有留下真实裁决。

### 生成前置，不靠检查补题

每条在起草前完成一张内部设计卡：源码已具备/缺少什么、真实题型、不带产品名的主工作摘要、领域输入到产物的链路、两项相互作用的失败约束、第二模块如何消费产物、冲突场景与两个可行方案的收益/代价。先用源码确认现有事实；新增能力所需输入可作为明确的新需求，但不能伪称已有迟到数据、规模、版本或失败现象。设计卡任一项站不住就换题，不能先写简单题再补刷新、并发或开放尾句。

起草时同时满足表达、题型、领域、H1/H2/H3：首段包含主工作与真实矛盾，中段写处理边界和跨模块后果，实例及验收只验证这些要求。内部方案不复制到题面，不向模型发出“做设计取舍”的指令。完成一条就做语义核对和自动样稿准入，通过后直接继续正式生成；全量检查是防回归，不是首次发现生成规则的环节。写作参考及有平台回执的自然 H3 例子见 [difficulty-hardening.md](references/difficulty-hardening.md)。

题面只描述用户问题、目标行为、业务边界和可观察的验收结果。不要求以“请”开头，也不统一开场模板；按题型自然起句，具体要求见 [generation-rules.md](references/generation-rules.md) 的“题面表达”和“任务类型语义”。题面不得出现任何模型上下文或信息来源限定，生成、写表和交接均不得追加此类文字。业务数据范围、权限及计算口径不受影响。

先写自然的业务主请求，再将其中的真实输入、处理、输出和观察结果提取到 `domain_chain`，不要反过来拼接字段制造题面。`prompt_main_request` 保留自然的开头段，可包含故障现象和修复目标两句，并作为题面的原样前缀；不能强迫 Bugfix 写成新增计算能力，也不能强迫所有题目生成一个新数据集。领域主任务仍须明确，权限、路由、灰度、缓存、发布和 UI 不得喧宾夺主。不得为方便验收或录屏，额外指定新建的验证脚本、文件路径、命令参数、测试框架或内部实现结构。真实已有的接口/字段、兼容契约及用户明确指定的交付形式可保留。

逐题核对每句话的业务对象和源码依据。跨模块要求必须写出具体模块、消费的数据和预期行为，设计取舍必须来自真实冲突；不得拼接“另一个入口”、不明状态或虚构的双版本来凑 H1/H2/H3。困难契约仍须先设计，不能事后倒填；题位无法自然满足时更换题位，不增补空泛尾句。默认直接进入正式工作簿；只有用户明确要求“只先看预览”时，才只产出指定数量的独立预览，此时不写正式工作簿、批量生成器或交接文件。

### 提示词机会地图与自然成稿

所有正式新批次必须提供仓库外机会地图，默认路径为
`<parent>/prompt-opportunities/<project_id>.json`。旧批次和既有项目可以没有地图，
`s11-feedback-run` 对两种输入都必须兼容。不得把机会地图提交进生成仓库。
格式与使用约束见 [references/prompt-opportunities.md](references/prompt-opportunities.md)。
使用前读取 [references/natural-prompt-style.md](references/natural-prompt-style.md) 并运行：

```bash
python3 <skill>/scripts/check_prompt_opportunities.py \
  --repo <源仓库> \
  --topic-map <批次目录>/repo-topic-map.json \
  --opportunity-map <parent>/prompt-opportunities/<project_id>.json \
  --json
```

机会地图只提供业务故事、数据链、冲突轴、自然切入角度和源码锚点，不是最终题面。
生成提示词时先写一篇连贯业务需求，再从正文提取 `domain_chain`、H1/H2/H3 和验收片段。
禁止先生成主体，再为命中脚本追加“另一个业务入口必须……”“同一份……必须……”等条件句。

每条先写不含产品名的故事摘要，再按自然因果展开：

1. 当前行为、用户遇到的问题及业务后果。
2. 需要新增、修复或优化的数据处理能力和跨模块产物。
3. 历史、并发、失败恢复、冲突裁决等边界如何影响主链路。
4. 用户可执行的操作和可观察结果。

默认写成一个连贯段落；确有多个业务阶段时才能自然分段，每段仍须推进同一件事。
禁止“第一段写业务主体，第二段集中补另一个模块、并发、恢复和回查”的验收拼装结构。
必须先形成单一连贯需求，再运行 `check_prompt_naturalness.py`。若契约或自然度不过，
重写对应段落或整篇，不得以文末追加补充句的方式修补。

大批量生成前先生成最多 3 条不同任务类型的自然样稿；有效容量不足 3 条时按实际题位生成。
样稿在内部自动完成表达、自然度、领域和 H1/H2/H3 准入，不等待用户确认；通过后直接生成
与有效容量一致的正式题位。技术闸全部通过不等于自然度
通过；故事卡、领域证据和最终题面必须逐题一致。

### 样稿自动准入

样稿不是降低领域或难度标准的通道，也不是默认流程中的用户确认点。2026-11-16 本项目的两条预览暴露了流程漏检：简单达成率除法配“全量或增量自行选择”没有充分 H3，筛选参数与导出路由修复则可能只是通用接口 Bugfix。此前两条不作为合格范例，不能照抄；表达/类型检查通过不等于领域或 H1/H2/H3 通过。

每条先写一句不含产品名的主工作摘要，审计实际改变的数据语义；如果主要只是按钮、参数转发、路由匹配、权限配额或业务状态控制，即使带“导出、指标、刷新”也不算领域准入。简单业务计算字段不能只靠附加刷新与并发控制升级成困难数据工程题。证据不足即换题，Bugfix 不能通过附加新计算能力挽救领域。

H3 必须有真实触发条件、两个成立方案、各自收益/代价/适用条件、对数据语义或链路的影响和共同不变量；“由你选择”“全量或增量均可”只是实现自由，不是充分证据。填写 `design_tradeoff.context_excerpt`、`context_anchors`、`option_analysis` 和 `decision_impact`，细则见 [difficulty-hardening.md](references/difficulty-hardening.md)。取舍条件及真实矛盾须自然体现在题面中，但不把内部审计字段或参考方案强行复制进题面。

对指定数量的样稿在独立 JSON 中保存 `records`（与工作簿行同名的字段）及 `topic_map`（只含这些题位及相关 topics，含完整领域和困难契约），运行：

```bash
python3 <skill>/scripts/check_prompt_preview.py --candidate <独立样稿.json> --repo <基准源码目录>
```

表达、题型、领域、H1/H2/H3 四项均通过，且生成侧逐题复核没有上述借壳/假取舍后，
直接把该候选纳入正式生成，不暂停等待用户确认。未通过只记录淘汰原因并重写或换题，不能用
不合格样稿推进。该命令本身不写正式表；自动准入后仍须执行全部正式写表闸。用户看过预览
也不能豁免领域或难度要求。

使用本 skill 自带的辅助脚本；它们是原 helper 的隔离副本，避免共享脚本把功能开发和功能优化折叠成同一旧类型。G16 核心判据仍读取同级 `cc-gsb-create/scripts/gsb_difficulty.py`，不可自行抄判据。

1. 确认当前 SHA 的启动预检、依赖复制与无安装 smoke 已通过。
2. 同步平台历史语料，检查全局注册表，扫本批及历史近邻。默认共享原 GSB 全局注册表，保留跨批次防撞；允许用 `GSB_CROSS_PROJECT_REGISTRY` 显式指定。没有历史时只能按引用规则记录一次 bootstrap。
3. 先生成台账与题位表。逐题读取自己的 origin 源码，避开依赖目录。缺陷必须真实复现，不猜现象或结果。台账中记录领域准入结论、主要工作、`domain_chain`、`prompt_main_request`、反事实通用任务、受影响的数据对象/链路、带行号源码锚点和领域验收结果；领域不符或证据不足的候选先换切入点，不进入写表。
4. 写表只调用本地 `batch_prompt_workbook.py update` 或 `update-many`；新批次还必须传外部 `--opportunity-map`。写入前执行题面表达、自然度、任务类型、领域 v2、困难 H1/H2/H3 v3、G16/G19、主题、批内和跨项目闸。题面出现需求/任务自指、第一人称请求、“请你帮我”类外壳或上下文来源限制时直接拒写；不以“请”开头不是拒写理由。
5. 完成后全量复检领域准入、困难契约与题面副本；交接前再次核对最终题面和 requirements 的主体一致。写入后重新读取正式工作簿的提示词单元格，并检查候选题面、`prompt.txt`、`task.json` 中的题面文本一致且都通过 `prompt_style`，不能只验生成器内存中的字符串。中间版工作簿和历史日志不得当作当前交付物。G16/G19、主题及查重通过不能替代领域准入或 difficulty_design。strict 或困难契约命中时逐项重写；`难度复核` 备注不能放行，已确认负向硬闸不能备注放行。若词面 G16 已通过但没有真实 H3 选择权，仍以 `difficulty_contract` v3 的结果为准。

```bash
python3 <skill>/scripts/sync_platform_prompt_corpus.py
python3 <skill>/scripts/check_repo_theme.py --parent <batch目录> --repo <任务目录>/origin --topic-map <batch目录>/repo-topic-map.json --require-topic-map --write-ledger --write-feature-points --max-per-mode 3
python3 <skill>/scripts/batch_prompt_workbook.py scan --parent <batch目录>
python3 <skill>/scripts/preflight_batch.py --parent <batch目录> --repo <任务目录>/origin --topic-map <batch目录>/repo-topic-map.json --opportunity-map <外部机会地图> --require-opportunity-map --candidates-file <候选JSON> --output <batch目录>/preflight-report.json
python3 <skill>/scripts/batch_prompt_workbook.py update-many --parent <batch目录> --repo <任务目录>/origin --topic-map <batch目录>/repo-topic-map.json --opportunity-map <外部机会地图> --require-topic-map --candidates-file <候选JSON> --max-per-mode 3

# 以下单项检查器用于排错；正常流程由 preflight_batch、update-many 和 prepare_handoff
# 内部统一调用，不要为同一份未变化的工作簿反复手工全量执行。
python3 <skill>/scripts/check_prompt_type_intent.py --parent <batch目录> --json
python3 <skill>/scripts/check_prompt_g16.py --parent <batch目录>
python3 <skill>/scripts/check_prompt_g16.py --parent <batch目录> --strict
python3 <skill>/scripts/check_prompt_naturalness.py --parent <batch目录> --json
python3 <skill>/scripts/check_difficulty_contract.py --parent <batch目录> --json
python3 <skill>/scripts/domain_fit.py --parent <batch目录> --repo <任务目录>/origin
python3 <skill>/scripts/check_prompt_difficulty.py --parent <batch目录>
python3 <skill>/scripts/check_prompt_dedup.py --parent <batch目录> --include-history
python3 <skill>/scripts/check_cross_project_prompts.py --batch-dir <batch目录> --registry <注册表> --require-registry --require-coverage --min-peer-batches 1 --require-platform-corpus
```

批次已经产生平台回执后，新增失败先做责任归因，不能把所有作废都改写成 Feedback：

```bash
python3 <skill>/scripts/batch_platform_audit.py \
  --batch-dir <batch目录> \
  --output <批次目录>/platform-rejection-analysis.json
```

P5、P6 和 A-5 属于出题侧；私有轨迹读取属于运行侧。只有失败检查明确指向 Feedback 字段时，
才进入运行侧的 Feedback 返修。

公开建仓前检查即将推送的基线提交及其可达历史。发现疑似凭据时，错误指向 Git 对象，便于定位。写表时，工作簿、生成清单和跨项目注册表在锁内更新。上次写入中断时，下次写入先按注册表回执决定保留或恢复该批文件。

## 4. 交接给运行侧

交接清单绑定题位序号、题位图、生成清单和逐题验收契约。新批次交接前必须具备已通过的批内查重、跨项目查重及平台语料覆盖回执。

复核每题的领域准入证据后，读 [references/handoff-schema.md](references/handoff-schema.md)，准备真实的领域/语言元数据、逐项 requirements、`review_contract` 和可执行录屏方案。`review_contract` 必须把每个需求映射到具体业务行为、证据类型和成功信号；测试只写真实行为映射，不写泛化总数；环境、网关、端口、进程清理和工具缺失统一列为不扣分噪声。交接不得向最终题面追加上下文隔离声明或其他信息来源限制。`_config/settings.json` 中镜像摘要必须来自本机真实预检，不猜另一架构的值。原 `cc-domain-task-create` 不是本批必经入口。同一基础仓库、基线和镜像的所属领域、子方向、语言/框架、Harness、Harness 版本、操作系统及环境可复现等级仅在 metadata.common 核实一次，交接生成公共档案供其余题和后续同身份批次复用，不逐题重新生成。`metadata.common` 的领域和子方向必须与 topic map 一致；交接会把该题位的 `domain_contract` 冻结进 `task.json`，运行侧按 hash 校验；`review_contract` 原文写入 `review-contract.json`，摘要写入 `task.json`。公共领域字段不能免除逐题语义核验；不相符时更换题位，不靠改领域字段、挪账号或追加业务名词放行。

`prepare_handoff.py` 在写任何交接文件前调用 `platform_contract.py`：领域、子方向、
任务类型、难度、操作系统和 `repro_level` 必须是平台枚举；安装和镜像经过写入
`repro_evidence`。新 Web 录屏方案同时校验 `pages`、页面动作归属和 12 秒页面预算。

```bash
python3 <skill>/scripts/check_review_contract.py --parent <batch目录> --metadata <batch目录>/handoff-metadata.json --json
python3 <skill>/scripts/prepare_handoff.py --root <root> --batch b1111-01 --metadata <batch目录>/handoff-metadata.json
python3 <s11-feedback-run>/scripts/fbr_run.py --root <root> run --batch b1111-01
```

交接脚本按 type_policy 重新核对题型、难度、主题和跨项目查重，并重新读取
`prompt-generation-manifest.json`：校验 `tasks.xlsx` 摘要与外部机会地图路径、SHA-256、
源提交及 topic map 摘要。随后冻结 H3 v3 契约与 hash，把生成清单和机会地图绑定写入
`batch-manifest.json`，生成 task.json、录屏方案、原文 prompt.txt，从远端 main 把唯一
workspace 铺为干净克隆，再实际调用运行侧 `load_task` 校验。已有运行状态的任务不覆盖。

运行侧不把 Git workspace 挂给模型，而是从冻结初始提交生成无 `.git` 的运行树并只挂载该目录；一题一新容器、一次首轮题面，原有获准“继续”、工程异常归档规则保持。运行结束后由宿主侧把运行树同步回 workspace，再生成快照并只推 main。出题交接不代表用户已授权真实跑题、产物推送或平台提交，按 `s11-feedback-run` 原授权边界执行。

## 验证与反馈

```bash
PYTHONDONTWRITEBYTECODE=1 python3 -m pytest <skill>/tests -q
PYTHONDONTWRITEBYTECODE=1 python3 -m unittest discover -s <s11-feedback-run>/tests
```

自测用临时目录和本地 Git，替换 GitHub/Docker，不消耗模型额度。报告只写实际验证：新 skill 路径、仓库/Excel 位置、各类型数量、启动和依赖 smoke、容量依据、各闸结果、交接是否通过与未处理原因。只修改 skill 时报告实际改动与自测，不声称已经创建或运行真实题目。

新题按[独立难度复核与同项目查重](references/difficulty-hardening.md)生成并验证最终题面。样稿、写表和交接使用同一最终正文。
