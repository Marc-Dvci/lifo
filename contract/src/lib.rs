//! LIFO: a memecoin whose reserve pays the newest buyers first in a crash.
//!
//! The token is sold and bought back by its own constant-product pool. Buys execute at once. Sells are requests,
//! batched per epoch and settled through a gate: one epoch can pay out at most `GATE_BPS` of the reserve. When more is
//! asked, the newest vintages are filled first and the oldest wait. In calm markets every request is filled in full.
//!
//! The rules in plain words are `RULES.md`. The Python reference model is `lifo/model.py`, and the tests in
//! `contract/tests/` replay its vectors (`vectors/*.json`) and must agree on every step.
//!
//! What this contract implements: requirements A1 to A5, B1 to B6, C1 to C9, D1 and D2, with the launch rules of
//! version 2 (S5'). The three options of version 2 (A6 buy fee, C10 young-token fee, C11 minimum age) launch at 0,
//! where they have no effect, and are left out of this contract: there is no buy fee, no young-token fee and no
//! minimum age, and the parameters do not include them.
//!
//! There is no owner, no admin method, no upgrade method and no method that changes a parameter. `new` can run once,
//! and only from the contract account itself (in the deploy transaction).
//!
//! Rounding: every division rounds down unless noted; every rounding favours the reserve. Products go through a
//! 256-bit integer (`uint::construct_uint!`, see `math.rs`).
//!
//! Storage (NEP-145): an account pays for its own record when it registers (or on its first buy, from the deposit),
//! and each open sell request reserves its worst-case storage from the owner's storage balance until it is claimed.

pub mod math;

use math::{after_fee, buy_out, gate_fill, mul_div, sell_gross, u, BPS, U256};
use near_sdk::json_types::{U128, U64};
use near_sdk::serde_json::{json, Value};
use near_sdk::store::LookupMap;
use near_sdk::{
    env, ext_contract, near, AccountId, Gas, NearToken, PanicOnDefault, Promise, PromiseOrValue,
};

// Stable error messages. The first group is shared with the model (`lifo/model.py`); tests assert on all of them.
pub const E_PARAMS: &str = "invalid parameters";
pub const E_NOT_LAUNCHED: &str = "not launched";
pub const E_TIME: &str = "time went backwards";
pub const E_ZERO: &str = "zero amount";
pub const E_BUY_TOO_SMALL: &str = "buy too small";
pub const E_MAX_SUPPLY: &str = "max supply reached";
pub const E_BALANCE: &str = "not enough tokens";
pub const E_SELF: &str = "sender is receiver";
pub const E_NOTHING: &str = "nothing to claim";
// Contract only: storage, deposits and gas.
pub const E_NOT_REGISTERED: &str = "account not registered";
pub const E_STORAGE: &str = "storage not covered";
pub const E_STORAGE_BALANCE: &str = "not enough storage balance";
pub const E_HOLDS_TOKENS: &str = "account holds tokens";
pub const E_PENDING: &str = "pending requests";
pub const E_ONE_YOCTO: &str = "attach exactly 1 yoctoNEAR";
pub const E_GAS: &str = "attach more gas";
pub const E_CONTRACT_ACCOUNT: &str = "contract cannot hold tokens";

/// Largest `max_lots` accepted at initialisation: it bounds the gas of a transfer and the storage of an account.
pub const MAX_LOTS_LIMIT: u32 = 64;
/// Worst-case storage of an account record: key, lots (20 bytes each), pending ids, payout and storage balance.
pub fn account_bytes(max_lots: u32) -> u64 {
    200 + 20 * max_lots as u64
}
/// Worst-case storage of one open sell request: its record and id (36 bytes per portion), its share of the epoch's
/// per-vintage aggregates (open, requested and filled: 60 bytes per portion) and a whole settlement header.
pub fn request_bytes(max_lots: u32) -> u64 {
    320 + 96 * max_lots as u64
}

const GAS_RESOLVE: Gas = Gas::from_tgas(20);
const GAS_TRANSFER_CALL_MIN: Gas = Gas::from_tgas(50);
const TOKEN_DECIMALS: u8 = 18;

/// Every parameter, fixed at initialisation (A1). Times are nanoseconds; NEAR is yoctoNEAR; tokens are the smallest
/// unit (18 decimals).
#[near(serializers = [borsh, json])]
#[derive(Clone, Debug, PartialEq)]
pub struct Params {
    pub virtual_near: U128,
    pub virtual_tokens: U128,
    pub max_supply: U128,
    pub gate_bps: u32,
    pub sell_fee_bps: u32,
    pub epoch_length: U64,
    pub vintage_length: U64,
    pub launch_time: U64,
    pub max_lots: u32,
}

