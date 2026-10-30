"""IBKR Live（纸面 / 实盘切换点）——仅生成订单草稿，默认不发送。

Web API REST 路线：/iserver/account/{accountId}/orders，需 brokerage session。
    - brokerage session 只从调用者传入的 ``session`` 参数读取。
    - 本模块绝不从环境变量、文件、或任何持久化位置读取密码/凭证，
      绝不保存、打印、记录 session 内容。

4 腿组合单在 Web API 上的支持情况【未实测】：
    - 本模块没有对 IBKR Web API 做过真实组合单下单实测。
    - 下面的组合单构造（``build_iron_condor_draft``）只是按 Web API
      ``/iserver/account/{accountId}/orders`` 的常规订单字段形状拼出的
      草稿结构，是否被 IBKR 接受为一笔组合单【未实测】，不要硬编
      成功响应——本模块不伪造任何 IBKR 返回。
    - 单腿回退路径（``build_single_leg_fallback_drafts``）同样【未实测】：
      它只是把 4 条腿拆成 4 份单腿草稿，是否能按预期分步成交、
      是否会产生腿间滑点/部分成交风险，均【未实测】，默认不启用，
      仅在调用者明确要求时才生成草稿，同样不发送。

硬约束（全部写死，不可由调用参数放宽）：
    - 不下真实订单；live 默认 OFF（``LIVE_ENABLED = False``，
      ``MODE = 'paper'``）。
    - 所有订单函数在未人工点头前只生成待确认的订单草稿并返回，
      绝不发送 HTTP 请求到 IBKR。
    - 发送路径（``send_order_draft``）默认关闭：只有模块级
      ``LIVE_ENABLED`` 被人工显式改为 True，且调用时同时传入
      ``live=True`` 与 ``confirm='YES-I-CONFIRM'``，才可能走到
      HTTP 调用；其他任何情况一律拒绝。演示（``main``）永远不
      调用发送路径，不发任何请求。

风控硬规则与 SPXW 冲突声明（必须明说）：
    - ``MAX_LOSS_PER_TRADE = 150`` 美元。
    - 真实扫参 wing10 的 SPXW 铁鹰 max_loss 为 760-990 美元，
      远超 150 美元上限。因此在 $1000 账户下，SPXW wing10 铁鹰
      本身就不满足“单笔最大亏损 <= $150”，默认拒绝 SPXW 铁鹰。
    - 只有翼宽更小的 ETF 版本（wing - credit 足够小，使
      ``(wing - credit) * 100 <= 150``）才可能通过本检查。
    - 不要把规则放宽去迁就 SPXW：本模块不提供任何放宽参数，
      ``MAX_LOSS_PER_TRADE`` 是模块常量，调用者传参无法覆盖。

铁鹰结构（4 腿）：
    - 卖 put（short put）、卖 call（short call）、
      买 put（long put，保护短 put 下方）、买 call（long call，
      保护短 call 上方）。
    - credit = 卖出两腿的 bid 之和 - 买入两腿的 ask 之和
      （按市价可成交口径的保守估计；实际成交价以经纪商为准）。
    - put 翼宽 = short_put_strike - long_put_strike；
      call 翼宽 = long_call_strike - short_call_strike。
      若两翼不等，max_loss 按较宽一翼保守计算：
      max_loss = (max(put_wing, call_wing) - credit) * 100。
    - breakevens：下 = short_put_strike - credit；
      上 = short_call_strike + credit。
"""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import time as _time
from typing import Any, Dict, List, Optional, Sequence

# ---------------------------------------------------------------------------
# 模式开关：默认全部关闭
# ---------------------------------------------------------------------------

LIVE_ENABLED = False  # 模块级实盘总开关，默认 False；改 True 须人工显式操作
MODE = "paper"  # 默认 'paper'；实盘需同时满足 LIVE_ENABLED + live=True + 确认串
AUTO_TRADING = False  # 全自动模式默认关闭；本模块默认只做人工确认的草稿

# 确认字符串：切换到 live 时调用者必须原样传入，否则拒绝。
LIVE_CONFIRM_STRING = "YES-I-CONFIRM"

# ---------------------------------------------------------------------------
# 风控硬规则常量（写死；下单前逐项检查，任一不通过即拒绝并返回原因）
# ---------------------------------------------------------------------------

