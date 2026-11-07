//! Unit tests named by requirement (A, B, C, D in `requirements.md`, launch rules of `requirements-v2.md`), plus the
//! contract-only rules: storage (NEP-145) and deposits.

mod common;

use common::*;
use lifo::math::{after_fee, gate_fill, sell_gross, u};
use lifo::*;
use near_sdk::json_types::{U128, U64};
use near_sdk::serde_json::Value;
use near_sdk::{env, PromiseResult};

// Small pool of `small()`: X 10,000, Y 1,000,000, gate 10 %, fee 1 %, epoch 10, vintage 20 (2 epochs), launch 100.
fn t(epoch: u64) -> u64 {
    100 + 10 * epoch
}

fn setup(names: &[&str]) {
    quiet();
    deploy(small());
    for n in names {
        register(n, 10);
    }
}

fn buy(name: &str, now: u64, deposit: u128) -> u128 {
    call(&acct(name), now, deposit, |c| c.buy().0).unwrap()
}

fn sell(name: &str, now: u64, amount: u128) -> u64 {
    call(&acct(name), now, 0, |c| c.request_sell(U128(amount)).0).unwrap()
}

fn send(from: &str, to: &str, now: u64, amount: u128) {
    call(&acct(from), now, 1, |c| {
        c.ft_transfer(acct(to), U128(amount), None)
    })
    .unwrap()
}

fn claim(name: &str, now: u64) -> Vec<ClaimView> {
    call(&acct(name), now, 0, |c| c.claim()).unwrap()
}

fn settle(now: u64) -> u32 {
    call(&acct("keeper"), now, 0, |c| c.settle()).unwrap()
}

fn lots(name: &str) -> Vec<(u64, u128)> {
    view(|c| c.get_account(acct(name)))
        .map_or(vec![], |a| a.lots.iter().map(|(v, x)| (*v, x.0)).collect())
}

fn balance(name: &str) -> u128 {
    lots(name).iter().map(|l| l.1).sum()
}

fn pool() -> (u128, u128, u128, u128) {
    let p = view(|c| c.get_pool());
    (p.x.0, p.y.0, p.reserve.0, p.supply.0)
}

fn paid(claims: &[ClaimView]) -> u128 {
    claims.iter().map(|c| c.paid.0).sum()
}

/// A refused call: its message, and proof that the state did not change.
fn refused<R>(who: &str, now: u64, deposit: u128, f: impl FnOnce(&mut Lifo) -> R) -> String {
    let before = (pool(), view(|c| c.get_open().requested));
    let e = call(&acct(who), now, deposit, f)
        .err()
        .expect("the call should be refused");
    assert_eq!(
        before,
        (pool(), view(|c| c.get_open().requested)),
        "a refused call changed the state"
    );
    e
}

// --- A. The pool -----------------------------------------------------------------------------------------------

#[test]
fn a1_initialisation_checks_every_parameter() {
    quiet();
    let bad = [
        Params {
            virtual_near: U128(0),
            ..small()
        },
        Params {
            max_supply: U128(1_000_000),
            ..small()
        },
        Params {
            gate_bps: 0,
            ..small()
        },
        Params {
            gate_bps: 10_001,
            ..small()
        },
        Params {
            sell_fee_bps: 10_000,
            ..small()
        },
        Params {
            vintage_length: U64(25),
            ..small()
        },
        Params {
            max_lots: 0,
            ..small()
        },
        Params {
            max_lots: MAX_LOTS_LIMIT + 1,
            ..small()
        },
    ];
    for p in bad {
        deploy(small());
        let e = call(&contract_id(), 100, 0, |_| Lifo::new(p.clone())).err();
        assert_eq!(e.as_deref(), Some(E_PARAMS), "{p:?}");
    }
}

#[test]
fn a1_parameters_never_change() {
    setup(&["a", "b"]);
    let before = view(|c| c.get_params());
    buy("a", t(0), 5_000);
    sell("a", t(1), 1_000);
    send("a", "b", t(2), 10);
    settle(t(3));
    assert_eq!(view(|c| c.get_params()), before);
}

#[test]
fn a2_buy_gives_floor_of_curve_output() {
    setup(&["a"]);
    assert_eq!(buy("a", t(0), 10_000), 500_000);
    for d in [1u128, 3, 7, 13, 999, 4_001] {
        let (x, y, r, s) = pool();
        let out = buy("a", t(0), d);
        assert_eq!(
            out,
            y * d / (x + d),
            "deposit {d}: Y - ceil(k / (X + d)) is floor(Y d / (X + d))"
        );
        assert_eq!(pool(), (x + d, y - out, r + d, s + out));
    }
    assert_eq!(lots("a").len(), 1, "every buy of one vintage is one lot");
}