impl Params {
    fn valid(&self) -> bool {
        let (vn, vt, ms) = (
            self.virtual_near.0,
            self.virtual_tokens.0,
            self.max_supply.0,
        );
        let (el, vl) = (self.epoch_length.0, self.vintage_length.0);
        vn > 0
            && 0 < ms
            && ms < vt
            && 0 < self.gate_bps
            && self.gate_bps as u128 <= BPS
            && (self.sell_fee_bps as u128) < BPS
            && el > 0
            && vl > 0
            && vl % el == 0
            && (1..=MAX_LOTS_LIMIT).contains(&self.max_lots)
    }
}

/// An account: its lots `(vintage, amount)` ascending by vintage, its unclaimed requests, what it was paid in total
/// and its NEP-145 storage balance.
#[near(serializers = [borsh])]
#[derive(Clone, Default)]
pub struct Account {
    pub lots: Vec<(u32, u128)>,
    pub pending: Vec<u64>,
    pub paid_out: u128,
    pub storage: u128,
}

/// A sell request: `(vintage, amount, start)` portions, `start` being the vintage's open total before this portion.
#[near(serializers = [borsh])]
#[derive(Clone)]
pub struct Request {
    pub epoch: u64,
    pub portions: Vec<(u32, u128, u128)>,
}

/// The per-vintage aggregates of a settled epoch (C8). Kept until its last request is claimed, then removed; the
/// `settle` event keeps the record.
#[near(serializers = [borsh])]
#[derive(Clone)]
pub struct Settlement {
    pub cap: u128,
    pub max_fill: Option<String>,
    pub requested: Vec<(u32, u128)>,
    pub filled: Vec<(u32, u128)>,
    pub total_requested: u128,
    pub total_filled: u128,
    pub paid: u128,
    pub bound: bool,
    pub open_requests: u32,
    pub paid_left: u128,
}

// --- JSON views (D1) ----------------------------------------------------------------------------------------------

#[near(serializers = [json])]
#[derive(Clone, Debug, PartialEq)]
pub struct ClaimView {
    pub request_id: U64,
    pub filled: U128,
    pub paid: U128,
    pub returned: Vec<(u32, U128)>,
}

#[near(serializers = [json])]
pub struct PoolView {
    pub x: U128,
    pub y: U128,
    pub k: String,
    pub reserve: U128,
    pub supply: U128,
    pub unclaimed_near: U128,
    pub locked: U128,
    /// yoctoNEAR per whole token (10^18 units) at the margin, `X * 10^18 / Y` rounded down.
    pub spot_price: U128,
    pub now: U64,
}

#[near(serializers = [json])]
pub struct OpenView {
    pub epoch: Option<U64>,
    pub requested: Vec<(u32, U128)>,
    pub requests: u32,
    pub current_epoch: U64,
    pub current_vintage: u32,
}

#[near(serializers = [json])]
pub struct SettlementView {
    pub epoch: U64,
    pub cap: U128,
    pub max_fill: Option<String>,
    pub requested: Vec<(u32, U128)>,
    pub filled: Vec<(u32, U128)>,
    pub total_requested: U128,
    pub total_filled: U128,
    pub paid: U128,
    /// yoctoNEAR per whole token paid in this epoch, `paid * 10^18 / total_filled` rounded down.
    pub price: U128,
    pub bound: bool,
    pub open_requests: u32,
    pub paid_left: U128,
}

#[near(serializers = [json])]
pub struct RequestView {
    pub id: U64,
    pub epoch: U64,
    pub portions: Vec<(u32, U128, U128)>,
}

#[near(serializers = [json])]
pub struct AccountView {
    pub lots: Vec<(u32, U128)>,
    pub pending: Vec<RequestView>,
    pub paid_out: U128,
}

#[near(serializers = [json])]
pub struct StorageBalance {
    pub total: U128,
    pub available: U128,
}

#[near(serializers = [json])]
pub struct StorageBalanceBounds {
    pub min: U128,
    pub max: Option<U128>,
}

#[near(serializers = [json])]
pub struct FungibleTokenMetadata {
    pub spec: String,
    pub name: String,
    pub symbol: String,
    pub icon: Option<String>,
    pub reference: Option<String>,
    pub reference_hash: Option<String>,
    pub decimals: u8,
}

