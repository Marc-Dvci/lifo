//! G3: the contract replays every vector the Python model wrote (`vectors/edge.json`, `vectors/random.json`) and
//! agrees on every step: the result or the exact error, then the reserve, the supply, the unclaimed NEAR, every
//! account's lots and payouts, and the open epoch with its requests by vintage. At the end of each case it agrees
//! on every settlement and every account's pending requests.
//!
//! Reader notes (the model is not changed to suit Rust):
//! - Account names get a `.test` suffix, because NEAR account ids have at least two characters.
//! - Every account is registered (NEP-145) before the first step, so a buy's deposit is the model's deposit.
//! - The model's `transfer_call` is two receipts here: `ft_transfer_call`, then `ft_resolve_transfer` in the same
//!   block with the receiver's answer (`unused`) as the promise result.
//! - The contract removes a settled epoch's record once its last request is claimed; its fixed fields are then
//!   checked against the `settle` event, and the model must show no open request and no NEAR left for it.

mod common;

use common::*;
use lifo::ClaimView;
use near_sdk::json_types::U128;
use near_sdk::serde_json::{json, Value};
use near_sdk::PromiseResult;
use std::collections::{BTreeMap, BTreeSet};

fn int(v: &Value) -> u128 {
    match v {
        Value::String(s) => s.parse().unwrap(),
        Value::Number(n) => n.as_u64().unwrap() as u128,
        _ => panic!("not an integer: {v}"),
    }
}

fn s(v: &Value) -> String {
    match v {
        Value::String(s) => s.clone(),
        other => other.to_string(),
    }
}

/// Lists of pairs or claims with every number as a string, so the model's and the contract's JSON compare equal.
fn norm(v: &Value) -> Value {
    match v {
        Value::Array(a) => Value::Array(a.iter().map(norm).collect()),
        Value::Number(n) => Value::String(n.to_string()),
        other => other.clone(),
    }
}

fn claims_json(claims: &[ClaimView]) -> Value {
    Value::Array(
        claims
            .iter()
            .map(|c| {
                let returned: Vec<Value> = c
                    .returned
                    .iter()
                    .map(|(v, a)| json!([v.to_string(), a.0.to_string()]))
                    .collect();
                json!([
                    c.request_id.0.to_string(),
                    c.filled.0.to_string(),
                    c.paid.0.to_string(),
                    returned
                ])
            })
            .collect(),
    )
}

struct Replay {
    name: String,
    accounts: BTreeSet<String>,
    settles: BTreeMap<u64, Value>,
}

impl Replay {
    fn step(&mut self, i: usize, step: &Value) {
        let now = step["now"].as_u64().unwrap();
        let ctx = format!("{} step {i} ({})", self.name, step["op"]);
        let a = |k: &str| acct(step[k].as_str().unwrap());
        let (outcome, logs): (Result<Value, String>, Vec<Value>) = match step["op"]
            .as_str()
            .unwrap()
        {
            "buy" => {
                let r = call(&a("account"), now, int(&step["deposit"]), |c| {
                    c.buy().0.to_string()
                });
                (r.map(Value::String), events("settle"))
            }
            "request_sell" => {
                let amount = U128(int(&step["amount"]));
                let r = call(&a("account"), now, 0, |c| {
                    c.request_sell(amount).0.to_string()
                });
                (r.map(Value::String), events("settle"))
            }
            "transfer" => {
                let (to, amount) = (a("receiver"), U128(int(&step["amount"])));
                let r = call(&a("sender"), now, 1, |c| c.ft_transfer(to, amount, None));
                let settled = events("settle");
                (r.map(|_| norm(&events("transfer")[0]["lots"])), settled)
            }
            "transfer_call" => {
                let (from, to, amount) = (a("sender"), a("receiver"), int(&step["amount"]));
                let r = call(&from, now, 1, |c| {
                    let _ = c.ft_transfer_call(to.clone(), U128(amount), None, String::new());
                });
                let settled = events("settle");
                let r = r.map(|_| {
                    let lots = &events("transfer")[0]["lots"];
                    let oldest = lots
                        .as_array()
                        .unwrap()
                        .iter()
                        .map(|l| l[0].as_u64().unwrap())
                        .min()
                        .unwrap() as u32;
                    let answer = near_sdk::serde_json::to_vec(&U128(int(&step["unused"]))).unwrap();
                    let used = call_with(
                        &contract_id(),
                        now,
                        0,
                        vec![PromiseResult::Successful(answer)],
                        |c| c.ft_resolve_transfer(from.clone(), to.clone(), U128(amount), oldest),
                    )
                    .expect("resolve never panics");
                    Value::String((amount - used.0).to_string())
                });
                (r, settled)
            }
            "claim" => {
                let r = call(&a("account"), now, 0, |c| claims_json(&c.claim()));
                (r, events("settle"))
            }
            "settle" => {
                let r = call(&acct("keeper"), now, 0, |c| c.settle().to_string());
                (r.map(Value::String), events("settle"))
            }
            op => panic!("{ctx}: unknown op {op}"),
        };
        match (&outcome, step.get("error")) {
            (Err(got), Some(want)) => assert_eq!(got, want.as_str().unwrap(), "{ctx}: error"),
            (Err(got), None) => panic!("{ctx}: contract refused with {got:?}, the model accepted"),
            (Ok(_), Some(want)) => {
                panic!("{ctx}: contract accepted, the model refused with {want}")
            }
            (Ok(got), None) => assert_eq!(norm(got), norm(&step["result"]), "{ctx}: result"),
        }
        for e in logs {
            self.settles.insert(int(&e["epoch"]) as u64, e);
        }
        self.state(&ctx, &step["state"]);
    }

