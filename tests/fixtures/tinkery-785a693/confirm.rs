//! TEST-only fixture recorder: use the pinned Rust renderer and confirmation writer, no model.
use std::{env, fs, path::PathBuf};
use tinkery::seed_me::ledger::{self, Value};

fn main() -> Result<(), String> {
    let directory = PathBuf::from(env::args().nth(1).ok_or("TEST directory required")?);
    let read = |name| fs::read_to_string(directory.join(name)).map_err(|e| e.to_string());
    let records = Value::parse(&read("seed-checks.json")?)?;
    let ledger = ledger::load(&directory)?;
    ledger::set_checks(&directory, &records)?;
    let draft = read("TEST-draft.md")?
        .replace("{{checks}}", &ledger::checks_block(&records)?)
        .replace("{{decisions}}", &ledger::decisions_block(&ledger)?);
    fs::write(directory.join("seed-contract.md"), draft).map_err(|e| e.to_string())?;
    ledger::confirm_seed(
        &directory, "r1", "TEST explicit confirmation; not operator approval",
        "2026-10-21T00:00:00+00:00",
    )?;
    Ok(())
}