#[ext_contract(ext_ft_receiver)]
pub trait FungibleTokenReceiver {
    fn ft_on_transfer(
        &mut self,
        sender_id: AccountId,
        amount: U128,
        msg: String,
    ) -> PromiseOrValue<U128>;
}

// --- Events (D2, NEP-297) -----------------------------------------------------------------------------------------

fn emit(standard: &str, version: &str, event: &str, data: Value) {
    let line = json!({"standard": standard, "version": version, "event": event, "data": [data]});
    env::log_str(&format!("EVENT_JSON:{line}"));
}

fn lifo_event(event: &str, data: Value) {
    emit("lifo", "1.0.0", event, data);
}

fn ft_event(event: &str, data: Value) {
    emit("nep141", "1.0.0", event, data);
}

fn pairs(v: &[(u32, u128)]) -> Vec<(u32, U128)> {
    v.iter().map(|&(a, b)| (a, U128(b))).collect()
}

fn fail(msg: &str) -> ! {
    env::panic_str(msg)
}

fn check(ok: bool, msg: &str) {
    if !ok {
        fail(msg)
    }
}

fn one_yocto() {
    check(
        env::attached_deposit() == NearToken::from_yoctonear(1),
        E_ONE_YOCTO,
    );
}

// --- Lots (B1 to B3) ----------------------------------------------------------------------------------------------

/// B1: removes `amount` from the newest lots first; returns what was taken, newest first.
fn take(lots: &mut Vec<(u32, u128)>, amount: u128) -> Vec<(u32, u128)> {
    let total: u128 = lots.iter().map(|l| l.1).sum();
    check(total >= amount, E_BALANCE);
    let mut left = amount;
    let mut taken = Vec::new();
    while left > 0 {
        let last = lots.len() - 1;
        let (v, a) = lots[last];
        let got = a.min(left);
        taken.push((v, got));
        left -= got;
        if got == a {
            lots.pop();
        } else {
            lots[last].1 = a - got;
        }
    }
    taken
}

/// B2, B3: adds a lot, merged with one of equal vintage; while there are too many, the two oldest merge into the older.
fn add_lot(lots: &mut Vec<(u32, u128)>, vintage: u32, amount: u128, max_lots: u32) {
    let i = lots.partition_point(|l| l.0 < vintage);
    if i < lots.len() && lots[i].0 == vintage {
        lots[i].1 += amount;
    } else {
        lots.insert(i, (vintage, amount));
    }
    while lots.len() > max_lots as usize {
        let (_, a) = lots.remove(1);
        lots[0].1 += a;
    }
}

fn find(v: &[(u32, u128)], vintage: u32) -> u128 {
    v.binary_search_by_key(&vintage, |l| l.0)
        .map(|i| v[i].1)
        .unwrap_or(0)
}

// --- The contract -------------------------------------------------------------------------------------------------

#[near(
    contract_state,
    contract_metadata(
        version = "0.1.0",
        link = "https://github.com/Marc-Dvci/lifo",
        standard(standard = "nep141", version = "1.0.0"),
        standard(standard = "nep145", version = "1.0.0"),
        standard(standard = "nep148", version = "1.0.0"),
        standard(standard = "nep297", version = "1.0.0")
    )
)]
#[derive(PanicOnDefault)]
pub struct Lifo {
    params: Params,
    now: u64,
    reserve: u128,
    supply: u128,
    unclaimed_near: u128,
    /// Tokens inside open or unclaimed requests: in `supply`, in no account's balance.
    locked: u128,
    open_epoch: Option<u64>,
    open_requested: Vec<(u32, u128)>,
    open_count: u32,
    next_id: u64,
    accounts: LookupMap<AccountId, Account>,
    requests: LookupMap<u64, Request>,
    settlements: LookupMap<u64, Settlement>,
}

#[near]
impl Lifo {
    /// A1: takes every parameter, runs once, only from the contract account itself.
    #[init]
    #[private]
    pub fn new(params: Params) -> Self {
        check(params.valid(), E_PARAMS);
        Self {
            now: params.launch_time.0,
            params,
            reserve: 0,
            supply: 0,
            unclaimed_near: 0,
            locked: 0,
            open_epoch: None,
            open_requested: Vec::new(),
            open_count: 0,
            next_id: 0,
            accounts: LookupMap::new(b"a"),
            requests: LookupMap::new(b"r"),
            settlements: LookupMap::new(b"s"),
        }
    }