MAX_LOSS_PER_TRADE = 150.0  # 美元/笔。见模块 docstring：SPXW wing10 默认被拒。
MAX_OPEN_IRON_CONDORS = 1  # 同时只持 1 个铁鹰
MIN_NET_CREDIT = 0.20  # 美元/股。净权利金下限，工程常量，【待用户点头】
MAX_SPREAD_ABS = 0.30  # 美元。任一腿 bid/ask 价差 > 0.30 即拒绝，工程阈值【待用户点头】
MAX_SPREAD_REL = 0.20  # 任一腿价差 / mid > 20% 即拒绝，工程阈值【待用户点头】
MAX_CONNECTION_FAILURES = 3  # 断线次数超限后只允许平仓草稿
FORCE_CLOSE_TIME_ET = _time(15, 30)  # 15:30 ET 收盘前强制平仓检查点

# Kill Switch：全局开关。True 则全部拒绝（开仓与平仓草稿生成亦拒绝，
# 调用者须先人工解除才可继续；演示中以参数覆盖模拟解除后的纸面检查，
# 不修改本全局值）。
KILL_SWITCH = True

# Web API 路线（仅作草稿字段参考，不在演示中调用）：
#   POST /iserver/account/{accountId}/orders
WEB_API_ORDERS_PATH_TEMPLATE = "/iserver/account/{accountId}/orders"


# ---------------------------------------------------------------------------
# 数据结构
# ---------------------------------------------------------------------------

@dataclass(frozen=True)
class LegQuote:
    """单腿报价。bid/ask 均为美元/股；strike 为行权价。"""

    side: str  # 'SELL' 或 'BUY'（本模块铁鹰中卖腿为 SELL，买腿为 BUY）
    right: str  # 'P' 或 'C'
    strike: float
    bid: float
    ask: float

    @property
    def spread(self) -> float:
        return self.ask - self.bid

    @property
    def mid(self) -> float:
        return (self.bid + self.ask) / 2.0


@dataclass(frozen=True)
class IronCondorQuote:
    """铁鹰 4 腿报价（顺序固定：卖 put、卖 call、买 put、买 call）。"""

    short_put: LegQuote
    short_call: LegQuote
    long_put: LegQuote
    long_call: LegQuote
    symbol: str = "SPXW"  # 仅为草稿标识；ETF 小翼宽版本调用时传入对应标的
    expiry: str = ""  # 例如 '2026-10-30'，草稿字段，不作校验

    def legs(self) -> Sequence[LegQuote]:
        return (self.short_put, self.short_call, self.long_put, self.long_call)


@dataclass
class IronCondorMetrics:
    credit: float
    put_wing: float
    call_wing: float
    wing: float  # 保守翼宽 = max(put_wing, call_wing)
    max_loss: float  # 美元/组合 = (wing - credit) * 100
    lower_breakeven: float
    upper_breakeven: float


@dataclass
class CheckResult:
    """风控检查结果。ok 为 False 时 draft 为 None，且 reasons 非空。"""

    ok: bool
    reasons: List[str] = field(default_factory=list)
    draft: Optional[Dict[str, Any]] = None
    metrics: Optional[IronCondorMetrics] = None
    # 部分成交状态字段：只报警不补单，见 handle_partial_fill。
    # 取值：'NONE' | 'PARTIAL_FILL_ALERT_ONLY_NO_TOPUP'
    fill_status: str = "NONE"


# ---------------------------------------------------------------------------
# 铁鹰计算
# ---------------------------------------------------------------------------

def compute_iron_condor(quote: IronCondorQuote) -> IronCondorMetrics:
    """计算铁鹰 credit / 翼宽 / max_loss / breakevens。纯计算，不发请求。"""
    credit = (
        quote.short_put.bid
        + quote.short_call.bid
        - quote.long_put.ask
        - quote.long_call.ask
    )
    put_wing = quote.short_put.strike - quote.long_put.strike
    call_wing = quote.long_call.strike - quote.short_call.strike
    wing = max(put_wing, call_wing)
    max_loss = (wing - credit) * 100.0
    return IronCondorMetrics(
        credit=credit,
        put_wing=put_wing,
        call_wing=call_wing,
        wing=wing,
        max_loss=max_loss,
        lower_breakeven=quote.short_put.strike - credit,
        upper_breakeven=quote.short_call.strike + credit,
    )


