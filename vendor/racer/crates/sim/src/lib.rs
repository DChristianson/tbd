//! Racer's cycle-exact simulator of the Racer-26 target model.
//!
//! Hardware facts implemented here are recorded, with their sources and the
//! tests that demonstrate them, in docs/target-model.md.
//!
//! - [`cpu`]: the 6507, every documented instruction, cycle by cycle.
//! - [`domain`]: value domains; the CPU runs concretely or symbolically
//!   (decision 0001).
//! - [`symbolic`]: the symbolic domain and its SMT solver interface.
//! - [`flat`]: a flat 64K memory bus, for CPU tests.
//! - [`board`]: the 2600's memory map, cartridge, RIOT, and TIA.
//! - [`tia`]: the TIA subset: line and frame timing, sync, blanking, and
//!   the background color.
//! - [`audio`]: the TIA's two audio channels.
//! - [`input`]: the joysticks and the console's switches, and scripts of
//!   them frame by frame.
//! - [`trace`]: what happened, at the levels requested.
//! - [`fragment`]: run a code sequence on the board from a given state.
//! - [`recording`]: run a code sequence symbolically, recording its TIA
//!   writes (decision 0020).
//! - [`machine`]: the CPU and the board together.

pub mod audio;
pub mod board;
pub mod cpu;
pub mod domain;
pub mod flat;
pub mod fragment;
pub mod input;
pub mod machine;
pub mod recording;
pub mod symbolic;
pub mod tia;
pub mod trace;
