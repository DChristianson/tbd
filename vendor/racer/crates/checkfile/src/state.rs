//! The names a `state` property may read (milestone 9, task 13). The
//! checker reads their values from the machine.

/// TIA read registers, by address.
#[rustfmt::skip]
pub const TIA_READS: [&str; 8] = ["CXM0P", "CXM1P", "CXP0FB", "CXP1FB", "CXM0FB", "CXM1FB", "CXBLPF", "CXPPMM"];

/// Every name a `state` property may read: the TIA's write registers, as
/// last written; its read registers; each audio channel's counters; and
/// the RIOT's timer and ports, as a read would see them.
pub fn names() -> Vec<String> {
    let mut out: Vec<String> = crate::property::TIA_REGISTERS
        .iter()
        .filter(|r| !matches!(**r, "WSYNC" | "RSYNC" | "HMOVE" | "HMCLR" | "CXCLR"))
        .map(|r| r.to_string())
        .collect();
    out.extend(TIA_READS.iter().map(|r| r.to_string()));
    out.extend(["INPT0", "INPT1", "INPT2", "INPT3", "INPT4", "INPT5"].map(String::from));
    for c in 0..2 {
        for counter in ["DIVIDER", "PULSE", "NOISE"] {
            out.push(format!("AUDIO{c}_{counter}"));
        }
    }
    out.extend(["INTIM", "SWCHA", "SWCHB"].map(String::from));
    out
}
