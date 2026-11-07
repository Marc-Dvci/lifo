//! A small harness: each call runs on the mocked blockchain like a transaction. The state is read from storage,
//! the method runs, the state is written back. A panic restores the storage snapshot taken before the call, the way
//! the chain reverts a failed receipt, and returns the panic message.

#![allow(dead_code)]

use lifo::{Lifo, Params};
use near_sdk::json_types::{U128, U64};
use near_sdk::mock::with_mocked_blockchain;
use near_sdk::serde_json::Value;
use near_sdk::test_utils::{get_logs, VMContextBuilder};
use near_sdk::{
    env, test_vm_config, AccountId, Gas, MockedBlockchain, NearToken, PromiseResult,
    RuntimeFeesConfig,
};
use std::collections::HashMap;
use std::panic::{catch_unwind, AssertUnwindSafe};

pub const NEAR: u128 = 10u128.pow(24);
pub const TOKEN: u128 = 10u128.pow(18);
pub const MINUTE: u64 = 60_000_000_000;
pub const HOUR: u64 = 60 * MINUTE;
pub const DAY: u64 = 24 * HOUR;
pub const LAUNCH: u64 = 1_800_000_000_000_000_000;

pub fn acct(name: &str) -> AccountId {
    format!("{name}.test").parse().unwrap()
}

pub fn contract_id() -> AccountId {
    "lifo.test".parse().unwrap()
}

fn install(
    who: &AccountId,
    now: u64,
    deposit: u128,
    results: Vec<PromiseResult>,
    storage: HashMap<Vec<u8>, Vec<u8>>,
) {
    let ctx = VMContextBuilder::new()
        .current_account_id(contract_id())
        .predecessor_account_id(who.clone())
        .signer_account_id(who.clone())
        .block_timestamp(now)
        .attached_deposit(NearToken::from_yoctonear(deposit))
        .prepaid_gas(Gas::from_tgas(300))
        .account_balance(NearToken::from_near(1_000_000))
        .build();
    env::set_blockchain_interface(MockedBlockchain::new(
        ctx,
        test_vm_config(),
        RuntimeFeesConfig::test(),
        results,
        storage,
        Default::default(),
        None,
    ));
}

/// The message of a contract panic, without the host's prefix.
fn message(payload: Box<dyn std::any::Any + Send>) -> String {
    let text = payload
        .downcast_ref::<String>()
        .cloned()
        .or_else(|| payload.downcast_ref::<&str>().map(|s| s.to_string()))
        .unwrap_or_default();
    const MARK: &str = "GuestPanic { panic_msg: \"";
    match text.find(MARK) {
        Some(i) => text[i + MARK.len()..]
            .trim_end_matches(" })")
            .trim_end_matches('"')
            .to_string(),
        None => text,
    }
}

/// Silences the panic printer for contract refusals only: expected refusals would flood the test output.
pub fn quiet() {
    static ONCE: std::sync::Once = std::sync::Once::new();
    ONCE.call_once(|| {
        let default = std::panic::take_hook();
        std::panic::set_hook(Box::new(move |info| {
            if !info.to_string().contains("GuestPanic") {
                default(info)
            }
        }));
    });
}

pub fn call_with<R>(
    who: &AccountId,
    now: u64,
    deposit: u128,
    results: Vec<PromiseResult>,
    f: impl FnOnce(&mut Lifo) -> R,
) -> Result<R, String> {
    let snapshot = with_mocked_blockchain(|b| b.take_storage());
    install(who, now, deposit, results, snapshot.clone());
    let out = catch_unwind(AssertUnwindSafe(|| {
        let mut c: Lifo = env::state_read().expect("contract state");
        let r = f(&mut c);
        env::state_write(&c);
        drop(c);
        r
    }));
    out.map_err(|p| {
        install(who, now, 0, vec![], snapshot);
        message(p)
    })
}

pub fn call<R>(
    who: &AccountId,
    now: u64,
    deposit: u128,
    f: impl FnOnce(&mut Lifo) -> R,
) -> Result<R, String> {
    call_with(who, now, deposit, vec![], f)
}

pub fn view<R>(f: impl FnOnce(&Lifo) -> R) -> R {
    let c: Lifo = env::state_read().expect("contract state");
    f(&c)
}

/// Deploys and initialises the contract on a fresh mocked chain.
pub fn deploy(params: Params) {
    install(
        &contract_id(),
        params.launch_time.0,
        0,
        vec![],
        HashMap::new(),
    );
    let c = Lifo::new(params);
    env::state_write(&c);
    drop(c);
}

/// Registers an account with `near` NEAR of storage balance (enough for many open requests).
pub fn register(name: &str, near: u128) {
    let who = acct(name);
    call(&who, 0, near * NEAR, |c| c.storage_deposit(None, None)).unwrap();
}

/// The `lifo` events logged by the last call.
pub fn events(kind: &str) -> Vec<Value> {
    get_logs()
        .iter()
        .filter_map(|l| l.strip_prefix("EVENT_JSON:"))
        .map(|l| near_sdk::serde_json::from_str::<Value>(l).unwrap())
        .filter(|e| e["standard"] == "lifo" && e["event"] == kind)
        .map(|e| e["data"][0].clone())
        .collect()
}

pub fn small() -> Params {
    Params {
        virtual_near: U128(10_000),
        virtual_tokens: U128(1_000_000),
        max_supply: U128(800_000),
        gate_bps: 1000,
        sell_fee_bps: 100,
        epoch_length: U64(10),
        vintage_length: U64(20),
        launch_time: U64(100),
        max_lots: 3,
    }
}

pub fn launch() -> Params {
    Params {
        virtual_near: U128(30 * NEAR),
        virtual_tokens: U128(1_073_000_000 * TOKEN),
        max_supply: U128(1_073_000_000 * TOKEN * 8 / 10),
        gate_bps: 500,
        sell_fee_bps: 50,
        epoch_length: U64(10 * MINUTE),
        vintage_length: U64(DAY),
        launch_time: U64(LAUNCH),
        max_lots: 16,
    }
}

/// Bytes the contract's storage takes, counted the way NEAR does: key, value and 40 bytes per record.
pub fn storage_bytes() -> u64 {
    let storage = with_mocked_blockchain(|b| b.take_storage());
    let bytes = storage
        .iter()
        .map(|(k, v)| (k.len() + v.len() + 40) as u64)
        .sum();
    install(&contract_id(), 0, 0, vec![], storage);
    bytes
}
