//! What a player does: the joysticks and the console's switches, frame by
//! frame (milestone 9, task 5).
//!
//! An [`Input`] is what is held on a frame; a [`Script`] says, from each
//! frame on, what is held, until the next of its entries. The board applies
//! a script's input when a frame starts, as `VSYNC` turns on (`TIA-FRAME-1`),
//! so a program that reads its input once a frame sees one value for the
//! whole frame. Frames are numbered from 1, at the first `VSYNC`, as the
//! checker numbers them; before it, nothing is held.
//!
//! The bits each control sets are `RIOT-IO-2`'s and `TIA-INPUT-1`'s.

use crate::board::Controls;

/// A joystick: its four directions and its button, each true while held.
#[derive(Clone, Copy, Debug, Default, PartialEq, Eq)]
pub struct Joystick {
    pub up: bool,
    pub down: bool,
    pub left: bool,
    pub right: bool,
    pub fire: bool,
}

/// What is held on a frame.
#[derive(Clone, Copy, Debug, Default, PartialEq, Eq)]
pub struct Input {
    pub joysticks: [Joystick; 2],
    /// The console's reset and select switches, each true while held.
    pub reset: bool,
    pub select: bool,
}

impl Input {
    /// The lines the RIOT's ports read with this input held: a direction
    /// pushed clears its bit of port A (joystick 0's in bits 7-4, joystick
    /// 1's in 3-0, right, left, down, up), and reset and select clear bits 0
    /// and 1 of port B. The color and difficulty switches are as
    /// [`Controls::NONE`] sets them.
    pub fn controls(&self) -> Controls {
        let mut c = Controls::NONE;
        for (n, j) in self.joysticks.iter().enumerate() {
            let shift = 4 * (1 - n);
            for (held, bit) in [(j.right, 3), (j.left, 2), (j.down, 1), (j.up, 0)] {
                if held {
                    c.port_a &= !(1 << (bit + shift));
                }
            }
        }
        if self.reset {
            c.port_b &= !0x01;
        }
        if self.select {
            c.port_b &= !0x02;
        }
        c
    }

    /// Parse a control's name, as check files write them: `up`, `down`,
    /// `left`, `right`, or `fire` for a joystick; `reset` or `select` for the
    /// console. Sets it held on joystick `n`, or the console.
    pub fn hold(&mut self, n: usize, name: &str) -> Result<(), String> {
        let j = &mut self.joysticks[n];
        match name {
            "up" => j.up = true,
            "down" => j.down = true,
            "left" => j.left = true,
            "right" => j.right = true,
            "fire" => j.fire = true,
            _ => {
                return Err(format!(
                    "unknown joystick control {name:?}: give up, down, left, right, or fire"
                ))
            }
        }
        if j.up && j.down || j.left && j.right {
            return Err("a joystick cannot be pushed two opposite ways at once".into());
        }
        Ok(())
    }
}

/// An input held from frame `from` on, until the next entry's frame.
#[derive(Clone, Copy, Debug, PartialEq, Eq)]
pub struct Entry {
    pub from: u64,
    pub input: Input,
}

/// A script: what is held on every frame. Entries are in order of frame; a
/// frame before the first entry holds nothing.
#[derive(Clone, Debug, Default, PartialEq, Eq)]
pub struct Script {
    entries: Vec<Entry>,
}

impl Script {
    /// A script from its entries, which must be in increasing order of
    /// frame, each frame at least 1.
    pub fn new(entries: Vec<Entry>) -> Result<Script, String> {
        if let Some(e) = entries.iter().find(|e| e.from == 0) {
            return Err(format!("an entry from frame {}: frames count from 1", e.from));
        }
        if let Some(w) = entries.windows(2).find(|w| w[1].from <= w[0].from) {
            return Err(format!(
                "entries from frames {} and then {}: give them in increasing order",
                w[0].from, w[1].from
            ));
        }
        Ok(Script { entries })
    }

    /// What is held on frame `frame`.
    pub fn input(&self, frame: u64) -> Input {
        self.entries
            .iter()
            .rev()
            .find(|e| e.from <= frame)
            .map_or_else(Input::default, |e| e.input)
    }

    pub fn entries(&self) -> &[Entry] {
        &self.entries
    }
}