    fn state(&self, ctx: &str, want: &Value) {
        let pool = view(|c| c.get_pool());
        assert_eq!(pool.reserve.0, int(&want["reserve"]), "{ctx}: reserve");
        assert_eq!(pool.supply.0, int(&want["supply"]), "{ctx}: supply");
        assert_eq!(
            pool.unclaimed_near.0,
            int(&want["unclaimed_near"]),
            "{ctx}: unclaimed NEAR"
        );
        for name in &self.accounts {
            let got = view(|c| c.get_account(acct(name))).unwrap();
            let lots: Vec<Value> = got
                .lots
                .iter()
                .map(|(v, a)| json!([v.to_string(), a.0.to_string()]))
                .collect();
            let want_lots = norm(want["lots"].get(name).unwrap_or(&json!([])));
            assert_eq!(Value::Array(lots), want_lots, "{ctx}: lots of {name}");
            let paid = want["paid_out"].get(name).map_or(0, int);
            assert_eq!(got.paid_out.0, paid, "{ctx}: paid out to {name}");
        }
        let open = view(|c| c.get_open());
        assert_eq!(
            open.epoch.map(|e| e.0),
            want["open_epoch"].as_u64(),
            "{ctx}: open epoch"
        );
        let requested: BTreeMap<String, String> = open
            .requested
            .iter()
            .map(|(v, a)| (v.to_string(), a.0.to_string()))
            .collect();
        let want_requested: BTreeMap<String, String> = want["open_requested"]
            .as_object()
            .unwrap()
            .iter()
            .map(|(k, v)| (k.clone(), s(v)))
            .collect();
        assert_eq!(requested, want_requested, "{ctx}: open requests by vintage");
    }