    // --- calls ---

    /// A2, A3: buys with the attached NEAR (less the account's storage on a first buy). Returns the tokens bought.
    #[payable]
    pub fn buy(&mut self) -> U128 {
        let account = env::predecessor_account_id();
        let mut deposit = env::attached_deposit().as_yoctonear();
        self.begin(Some(&account));
        check(deposit > 0, E_ZERO);
        let mut acc = match self.accounts.get(&account) {
            Some(a) => a.clone(),
            None => {
                check(account != env::current_account_id(), E_CONTRACT_ACCOUNT);
                let cost = self.account_cost();
                check(deposit > cost, E_STORAGE);
                deposit -= cost;
                Account {
                    storage: cost,
                    ..Default::default()
                }
            }
        };
        let out = buy_out(self.x(), self.y(), deposit);
        check(out > 0, E_BUY_TOO_SMALL);
        check(self.supply + out <= self.params.max_supply.0, E_MAX_SUPPLY);
        self.reserve += deposit;
        self.supply += out;
        let vintage = self.vintage_of(self.now);
        add_lot(&mut acc.lots, vintage, out, self.params.max_lots);
        self.accounts.insert(account.clone(), acc);
        ft_event(
            "ft_mint",
            json!({"owner_id": account, "amount": U128(out), "memo": "buy"}),
        );
        lifo_event(
            "buy",
            json!({"account_id": account, "deposit": U128(deposit), "tokens": U128(out), "vintage": vintage,
                   "reserve": U128(self.reserve), "supply": U128(self.supply)}),
        );
        U128(out)
    }

    /// C1: locks `amount` tokens, newest lots first, into a request for the current epoch. Any attached NEAR is added
    /// to the caller's storage balance, which must cover the request. Returns the request id.
    #[payable]
    pub fn request_sell(&mut self, amount: U128) -> U64 {
        let account = env::predecessor_account_id();
        self.begin(Some(&account));
        let amount = amount.0;
        check(amount > 0, E_ZERO);
        let mut acc = self
            .accounts
            .get(&account)
            .cloned()
            .unwrap_or_else(|| fail(E_BALANCE));
        acc.storage += env::attached_deposit().as_yoctonear();
        let taken = take(&mut acc.lots, amount);
        let needed = self.account_cost() + (acc.pending.len() as u128 + 1) * self.request_cost();
        check(acc.storage >= needed, E_STORAGE);
        let epoch = self.epoch_of(self.now);
        let mut portions = Vec::with_capacity(taken.len());
        for (v, a) in taken {
            let i = self.open_requested.partition_point(|l| l.0 < v);
            let start = if i < self.open_requested.len() && self.open_requested[i].0 == v {
                let s = self.open_requested[i].1;
                self.open_requested[i].1 = s + a;
                s
            } else {
                self.open_requested.insert(i, (v, a));
                0
            };
            portions.push((v, a, start));
        }
        self.open_epoch = Some(epoch);
        self.open_count += 1;
        self.locked += amount;
        let id = self.next_id;
        self.next_id += 1;
        acc.pending.push(id);
        self.accounts.insert(account.clone(), acc);
        let shown: Vec<(u32, U128, U128)> = portions
            .iter()
            .map(|&(v, a, s)| (v, U128(a), U128(s)))
            .collect();
        self.requests.insert(id, Request { epoch, portions });
        ft_event(
            "ft_burn",
            json!({"owner_id": account, "amount": U128(amount), "memo": format!("sell request {id}")}),
        );
        lifo_event(
            "sell_request",
            json!({"account_id": account, "request_id": U64(id), "epoch": U64(epoch), "portions": shown}),
        );
        U64(id)
    }

    /// C2: settles the finished epoch, if there is one. Anyone may call it. Returns how many epochs it settled.
    pub fn settle(&mut self) -> u32 {
        let before = self.open_epoch;
        self.begin(None);
        u32::from(before.is_some() && self.open_epoch.is_none())
    }

    /// C9: pays every settled request of the caller once and gives back its unfilled tokens.
    pub fn claim(&mut self) -> Vec<ClaimView> {
        self.begin(None);
        let claims = self.claim_settled(&env::predecessor_account_id());
        check(!claims.is_empty(), E_NOTHING);
        claims
    }

    // --- NEP-141 ---