#[test]
fn a2_first_buy_pays_the_account_storage_from_the_deposit() {
    quiet();
    deploy(small());
    let cost = view(|c| c.storage_balance_bounds().min.0);
    let deposit = cost + 10_000;
    assert_eq!(
        call(&acct("new"), t(0), cost, |c| c.buy()).err().as_deref(),
        Some(E_STORAGE)
    );
    assert_eq!(
        buy("new", t(0), deposit),
        500_000,
        "the buy uses the deposit less the storage"
    );
    assert_eq!(pool().2, 10_000);
    assert_eq!(
        view(|c| c.storage_balance_of(acct("new"))).unwrap().total.0,
        cost
    );
}

#[test]
fn a3_buy_above_max_supply_is_refused_with_the_state_unchanged() {
    setup(&["a"]);
    assert_eq!(refused("a", t(0), 10u128.pow(9), |c| c.buy()), E_MAX_SUPPLY);
    assert_eq!(refused("a", t(0), 0, |c| c.buy()), E_ZERO);
    buy("a", t(0), 10_000);
    assert_eq!(refused("a", t(0), 0, |c| c.buy()), E_ZERO);
    deploy(Params {
        virtual_near: U128(10u128.pow(6)),
        virtual_tokens: U128(10u128.pow(5)),
        max_supply: U128(80_000),
        ..small()
    });
    register("a", 10);
    assert_eq!(refused("a", t(0), 1, |c| c.buy()), E_BUY_TOO_SMALL);
}

#[test]
fn a4_sell_pays_gross_less_a_rounded_up_fee_and_k_never_decreases() {
    setup(&["a"]);
    buy("a", t(0), 7_001);
    let (x, y, _, _) = pool();
    let k = x * y;
    sell("a", t(1), 12_345);
    let claims = claim("a", t(2));
    let gross = x * 12_345 / (y + 12_345);
    let fee = (gross * 100).div_ceil(10_000);
    assert_eq!(paid(&claims), gross - fee);
    let (x2, y2, _, _) = pool();
    assert!(
        x2 * y2 >= k,
        "the fee stays in the reserve: k never decreases"
    );
}

#[test]
fn a5_reserve_covers_buying_back_the_whole_supply() {
    setup(&["a", "b", "c"]);
    for (i, who) in ["a", "b", "c", "a", "b"].iter().enumerate() {
        buy(who, t(2 * i as u64), 3_000 + i as u128);
    }
    for round in 0..6u64 {
        for who in ["a", "b", "c"] {
            if balance(who) > 0 {
                sell(who, t(20 + round), balance(who));
            }
        }
        let (x, y, r, s) = pool();
        let buyback = sell_gross(u(x), u(y), s);
        assert!(
            buyback <= r,
            "round {round}: buying back {s} tokens costs {buyback}, reserve {r}"
        );
    }
}

#[test]
fn a5_contract_storage_is_covered_by_what_accounts_reserved() {
    quiet();
    deploy(Params {
        max_lots: 16,
        ..small()
    });
    let names = ["a", "b", "c", "d"];
    for n in names {
        register(n, 1);
    }
    let base = storage_bytes();
    let byte = env::storage_byte_cost().as_yoctonear();
    let check = |when: &str| {
        let used = storage_bytes() - base;
        let reserved: u128 = names
            .iter()
            .map(|n| {
                view(|c| c.storage_balance_of(acct(n))).map_or(0, |s| s.total.0 - s.available.0)
            })
            .sum();
        assert!(
            used as u128 * byte <= reserved,
            "{when}: {used} bytes used, {} reserved",
            reserved / byte
        );
    };
    for v in 0..18u64 {
        for n in names {
            buy(n, t(2 * v), 300 + v as u128);
        }
        check("after buys");
    }
    for n in names {
        sell(n, t(40), balance(n) / 3);
        sell(n, t(40), balance(n) / 2);
        check("after requests");
    }
    settle(t(41));
    check("after settlement");
    for n in names {
        claim(n, t(41));
        check("after a claim");
    }
}

// --- B. Vintages and transfers -------------------------------------------------------------------------------

#[test]
fn b1_tokens_leave_from_the_newest_lots_first() {
    setup(&["a", "b"]);
    buy("a", t(0), 1_000);
    buy("a", t(2), 1_000);
    let before = lots("a");
    send("a", "b", t(3), before[1].1 + 5);
    assert_eq!(lots("b"), vec![(0, 5), (1, before[1].1)]);
    assert_eq!(lots("a"), vec![(0, before[0].1 - 5)]);
}