    fn finish(&self, want: &Value) {
        let ctx = format!("{} final", self.name);
        let model: &SerdeMap = want["settlements"].as_object().unwrap();
        let epochs: BTreeSet<u64> = model.keys().map(|k| k.parse().unwrap()).collect();
        assert_eq!(
            epochs,
            self.settles.keys().copied().collect(),
            "{ctx}: settled epochs"
        );
        for (k, m) in model {
            let e: u64 = k.parse().unwrap();
            let ev = &self.settles[&e];
            assert_eq!(s(&ev["cap"]), s(&m["cap"]), "{ctx}: cap of {e}");
            assert_eq!(ev["max_fill"], m["max_fill"], "{ctx}: max fill of {e}");
            let filled: BTreeMap<String, String> = ev["filled"]
                .as_array()
                .unwrap()
                .iter()
                .map(|p| (s(&p[0]), s(&p[1])))
                .collect();
            let want_filled: BTreeMap<String, String> = m["filled"]
                .as_object()
                .unwrap()
                .iter()
                .map(|(k, v)| (k.clone(), s(v)))
                .collect();
            assert_eq!(filled, want_filled, "{ctx}: filled by vintage in {e}");
            assert_eq!(
                s(&ev["total_filled"]),
                s(&m["total_filled"]),
                "{ctx}: total filled in {e}"
            );
            assert_eq!(s(&ev["paid"]), s(&m["paid"]), "{ctx}: paid in {e}");
            assert_eq!(ev["bound"], m["bound"], "{ctx}: bound in {e}");
            match view(|c| c.get_settlement(e.into())) {
                Some(live) => {
                    assert_eq!(
                        live.open_requests as u64,
                        m["open_requests"].as_u64().unwrap(),
                        "{ctx}: open in {e}"
                    );
                    assert_eq!(
                        live.paid_left.0,
                        int(&m["paid_left"]),
                        "{ctx}: NEAR left in {e}"
                    );
                }
                None => {
                    assert_eq!(
                        m["open_requests"], 0,
                        "{ctx}: epoch {e} removed with requests open"
                    );
                    assert_eq!(
                        s(&m["paid_left"]),
                        "0",
                        "{ctx}: epoch {e} removed with NEAR left"
                    );
                }
            }
        }
        for name in &self.accounts {
            let got: Vec<u64> = view(|c| c.get_account(acct(name)))
                .unwrap()
                .pending
                .iter()
                .map(|r| r.id.0)
                .collect();
            let want_ids: Vec<u64> = want["pending"].get(name).map_or(vec![], |ids| {
                ids.as_array()
                    .unwrap()
                    .iter()
                    .map(|i| i.as_u64().unwrap())
                    .collect()
            });
            assert_eq!(got, want_ids, "{ctx}: pending requests of {name}");
        }
    }
}

type SerdeMap = near_sdk::serde_json::Map<String, Value>;

fn params_of(p: &Value) -> lifo::Params {
    for option in ["buy_fee_bps", "young_age", "young_fee_bps", "min_age"] {
        assert_eq!(
            p[option], "0",
            "the contract leaves out {option}: a vector with it on cannot be replayed"
        );
    }
    let n = |k: &str| int(&p[k]);
    lifo::Params {
        virtual_near: U128(n("virtual_near")),
        virtual_tokens: U128(n("virtual_tokens")),
        max_supply: U128(n("max_supply")),
        gate_bps: n("gate_bps") as u32,
        sell_fee_bps: n("sell_fee_bps") as u32,
        epoch_length: (n("epoch_length") as u64).into(),
        vintage_length: (n("vintage_length") as u64).into(),
        launch_time: (n("launch_time") as u64).into(),
        max_lots: n("max_lots") as u32,
    }
}

fn replay_file(file: &str) -> (usize, usize) {
    let path = format!("{}/../vectors/{file}", env!("CARGO_MANIFEST_DIR"));
    let text = std::fs::read_to_string(&path).unwrap_or_else(|e| panic!("{path}: {e}"));
    let doc: Value = near_sdk::serde_json::from_str(&text).unwrap();
    let cases = doc["cases"].as_array().unwrap();
    let mut steps = 0;
    quiet();
    for case in cases {
        deploy(params_of(&case["params"]));
        let mut accounts = BTreeSet::new();
        for step in case["steps"].as_array().unwrap() {
            for k in ["account", "sender", "receiver"] {
                if let Some(name) = step.get(k).and_then(|v| v.as_str()) {
                    accounts.insert(name.to_string());
                }
            }
        }
        for name in &accounts {
            register(name, 100);
        }
        let mut r = Replay {
            name: s(&case["name"]),
            accounts,
            settles: BTreeMap::new(),
        };
        for (i, step) in case["steps"].as_array().unwrap().iter().enumerate() {
            r.step(i, step);
            steps += 1;
        }
        r.finish(&case["final"]);
    }
    (cases.len(), steps)
}

#[test]
fn g3_contract_replays_every_edge_vector() {
    let (cases, steps) = replay_file("edge.json");
    assert_eq!(cases, 8);
    eprintln!("edge.json: {cases} cases, {steps} steps replayed");
}

#[test]
fn g3_contract_replays_every_random_vector() {
    let (cases, steps) = replay_file("random.json");
    assert_eq!(cases, 200);
    eprintln!("random.json: {cases} cases, {steps} steps replayed");
}