# ---------------------------------------------------------------------------
# 风控检查（逐项，任一不通过即拒绝）
# ---------------------------------------------------------------------------

def _check_spreads(quote: IronCondorQuote, reasons: List[str]) -> None:
    for leg in quote.legs():
        if leg.spread > MAX_SPREAD_ABS:
            reasons.append(
                f"价差过宽拒绝：{leg.right} strike={leg.strike} "
                f"spread={leg.spread:.2f} > {MAX_SPREAD_ABS:.2f} "
                f"（工程阈值，待用户点头）"
            )
        elif leg.mid > 0 and leg.spread / leg.mid > MAX_SPREAD_REL:
            reasons.append(
                f"价差过宽拒绝：{leg.right} strike={leg.strike} "
                f"相对价差={leg.spread / leg.mid:.1%} > {MAX_SPREAD_REL:.0%} "
                f"（工程阈值，待用户点头）"
            )


def check_risk(
    quote: IronCondorQuote,
    *,
    direction_ok: bool,
    daily_stop_hit: bool,
    open_iron_condors: int = 0,
    connection_failures: int = 0,
    kill_switch: Optional[bool] = None,
    session: Any = None,
) -> CheckResult:
    """下单前风控逐项检查。

    参数说明（均由调用者传入，本模块不从环境/文件读取）：
      direction_ok: 决策树方向是否符合；False 则拒绝。
      daily_stop_hit: 当日止损是否已触发；True 则拒绝。
      open_iron_condors: 当前持有铁鹰数；>= 1 时新开仓拒绝（只持 1 个）。
      connection_failures: 累计断线次数；超限后只允许平仓草稿（本函数
        只做开仓检查，超限时直接拒绝，见 build_close_draft）。
      kill_switch: 默认取全局 KILL_SWITCH；显式传入可覆盖单次检查，
        但不修改全局值。
      session: 调用者传入的 brokerage session（可为 None；仅记录
        “是否提供”，绝不保存/打印其内容）。

    返回 CheckResult；ok 为 False 时 draft 为 None。
    本函数只检查、不发送任何请求。
    """
    if kill_switch is None:
        kill_switch = KILL_SWITCH

    reasons: List[str] = []
    metrics = compute_iron_condor(quote)

    # 1. Kill Switch：全局 KILL_SWITCH=True 则全部拒绝。
    if kill_switch:
        reasons.append("Kill Switch 已开启（KILL_SWITCH=True），全部拒绝。")

    # 2. 断线超限：只平仓模式——开仓一律拒绝。
    if connection_failures > MAX_CONNECTION_FAILURES:
        reasons.append(
            f"断线超限：connection_failures={connection_failures} > "
            f"{MAX_CONNECTION_FAILURES}，只允许平仓草稿，开仓拒绝。"
        )

    # 3. 每日止损到即停。
    if daily_stop_hit:
        reasons.append("每日止损已触发（daily_stop_hit=True），停止开仓。")

    # 4. 只做决策树符合方向。
    if not direction_ok:
        reasons.append("决策树方向不符合（direction_ok=False），拒绝。")

    # 5. 同时只持 1 个铁鹰，绝不加仓加倍。
    if open_iron_condors >= MAX_OPEN_IRON_CONDORS:
        reasons.append(
            f"已持有 {open_iron_condors} 个铁鹰，同时只持 1 个，"
            "拒绝加仓/加倍开仓。"
        )

    # 6. 短腿 bid <= 0 拒绝。
    for label, leg in (("short_put", quote.short_put), ("short_call", quote.short_call)):
        if leg.bid <= 0:
            reasons.append(f"短腿 bid<=0 拒绝：{label} bid={leg.bid}。")

    # 7. 净权利金低于下限拒绝。
    if metrics.credit < MIN_NET_CREDIT:
        reasons.append(
            f"净权利金过低拒绝：credit={metrics.credit:.2f} < "
            f"MIN_NET_CREDIT={MIN_NET_CREDIT:.2f}（待用户点头）。"
        )

    # 8. 价差过宽拒绝（绝对 + 相对双阈值）。
    _check_spreads(quote, reasons)

    # 9. 单笔最大亏损硬上限——与 SPXW wing10 冲突，见模块 docstring。
    # $1000 账户下 SPXW wing10 铁鹰 max_loss 760-990 美元，本项必拒；
    # 只有 ETF 小翼宽版本才可能通过。本规则不放宽。
    if metrics.max_loss > MAX_LOSS_PER_TRADE:
        reasons.append(
            f"单笔最大亏损超限拒绝：max_loss=${metrics.max_loss:.2f} > "
            f"MAX_LOSS_PER_TRADE=${MAX_LOSS_PER_TRADE:.2f}。"
            f"（提示：SPXW wing10 铁鹰 max_loss 通常 760-990 美元，"
            "在 $1000 账户下本身就不满足本规则，默认拒绝；"
            "ETF 小翼宽版本才可能通过；不要放宽规则迁就 SPXW。）"
        )

    # 10. 翼宽异常（保护腿不在短腿外侧）直接拒绝，避免 max_loss 失真。
    if metrics.put_wing <= 0 or metrics.call_wing <= 0:
        reasons.append(
            f"翼宽异常拒绝：put_wing={metrics.put_wing}，"
            f"call_wing={metrics.call_wing}，保护腿必须在短腿外侧。"
        )

    if reasons:
        return CheckResult(ok=False, reasons=reasons, draft=None, metrics=metrics)

    draft = build_iron_condor_draft(
        quote, metrics, session_provided=session is not None
    )
    return CheckResult(ok=True, reasons=[], draft=draft, metrics=metrics)


