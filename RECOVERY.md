# V6.3 对账与停机恢复

## 正常运行

每次模拟运行在请求行情前，将 CSV 与状态 JSON（包含中断待写队列）逐条核对；保存新状态前再次核对。事件身份为 `event + side + signal_ts`，交易身份为 `side + signal_ts`，兼容原始 CSV，无需迁移或修改历史文件。

检查 SIGNAL → ENTRY → EXIT 顺序、跨事件时间顺序、重复交易/事件、遗漏/多余记录、入场/退出字段和净 R、确认数、累计 R 与最大回撤。待入场和未平仓也必须分别对应 SIGNAL 或 SIGNAL+ENTRY。只有已在日志中完整出现的相同待写事件允许幂等重放；异常会立即报错，不自动重建/删除日志。普通主程序仍仅访问公开行情。

只读核对：

```sh
python recover_paper.py audit
```

审计入口直接读取并校验现有文件；缺失、损坏或不一致时失败，不创建备份、不修复、不请求行情。正常模拟入口的损坏备份行为保持不变。

## 停机后需要恢复时

不要删除状态/CSV，不要将游标设为最新来绕过数据缺失。正常任务在缺数据、断档或缺指标预热时停止，不会默认跳过丢失周期。

新增 `ZEC isolated historical recovery` 工作流（仅手动触发，contents: read）。在包含本改动的分支运行它，会下载公开历史 K 线、生成快照与校验清单、在 `candidate/` 副本内回放，最后上传恢复包。它不提交状态、不推送分支、不覆盖正式文件。Actions 在默认分支包含此 workflow 后才可从菜单手动触发。

本地等价命令：

```sh
python recover_paper.py prepare --directory recovery-artifacts
python recover_paper.py replay --directory recovery-artifacts
```

每次使用全新的目录。恢复覆盖最多 30 天停机；每个周期最多 60 页，每页 100 根，使用 OKX `history-candles` 的 `after` 向前分页，间隔 0.15 秒。三个周期分别补足首个待处理决策前至少 300 根预热数据；所有数据必须连续、有效、已收盘且最新。空页、无进展、冲突、断档、页数上限均失败。更长停机需人工调查，不放宽限制直接重置。

输入文件和行情保存于 `input/`，`manifest.json` 保存 SHA-256；回放前校验完整性和当前新鲜度。过期包需重新准备。`candidate/` 已存在时拒绝覆盖，失败副本保留用于排查。成功才写 `result.json`。SHA-256 用于检测意外改动，不是签名或可信身份认证。

## 审阅恢复包

1. `result.json` 必须为 complete；只有部分文件或失败任务不能用于恢复。
2. 核对正式分支当前 JSON/CSV 与输入快照 SHA-256；若正式任务在准备期间或之后继续更新，废弃候选包，从最新正式快照重新准备。
3. 比较新增 SIGNAL/ENTRY/EXIT、价格、成本、净 R 和持仓变化。历史补算使用补下载数据初始化指标，可能与当时连续运行的结果不同；不能把补算称为原始前向记录。候选状态保留 `recovery.mode=historical_recovery` 及来源游标。
4. 若决定采用，先暂停正式模拟 workflow 并等待已运行任务结束。备份 JSON/CSV，在独立 PR 中**成对**更新候选 JSON/CSV（没有 CSV 表示完整记录为空，不应据此删除现有日志）。重新对账与测试，再审阅合并，恢复正常任务。不要单独复制一个文件，也不要清除 recovery 来源字段。

工具不提供自动提升到正式状态的命令。正式 JSON/CSV 不属于本次代码改动，仍持续由既有模拟 workflow 管理。

API 依据：[OKX 官方 API 文档](https://www.okx.com/docs-v5/en/#order-book-trading-market-data-get-candlesticks-history)。
