# 提示词机会地图输入

新批次从 `s11-cc-repo-create` 读取仓库外机会地图，默认位置：

```text
<source-parent>/prompt-opportunities/<project_id>.json
```

机会地图不得位于源仓库内，也不得进入 workspace。它必须绑定：

- 源仓库当前 HEAD。
- `repo-topic-map.json` 的源码基线。
- topic map 文件 SHA-256。
- 正式机会数量与 `repo-topic-map.json` 的 `task_plan` 完全一致。
- 正式机会在 12 个可用需求模式之间均匀铺开，每个模式最多 3 条；题位不足 12 条时使用
  相同数量的不同模式。
- 每个实际使用的模式提供 1 条备用机会。
- `mode_counts` 与 `reserve_mode_counts` 必须和逐卡实际统计一致。

使用前运行：

```bash
python3 <skill>/scripts/check_prompt_opportunities.py \
  --repo <源仓库> \
  --topic-map <批次目录>/repo-topic-map.json \
  --opportunity-map <外部机会地图> \
  --json
```

通过后，机会地图只作为起草辅助。每条题面仍需重新阅读源码、复核故事卡并从最终正文提取
领域链与 H1/H2/H3 证据。机会卡不是最终题面，不能直接复制进工作簿。

新机会地图使用 `selection_policy=capacity-balanced-up-to-three-per-mode`。旧的固定
36 条地图仍可读取，但只适用于刚好 36 个题位的批次。

写表时检查结果及地图 SHA-256 会写入 `prompt-generation-manifest.json`。运行侧不要求机会地图；
旧批次无论有没有地图都可继续运行。新批次交接时仍会再次核对
`tasks.xlsx` 摘要、机会地图 SHA-256、源提交和 topic map 摘要；任一文件在生成后被替换
都必须重新执行完整写入与交接检查。
