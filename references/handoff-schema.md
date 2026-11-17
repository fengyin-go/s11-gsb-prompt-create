# 交接数据

出题与运行使用同一个 `<root>/batches/<batch>/`。`<root>/_config/settings.json` 由运行侧定位；镜像、Harness、key 文件路径遵守 `s11-feedback-run`，本 skill 不读取或复制 key 原文。

## 启动证据 startup-preflight.json

每个成功值都必须来自本轮实际执行；记录实际命令、就绪入口/日志、提交和清理结果，不能复制示例里的占位内容宣称启动成功。

```json
{
  "source_mode": "git_remote",
  "startup_ok": true,
  "startup_commit": "40位最终基准SHA",
  "startup_command": "实际启动命令",
  "startup_result": "实际结果摘要",
  "startup_evidence": {"entry": "实际端口或CLI输入输出", "log": "真实成功日志或日志文件位置"},
  "cleaned_up": true,
  "baseline_commit": "同一个SHA",
  "baseline_push_ok": true,
  "dependency_paths": ["node_modules"],
  "dependency_ignored": true
}
```

没有本地依赖目录时 `dependency_paths` 可为 `[]`，但仍需真实运行入口。相对依赖路径必须在源仓库内，被 .gitignore 覆盖，且未被 Git 跟踪。已跟踪的 vendor 属于源码快照，不当成需复制的忽略目录。

普通本地目录使用 `source-snapshot` 模式。原目录不需要 Git 或远端，但启动必须在冻结快照中
执行，并绑定同一份快照清单：

```json
{
  "source_mode": "directory_snapshot",
  "snapshot_commit": "40位快照SHA",
  "snapshot_id": "sha256:...",
  "startup_ok": true,
  "startup_commit": "同一个快照SHA",
  "baseline_commit": "同一个快照SHA",
  "startup_command": "在 source-snapshot 中实际执行的启动命令",
  "startup_result": "实际结果摘要",
  "startup_evidence": {"entry": "实际端口或CLI输入输出", "log": "真实成功日志"},
  "cleaned_up": true,
  "dependency_paths": ["node_modules"],
  "dependency_ignored": true
}
```

快照模式不要求 `baseline_push_ok`。`source-snapshot.json` 记录原目录、任务仓库名前缀、
`snapshot_commit`、`snapshot_tree`、`snapshot_id`、文件摘要和实际排除模式；建仓清单同时冻结
`source_mode=directory_snapshot` 与这些字段。

## handoff-metadata.json

`common` 提供同一基线的领域与语言等信息，`tasks` 按任务号覆盖，并提供每题自己的需求点、录屏方案和 smoke 证据。所有任务使用同一基准时基础字段必须一致。

```json
{
  "common": {
    "repo_id": "本批源仓库的稳定标识",
    "domain": "必须与 repo-topic-map.json 的 domain 一致",
    "subdirection": "必须与 repo-topic-map.json 的 subdirection 一致",
    "language": "Python",
    "frameworks": "实际语言与框架版本",
    "repro_level": "已容器化，可一键起环境",
    "repro_evidence": "真实镜像首次安装和无安装重启均通过；说明留在这里，不写进枚举"
  },
  "tasks": {
    "cc10003-01-bugfix": {
      "difficulty": "困难",
      "requirements": [
        {"id": "R1", "text": "与该题题面一致的需求点", "check": "操作场景、输入与预期结果；已有真实命令也可使用"}
      ],
      "review_contract": {
        "version": 1,
        "business_checks": [
          {
            "requirement_id": "R1",
            "behavior": "用户执行什么动作后看到哪份数据结果",
            "evidence_kind": "http",
            "success_signal": "可核对的状态码、数据字段或文件内容"
          }
        ],
        "test_behaviors": [
          {
            "requirement_ids": ["R1"],
            "selector": "测试名或稳定选择器",
            "behavior": "该测试具体检查的业务行为"
          }
        ],
        "key_business_constraint": "必须逐字出现在最终题面中的关键约束句",
        "environment_exclusions": [
          "dependency_install",
          "gateway_network",
          "port_conflict",
          "process_cleanup",
          "tool_availability"
        ],
        "model_process_focus": [
          "只允许写入模型自身代码、验证或判断造成的失败与修正"
        ],
        "style_focus": "Feedback 用完整主谓句写业务行为、实际落差和用户可观察结果",
        "language_terms": [
          {"source": "内部缩写", "public": "用户能理解的动作或字段"}
        ]
      },
      "recording_plan": {
        "setup_cmd": "make setup",
        "demo": [{"cmd": "真实演示命令", "show": "该需求的可观察结果"}],
        "notes": "",
        "max_seconds": 90,
        "resolution": "1280x720",
        "format": "mp4"
      },
      "dependency_smoke": {
        "ok": true,
        "commit": "同一个40位基准SHA",
        "command": "真实无安装启动命令",
        "evidence": "代表性克隆及实际运行环境的就绪日志/入口证据",
        "cleaned_up": true
      }
    }
  }
}
```

