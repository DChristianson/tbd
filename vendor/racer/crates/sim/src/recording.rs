//! Symbolic runs against a recording bus (decision 0020): the superoptimizer's
//! proofs.
//!
//! The CPU runs in the symbolic domain, from an entry state whose free values
//! are variables, against a bus that holds the cartridge as constants and RAM
//! as terms, and records each TIA write, with its cycle and its value's term,
//! instead of simulating the TIA. What the code wrote, and what it left, can
//! then be compared with a specification by the solver, for every value of
//! the variables.
//!
//! What it does not model, it refuses rather than guesses:
//! - **TIA reads** return a fresh variable for each read: an input the code
//!   cannot know. A result that depends on one cannot be proved equal to a
//!   specification, which is the right answer.
//! - **`WSYNC` and `RSYNC`** are recorded as writes, as any TIA write is, but
//!   the halt or the counter reset they cause is not followed: cycles after
//!   one are not the board's. A timed specification must not allow them;
//!   untimed code, such as startup's, may write them harmlessly.
//! - **The inputs** (`SWCHA`, `SWCHB`, `INPT4`, and `INPT5`) read as one
//!   variable each for the whole run, `swcha`, `swchb`, `inpt4`, and
//!   `inpt5`: a frame's input is held for the frame (`input.rs`), so a proof
//!   of code that reads it holds for every input.
//! - **The RIOT's direction registers and timer** refuse the run on any
//!   access.
//!
//! Cycles are counted as the concrete board counts them, one for each bus
//! access, so a write's cycle here is the concrete trace's, less the cycle the
//! run started at.

use crate::board::{decode, Device};
use crate::cpu::{self, Bus, Regs, Stop, FLAG_B, FLAG_U};
use crate::fragment::End;
use crate::symbolic::{self, Byte, Symbolic};

/// The state a symbolic run starts from.
#[derive(Clone, Debug)]
pub struct Start {
    /// The program counter is replaced by the code's origin.
    pub regs: Regs<Byte>,
    pub ram: Vec<Byte>,
}

impl Start {
    /// Every register and RAM byte a variable: `a`, `x`, `y`, `s`, `p`, and
    /// `m80` to `mff`. P's B bit is 0 and bit 5 is 1, as stored (`CPU-P-1`).
    pub fn free() -> Start {
        Start {
            regs: Regs {
                a: symbolic::var("a"),
                x: symbolic::var("x"),
                y: symbolic::var("y"),
                s: symbolic::var("s"),
                p: symbolic::partly_known("p", FLAG_B | FLAG_U, FLAG_U),
                pc: 0,
            },
            ram: (0x80..=0xFFu16).map(|a| symbolic::var(&format!("m{a:02x}"))).collect(),
        }
    }
}

/// A TIA write the code made.
#[derive(Clone, Debug)]
pub struct TiaWrite {
    /// The bus cycle that wrote it, from 0 at the run's start. It takes
    /// effect at the end of that cycle (`BOARD-CYCLE-1`).
    pub cycle: u64,
    /// The register: the address modulo 64.
    pub register: u8,
    pub value: Byte,
}

#[derive(Clone, Debug)]
pub struct Run {
    pub end: End,
    pub regs: Regs<Byte>,
    pub ram: Vec<Byte>,
    pub writes: Vec<TiaWrite>,
    /// Bus cycles run.
    pub cycles: u64,
}

/// Why a symbolic run was refused.
#[derive(Clone, Debug, PartialEq, Eq)]
pub enum Refused {
    /// The CPU stopped: a branch on a flag still a term, a value used as an
    /// address, decimal arithmetic on terms, or an undocumented opcode.
    Cpu(Stop),
    /// An access the recording does not model, at the instruction at `pc`.
    Access { pc: u16, addr: u16, why: &'static str },
}

struct RecordingBus<'a> {
    rom: &'a [u8],
    ram: Vec<Byte>,
    writes: Vec<TiaWrite>,
    cycles: u64,
    tia_reads: u32,
    refused: Option<(u16, &'static str)>,
}

impl RecordingBus<'_> {
    fn refuse(&mut self, addr: u16, why: &'static str) {
        self.refused.get_or_insert((addr, why));
    }
}