#[test]
fn b2_received_tokens_keep_their_vintages_and_merge_with_equal_ones() {
    setup(&["a", "b"]);
    buy("a", t(0), 1_000);
    buy("b", t(0), 1_000);
    buy("b", t(2), 1_000);
    let b0 = lots("b")[0].1;
    send("a", "b", t(3), 100);
    assert_eq!(lots("b")[0], (0, b0 + 100));
    assert_eq!(lots("b").len(), 2);
}

#[test]
fn b3_the_two_oldest_lots_merge_into_the_older_vintage() {
    setup(&["a"]);
    let mut bought = vec![];
    for v in 0..5u64 {
        bought.push(buy("a", t(2 * v), 500));
    }
    let l = lots("a");
    assert_eq!(l.len(), 3);
    assert_eq!(
        l[0],
        (0, bought[0] + bought[1] + bought[2]),
        "merged into vintage 0, never newer"
    );
    assert_eq!(&l[1..], &[(3, bought[3]), (4, bought[4])]);
}

fn transfer_call(from: &str, to: &str, now: u64, amount: u128, answer: PromiseResult) -> u128 {
    transfer_call_moving(from, to, now, amount, &[], answer)
}

/// `ft_transfer_call`, then the receiver's own transfers (as from `ft_on_transfer`), then the resolve callback.
fn transfer_call_moving(
    from: &str,
    to: &str,
    now: u64,
    amount: u128,
    moves: &[(&str, u128)],
    answer: PromiseResult,
) -> u128 {
    call(&acct(from), now, 1, |c| {
        let _ = c.ft_transfer_call(acct(to), U128(amount), None, String::new());
    })
    .unwrap();
    let oldest = events("transfer")[0]["lots"]
        .as_array()
        .unwrap()
        .iter()
        .map(|l| l[0].as_u64().unwrap())
        .min()
        .unwrap();
    for &(third, moved) in moves {
        send(to, third, now, moved);
    }
    let used = call_with(&contract_id(), now, 0, vec![answer], |c| {
        c.ft_resolve_transfer(acct(from), acct(to), U128(amount), oldest)
    })
    .unwrap();
    amount - used.0
}

fn unused(n: u128) -> PromiseResult {
    PromiseResult::Successful(near_sdk::serde_json::to_vec(&U128(n)).unwrap())
}

/// Epochs and vintages of 10 ns from a launch at 0: the parameters of the review's sequence.
fn setup_tens(names: &[&str]) {
    quiet();
    let mut p = small();
    p.vintage_length = U64(10);
    p.launch_time = U64(0);
    deploy(p);
    for n in names {
        register(n, 10);
    }
}

#[test]
fn b4_a_refund_after_the_receiver_moved_the_tokens_turns_no_old_token_into_a_newer_vintage() {
    setup_tens(&["receiver", "sender", "third"]);
    assert_eq!(buy("receiver", 0, 1_000), 90_909);
    assert_eq!(buy("sender", 10, 1_000), 75_757);
    let back = transfer_call_moving(
        "sender",
        "receiver",
        10,
        75_757,
        &[("third", 75_757)],
        unused(75_757),
    );
    assert_eq!(back, 75_757);
    assert_eq!(lots("third"), vec![(1, 75_757)]);
    assert_eq!(
        lots("sender"),
        vec![(0, 75_757)],
        "the refund is vintage-0 tokens and stays vintage 0"
    );
    assert_eq!(lots("receiver"), vec![(0, 90_909 - 75_757)]);
}

#[test]
fn b4_old_tokens_sent_to_a_pool_that_refunds_come_back_old_and_leave_the_pools_newer_lots() {
    setup_tens(&["a", "exchange", "b"]);
    buy("a", 0, 1_000);
    buy("exchange", 50, 5_000);
    let stock = balance("exchange");
    assert_eq!(
        transfer_call("a", "exchange", 50, 1_000, unused(1_000)),
        1_000
    );
    assert_eq!(lots("a"), vec![(0, balance("a"))]);
    assert_eq!(lots("exchange"), vec![(5, stock)]);
    let back = transfer_call_moving("a", "exchange", 50, 1_000, &[("b", 1_000)], unused(1_000));
    assert_eq!(back, 1_000);
    assert_eq!(
        lots("a"),
        vec![(0, balance("a"))],
        "never the pool's vintage 5"
    );
    assert_eq!(lots("b"), vec![(5, 1_000)]);
    assert_eq!(lots("exchange"), vec![(5, stock - 1_000)]);
}

