//! Writing a check file as TOML, in the form people read: colors in
//! hexadecimal, long lists over several lines, and its comments.

use crate::property::{Band, Kind, Property, Register, Span};
use crate::{CheckFile, InputEntry};
use std::fmt::{Display, Write as _};

impl CheckFile {
    /// The file's text. [`CheckFile::parse`] gives back everything but its
    /// comments.
    pub fn to_toml(&self) -> String {
        let mut out = String::new();
        for line in &self.header {
            let _ = writeln!(out, "# {line}");
        }
        if !self.header.is_empty() {
            out.push('\n');
        }
        let _ = writeln!(out, "rom = {:?}", self.rom.display().to_string());
        if !self.expect_failures.is_empty() {
            let _ = writeln!(out, "expect_failures = {}", strings(&self.expect_failures));
        }
        let run = &self.run;
        let _ = writeln!(out, "\n[run]\nframes = {}", run.frames);
        let why = if run.skip_frames == 1 {
            "        # the first frame shows the power-on state"
        } else {
            ""
        };
        let _ = writeln!(out, "skip_frames = {}{why}", run.skip_frames);
        let _ = writeln!(out, "zero = {}\nrandom_seeds = {}", run.zero, run.random_seeds);
        if let Some(frames) = run.random_frames {
            let _ = writeln!(
                out,
                "random_frames = {frames}   # the random states need not follow the state through"
            );
        }
        for (i, property) in self.properties.iter().enumerate() {
            notes(&mut out, self, |before| before == i);
            write_property(&mut out, property);
        }
        notes(&mut out, self, |before| before >= self.properties.len());
        for entry in &self.inputs {
            write_input(&mut out, entry);
        }
        out
    }
}

fn notes(out: &mut String, file: &CheckFile, here: impl Fn(usize) -> bool) {
    for note in file.notes.iter().filter(|n| here(n.before)) {
        let _ = writeln!(out, "\n# {}", note.text);
    }
}

fn write_input(out: &mut String, entry: &InputEntry) {
    let _ = writeln!(out, "\n[[input]]\nfrom = {}", entry.from);
    let nothing = entry.joystick1.is_empty() && entry.console.is_empty() && !entry.uses_paddles();
    if !entry.joystick0.is_empty() || nothing {
        let _ = writeln!(out, "joystick0 = {}", strings(&entry.joystick0));
    }
    if !entry.joystick1.is_empty() {
        let _ = writeln!(out, "joystick1 = {}", strings(&entry.joystick1));
    }
    if !entry.console.is_empty() {
        let _ = writeln!(out, "console = {}", strings(&entry.console));
    }
    for (n, knob, fire) in [
        (0, entry.paddle0, entry.paddle0_fire),
        (1, entry.paddle1, entry.paddle1_fire),
    ] {
        if let Some(knob) = knob {
            let _ = writeln!(out, "paddle{n} = {knob:?}");
        }
        if fire {
            let _ = writeln!(out, "paddle{n}_fire = true");
        }
    }
}

