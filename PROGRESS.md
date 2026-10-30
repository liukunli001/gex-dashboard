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

## 2026-10-29 20:25 新令（覆盖此前 ≤$125 下载授权）
- Databento 绝不产生费用：门户下载任务已关，不再提交任何会扣额度或扣卡的下载。
- 此前“估算 ≤$125 才下载”的授权作废，Databento 路线冻结，改找其他数据源。

## 2026-10-30 真实 Databento 首日验证（2026-10-28，格式不一致，未出交易结果）

来源：浏览器已下载的 2026-10-29 20:23 那批（OPRA.PILLAR、SPXW、CBBO-1m），
zip 原件未改动，仅从中取出 2026-10-28 一天解压到本地 data/databento/
（data/ 为本地数据，不提交进仓库；本段只提交 PROGRESS.md）。
文件：opra-pillar-20261028.cbbo-1m.csv.zst（166 MB）→ .csv（986 MB），
另有 condition.json / manifest.json / metadata.json（dataset OPRA.PILLAR，
schema cbbo-1m，symbols SPXW.OPT，pretty_px / pretty_ts，split by day）。

实测（全文件流式统计，2026-10-28 文件）：
- 总行数 7,850,338，全部 root 为 SPXW。
- 列名与代码假设部分一致：ts_event、symbol、bid_px_00、ask_px_00 均存在；
  完整列：ts_recv, ts_event, rtype, publisher_id, instrument_id, side, price,
  size, flags, bid_px_00, ask_px_00, bid_sz_00, ask_sz_00, bid_pb_00, ask_pb_00, symbol。
- OCC 解析一致：symbol 如 "SPXW  261028C07700000"（root 后两个空格），
  src/backtest_real.py 的正则 \s+ 能解析，到期日 2026-10-28、行权价 7700.0 正确。
- 按 ts_recv（分钟桶）计的真实 0DTE：到期日 = 2026-10-28 的行数 168,203，
  分钟桶 391 个（UTC 13:31–20:01，即 ET 09:31–16:01），不同行权价 252 个，
  其中 bid <= 0 共 54,736 行（32.54%），ask <= 0 共 504 行（0.30%）。
  全文件分钟桶（所有到期日）452 个（UTC 13:31–21:16）。

与 src/backtest_real.py 输入假设不一致（共 3 点，代码未改，不硬跑）：
1. 时间字段错：代码用 ts_event 作分钟键并注释“ET as delivered”，实际
   ts_event 是桶内最后一次报价的原始时间（多为秒级、且 69.64% 的行
   ts_event 为空：5,467,291 / 7,850,338），真正的分钟桶时间在 ts_recv。
   代码因此直接丢掉 69.64% 的行；按 ts_event 计只剩 0DTE 94,920 行，
   仅为真实 168,203 行的 56.4%，分钟也错位为 UTC 13:30–19:59。
2. 时区错：ts_recv / ts_event 均为 UTC（后缀 Z），不是 ET。代码不做时区
   转换就把 UTC 的 HH:MM 当作 ET 的 HH:MM 用。默认入场 10:00 ET 应为
   UTC 14:00，代码要的 "10:00" 在 0DTE 分钟键里根本不存在。
3. 因此原代码实测结果为空：cd /tmp 跑未改动的 backtest_real.py，
   输出 "Loaded 1 0DTE days"，distance 20/30/50 × wing 10/20 六组全部
   metrics {}、1H 0、2H 0、无交易、无权益曲线。这不是 2026-10-28 的
   回测盈亏，不得当作交易结果引用。

结论与下一步（未执行，需另行决定）：
- 2026-10-28 默认参数（入场 10:00 ET、出场 15:30 ET、费用 $5.20/笔）
  在现行代码下没有可报的交易结果；不要用 UTC 14:00/19:30 简单替换
  入场/出场时间就声称修好，因为 ts_event 丢行与错位问题仍在。
- 正确修复方向（待定稿后改代码）：以 ts_recv 为分钟桶键，UTC→ET 换算
  后再匹配 10:00/15:30 ET，并把 ts_event 为空的行理解为该分钟内无新
  报价的结转报价（而非丢弃），修完后再重跑 2026-10-28 单日，核对
  ATM、短腿报价、credit 后才谈扫参与样本外。
- 其余 21 天（2026-09-29 至 2026-10-27）尚未解压；2026-10-29 当天
  文件不在这批 zip 内（zip 内 22 个日期文件止于 2026-10-28）。

## 2026-10-30 backtest_real.py 修复完成（2026-10-28 单日验证，n=1）

修复（只改 src/backtest_real.py，其余逻辑不变）：
- 分钟桶键改用 ts_recv：UTC（后缀 Z）→ America/New_York（夏令时，10 月为 EDT UTC-4）
  换算后，再以 ET 日期 + ET HH:MM 作键，入场 10:00 / 出场 15:30 均为 ET。
- ts_event 不再使用：ts_event 为空的行不再丢弃，理解为其 ts_recv 分钟桶的
  结转报价，参与该分钟 bid/ask（2026-10-28 文件中 ts_event 为空占 69.64%）。
- 其余不变：只留 SPXW 且到期日 = 交易日，卖 bid、买 ask，费用 $5.20/笔，
  短腿 bid<=0 跳过，保证金跳过；ATM 代理口径不变，docstring 已注明
  ATM 是链内 call mid 与 put mid 最接近的行权价推断的代理值，不是观测到的
  SPX 现货价，止损/到期检查用的也是同一代理值。

验证（只读本地已解压文件，无 Databento 调用、无下载、无费用；载入约 59 秒）：
- 参数（单组验证，非扫参）：distance 30、wing 10、stop_mult 1.5、
  入场 10:00 ET、出场 15:30 ET、费用 $5.20/笔、1 张、起始 $1000。
- 载入结果：仅 2026-10-28 一天，391 个 ET 分钟桶 09:31–16:01，
  与按 ts_recv 计的 391 桶（UTC 13:31–20:01）一致；入场 10:00 与出场 15:30
  分钟桶均存在。
- 入场 10:00 ET 链：252 个行权价，ATM 推断 7730.0。
- 短腿与报价（真实 bid/ask）：短 put 7700 bid 8.8 / ask 8.9；
  短 call 7760 bid 7.5 / ask 7.6；长 put 7690 ask 6.7；长 call 7770 ask 5.1。
- credit = 8.8 + 7.5 − 6.7 − 5.1 = $4.50；保证金 (10 − 4.50) × 100 = $550
 （≤ $1000，不跳过）；盈亏平衡下限 7695.50、上限 7764.50。
- 过程：15:30 前无盈亏平衡突破；15:30 ET 代理值 7735.0，位于两短腿之间，
  内在价值支付为 0；结果 outcome=expiry，pnl = 450.00 − 5.20 = $444.80，
  权益 $1000 → $1444.80，交易数 1。

明确边界（不得外推）：
- 这是单日、单笔、单组参数的修复验证，不是策略结论、不是回测曲线、
  不得年化；$444.80 是 2026-10-28 这一天这一组参数的结果，样本 n=1。
- 扫参、样本外验证、多日结果均未做，等下一步指令。
- 数据文件（data/databento/，986 MB CSV + 166 MB zst）仍为本地数据，
  不提交进仓库；本次提交只含 src/backtest_real.py + PROGRESS.md。