#[test]
fn b4_a_refund_out_of_lots_newer_than_the_oldest_sent_gets_the_oldest_vintage_sent() {
    // the receiver passes the tokens on and buys newer ones of its own before the refund arrives
    setup_tens(&["a", "receiver", "b"]);
    buy("a", 0, 1_000);
    call(&acct("a"), 50, 1, |c| {
        let _ = c.ft_transfer_call(acct("receiver"), U128(500), None, String::new());
    })
    .unwrap();
    send("receiver", "b", 50, 500);
    buy("receiver", 70, 1_000);
    assert_eq!(lots("receiver")[0].0, 7);
    let used = call_with(&contract_id(), 70, 0, vec![unused(500)], |c| {
        c.ft_resolve_transfer(acct("a"), acct("receiver"), U128(500), 0)
    })
    .unwrap();
    assert_eq!(used.0, 0);
    assert_eq!(
        lots("a"),
        vec![(0, balance("a"))],
        "vintage-7 tokens come back as vintage 0"
    );
}

#[test]
fn vintages_above_u32_max_do_not_wrap() {
    quiet();
    let mut p = small();
    p.epoch_length = U64(1);
    p.vintage_length = U64(1);
    p.launch_time = U64(0);
    deploy(p);
    register("a", 10);
    register("b", 10);
    let late = u32::MAX as u64 + 6;
    buy("a", 5, 1_000);
    buy("b", late, 1_000);
    assert_eq!(lots("b")[0].0, late, "vintage 2^32 + 5, not 5");
    send("b", "a", late, 1_000);
    assert_eq!(
        lots("a").iter().map(|l| l.0).collect::<Vec<_>>(),
        vec![5, late]
    );
}

#[test]
fn b4_transfer_call_refund_returns_with_the_oldest_vintage_sent() {
    setup(&["a", "dex"]);
    buy("a", t(0), 1_000);
    buy("a", t(2), 1_000);
    buy("a", t(4), 1_000);
    let sent = lots("a")[2].1 + lots("a")[1].1 + 1; // vintages 2, 1 and one unit of 0
    let unused = near_sdk::serde_json::to_vec(&U128(sent - 1)).unwrap();
    assert_eq!(
        transfer_call("a", "dex", t(5), sent, PromiseResult::Successful(unused)),
        sent - 1
    );
    assert_eq!(
        lots("a").last().unwrap().0,
        0,
        "the refund carries vintage 0, the oldest sent"
    );
    assert_eq!(lots("a").len(), 1);
    assert_eq!(balance("dex"), 1);
    assert_eq!(
        transfer_call("a", "dex", t(5), 10, PromiseResult::Failed),
        10,
        "a failed receiver refunds all"
    );
}

#[test]
fn b5_no_path_gives_tokens_a_newer_vintage() {
    setup(&["a", "b"]);
    for v in 0..4u64 {
        buy("a", t(2 * v), 700);
    }
    send("a", "b", t(8), balance("a") / 2);
    sell("b", t(9), balance("b"));
    buy("b", t(20), 10_000); // vintage 10; this call first settles the request and claims it for b
    let claimed = events("claim");
    assert_eq!(claimed.len(), 1);
    let returned: Vec<u64> = claimed[0]["returned"]
        .as_array()
        .unwrap()
        .iter()
        .map(|r| r[0].as_u64().unwrap())
        .collect();
    assert!(
        !returned.is_empty(),
        "the gate bound, so part of the request came back"
    );
    assert!(
        returned.iter().all(|&v| v <= 3),
        "unfilled tokens return with their own vintages: {returned:?}"
    );
    let newest_old = lots("b").iter().filter(|l| l.0 < 10).map(|l| l.0).max();
    assert!(newest_old <= Some(3));
}

#[test]
fn b6_splitting_across_accounts_changes_the_payout_by_rounding_only() {
    let run = |split: bool| -> u128 {
        setup(&["a", "b", "c", "x"]);
        buy("x", t(0), 9_000);
        buy("a", t(2), 3_000);
        let whole = balance("a");
        if split {
            send("a", "b", t(3), whole / 3);
            send("a", "c", t(3), whole / 3);
        }
        sell("x", t(4), balance("x"));
        for who in ["a", "b", "c"] {
            if balance(who) > 0 {
                sell(who, t(4), balance(who));
            }
        }
        ["a", "b", "c"]
            .iter()
            .map(|w| call(&acct(w), t(5), 0, |c| c.claim()).map_or(0, |c| paid(&c)))
            .sum()
    };
    let (one, three) = (run(false), run(true));
    assert!(
        one.abs_diff(three) <= 3,
        "one account {one}, three accounts {three}"
    );
}

