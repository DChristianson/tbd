//! The 2600 board: the 6507's 13-bit address space, the 4K cartridge, the
//! RIOT (6532: RAM, I/O ports, and interval timer), and the TIA.
//!
//! Every bus access is one CPU cycle, three color clocks. Within a cycle, the
//! order of events differs by device, as measured against the emulators (see
//! `BOARD-CYCLE-1` in docs/target-model.md):
//! - A TIA write takes effect after the cycle's three color clocks, so the
//!   first clock to show it is the one after the writing cycle.
//! - A RIOT timer write takes effect before the cycle's timer decrement.
//! - A read halted by `WSYNC` waits, a cycle at a time, for the next line.
//!
//! Facts implemented here are recorded in docs/target-model.md under
//! `BOARD-*`, `RIOT-*`, and `TIA-*`.

use crate::cpu::Bus;
use crate::domain::Concrete;
use crate::input::{Input, Script};
use crate::tia::{Event, Tia};
use crate::trace::{Access, Chip, CpuCycle, Instruction, RegisterWrite, Trace, TraceConfig};

/// How the state the hardware leaves undefined at power-on is set.
#[derive(Clone, Copy, Debug, PartialEq, Eq)]
pub enum PowerOn {
    /// Every register and RAM byte is zero.
    Zero,
    /// Registers, RAM, the TIA's registers, and the timer are random,
    /// reproducibly from the seed. See `BOARD-POWERON-1`.
    Random(u64),
}

/// A reproducible pseudo-random sequence (splitmix64).
pub(crate) struct Rng(u64);

impl Rng {
    pub(crate) fn new(seed: u64) -> Rng {
        Rng(seed)
    }
    pub(crate) fn byte(&mut self) -> u8 {
        self.0 = self.0.wrapping_add(0x9E37_79B9_7F4A_7C15);
        let mut z = self.0;
        z = (z ^ (z >> 30)).wrapping_mul(0xBF58_476D_1CE4_E5B9);
        z = (z ^ (z >> 27)).wrapping_mul(0x94D0_49BB_1331_11EB);
        (z ^ (z >> 31)) as u8
    }
}

/// The RIOT's interval timer. See `RIOT-TIMER-1`.
#[derive(Clone, Debug, PartialEq, Eq)]
pub struct Timer {
    /// The value `INTIM` reads.
    pub value: u8,
    /// Cycles per decrement: 1, 8, 64, or 1024.
    pub interval: u16,
    /// Cycles left until the next decrement.
    countdown: u16,
    /// Whether the timer has passed zero, after which it decrements every cycle.
    pub underflowed: bool,
}

impl Timer {
    fn new(value: u8) -> Timer {
        Timer {
            value,
            interval: 1024,
            countdown: 1024,
            underflowed: false,
        }
    }

    /// A write to `TIM1T`, `TIM8T`, `TIM64T`, or `T1024T`. The first
    /// decrement happens at the end of the writing cycle.
    fn set(&mut self, value: u8, interval: u16) {
        self.value = value;
        self.interval = interval;
        self.countdown = 1;
        self.underflowed = false;
    }

    /// The end of one cycle.
    fn tick(&mut self) {
        if self.underflowed {
            self.value = self.value.wrapping_sub(1);
            return;
        }
        self.countdown -= 1;
        if self.countdown == 0 {
            self.countdown = self.interval;
            if self.value == 0 {
                self.value = 0xFF;
                self.underflowed = true;
            } else {
                self.value -= 1;
            }
        }
    }
}

/// What is plugged into the console and set on it: the lines the RIOT's
/// ports read, and the fire buttons, which the TIA reads. See `RIOT-IO-1`,
/// `RIOT-IO-2`, and `TIA-INPUT-1`.
#[derive(Clone, Copy, Debug, PartialEq, Eq)]
pub struct Controls {
    /// Port A's lines: joystick 0's right, left, down, and up in bits 7-4,
    /// joystick 1's in bits 3-0, each 0 while pushed.
    pub port_a: u8,
    /// Port B's lines: the console's switches, each as `RIOT-IO-2` says.
    pub port_b: u8,
}

/// Port B's unused bits, 2, 4, and 5, which no switch drives.
pub const PORT_B_UNUSED: u8 = 0x34;

impl Controls {
    /// Nothing pushed, no switch held, color, both difficulties B. Port B's
    /// unused bits, 2, 4, and 5, read 1.
    pub const NONE: Controls = Controls {
        port_a: 0xFF,
        port_b: 0x3F,
    };
}

