---
name: s11-gsb-prompt-create
description: "solo11 单轮 pointwise 批量出题：真实启动基准仓库并复用依赖，默认按仓库最高有效容量、类型比例仅作建议，按 semantic-v2 业务题位创建每题独立 GitHub 仓库，只使用 main、origin 和 workspace；生成提示词并通过难度及跨项目查重，再交接给 s11-feedback-run。用于新批次建仓、出题和运行交接。"
---

# S11 批量出题

数据仓库发布身份以当前电脑 `gh` 实际生效令牌为准，用 `gh api user` 读取活跃账号；不同电脑不要求固定账号，`_config/settings.json` 的 `github_owner` 不再决定建仓 owner。显式传入的 owner 只能与当前活跃账号一致。活跃账号为 `fengyin-solo` 时必须立即停止任务并提示切换账号，不能尝试其他账号回退。旧交付链接与历史证据保持原样，迁移既有仓库需另行明确处理。

从 `gsb-prompt-create` 复制的单次运行版本。先真实启动原始仓库、冻结依赖基线，再按业务 topic 排题位、建仓、生成首轮提示词，最后输出 `s11-feedback-run` 可直接读取的批次。不投递 Trae、不运行模型、不生成 Feedback。

本 skill 内的脚本和类型规则独立维护；不要调用原 GSB 的 A/B 建仓脚本，或用共享 solo-create 脚本写表。本文命令中的 `<skill>` 指本 `SKILL.md` 所在目录。

## 默认容量优先与任务类型

| 任务类型（写表和交接使用这个原名） | slug | 建议比例 | 题意 |
|---|---|---:|---|
| 功能开发 | development | 30% | 新增用户可感知的能力，可在证据支持下新增模块 |
| Bugfix | bugfix | 30% | 真实代码中可验证的错误行为与修复目标 |
| 功能优化 | optimization | 30% | 改善已有功能的性能、易用性、可靠性等 |
| 代码重构 | refactor | 5% | 有真实结构目标，保持约定行为 |
| 依赖升级与迁移 | migration | 5% | 明确依赖版本、兼容影响或平台迁移目标 |

用户说“批量生成提示词”时，默认按真实仓库的最高有效容量建仓并出题。`30/30/30/5/5` 仅作建议；真实 Bugfix、重构或迁移目标不足时，使用其他有证据支持的类型填满有效题位，不因类型比例减少题数、停止或要求确认。不得编造缺陷、放宽难度、去重、topic 家族或模式上限来凑容量。不出代码理解，不把普通工程化泛题标作依赖升级。

`scripts/s11_policy.py` 统一校验五类名称；topic map 的 `type_policy` 默认 `capacity-first`，只有用户明确要求固定比例时才设 `strict-ratio`。`allocate_counts` 保留最大余数法作为建议；计划、建仓和交接遵守同一策略。最高容量以真实源码、语义题位及所有质量闸门为依据；理论座位无法形成合格题时，记录具体原因并报告最终有效容量。

GitHub 明确返回主/次级限流、HTTP 429 或 `created too many repositories, too quickly` 创建频控时，记录原因并等待重试，优先遵守 Retry-After，否则从 60 秒指数退避到最多 900 秒，默认等待预算 2 小时，可用 `S11_GITHUB_RETRY_BUDGET_SECONDS` 调整。等待期间每次最多暂停 30 秒并向用户报告进展。权限、认证、仓库重名和非限流错误不能冒充限流重试。预算耗尽后保留清单并报告恢复位置；重试仍只能使用本批登记的仓库，不改名或认领陌生仓库。

## 单份目录和 main

```
<root>/_config/settings.json
<root>/batches/<batch>/
  repo-topic-map.json
  startup-preflight.json
  tasks.xlsx
  s11-task-repos.json
  handoff-metadata.json
  batch-manifest.json                 交接时生成
  cc10003-01-bugfix/
    origin/                           独立 Git 克隆，main 固定在初始提交，保留本地依赖
    workspace/                        建仓时空；交接时从任务仓库 main 克隆，不携带宿主依赖
    task-seed.json                    编号、类型、topic、仓库、初始提交
    task.json recording-plan.json prompt.txt   交接时生成
```