// --- C. Requests, the gate and settlement --------------------------------------------------------------------

#[test]
fn c1_a_request_locks_tokens_newest_first_for_the_current_epoch() {
    setup(&["a"]);
    buy("a", t(0), 1_000);
    buy("a", t(2), 1_000);
    let l = lots("a");
    let id = sell("a", t(3), l[1].1 + 7);
    let acc = view(|c| c.get_account(acct("a"))).unwrap();
    assert_eq!(acc.pending[0].id.0, id);
    assert_eq!(acc.pending[0].epoch.0, 3);
    assert_eq!(
        acc.pending[0].portions,
        vec![(1, U128(l[1].1), U128(0)), (0, U128(7), U128(0))]
    );
    assert_eq!(lots("a"), vec![(0, l[0].1 - 7)]);
    assert_eq!(
        view(|c| c.get_open().requested),
        vec![(0, U128(7)), (1, U128(l[1].1))]
    );
    assert_eq!(
        view(|c| c.ft_total_supply().0),
        l[0].1 - 7,
        "locked tokens are out of circulation"
    );
    assert_eq!(refused("a", t(3), 0, |c| c.request_sell(U128(0))), E_ZERO);
    assert_eq!(
        refused("a", t(3), 0, |c| c.request_sell(U128(10u128.pow(9)))),
        E_BALANCE
    );
}

#[test]
fn c2_an_epoch_settles_after_it_ends_on_the_pool_at_its_end() {
    setup(&["a", "b"]);
    buy("a", t(0), 10_000);
    sell("a", t(1), 50_000);
    buy("b", t(1) + 9, 2_000); // same epoch: the settlement must see this buy
    assert_eq!(settle(t(1) + 9), 0, "epoch 1 is not over");
    let (x, y, r, _) = pool();
    buy("b", t(2), 1); // any call in epoch 2 settles epoch 1 first, before its own effect
    let s = view(|c| c.get_settlement(U64(1))).unwrap();
    assert_eq!(s.cap.0, r * 1000 / 10_000);
    assert_eq!(
        s.paid.0,
        after_fee(sell_gross(u(x), u(y), s.total_filled.0), 100)
    );
    assert_eq!(settle(t(3)), 0, "nothing left to settle");
}

#[test]
fn c3_gate_fill_is_the_largest_amount_under_the_cap() {
    let mut seed = 0x2545F4914F6CDD1Du64;
    let mut next = |m: u128| {
        seed ^= seed << 13;
        seed ^= seed >> 7;
        seed ^= seed << 17;
        (seed as u128 * 0x9E37_79B9_7F4A_7C15) % m
    };
    let paid = |x: u128, y: u128, f: u128, fee: u128| after_fee(sell_gross(u(x), u(y), f), fee);
    let mut checked = 0;
    for _ in 0..20_000 {
        let ex = next(27) as u32;
        let x = 1 + next(10u128.pow(1 + ex));
        let ey = next(27) as u32;
        let y = 1 + next(10u128.pow(1 + ey));
        let cap = next(x);
        let fi = next(6) as usize;
        let fee = [0, 1, 50, 100, 333, 9_999][fi];
        match gate_fill(u(x), u(y), cap, fee) {
            Some(f) if f < u(1u128 << 100) => {
                let f = f.as_u128();
                assert!(
                    paid(x, y, f, fee) <= cap,
                    "F pays at most the cap: x {x} y {y} cap {cap} fee {fee}"
                );
                assert!(
                    paid(x, y, f + 1, fee) > cap,
                    "F + 1 pays more: x {x} y {y} cap {cap} fee {fee}"
                );
                checked += 1;
            }
            Some(_) => {}
            None => assert!(
                paid(x, y, 1u128 << 100, fee) <= cap,
                "no amount exceeds the cap"
            ),
        }
    }
    assert!(checked > 10_000, "{checked}");
}

#[test]
fn c4_a_calm_epoch_fills_every_request() {
    setup(&["a", "b"]);
    buy("a", t(0), 10_000);
    buy("b", t(2), 1_000);
    sell("a", t(3), 100);
    sell("b", t(3), 200);
    settle(t(4));
    let s = view(|c| c.get_settlement(U64(3))).unwrap();
    assert!(!s.bound);
    assert_eq!(s.total_filled.0, 300);
    assert!(claim("a", t(4))[0].returned.is_empty() && claim("b", t(4))[0].returned.is_empty());
}