    #[payable]
    pub fn ft_transfer(&mut self, receiver_id: AccountId, amount: U128, memo: Option<String>) {
        one_yocto();
        let sender = env::predecessor_account_id();
        self.begin(Some(&sender));
        self.transfer(&sender, &receiver_id, amount.0, memo);
    }

    #[payable]
    pub fn ft_transfer_call(
        &mut self,
        receiver_id: AccountId,
        amount: U128,
        memo: Option<String>,
        msg: String,
    ) -> PromiseOrValue<U128> {
        one_yocto();
        check(env::prepaid_gas() >= GAS_TRANSFER_CALL_MIN, E_GAS);
        let sender = env::predecessor_account_id();
        self.begin(Some(&sender));
        let moved = self.transfer(&sender, &receiver_id, amount.0, memo);
        let oldest = moved.iter().map(|l| l.0).min().unwrap_or(0);
        ext_ft_receiver::ext(receiver_id.clone())
            .with_static_gas(Gas::from_tgas(5))
            .with_unused_gas_weight(1)
            .ft_on_transfer(sender.clone(), amount, msg)
            .then(
                Self::ext(env::current_account_id())
                    .with_static_gas(GAS_RESOLVE)
                    .with_unused_gas_weight(0)
                    .ft_resolve_transfer(sender, receiver_id, amount, oldest),
            )
            .into()
    }

    /// B4: takes back what the receiver did not use, with the oldest vintage among the lots that were sent. Returns
    /// the amount used. Settling here is not needed: a refund changes neither the reserve nor the supply.
    #[private]
    pub fn ft_resolve_transfer(
        &mut self,
        sender_id: AccountId,
        receiver_id: AccountId,
        amount: U128,
        oldest: u32,
    ) -> U128 {
        let unused = match env::promise_result_checked(0, 64) {
            Ok(v) => near_sdk::serde_json::from_slice::<U128>(&v).map_or(amount.0, |u| u.0),
            Err(_) => amount.0,
        };
        U128(amount.0 - self.refund(&sender_id, &receiver_id, amount.0, oldest, unused))
    }

    pub fn ft_total_supply(&self) -> U128 {
        U128(self.supply - self.locked)
    }

    pub fn ft_balance_of(&self, account_id: AccountId) -> U128 {
        U128(
            self.accounts
                .get(&account_id)
                .map_or(0, |a| a.lots.iter().map(|l| l.1).sum()),
        )
    }

    pub fn ft_metadata(&self) -> FungibleTokenMetadata {
        FungibleTokenMetadata {
            spec: "ft-1.0.0".into(),
            name: "LIFO".into(),
            symbol: "LIFO".into(),
            icon: None,
            reference: None,
            reference_hash: None,
            decimals: TOKEN_DECIMALS,
        }
    }

    // --- NEP-145 ---

    #[payable]
    pub fn storage_deposit(
        &mut self,
        account_id: Option<AccountId>,
        registration_only: Option<bool>,
    ) -> StorageBalance {
        let account = account_id.unwrap_or_else(env::predecessor_account_id);
        check(account != env::current_account_id(), E_CONTRACT_ACCOUNT);
        let deposit = env::attached_deposit().as_yoctonear();
        let only = registration_only.unwrap_or(false);
        let min = self.account_cost();
        let mut refund = 0;
        let acc = match self.accounts.get(&account) {
            Some(a) => {
                let mut a = a.clone();
                if only {
                    refund = deposit;
                } else {
                    a.storage += deposit;
                }
                a
            }
            None => {
                check(deposit >= min, E_STORAGE);
                let keep = if only { min } else { deposit };
                refund = deposit - keep;
                Account {
                    storage: keep,
                    ..Default::default()
                }
            }
        };
        self.accounts.insert(account.clone(), acc);
        if refund > 0 {
            let _ = Promise::new(env::predecessor_account_id())
                .transfer(NearToken::from_yoctonear(refund));
        }
        self.storage_balance_of(account).unwrap()
    }

    #[payable]
    pub fn storage_withdraw(&mut self, amount: Option<U128>) -> StorageBalance {
        one_yocto();
        let account = env::predecessor_account_id();
        let acc = self
            .accounts
            .get(&account)
            .cloned()
            .unwrap_or_else(|| fail(E_NOT_REGISTERED));
        let available = acc.storage - self.storage_used(&acc);
        let amount = amount.map_or(available, |a| a.0);
        check(amount <= available, E_STORAGE_BALANCE);
        let mut acc = acc;
        acc.storage -= amount;
        self.accounts.insert(account.clone(), acc);
        if amount > 0 {
            let _ = Promise::new(account.clone()).transfer(NearToken::from_yoctonear(amount));
        }
        self.storage_balance_of(account).unwrap()
    }

