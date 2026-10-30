# 进展（gex-dashboard）

仓库：https://github.com/liukunli001/gex-dashboard

## 已完成
- 第 1 步 wisdep 试用探索：88# SPXW Iron Condor 0DTE 的完整参数在试用区未显示，
  入场时间、短腿选择、翼宽、权利金目标、止盈、止损、离场时间、仓位、回测细节均无出处，
  不得用通用铁鹰表述冒充 88# 原参数。
- 回测 v1：BS 模型、距离固定短腿，权利金中位数仅 $0.086，缺陷已记录，不作结论。
- 回测 v2：delta 目标短腿、IV 校准（市场 IV 约为实现波动率 1.5 倍），
  结果随 IV 假设完全翻面：IV 1.0x 时 $1000 -> $362，IV 1.2x 时 $357，IV 1.5x 时 $14,294。
  结论：模型对假设极度敏感，不是策略验证。曲线见 backtest88_curve.html，
  数据见 backtest88_sensitivity.json，代码见 src/backtest88_v2.py。
- 回测 v3（真实数据用）：src/backtest_real.py，只用真实 bid/ask（卖 bid、买 ask），
  每分钟查止损，ATM 由链内 call mid 与 put mid 最接近的行权价推断，
  合成数据已跑通。真实数据未到，v3 尚未跑出真实曲线。
- Databento 账户：已创建并登录，$125.00 remaining credits 已确认，
  $125.00 monthly limit 已设（Usage-based access，90% 提醒），
  Subscriptions 为 0 plans、0 licenses，Current balance 为 $0.00。
  银行卡未能在门户删除：唯一默认卡 VISA ••••4348 只有 Update / Add new credit card，
  无 Delete/Remove 入口，卡仍在账上；月度上限是本月防超额手段，不是永久保证。
- Databento API：连接已建立（custom.databento），但首次免费调用
  metadata.list_datasets 返回 401 Authentication failed。
  原因待查：Databento 历史接口用 HTTP Basic（key 作用户名、密码空），
  当前 surrogate 以 Authorization 头原值方式送出，与 Basic 格式不一致的可能性最大，
  同一请求未重复发送。门户下载路线已停，避免两条路重复扣费。

## 进行中 / 待办
- 解决 Databento API 401 后，跑 metadata.get_cost：OPRA.PILLAR、SPXW、CBBO-1m、
  2026-09-29 至 2026-10-29、0DTE、±2% 行权价、美东 09:30–16:00，
  估算 <= $125 才下载；超了停在估算。
- 用真实数据跑 v3 试算 + 扫参 + 前后半段样本外验证，出优化后的曲线 UI，
  然后直接进入下一天，不等定时报告。
- 扩到 1 年的条件：试点精确价在免费额度内、试算逻辑跑通、用户再点头。

## 不做的事
- 不抄 GFM 曲线数字，不用通用铁鹰冒充 88#，不订 Standard/Plus/Unlimited，
  不买实时数据，不在免费额度外自动扣费。