# ---------------------------------------------------------------------------
# 订单草稿构造（只生成草稿，绝不发送）
# ---------------------------------------------------------------------------

def build_iron_condor_draft(
    quote: IronCondorQuote,
    metrics: IronCondorMetrics,
    *,
    session_provided: bool = False,
) -> Dict[str, Any]:
    """构造 4 腿组合单草稿并返回，不发送。

    【未实测】：组合单在 Web API ``/iserver/account/{accountId}/orders``
    上的支持情况未实测；以下只是草稿字段形状，不要当作已验证的
    成功请求体，也不要硬编成功响应。
    """
    legs = []
    for leg in quote.legs():
        legs.append(
            {
                "side": leg.side,
                "right": leg.right,
                "strike": leg.strike,
                "reference_price": leg.bid if leg.side == "SELL" else leg.ask,
            }
        )
    return {
        "status": "DRAFT-NOT-SENT",
        "kind": "IRON_CONDOR_COMBO",
        "combo_support": "UNTESTED",  # 组合单 Web API 支持情况【未实测】
        "web_api_path": WEB_API_ORDERS_PATH_TEMPLATE,
        "symbol": quote.symbol,
        "expiry": quote.expiry,
        "mode": MODE,
        "live_enabled": LIVE_ENABLED,
        "legs": legs,
        "net_credit_per_share": round(metrics.credit, 4),
        "max_loss_usd": round(metrics.max_loss, 2),
        "lower_breakeven": round(metrics.lower_breakeven, 4),
        "upper_breakeven": round(metrics.upper_breakeven, 4),
        "session_provided_by_caller": session_provided,
        "note": (
            "本草稿未发送。发送需人工点头 + LIVE_ENABLED + "
            "live=True + confirm='YES-I-CONFIRM'，且组合单支持情况未实测。"
        ),
    }


def build_single_leg_fallback_drafts(
    quote: IronCondorQuote,
    metrics: IronCondorMetrics,
) -> List[Dict[str, Any]]:
    """单腿回退路径：把 4 条腿拆成 4 份单腿草稿，仅生成、不发送。

    【未实测】：分步单腿成交的滑点、腿间风险与最终组合形态均未实测；
    默认不启用，仅在调用者明确要求时生成草稿。
    """
    drafts: List[Dict[str, Any]] = []
    for leg in quote.legs():
        drafts.append(
            {
                "status": "DRAFT-NOT-SENT",
                "kind": "SINGLE_LEG_FALLBACK",
                "fallback_support": "UNTESTED",  # 单腿回退路径【未实测】
                "web_api_path": WEB_API_ORDERS_PATH_TEMPLATE,
                "symbol": quote.symbol,
                "expiry": quote.expiry,
                "side": leg.side,
                "right": leg.right,
                "strike": leg.strike,
                "reference_price": leg.bid if leg.side == "SELL" else leg.ask,
                "note": "单腿回退草稿，未发送；分步成交风险未实测。",
            }
        )
    return drafts