    /// Removes an empty account and returns its storage balance. `force` never burns tokens: an account that holds
    /// tokens or has unclaimed requests is refused.
    #[payable]
    pub fn storage_unregister(&mut self, force: Option<bool>) -> bool {
        one_yocto();
        let _ = force;
        let account = env::predecessor_account_id();
        let Some(acc) = self.accounts.get(&account).cloned() else {
            return false;
        };
        check(acc.lots.is_empty(), E_HOLDS_TOKENS);
        check(acc.pending.is_empty(), E_PENDING);
        self.accounts.remove(&account);
        if acc.storage > 0 {
            let _ = Promise::new(account).transfer(NearToken::from_yoctonear(acc.storage));
        }
        true
    }

    pub fn storage_balance_bounds(&self) -> StorageBalanceBounds {
        StorageBalanceBounds {
            min: U128(self.account_cost()),
            max: None,
        }
    }

    pub fn storage_balance_of(&self, account_id: AccountId) -> Option<StorageBalance> {
        self.accounts.get(&account_id).map(|a| StorageBalance {
            total: U128(a.storage),
            available: U128(a.storage - self.storage_used(a)),
        })
    }

    // --- views (D1) ---

    pub fn get_params(&self) -> Params {
        self.params.clone()
    }

    /// What one sell request reserves from the owner's storage balance until it is claimed, in yoctoNEAR.
    pub fn get_request_storage(&self) -> U128 {
        U128(self.request_cost())
    }

    pub fn get_pool(&self) -> PoolView {
        let (x, y) = (self.x(), self.y());
        PoolView {
            x: U128(x.as_u128()),
            y: U128(y.as_u128()),
            k: (x * y).to_string(),
            reserve: U128(self.reserve),
            supply: U128(self.supply),
            unclaimed_near: U128(self.unclaimed_near),
            locked: U128(self.locked),
            spot_price: U128((x * u(10u128.pow(TOKEN_DECIMALS as u32)) / y).as_u128()),
            now: U64(self.now),
        }
    }

    /// The open epoch's requests grouped by vintage, and the current epoch and vintage from the block time.
    pub fn get_open(&self) -> OpenView {
        let t = env::block_timestamp().max(self.params.launch_time.0);
        OpenView {
            epoch: self.open_epoch.map(U64),
            requested: pairs(&self.open_requested),
            requests: self.open_count,
            current_epoch: U64(self.epoch_of(t)),
            current_vintage: self.vintage_of(t),
        }
    }

    /// A settled epoch while it still has unclaimed requests; afterwards its record is in the `settle` event.
    pub fn get_settlement(&self, epoch: U64) -> Option<SettlementView> {
        self.settlements.get(&epoch.0).map(|s| SettlementView {
            epoch,
            cap: U128(s.cap),
            max_fill: s.max_fill.clone(),
            requested: pairs(&s.requested),
            filled: pairs(&s.filled),
            total_requested: U128(s.total_requested),
            total_filled: U128(s.total_filled),
            paid: U128(s.paid),
            price: U128(if s.total_filled == 0 {
                0
            } else {
                mul_div(s.paid, 10u128.pow(18), s.total_filled)
            }),
            bound: s.bound,
            open_requests: s.open_requests,
            paid_left: U128(s.paid_left),
        })
    }

    pub fn get_account(&self, account_id: AccountId) -> Option<AccountView> {
        self.accounts.get(&account_id).map(|a| AccountView {
            lots: pairs(&a.lots),
            pending: a
                .pending
                .iter()
                .map(|&id| {
                    let r = self.requests.get(&id).unwrap();
                    RequestView {
                        id: U64(id),
                        epoch: U64(r.epoch),
                        portions: r
                            .portions
                            .iter()
                            .map(|&(v, a, s)| (v, U128(a), U128(s)))
                            .collect(),
                    }
                })
                .collect(),
            paid_out: U128(a.paid_out),
        })
    }

    /// Tokens a buy of `deposit` yoctoNEAR would receive now, for a registered account.
    pub fn quote_buy(&self, deposit: U128) -> U128 {
        U128(buy_out(self.x(), self.y(), deposit.0))
    }
}