pub struct Board {
    pub rom: [u8; 4096],
    pub ram: [u8; 128],
    pub timer: Timer,
    pub tia: Tia,
    /// RIOT port A and B data direction registers: a bit set is an output.
    pub swacnt: u8,
    pub swbcnt: u8,
    /// What RIOT port A and B drive on their output bits.
    pub swa_out: u8,
    pub swb_out: u8,
    /// The controllers and switches; the fire buttons are the TIA's
    /// (`Tia::set_fire`).
    pub controls: Controls,
    /// What port B's unused bits read: 1, as Gopher2600 and Stella read
    /// them, by default; 6502.ts reads them as 0 (E28).
    pub port_b_unused: u8,
    /// A script of what is held on each frame, applied as each frame starts.
    pub script: Option<Script>,
    /// The value on the data bus after the last cycle. Bits the TIA does not
    /// drive on a read keep this value. See `BOARD-TIA-READ-1`.
    pub data_bus: u8,
    /// Cycles since power-on.
    pub cycles: u64,
    /// What has been recorded; see `trace.rs`. Off by default.
    pub trace: Trace,
    /// The instruction being executed, for the trace. Set by `Machine`.
    pub instruction: Instruction,
}

impl Board {
    /// A board with `rom` inserted. The ROM must be 4096 bytes: the stock 4K
    /// cartridge, the first target profile.
    pub fn new(rom: &[u8], power_on: PowerOn) -> Result<Board, String> {
        let rom: [u8; 4096] = rom
            .try_into()
            .map_err(|_| format!("expected a 4096-byte ROM, got {} bytes", rom.len()))?;
        let mut board = Board {
            rom,
            ram: [0; 128],
            timer: Timer::new(0),
            tia: Tia::new([0; 64]),
            swacnt: 0,
            swbcnt: 0,
            swa_out: 0,
            swb_out: 0,
            controls: Controls::NONE,
            port_b_unused: PORT_B_UNUSED,
            script: None,
            data_bus: 0,
            cycles: 0,
            trace: Trace::new(TraceConfig::OFF),
            instruction: Instruction::Reset,
        };
        if let PowerOn::Random(seed) = power_on {
            // A different stream from the CPU registers' (see Machine::new).
            let mut rng = Rng::new(seed ^ 0xB0A2_D000);
            board.ram.iter_mut().for_each(|b| *b = rng.byte());
            let mut registers = [0u8; 64];
            registers.iter_mut().for_each(|b| *b = rng.byte());
            board.tia = Tia::new(registers);
            board.timer = Timer::new(rng.byte());
            // Drawn after the others, so their values are unchanged.
            board.tia.player0.counter = rng.byte() % 160;
            board.tia.missile0.counter = rng.byte() % 160;
            board.tia.player1.counter = rng.byte() % 160;
            board.tia.ball.counter = rng.byte() % 160;
            for c in &mut board.tia.audio.channels {
                c.divider = rng.byte() % 32;
                c.pulse = rng.byte() % 16;
                c.noise = rng.byte() % 32;
            }
        }
        Ok(board)
    }

    /// One CPU cycle: three color clocks, then the end of the cycle.
    fn tick(&mut self) {
        for _ in 0..3 {
            let clock = self.tia.clock;
            let (signal, event) = self.tia.clock();
            self.trace.video(clock, signal, event == Event::LineEnded);
            if let Some(sample) = self.tia.sample {
                self.trace.audio(sample);
            }
        }
        self.cycles += 1;
        self.timer.tick();
    }

    fn record_cycle(&mut self, cycle: u64, access: Access, addr: u16, value: u8) {
        let instruction = self.instruction;
        self.trace.cpu_cycle(CpuCycle {
            cycle,
            access,
            addr,
            value,
            instruction,
        });
    }

    /// Record a register write that took effect at the current position.
    fn record_write(&mut self, chip: Chip, register: u8, value: u8, cycle: u64) {
        let (frame, line, clock) = (self.tia.frame, self.tia.line, self.tia.clock);
        self.trace.register_write(RegisterWrite {
            chip,
            register,
            value,
            cycle,
            frame,
            line,
            clock,
        });
    }

    /// Hold `input`, from now until it is changed: the RIOT's ports read its
    /// lines, and the TIA its fire buttons.
    pub fn set_input(&mut self, input: Input) {
        self.controls = input.controls();
        for (n, j) in input.joysticks.iter().enumerate() {
            self.tia.set_fire(n, j.fire);
        }
    }