def build_order_draft(
    quote: IronCondorQuote,
    *,
    direction_ok: bool,
    daily_stop_hit: bool,
    open_iron_condors: int = 0,
    connection_failures: int = 0,
    kill_switch: Optional[bool] = None,
    live: bool = False,
    confirm: str = "",
    auto: bool = False,
    session: Any = None,
) -> CheckResult:
    """开仓订单入口：先过 live/auto 门禁，再过风控，全部只返回草稿。

    门禁：
      - ``live=True`` 时必须同时满足模块级 ``LIVE_ENABLED`` 与
        ``confirm == 'YES-I-CONFIRM'``，否则拒绝（且不发送）。
      - ``auto=True``（全自动）为另行参数，默认 False；auto 模式
        同样只生成草稿，发送仍需上述 live 门禁全过。
      - 无论门禁是否通过，本函数本身绝不发送 HTTP 请求；发送只在
        ``send_order_draft`` 中，且该路径默认关闭。
    """
    if live and (not LIVE_ENABLED or confirm != LIVE_CONFIRM_STRING):
        return CheckResult(
            ok=False,
            reasons=[
                "实盘门禁未通过：live=True 需要 LIVE_ENABLED=True 且 "
                "confirm='YES-I-CONFIRM'；当前 LIVE_ENABLED="
                f"{LIVE_ENABLED}，确认串{'相符' if confirm == LIVE_CONFIRM_STRING else '不符'}，拒绝。"
            ],
            draft=None,
            metrics=compute_iron_condor(quote),
        )
    if auto and not live:
        return CheckResult(
            ok=False,
            reasons=[
                "全自动模式（auto=True）须另行与 live 门禁一并确认；"
                "当前 live=False，拒绝自动开仓草稿。"
            ],
            draft=None,
            metrics=compute_iron_condor(quote),
        )
    return check_risk(
        quote,
        direction_ok=direction_ok,
        daily_stop_hit=daily_stop_hit,
        open_iron_condors=open_iron_condors,
        connection_failures=connection_failures,
        kill_switch=kill_switch,
        session=session,
    )


# ---------------------------------------------------------------------------
# 收盘前强制平仓检查、部分成交、断线模式
# ---------------------------------------------------------------------------

def check_force_close(
    *,
    position_open: bool,
    current_time_et: _time,
    symbol: str = "",
) -> Dict[str, Any]:
    """收盘前强制平仓检查。

    调用方应在 15:30 ET 前调用本函数做收盘前检查；一旦到达
    15:30 ET 仍未平仓，即报警并生成平仓草稿。
    只报警 + 只生成草稿，不自动发送、不自动平仓。

    返回：{"alert": bool, "close_draft": dict | None, "message": str}
    """
    if position_open and current_time_et >= FORCE_CLOSE_TIME_ET:
        draft = {
            "status": "DRAFT-NOT-SENT",
            "kind": "FORCE_CLOSE",
            "web_api_path": WEB_API_ORDERS_PATH_TEMPLATE,
            "symbol": symbol,
            "note": (
                "15:30 ET 后仍未平仓：报警并生成平仓草稿，"
                "不自动发送，需人工确认后才可执行。"
            ),
        }
        return {
            "alert": True,
            "close_draft": draft,
            "message": "FORCE_CLOSE_ALERT：15:30 ET 后仍有未平仓铁鹰，已生成平仓草稿（未发送）。",
        }
    return {
        "alert": False,
        "close_draft": None,
        "message": "无需强制平仓（仓位已平或未到 15:30 ET 检查点）。",
    }