#[test]
fn c5_newer_vintage_filled_before_older() {
    setup(&["old", "mid", "new"]);
    buy("old", t(0), 10_000);
    buy("mid", t(2), 2_000);
    buy("new", t(4), 1_000);
    for who in ["old", "mid", "new"] {
        sell(who, t(5), balance(who));
    }
    settle(t(6));
    let s = view(|c| c.get_settlement(U64(5))).unwrap();
    assert!(s.bound);
    let f: Vec<(u64, u128)> = s.filled.iter().map(|(v, a)| (*v, a.0)).collect();
    let r: Vec<(u64, u128)> = s.requested.iter().map(|(v, a)| (*v, a.0)).collect();
    assert_eq!(f[2].1, r[2].1, "the newest vintage is filled in full");
    assert!(
        f[1].1 > 0 && f[1].1 < r[1].1,
        "the boundary vintage is filled in part"
    );
    assert_eq!(f[0].1, 0, "the oldest gets nothing this epoch");
    assert_eq!(
        f.iter().map(|x| x.1).sum::<u128>(),
        s.max_fill.unwrap().parse::<u128>().unwrap()
    );
}

#[test]
fn c5_the_boundary_vintage_is_shared_pro_rata_and_adds_up_exactly() {
    setup(&["x", "a", "b", "c"]);
    buy("x", t(0), 2_000);
    buy("a", t(2), 7_001);
    send("a", "b", t(2), 100_003);
    send("a", "c", t(2), 33_337);
    for who in ["a", "b", "c"] {
        sell(who, t(3), balance(who));
    }
    settle(t(4));
    let s = view(|c| c.get_settlement(U64(3))).unwrap();
    let (fill, req) = (s.filled[0].1 .0, s.requested[0].1 .0);
    assert!(fill < req);
    let mut sum = 0;
    for who in ["a", "b", "c"] {
        let mine = view(|c| c.get_account(acct(who))).unwrap().pending[0].portions[0]
            .1
             .0;
        let got = claim(who, t(4))[0].filled.0;
        let share = mine * fill / req;
        assert!(
            got == share || got == share + 1,
            "{who}: {got} against a pro rata share of {share}"
        );
        sum += got;
    }
    assert_eq!(sum, fill);
}

#[test]
fn c6_every_filled_token_gets_the_same_price() {
    setup(&["x", "a", "b"]);
    buy("x", t(0), 10_000);
    buy("a", t(2), 1_500);
    buy("b", t(2), 900);
    for who in ["x", "a", "b"] {
        sell(who, t(3), balance(who));
    }
    settle(t(4));
    let s = view(|c| c.get_settlement(U64(3))).unwrap();
    for who in ["x", "a", "b"] {
        let c = &claim(who, t(4))[0];
        assert_eq!(c.paid.0, c.filled.0 * s.paid.0 / s.total_filled.0);
    }
    assert!(
        view(|c| c.get_settlement(U64(3))).is_none(),
        "the record goes once every request is claimed"
    );
    assert_eq!(
        view(|c| c.get_pool().unclaimed_near.0),
        0,
        "rounding dust went back to the reserve"
    );
}

#[test]
fn c7_unfilled_tokens_come_back_with_their_vintage_and_no_place_in_line() {
    setup(&["old", "new"]);
    buy("old", t(0), 10_000);
    sell("old", t(3), balance("old"));
    let back = &claim("old", t(4))[0].returned;
    assert_eq!(back.len(), 1);
    assert_eq!(back[0].0, 0, "returned with vintage 0");
    buy("new", t(4), 500);
    sell("old", t(5), balance("old"));
    sell("new", t(5), balance("new"));
    settle(t(6));
    let s = view(|c| c.get_settlement(U64(5))).unwrap();
    assert_eq!(
        s.filled[1].1, s.requested[1].1,
        "the new buyer goes first although the old one asked earlier"
    );
}

