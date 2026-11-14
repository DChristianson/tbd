//! Fragment mode: run a short code sequence on the full board, from a given
//! state, and report the state it ends in and what happened.
//!
//! This is the interface the superoptimizer and the scheduler will use to
//! test candidate code: the machine, the timing, and the trace are exactly
//! those of a whole program. The code is placed in the cartridge at `origin`,
//! so it runs from ROM as 2600 code does.

use crate::cpu::Stop;
use crate::machine::{Machine, State};
use crate::trace::{Trace, TraceConfig};

/// Why a fragment run ended.
#[derive(Clone, Copy, Debug, PartialEq, Eq)]
pub enum End {
    /// The program counter left the fragment, by falling off its end or by
    /// jumping or branching out.
    LeftFragment,
    /// The cycle limit was reached first.
    CycleLimit,
}

#[derive(Clone, Debug, PartialEq, Eq)]
pub struct Run {
    pub end: End,
    /// The state after the last instruction executed.
    pub state: State,
    pub trace: Trace,
}

/// Run `code`, placed at `origin` in the cartridge, from `start` (whose PC is
/// replaced by `origin`), until the program counter leaves the fragment or
/// at least `max_cycles` cycles have passed. The rest of the cartridge is
/// zero. `origin` must be a cartridge address (A12 set).
pub fn run(code: &[u8], origin: u16, start: &State, max_cycles: u64, config: TraceConfig) -> Result<Run, Stop> {
    let offset = (origin & 0x0FFF) as usize;
    assert!(
        origin & 0x1000 != 0,
        "fragment origin ${origin:04x} is not a cartridge address"
    );
    assert!(offset + code.len() <= 4096, "fragment does not fit in the cartridge");

    let mut rom = [0u8; 4096];
    rom[offset..offset + code.len()].copy_from_slice(code);
    let mut m = Machine::from_state(&rom, start).expect("a 4K image");
    m.regs.pc = origin;
    m.record(config);

    let end_pc = origin.wrapping_add(code.len() as u16);
    let limit = start.cycles + max_cycles;
    let end = loop {
        let pc = m.regs.pc;
        if pc < origin || pc >= end_pc {
            break End::LeftFragment;
        }
        if m.board.cycles >= limit {
            break End::CycleLimit;
        }
        m.step()?;
    };
    let state = m.state();
    Ok(Run {
        end,
        state,
        trace: m.board.trace,
    })
}
