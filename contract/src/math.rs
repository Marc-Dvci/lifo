//! The curve (A2, A4) and the gate (C3) in 256-bit integers.
//!
//! `k = X * Y` reaches about 3.2e52 on the launch curve, above `u128::MAX` (3.4e38), so every product goes through
//! `U256` from the `uint` crate (`construct_uint!`). Each function is the same formula as in `lifo/model.py`, with the
//! same rounding: what a buyer or seller receives is rounded down, so the remainder stays in the reserve.

#![allow(clippy::manual_div_ceil, clippy::assign_op_pattern)] // the second one is inside construct_uint!

uint::construct_uint! {
    /// Unsigned 256-bit integer.
    pub struct U256(4);
}

pub const BPS: u128 = 10_000;

pub fn u(x: u128) -> U256 {
    U256::from(x)
}

/// `a * b / c`, rounded down, with a 256-bit intermediate. The result must fit in `u128`.
pub fn mul_div(a: u128, b: u128, c: u128) -> u128 {
    (u(a) * u(b) / u(c)).as_u128()
}

pub fn ceil_div(a: U256, b: U256) -> U256 {
    (a + b - 1) / b
}

/// A2: tokens a buy of `deposit` receives, `Y - ceil(k / (X + d))`.
pub fn buy_out(x: U256, y: U256, deposit: u128) -> u128 {
    (y - ceil_div(x * y, x + u(deposit))).as_u128()
}

/// A4: NEAR the curve gives for `tokens` before the fee, `X - ceil(k / (Y + f))`.
pub fn sell_gross(x: U256, y: U256, tokens: u128) -> u128 {
    (x - ceil_div(x * y, y + u(tokens))).as_u128()
}

/// A4: what the sellers get from `gross`. The fee is rounded up and stays in the reserve.
pub fn after_fee(gross: u128, fee_bps: u128) -> u128 {
    gross - ceil_div(u(gross) * u(fee_bps), u(BPS)).as_u128()
}

/// C3: the largest token amount `F` with `after_fee(sell_gross(F)) <= cap`, or `None` if every amount fits.
///
/// `paid(g) = floor(g * (B - fee) / B)`, so the largest allowed gross is `G = floor(((cap + 1) * B - 1) / (B - fee))`.
/// `gross(f) <= G` exactly when `ceil(k / (Y + f)) >= m` with `m = X - G`. For `m >= 2` that is
/// `(m - 1) * (Y + f) < k`, so `F = floor((k - 1) / (m - 1)) - Y`. For `m <= 1` no amount can exceed the cap.
/// The test `c3_gate_fill_is_the_largest_amount_under_the_cap` checks both sides of the bound.
pub fn gate_fill(x: U256, y: U256, cap: u128, fee_bps: u128) -> Option<U256> {
    let largest_gross = (u(cap) + 1) * u(BPS) - 1;
    let largest_gross = largest_gross / u(BPS - fee_bps);
    if largest_gross + 1 >= x {
        return None;
    }
    let m = x - largest_gross;
    Some((x * y - 1) / (m - 1) - y)
}
