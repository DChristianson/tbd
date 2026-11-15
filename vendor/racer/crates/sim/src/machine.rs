//! A whole 2600: the CPU and the board.

use crate::board::{Board, PowerOn, Rng, Timer};
use crate::cpu::{self, Regs, Stop, FLAG_B, FLAG_U};
use crate::domain::Concrete;
use crate::tia::Tia;
use crate::trace::{Instruction, Trace, TraceConfig};

pub struct Machine {
    pub regs: Regs<u8>,
    pub board: Board,
}

impl Machine {
    /// Power on with `rom` inserted. Call [`Machine::reset`] before running.
    ///
    /// S starts at 0 in both power-on modes, so reset leaves it at $FD, as all
    /// three surveyed emulators do. See `BOARD-POWERON-1`.
    pub fn new(rom: &[u8], power_on: PowerOn) -> Result<Machine, String> {
        let board = Board::new(rom, power_on)?;
        let mut regs = Regs {
            a: 0,
            x: 0,
            y: 0,
            s: 0,
            p: FLAG_U,
            pc: 0,
        };
        if let PowerOn::Random(seed) = power_on {
            let mut rng = Rng::new(seed);
            regs.a = rng.byte();
            regs.x = rng.byte();
            regs.y = rng.byte();
            regs.p = (rng.byte() | FLAG_U) & !FLAG_B;
        }
        Ok(Machine { regs, board })
    }

    /// Start recording at the levels in `config`, discarding any trace so far.
    pub fn record(&mut self, config: TraceConfig) {
        self.board.trace = Trace::new(config);
    }

    /// Run the CPU's reset sequence. See `CPU-RESET-1`.
    pub fn reset(&mut self) -> Result<(), Stop> {
        self.board.instruction = Instruction::Reset;
        cpu::reset(&mut Concrete, &mut self.regs, &mut self.board)
    }

    /// Execute one instruction.
    pub fn step(&mut self) -> Result<(), Stop> {
        let pc = self.regs.pc;
        self.board.instruction = Instruction::At {
            pc,
            opcode: self.board.peek(pc),
        };
        cpu::step(&mut Concrete, &mut self.regs, &mut self.board)
    }

    /// Execute instructions until `frames` frames have completed in all.
    pub fn run_frames(&mut self, frames: usize) -> Result<(), Stop> {
        while self.board.tia.frame_lengths.len() < frames {
            self.step()?;
        }
        Ok(())
    }

    /// The machine's state, apart from the cartridge and the trace.
    pub fn state(&self) -> State {
        let b = &self.board;
        State {
            regs: self.regs.clone(),
            ram: b.ram,
            tia: b.tia.clone(),
            timer: b.timer.clone(),
            data_bus: b.data_bus,
            cycles: b.cycles,
        }
    }

    /// A machine with `rom` inserted, in `state`.
    pub fn from_state(rom: &[u8], state: &State) -> Result<Machine, String> {
        let mut board = Board::new(rom, PowerOn::Zero)?;
        board.ram = state.ram;
        board.tia = state.tia.clone();
        board.timer = state.timer.clone();
        board.data_bus = state.data_bus;
        board.cycles = state.cycles;
        Ok(Machine {
            regs: state.regs.clone(),
            board,
        })
    }

    /// Execute instructions until at least `cycles` more cycles have passed.
    pub fn run_cycles(&mut self, cycles: u64) -> Result<(), Stop> {
        let end = self.board.cycles + cycles;
        while self.board.cycles < end {
            self.step()?;
        }
        Ok(())
    }
}

/// Everything that determines what a machine does next, except the
/// cartridge: the CPU registers, RAM, the TIA (registers and beam position),
/// the RIOT timer, the data bus, and the cycle count.
#[derive(Clone, Debug, PartialEq, Eq)]
pub struct State {
    pub regs: Regs<u8>,
    pub ram: [u8; 128],
    pub tia: Tia,
    pub timer: Timer,
    pub data_bus: u8,
    pub cycles: u64,
}