fn write_property(out: &mut String, property: &Property) {
    out.push_str("[[property]]\n");
    if let Some(name) = &property.name {
        let _ = writeln!(out, "name = {name:?}");
    }
    let _ = writeln!(out, "kind = {:?}", property.kind.name());
    if !property.on_frames.is_empty() {
        let _ = writeln!(out, "on_frames = {}", spans(&property.on_frames));
    }
    let mut field = |key: &str, value: String| {
        let _ = writeln!(out, "{key} = {value}");
    };
    match &property.kind {
        Kind::FrameLength { lines } => field("lines", lines.to_string()),
        Kind::FrameLengthRange { min_lines, max_lines } => {
            field("min_lines", min_lines.to_string());
            field("max_lines", max_lines.to_string());
        }
        Kind::PictureBlock {
            lines,
            start_window,
            clocks,
            color,
        } => {
            field("lines", lines.to_string());
            field("start_window", span(start_window));
            field("clocks", span(clocks));
            field("color", hex(*color));
        }
        Kind::SignalOnLines {
            signal,
            first_line,
            lines,
        } => {
            field("signal", format!("{:?}", signal.name()));
            field("first_line", first_line.to_string());
            field("lines", lines.to_string());
        }
        Kind::SignalSpan {
            signal,
            first_line,
            min_clocks,
            max_clocks,
        } => {
            field("signal", format!("{:?}", signal.name()));
            field("first_line", first_line.to_string());
            field("min_clocks", min_clocks.to_string());
            field("max_clocks", max_clocks.to_string());
        }
        Kind::BlankOutside { lines } => field("lines", span(lines)),
        Kind::ConstantOutput { lines, clocks, color } => {
            field("lines", span(lines));
            field("clocks", span(clocks));
            field("color", hex(*color));
        }
        Kind::OutputByLine {
            lines,
            clocks,
            start,
            step,
        } => {
            field("lines", span(lines));
            field("clocks", span(clocks));
            field("start", hex(*start));
            field("step", hex(*step));
        }
        Kind::OutputColors { lines, clocks, colors } => {
            field("lines", span(lines));
            field("clocks", span(clocks));
            let rows: Vec<String> = colors
                .chunks(16)
                .map(|row| format!("    {},", row.iter().map(|c| hex(*c)).collect::<Vec<_>>().join(", ")))
                .collect();
            field("colors", format!("[\n{}\n]", rows.join("\n")));
        }
        Kind::WriteWindow {
            chip,
            register,
            lines,
            clocks,
            windows,
        } => {
            field("chip", format!("{:?}", chip.name()));
            field(
                "register",
                match register {
                    Register::Name(name) => format!("{name:?}"),
                    Register::Number(n) => n.to_string(),
                },
            );
            field("lines", span(lines));
            if let Some(clocks) = clocks {
                field("clocks", span(clocks));
            }
            if !windows.is_empty() {
                field("windows", spans(windows));
            }
        }
        Kind::Image {
            lines,
            clocks,
            rows,
            behind,
        } => {
            field("lines", span(lines));
            field("clocks", span(clocks));
            let rows: Vec<String> = rows.iter().map(band).collect();
            field("rows", format!("[\n{}\n]", rows.join("\n")));
            if !behind.is_empty() {
                field("behind", strings(behind));
            }
        }
        Kind::Object {
            lines,
            rows,
            color,
            positions,
            shown,
            frames,
            behind,
        } => {
            field("lines", span(lines));
            field("rows", list(rows.iter().map(|r| hex(*r))));
            field("color", hex(*color));
            field("positions", list(positions.iter().map(list)));
            if let Some(shown) = shown {
                field("shown", shown.to_string());
            }
            if let Some(frames) = frames {
                field("frames", frames.to_string());
            }
            if !behind.is_empty() {
                field("behind", strings(behind));
            }
        }
        Kind::AudioRegisters { channel, settings } => {
            field("channel", channel.to_string());
            field("settings", list(settings.iter().map(list)));
        }
        Kind::AudioPitch {
            channel,
            frames,
            hz,
            cents,
            clock_hz,
        } => {
            field("channel", channel.to_string());
            field("frames", span(frames));
            field("hz", format!("{hz:?}"));
            field("cents", format!("{cents:?}"));
            field("clock_hz", format!("{clock_hz:?}"));
        }
        Kind::Ram {
            address,
            line,
            value,
            values,
        } => {
            field("address", format!("0x{address:02x}"));
            state_fields(&mut field, *line, *value, values);
        }
        Kind::State {
            register,
            line,
            value,
            values,
        } => {
            field("register", format!("{register:?}"));
            state_fields(&mut field, *line, *value, values);
        }
        Kind::Paddle {
            paddle,
            address,
            line,
            lines_per_count,
            max,
            tolerance,
        } => {
            field("paddle", paddle.to_string());
            field("address", format!("0x{address:02x}"));
            if let Some(line) = line {
                field("line", line.to_string());
            }
            if *lines_per_count != 1 {
                field("lines_per_count", lines_per_count.to_string());
            }
            field("max", max.to_string());
            field("tolerance", format!("{tolerance:?}"));
        }
    }
}

fn state_fields(field: &mut impl FnMut(&str, String), line: Option<u32>, value: Option<u8>, values: &[u8]) {
    if let Some(line) = line {
        field("line", line.to_string());
    }
    if let Some(value) = value {
        field("value", hex(value));
    }
    if !values.is_empty() {
        field("values", list(values.iter().map(|v| hex(*v))));
    }
}

fn hex(v: u8) -> String {
    format!("0x{v:02x}")
}

fn span(s: &Span) -> String {
    format!("[{}, {}]", s.start, s.end)
}

fn spans(s: &[Span]) -> String {
    list(s.iter().map(span))
}

fn strings(s: &[String]) -> String {
    list(s.iter().map(|s| format!("{s:?}")))
}

fn list<T: Display>(items: impl IntoIterator<Item = T>) -> String {
    let items: Vec<String> = items.into_iter().map(|i| i.to_string()).collect();
    format!("[{}]", items.join(", "))
}

fn band(band: &Band) -> String {
    let runs = list(
        band.runs
            .iter()
            .map(|[color, length]| format!("[0x{color:02x}, {length}]")),
    );
    format!("    {{ lines = {}, runs = {runs} }},", band.lines)
}