#[test]
fn c8_settlement_cost_does_not_grow_with_the_number_of_sellers() {
    let gas = |sellers: usize, vintages: u64| -> u64 {
        quiet();
        deploy(Params {
            max_lots: 16,
            ..small()
        });
        let names: Vec<String> = (0..sellers).map(|i| format!("s{i}")).collect();
        for (i, n) in names.iter().enumerate() {
            register(n, 1);
            buy(n, t(2 * (i as u64 * vintages / sellers as u64)), 50);
        }
        let now = t(2 * vintages + 1);
        for n in &names {
            sell(n, now, balance(n));
        }
        call(&acct("keeper"), now + 10, 0, |c| {
            c.settle();
            env::used_gas().as_gas()
        })
        .unwrap()
    };
    // The mocked host gas grows with the depth of the storage trie (about log of the number of keys), whatever the
    // contract does; a loop over requests would make 200 sellers cost far more than 1. Measured: +3 % for 200x.
    let (one, many) = (gas(1, 1), gas(200, 1));
    assert!(
        many <= one + one / 20,
        "1 seller: {one} gas, 200 sellers: {many} gas"
    );
    let (v4, v16) = (gas(16, 4), gas(16, 16));
    assert!(
        v16 > v4,
        "work grows with vintages: {v4} for 4, {v16} for 16"
    );
    eprintln!("mocked host gas of settle: 1 seller {one}, 200 sellers {many}; 4 vintages {v4}, 16 vintages {v16}");
}

#[test]
fn c9_a_claim_pays_once() {
    setup(&["a"]);
    buy("a", t(0), 10_000);
    sell("a", t(1), 1_000);
    assert_eq!(
        refused("a", t(1), 0, |c| c.claim()),
        E_NOTHING,
        "not settled yet"
    );
    let first = claim("a", t(2));
    assert_eq!(first.len(), 1);
    assert_eq!(refused("a", t(2), 0, |c| c.claim()), E_NOTHING);
    assert_eq!(
        view(|c| c.get_account(acct("a"))).unwrap().paid_out.0,
        first[0].paid.0
    );
}

#[test]
fn c9_the_owner_is_paid_on_its_next_call_too() {
    setup(&["a", "b"]);
    buy("a", t(0), 10_000);
    buy("b", t(0), 10_000);
    sell("a", t(1), 1_000);
    send("a", "b", t(2), 5);
    let paid_out = view(|c| c.get_account(acct("a"))).unwrap().paid_out.0;
    assert!(paid_out > 0);
    assert!(view(|c| c.get_account(acct("a")))
        .unwrap()
        .pending
        .is_empty());
}

// --- D. Views and events -------------------------------------------------------------------------------------

#[test]
fn d1_views_show_pool_epoch_requests_settlements_and_accounts() {
    setup(&["a"]);
    buy("a", t(0), 10_000);
    sell("a", t(3), 1_000);
    let p = view(|c| c.get_pool());
    assert_eq!(
        (p.x.0, p.y.0, p.k.as_str()),
        (20_000, 500_000, "10000000000")
    );
    assert_eq!(p.spot_price.0, 20_000 * 10u128.pow(18) / 500_000);
    let o = view(|c| c.get_open());
    assert_eq!((o.epoch, o.requests), (Some(U64(3)), 1));
    settle(t(4));
    let s = view(|c| c.get_settlement(U64(3))).unwrap();
    assert_eq!(s.price.0, s.paid.0 * 10u128.pow(18) / s.total_filled.0);
    assert_eq!(view(|c| c.get_account(acct("a"))).unwrap().pending.len(), 1);
    assert_eq!(view(|c| c.ft_metadata()).decimals, 18);
}

