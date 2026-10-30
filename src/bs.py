"""Black-Scholes second-order Greeks: Vanna and Charm.

Formulas as published by GammaForceMatrix's own help pages:
    Vanna = -N'(d1) * d2 / sigma
    Charm = -N'(d1) * (2*r*T - d2*sigma*sqrt(T)) / (2*T*sigma*sqrt(T))
with d1, d2 the usual Black-Scholes terms, r = 0.035 by default
(the rate GFM's calculator states), T in years.

Note: sign conventions for Charm differ across vendors. This module
uses the convention above consistently; do not compare its sign
directly with a platform that defines Charm = +dDelta/dT.
"""
import math


def _norm_pdf(x):
    return math.exp(-0.5 * x * x) / math.sqrt(2.0 * math.pi)


def _d1_d2(spot, strike, vol, t_years, r=0.035):
    if spot <= 0 or strike <= 0 or vol <= 0 or t_years <= 0:
        return None, None
    d1 = (math.log(spot / strike) + (r + 0.5 * vol * vol) * t_years) / (vol * math.sqrt(t_years))
    d2 = d1 - vol * math.sqrt(t_years)
    return d1, d2


def vanna_charm(spot, strike, vol, t_years, r=0.035):
    d1, d2 = _d1_d2(spot, strike, vol, t_years, r)
    if d1 is None:
        return 0.0, 0.0
    pdf = _norm_pdf(d1)
    vanna = -pdf * d2 / vol
    charm = -pdf * (2 * r * t_years - d2 * vol * math.sqrt(t_years)) / (2 * t_years * vol * math.sqrt(t_years))
    return vanna, charm