一个提示词对应一个任务目录、一个独立 GitHub 仓库、一份 origin、一份 workspace。远端只创建 `main`。初始时 main 指向基准提交；运行侧之后把产物提交推进到同一 main。初始快照使用 `/commit/<initial_commit>`，不要要求运行后的 main 仍停在基线，也不要另建基线、A、B 分支。

任务目录名、任务 ID、容器名和 GitHub 任务仓库名统一为 `<原仓库名>-<提示词编号两位数>-<任务类型英文名>`。提示词编号取 task_plan 的 sequence，例如原仓库 `cc10003` 的第一题为 Bugfix 时，目录和仓库都命名为 `cc10003-01-bugfix`。五类英文名固定为 `development`、`bugfix`、`optimization`、`refactor`、`migration`。`--start-index` 仅控制工作簿“编号”列的起始值，不参与目录或仓库命名。同一 root 的不同批次不得复用任务 ID（容器名就是任务 ID）。仓库名已被占用且未登记在本批清单时立即停止，不换名、不认领；只有清单明确登记的仓库可恢复复用。旧 `s11-六位数字` 批次保持原样并可继续读取，不自动重命名；已启动批次可按冻结的完整计划恢复未完成建仓，沿用原编号和 start-index。旧批次的额外扩容需单独处理，不与新命名混用。

旧 A/B 批次不自动删除、改名或覆盖；本入口会拒绝。需要迁移时先明确保留哪次产物并单独处理，不能把两侧结果合成一次运行。

## 1. 真实启动和依赖基线

先读 [references/startup-baseline.md](references/startup-baseline.md)。必须实际执行项目主入口，Web 项目优先内置浏览器验证页面，并检查前后端就绪；测试或编译成功不能替代启动。工作树必须属于最终基准提交。

依赖安装成功后保留依赖目录，只停本次进程。缺忽略规则时只修改 `.gitignore`，检查暂存列表，提交并推送原始仓库，再以推送后的提交重新启动验证。不擅自修源码。失败即停，保留命令、SHA、日志和原因，不建题、不写表。

在批次目录保存真实预检证据 `startup-preflight.json`，字段见 [references/handoff-schema.md](references/handoff-schema.md)。它不是让脚本代替启动的自我声明；所有成功值都必须有本轮实跑日志支持。脚本校验 SHA、必填证据、远端基线以及依赖存在和忽略状态。

## 2. 排题位、建仓

先读 [references/domain-fit.md](references/domain-fit.md)，按题目的主要工作及领域数据结果核验领域准入；再读 [references/semantic-v2-topic-map.md](references/semantic-v2-topic-map.md)，按真实源码和页面建立 `repo-topic-map.json`，`profile` 保留 `gsb-semantic-v2`。topic map 必须写明 `domain`、`subdirection`；每个 topic 写明 `domain_objects`、`domain_results`，每个 `task_plan` 题位写明 `domain_fit` 的主任务、领域结果、真实源码依据和 pass 结论。`task_plan` 使用上表五个 slug 与任务类型，逐项包含 sequence、topic、family、angle。类型比例不阻塞容量优先计划，同 topic 的多题跨家族并受 seat_capacity 限制。

每个题位先确认领域适配再计入容量、创建仓库。所属仓库是数据工程项目、topic 名叫血缘或圈选，都不能证明题目主体属于数据工程；通用前端升级、构建、路由或部署不得靠借用两个业务页面取得领域资格。迁移题缺少真实领域目标时换成有源码依据的合格题位，不为配比凑迁移题。

容量沿用语义题位、主体/模式护栏和单仓上限 50、每模式 3；源码派生有 warning 就先修扫描，不猜容量。新主体仅功能开发可用，并遵守原来的限流、跨域和台账规则。扩容只向最大 sequence 后追加；旧序号、类型、topic、family、angle 和仓库保持原样，追加默认保持容量优先；只有显式 strict-ratio 才核对整体比例。不要重排历史题。