def build_close_draft(
    *,
    symbol: str = "",
    kill_switch: Optional[bool] = None,
    connection_failures: int = 0,
) -> Dict[str, Any]:
    """生成平仓草稿（断线超限后只允许的模式），不发送。

    断线超限（connection_failures > MAX_CONNECTION_FAILURES）时，
    开仓已拒绝，但平仓草稿仍可生成——平仓本身仍需人工确认与发送门禁。
    Kill Switch 开启时连平仓草稿也拒绝生成，须人工先解除。
    """
    if kill_switch is None:
        kill_switch = KILL_SWITCH
    if kill_switch:
        return {
            "status": "REJECTED",
            "kind": "FORCE_CLOSE",
            "reason": "Kill Switch 已开启（KILL_SWITCH=True），平仓草稿亦拒绝生成。",
        }
    return {
        "status": "DRAFT-NOT-SENT",
        "kind": "FORCE_CLOSE",
        "web_api_path": WEB_API_ORDERS_PATH_TEMPLATE,
        "symbol": symbol,
        "close_only_mode": connection_failures > MAX_CONNECTION_FAILURES,
        "note": "平仓草稿，未发送；需人工确认后才可执行。",
    }


def handle_partial_fill(result: CheckResult) -> CheckResult:
    """部分成交处理：只报警，不补单。

    部分成交只报警不补单：本函数仅把 ``fill_status`` 置为
    ``PARTIAL_FILL_ALERT_ONLY_NO_TOPUP`` 并附加报警原因，
    绝不生成补单/加仓草稿，绝不加倍。
    """
    result.fill_status = "PARTIAL_FILL_ALERT_ONLY_NO_TOPUP"
    result.reasons.append(
        "PARTIAL_FILL_ALERT：检测到部分成交，只报警，不补单、不加仓。"
    )
    return result


# ---------------------------------------------------------------------------
# 发送路径（默认关闭；演示与订单函数永不调用）
# ---------------------------------------------------------------------------

def send_order_draft(
    draft: Dict[str, Any],
    *,
    account_id: str,
    live: bool = False,
    confirm: str = "",
    session: Any = None,
) -> Dict[str, Any]:
    """发送路径——默认关闭。

    只有同时满足以下全部条件才可能走到 HTTP 调用：
      LIVE_ENABLED=True、live=True、confirm='YES-I-CONFIRM'、
      draft 状态为 DRAFT-NOT-SENT、调用者传入 session。
    其他任何情况一律拒绝且不发请求。
    即使门禁全过，组合单支持情况仍【未实测】，调用者须自行承担
    实测风险；本函数不伪造成功响应，原样返回 HTTP 层结果或异常。
    """
    if not LIVE_ENABLED or not live or confirm != LIVE_CONFIRM_STRING:
        return {
            "status": "SEND_REFUSED",
            "reason": (
                "发送路径关闭：需 LIVE_ENABLED=True + live=True + "
                "confirm='YES-I-CONFIRM'，当前未全部满足，不发任何请求。"
            ),
        }
    if draft.get("status") != "DRAFT-NOT-SENT":
        return {"status": "SEND_REFUSED", "reason": "非待发送草稿，拒绝发送。"}
    if session is None:
        return {
            "status": "SEND_REFUSED",
            "reason": "未传入调用者的 brokerage session，拒绝发送。",
        }
    # 门禁全过后的唯一 HTTP 调用点（requests 可选；未安装则明确报错）。
    try:
        import requests  # type: ignore
    except ImportError:
        return {"status": "SEND_REFUSED", "reason": "requests 未安装，无法发送。"}
    path = WEB_API_ORDERS_PATH_TEMPLATE.replace("{accountId}", account_id)
    # 注意：此处不拼 base URL、不硬编成功响应；实际 base URL 与
    # session 鉴权方式须由调用者在实测时提供，组合单支持情况【未实测】。
    raise NotImplementedError(
        "发送路径的 base URL 与 session 鉴权细节未实测、未接入，"
        f"故不擅自向 {path} 发请求；请在人工实测环境下补齐后再启用。"
    )


# ---------------------------------------------------------------------------
# 演示：假报价跑风控检查并打印拒绝 / 草稿结果，不发任何请求
# ---------------------------------------------------------------------------

def _fake_etf_quote() -> IronCondorQuote:
    """ETF 小翼宽假报价：wing=1，credit=0.40，max_loss=(1-0.40)*100=60 <= 150。"""
    return IronCondorQuote(
        symbol="FAKE-ETF",
        expiry="2026-10-30",
        short_put=LegQuote("SELL", "P", 100.0, 0.60, 0.65),
        short_call=LegQuote("SELL", "C", 102.0, 0.60, 0.65),
        long_put=LegQuote("BUY", "P", 99.0, 0.35, 0.40),
        long_call=LegQuote("BUY", "C", 103.0, 0.35, 0.40),
    )