impl Lifo {
    fn x(&self) -> U256 {
        u(self.params.virtual_near.0) + u(self.reserve)
    }

    fn y(&self) -> U256 {
        u(self.params.virtual_tokens.0 - self.supply)
    }

    fn epoch_of(&self, t: u64) -> u64 {
        (t - self.params.launch_time.0) / self.params.epoch_length.0
    }

    fn vintage_of(&self, t: u64) -> u32 {
        ((t - self.params.launch_time.0) / self.params.vintage_length.0) as u32
    }

    fn byte_cost() -> u128 {
        env::storage_byte_cost().as_yoctonear()
    }

    fn account_cost(&self) -> u128 {
        account_bytes(self.params.max_lots) as u128 * Self::byte_cost()
    }

    fn request_cost(&self) -> u128 {
        request_bytes(self.params.max_lots) as u128 * Self::byte_cost()
    }

    fn storage_used(&self, a: &Account) -> u128 {
        self.account_cost() + a.pending.len() as u128 * self.request_cost()
    }

    /// C2: every state-changing call starts here. Settles the finished epoch, then claims the caller's settled
    /// requests (if it names one).
    fn begin(&mut self, actor: Option<&AccountId>) {
        let now = env::block_timestamp();
        check(now >= self.params.launch_time.0, E_NOT_LAUNCHED);
        check(now >= self.now, E_TIME);
        self.now = now;
        self.settle_finished();
        if let Some(a) = actor {
            self.claim_settled(a);
        }
    }

    /// C3 to C5: settles the one epoch with requests once it is over, on the pool as it is now. Work: one pass over
    /// the epoch's distinct vintages (C8), whatever the number of requests.
    fn settle_finished(&mut self) {
        let Some(e) = self.open_epoch else { return };
        if self.epoch_of(self.now) <= e {
            return;
        }
        let fee = self.params.sell_fee_bps as u128;
        let cap = mul_div(self.reserve, self.params.gate_bps as u128, BPS);
        let (x, y) = (self.x(), self.y());
        let max_fill = gate_fill(x, y, cap, fee);
        let requested = std::mem::take(&mut self.open_requested);
        let total: u128 = requested.iter().map(|l| l.1).sum();
        let total_filled = match max_fill {
            Some(f) if f < u(total) => f.as_u128(),
            _ => total,
        };
        let bound = total_filled < total;
        let mut left = total_filled;
        let mut filled: Vec<(u32, u128)> = requested
            .iter()
            .rev()
            .map(|&(v, r)| {
                let f = r.min(left);
                left -= f;
                (v, f)
            })
            .collect();
        filled.reverse();
        let paid = after_fee(sell_gross(x, y, total_filled), fee);
        self.reserve -= paid;
        self.supply -= total_filled;
        self.locked -= total_filled;
        self.unclaimed_near += paid;
        let max_fill = max_fill.map(|f| f.to_string());
        lifo_event(
            "settle",
            json!({"epoch": U64(e), "cap": U128(cap), "max_fill": max_fill, "requested": pairs(&requested),
                   "filled": pairs(&filled), "total_requested": U128(total), "total_filled": U128(total_filled),
                   "paid": U128(paid), "bound": bound, "requests": self.open_count,
                   "reserve": U128(self.reserve), "supply": U128(self.supply)}),
        );
        self.settlements.insert(
            e,
            Settlement {
                cap,
                max_fill,
                requested,
                filled,
                total_requested: total,
                total_filled,
                paid,
                bound,
                open_requests: self.open_count,
                paid_left: paid,
            },
        );
        self.open_epoch = None;
        self.open_count = 0;
    }

