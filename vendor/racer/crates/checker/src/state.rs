//! State (milestone 9, task 13): RAM and registers, as a property reads
//! them, at the start of a line or at a frame's end. Probes and reference
//! ROMs check what they compute, not only what they show (decision 0010,
//! amended).

pub use racer_checkfile::state::{names, TIA_READS};
use racer_sim::board::Board;
use std::collections::BTreeMap;

/// The machine's state at one moment: on frame `frame`, at the start of
/// line `line`, or at the frame's end.
#[derive(Clone, Debug)]
pub struct Snapshot {
    pub frame: u64,
    pub line: Option<u32>,
    pub ram: [u8; 128],
    pub values: BTreeMap<String, u8>,
}

impl Snapshot {
    pub fn of(board: &Board, frame: u64, line: Option<u32>) -> Snapshot {
        let tia = &board.tia;
        let mut values = BTreeMap::new();
        for (address, name) in crate::property::TIA_REGISTERS.iter().enumerate() {
            values.insert(name.to_string(), tia.registers[address]);
        }
        for (address, name) in TIA_READS.iter().enumerate() {
            values.insert(name.to_string(), tia.read(address as u8));
        }
        for n in 0..6u8 {
            values.insert(format!("INPT{n}"), tia.read(0x08 + n));
        }
        for (c, ch) in tia.audio.channels.iter().enumerate() {
            values.insert(format!("AUDIO{c}_DIVIDER"), ch.divider);
            values.insert(format!("AUDIO{c}_PULSE"), ch.pulse);
            values.insert(format!("AUDIO{c}_NOISE"), ch.noise);
        }
        values.insert("INTIM".into(), board.timer.value);
        values.insert("SWCHA".into(), board.peek(0x280));
        values.insert("SWCHB".into(), board.peek(0x282));
        Snapshot {
            frame,
            line,
            ram: board.ram,
            values,
        }
    }
}
