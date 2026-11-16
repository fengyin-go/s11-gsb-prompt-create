# 提示词机会地图输入

新批次从 `s11-cc-repo-create` 读取仓库外机会地图，默认位置：

```text
<source-parent>/prompt-opportunities/<project_id>.json
```

机会地图不得位于源仓库内，也不得进入 workspace。它必须绑定：

- 源仓库当前 HEAD。
- `repo-topic-map.json` 的源码基线。
- topic map 文件 SHA-256。
- 36 条正式机会和 12 条备用机会。
- 12 个可用需求模式各 3 条正式机会。
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

写表时检查结果及地图 SHA-256 会写入 `prompt-generation-manifest.json`。交接会再次核对
`tasks.xlsx` 摘要、机会地图 SHA-256、源提交和 topic map 摘要；任一文件在生成后被替换
都必须重新执行完整写入与交接检查。
