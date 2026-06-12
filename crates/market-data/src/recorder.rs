//! On-disk event logs: the recorder's output and the backtester's input.
//!
//! Format (version 1): a 4-byte magic `QNT1`, a length-prefixed bincode
//! [`LogHeader`] naming the venue and instrument once (events themselves
//! carry no instrument), then length-prefixed bincode [`MarketEvent`] frames.
//! Length prefixes make truncation (e.g. a killed recorder) detectable: a
//! clean EOF is only valid at a frame boundary, anything else surfaces as
//! [`LogError::TruncatedFrame`] instead of silently shortening history.

use std::fs::File;
use std::io::{self, BufReader, BufWriter, ErrorKind, Read, Write};
use std::path::Path;

use quantis_core::events::MarketEvent;
use serde::{Deserialize, Serialize};
use thiserror::Error;

/// File magic for event logs, version 1.
pub const MAGIC: [u8; 4] = *b"QNT1";

/// Errors reading or writing an event log.
#[derive(Debug, Error)]
pub enum LogError {
    /// Underlying I/O failure.
    #[error("event log I/O: {0}")]
    Io(#[from] io::Error),
    /// The file does not start with the expected magic bytes.
    #[error("not a Quantis event log (bad magic)")]
    BadMagic,
    /// Serialization or deserialization failure.
    #[error("event log codec: {0}")]
    Codec(#[from] bincode::Error),
    /// The file ends in the middle of a frame.
    #[error("event log is truncated mid-frame")]
    TruncatedFrame,
}

/// Log-level metadata, written once at the head of every event log.
#[derive(Debug, Clone, PartialEq, Eq, Serialize, Deserialize)]
pub struct LogHeader {
    /// Venue the data came from (e.g. `"hyperliquid"`).
    pub venue: String,
    /// Instrument symbol (e.g. `"BTC"`).
    pub instrument: String,
    /// Wall-clock creation time, ms since the Unix epoch.
    pub created_unix_ms: i64,
}

/// Streaming writer for event logs.
pub struct EventWriter {
    out: BufWriter<File>,
    events_written: u64,
}

impl EventWriter {
    /// Create a new log at `path`, writing the magic and header immediately.
    pub fn create(path: &Path, header: &LogHeader) -> Result<Self, LogError> {
        let mut out = BufWriter::new(File::create(path)?);
        out.write_all(&MAGIC)?;
        write_frame(&mut out, header)?;
        Ok(Self {
            out,
            events_written: 0,
        })
    }

    /// Append one event.
    pub fn write_event(&mut self, event: &MarketEvent) -> Result<(), LogError> {
        write_frame(&mut self.out, event)?;
        self.events_written += 1;
        Ok(())
    }

    /// Number of events written so far.
    pub fn events_written(&self) -> u64 {
        self.events_written
    }

    /// Flush buffers and close the log.
    pub fn finish(mut self) -> Result<(), LogError> {
        self.out.flush()?;
        Ok(())
    }
}

fn write_frame<T: Serialize>(out: &mut impl Write, value: &T) -> Result<(), LogError> {
    let bytes = bincode::serialize(value)?;
    let len = u32::try_from(bytes.len()).expect("frame larger than 4 GiB");
    out.write_all(&len.to_le_bytes())?;
    out.write_all(&bytes)?;
    Ok(())
}

/// Streaming reader for event logs.
pub struct EventReader {
    input: BufReader<File>,
    header: LogHeader,
}

impl EventReader {
    /// Open a log at `path`, validating magic and reading the header.
    pub fn open(path: &Path) -> Result<Self, LogError> {
        let mut input = BufReader::new(File::open(path)?);
        let mut magic = [0u8; 4];
        input.read_exact(&mut magic)?;
        if magic != MAGIC {
            return Err(LogError::BadMagic);
        }
        let header: LogHeader = read_frame(&mut input)?.ok_or(LogError::TruncatedFrame)?;
        Ok(Self { input, header })
    }

    /// The log's metadata header.
    pub fn header(&self) -> &LogHeader {
        &self.header
    }
}

impl Iterator for EventReader {
    type Item = Result<MarketEvent, LogError>;

    fn next(&mut self) -> Option<Self::Item> {
        read_frame(&mut self.input).transpose()
    }
}

/// Read one frame; `Ok(None)` means clean EOF at a frame boundary.
fn read_frame<T: for<'de> Deserialize<'de>>(input: &mut impl Read) -> Result<Option<T>, LogError> {
    let mut len_bytes = [0u8; 4];
    match input.read_exact(&mut len_bytes) {
        Ok(()) => {}
        Err(e) if e.kind() == ErrorKind::UnexpectedEof => return Ok(None),
        Err(e) => return Err(e.into()),
    }
    let len = u32::from_le_bytes(len_bytes) as usize;
    let mut buf = vec![0u8; len];
    match input.read_exact(&mut buf) {
        Ok(()) => {}
        Err(e) if e.kind() == ErrorKind::UnexpectedEof => return Err(LogError::TruncatedFrame),
        Err(e) => return Err(e.into()),
    }
    Ok(Some(bincode::deserialize(&buf)?))
}

#[cfg(test)]
mod tests {
    use super::*;
    use quantis_core::events::{L2Snapshot, Level, Trade};
    use quantis_core::types::{Side, TsNanos};

    fn sample_events() -> Vec<MarketEvent> {
        vec![
            MarketEvent::Trade(Trade {
                px: "100000.5".parse().unwrap(),
                qty: "0.25".parse().unwrap(),
                side: Side::Buy,
                exch_ts: TsNanos::from_millis(1_000),
                recv_ts: TsNanos::from_millis(1_001),
                tid: 42,
            }),
            MarketEvent::L2Snapshot(L2Snapshot {
                exch_ts: TsNanos::from_millis(1_500),
                recv_ts: TsNanos::from_millis(1_501),
                bids: vec![Level {
                    px: "100000".parse().unwrap(),
                    qty: "1.5".parse().unwrap(),
                    n_orders: 3,
                }],
                asks: vec![Level {
                    px: "100001".parse().unwrap(),
                    qty: "2".parse().unwrap(),
                    n_orders: 4,
                }],
            }),
        ]
    }

    fn header() -> LogHeader {
        LogHeader {
            venue: "hyperliquid".into(),
            instrument: "BTC".into(),
            created_unix_ms: 1_700_000_000_000,
        }
    }

    #[test]
    fn roundtrips_header_and_events() {
        let dir = std::env::temp_dir().join("quantis-recorder-test");
        std::fs::create_dir_all(&dir).unwrap();
        let path = dir.join("roundtrip.qnts");

        let mut writer = EventWriter::create(&path, &header()).unwrap();
        for e in sample_events() {
            writer.write_event(&e).unwrap();
        }
        assert_eq!(writer.events_written(), 2);
        writer.finish().unwrap();

        let reader = EventReader::open(&path).unwrap();
        assert_eq!(reader.header(), &header());
        let read: Vec<MarketEvent> = reader.map(Result::unwrap).collect();
        assert_eq!(read, sample_events());
    }

    #[test]
    fn truncated_file_is_detected_not_absorbed() {
        let dir = std::env::temp_dir().join("quantis-recorder-test");
        std::fs::create_dir_all(&dir).unwrap();
        let path = dir.join("truncated.qnts");

        let mut writer = EventWriter::create(&path, &header()).unwrap();
        for e in sample_events() {
            writer.write_event(&e).unwrap();
        }
        writer.finish().unwrap();

        let bytes = std::fs::read(&path).unwrap();
        std::fs::write(&path, &bytes[..bytes.len() - 3]).unwrap();

        let reader = EventReader::open(&path).unwrap();
        let results: Vec<_> = reader.collect();
        assert!(matches!(
            results.last().unwrap(),
            Err(LogError::TruncatedFrame)
        ));
    }

    #[test]
    fn rejects_foreign_files() {
        let dir = std::env::temp_dir().join("quantis-recorder-test");
        std::fs::create_dir_all(&dir).unwrap();
        let path = dir.join("foreign.qnts");
        std::fs::write(&path, b"not an event log").unwrap();
        assert!(matches!(EventReader::open(&path), Err(LogError::BadMagic)));
    }
}
