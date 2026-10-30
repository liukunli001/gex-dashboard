# GEX Dashboard — 免费版

一个用**免费期权 API**实现的 GEX（Gamma Exposure）看板，复刻 GammaForceMatrix
核心功能：逐行权价 GEX 分布、Call Wall / Put Wall、Gamma Flip、Max Pain、
Charm 暴露。不下单、不收费、不需要 API Key。

> 本项目是独立实现，**没有复制 GammaForceMatrix 的代码**（也从未见过其源码）。
> 方法论来自公开的 GEX 定义，数据来自 CBOE 公开延迟行情。

## 数据来源：为什么是免费的

CBOE 延迟行情接口，无需注册、无需 Key，约 15 分钟延迟：

```
https://cdn.cboe.com/api/global/delayed_quotes/options/QQQ.json
https://cdn.cboe.com/api/global/delayed_quotes/options/SPY.json
https://cdn.cboe.com/api/global/delayed_quotes/options/IWM.json
```

每条合约直接给出 `gamma / delta / iv / open_interest / volume`，
所以不需要自己反推 IV、也不需要付费的 Databento / OPRA 订阅。
代价：延迟 15 分钟、盘中 OI 不是实时 tick。范围说明：QQQ 跟踪 Nasdaq-100（NDX）、
SPY 跟踪 S&P 500（SPX）、IWM 跟踪 Russell 2000（RUT），但本项目只算三只 ETF 自身的期权链，
不算 SPX / NDX / RUT 指数期权链——ETF 期权与指数期权是两个不同的持仓池，GEX 不能混用。

## 快速开始

```bash
pip install -r requirements.txt   # 只有 requests
python generate.py --symbols QQQ SPY IWM
# 打开 dashboard.html（纯静态页，无需服务器）
# 数据同时存为 gex_data.json
python generate.py --symbols SPY --expiry 2026-10-30
python tests/test_gex.py
```

默认取最近一个未到期日；美东 16:00 收盘后自动跳到下一到期日（0DTE 当天已过期无意义）。

## 算了什么

```
GEX(合约) = gamma × 持仓量 × 100 × 现货价² × 1%
Call GEX(K) = 该行权价所有 Call 的 GEX 之和（正）
Put  GEX(K) = 该行权价所有 Put 的 GEX 之和（取负，计入净值）
Net  GEX(K) = Call GEX(K) + Put GEX(K)
Total GEX   = Σ Net GEX(K)
```

- **Call Wall**：Call GEX 最大的行权价（阻力）
- **Put Wall**：Put GEX 最负的行权价（支撑）
- **Gamma Flip**：从最高行权价向下累加 Net GEX，首次穿零的行权价（取离现货最近的一次）；
  上方做市商行为压波动，下方放大波动
- **Max Pain**：使全部未平仓期权到期内在价值总和最小的行权价
- **Charm**：Black-Scholes 二阶希腊字母，
  `Charm = -N'(d1) × (2rT - d2·σ√T) / (2T·σ√T)`，r 默认 3.5%，按持仓量加权聚合
- **Vanna**：`Vanna = -N'(d1) × d2 / σ`，见 `src/bs.py`

## 最重要的 caveat（所有公开 GEX 工具都一样）

**做市商真实净头寸是不可观测的。** 这里用全部持仓量作做市商对冲盘的代理。
在指数期权上这个代理相对合理，在个股、财报、小盘上会失真。
所以这张图是**结构参考**，不是做市商账本；墙和翻转点是“对冲压力集中区”，
不是“价格一定会在这里反转”的保证。绝对美元 GEX 值不要跨平台直接比较——
不同平台的缩放系数（是否乘 100、是否乘现货价）不同，排名和墙不受影响。

## 本项目有 / 没有

有：QQQ / SPY / IWM 的 GEX 分布、关键价位、Charm 列、静态看板、JSON 数据、单元测试。
没有（GFM 有，本项目故意不做或列为后续）：Discord 自动信号推送、Iron Condor 自动推送、
30 天回放数据库（每天存一份 `gex_data.json` 即可自己搭）、US Stocks 模块、3D IV Surface、
实时 WebSocket（CBOE 免费端点是拉取式，每分钟跑一次 `generate.py` 即等价）。

## 文件结构

```
generate.py          CLI：拉取 → 聚合 → 生成 dashboard.html + gex_data.json
src/cboe.py          CBOE 免费接口 + 期权代码解析（到期日/方向/行权价）
src/gex.py           按行权价聚合 + Call/Put Wall + Flip + Max Pain
src/bs.py            Black-Scholes Vanna / Charm
tests/test_gex.py    聚合与关键价位的合成数据测试
```

## 免责

延迟行情、公开假设、简化模型（Black-Scholes、r=3.5%），本项目不构成投资建议，
不执行交易。实盘前请用自己的回测验证任何基于 GEX 的策略——包括所谓“年化 500%”的说法。
