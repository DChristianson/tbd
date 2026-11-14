//! A flat 64K memory bus with no devices, recording every bus cycle. This is
//! the memory model the CPU test suites assume.

use crate::cpu::Bus;
use crate::domain::Concrete;

/// One bus cycle.
#[derive(Clone, Copy, Debug, PartialEq, Eq)]
pub struct Cycle {
    pub addr: u16,
    pub value: u8,
    pub write: bool,
}

pub struct FlatBus {
    pub mem: Vec<u8>,
    /// Every cycle since the log was last cleared.
    pub cycles: Vec<Cycle>,
    /// Whether to record cycles. Off for long runs.
    pub record: bool,
}

impl FlatBus {
    pub fn new() -> FlatBus {
        FlatBus {
            mem: vec![0; 0x10000],
            cycles: Vec::new(),
            record: true,
        }
    }
}

impl Default for FlatBus {
    fn default() -> Self {
        Self::new()
    }
}

impl Bus<Concrete> for FlatBus {
    #[inline]
    fn read(&mut self, _: &mut Concrete, addr: u16) -> u8 {
        let value = self.mem[addr as usize];
        if self.record {
            self.cycles.push(Cycle {
                addr,
                value,
                write: false,
            });
        }
        value
    }

    #[inline]
    fn write(&mut self, _: &mut Concrete, addr: u16, value: u8) {
        self.mem[addr as usize] = value;
        if self.record {
            self.cycles.push(Cycle {
                addr,
                value,
                write: true,
            });
        }
    }
}