def _fake_spxw_wing10_quote() -> IronCondorQuote:
    """SPXW wing10 假报价：wing=10，credit 约 1.50，max_loss 约 850 美元。"""
    return IronCondorQuote(
        symbol="SPXW",
        expiry="2026-10-30",
        short_put=LegQuote("SELL", "P", 6000.0, 1.20, 1.25),
        short_call=LegQuote("SELL", "C", 6020.0, 1.20, 1.25),
        long_put=LegQuote("BUY", "P", 5990.0, 0.40, 0.45),
        long_call=LegQuote("BUY", "C", 6030.0, 0.40, 0.45),
    )


def main() -> None:
    print(f"MODE={MODE} LIVE_ENABLED={LIVE_ENABLED} KILL_SWITCH={KILL_SWITCH}")
    print("演示说明：以下全部为假报价风控检查，不发任何 HTTP 请求。\n")

    # 案例 1：Kill Switch 默认开启 → 全部拒绝。
    r1 = build_order_draft(
        _fake_etf_quote(),
        direction_ok=True,
        daily_stop_hit=False,
        open_iron_condors=0,
    )
    print("案例 1（ETF 小翼宽，Kill Switch 默认 True）：")
    print(f"  ok={r1.ok} reasons={r1.reasons} draft={r1.draft}\n")

    # 案例 2：模拟解除 Kill Switch 后的纸面检查（参数覆盖单次检查，
    # 不修改全局 KILL_SWITCH）→ ETF 小翼宽通过，生成草稿（未发送）。
    r2 = build_order_draft(
        _fake_etf_quote(),
        direction_ok=True,
        daily_stop_hit=False,
        open_iron_condors=0,
        kill_switch=False,
    )
    print("案例 2（ETF 小翼宽，kill_switch=False 单次覆盖，paper 草稿）：")
    if r2.metrics:
        m = r2.metrics
        print(
            f"  credit={m.credit:.2f} wing={m.wing} max_loss=${m.max_loss:.2f} "
            f"breakevens=({m.lower_breakeven:.2f}, {m.upper_breakeven:.2f})"
        )
    print(f"  ok={r2.ok} reasons={r2.reasons}")
    print(f"  draft={r2.draft}\n")

    # 案例 3：SPXW wing10 → 即使 Kill Switch 解除，也被 $150 上限拒绝。
    r3 = build_order_draft(
        _fake_spxw_wing10_quote(),
        direction_ok=True,
        daily_stop_hit=False,
        open_iron_condors=0,
        kill_switch=False,
    )
    print("案例 3（SPXW wing10，kill_switch=False 单次覆盖）：")
    if r3.metrics:
        m = r3.metrics
        print(
            f"  credit={m.credit:.2f} wing={m.wing} max_loss=${m.max_loss:.2f} "
            f"breakevens=({m.lower_breakeven:.2f}, {m.upper_breakeven:.2f})"
        )
    print(f"  ok={r3.ok} reasons={r3.reasons} draft={r3.draft}\n")

    # 案例 4：实盘门禁未过 → 拒绝（仍不发送）。
    r4 = build_order_draft(
        _fake_etf_quote(),
        direction_ok=True,
        daily_stop_hit=False,
        open_iron_condors=0,
        kill_switch=False,
        live=True,
        confirm="NO",
    )
    print("案例 4（live=True 但确认串错误）：")
    print(f"  ok={r4.ok} reasons={r4.reasons}\n")

    # 案例 5：15:30 ET 后未平仓 → 报警 + 平仓草稿（未发送）。
    fc = check_force_close(
        position_open=True, current_time_et=_time(15, 35), symbol="FAKE-ETF"
    )
    print("案例 5（15:35 ET 仍未平仓）：")
    print(f"  alert={fc['alert']} message={fc['message']}")
    print(f"  close_draft={fc['close_draft']}\n")

    print("演示结束：全程未调用发送路径，未发任何请求。")


if __name__ == "__main__":
    main()