impl Bus<Symbolic> for RecordingBus<'_> {
    fn read(&mut self, _: &mut Symbolic, addr: u16) -> Byte {
        self.cycles += 1;
        match decode(addr) {
            Device::Cartridge(offset) => symbolic::constant(self.rom[offset]),
            Device::Ram(offset) => self.ram[offset].clone(),
            Device::Tia(r) if r & 0x0F == crate::tia::INPT4 => symbolic::var("inpt4"),
            Device::Tia(r) if r & 0x0F == crate::tia::INPT5 => symbolic::var("inpt5"),
            Device::Tia(r) if (crate::tia::INPT0..=crate::tia::INPT3).contains(&(r & 0x0F)) => {
                symbolic::var(&format!("inpt{}", (r & 0x0F) - crate::tia::INPT0))
            }
            Device::Tia(_) => {
                self.tia_reads += 1;
                symbolic::var(&format!("tia{}", self.tia_reads))
            }
            Device::RiotIo(0) => symbolic::var("swcha"),
            Device::RiotIo(2) => symbolic::var("swchb"),
            Device::RiotIo(_) | Device::RiotTimer(_) => {
                self.refuse(addr, "the RIOT is not modeled symbolically");
                symbolic::constant(0)
            }
        }
    }

    fn write(&mut self, _: &mut Symbolic, addr: u16, value: Byte) {
        let cycle = self.cycles;
        self.cycles += 1;
        match decode(addr) {
            Device::Cartridge(_) => {} // ROM
            Device::Ram(offset) => self.ram[offset] = value,
            Device::Tia(register) => self.writes.push(TiaWrite { cycle, register, value }),
            Device::RiotIo(_) | Device::RiotTimer(_) => self.refuse(addr, "the RIOT is not modeled symbolically"),
        }
    }
}

/// Run `code`, placed at `origin` in an otherwise empty cartridge, from
/// `start`, until the program counter leaves it or `max_cycles` bus cycles
/// have passed. As [`crate::fragment::run`], symbolically.
pub fn run(code: &[u8], origin: u16, start: &Start, max_cycles: u64) -> Result<Run, Refused> {
    let offset = (origin & 0x0FFF) as usize;
    assert!(
        origin & 0x1000 != 0,
        "fragment origin ${origin:04x} is not a cartridge address"
    );
    assert!(offset + code.len() <= 4096, "fragment does not fit in the cartridge");
    let mut rom = vec![0u8; 4096];
    rom[offset..offset + code.len()].copy_from_slice(code);
    run_rom(&rom, origin, origin.wrapping_add(code.len() as u16), start, max_cycles)
}

/// Run the code from `origin` to `end` in `rom`, a 4K cartridge image whose
/// other bytes (tables) the code may read, as [`run`] does.
pub fn run_rom(rom: &[u8], origin: u16, end: u16, start: &Start, max_cycles: u64) -> Result<Run, Refused> {
    assert_eq!(rom.len(), 4096, "a 4K cartridge image");
    assert_eq!(start.ram.len(), 128, "128 bytes of RAM");
    let mut regs = start.regs.clone();
    regs.pc = origin;
    let mut bus = RecordingBus {
        rom,
        ram: start.ram.clone(),
        writes: Vec::new(),
        cycles: 0,
        tia_reads: 0,
        refused: None,
    };
    let end = loop {
        let pc = regs.pc;
        if pc < origin || pc >= end {
            break End::LeftFragment;
        }
        if bus.cycles >= max_cycles {
            break End::CycleLimit;
        }
        cpu::step(&mut Symbolic, &mut regs, &mut bus).map_err(Refused::Cpu)?;
        if let Some((addr, why)) = bus.refused {
            return Err(Refused::Access { pc, addr, why });
        }
    };
    Ok(Run {
        end,
        regs,
        ram: bus.ram,
        writes: bus.writes,
        cycles: bus.cycles,
    })
}