```bash
python3 <skill>/scripts/validate_topic_map.py --map <batch目录>/repo-topic-map.json --repo <源仓库> --expected-base-commit <SHA> --require-domain-fit
python3 <skill>/scripts/create_batch_tasks.py --parent <root>/batches/b1111-01 --source <源仓库> --topic-map <batch目录>/repo-topic-map.json --preflight <batch目录>/startup-preflight.json --start-index 123 --dry-run
# 核对演练清单后，去掉 --dry-run 创建并发布初始仓库
python3 <skill>/scripts/create_batch_tasks.py --parent <root>/batches/b1111-01 --source <源仓库> --topic-map <batch目录>/repo-topic-map.json --preflight <batch目录>/startup-preflight.json --start-index 123
```

依赖只复制到 origin，保留链接、权限和时间戳，不用硬链接，不提交依赖。至少在代表性 origin 和实际任务环境分别做无安装启动 smoke，全栈覆盖前后端。宿主 native 依赖不能直接搬到 Linux 容器。交接时 workspace 仍为干净克隆；运行侧按冻结基线在实际 Linux 镜像中一次准备、验证依赖，再向每题对应目录复制，起跑只允许有运行侧有效缓存回执解释的忽略依赖；初始和产物 Git 快照均不得包含依赖。详见 [运行侧缓存规则](../s11-feedback-run/references/performance.md)。

`--verify` 只读复检原始计划对应的任务、origin/main 与远端仅有 main；运行后 main 可以推进，origin 和 task.json 的初始 SHA 保留。输出容量三块座位、实际题数、各类型数量、成功与失败清单。限流按上面的等待策略重试；其他失败不静默重试、不覆盖已有内容。

## 3. 生成提示词

先按 [references/domain-fit.md](references/domain-fit.md) 逐题复核最终题面的主任务、真实数据链路及可观察领域结果，再读 [references/generation-rules.md](references/generation-rules.md)，继承原 GSB 的真实代码准入、G16/G19 难度、负向案例、主题去重和跨项目防撞。类型已换为 S11 五类；功能优化按已有能力题审视，依赖升级与迁移必须有真实版本/兼容目标。五类默认困难；缺陷要求三项困难特征，其余至少两项，并至少一项硬证据。每题至少牵动两个真实功能模块；普通列表/概览一致不算第二模块。

题面只描述用户问题、目标行为、业务边界和可观察的验收结果。不得为方便验收或录屏，额外指定新建的验证脚本、文件路径、命令参数、测试框架或内部实现结构。真实已有的接口/字段、兼容契约及用户明确指定的交付形式可保留；验证方式与交接编排按引用规则单独维护。

使用本 skill 自带的辅助脚本；它们是原 helper 的隔离副本，避免共享脚本把功能开发和功能优化折叠成同一旧类型。G16 核心判据仍读取同级 `cc-gsb-create/scripts/gsb_difficulty.py`，不可自行抄判据。

1. 确认当前 SHA 的启动预检、依赖复制与无安装 smoke 已通过。
2. 同步平台历史语料，检查全局注册表，扫本批及历史近邻。默认共享原 GSB 全局注册表，保留跨批次防撞；允许用 `GSB_CROSS_PROJECT_REGISTRY` 显式指定。没有历史时只能按引用规则记录一次 bootstrap。
3. 先生成台账与题位表。逐题读取自己的 origin 源码，避开依赖目录。缺陷必须真实复现，不猜现象或结果。台账中记录领域准入结论、主要工作、受影响的数据对象/链路、源码依据和领域验收结果；领域不符或证据不足的候选先换切入点，不进入写表。
4. 写表只调用本地 `batch_prompt_workbook.py update`；它在写入前执行领域题位契约、G16/G19 和原三道闸、跨项目闸。领域闸要求最终题面命中计划 topic，并同时出现该题位的领域对象和可观察领域结果；只在标题或末尾补业务名词不放行。禁止 `--allow-gate-failure`。`tasks.xlsx` 固定原八列，题型严格使用上表名称。
5. 完成后全量复检领域准入与题面副本；交接前再次核对最终题面和 requirements 的主体一致。G16/G19、主题及查重通过不能替代领域准入。strict 命中逐条重写或按原规则有证据地复核；已确认负向硬闸不能备注放行。

