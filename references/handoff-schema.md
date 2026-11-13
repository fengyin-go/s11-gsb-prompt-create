# 交接数据

出题与运行使用同一个 `<root>/batches/<batch>/`。`<root>/_config/settings.json` 由运行侧定位；镜像、Harness、key 文件路径遵守 `s11-feedback-run`，本 skill 不读取或复制 key 原文。

## 启动证据 startup-preflight.json

每个成功值都必须来自本轮实际执行；记录实际命令、就绪入口/日志、提交和清理结果，不能复制示例里的占位内容宣称启动成功。

```json
{
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
    "repro_level": "已容器化，可一键起环境"
  },
  "tasks": {
    "cc10003-01-bugfix": {
      "difficulty": "困难",
      "requirements": [
        {"id": "R1", "text": "与该题题面一致的需求点", "check": "操作场景、输入与预期结果；已有真实命令也可使用"}
      ],
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

同一批的代表性 smoke 可引用同份证据，但该证据须涵盖本批技术栈、依赖来源和运行环境，不能只写 true。容器架构与宿主不同或依赖含 native 组件时，实际运行环境必须另验证，不能从宿主启动成功推断容器成功。

`prepare_handoff.py` 不执行参考解、不调用模型。它从 tasks.xlsx 原样取题面，从仓库清单取 GitHub 地址和初始提交，并再次运行领域题位闸。输出使用 `producer=s11-gsb-prompt-create`、`execution_mode=single`、`branch=main`，把每个题位的 `domain_contract`（domain、subdirection、topic、主任务、领域结果、源码依据、pass 结论）冻结进 `task.json`，同时把 producer/execution_mode/branch、origin/workspace 路径和领域契约纳入 hash 与批次清单。运行侧会拒绝 A/B 目录、错误分支、错误路径、领域契约不一致和输入漂移。

origin 始终保留初始提交与依赖，workspace 只克隆已提交的源码，起跑时没有缓存或被忽略文件。初始地址固定 `/commit/<SHA>`；产物后续推到 main，不增设其他分支。

requirements.check 是供验收者阅读的核对方法，可为自然语言，不要求命令。不得倒推题面，强迫实现方创建指定测试文件来填此字段。recording_plan.demo.cmd 会被运行侧实际执行，只能使用真实入口；若功能尚未实现，先记录已有构建/启动演示并明确其仅覆盖基础检查，将业务场景留在 requirements 中，notes 标明业务演示待按实际产物补齐。不得把基础检查视频当成功能验收证据，也不得写入不存在的未来脚本。正式业务录制前，运行侧应根据实际产物更新计划并同步交接哈希。