`repro_level` 只能使用平台枚举：

- `无外部依赖`
- `有外部依赖，未容器化`
- `已容器化，可一键起环境`

安装经过、镜像架构、首次安装和无安装重启说明写入 `repro_evidence`。`prepare_handoff.py`
会在写 `task.json` 前调用 `platform_contract.py` 校验领域、子方向、任务类型、难度、
操作系统、`repro_level`、Harness 版本和 Web 页面方案；非法值指出原值、允许值和应迁移到
证据字段的内容。

Web 录屏方案必须声明 `pages`，每个 `steps[]` 用 `page` 指向真实页面：

```json
{
  "mode": "web",
  "serve_cmd": "npm run dev -- --host {host} --port {port}",
  "pages": [
    {"id": "home", "path": "/", "ready_selector": "#app", "min_visible_seconds": 2},
    {"id": "detail", "path": "/detail", "ready_selector": "#detail", "min_visible_seconds": 2}
  ],
  "steps": [
    {"action": "click", "selector": "#open-detail", "page": "home"},
    {"action": "expect_text", "selector": "#detail", "text": "已保存", "page": "detail"}
  ]
}
```

同一批的代表性 smoke 可引用同份证据，但该证据须涵盖本批技术栈、依赖来源和运行环境，不能只写 true。容器架构与宿主不同或依赖含 native 组件时，实际运行环境必须另验证，不能从宿主启动成功推断容器成功。

`prepare_handoff.py` 不执行参考解、不调用模型。它从 tasks.xlsx 原样取题面，从仓库清单取 GitHub 地址和初始提交，并再次运行领域题位闸。输出使用 `producer=s11-gsb-prompt-create`、`execution_mode=single`、`branch=main`，把每个题位的 v2 `domain_contract`（domain、subdirection、topic、主任务、领域结果、`domain_chain`、前置 `prompt_main_request`、带行号源码锚点、反事实通用任务、pass 结论）冻结进 `task.json`，同时把 producer/execution_mode/branch、origin/workspace 路径和领域契约纳入 hash 与批次清单。运行侧会拒绝 A/B 目录、错误分支、错误路径、旧版或字段不全的 v2 领域契约以及输入漂移。

新批次 `batch-manifest.json` 必须带 `review_contract_required: true`。运行侧加载任务时，
若清单要求契约但 `task.json.review_contract` 缺失、`review-contract.json` 内容不一致或摘要不符，
直接拒绝交接。

`batch-manifest.json.prompt_opportunities` 是可选生成溯源。新批次在出题交接时会写入并校验，
但运行侧接收旧批次时不要求该字段或地图文件存在；缺失、路径不可访问或只保留历史绑定时都不能
阻断运行、评审和平台提交。

origin 始终保留初始提交与依赖，workspace 只克隆已提交的源码，起跑时没有缓存或被忽略文件。初始地址固定 `/commit/<SHA>`；产物后续推到 main，不增设其他分支。

requirements.check 是供验收者阅读的核对方法，可为自然语言，不要求命令。不得倒推题面，强迫实现方创建指定测试文件来填此字段。recording_plan.demo.cmd 会被运行侧实际执行，只能使用真实入口；若功能尚未实现，先记录已有构建/启动演示并明确其仅覆盖基础检查，将业务场景留在 requirements 中，notes 标明业务演示待按实际产物补齐。不得把基础检查视频当成功能验收证据，也不得写入不存在的未来脚本。正式业务录制前，运行侧应根据实际产物更新计划并同步交接哈希。

`review_contract` 是必填的反馈生产契约。`business_checks` 必须覆盖全部 requirements；每个 check
要写清业务行为、证据类型和成功信号。`test_behaviors` 只登记真实测试与需求的映射，不能只写
“15 个测试通过”。环境依赖安装、网关、端口、进程清理和工具缺失统一列入排除项，不能作为模型
扣分依据。不生成或要求 `context_isolation`，最终题面不得包含上下文、会话、记忆或答案来源限制。
`prepare_handoff.py` 会运行该契约，并把契约原文保存到每个任务的
`review-contract.json`，同时在 `task.json` 写入内容摘要。
