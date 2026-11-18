//! Check files: what a ROM must do, shared by the compiler, which predicts
//! it, and the checker, which runs the ROM in the simulator and checks it.
//!
//! A check file, written in TOML next to the ROM it describes, names the
//! ROM, says how to run it, and lists the properties every checked frame must
//! satisfy:
//!
//! ```toml
//! rom = "build/frame-0a.bin"     # relative to the check file
//!
//! [run]
//! frames = 10                    # frames to run
//! skip_frames = 1                # frames not checked (the first, after power-on)
//! zero = true                    # run once from an all-zero power-on state
//! random_seeds = 100             # and from random states with seeds 1-100
//! random_frames = 256            # frames from each random state, if not `frames`
//!
//! [[property]]
//! kind = "frame_length"
//! lines = 262
//! ```
//!
//! A file can also list the properties expected to fail, for ROMs that must
//! be rejected: `expect_failures = ["frame_length"]`. The file passes when
//! every run fails exactly those properties.
//!
//! A file can give an input script, `[[input]]` entries each holding
//! controls from a frame on (milestone 9), which the simulator applies as
//! each frame starts: `[[input]] from = 3, joystick0 = ["right"]`.
//!
//! Property kinds are described in [`property::Kind`]. [`CheckFile::parse`]
//! reads a file and [`CheckFile::to_toml`] writes one. What it writes keeps
//! the comments and the hexadecimal colors that make a file readable, which
//! serializing the types would lose.

pub mod property;
pub mod state;
mod write;

use property::Property;
use serde::{Deserialize, Serialize};
use std::path::PathBuf;

#[derive(Clone, Debug, PartialEq, Deserialize)]
#[serde(deny_unknown_fields)]
pub struct CheckFile {
    /// The ROM, relative to the check file.
    pub rom: PathBuf,
    pub run: RunConfig,
    #[serde(default)]
    pub expect_failures: Vec<String>,
    #[serde(rename = "property", default)]
    pub properties: Vec<Property>,
    /// The input held on each frame (milestone 9): each entry from its
    /// frame on, until the next. Without any, nothing is held.
    #[serde(rename = "input", default)]
    pub inputs: Vec<InputEntry>,
    /// The comment lines at the top of the file. Parsing does not keep them.
    #[serde(skip)]
    pub header: Vec<String>,
    /// The comments among the properties. Parsing does not keep them.
    #[serde(skip)]
    pub notes: Vec<Note>,
}

/// A comment among the properties: before the property numbered `before`,
/// or, if there is none, after the last.
#[derive(Clone, Debug, PartialEq, Eq)]
pub struct Note {
    pub before: usize,
    pub text: String,
}

/// What is held from frame `from` on, until the next entry: each joystick's
/// controls (`up`, `down`, `left`, `right`, `fire`), and the console's
/// switches (`reset`, `select`). What an entry does not name is released.
///
/// Paddles (milestone 10) are plugged into joystick 0's port: `paddle0` and
/// `paddle1` turn a knob to a position from 0 to 1, which stays until a
/// later entry turns it, and `paddle0_fire` and `paddle1_fire` press a
/// button, released unless named. A paddle never turned is unplugged, and
/// an entry cannot use joystick 0 and a paddle together.
#[derive(Clone, Debug, Default, PartialEq, Deserialize, Serialize)]
#[serde(deny_unknown_fields)]
pub struct InputEntry {
    pub from: u64,
    #[serde(default)]
    pub joystick0: Vec<String>,
    #[serde(default)]
    pub joystick1: Vec<String>,
    #[serde(default)]
    pub console: Vec<String>,
    #[serde(default, skip_serializing_if = "Option::is_none")]
    pub paddle0: Option<f64>,
    #[serde(default, skip_serializing_if = "Option::is_none")]
    pub paddle1: Option<f64>,
    #[serde(default, skip_serializing_if = "std::ops::Not::not")]
    pub paddle0_fire: bool,
    #[serde(default, skip_serializing_if = "std::ops::Not::not")]
    pub paddle1_fire: bool,
}

impl InputEntry {
    /// Whether the entry turns or presses a paddle.
    pub fn uses_paddles(&self) -> bool {
        self.paddle0.is_some() || self.paddle1.is_some() || self.paddle0_fire || self.paddle1_fire
    }
}

#[derive(Clone, Debug, PartialEq, Eq, Deserialize)]
#[serde(deny_unknown_fields)]
pub struct RunConfig {
    pub frames: usize,
    #[serde(default = "one")]
    pub skip_frames: usize,
    #[serde(default = "yes")]
    pub zero: bool,
    #[serde(default)]
    pub random_seeds: u64,
    /// Frames to run from each random state, if fewer than `frames`: the
    /// random states show that the power-on state does not matter, which
    /// takes fewer frames than following a program's state through.
    #[serde(default)]
    pub random_frames: Option<usize>,
}

fn one() -> usize {
    1
}
fn yes() -> bool {
    true
}

impl CheckFile {
    /// Read a check file's text.
    pub fn parse(text: &str) -> Result<CheckFile, String> {
        toml::from_str(text).map_err(|e| e.to_string())
    }

    /// A comment here, after the properties so far.
    pub fn note(&mut self, text: impl Into<String>) {
        self.notes.push(Note {
            before: self.properties.len(),
            text: text.into(),
        });
    }

    /// The first property named `name`.
    pub fn property(&self, name: &str) -> Option<&Property> {
        self.properties.iter().find(|p| p.name.as_deref() == Some(name))
    }
}