    /// Read without a bus cycle or side effects, for inspection.
    pub fn peek(&self, addr: u16) -> u8 {
        match decode(addr) {
            Device::Cartridge(offset) => self.rom[offset],
            Device::Ram(offset) => self.ram[offset],
            Device::Tia(reg) => self.tia.read(reg) | (self.data_bus & 0x3F),
            // An input bit reads its line; an output bit, what the port drives.
            Device::RiotIo(reg) => match reg {
                0 => (self.controls.port_a & !self.swacnt) | (self.swa_out & self.swacnt),
                1 => self.swacnt,
                2 => {
                    let lines = self.controls.port_b & !PORT_B_UNUSED | self.port_b_unused;
                    (lines & !self.swbcnt) | (self.swb_out & self.swbcnt)
                }
                _ => self.swbcnt,
            },
            // INTIM, or the interrupt flag in bit 7 (TIMINT; not verified).
            Device::RiotTimer(reg) if reg & 0x01 == 0 => self.timer.value,
            Device::RiotTimer(_) if self.timer.underflowed => 0x80,
            Device::RiotTimer(_) => 0x00,
        }
    }
}

/// What an address selects. See `BOARD-MAP-1`.
#[derive(Clone, Copy, Debug, PartialEq, Eq)]
pub enum Device {
    /// A12 set: the cartridge, offset within the 4K ROM.
    Cartridge(usize),
    /// A12 and A7 clear: the TIA, register number (A0-A5 for writes).
    Tia(u8),
    /// A12 clear, A7 set, A9 clear: RIOT RAM, offset 0-127.
    Ram(usize),
    /// A12 clear, A7 and A9 set, A2 clear: RIOT I/O ports, register A0-A1.
    RiotIo(u8),
    /// A12 clear, A7, A9, and A2 set: the RIOT timer, register A0-A4.
    RiotTimer(u8),
}

/// Decode an address. The 6507 has only 13 address lines, so A13-A15 are
/// ignored, and devices respond to every address their selecting lines match.
pub fn decode(addr: u16) -> Device {
    let a = addr & 0x1FFF;
    if a & 0x1000 != 0 {
        Device::Cartridge((a & 0x0FFF) as usize)
    } else if a & 0x0080 == 0 {
        Device::Tia((a & 0x3F) as u8)
    } else if a & 0x0200 == 0 {
        Device::Ram((a & 0x7F) as usize)
    } else if a & 0x0004 == 0 {
        Device::RiotIo((a & 0x03) as u8)
    } else {
        Device::RiotTimer((a & 0x1F) as u8)
    }
}

impl Bus<Concrete> for Board {
    fn read(&mut self, _: &mut Concrete, addr: u16) -> u8 {
        // RDY: after a WSYNC strobe, the CPU halts at its next read until the
        // next line starts. Write cycles are not halted. See TIA-WSYNC-1.
        if self.tia.wsync {
            while self.tia.clock != 0 {
                let value = self.peek(addr);
                self.record_cycle(self.cycles, Access::Halted, addr, value);
                self.tick();
            }
            self.tia.wsync = false;
        }
        let value = self.peek(addr);
        self.record_cycle(self.cycles, Access::Read, addr, value);
        self.data_bus = value;
        self.tick();
        value
    }

    fn write(&mut self, _: &mut Concrete, addr: u16, value: u8) {
        let cycle = self.cycles;
        self.record_cycle(cycle, Access::Write, addr, value);
        if let Device::Tia(reg) = decode(addr) {
            self.data_bus = value;
            self.tick();
            if self.tia.write(reg, value) == Event::FrameStarted {
                self.trace.frame_started(self.tia.frame);
                if let Some(input) = self.script.as_ref().map(|s| s.input(self.tia.frame)) {
                    self.set_input(input);
                }
            }
            self.record_write(Chip::Tia, reg, value, cycle);
            return;
        }
        match decode(addr) {
            Device::Cartridge(_) => {} // ROM
            Device::Ram(offset) => self.ram[offset] = value,
            Device::Tia(_) => unreachable!("handled above"),
            Device::RiotIo(reg) => match reg {
                0 => self.swa_out = value,
                1 => self.swacnt = value,
                2 => self.swb_out = value,
                _ => self.swbcnt = value,
            },
            Device::RiotTimer(reg) => {
                // A4 set: set the timer, with A0-A1 selecting the interval.
                // A4 clear: edge-detect control, not modeled.
                if reg & 0x10 != 0 {
                    let interval = [1, 8, 64, 1024][(reg & 0x03) as usize];
                    self.timer.set(value, interval);
                }
            }
        }
        self.data_bus = value;
        self.tick();
        match decode(addr) {
            Device::RiotIo(reg) => self.record_write(Chip::Riot, reg, value, cycle),
            Device::RiotTimer(reg) => self.record_write(Chip::Riot, reg, value, cycle),
            _ => {}
        }
    }
}