```bash
python3 <skill>/scripts/sync_platform_prompt_corpus.py
python3 <skill>/scripts/check_repo_theme.py --parent <batch目录> --repo <任务目录>/origin --topic-map <batch目录>/repo-topic-map.json --require-topic-map --write-ledger --write-feature-points --max-per-mode 3
python3 <skill>/scripts/batch_prompt_workbook.py scan --parent <batch目录>
python3 <skill>/scripts/batch_prompt_workbook.py update --parent <batch目录> --repo <任务目录>/origin --topic-map <batch目录>/repo-topic-map.json --require-topic-map --folder cc10003-01-bugfix --prompt '<最终题面>' --max-per-mode 3
python3 <skill>/scripts/check_prompt_g16.py --parent <batch目录>
python3 <skill>/scripts/check_prompt_g16.py --parent <batch目录> --strict
python3 <skill>/scripts/check_prompt_difficulty.py --parent <batch目录>
python3 <skill>/scripts/check_prompt_dedup.py --parent <batch目录> --include-history
python3 <skill>/scripts/check_cross_project_prompts.py --batch-dir <batch目录> --registry <注册表> --require-registry --require-coverage --min-peer-batches 1 --require-platform-corpus
```

## 4. 交接给运行侧

复核每题的领域准入证据后，读 [references/handoff-schema.md](references/handoff-schema.md)，准备真实的领域/语言元数据、逐项 requirements 和可执行录屏方案。`_config/settings.json` 中镜像摘要必须来自本机真实预检，不猜另一架构的值。原 `cc-domain-task-create` 不是本批必经入口。同一基础仓库、基线和镜像的所属领域、子方向、语言/框架、Harness、Harness 版本、操作系统及环境可复现等级仅在 metadata.common 核实一次，交接生成公共档案供其余题和后续同身份批次复用，不逐题重新生成。`metadata.common` 的领域和子方向必须与 topic map 一致；交接会把该题位的 `domain_contract` 冻结进 `task.json`，运行侧按 hash 校验。公共领域字段不能免除逐题语义核验；不相符时更换题位，不靠改领域字段、挪账号或追加业务名词放行。

```bash
python3 <skill>/scripts/prepare_handoff.py --root <root> --batch b1111-01 --metadata <batch目录>/handoff-metadata.json
python3 <s11-feedback-run>/scripts/fbr_run.py --root <root> run --batch b1111-01
```

交接脚本按 type_policy 重新核对题型、难度、主题和跨项目查重，冻结 V2 基线与 hash，生成 task.json、录屏方案、原文 prompt.txt 和 batch-manifest.json，从远端 main 把唯一 workspace 铺为干净克隆，再实际调用运行侧 `load_task` 校验。已有运行状态的任务不覆盖。

运行侧只挂载 workspace，一题一新容器、一次首轮题面；原有获准“继续”、工程异常归档规则保持。产物只推 main。出题交接不代表用户已授权真实跑题、产物推送或平台提交，按 `s11-feedback-run` 原授权边界执行。

## 验证与反馈

```bash
PYTHONDONTWRITEBYTECODE=1 python3 -m pytest <skill>/tests -q
PYTHONDONTWRITEBYTECODE=1 python3 -m unittest discover -s <s11-feedback-run>/tests
```

自测用临时目录和本地 Git，替换 GitHub/Docker，不消耗模型额度。报告只写实际验证：新 skill 路径、仓库/Excel 位置、各类型数量、启动和依赖 smoke、容量依据、各闸结果、交接是否通过与未处理原因。只修改 skill 时报告实际改动与自测，不声称已经创建或运行真实题目。