    /// C6, C7, C9: computes and pays each settled request of `account` from its epoch's aggregates, exactly once.
    fn claim_settled(&mut self, account: &AccountId) -> Vec<ClaimView> {
        let Some(mut acc) = self.accounts.get(account).cloned() else {
            return Vec::new();
        };
        let mut claims = Vec::new();
        let mut keep = Vec::new();
        let mut total_paid = 0u128;
        for &id in &acc.pending.clone() {
            let epoch = self.requests.get(&id).unwrap().epoch;
            let Some(s) = self.settlements.get_mut(&epoch) else {
                keep.push(id);
                continue;
            };
            let r = self.requests.remove(&id).unwrap();
            let mut filled = 0u128;
            let mut returned = Vec::new();
            for &(v, a, start) in &r.portions {
                let (f_v, r_v) = (find(&s.filled, v), find(&s.requested, v));
                let got = mul_div(start + a, f_v, r_v) - mul_div(start, f_v, r_v);
                filled += got;
                if a > got {
                    returned.push((v, a - got));
                }
            }
            let paid = if s.total_filled == 0 {
                0
            } else {
                mul_div(filled, s.paid, s.total_filled)
            };
            s.paid_left -= paid;
            s.open_requests -= 1;
            self.unclaimed_near -= paid;
            let mut dust = 0;
            if s.open_requests == 0 {
                dust = s.paid_left;
                self.reserve += dust;
                self.unclaimed_near -= dust;
                self.settlements.remove(&epoch);
            }
            acc.paid_out += paid;
            total_paid += paid;
            let back: u128 = returned.iter().map(|l: &(u32, u128)| l.1).sum();
            self.locked -= back;
            for &(v, a) in &returned {
                add_lot(&mut acc.lots, v, a, self.params.max_lots);
            }
            if back > 0 {
                ft_event(
                    "ft_mint",
                    json!({"owner_id": account, "amount": U128(back), "memo": format!("unfilled {id}")}),
                );
            }
            lifo_event(
                "claim",
                json!({"account_id": account, "request_id": U64(id), "epoch": U64(epoch), "filled": U128(filled),
                       "paid": U128(paid), "returned": pairs(&returned), "dust_returned": U128(dust)}),
            );
            claims.push(ClaimView {
                request_id: U64(id),
                filled: U128(filled),
                paid: U128(paid),
                returned: pairs(&returned),
            });
        }
        if claims.is_empty() {
            return claims;
        }
        acc.pending = keep;
        self.accounts.insert(account.clone(), acc);
        if total_paid > 0 {
            let _ = Promise::new(account.clone()).transfer(NearToken::from_yoctonear(total_paid));
        }
        claims
    }

    /// B1, B2: moves `amount` from the sender's newest lots to the receiver, keeping vintages.
    fn transfer(
        &mut self,
        sender: &AccountId,
        receiver: &AccountId,
        amount: u128,
        memo: Option<String>,
    ) -> Vec<(u32, u128)> {
        check(sender != receiver, E_SELF);
        check(amount > 0, E_ZERO);
        let mut from = self
            .accounts
            .get(sender)
            .cloned()
            .unwrap_or_else(|| fail(E_BALANCE));
        let moved = take(&mut from.lots, amount);
        let mut to = self
            .accounts
            .get(receiver)
            .cloned()
            .unwrap_or_else(|| fail(E_NOT_REGISTERED));
        for &(v, a) in &moved {
            add_lot(&mut to.lots, v, a, self.params.max_lots);
        }
        self.accounts.insert(sender.clone(), from);
        self.accounts.insert(receiver.clone(), to);
        ft_event(
            "ft_transfer",
            json!({"old_owner_id": sender, "new_owner_id": receiver, "amount": U128(amount), "memo": memo}),
        );
        lifo_event(
            "transfer",
            json!({"old_owner_id": sender, "new_owner_id": receiver, "lots": pairs(&moved)}),
        );
        moved
    }

    /// B4: the refund of `ft_transfer_call`, `min(unused, amount, receiver's balance)`, returned to the sender as one lot
    /// of `oldest`. Public for the tests, which replay the model's `transfer_call` without a second block.
    pub fn refund(
        &mut self,
        sender: &AccountId,
        receiver: &AccountId,
        amount: u128,
        oldest: u32,
        unused: u128,
    ) -> u128 {
        let Some(mut to) = self.accounts.get(receiver).cloned() else {
            return 0;
        };
        let Some(mut from) = self.accounts.get(sender).cloned() else {
            return 0;
        };
        let held: u128 = to.lots.iter().map(|l| l.1).sum();
        let refund = unused.min(amount).min(held);
        if refund == 0 {
            return 0;
        }
        take(&mut to.lots, refund);
        add_lot(&mut from.lots, oldest, refund, self.params.max_lots);
        self.accounts.insert(receiver.clone(), to);
        self.accounts.insert(sender.clone(), from);
        ft_event(
            "ft_transfer",
            json!({"old_owner_id": receiver, "new_owner_id": sender, "amount": U128(refund), "memo": "refund"}),
        );
        lifo_event(
            "transfer",
            json!({"old_owner_id": receiver, "new_owner_id": sender, "lots": [[oldest, U128(refund)]]}),
        );
        refund
    }
}
