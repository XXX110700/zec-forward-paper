# iPhone Bark 多空达标提醒

## 接入

1. iPhone 安装 Bark 并允许通知；在 App 内点击发送测试，确认能收到。
2. 在仓库 Settings → Secrets and variables → Actions → New repository secret 新建 `BARK_PUSH_URL`。
3. 值为 Bark 的服务器与设备 Key 地址，例如 `https://api.day.app/你的设备Key`。推荐只保留 `服务器/Key`。也兼容 Bark 复制出的完整示例 URL（包含标题、正文和查询参数）或 `curl -X GET ...` 示例；程序只解析地址，不执行命令。只填设备 Key 不够。不用把地址发给任何聊天，也不要提交到代码或截图中。支持 HTTPS 自建服务器（根路径部署）。
4. 合并本 PR 后，在 Actions → ZEC Bark notification test → Run workflow，选择 main，手动发送一条明确标注的测试通知。成功表示 Bark 服务接受请求，需在 iPhone 确认实际收到。此测试不修改模拟状态或提醒去重文件。
5. 正常的 ZEC V6.3 Forward Paper 任务会在成功保存并推送模拟状态后，准备提醒队列、先保存队列到 GitHub，再尝试发送和保存发送结果。只在默认分支发送；优化分支、PR 测试及独立历史恢复流程均不发送正式达标通知。

## 提醒规则

- 多头与空头分别判断最新一次后台成功处理的收盘分数；从 `<5/6` 到 `>=5/6` 时，各通知一次。5→6 不重复；低于5后再达到5，重新提醒。
- 首次启用或从未记录时，当前最新分数已经 >=5 也提醒一次，不需要等待再穿越。
- 有模拟持仓或待入场信号也提醒，但说明状态；分数达标不等于已经开仓，不触发真实下单。
- 每次任务只观察最终最新收盘快照；停机期间先达标又回落的历史瞬时信号不补发。行情收盘距当前超过30分钟、未来时间或后台检查时间无效时停止提醒。GitHub 调度与 Pages 部署可能延迟，不保证在收盘瞬间收到。
- 网络失败保留待发记录，下一次成功模拟任务重试；已回落或超过30分钟的旧提醒丢弃。持续达标不会为每根新K线创建新提醒。
- `zec_v63_bark_state.json` 保存观察游标、方向分数与待发事件，不包含设备 Key。请勿删除/重置，否则首次启用规则可能再次发送。损坏时备份并停止，待人工核对。
- POST 发送至服务器 `/push`，设备 Key 放在 JSON 中，不进入请求地址；不跟随重定向，不打印请求异常或服务返回内容。消息包含方向、分数、北京时间、模拟状态及事件 ID，点击消息可进入看板。

## 交付语义和故障处理

Bark 接受请求后、去重记录成功推送前如果任务中断，或网络超时但服务实际上已收到，下次重试可能重复。这是至少一次尝试，并非严格恰好一次送达；同一事件 ID 可帮助识别重复。HTTP 200 且 JSON code=200 只确认服务接受，不等于设备一定展示。

未配置 Secret 时跳过提醒，原有模拟继续运行。已配置但发送失败时，该提醒步骤报错，已保存的模拟结果不会回滚。失败诊断产物包含提醒去重文件供排查，不包含 Secret。日常任务仍每小时第3、18、33、48分钟调度。

如果不再接收，可在仓库 Secrets 删除 `BARK_PUSH_URL`。不需要改策略文件，也不需要删除提醒记录。

本 PR 与状态/日志自动对账 PR 独立，不修改生产 JSON/CSV、不修改策略参数。

协议依据：[Bark 官方项目](https://github.com/Finb/Bark)、[Bark Server API V2](https://github.com/Finb/bark-server/blob/master/docs/API_V2.md)。

## 发送失败诊断

错误日志只显示固定分类，不展示 Secret、原始响应内容或请求异常：

- BARK_CONFIG_MISSING / FORMAT：Secret 缺失或格式不正确。
- BARK_TLS_ERROR / CONNECTION_ERROR / TIMEOUT：GitHub 执行环境与推送服务的 TLS、连接或超时问题。
- BARK_HTTP_ERROR：仅展示 HTTP 数字状态码。
- BARK_RESPONSE_ERROR：响应无法解析。
- BARK_REJECTED：仅展示数字服务错误码；设备 Key、服务器或配置可能不正确。

手机 App 内测试成功只能确认手机端链路可用，不能替代 GitHub 端测试。