/// Rebuilds every account's lots from the `lifo` events alone and compares them with the view.
#[test]
fn d2_events_rebuild_every_account_lots() {
    setup(&["a", "b", "c"]);
    let mut log: Vec<Value> = vec![];
    let keep = |log: &mut Vec<Value>| {
        log.extend(
            near_sdk::test_utils::get_logs()
                .iter()
                .filter_map(|l| l.strip_prefix("EVENT_JSON:"))
                .map(|l| near_sdk::serde_json::from_str::<Value>(l).unwrap()),
        )
    };
    let script: Vec<(&str, u64)> = vec![("a", 0), ("b", 2), ("c", 4), ("a", 6), ("b", 8)];
    for (who, e) in &script {
        buy(who, t(*e), 2_000);
        keep(&mut log);
    }
    send("a", "b", t(9), balance("a") / 2);
    keep(&mut log);
    for who in ["a", "b", "c"] {
        sell(who, t(10), balance(who) * 2 / 3);
        keep(&mut log);
    }
    for who in ["c", "b", "a"] {
        claim(who, t(11));
        keep(&mut log);
    }
    let mut rebuilt: std::collections::BTreeMap<String, Vec<(u64, u128)>> = Default::default();
    let n = |v: &Value| v.as_str().unwrap().parse::<u128>().unwrap();
    let add = |l: &mut Vec<(u64, u128)>, v: u64, a: u128| {
        let i = l.partition_point(|x| x.0 < v);
        if i < l.len() && l[i].0 == v {
            l[i].1 += a
        } else {
            l.insert(i, (v, a))
        }
        while l.len() > 3 {
            let (_, x) = l.remove(1);
            l[0].1 += x;
        }
    };
    let sub = |l: &mut Vec<(u64, u128)>, v: u64, a: u128| {
        let i = l.iter().position(|x| x.0 == v).unwrap();
        l[i].1 -= a;
        if l[i].1 == 0 {
            l.remove(i);
        }
    };
    for e in log.iter().filter(|e| e["standard"] == "lifo") {
        let d = &e["data"][0];
        match e["event"].as_str().unwrap() {
            "buy" => add(
                rebuilt.entry(s_(d, "account_id")).or_default(),
                d["vintage"].as_u64().unwrap(),
                n(&d["tokens"]),
            ),
            "sell_request" => {
                for p in d["portions"].as_array().unwrap() {
                    sub(
                        rebuilt.get_mut(&s_(d, "account_id")).unwrap(),
                        p[0].as_u64().unwrap(),
                        n(&p[1]),
                    );
                }
            }
            "transfer" => {
                for p in d["lots"].as_array().unwrap() {
                    let (v, a) = (p[0].as_u64().unwrap(), n(&p[1]));
                    sub(rebuilt.get_mut(&s_(d, "old_owner_id")).unwrap(), v, a);
                    add(rebuilt.entry(s_(d, "new_owner_id")).or_default(), v, a);
                }
            }
            "claim" => {
                for p in d["returned"].as_array().unwrap() {
                    add(
                        rebuilt.get_mut(&s_(d, "account_id")).unwrap(),
                        p[0].as_u64().unwrap(),
                        n(&p[1]),
                    );
                }
            }
            _ => {}
        }
    }
    for who in ["a", "b", "c"] {
        assert_eq!(rebuilt[&format!("{who}.test")], lots(who), "{who}");
    }
}

fn s_(d: &Value, k: &str) -> String {
    d[k].as_str().unwrap().to_string()
}

// --- Storage (NEP-145) and deposits --------------------------------------------------------------------------

#[test]
fn storage_a_request_must_be_covered_by_the_storage_balance() {
    quiet();
    deploy(small());
    let min = view(|c| c.storage_balance_bounds().min.0);
    call(&acct("a"), 0, min, |c| c.storage_deposit(None, None)).unwrap();
    buy("a", t(0), 10_000);
    assert_eq!(
        refused("a", t(1), 0, |c| c.request_sell(U128(10))),
        E_STORAGE
    );
    let cost = view(|c| c.get_request_storage().0);
    call(&acct("a"), t(1), cost, |c| c.request_sell(U128(10))).unwrap();
    claim("a", t(2));
    assert_eq!(
        view(|c| c.storage_balance_of(acct("a")))
            .unwrap()
            .available
            .0,
        cost,
        "released at claim"
    );
}

#[test]
fn storage_unregister_refuses_an_account_with_tokens() {
    setup(&["a", "b"]);
    buy("a", t(0), 1_000);
    assert_eq!(
        refused("a", t(0), 1, |c| c.storage_unregister(Some(true))),
        E_HOLDS_TOKENS
    );
    assert!(call(&acct("b"), t(0), 1, |c| c.storage_unregister(None)).unwrap());
    assert_eq!(
        refused("a", t(0), 1, |c| c.ft_transfer(acct("b"), U128(1), None)),
        E_NOT_REGISTERED
    );
}

#[test]
fn nep141_transfers_need_one_yocto_and_refuse_self_and_zero() {
    setup(&["a", "b"]);
    buy("a", t(0), 1_000);
    assert_eq!(
        refused("a", t(0), 0, |c| c.ft_transfer(acct("b"), U128(1), None)),
        E_ONE_YOCTO
    );
    assert_eq!(
        refused("a", t(0), 1, |c| c.ft_transfer(acct("a"), U128(1), None)),
        E_SELF
    );
    assert_eq!(
        refused("a", t(0), 1, |c| c.ft_transfer(acct("b"), U128(0), None)),
        E_ZERO
    );
    assert_eq!(
        refused("a", t(0), 1, |c| c.ft_transfer(
            acct("b"),
            U128(10u128.pow(9)),
            None
        )),
        E_BALANCE
    );
    assert_eq!(view(|c| c.ft_balance_of(acct("a")).0), balance("a"));
    assert_eq!(refused("a", 99, 1, |c| c.buy()), E_NOT_LAUNCHED);
}
